from __future__ import annotations

import asyncio
import errno
import fcntl
import importlib.util
import json
import logging
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, TextIO

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import __version__
from app.agent.main_agent import (
    BlockingAmbiguityError,
    ProvidersUnavailableError,
    UnsupportedCapabilityError,
    run_agent,
)
from app.api.connection import manager
from app.api.monitor import monitor
from app.config import get_settings
from app.memory.commands import execute_memory_commands
from app.memory.store import PreferenceStore, PreferenceStoreError, build_preference_store
from app.recall.orchestrator import recall_readiness
from app.reports import (
    ReportGenerationError,
    generate_reports,
    report_file_links,
)
from app.schemas import (
    ClarificationCommand,
    ClarificationCommandResponse,
    ClarificationPrompt,
    ClarificationRequiredEventData,
    ClarificationResolvedEventData,
    ConstraintRelaxation,
    ConstraintRelaxationChange,
    ConstraintRelaxationCommand,
    DataMode,
    EventName,
    HealthResponse,
    IntentResolvedEventData,
    MemoryCommand,
    MonitorEvent,
    PreferenceDeleteResponse,
    PreferenceResponse,
    ProviderCapability,
    ReadinessComponents,
    ReadinessComponentStatus,
    ReadinessResponse,
    ReferenceImageBinding,
    RememberedPreference,
    ReportGeneratedEventData,
    ReportGenerationResponse,
    ReportListResponse,
    RerunCommand,
    ResearchHistoryResponse,
    ShoppingPlan,
    ShoppingSummaryOutput,
    SnapshotLineage,
    TaskDeleteCommand,
    TaskDeleteResponse,
    TaskRequest,
    TaskRerunResponse,
    TaskSnapshot,
    TaskSnapshotMessage,
    TaskStarted,
    TaskTombstone,
    UploadResponse,
)
from app.tools.clarification import InvalidClarificationResponse, normalize_clarification_response
from app.tools.price_compare import MissingExchangeRatesError
from app.utils.path_utils import output_root, safe_join, session_dir, upload_root
from app.utils.thread_ctx import thread_scope

load_dotenv()
logger = logging.getLogger("shopping_agent.api")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SnapshotPersistenceError(OSError):
    pass


class TaskDeletedError(RuntimeError):
    """Raised when a late worker attempts to mutate a tombstoned task."""


@dataclass(frozen=True, slots=True)
class ResolvedUpload:
    upload_id: str
    name: str
    content_type: str
    size: int
    source: Path


_CONTROL_DIRECTORY = ".task-control"
_TOMBSTONE_DIRECTORY = ".task-tombstones"
_MUTATION_LOCK = ".research-mutation.lock"
_TOMBSTONE_SUFFIX = ".json"
_mutation_lock_held: ContextVar[bool] = ContextVar("mutation_lock_held", default=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _task_directory(thread_id: str, *, create: bool = False) -> Path:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise ValueError("invalid thread id")
    root = output_root().resolve()
    candidate = root / thread_id
    if candidate.is_symlink():
        raise ValueError("task directory must not be a symlink")
    directory = safe_join(root, thread_id)
    if directory != candidate:
        raise ValueError("task directory canonical path mismatch")
    if create:
        # Keep the existing session-dir seam for persistence failures and legacy callers.
        created = session_dir(thread_id)
        if created != directory:
            raise ValueError("task directory canonical path mismatch")
    return directory


def _control_path(thread_id: str, filename: str, *, create: bool = True) -> Path:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise ValueError("invalid thread id")
    if not re.fullmatch(r"\.[A-Za-z0-9_.-]+", filename):
        raise ValueError("invalid task control filename")
    root = output_root().resolve()
    directory = root / _CONTROL_DIRECTORY
    if directory.is_symlink():
        raise ValueError("task control directory must not be a symlink")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{thread_id}{filename}"
    path = safe_join(root, _CONTROL_DIRECTORY, candidate.name)
    if path != candidate:
        raise ValueError("task control path canonical mismatch")
    return path


def _tombstone_path(thread_id: str, *, create: bool = True) -> Path:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise ValueError("invalid thread id")
    root = output_root().resolve()
    directory = root / _TOMBSTONE_DIRECTORY
    if directory.is_symlink():
        raise ValueError("task tombstone directory must not be a symlink")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{thread_id}{_TOMBSTONE_SUFFIX}"
    path = safe_join(root, _TOMBSTONE_DIRECTORY, candidate.name)
    if path != candidate:
        raise ValueError("task tombstone path canonical mismatch")
    return path


def _read_tombstone(thread_id: str) -> TaskTombstone | None:
    path = _tombstone_path(thread_id, create=False)
    if path.is_symlink() or not path.exists():
        if path.is_symlink():
            return TaskTombstone(thread_id=thread_id, generation=1, deleted_at=_now())
        return None
    if not path.is_file():
        return TaskTombstone(thread_id=thread_id, generation=1, deleted_at=_now())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        logger.exception("failed to read task tombstone", extra={"thread_id": thread_id})
        return TaskTombstone(thread_id=thread_id, generation=1, deleted_at=_now())
    if not isinstance(payload, dict) or payload.get("thread_id") != thread_id:
        return TaskTombstone(thread_id=thread_id, generation=1, deleted_at=_now())
    try:
        return TaskTombstone.model_validate(payload)
    except ValueError:
        logger.exception("invalid task tombstone", extra={"thread_id": thread_id})
        return TaskTombstone(thread_id=thread_id, generation=1, deleted_at=_now())


def _task_is_deleted(thread_id: str) -> bool:
    return _read_tombstone(thread_id) is not None


def _atomic_json_write(destination: Path, payload: dict[str, Any]) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError:
        with suppress(OSError):
            temporary.unlink()
        raise


def _persist_snapshot(snapshot: TaskSnapshot) -> None:
    if not _mutation_lock_held.get():
        with _task_mutation_lock(snapshot.thread_id):
            _persist_snapshot(snapshot)
        return
    if _task_is_deleted(snapshot.thread_id):
        raise TaskDeletedError(f"task {snapshot.thread_id} has been deleted")
    temporary: Path | None = None
    try:
        directory = _task_directory(snapshot.thread_id, create=True)
        destination = directory / "task.json"
        temporary = directory / f".task-{uuid.uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError as exc:
        logger.exception("failed to persist task snapshot", extra={"thread_id": snapshot.thread_id})
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()
        raise SnapshotPersistenceError(str(exc)) from exc


def _open_lock_handle(
    thread_id: str,
    filename: str,
    *,
    non_blocking: bool = False,
) -> TextIO | None:
    handle: TextIO | None = None
    try:
        lock_path = _control_path(thread_id, filename)
        handle = lock_path.open("a+", encoding="utf-8")
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if non_blocking else 0)
        fcntl.flock(handle.fileno(), flags)
        return handle
    except OSError as exc:
        if handle is not None:
            with suppress(OSError):
                handle.close()
        if non_blocking and exc.errno in {errno.EACCES, errno.EAGAIN}:
            return None
        raise SnapshotPersistenceError(str(exc)) from exc


def _release_lock_handle(handle: TextIO | None) -> None:
    if handle is None:
        return
    with suppress(OSError):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    with suppress(OSError):
        handle.close()


def _try_acquire_owner_lock(thread_id: str) -> TextIO | None:
    return _open_lock_handle(thread_id, ".research-owner.lock", non_blocking=True)


@contextmanager
def _persistent_lock(thread_id: str, filename: str):
    handle = _open_lock_handle(thread_id, filename)
    try:
        yield
    finally:
        _release_lock_handle(handle)


@contextmanager
def _command_lock(thread_id: str):
    """Serialize keyed child commands across workers sharing the snapshot store."""
    with _persistent_lock(thread_id, ".research-commands.lock"):
        yield


@contextmanager
def _task_mutation_lock(thread_id: str):
    with _persistent_lock(thread_id, _MUTATION_LOCK):
        token = _mutation_lock_held.set(True)
        try:
            yield
        finally:
            _mutation_lock_held.reset(token)


def _write_tombstone(
    thread_id: str,
    *,
    user_id: str | None,
    generation: int,
) -> TaskTombstone:
    with _task_mutation_lock(thread_id):
        existing = _read_tombstone(thread_id)
        if existing is not None:
            return existing
        path = _tombstone_path(thread_id)
        payload = TaskTombstone(
            thread_id=thread_id,
            user_id=user_id,
            generation=max(1, generation),
            deleted_at=_now(),
        )
        try:
            _atomic_json_write(path, payload.model_dump(mode="json"))
        except OSError as exc:
            raise SnapshotPersistenceError(str(exc)) from exc
        return payload


