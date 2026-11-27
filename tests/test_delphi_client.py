"""Mock client contract tests: terms gate, idempotent replay, slots, judging."""

import pytest

from trenchcoat.delphi import DelphiAPIError, MockClient, TermsNotAccepted, get_client


@pytest.fixture()
def client(cfg, store):
    from trenchcoat.demo import seed_demo

    seed_demo(cfg, store)  # loads markets + accepts mock terms
    return get_client(cfg, store)


def test_mode_factory(cfg, store):
    assert get_client(cfg, store).mode == "mock"
    cfg.delphi_mode = "dry-run"
    assert get_client(cfg, store).mode == "dry-run"
    cfg.delphi_mode = "live"
    with pytest.raises(DelphiAPIError):
        get_client(cfg, store)  # no key


def test_terms_gate(cfg, store):
    client = MockClient(cfg, store)
    # fresh store: terms not accepted
    with pytest.raises(TermsNotAccepted):
        client.submit_evidence("0x" + "a" * 40, "note 2026", ["https://a.org"], [], "key-1")


def test_submit_and_judge(client, store):
    m = store.markets()[0]
    res = client.submit_evidence(m.address, "On 2026-11-20 turnout was 46.9% per the table.",
                                 ["https://bk.admin.ch/table"], [], "key-abc")
    assert res.status_code == 202
    sid = res.data["id"]
    result = client.get_submission(sid)
    assert result.data["status"] in {"judged", "queued", "judging"}
    # mock judges instantly on read
    assert result.data["status"] == "judged"
    assert result.data["markets"][0]["verdict"] in {
        "accepted", "duplicate", "unverifiable", "irrelevant", "no_information"}


def test_idempotent_replay(client, store):
    m = store.markets()[0]
    note = "On 2026-11-20 the office reported 4.2% for October."
    r1 = client.submit_evidence(m.address, note, ["https://a.org"], [], "same-key")
    r2 = client.submit_evidence(m.address, note, ["https://a.org"], [], "same-key")
    assert r1.data["id"] == r2.data["id"]  # same slot, not two


def test_three_items_accepted(client, store):
    m = store.markets()[0]
    # note + 2 links = exactly 3 items: allowed
    res = client.submit_evidence(m.address, "note 2026-11-20 4.2%",
                                 ["https://a.org", "https://b.org"], [], "key-3items")
    assert res.status_code == 202


def test_four_items_rejected(client, store, tmp_path):
    m = store.markets()[0]
    f = tmp_path / "a.png"
    f.write_bytes(b"png")
    with pytest.raises(DelphiAPIError) as ei:
        client.submit_evidence(m.address, "note 2026", ["https://a.org", "https://b.org"],
                               [str(f)], "key-4")
    assert ei.value.code == "invalid_request"


def test_entry_state_cap(client, store):
    # pick a market with no demo submissions so we control the slot count
    m = next(mm for mm in store.markets() if store.slots_used(mm.address) == 0)
    for i in range(3):
        client.submit_evidence(m.address, f"fact {i} on 2026-11-2{i} was 4.{i}%.",
                               ["https://a.org"], [], f"cap-key-{i}")
    state = client.get_entry_state(m.address).data
    assert state["remainingOnMarket"] == 0
    assert state["canSubmit"] is False
    assert state["reason"] == "cap_reached"


def test_unknown_market_404(client, store):
    with pytest.raises(DelphiAPIError) as ei:
        client.get_entry_state("0x" + "f" * 40)
    assert ei.value.code == "market_not_found"


def test_limits_shape(client, store):
    data = client.get_limits().data
    assert "submitsLeftThisHour" in data
    assert "hourEndsAt" in data
    assert "dailyCapLeft" in data
