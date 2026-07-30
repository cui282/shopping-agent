from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import shutil
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import __version__
from app.agent.main_agent import ProvidersUnavailableError, run_agent
from app.api.connection import manager
from app.api.monitor import monitor
from app.config import get_settings
from app.memory.store import PreferenceStore, build_preference_store
from app.schemas import (
    HealthResponse,
    ProviderCapability,
    ReadinessResponse,
    TaskRequest,
    TaskSnapshot,
    TaskStarted,
    UploadResponse,
)
from app.tools.price_compare import MissingExchangeRatesError
from app.utils.path_utils import output_root, safe_join, session_dir, upload_root
from app.utils.thread_ctx import thread_scope

load_dotenv()
logger = logging.getLogger("shopping_agent.api")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _persist_snapshot(snapshot: TaskSnapshot) -> None:
    directory = session_dir(snapshot.thread_id)
    destination = directory / "task.json"
    temporary = directory / f".task-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError:
        logger.exception("failed to persist task snapshot", extra={"thread_id": snapshot.thread_id})
        with suppress(OSError):
            temporary.unlink()


def _load_snapshot(thread_id: str) -> TaskSnapshot | None:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        return None
    path = safe_join(output_root(), thread_id, "task.json")
    if not path.is_file():
        return None
    try:
        snapshot = TaskSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("failed to load task snapshot", extra={"thread_id": thread_id})
        return None
    if snapshot.status == "running" and thread_id not in records:
        snapshot = snapshot.model_copy(
            update={
                "status": "error",
                "updated_at": _now(),
                "error_code": "task_interrupted",
                "error": "研究服务已重启，这次任务未能继续，请重新提交",
            }
        )
        _persist_snapshot(snapshot)
    return snapshot


def _prune_records(retention_seconds: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=retention_seconds)
    expired: list[str] = []
    for thread_id, record in records.items():
        if not record.task.done():
            continue
        updated_at = datetime.fromisoformat(record.snapshot.updated_at.replace("Z", "+00:00"))
        if updated_at < cutoff:
            expired.append(thread_id)
    for thread_id in expired:
        records.pop(thread_id, None)


@dataclass(slots=True)
class TaskRecord:
    run_id: str
    snapshot: TaskSnapshot
    task: asyncio.Task[None]


records: dict[str, TaskRecord] = {}
task_locks: dict[str, asyncio.Lock] = {}
preference_store: PreferenceStore = build_preference_store()
task_slots = asyncio.Semaphore(get_settings().max_concurrent_tasks)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    pending = [record.task for record in records.values() if not record.task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title="Shopping Agent API", version=__version__, lifespan=lifespan)
origins = list(get_settings().cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    request_id = incoming if _REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _update_snapshot(thread_id: str, run_id: str, **changes: Any) -> None:
    record = records.get(thread_id)
    if record is None or record.run_id != run_id:
        return
    record.snapshot = record.snapshot.model_copy(update={**changes, "updated_at": _now()})
    _persist_snapshot(record.snapshot)


def _task_lock(thread_id: str) -> asyncio.Lock:
    return task_locks.setdefault(thread_id, asyncio.Lock())


async def _execute(
    request: TaskRequest,
    run_id: str,
    directory: Path,
    reference_images: list[dict[str, Any]],
) -> None:
    thread_id = request.thread_id
    assert thread_id is not None
    try:
        async with task_slots:
            with thread_scope(thread_id, directory):
                await monitor.emit(
                    thread_id,
                    "session_created",
                    data={
                        "thread_id": thread_id,
                        "reference_images": reference_images,
                    },
                )
                result = await asyncio.wait_for(
                    run_agent(request, monitor, preference_store, reference_images),
                    timeout=get_settings().task_timeout_seconds,
                )
                _update_snapshot(thread_id, run_id, status="completed", result=result)
                await monitor.emit(
                    thread_id,
                    "task_result",
                    data=result.model_dump(mode="json"),
                )
    except asyncio.CancelledError:
        record = records.get(thread_id)
        if record is not None and record.run_id == run_id and record.snapshot.status == "running":
            _update_snapshot(thread_id, run_id, status="cancelled")
            await monitor.emit(thread_id, "task_cancelled", data={"thread_id": thread_id})
        raise
    except asyncio.TimeoutError:
        message = "研究任务超过运行时限，请缩小范围后重试"
        _update_snapshot(
            thread_id,
            run_id,
            status="error",
            error_code="task_timeout",
            error=message,
        )
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "task_timeout"},
        )
    except ProvidersUnavailableError:
        message = "已启用的商品平台暂时均不可用，请稍后重试"
        _update_snapshot(
            thread_id,
            run_id,
            status="error",
            error_code="providers_unavailable",
            error=message,
        )
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "providers_unavailable"},
        )
    except MissingExchangeRatesError:
        message = "候选商品币种缺少可用汇率，请配置 FX_RATES_JSON 后重试"
        _update_snapshot(
            thread_id,
            run_id,
            status="error",
            error_code="fx_rates_unavailable",
            error=message,
        )
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "fx_rates_unavailable"},
        )
    except Exception as exc:
        logger.exception(
            "shopping task failed",
            extra={"thread_id": thread_id, "error_type": type(exc).__name__},
        )
        message = "研究任务执行失败，请稍后重试或检查服务配置"
        _update_snapshot(
            thread_id,
            run_id,
            status="error",
            error_code="task_failed",
            error=message,
        )
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "task_failed"},
        )