def _remove_task_control_files(thread_id: str) -> None:
    for filename in (
        ".research-owner.lock",
        ".research-commands.lock",
        ".research-recovery.lock",
    ):
        try:
            path = _control_path(thread_id, filename, create=False)
        except ValueError:
            continue
        with suppress(OSError):
            path.unlink()


def _remove_task_directory(thread_id: str) -> None:
    directory = _task_directory(thread_id)
    if not directory.exists():
        return
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("task directory is not a canonical directory")
    shutil.rmtree(directory)


def _as_data_mode(value: Any) -> DataMode | None:
    return value if value in {"live", "sandbox", "mixed"} else None


def _infer_legacy_snapshot_data_mode(payload: dict[str, Any]) -> DataMode:
    result = payload.get("result")
    result_mode: DataMode | None = None
    if isinstance(result, dict):
        result_mode = _as_data_mode(result.get("data_mode") or result.get("provider_mode"))

    event_modes: set[DataMode] = set()
    sources: set[str] = set()

    for event in payload.get("events", []):
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        event_mode = _as_data_mode(data.get("data_mode"))
        if event_mode is not None:
            event_modes.add(event_mode)
        if event.get("event") != "tool_end" or data.get("tool_name") != "item_search":
            continue
        source = data.get("source")
        if source in {"live", "fixture"}:
            sources.add(source)

    if result_mode is not None and any(mode != result_mode for mode in event_modes):
        raise ValueError("legacy snapshot contains conflicting data modes")
    if result_mode is not None:
        return result_mode
    if "mixed" in event_modes or len(event_modes) > 1 or sources == {"live", "fixture"}:
        return "mixed"
    if event_modes:
        return next(iter(event_modes))
    if sources == {"fixture"}:
        return "sandbox"
    return "live"


def _read_persisted_snapshot(thread_id: str) -> TaskSnapshot | None:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        return None
    if _task_is_deleted(thread_id):
        return None
    try:
        path = _task_directory(thread_id) / "task.json"
    except ValueError:
        return None
    if path.is_symlink() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("task snapshot must be a JSON object")
        validation_payload = dict(payload)
        if validation_payload.get("snapshot_id") is None:
            validation_payload["snapshot_id"] = validation_payload.get("thread_id")
        if "data_mode" not in payload:
            data_mode = _infer_legacy_snapshot_data_mode(payload)
            validation_payload["data_mode"] = data_mode
            validation_payload["events"] = [
                {
                    **event,
                    "data": {**event["data"], "data_mode": data_mode},
                }
                if isinstance(event, dict) and isinstance(event.get("data"), dict)
                else event
                for event in payload.get("events", [])
            ]
        snapshot = TaskSnapshot.model_validate(validation_payload)
    except (OSError, TypeError, ValueError):
        logger.exception("failed to read persisted task snapshot", extra={"thread_id": thread_id})
        return None
    return snapshot


def _load_snapshot(thread_id: str) -> TaskSnapshot | None:
    if _task_is_deleted(thread_id):
        return None
    snapshot = _read_persisted_snapshot(thread_id)
    if snapshot is None:
        return None
    if snapshot.status == "running" and thread_id not in records:
        with _persistent_lock(thread_id, ".research-recovery.lock"):
            snapshot = _read_persisted_snapshot(thread_id)
            if snapshot is None:
                return None
            if snapshot.status == "running" and thread_id not in records:
                owner_handle = _try_acquire_owner_lock(thread_id)
                if owner_handle is None:
                    return snapshot
                try:
                    timestamp = _now()
                    message = "研究服务已重启，这次任务未能继续，请重新提交"
                    sequence = snapshot.events[-1].sequence + 1 if snapshot.events else 1
                    interrupted = MonitorEvent(
                        event_id=f"evt-{uuid.uuid4().hex}",
                        thread_id=thread_id,
                        run_id=snapshot.run_id,
                        sequence=sequence,
                        event="error",
                        message=message,
                        data={
                            "thread_id": thread_id,
                            "code": "task_interrupted",
                            "data_mode": snapshot.data_mode,
                        },
                        timestamp=timestamp,
                    )
                    snapshot = snapshot.model_copy(
                        update={
                            "status": "error",
                            "updated_at": timestamp,
                            "events": [*snapshot.events, interrupted],
                            "error_code": "task_interrupted",
                            "error": message,
                        }
                    )
                    try:
                        _persist_snapshot(snapshot)
                    except TaskDeletedError:
                        return None
                finally:
                    _release_lock_handle(owner_handle)
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
        record = records.pop(thread_id, None)
        if record is not None:
            _release_lock_handle(record.owner_handle)


@dataclass(slots=True)
class TaskRecord:
    run_id: str
    snapshot: TaskSnapshot
    task: asyncio.Task[None]
    owner_handle: TextIO | None = None
    generation: int = 0
    deleting: bool = False


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


def _record_task_result(
    record: TaskRecord,
    thread_id: str,
    message: str,
    data: dict[str, Any],
    event_id: str,
    timestamp: str,
) -> tuple[MonitorEvent, MonitorEvent]:
    if record.deleting or _task_is_deleted(thread_id):
        raise TaskDeletedError(f"task {thread_id} has been deleted")
    task_data_mode = record.snapshot.data_mode
    result = ShoppingSummaryOutput.model_validate(data)
    if result.data_mode != task_data_mode:
        raise RuntimeError(
            f"task result data mode {result.data_mode} does not match task mode {task_data_mode}"
        )
    files = report_file_links(record.snapshot)
    result = result.model_copy(update={"files": files})
    terminal_snapshot = record.snapshot.model_copy(
        update={
            "status": "completed",
            "updated_at": timestamp,
            "result": result,
            "snapshot_id": record.snapshot.snapshot_id or thread_id,
            "resolved_query": result.resolved_query or record.snapshot.resolved_query,
            "resolved_intent": result.resolved_intent or record.snapshot.resolved_intent,
            "mode": result.mode,
            "working_assumptions": result.working_assumptions,
            "applied_preferences": result.applied_preferences,
            "task_overrides": result.task_overrides,
            "constraint_relaxations": result.constraint_relaxations,
            "provider_coverage": result.providers,
            "product_evidence": result.product_evidence,
            "exchange_rate": result.exchange_rate,
            "report_references": files,
            "recall_provenance": result.recall_provenance,
            "error_code": None,
            "error": None,
        }
    )
    try:
        generate_reports(terminal_snapshot, _task_directory(thread_id, create=True))
    except ReportGenerationError as exc:
        raise RuntimeError(str(exc)) from exc

    previous_sequence = record.snapshot.events[-1].sequence if record.snapshot.events else 0
    report_event = MonitorEvent(
        event_id=f"evt-{uuid.uuid4().hex}",
        thread_id=thread_id,
        run_id=record.run_id,
        sequence=previous_sequence + 1,
        event="report_generated",
        message="研究报告已生成",
        data=ReportGeneratedEventData(
            snapshot_id=terminal_snapshot.snapshot_id,
            snapshot_effective_at=timestamp,
            files=files,
            data_mode=task_data_mode,
        ).model_dump(mode="json"),
        timestamp=timestamp,
    )
    envelope = MonitorEvent(
        event_id=event_id,
        thread_id=thread_id,
        run_id=record.run_id,
        sequence=previous_sequence + 2,
        event="task_result",
        message=message,
        data={**result.model_dump(mode="json"), "data_mode": task_data_mode},
        timestamp=timestamp,
    )
    snapshot = terminal_snapshot.model_copy(
        update={"events": [*record.snapshot.events, report_event, envelope]}
    )
    _persist_snapshot(snapshot)
    record.snapshot = snapshot
    return report_event, envelope


