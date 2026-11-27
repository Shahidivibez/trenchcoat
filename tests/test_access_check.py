"""Diagnosis table from competition.delphi.fyi/key-recovery.md (step 1)."""

from trenchcoat.access import Probe, diagnose, run_access_check


def test_404_with_wallet_means_no_evidence_access():
    a = Probe(404, {"error": {"code": "not_found"}}, "Not found.")
    b = Probe(200, {"wallet": "0x" + "a" * 40}, "")
    verdict, _, steps = diagnose(a, b)
    assert verdict == "no_evidence_access"
    assert any("DoraHacks" in s for s in steps)


def test_401_both_means_bad_key():
    a = Probe(401, {"error": {"code": "unauthorized"}}, "")
    b = Probe(401, {}, "")
    verdict, _, _ = diagnose(a, b)
    assert verdict == "bad_key"


def test_401_with_wallet_means_no_delphi_account():
    a = Probe(401, {"error": {"code": "unauthorized"}}, "")
    b = Probe(200, {"wallet": "0x" + "b" * 40}, "")
    verdict, _, steps = diagnose(a, b)
    assert verdict == "no_delphi_account"
    assert any("Do NOT create a new key" in s for s in steps)


def test_403_no_delphi_account():
    a = Probe(403, {"error": {"code": "no_delphi_account"}}, "")
    b = Probe(200, {"wallet": "0x" + "c" * 40}, "")
    assert diagnose(a, b)[0] == "no_delphi_account"


def test_200_means_ok():
    a = Probe(200, {"submitsLeftThisHour": 10}, "")
    b = Probe(200, {"wallet": "0x" + "d" * 40}, "")
    assert diagnose(a, b)[0] == "ok"


def test_terms_not_accepted():
    a = Probe(403, {"error": {"code": "terms_not_accepted"}}, "")
    b = Probe(200, {"wallet": "0x" + "e" * 40}, "")
    assert diagnose(a, b)[0] == "terms_needed"


def test_client_blocked_1010():
    a = Probe(403, {}, "error code: 1010")
    b = Probe(403, {}, "error code: 1010")
    assert diagnose(a, b)[0] == "client_blocked"


def test_service_down():
    assert diagnose(Probe(503, {}, ""), Probe(200, {}, ""))[0] == "service_down"


def test_no_key_short_circuits(cfg):
    cfg.delphi_api_key = ""
    assert "error" in run_access_check(cfg)


def test_wallet_short_form():
    a = Probe(404, {"error": {"code": "not_found"}}, "")
    b = Probe(200, {"wallet": "0x1234567890abcdef1234567890abcdef12345678"}, "")
    verdict, _, _ = diagnose(a, b)
    assert verdict == "no_evidence_access"
