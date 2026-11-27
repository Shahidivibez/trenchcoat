"""Environment-driven configuration. Keys are read from env/.env only, never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # reads .env from CWD if present; real env vars win


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v else default
    except ValueError:
        return default


def _float(v: str | None, default: float) -> float:
    try:
        return float(v) if v else default
    except ValueError:
        return default


@dataclass
class Config:
    # Delphi
    delphi_api_key: str = field(default_factory=lambda: os.getenv("DELPHI_API_ACCESS_KEY", ""))
    delphi_wallet: str = field(default_factory=lambda: os.getenv("DELPHI_WALLET_ADDRESS", ""))
    delphi_mode: str = field(default_factory=lambda: os.getenv("DELPHI_MODE", "mock").lower())
    delphi_base_url: str = field(
        default_factory=lambda: os.getenv("DELPHI_BASE_URL", "https://delphi.fyi/v1").rstrip("/")
    )
    markets_url: str = field(
        default_factory=lambda: os.getenv(
            "DELPHI_MARKETS_URL", "https://competition.delphi.fyi/markets.json"
        )
    )
    feedback_url: str = field(
        default_factory=lambda: os.getenv(
            "DELPHI_FEEDBACK_URL", "https://feedback.delphi.fyi/v1/feedback"
        )
    )
    agent_id: str = field(default_factory=lambda: os.getenv("DELPHI_AGENT_ID", "Trenchcoat"))
    agent_harness: str = field(default_factory=lambda: os.getenv("DELPHI_AGENT_HARNESS", "trenchcoat"))
    agent_model: str = field(default_factory=lambda: os.getenv("DELPHI_AGENT_MODEL", "gemini-2.5-flash"))

    # LLM providers
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))
    github_models_token: str = field(
        default_factory=lambda: os.getenv("GITHUB_MODELS_TOKEN", "") or os.getenv("GITHUB_TOKEN", "")
    )
    github_models_model: str = field(
        default_factory=lambda: os.getenv("GITHUB_MODELS_MODEL", "openai/gpt-4o-mini")
    )

    # Search boosters
    serper_api_key: str = field(default_factory=lambda: os.getenv("SERPER_API_KEY", ""))
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))

    # Behaviour
    autopilot: bool = field(default_factory=lambda: _bool(os.getenv("TRENCHCOAT_AUTOPILOT"), False))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("TRENCHCOAT_DATA_DIR", "./data")))
    max_markets_per_run: int = field(
        default_factory=lambda: _int(os.getenv("TRENCHCOAT_MAX_MARKETS_PER_RUN"), 4)
    )
    max_submissions_per_run: int = field(
        default_factory=lambda: _int(os.getenv("TRENCHCOAT_MAX_SUBMISSIONS_PER_RUN"), 2)
    )
    min_score: float = field(default_factory=lambda: _float(os.getenv("TRENCHCOAT_MIN_SCORE"), 0.55))
    novelty_simhash_threshold: float = field(
        default_factory=lambda: _float(os.getenv("TRENCHCOAT_NOVELTY_SIMHASH"), 0.86)
    )
    min_cutoff_hours: float = field(
        default_factory=lambda: _float(os.getenv("TRENCHCOAT_MIN_CUTOFF_HOURS"), 3.0)
    )
    playwright_enabled: bool = field(
        default_factory=lambda: _bool(os.getenv("PLAYWRIGHT_ENABLED"), True)
    )

    # Board
    board_host: str = field(default_factory=lambda: os.getenv("BOARD_HOST", "127.0.0.1"))
    board_port: int = field(default_factory=lambda: _int(os.getenv("BOARD_PORT") or os.getenv("PORT"), 8000))
    board_token: str = field(default_factory=lambda: os.getenv("BOARD_TOKEN", ""))

    # Official competition constants (docs v1.0, 2026-11-19)
    max_submissions_per_market: int = 3
    max_items_per_submission: int = 3  # note + each link + each file each count as 1
    max_note_chars: int = 8000
    max_file_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 20
    qualifier_submissions: int = 10
    qualifier_markets: int = 3
    competition_open: str = "2026-11-25T17:00:00Z"
    competition_close: str = "2026-12-11T17:00:00Z"

    def __post_init__(self) -> None:
        if self.delphi_mode not in {"mock", "dry-run", "live"}:
            raise ValueError(f"DELPHI_MODE must be mock|dry-run|live, got {self.delphi_mode!r}")
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "artifacts").mkdir(exist_ok=True)
        (self.data_dir / "outbox").mkdir(exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "trenchcoat.db"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def llm_providers_available(self) -> list[str]:
        out = []
        if self.gemini_api_key:
            out.append("gemini")
        if self.groq_api_key:
            out.append("groq")
        if self.github_models_token:
            out.append("github-models")
        return out

    def redacted_summary(self) -> dict:
        """Safe to render in the board / logs: presence booleans, never values."""
        return {
            "mode": self.delphi_mode,
            "agent_id": self.agent_id,
            "agent_harness": self.agent_harness,
            "agent_model": self.agent_model,
            "delphi_key_set": bool(self.delphi_api_key),
            "wallet_set": bool(self.delphi_wallet),
            "llm_providers": self.llm_providers_available(),
            "serper_set": bool(self.serper_api_key),
            "tavily_set": bool(self.tavily_api_key),
            "autopilot": self.autopilot,
            "playwright_enabled": self.playwright_enabled,
        }


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """Test hook."""
    global _config
    _config = None