def _record_event(
    thread_id: str,
    event: EventName,
    message: str,
    data: dict[str, Any],
    event_id: str,
    timestamp: str,
    emitted_run_id: str | None,
) -> MonitorEvent | tuple[MonitorEvent, MonitorEvent]:
    with _task_mutation_lock(thread_id):
        record = records.get(thread_id)
        if record is None:
            raise TaskDeletedError(f"cannot record an event for unknown task {thread_id}")
        if record.deleting or _task_is_deleted(thread_id):
            raise TaskDeletedError(f"task {thread_id} has been deleted")
        if emitted_run_id is not None and emitted_run_id != record.run_id:
            raise RuntimeError(f"cannot record an event from a superseded run for {thread_id}")
        current_status = record.snapshot.status
        allowed_statuses = {
            "session_created": {"running"},
            "intent_resolved": {"running"},
            "assistant_call": {"running"},
            "context_compression": {"running"},
            "tool_start": {"running"},
            "tool_end": {"running"},
            "fork": {"running"},
            "report_generated": {"completed"},
            "task_result": {"running"},
            "task_cancelled": {"running", "awaiting_clarification"},
            "clarification_required": {"running"},
            "clarification_resolved": {"awaiting_clarification"},
            "error": {"running"},
        }
        if current_status not in allowed_statuses[event]:
            if current_status in {"completed", "cancelled", "error"}:
                raise RuntimeError(f"cannot record an event for terminal task {thread_id}")
            raise RuntimeError(
                f"event {event} cannot transition task {thread_id} from {current_status}"
            )

        if event == "task_result":
            return _record_task_result(record, thread_id, message, data, event_id, timestamp)

        sequence = record.snapshot.events[-1].sequence + 1 if record.snapshot.events else 1
        task_data_mode = record.snapshot.data_mode
        event_data = {**data, "data_mode": task_data_mode}
        envelope = MonitorEvent(
            event_id=event_id,
            thread_id=thread_id,
            run_id=record.run_id,
            sequence=sequence,
            event=event,
            message=message,
            data=event_data,
            timestamp=timestamp,
        )
        changes: dict[str, Any] = {
            "events": [*record.snapshot.events, envelope],
            "updated_at": timestamp,
        }
        if event == "intent_resolved":
            resolved = IntentResolvedEventData.model_validate(event_data)
            changes.update(
                resolved_query=resolved.resolved_query,
                resolved_intent=resolved.resolved_intent,
                mode=resolved.resolved_intent.mode,
                working_assumptions=resolved.resolved_intent.working_assumptions,
                applied_preferences=resolved.applied_preferences,
                task_overrides=resolved.task_overrides,
                constraint_relaxations=resolved.constraint_relaxations,
            )
        elif event == "task_cancelled":
            changes.update(
                status="cancelled",
                result=None,
                clarification=None,
                error_code=None,
                error=None,
            )
        elif event == "clarification_required":
            required = ClarificationRequiredEventData.model_validate(event_data)
            changes.update(
                status="awaiting_clarification",
                result=None,
                clarification=ClarificationPrompt(
                    field=required.field,
                    reason_code=required.reason_code,
                    question=required.question,
                ),
                error_code=None,
                error=None,
            )
        elif event == "clarification_resolved":
            resolved = ClarificationResolvedEventData.model_validate(event_data)
            resolved_value = resolved.resolved_value or normalize_clarification_response(
                resolved.field,
                resolved.response,
            )
            changes.update(
                status="running",
                clarification=None,
                clarification_answers={
                    **record.snapshot.clarification_answers,
                    resolved.field: resolved_value,
                },
                error_code=None,
                error=None,
            )
        elif event == "error":
            changes.update(
                status="error",
                result=None,
                clarification=None,
                error_code=str(data.get("code") or "task_failed"),
                error=message,
            )

        snapshot = record.snapshot.model_copy(update=changes)
        _persist_snapshot(snapshot)
        record.snapshot = snapshot
        return envelope


monitor.set_event_recorder(_record_event)


def _task_lock(thread_id: str) -> asyncio.Lock:
    return task_locks.setdefault(thread_id, asyncio.Lock())


def _snapshot_message(thread_id: str) -> dict[str, Any]:
    if _task_is_deleted(thread_id):
        raise TaskDeletedError(f"task {thread_id} has been deleted")
    record = records.get(thread_id)
    if record is not None and record.deleting:
        raise TaskDeletedError(f"task {thread_id} is being deleted")
    snapshot = record.snapshot if record is not None else _load_snapshot(thread_id)
    if snapshot is None:
        raise RuntimeError(f"cannot bootstrap an unknown task {thread_id}")
    return TaskSnapshotMessage(snapshot=snapshot).model_dump(mode="json")


async def _execute_task(
    request: TaskRequest,
    run_id: str,
    directory: Path,
    reference_images: list[dict[str, Any]],
    *,
    resume: bool = False,
    clarification_answers: dict[str, str] | None = None,
    resolved_intent: ShoppingPlan | None = None,
    resolved_query: str | None = None,
    applied_preferences: RememberedPreference | None = None,
    constraint_relaxation_changes: list[ConstraintRelaxationChange] | None = None,
) -> None:
    thread_id = request.thread_id
    assert thread_id is not None
    record = records.get(thread_id)
    task_data_mode: DataMode = (
        record.snapshot.data_mode if record is not None else get_settings().data_mode
    )
    try:
        async with task_slots:
            with thread_scope(thread_id, directory, run_id):
                if not resume:
                    await monitor.emit(
                        thread_id,
                        "session_created",
                        data={
                            "thread_id": thread_id,
                            "reference_images": reference_images,
                            "data_mode": task_data_mode,
                        },
                    )
                result = await asyncio.wait_for(
                    run_agent(
                        request,
                        monitor,
                        preference_store,
                        reference_images,
                        data_mode=task_data_mode,
                        clarification_answers=clarification_answers,
                        resolved_intent=resolved_intent,
                        resolved_query=resolved_query,
                        applied_preferences=applied_preferences,
                        constraint_relaxation_changes=constraint_relaxation_changes,
                        history_events=(record.snapshot.events if record is not None else ()),
                    ),
                    timeout=get_settings().task_timeout_seconds,
                )
                await monitor.emit(
                    thread_id,
                    "task_result",
                    data=result.model_dump(mode="json"),
                )
    except TaskDeletedError:
        return
    except asyncio.CancelledError:
        record = records.get(thread_id)
        if (
            record is not None
            and not record.deleting
            and not _task_is_deleted(thread_id)
            and record.run_id == run_id
            and record.snapshot.status == "running"
        ):
            await monitor.emit(
                thread_id,
                "task_cancelled",
                data={"thread_id": thread_id},
                run_id=run_id,
            )
        raise
    except BlockingAmbiguityError as exc:
        ambiguity = exc.ambiguity
        await monitor.emit(
            thread_id,
            "clarification_required",
            message=ambiguity.question,
            data={
                "field": ambiguity.field,
                "reason_code": ambiguity.reason_code,
                "question": ambiguity.question,
            },
            run_id=run_id,
        )
    except asyncio.TimeoutError:
        message = "研究任务超过运行时限，请缩小范围后重试"
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "task_timeout"},
            run_id=run_id,
        )
    except ProvidersUnavailableError:
        message = "已启用的商品平台暂时均不可用，请稍后重试"
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "providers_unavailable"},
            run_id=run_id,
        )
    except UnsupportedCapabilityError as exc:
        message = str(exc)
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "unsupported_capability"},
            run_id=run_id,
        )
    except MissingExchangeRatesError as exc:
        currencies = "、".join(exc.currencies) or "未知币种"
        message = f"候选商品币种缺少可用汇率（{currencies}），请配置 FX_RATES_JSON 后重试"
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "fx_rates_unavailable"},
            run_id=run_id,
        )
    except PreferenceStoreError as exc:
        message = f"Remembered Preference backend unavailable: {exc}"
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "preference_store_unavailable"},
            run_id=run_id,
        )
    except SnapshotPersistenceError:
        raise
    except Exception as exc:
        if _task_is_deleted(thread_id):
            return
        logger.exception(
            "shopping task failed",
            extra={"thread_id": thread_id, "error_type": type(exc).__name__},
        )
        message = "研究任务执行失败，请稍后重试或检查服务配置"
        await monitor.emit(
            thread_id,
            "error",
            message=message,
            data={"thread_id": thread_id, "code": "task_failed"},
            run_id=run_id,
        )


async def _execute(
    request: TaskRequest,
    run_id: str,
    directory: Path,
    reference_images: list[dict[str, Any]],
    *,
    resume: bool = False,
    clarification_answers: dict[str, str] | None = None,
    resolved_intent: ShoppingPlan | None = None,
    resolved_query: str | None = None,
    applied_preferences: RememberedPreference | None = None,
    constraint_relaxation_changes: list[ConstraintRelaxationChange] | None = None,
    owner_handle: TextIO | None = None,
) -> None:
    thread_id = request.thread_id
    assert thread_id is not None
    try:
        await _execute_task(
            request,
            run_id,
            directory,
            reference_images,
            resume=resume,
            clarification_answers=clarification_answers,
            resolved_intent=resolved_intent,
            resolved_query=resolved_query,
            applied_preferences=applied_preferences,
            constraint_relaxation_changes=constraint_relaxation_changes,
        )
    except SnapshotPersistenceError:
        logger.exception(
            "task timeline persistence failed; releasing worker ownership",
            extra={"thread_id": thread_id},
        )
        record = records.get(thread_id)
        if record is not None and record.run_id == run_id:
            records.pop(thread_id, None)
            await manager.close_active(
                thread_id,
                code=1011,
                reason="timeline persistence failed",
            )
    finally:
        record = records.get(thread_id)
        if record is not None and record.owner_handle is owner_handle:
            record.owner_handle = None
        _release_lock_handle(owner_handle)


