import pytest

from trenchcoat.config import Config
from trenchcoat.store import Store


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENCHCOAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DELPHI_MODE", "mock")
    monkeypatch.delenv("DELPHI_API_ACCESS_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_MODELS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    c = Config()
    return c


@pytest.fixture()
def store(cfg):
    return Store(cfg.db_path)


@pytest.fixture()
def seeded(cfg, store):
    from trenchcoat.demo import seed_demo

    seed_demo(cfg, store)
    return store
