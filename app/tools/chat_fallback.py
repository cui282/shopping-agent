from __future__ import annotations


async def chat_fallback(message: str) -> str:
    """Respond when a message does not contain enough shopping intent."""

    cleaned = message.strip()
    if not cleaned:
        return "请告诉我想买什么，以及预算或必须满足的条件。"
    return f"我可以帮你比较“{cleaned[:80]}”。补充预算和收货地后，结果会更准确。"
