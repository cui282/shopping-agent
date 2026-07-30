from __future__ import annotations

import re

KNOWN_CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "耳机": ("耳机", "耳塞"),
    "咖啡机": ("咖啡机",),
    "背包": ("背包", "双肩包", "通勤包"),
    "键盘": ("键盘", "机械键盘"),
    "运动鞋": ("运动鞋", "跑鞋", "球鞋"),
    "手机": ("手机", "智能手机"),
}

_CLAUSE_SPLIT = re.compile(r"[，。；,;！？!?\n]+")
_SPACE = re.compile(r"\s+")
_MEASURE_WORD = r"(?:一)?(?:个|款|台|部|双|套|只|件|些|根|把|盒|瓶|块|对)"
_EXPLICIT_REQUEST_PREFIX = re.compile(
    r"^(?:(?:请)?帮我(?:找|买|选|挑|推荐|对比|比较|比价|看看)?|"
    r"我(?:想|需要)(?:要|买|找|选|挑)?|想(?:要|买|找|选|挑)?|"
    r"(?:请)?(?:推荐|找|买|选|挑|对比|比较|比价|看看)|有没有|需要)"
)
_REQUEST_PREFIX = re.compile(
    _EXPLICIT_REQUEST_PREFIX.pattern + rf"\s*(?:一下)?\s*(?:{_MEASURE_WORD})?\s*"
)
_RECIPIENT_PURCHASE_PREFIX = re.compile(
    rf"^给[^，。；,;]{{1,16}}?(?:买|选|找)\s*(?:{_MEASURE_WORD})?\s*"
)
_NEGATED_CLAUSE = re.compile(r"^(?:不要|不买|不选|避免|排除|不考虑)")
_QUANTIFIER_PREFIX = re.compile(rf"^(?:{_MEASURE_WORD})\s*")
_COMPARISON_REQUEST = re.compile(
    r"^(?:(?:请)?帮我|我想(?:要)?|想(?:要)?|我需要|请)?\s*(?:对比|比较|比价)"
)
_PRICE_PHRASE = re.compile(
    r"(?:预算\s*)?(?:人民币|RMB|CNY|[¥￥])?\s*\d+(?:\.\d+)?\s*(?:元|块)"
    r"(?:\s*(?:左右|上下|以内|以下|之内|不超过))?\s*的?",
    re.IGNORECASE,
)
_TRAILING_CONSTRAINT = re.compile(
    r"(?<=.)(?:适合|用于|需要|要求|重点(?:看|关注)|不要|不含|必须|希望|最好)"
)
_BUDGET_ONLY = re.compile(r"^(?:预算|价格|价位|控制在|不超过|低于|少于|以内|以下)\s*$")
_MAX_SUBJECT_LENGTH = 32


def extract_budget_cny(query: str) -> float | None:
    match = re.search(r"(?:预算|不超过|控制在|低于|少于)[^\d]{0,6}(\d+(?:\.\d+)?)", query)
    if match is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*元", query)
    return float(match.group(1)) if match else None


def _clean_subject_clause(clause: str) -> str:
    comparison_request = bool(_COMPARISON_REQUEST.match(clause.strip()))
    value = _RECIPIENT_PURCHASE_PREFIX.sub("", clause.strip())
    value = _REQUEST_PREFIX.sub("", value)
    value = _QUANTIFIER_PREFIX.sub("", value)
    value = _PRICE_PHRASE.sub("", value)
    # Price phrases can precede the actual request, for example "预算5000元买手机".
    value = _REQUEST_PREFIX.sub("", value)
    value = _QUANTIFIER_PREFIX.sub("", value)
    value = value.strip(" ：:、，,。；;！？!?的")
    if not value or _BUDGET_ONLY.fullmatch(value):
        return ""

    constraint = _TRAILING_CONSTRAINT.search(value)
    if constraint:
        value = value[: constraint.start()].strip()

    if "的" in value:
        parts = [part.strip() for part in value.split("的") if part.strip()]
        if len(parts) >= 2:
            tail = parts[-1]
            candidate = parts[-2] if comparison_request else tail
            if 1 <= len(candidate) <= _MAX_SUBJECT_LENGTH:
                value = candidate

    return value.strip(" ：:、，,。；;！？!?")[:_MAX_SUBJECT_LENGTH]


def extract_product_subject(query: str) -> str:
    """Return a concise product subject without collapsing unknown products to a generic label."""

    normalized = _SPACE.sub(" ", query).strip()
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(normalized) if clause.strip()]
    explicit = [
        clause
        for clause in clauses
        if _EXPLICIT_REQUEST_PREFIX.match(clause) or _RECIPIENT_PURCHASE_PREFIX.match(clause)
    ]
    remaining = [clause for clause in clauses if clause not in explicit]
    for clause in (*explicit, *remaining):
        if _NEGATED_CLAUSE.match(clause):
            continue
        subject = _clean_subject_clause(clause)
        if subject:
            for category, terms in KNOWN_CATEGORY_TERMS.items():
                if any(subject == term or subject.endswith(term) for term in terms):
                    return category
            return subject

    return normalized[:_MAX_SUBJECT_LENGTH] or "商品"
