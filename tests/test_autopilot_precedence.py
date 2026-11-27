"""Autopilot precedence: explicit flag > persisted board toggle > env default.

Regression guard: a scheduled scout must honour the board toggle instead of
silently falling back to the env var.
"""

from trenchcoat.config import resolve_autopilot


def test_env_default_off(cfg, store):
    cfg.autopilot = False
    assert resolve_autopilot(cfg, store) is False


def test_env_default_on(cfg, store):
    cfg.autopilot = True
    assert resolve_autopilot(cfg, store) is True


def test_persisted_toggle_overrides_env(cfg, store):
    cfg.autopilot = False
    store.kv_set("autopilot", "1")
    assert resolve_autopilot(cfg, store) is True
    store.kv_set("autopilot", "0")
    cfg.autopilot = True
    assert resolve_autopilot(cfg, store) is False


def test_explicit_flag_overrides_everything(cfg, store):
    cfg.autopilot = True
    store.kv_set("autopilot", "1")
    assert resolve_autopilot(cfg, store, False) is False
    store.kv_set("autopilot", "0")
    assert resolve_autopilot(cfg, store, True) is True


def test_scout_run_honours_persisted_toggle(cfg, store, monkeypatch):
    """Scheduled-style call (autopilot=None) must use the persisted toggle."""
    import trenchcoat.scout as scout_mod

    cfg.autopilot = False
    store.kv_set("autopilot", "1")

    captured = {}

    class FakeClient:
        mode = "mock"

        def fetch_markets(self):
            return []

    def fake_get_client(c, s):
        return FakeClient()

    monkeypatch.setattr(scout_mod, "get_client", fake_get_client)
    monkeypatch.setattr(scout_mod, "poll_receipts", lambda *a, **k: {}, raising=False)

    report = scout_mod.scout_run(cfg, store, autopilot=None)
    captured["modes"] = [e.message for e in store.events(20) if e.kind == "scout-mode"]
    assert report.markets_seen == 0
    assert any("AUTOPILOT" in m for m in captured["modes"]), captured