def _readiness_component(
    *,
    configured: bool,
    ready: bool,
    state: str,
    reason_code: str,
    reason: str,
) -> ReadinessComponentStatus:
    return ReadinessComponentStatus(
        configured=configured,
        ready=ready,
        state=state,  # type: ignore[arg-type]
        reason_code=reason_code,
        reason=reason,
    )


def _configured_runtime_path(variable: str, default: str) -> Path:
    raw = Path(os.getenv(variable, default).strip())
    return (raw if raw.is_absolute() else _PROJECT_ROOT / raw).resolve()


def _storage_readiness() -> ReadinessComponentStatus:
    roots = {
        "output": _configured_runtime_path("OUTPUT_ROOT", "output"),
        "uploaded": _configured_runtime_path("UPLOAD_ROOT", "uploaded"),
        "data": _PROJECT_ROOT / "data",
    }
    failures: list[str] = []
    for name, root in roots.items():
        try:
            if not root.exists():
                root.mkdir(parents=True, exist_ok=True)
            if not root.is_dir():
                failures.append(f"{name} is not a directory")
            elif not os.access(root, os.R_OK | os.W_OK | os.X_OK):
                failures.append(f"{name} is not readable and writable")
        except OSError as exc:
            failures.append(f"{name} unavailable ({type(exc).__name__})")
    if failures:
        return _readiness_component(
            configured=True,
            ready=False,
            state="degraded",
            reason_code="storage_unavailable",
            reason="; ".join(failures),
        )
    return _readiness_component(
        configured=True,
        ready=True,
        state="ready",
        reason_code="writable",
        reason="output, uploaded, and data directories are readable and writable",
    )


def _recall_component(report, *, disabled_codes: set[str]) -> ReadinessComponentStatus:
    if report.state == "ready":
        return _readiness_component(
            configured=True,
            ready=True,
            state="ready",
            reason_code=report.reason_code,
            reason=report.reason,
        )
    if report.state == "configured":
        return _readiness_component(
            configured=True,
            ready=False,
            state="configured",
            reason_code=report.reason_code,
            reason=report.reason,
        )
    if report.reason_code in disabled_codes:
        return _readiness_component(
            configured=False,
            ready=False,
            state="disabled",
            reason_code=report.reason_code,
            reason=report.reason,
        )
    return _readiness_component(
        configured=report.configured,
        ready=False,
        state="degraded" if report.state == "degraded" else "unavailable",
        reason_code=report.reason_code,
        reason=report.reason,
    )


def _readiness_components(settings, preference_backend) -> ReadinessComponents:
    if settings.llm_configured:
        llm = _readiness_component(
            configured=True,
            ready=False,
            state="configured",
            reason_code="configured_not_probed",
            reason="LLM credentials and model are configured; readiness does not make a live model call",
        )
    else:
        llm = _readiness_component(
            configured=False,
            ready=False,
            state="unavailable",
            reason_code="not_configured",
            reason=(
                "OPENAI_API_KEY and LLM_MAIN are not both configured; the active rules planner"
                if settings.active_agent_mode == "rules"
                else "OPENAI_API_KEY and LLM_MAIN are not both configured"
            ),
        )

    gateways: dict[str, ReadinessComponentStatus] = {}
    for marketplace in settings.marketplaces:
        if settings.sandbox_mode:
            gateways[marketplace.name] = _readiness_component(
                configured=marketplace.configured,
                ready=False,
                state="disabled",
                reason_code=(
                    "sandbox_forbidden"
                    if settings.app_env == "production"
                    else "sandbox_fixture_active"
                ),
                reason=(
                    "production rejects sandbox execution; live gateway is not used"
                    if settings.app_env == "production"
                    else "explicit Sandbox mode uses fixture providers; live gateway is not used"
                ),
            )
        elif marketplace.state == "configured":
            gateways[marketplace.name] = _readiness_component(
                configured=True,
                ready=False,
                state="configured",
                reason_code="configured_not_probed",
                reason="live gateway endpoint and key are configured; readiness does not call the gateway",
            )
        elif marketplace.state == "partial":
            gateways[marketplace.name] = _readiness_component(
                configured=False,
                ready=False,
                state="degraded",
                reason_code="partial_configuration",
                reason="gateway endpoint and key must both be configured",
            )
        else:
            gateways[marketplace.name] = _readiness_component(
                configured=False,
                ready=False,
                state="unavailable",
                reason_code="not_configured",
                reason="live gateway endpoint and key are not configured",
            )

    if preference_backend.fallback_reason:
        redis = _readiness_component(
            configured=True,
            ready=False,
            state="degraded",
            reason_code="fallback_active",
            reason=preference_backend.fallback_reason,
        )
    elif preference_backend.backend == "redis":
        redis = _readiness_component(
            configured=True,
            ready=False,
            state="configured",
            reason_code="configured_not_probed",
            reason="Redis is configured; readiness does not issue a network ping",
        )
    else:
        redis = _readiness_component(
            configured=False,
            ready=False,
            state="disabled",
            reason_code="backend_disabled",
            reason="STORE_BACKEND=memory uses process-local preference evaluation",
        )

    recall = recall_readiness(settings)
    opensearch = _recall_component(recall.channels["opensearch"], disabled_codes=set())
    query_tower = _recall_component(
        recall.channels["query_tower"], disabled_codes={"ann_backend_disabled"}
    )
    item_tower = _recall_component(
        recall.channels["item_tower"], disabled_codes={"ann_backend_disabled"}
    )
    faiss_report = recall.channels["faiss"]
    if settings.ann_backend == "disabled":
        faiss = _recall_component(faiss_report, disabled_codes={"backend_disabled"})
    else:
        index_path = _configured_runtime_path("ANN_INDEX_PATH", "./data/item_index.faiss")
        if not index_path.is_file():
            faiss = _readiness_component(
                configured=True,
                ready=False,
                state="unavailable",
                reason_code="index_missing",
                reason="ANN_BACKEND=faiss is enabled but ANN_INDEX_PATH does not point to a file",
            )
        elif importlib.util.find_spec("faiss") is None:
            faiss = _readiness_component(
                configured=True,
                ready=False,
                state="unavailable",
                reason_code="dependency_missing",
                reason="ANN_BACKEND=faiss is enabled but the faiss runtime dependency is unavailable",
            )
        else:
            faiss = _recall_component(faiss_report, disabled_codes=set())

    personalization = recall.personalization
    if personalization is None or personalization.reason_code in {
        "ann_backend_disabled",
        "not_configured",
    }:
        user_tower = _readiness_component(
            configured=bool(personalization and personalization.configured),
            ready=False,
            state="disabled"
            if personalization and personalization.reason_code == "ann_backend_disabled"
            else "unavailable",
            reason_code=personalization.reason_code if personalization else "not_configured",
            reason=personalization.reason if personalization else "user tower is not configured",
        )
    elif personalization.state == "configured":
        user_tower = _readiness_component(
            configured=True,
            ready=False,
            state="configured",
            reason_code=personalization.reason_code,
            reason=personalization.reason,
        )
    else:
        user_tower = _readiness_component(
            configured=personalization.configured,
            ready=False,
            state="degraded" if personalization.state == "degraded" else "unavailable",
            reason_code=personalization.reason_code,
            reason=personalization.reason,
        )

    return ReadinessComponents(
        llm=llm,
        marketplace_gateways=gateways,
        redis=redis,
        opensearch=opensearch,
        faiss=faiss,
        query_tower=query_tower,
        item_tower=item_tower,
        user_tower=user_tower,
        storage=_storage_readiness(),
        image_analysis=_readiness_component(
            configured=False,
            ready=False,
            state="disabled",
            reason_code="not_configured",
            reason="image analysis is not configured; image upload remains a separate storage capability",
        ),
    )


