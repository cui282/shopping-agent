"""Print AGUI events for one Shopping Agent task until it reaches a terminal state."""

from __future__ import annotations

import argparse
import asyncio
import json

import websockets


async def watch(url: str) -> None:
    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as socket:
        async for raw_message in socket:
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                print(raw_message)
                continue

            print(json.dumps(message, ensure_ascii=False, indent=2))
            if message.get("event") in {"task_result", "task_cancelled", "error"}:
                return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("thread_id", help="thread_id returned by POST /api/task")
    parser.add_argument(
        "--base",
        default="ws://127.0.0.1:8000",
        help="WebSocket origin without a trailing slash",
    )
    args = parser.parse_args()
    asyncio.run(watch(f"{args.base.rstrip('/')}/ws/{args.thread_id}"))


if __name__ == "__main__":
    main()
