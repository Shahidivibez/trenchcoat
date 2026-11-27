"""LLM router fallback + JSON extraction + search parsing (offline)."""

import json
from datetime import UTC

from trenchcoat.llm import HeuristicProvider, LLMRouter, _extract_json
from trenchcoat.research.search import _clean_ddg_url


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_bare_with_prose():
    assert _extract_json('Here you go: {"a": 1} hope that helps') == {"a": 1}


def test_extract_json_array():
    assert _extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_invalid():
    assert _extract_json("no json here") is None


def test_heuristic_queries():
    p = HeuristicProvider()
    res = p.complete("TASK: queries\nQUESTION: Will Swiss turnout be at least 50%?")
    data = json.loads(res.text)
    assert len(data["queries"]) >= 2
    assert res.provider == "heuristic"


def test_router_falls_back_to_heuristic(cfg, store):
    router = LLMRouter(cfg, store)  # no keys configured
    res = router.complete("TASK: queries\nQUESTION: Will X happen?")
    assert res.provider == "heuristic"


def test_router_json_task(cfg, store):
    router = LLMRouter(cfg, store)
    data, provider = router.json_task("TASK: evaluate\nQUESTION: Will CPI be 2.8%?\nTEXT: CPI was 2.7% in 2026.")
    assert isinstance(data, dict)
    assert "relevance" in data


def test_ddg_url_unwrap():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpage&rut=abc"
    assert _clean_ddg_url(wrapped) == "https://example.org/page"
    assert _clean_ddg_url("https://example.org/direct") == "https://example.org/direct"


def test_market_ranking_prefers_uncertainty(cfg, store):
    from datetime import datetime, timedelta

    from trenchcoat.models import Market
    from trenchcoat.scout import rank_markets

    cutoff = (datetime.now(UTC) + timedelta(hours=30)).isoformat()
    m1 = Market(address="0x" + "1" * 40, question="q1", status="open", odds_yes=0.5,
                evidence_cutoff_at=cutoff, judgement_pot="20000000", category="local_language")
    m2 = Market(address="0x" + "2" * 40, question="q2", status="open", odds_yes=0.95,
                evidence_cutoff_at=cutoff, judgement_pot="20000000")
    m3 = Market(address="0x" + "3" * 40, question="q3", status="open", odds_yes=0.5,
                evidence_cutoff_at=cutoff, judgement_pot="20000000")
    ranked = rank_markets(cfg, store, [m2, m3, m1])
    assert ranked[0].address == m1.address  # 50/50 + local_language blind spot
    assert m2 not in ranked  # odds outside 20-80%