def _readiness_response() -> ReadinessResponse:
    settings = get_settings()
    sandbox_available = settings.sandbox_mode and settings.app_env != "production"
    preference_backend = preference_store.backend_status
    components = _readiness_components(settings, preference_backend)

    def capability(marketplace) -> ProviderCapability:
        if sandbox_available:
            return ProviderCapability(
                configured=marketplace.configured,
                state=marketplace.state,
                available=True,
                source="fixture",
            )
        if settings.sandbox_mode and settings.app_env == "production":
            return ProviderCapability(
                configured=marketplace.configured,
                state=marketplace.state,
                available=False,
                source="fixture",
                failure_reason="sandbox_forbidden",
            )
        return ProviderCapability(
            configured=marketplace.configured,
            state=marketplace.state,
            available=marketplace.configured,
            source="live",
            failure_reason=None if marketplace.configured else "not_configured",
        )

    required_actions = list(settings.required_actions)
    if not components.storage.ready:
        required_actions.append(
            "Ensure OUTPUT_ROOT, UPLOAD_ROOT, and the data directory are readable and writable"
        )
    if preference_backend.fallback_reason:
        required_actions.append(
            "Redis preference backend unavailable; local evaluation is non-persistent"
        )
    task_ready = settings.task_ready and components.storage.ready
    readiness_status = settings.status if task_ready else "not_ready"
    if preference_backend.fallback_reason and readiness_status == "ready":
        readiness_status = "degraded"
    if components.redis.state == "disabled" and readiness_status == "ready":
        readiness_status = "degraded"
    return ReadinessResponse(
        status=readiness_status,
        task_ready=task_ready,
        environment=settings.app_env,
        runtime_mode="sandbox" if settings.sandbox_mode else "live",
        agent_mode=settings.active_agent_mode,
        requested_agent_mode=settings.agent_mode,
        preference_store=preference_backend.backend,
        providers={
            marketplace.name: capability(marketplace) for marketplace in settings.marketplaces
        },
        capabilities={
            "websocket_events": True,
            "persistent_snapshots": True,
            "image_upload": True,
            "image_analysis": False,
        },
        required_actions=required_actions,
        data_mode=settings.data_mode,
        developer_diagnostic_mode=settings.developer_diagnostic_mode,
        components=components,
        preference_backend=preference_backend,
        recall=recall_readiness(settings),
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
    resolved_uploads = _resolve_uploads(request.upload_ids)
    thread_id = request.thread_id or f"thread-{uuid.uuid4().hex[:12]}"
    request = request.model_copy(update={"thread_id": thread_id})
    async with _task_lock(thread_id):
        if _task_is_deleted(thread_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "task_deleted",
                    "message": "A deleted task thread cannot be reused",
                },
            )
        previous = records.get(thread_id)
        if previous is not None and previous.snapshot.status in {"completed", "cancelled", "error"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "thread_id_immutable",
                    "message": "A terminal Research Snapshot cannot be replaced; start a new task",
                },
            )
        if previous is None:
            persisted = _read_persisted_snapshot(thread_id)
            if persisted is not None and persisted.status in {"completed", "cancelled", "error"}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "thread_id_immutable",
                        "message": "A terminal Research Snapshot cannot be replaced; start a new task",
                    },
                )
        if previous is not None and not previous.task.done():
            records.pop(thread_id, None)
            previous.task.cancel()
            with suppress(asyncio.CancelledError):
                await previous.task
        await manager.clear(thread_id, close_active=True)

        created_at = _now()
        run_id = uuid.uuid4().hex
        snapshot = TaskSnapshot(
            snapshot_id=thread_id,
            thread_id=thread_id,
            run_id=run_id,
            status="running",
            generation=0,
            query=request.query,
            user_id=request.user_id,
            data_mode=settings.data_mode,
            created_at=created_at,
            updated_at=created_at,
        )
        directory = _task_directory(thread_id)
        directory_existed = directory.exists()
        owner_handle = _try_acquire_owner_lock(thread_id)
        if owner_handle is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "task_active_elsewhere",
                    "message": "The task is currently owned by another worker",
                },
            )
        try:
            with _task_mutation_lock(thread_id):
                if _task_is_deleted(thread_id):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "task_deleted",
                            "message": "A deleted task thread cannot be reused",
                        },
                    )
                directory = _task_directory(thread_id, create=True)
                reference_images = _bind_reference_images(thread_id, resolved_uploads)
                _persist_snapshot(snapshot)
            task = asyncio.create_task(
                _execute(request, run_id, directory, reference_images, owner_handle=owner_handle),
                name=f"shopping-agent:{thread_id}",
            )
            records[thread_id] = TaskRecord(
                run_id=run_id,
                snapshot=snapshot,
                task=task,
                owner_handle=owner_handle,
                generation=snapshot.generation,
            )
            owner_handle = None
        except Exception:
            _release_lock_handle(owner_handle)
            if not directory_existed:
                try:
                    with _task_mutation_lock(thread_id):
                        if not _task_is_deleted(thread_id):
                            _remove_task_directory(thread_id)
                except (OSError, ValueError):
                    logger.exception(
                        "failed to clean up an incomplete task directory",
                        extra={"thread_id": thread_id},
                    )
            raise
    return TaskStarted(thread_id=thread_id)


@app.get("/api/task/{thread_id}", response_model=TaskSnapshot)
async def get_task(thread_id: str) -> TaskSnapshot:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    async with _task_lock(thread_id):
        if _task_is_deleted(thread_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found", "message": "Task not found"},
            )
        record = records.get(thread_id)
        if record is not None and record.deleting:
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found", "message": "Task not found"},
            )
        if record is not None:
            return record.snapshot
        persisted = _load_snapshot(thread_id)
        if persisted is not None:
            return persisted
    raise HTTPException(
        status_code=404,
        detail={"code": "task_not_found", "message": "Task not found"},
    )


def _history_snapshots(user_id: str) -> list[TaskSnapshot]:
    snapshots: dict[str, TaskSnapshot] = {}
    root = output_root()
    for directory in root.iterdir():
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or not _THREAD_ID_PATTERN.fullmatch(directory.name)
        ):
            continue
        if directory.name in records:
            continue
        snapshot = _load_snapshot(directory.name)
        if snapshot is not None and snapshot.user_id == user_id:
            snapshots[snapshot.thread_id] = snapshot
    for thread_id, record in records.items():
        if record.snapshot.user_id == user_id:
            snapshots[thread_id] = record.snapshot
    return sorted(
        snapshots.values(),
        key=lambda snapshot: snapshot.updated_at,
        reverse=True,
    )


def _find_idempotent_child(
    parent_snapshot_id: str,
    command_key: str,
    relation: Literal["rerun", "constraint_relaxation"],
    user_id: str,
) -> TaskSnapshot | None:
    for snapshot in _history_snapshots(user_id):
        lineage = snapshot.lineage
        if (
            lineage is not None
            and lineage.parent_snapshot_id == parent_snapshot_id
            and lineage.command_idempotency_key == command_key
            and lineage.relation == relation
        ):
            return snapshot
    return None


def _lineage_for_child(
    parent: TaskSnapshot,
    *,
    relation: str,
    command_key: str,
    changed_constraints: list[ConstraintRelaxation] | None = None,
) -> SnapshotLineage:
    parent_snapshot_id = parent.snapshot_id or parent.thread_id
    root_snapshot_id = parent.lineage.root_snapshot_id if parent.lineage else parent_snapshot_id
    depth = parent.lineage.depth + 1 if parent.lineage else 1
    return SnapshotLineage(
        relation=relation,
        parent_snapshot_id=parent_snapshot_id,
        parent_thread_id=parent.thread_id,
        parent_run_id=parent.run_id,
        root_snapshot_id=root_snapshot_id,
        depth=depth,
        command_idempotency_key=command_key,
        changed_constraints=changed_constraints or [],
    )


def _start_child_task(
    parent: TaskSnapshot,
    lineage: SnapshotLineage,
    *,
    constraint_relaxation_changes: list[ConstraintRelaxationChange] | None = None,
) -> TaskSnapshot:
    if parent.resolved_intent is None:
        raise ValueError("a child task requires a resolved parent intent")

    child_thread_id = f"thread-{uuid.uuid4().hex[:12]}"
    created_at = _now()
    child = TaskSnapshot(
        snapshot_id=child_thread_id,
        thread_id=child_thread_id,
        run_id=uuid.uuid4().hex,
        status="running",
        generation=0,
        query=parent.query,
        user_id=parent.user_id,
        data_mode=parent.data_mode,
        created_at=created_at,
        updated_at=created_at,
        lineage=lineage.model_copy(deep=True),
        resolved_query=parent.resolved_query,
        resolved_intent=parent.resolved_intent.model_copy(deep=True),
        mode=parent.mode,
        working_assumptions=[item.model_copy(deep=True) for item in parent.working_assumptions],
        applied_preferences=parent.applied_preferences.model_copy(deep=True),
        task_overrides=[item.model_copy(deep=True) for item in parent.task_overrides],
        constraint_relaxations=[item.model_copy(deep=True) for item in lineage.changed_constraints],
    )
    directory = _task_directory(child_thread_id, create=True)
    owner_handle = _try_acquire_owner_lock(child_thread_id)
    if owner_handle is None:
        raise SnapshotPersistenceError("child task owner lock is already held")
    request = TaskRequest(
        query=parent.query,
        thread_id=child_thread_id,
        user_id=parent.user_id,
        upload_ids=[],
    )
    try:
        reference_images = _clone_reference_images(parent, child_thread_id)
        _persist_snapshot(child)
        task = asyncio.create_task(
            _execute(
                request,
                child.run_id,
                directory,
                reference_images,
                resolved_intent=parent.resolved_intent.model_copy(deep=True),
                resolved_query=parent.resolved_query,
                applied_preferences=parent.applied_preferences.model_copy(deep=True),
                constraint_relaxation_changes=(
                    [item.model_copy(deep=True) for item in constraint_relaxation_changes]
                    if constraint_relaxation_changes is not None
                    else None
                ),
                owner_handle=owner_handle,
            ),
            name=f"shopping-agent:{lineage.relation}:{child_thread_id}",
        )
        records[child_thread_id] = TaskRecord(
            run_id=child.run_id,
            snapshot=child,
            task=task,
            owner_handle=owner_handle,
            generation=child.generation,
        )
        owner_handle = None
    except Exception:
        _release_lock_handle(owner_handle)
        raise
    return child