def _readiness_response() -> ReadinessResponse:
    settings = get_settings()
    return ReadinessResponse(
        status=settings.status,
        task_ready=settings.task_ready,
        environment=settings.app_env,
        runtime_mode="sandbox" if settings.sandbox_mode else "live",
        agent_mode=settings.active_agent_mode,
        requested_agent_mode=settings.agent_mode,
        preference_store=settings.store_backend,
        providers={
            marketplace.name: ProviderCapability(
                configured=marketplace.configured,
                state=marketplace.state,
            )
            for marketplace in settings.marketplaces
        },
        capabilities={
            "websocket_events": True,
            "persistent_snapshots": True,
            "image_upload": True,
            "image_analysis": False,
        },
        required_actions=list(settings.required_actions),
    )


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@app.get("/api/readiness", response_model=ReadinessResponse)
async def readiness() -> ReadinessResponse:
    return _readiness_response()


@app.post("/api/task", response_model=TaskStarted, status_code=status.HTTP_202_ACCEPTED)
async def create_task(request: TaskRequest) -> TaskStarted:
    settings = get_settings()
    readiness_state = _readiness_response()
    if not readiness_state.task_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "runtime_not_ready",
                "message": "Shopping Agent is not configured to run research tasks",
                "required_actions": readiness_state.required_actions,
            },
        )
    _prune_records(settings.task_retention_seconds)
    reference_images = _resolve_uploads(request.upload_ids)
    thread_id = request.thread_id or f"thread-{uuid.uuid4().hex[:12]}"
    request = request.model_copy(update={"thread_id": thread_id})
    async with _task_lock(thread_id):
        previous = records.get(thread_id)
        if previous is not None and not previous.task.done():
            previous.task.cancel()
            with suppress(asyncio.CancelledError):
                await previous.task
        await manager.clear(thread_id)

        created_at = _now()
        run_id = uuid.uuid4().hex
        snapshot = TaskSnapshot(
            thread_id=thread_id,
            status="running",
            query=request.query,
            user_id=request.user_id,
            created_at=created_at,
            updated_at=created_at,
        )
        directory = session_dir(thread_id)
        task = asyncio.create_task(
            _execute(request, run_id, directory, reference_images),
            name=f"shopping-agent:{thread_id}",
        )
        records[thread_id] = TaskRecord(run_id=run_id, snapshot=snapshot, task=task)
        _persist_snapshot(snapshot)
    return TaskStarted(thread_id=thread_id)


@app.get("/api/task/{thread_id}", response_model=TaskSnapshot)
async def get_task(thread_id: str) -> TaskSnapshot:
    record = records.get(thread_id)
    if record is not None:
        return record.snapshot
    persisted = _load_snapshot(thread_id)
    if persisted is not None:
        return persisted
    raise HTTPException(status_code=404, detail="task not found")


