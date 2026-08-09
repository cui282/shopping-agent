from __future__ import annotations

from app.evolution.prompt_ab import finish_ab_test, prompt_for_user, start_ab_test
from app.evolution.prompt_versions import PromptVersion, PromptVersionStore
from app.evolution.strategy_extractor import extract_strategy
from app.memory.strategy import StrategyLibrary


def test_prompt_versions_support_activation_and_hash_routing(tmp_path) -> None:
    store = PromptVersionStore(tmp_path)
    base = PromptVersion(version="v1.0.0", content="base", changelog="initial")
    candidate = PromptVersion(version="v1.1.0", content="candidate", changelog="policy")
    store.save(base)
    store.activate(base.version)
    assert prompt_for_user("fallback", "user-a", store=store) == "base"
    start_ab_test(candidate, ratio=1.0, store=store)
    assert prompt_for_user("fallback", "user-a", store=store) == "candidate"
    finish_ab_test(promote=False, store=store)
    assert prompt_for_user("fallback", "user-a", store=store) == "base"


def test_strategy_extraction_is_review_gated_and_relevant() -> None:
    assert extract_strategy(query="耳机", tool_sequence=["planner"], rubric_score=0.9) is None
    strategy = extract_strategy(
        query="预算 300 的旅行三件套",
        tool_sequence=["planner", "item_search", "price_compare", "shopping_summary"],
        rubric_score=0.9,
    )
    assert strategy is not None
    library = StrategyLibrary()
    library.put_strategy(strategy)
    assert (
        library.read_relevant_strategies("旅行三件套", top_k=1)[0].strategy_id
        == strategy.strategy_id
    )
    assert library.record_use(strategy.strategy_id).use_count == 1