@app.get("/api/research", response_model=ResearchHistoryResponse)
@app.get("/api/research/snapshots", response_model=ResearchHistoryResponse)
async def recent_research(
    user_id: str = Query(..., min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$"),
) -> ResearchHistoryResponse:
    return ResearchHistoryResponse(snapshots=_history_snapshots(user_id))


@app.get("/api/research/{thread_id}", response_model=TaskSnapshot)
async def get_research_snapshot(thread_id: str) -> TaskSnapshot:
    return await get_task(thread_id)


def _load_report_snapshot(thread_id: str, *, rebuild_missing: bool = True) -> TaskSnapshot:
    with _task_mutation_lock(thread_id):
        if _task_is_deleted(thread_id):
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "research_snapshot_not_found",
                    "message": "Research Snapshot not found",
                },
            )
        record = records.get(thread_id)
        if record is not None and record.deleting:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "research_snapshot_not_found",
                    "message": "Research Snapshot not found",
                },
            )
        snapshot = record.snapshot if record is not None else _read_persisted_snapshot(thread_id)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "research_snapshot_not_found",
                    "message": "Research Snapshot not found",
                },
            )
        if snapshot.status != "completed" or snapshot.result is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "reports_not_available",
                    "message": "Reports are available only for a completed Research Snapshot",
                },
            )
        files = report_file_links(snapshot)
        result = snapshot.result.model_copy(update={"files": files})
        effective = snapshot.model_copy(update={"result": result, "report_references": files})
        directory = _task_directory(thread_id)
        missing = [file for file in files if not (directory / file.name).is_file()]
        if missing and rebuild_missing:
            try:
                generate_reports(effective, directory)
            except ReportGenerationError as exc:
                logger.exception("failed to rebuild reports", extra={"thread_id": thread_id})
                raise HTTPException(
                    status_code=500,
                    detail={"code": "report_generation_failed", "message": str(exc)},
                ) from exc
        return effective


@app.get("/api/task/{thread_id}/reports", response_model=ReportListResponse)
@app.get("/api/reports/{thread_id}", response_model=ReportListResponse)
async def list_reports(thread_id: str) -> ReportListResponse:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    snapshot = _load_report_snapshot(thread_id)
    assert snapshot.result is not None
    return ReportListResponse(
        snapshot_id=snapshot.snapshot_id,
        snapshot_effective_at=snapshot.updated_at,
        files=snapshot.result.files,
    )


@app.post("/api/task/{thread_id}/reports", response_model=ReportGenerationResponse)
async def generate_task_reports(thread_id: str) -> ReportGenerationResponse:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    snapshot = _load_report_snapshot(thread_id)
    assert snapshot.result is not None
    return ReportGenerationResponse(
        snapshot_id=snapshot.snapshot_id,
        snapshot_effective_at=snapshot.updated_at,
        files=snapshot.result.files,
        idempotent=True,
    )


@app.post("/api/task/{thread_id}/rerun", response_model=TaskRerunResponse)
async def rerun_task(
    thread_id: str,
    command: RerunCommand,
) -> TaskRerunResponse:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    requested_key = command.idempotency_key
    command_key = requested_key or uuid.uuid4().hex
    async with _task_lock(thread_id):
        parent_record = records.get(thread_id)
        parent = parent_record.snapshot if parent_record is not None else _load_snapshot(thread_id)
        if parent is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "research_snapshot_not_found",
                    "message": "Research Snapshot not found",
                },
            )
        if parent.user_id != command.user_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "research_snapshot_not_found",
                    "message": "Research Snapshot not found",
                },
            )
        with _command_lock(thread_id):
            existing = _find_idempotent_child(
                parent.snapshot_id or parent.thread_id,
                command_key,
                "rerun",
                command.user_id,
            )
            if existing is not None and existing.lineage is not None:
                return TaskRerunResponse(
                    thread_id=existing.thread_id,
                    parent_snapshot_id=parent.snapshot_id or parent.thread_id,
                    lineage=existing.lineage,
                    idempotent=True,
                )
            if parent.status != "completed" or parent.result is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "rerun_not_available",
                        "message": "Research Rerun requires a completed Research Snapshot",
                    },
                )
            if parent.resolved_intent is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "rerun_intent_missing",
                        "message": "The Research Snapshot has no resolved intent to rerun",
                    },
                )
            lineage = _lineage_for_child(
                parent,
                relation="rerun",
                command_key=command_key,
            )
            child = _start_child_task(parent, lineage)
    return TaskRerunResponse(
        thread_id=child.thread_id,
        parent_snapshot_id=parent.snapshot_id or parent.thread_id,
        lineage=lineage,
    )


@app.post("/api/task/{thread_id}/relaxation", response_model=TaskRerunResponse)
async def relax_task(
    thread_id: str,
    command: ConstraintRelaxationCommand,
) -> TaskRerunResponse:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    if not command.confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "constraint_relaxation_confirmation_required",
                "message": "Constraint Relaxation requires explicit shopper confirmation",
            },
        )
    if not command.constraint_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "constraint_relaxation_missing_constraints",
                "message": "Select at least one constraint",
            },
        )
    command_key = command.idempotency_key or uuid.uuid4().hex
    async with _task_lock(thread_id):
        parent_record = records.get(thread_id)
        parent = parent_record.snapshot if parent_record is not None else _load_snapshot(thread_id)
        if parent is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "research_snapshot_not_found",
                    "message": "Research Snapshot not found",
                },
            )
        if parent.user_id != command.user_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "research_snapshot_not_found",
                    "message": "Research Snapshot not found",
                },
            )
        with _command_lock(thread_id):
            existing = _find_idempotent_child(
                parent.snapshot_id or parent.thread_id,
                command_key,
                "constraint_relaxation",
                command.user_id,
            )
            if existing is not None and existing.lineage is not None:
                return TaskRerunResponse(
                    thread_id=existing.thread_id,
                    parent_snapshot_id=parent.snapshot_id or parent.thread_id,
                    lineage=existing.lineage,
                    idempotent=True,
                )
            if (
                parent.status != "completed"
                or parent.result is None
                or parent.resolved_intent is None
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "relaxation_not_available",
                        "message": "Constraint Relaxation requires a completed Research Snapshot",
                    },
                )
            changes_by_id = {change.constraint_id: change for change in command.changes}
            known_ids = {constraint.id for constraint in parent.resolved_intent.hard_constraints}
            unknown = sorted(set(command.constraint_ids) - known_ids)
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "constraint_relaxation_unknown_constraint",
                        "message": f"Unknown Hard Constraint: {', '.join(unknown)}",
                    },
                )
            changes = [
                changes_by_id.get(
                    constraint_id, ConstraintRelaxationChange(constraint_id=constraint_id)
                )
                for constraint_id in command.constraint_ids
            ]
            resolved_constraints = {
                item.id: item for item in parent.resolved_intent.hard_constraints
            }
            applied = [
                ConstraintRelaxation(
                    constraint_id=change.constraint_id,
                    previous=resolved_constraints[change.constraint_id],
                    replacement=change.replacement,
                    action="replaced" if change.replacement is not None else "removed",
                    reason=change.reason,
                )
                for change in changes
            ]
            lineage = _lineage_for_child(
                parent,
                relation="constraint_relaxation",
                command_key=command_key,
                changed_constraints=applied,
            )
            child = _start_child_task(
                parent,
                lineage,
                constraint_relaxation_changes=changes,
            )
    return TaskRerunResponse(
        thread_id=child.thread_id,
        parent_snapshot_id=parent.snapshot_id or parent.thread_id,
        lineage=lineage,
    )


def _reference_images(snapshot: TaskSnapshot) -> list[dict[str, Any]]:
    for event in snapshot.events:
        if event.event != "session_created":
            continue
        references = event.data.get("reference_images")
        if isinstance(references, list) and all(isinstance(item, dict) for item in references):
            return references
    return []


