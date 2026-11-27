"""End-to-end submit flow: guardrails, stance lock, receipts, qualifier, demo safety."""

from trenchcoat.models import Candidate
from trenchcoat.submit import poll_receipts, submit_candidate


def _draft(store, market, stance="YES", claim="Office reported 4.2% on 2026-11-20"):
    c = Candidate(market_address=market.address, claim=claim, stance=stance,
                  note=f"{claim}. Source: official table, published 2026-11-20, retrieved 2026-11-27.",
                  links=["https://example.org/table"], score=0.8)
    from trenchcoat.guardrails import simhash64

    c.simhash = simhash64(c.claim + c.note)
    store.save_candidate(c)
    return c


def test_submit_locks_stance_and_records_receipt(cfg, seeded):
    store = seeded
    m = store.markets()[0]
    cand = _draft(store, m, stance="NO")
    res = submit_candidate(cfg, store, cand.id)
    assert res["ok"], res
    assert store.locked_stance(m.address) == "NO"
    sub = store.submission(res["submission_id"])
    assert sub is not None
    assert sub.idempotency_key == cand.idempotency_key()
    poll_receipts(cfg, store)
    sub = store.submission(res["submission_id"])
    assert sub.is_terminal


def test_opposing_stance_blocked_after_lock(cfg, seeded):
    store = seeded
    m = store.markets()[0]
    first = _draft(store, m, stance="NO", claim="Fact A 4.2% on 2026-11-20")
    assert submit_candidate(cfg, store, first.id)["ok"]
    second = _draft(store, m, stance="YES", claim="Fact B 5.1% on 2026-11-21")
    res = submit_candidate(cfg, store, second.id)
    assert not res["ok"]
    assert any("Stance conflict" in v for v in res.get("violations", []))
    assert store.candidate(second.id).status == "blocked"


def test_idempotent_resubmit_returns_prior(cfg, seeded):
    store = seeded
    m = store.markets()[1]
    cand = _draft(store, m)
    r1 = submit_candidate(cfg, store, cand.id)
    assert r1["ok"]
    # force candidate back to draft to simulate a retry path
    c = store.candidate(cand.id)
    c.status = "draft"
    store.save_candidate(c)
    r2 = submit_candidate(cfg, store, cand.id)
    assert r2["ok"]
    assert r2["submission_id"] == r1["submission_id"]
    assert r2.get("replayed") is True


def test_demo_never_submits_live(cfg, seeded):
    store = seeded
    cfg.delphi_mode = "live"
    cfg.delphi_api_key = "fake-key-for-test"
    demo = next(c for c in store.candidates() if c.demo and c.status == "draft")
    res = submit_candidate(cfg, store, demo.id)
    assert not res["ok"]
    assert "demo" in res["error"]


def test_qualifier_progress(cfg, store):
    from trenchcoat.models import Submission

    for i in range(10):
        store.save_submission(Submission(
            id=f"q{i}", market_address=f"0x{'abc'[i % 3] * 40}", status="judged",
            verdict="accepted", bounty_amount="1000000", bounty_state="held", mode="mock"))
    stats = store.stats()
    assert stats["judged_total"] == 10
    assert stats["markets_covered"] == 3
    assert stats["qualified"] is True
    assert stats["earned_usdc"] == 10.0


def test_manipulation_halts_autopilot(cfg, store):
    from trenchcoat.guardrails import acceptance_guard_tripped
    from trenchcoat.models import Submission, utcnow

    store.save_submission(Submission(id="bad", market_address="0x" + "a" * 40,
                                     status="judged", verdict="manipulation",
                                     judged_at=utcnow(), mode="mock"))
    assert acceptance_guard_tripped(store) is not None


def test_dryrun_does_not_count_slots(cfg, store):
    from trenchcoat.models import Submission

    store.save_submission(Submission(id="dry1", market_address="0x" + "a" * 40,
                                     status="judged", verdict="accepted", mode="dry-run"))
    assert store.slots_used("0x" + "a" * 40) == 0