@app.delete("/api/task/{thread_id}")
async def delete_task(thread_id: str) -> dict[str, str]:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")

    async with _task_lock(thread_id):
        record = records.get(thread_id)
        if record is not None and not record.task.done():
            record.task.cancel()
            with suppress(asyncio.CancelledError):
                await record.task

        records.pop(thread_id, None)
        await manager.discard(thread_id)
        directory = safe_join(output_root(), thread_id)
        if directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                logger.exception("failed to delete task artifacts", extra={"thread_id": thread_id})
                raise HTTPException(status_code=500, detail="failed to delete task") from exc

    return {"status": "deleted", "thread_id": thread_id}


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str) -> dict[str, str]:
    async with _task_lock(thread_id):
        record = records.get(thread_id)
        if record is None:
            persisted = _load_snapshot(thread_id)
            if persisted is None:
                raise HTTPException(status_code=404, detail="task not found")
            if persisted.status != "running":
                return {"status": persisted.status, "thread_id": thread_id}
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "task_not_active",
                    "message": "The task snapshot exists, but no active worker owns it",
                },
            )
        if record.snapshot.status != "running" or record.task.done():
            return {"status": record.snapshot.status, "thread_id": thread_id}
        record.task.cancel()
        with suppress(asyncio.CancelledError):
            await record.task
        if record.snapshot.status == "running":
            _update_snapshot(thread_id, record.run_id, status="cancelled")
            await monitor.emit(thread_id, "task_cancelled", data={"thread_id": thread_id})
        return {"status": record.snapshot.status, "thread_id": thread_id}


@app.websocket("/ws/{thread_id}")
async def task_socket(websocket: WebSocket, thread_id: str) -> None:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        await websocket.close(code=1008, reason="invalid thread id")
        return
    async with _task_lock(thread_id):
        record = records.get(thread_id)
        if record is None and _load_snapshot(thread_id) is None:
            await websocket.close(code=1008, reason="task not found")
            return
        connected = await manager.connect(thread_id, websocket)
    if not connected:
        return
    try:
        while True:
            incoming = await websocket.receive()
            if incoming.get("type") == "websocket.disconnect":
                break
            raw = incoming.get("text")
            if raw is None:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            is_ping = payload == "ping" or (
                isinstance(payload, dict) and payload.get("type") == "ping"
            )
            if is_ping:
                await manager.send_ephemeral(thread_id, {"type": "pong", "timestamp": _now()})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(thread_id, websocket)


_ALLOWED_UPLOAD_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_UPLOAD_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _valid_image_signature(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def _resolve_uploads(upload_ids: list[str]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    root = upload_root()
    for upload_id in upload_ids:
        if not _UPLOAD_ID_PATTERN.fullmatch(upload_id):
            raise HTTPException(status_code=422, detail=f"invalid upload id: {upload_id}")
        matches = [path for path in root.iterdir() if path.is_file() and path.stem == upload_id]
        if len(matches) != 1:
            raise HTTPException(status_code=422, detail=f"upload not found: {upload_id}")
        path = matches[0]
        references.append(
            {
                "upload_id": upload_id,
                "name": path.name,
                "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "size": path.stat().st_size,
            }
        )
    return references


@app.post("/api/upload", response_model=UploadResponse)
async def upload_reference(file: Annotated[UploadFile, File()]) -> UploadResponse:
    content_type = file.content_type or ""
    suffix = _ALLOWED_UPLOAD_TYPES.get(content_type)
    if suffix is None:
        raise HTTPException(status_code=415, detail="only JPEG, PNG, and WebP images are supported")
    max_upload_bytes = get_settings().max_upload_bytes
    content = await file.read(max_upload_bytes + 1)
    if len(content) > max_upload_bytes:
        raise HTTPException(status_code=413, detail="image exceeds the configured upload limit")
    if not content or not _valid_image_signature(content_type, content):
        raise HTTPException(status_code=422, detail="file content does not match its image type")
    upload_id = uuid.uuid4().hex
    name = f"{upload_id}{suffix}"
    destination = safe_join(upload_root(), name)
    destination.write_bytes(content)
    return UploadResponse(
        upload_id=upload_id,
        name=name,
        content_type=content_type,
        size=len(content),
    )


@app.get("/api/files/{thread_id}/{name}")
async def download_file(thread_id: str, name: str) -> FileResponse:
    record = records.get(thread_id)
    snapshot = record.snapshot if record is not None else _load_snapshot(thread_id)
    if snapshot is None or snapshot.result is None:
        raise HTTPException(status_code=404, detail="file not found")
    allowed_names = {file.name for file in snapshot.result.files}
    if name not in allowed_names:
        raise HTTPException(status_code=404, detail="file not found")
    try:
        path = safe_join(output_root(), thread_id, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid file path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, filename=path.name)


@app.get("/api/preferences/{user_id}")
async def get_preferences(user_id: str) -> dict[str, Any]:
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise HTTPException(status_code=422, detail="invalid user id")
    return {"user_id": user_id, "preferences": await preference_store.get(user_id)}


@app.delete("/api/preferences/{user_id}")
async def delete_preferences(user_id: str) -> dict[str, str]:
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise HTTPException(status_code=422, detail="invalid user id")
    await preference_store.delete(user_id)
    return {"status": "deleted", "user_id": user_id}