def _is_duplicate_clarification_response(
    snapshot: TaskSnapshot, response: str, field: str | None = None
) -> str | None:
    normalized_response = response.strip()
    for event in reversed(snapshot.events):
        if event.event != "clarification_resolved":
            continue
        try:
            resolved = ClarificationResolvedEventData.model_validate(event.data)
            if field is not None and resolved.field != field:
                continue
            normalized = normalize_clarification_response(resolved.field, normalized_response)
            recorded = resolved.resolved_value or normalize_clarification_response(
                resolved.field,
                resolved.response,
            )
        except (InvalidClarificationResponse, ValueError):
            continue
        if normalized == recorded:
            return resolved.field
    return None


async def _clarify_task_locked(
    thread_id: str,
    command: ClarificationCommand,
) -> ClarificationCommandResponse:
    record = records.get(thread_id)
    persisted = _read_persisted_snapshot(thread_id)
    snapshot = persisted or (record.snapshot if record is not None else None)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "task_not_found", "message": "Task not found"},
        )
    if snapshot.status != "awaiting_clarification":
        duplicate_field = _is_duplicate_clarification_response(snapshot, command.response)
        if duplicate_field is not None:
            return ClarificationCommandResponse(
                thread_id=thread_id,
                field=duplicate_field,
                idempotent=True,
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "clarification_not_awaiting",
                "message": "The task is not awaiting clarification",
            },
        )

    pending = snapshot.clarification
    if pending is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "clarification_state_missing",
                "message": "The task has no pending clarification",
            },
        )
    duplicate_field = _is_duplicate_clarification_response(
        snapshot, command.response, pending.field
    )
    if duplicate_field is not None:
        return ClarificationCommandResponse(
            thread_id=thread_id,
            field=pending.field,
            idempotent=True,
        )
    try:
        normalized_response = normalize_clarification_response(
            pending.field,
            command.response,
        )
    except InvalidClarificationResponse as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "clarification_invalid_response",
                "field": exc.field,
                "message": str(exc),
            },
        ) from exc

    owner_handle: TextIO | None = None
    created_record = False
    if record is None:
        owner_handle = _try_acquire_owner_lock(thread_id)
        if owner_handle is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "task_active_elsewhere",
                    "message": "The task is currently owned by another worker",
                },
            )
        record = TaskRecord(
            run_id=snapshot.run_id,
            snapshot=snapshot,
            task=asyncio.current_task(),
            owner_handle=owner_handle,
        )
        records[thread_id] = record
        created_record = True
    elif record.owner_handle is None:
        owner_handle = _try_acquire_owner_lock(thread_id)
        if owner_handle is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "task_active_elsewhere",
                    "message": "The task is currently owned by another worker",
                },
            )
        record.owner_handle = owner_handle
    else:
        owner_handle = record.owner_handle

    try:
        await monitor.emit(
            thread_id,
            "clarification_resolved",
            message=f"已补充{pending.field}信息，继续研究",
            data={
                "field": pending.field,
                "reason_code": pending.reason_code,
                "response": command.response,
                "resolved_value": normalized_response,
            },
            run_id=record.run_id,
        )
        request = TaskRequest(
            query=snapshot.query,
            thread_id=thread_id,
            user_id=snapshot.user_id,
            upload_ids=[],
        )
        record.task = asyncio.create_task(
            _execute(
                request,
                record.run_id,
                _task_directory(thread_id, create=True),
                _reference_images(snapshot),
                resume=True,
                clarification_answers=record.snapshot.clarification_answers,
                owner_handle=owner_handle,
            ),
            name=f"shopping-agent:resume:{thread_id}",
        )
    except Exception:
        if created_record:
            records.pop(thread_id, None)
        if record.owner_handle is owner_handle:
            record.owner_handle = None
        _release_lock_handle(owner_handle)
        raise
    return ClarificationCommandResponse(
        thread_id=thread_id,
        field=pending.field,
        idempotent=False,
    )


@app.post(
    "/api/task/{thread_id}/clarification",
    response_model=ClarificationCommandResponse,
)
async def clarify_task(
    thread_id: str,
    command: ClarificationCommand,
) -> ClarificationCommandResponse:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")

    async with _task_lock(thread_id):
        directory = _task_directory(thread_id)
        if not directory.is_dir():
            return await _clarify_task_locked(thread_id, command)
        with _command_lock(thread_id):
            return await _clarify_task_locked(thread_id, command)


@app.delete("/api/task/{thread_id}", response_model=TaskDeleteResponse)
async def delete_task(
    thread_id: str,
    command: TaskDeleteCommand,
) -> TaskDeleteResponse:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    requested_user_id = command.user_id
    lock = _task_lock(thread_id)
    try:
        async with lock:
            tombstone = _read_tombstone(thread_id)
            record = records.get(thread_id)
            directory = _task_directory(thread_id)
            snapshot = (
                record.snapshot if record is not None else _read_persisted_snapshot(thread_id)
            )
            owner_handle = record.owner_handle if record is not None else None

            if tombstone is not None:
                owner = tombstone.user_id
                if owner is None or requested_user_id != owner:
                    raise HTTPException(
                        status_code=404,
                        detail={"code": "task_not_found", "message": "Task not found"},
                    )
                if record is not None:
                    record.deleting = True
            elif snapshot is not None:
                if requested_user_id != snapshot.user_id:
                    raise HTTPException(
                        status_code=404,
                        detail={"code": "task_not_found", "message": "Task not found"},
                    )
                if record is not None:
                    record.deleting = True
                try:
                    tombstone = _write_tombstone(
                        thread_id,
                        user_id=snapshot.user_id,
                        generation=snapshot.generation + 1,
                    )
                except Exception:
                    if record is not None:
                        record.deleting = False
                    raise
            elif directory.exists():
                raise HTTPException(
                    status_code=404,
                    detail={"code": "task_not_found", "message": "Task not found"},
                )
            else:
                return TaskDeleteResponse(thread_id=thread_id)

            if (
                record is not None
                and not record.task.done()
                and record.task is not asyncio.current_task()
            ):
                record.task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await record.task

            if record is not None and record.owner_handle is owner_handle:
                record.owner_handle = None
                _release_lock_handle(owner_handle)

            records.pop(thread_id, None)
            await manager.discard(thread_id)
            monitor.discard(thread_id)
            try:
                with _task_mutation_lock(thread_id):
                    if _task_is_deleted(thread_id):
                        _remove_task_directory(thread_id)
            except (OSError, ValueError) as exc:
                logger.exception("failed to delete task artifacts", extra={"thread_id": thread_id})
                raise HTTPException(
                    status_code=500,
                    detail={"code": "task_deletion_failed", "message": "Failed to delete task"},
                ) from exc
            _remove_task_control_files(thread_id)
    finally:
        if task_locks.get(thread_id) is lock:
            task_locks.pop(thread_id, None)

    return TaskDeleteResponse(thread_id=thread_id)


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str) -> dict[str, str]:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    async with _task_lock(thread_id):
        if _task_is_deleted(thread_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found", "message": "Task not found"},
            )
        record = records.get(thread_id)
        if record is None:
            persisted = _load_snapshot(thread_id)
            if persisted is None:
                raise HTTPException(status_code=404, detail="task not found")
            if persisted.status not in {"running", "awaiting_clarification"}:
                return {"status": persisted.status, "thread_id": thread_id}
            if persisted.status == "awaiting_clarification":
                owner_handle = _try_acquire_owner_lock(thread_id)
                if owner_handle is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "task_active_elsewhere",
                            "message": "The task is currently owned by another worker",
                        },
                    )
                record = TaskRecord(
                    run_id=persisted.run_id,
                    snapshot=persisted,
                    task=asyncio.current_task(),
                    owner_handle=owner_handle,
                )
                records[thread_id] = record
                try:
                    await monitor.emit(
                        thread_id,
                        "task_cancelled",
                        data={"thread_id": thread_id},
                        run_id=record.run_id,
                    )
                finally:
                    record.owner_handle = None
                    _release_lock_handle(owner_handle)
                return {"status": "cancelled", "thread_id": thread_id}
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "task_active_elsewhere",
                    "message": "The task is currently owned by another worker",
                },
            )
        if record.deleting:
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found", "message": "Task not found"},
            )
        if record.snapshot.status == "awaiting_clarification":
            owner_handle = record.owner_handle
            if owner_handle is None:
                owner_handle = _try_acquire_owner_lock(thread_id)
                if owner_handle is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "task_active_elsewhere",
                            "message": "The task is currently owned by another worker",
                        },
                    )
                record.owner_handle = owner_handle
            try:
                await monitor.emit(
                    thread_id,
                    "task_cancelled",
                    data={"thread_id": thread_id},
                    run_id=record.run_id,
                )
            finally:
                if record.owner_handle is owner_handle:
                    record.owner_handle = None
                _release_lock_handle(owner_handle)
            return {"status": "cancelled", "thread_id": thread_id}
        if record.snapshot.status != "running" or record.task.done():
            return {"status": record.snapshot.status, "thread_id": thread_id}
        record.task.cancel()
        with suppress(asyncio.CancelledError):
            await record.task
        current = records.get(thread_id)
        if current is None or current.run_id != record.run_id:
            persisted = _load_snapshot(thread_id)
            if persisted is None:
                raise HTTPException(status_code=500, detail="task state unavailable")
            return {"status": persisted.status, "thread_id": thread_id}
        if current.snapshot.status == "running":
            await monitor.emit(
                thread_id,
                "task_cancelled",
                data={"thread_id": thread_id},
                run_id=current.run_id,
            )
        return {"status": current.snapshot.status, "thread_id": thread_id}


