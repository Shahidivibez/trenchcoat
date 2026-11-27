"""Board API smoke tests via FastAPI TestClient."""

import pytest
from fastapi.testclient import TestClient

from trenchcoat.board.app import create_app


@pytest.fixture()
def client(cfg, seeded):
    app = create_app(cfg)
    return TestClient(app)


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "TRENCHCOAT" in r.text
    assert "Case files" in r.text


def test_market_page_renders(client, seeded):
    m = seeded.markets()[0]
    r = client.get(f"/market/{m.address}")
    assert r.status_code == 200
    assert "Will turnout in Switzerland" in r.text  # apostrophe is HTML-escaped


def test_receipts_page(client):
    assert client.get("/receipts").status_code == 200


def test_feedback_page(client):
    assert client.get("/feedback").status_code == 200


def test_settings_page(client):
    assert client.get("/settings").status_code == 200


def test_healthz(client):
    assert client.get("/healthz").json()["ok"] is True


def test_api_state(client):
    data = client.get("/api/state").json()
    assert "stats" in data
    assert data["mode"] == "mock"


def test_autopilot_toggle(client):
    r = client.post("/api/autopilot", json={"enabled": True})
    assert r.json()["autopilot"] is True
    assert client.get("/api/state").json()["autopilot"] is True
    client.post("/api/autopilot", json={"enabled": False})
    assert client.get("/api/state").json()["autopilot"] is False


def test_approve_draft_flow(client, seeded):
    draft = next(c for c in seeded.candidates(status="draft")
                 if not seeded.locked_stance(c.market_address))
    r = client.post(f"/api/candidates/{draft.id}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert seeded.candidate(draft.id).status == "submitted"


def test_discard_draft(client, seeded):
    draft = seeded.candidates(status="draft")[0]
    r = client.post(f"/api/candidates/{draft.id}/discard", json={"reason": "test"})
    assert r.json()["ok"] is True
    assert seeded.candidate(draft.id).status == "discarded"


def test_edit_draft(client, seeded):
    draft = seeded.candidates(status="draft")[0]
    r = client.post(f"/api/candidates/{draft.id}/edit",
                    json={"note": "Edited fact: 4.2% on 2026-11-20 per official table.",
                          "stance": "NO"})
    assert r.status_code == 200
    assert seeded.candidate(draft.id).note.startswith("Edited fact")


def test_feedback_add_and_send(client):
    r = client.post("/api/feedback", json={"area": "api", "title": "test quirk",
                                           "text": "something broke"})
    fid = r.json()["id"]
    r2 = client.post(f"/api/feedback/{fid}/send")
    assert r2.json()["ok"] is True


def test_terms_accept_requires_confirm(client):
    r = client.post("/api/terms/accept", json={"version": "2026-11-25"})
    assert r.status_code == 400


def test_token_gate(cfg, seeded, monkeypatch):
    cfg.board_token = "secret123"
    app = create_app(cfg)
    c = TestClient(app)
    assert c.get("/").status_code == 401
    assert c.get("/healthz").status_code == 200  # health exempt
    assert c.get("/", headers={"X-Board-Token": "secret123"}).status_code == 200
    assert c.get("/?token=secret123").status_code == 200


def test_export(client):
    data = client.get("/api/export").json()
    assert "submissions" in data
    assert "stance_ledger" in data