@app.websocket("/ws/{thread_id}")
async def task_socket(websocket: WebSocket, thread_id: str) -> None:
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        await websocket.close(code=1008, reason="invalid thread id")
        return
    async with _task_lock(thread_id):
        if _task_is_deleted(thread_id):
            await websocket.close(code=1008, reason="task not found")
            return
        record = records.get(thread_id)
        if record is not None and record.deleting:
            await websocket.close(code=1008, reason="task not found")
            return
        if record is None and _load_snapshot(thread_id) is None:
            await websocket.close(code=1008, reason="task not found")
            return
        connected = await manager.connect(
            thread_id,
            websocket,
            bootstrap=lambda: _snapshot_message(thread_id),
        )
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


def _resolve_uploads(upload_ids: list[str]) -> list[ResolvedUpload]:
    references: list[ResolvedUpload] = []
    root = upload_root().resolve()
    for upload_id in upload_ids:
        if not _UPLOAD_ID_PATTERN.fullmatch(upload_id):
            raise HTTPException(status_code=422, detail=f"invalid upload id: {upload_id}")
        matches: list[Path] = []
        for suffix in _ALLOWED_UPLOAD_TYPES.values():
            path = root / f"{upload_id}{suffix}"
            if path.is_symlink():
                continue
            if path.is_file() and path.resolve() == path:
                matches.append(path)
        if len(matches) != 1:
            raise HTTPException(status_code=422, detail=f"upload not found: {upload_id}")
        path = matches[0]
        content_type = next(
            content_type
            for content_type, suffix in _ALLOWED_UPLOAD_TYPES.items()
            if suffix == path.suffix
        )
        references.append(
            ResolvedUpload(
                upload_id=upload_id,
                name=path.name,
                content_type=content_type,
                size=path.stat().st_size,
                source=path,
            )
        )
    return references


def _write_reference_copy(
    destination_directory: Path,
    upload: ResolvedUpload,
    *,
    bound_at: str,
    source_root: Path | None = None,
) -> dict[str, Any]:
    if not _UPLOAD_ID_PATTERN.fullmatch(upload.upload_id):
        raise ValueError("invalid upload id")
    if (
        upload.name != f"{upload.upload_id}{Path(upload.name).suffix}"
        or Path(upload.name).suffix not in _ALLOWED_UPLOAD_TYPES.values()
    ):
        raise ValueError("invalid reference image name")
    source = upload.source.resolve()
    if source_root is not None and (
        source.parent != source_root.resolve() or source.name != upload.name
    ):
        raise ValueError("reference image source is outside upload root")
    if not source.is_file():
        raise FileNotFoundError(source)

    destination_directory.mkdir(parents=True, exist_ok=True)
    if destination_directory.is_symlink():
        raise ValueError("reference image directory must not be a symlink")
    destination = safe_join(destination_directory, upload.name)
    raw_destination = destination_directory / upload.name
    if raw_destination.is_symlink() or destination != raw_destination:
        raise ValueError("reference image destination is not canonical")
    temporary = destination_directory / f".{upload.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    except OSError:
        with suppress(OSError):
            temporary.unlink()
        raise
    return ReferenceImageBinding(
        upload_id=upload.upload_id,
        name=upload.name,
        content_type=upload.content_type,
        size=upload.size,
        bound_at=bound_at,
    ).model_dump(mode="json")


def _bind_reference_images(
    thread_id: str,
    uploads: list[ResolvedUpload],
) -> list[dict[str, Any]]:
    if not uploads:
        return []
    directory = _task_directory(thread_id, create=True)
    references_directory = safe_join(directory, "reference-images")
    bound_at = _now()
    return [
        _write_reference_copy(
            references_directory,
            upload,
            bound_at=bound_at,
            source_root=upload_root(),
        )
        for upload in uploads
    ]


def _clone_reference_images(parent: TaskSnapshot, child_thread_id: str) -> list[dict[str, Any]]:
    references = _reference_images(parent)
    if not references:
        return []
    parent_directory = _task_directory(parent.thread_id)
    child_directory = _task_directory(child_thread_id, create=True)
    parent_references = safe_join(parent_directory, "reference-images")
    child_references = safe_join(child_directory, "reference-images")
    bound_at = _now()
    cloned: list[dict[str, Any]] = []
    for raw in references:
        try:
            binding = ReferenceImageBinding.model_validate(raw)
        except ValueError:
            continue
        source = safe_join(parent_references, binding.name)
        if source.is_symlink() or not source.is_file():
            continue
        upload = ResolvedUpload(
            upload_id=binding.upload_id,
            name=binding.name,
            content_type=binding.content_type,
            size=binding.size,
            source=source,
        )
        cloned.append(_write_reference_copy(child_references, upload, bound_at=bound_at))
    return cloned


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
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_file_path", "message": "Invalid file path"},
        )
    if _task_is_deleted(thread_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "file_not_found", "message": "File not found"},
        )
    record = records.get(thread_id)
    original = record.snapshot if record is not None else _load_snapshot(thread_id)
    if original is None or original.result is None or original.status != "completed":
        raise HTTPException(
            status_code=404,
            detail={"code": "file_not_found", "message": "File not found"},
        )
    snapshot = _load_report_snapshot(thread_id)
    assert snapshot.result is not None
    file_ref = next((file for file in snapshot.result.files if file.name == name), None)
    if file_ref is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "file_not_found", "message": "File not found"},
        )
    try:
        directory = _task_directory(thread_id)
        raw_path = directory / name
        path = safe_join(directory, name)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_file_path", "message": "Invalid file path"},
        ) from exc
    if path != raw_path or raw_path.is_symlink() or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "file_not_found", "message": "File not found"},
        )
    return FileResponse(
        path,
        filename=file_ref.name,
        media_type=file_ref.content_type or "application/octet-stream",
        headers={"X-Report-ID": file_ref.file_id or f"{snapshot.snapshot_id}:{file_ref.format}"},
    )


def _preference_record(value: dict[str, Any]) -> dict[str, list[str]]:
    record = RememberedPreference.model_validate(
        {field: value.get(field, []) for field in RememberedPreference.model_fields}
    )
    return {field: values for field, values in record.model_dump(mode="json").items() if values}


def _preference_response(user_id: str, value: dict[str, Any]) -> PreferenceResponse:
    return PreferenceResponse(
        user_id=user_id,
        preferences=_preference_record(value),
        backend=preference_store.backend_status,
    )


def _preference_error(exc: PreferenceStoreError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "preference_store_unavailable",
            "message": "Remembered Preference backend is unavailable",
            "fallback_reason": str(exc),
        },
    )


@app.get("/api/preferences/{user_id}", response_model=PreferenceResponse)
async def get_preferences(user_id: str) -> PreferenceResponse:
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise HTTPException(status_code=422, detail="invalid user id")
    try:
        return _preference_response(user_id, await preference_store.get(user_id))
    except PreferenceStoreError as exc:
        raise _preference_error(exc) from exc


async def _update_preferences(user_id: str, command: MemoryCommand) -> PreferenceResponse:
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise HTTPException(status_code=422, detail="invalid user id")
    try:
        await execute_memory_commands(preference_store, user_id, [command])
        return _preference_response(user_id, await preference_store.get(user_id))
    except PreferenceStoreError as exc:
        raise _preference_error(exc) from exc


@app.put("/api/preferences/{user_id}", response_model=PreferenceResponse)
async def update_preferences(user_id: str, command: MemoryCommand) -> PreferenceResponse:
    return await _update_preferences(user_id, command)


@app.post("/api/preferences/{user_id}/commands", response_model=PreferenceResponse)
async def apply_preference_command(user_id: str, command: MemoryCommand) -> PreferenceResponse:
    return await _update_preferences(user_id, command)


@app.delete("/api/preferences/{user_id}", response_model=PreferenceDeleteResponse)
async def delete_preferences(user_id: str) -> PreferenceDeleteResponse:
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise HTTPException(status_code=422, detail="invalid user id")
    try:
        await preference_store.delete(user_id)
    except PreferenceStoreError as exc:
        raise _preference_error(exc) from exc
    return PreferenceDeleteResponse(
        user_id=user_id,
        backend=preference_store.backend_status,
    )
