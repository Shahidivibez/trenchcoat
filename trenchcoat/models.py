"""Core data models. Shapes mirror the official Evidence API contract (docs v1.0)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

TERMINAL_STATUSES = {"judged", "rejected", "failed", "unmatched"}
VERDICTS = {"accepted", "duplicate", "unverifiable", "irrelevant", "no_information", "manipulation"}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_until(ts: str | None) -> float | None:
    dt = parse_iso(ts)
    if dt is None:
        return None
    return (dt - datetime.now(UTC)).total_seconds() / 3600.0


def usdc(base_units: str | int | None) -> float:
    """USDC.e has 6 decimals; API amounts are base-unit strings."""
    if base_units in (None, ""):
        return 0.0
    try:
        return int(base_units) / 1_000_000
    except (ValueError, TypeError):
        return 0.0


@dataclass
class Market:
    address: str
    question: str
    delphi_url: str = ""
    category: str = ""
    category_label: str = ""
    market_tier: str = ""
    wave: int = 1
    status: str = "open"
    odds_yes: float = 0.5
    odds_no: float = 0.5
    closes_at: str = ""
    evidence_cutoff_at: str = ""
    judgement_pot: str = "0"  # USDC.e base units
    settlement_pot: str = "0"
    resolution_criteria: str = ""
    data_sources: list[str] = field(default_factory=list)
    settled: Any = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_feed(cls, d: dict) -> Market:
        odds = d.get("odds") or {}
        pots = d.get("pots") or {}
        return cls(
            address=d["address"],
            question=d.get("question", ""),
            delphi_url=d.get("delphiUrl", ""),
            category=d.get("category", ""),
            category_label=d.get("categoryLabel", ""),
            market_tier=d.get("marketTier", ""),
            wave=d.get("wave", 1),
            status=d.get("status", "open"),
            odds_yes=float(odds.get("yes", 0.5)),
            odds_no=float(odds.get("no", 0.5)),
            closes_at=d.get("closesAt", ""),
            evidence_cutoff_at=d.get("evidenceCutoffAt", ""),
            judgement_pot=str(pots.get("judgement", "0")),
            settlement_pot=str(pots.get("settlement", "0")),
            resolution_criteria=d.get("resolutionCriteria", ""),
            data_sources=list(d.get("dataSources") or []),
            settled=d.get("settled"),
            raw=d,
        )

    @property
    def cutoff_hours(self) -> float | None:
        return hours_until(self.evidence_cutoff_at)

    @property
    def judgement_pot_usdc(self) -> float:
        return usdc(self.judgement_pot)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    """A researched evidence draft awaiting review (Co-Pilot) or autopilot submission."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    market_address: str = ""
    claim: str = ""  # one-sentence factual claim
    stance: str = "NEUTRAL"  # YES / NO / NEUTRAL — direction the fact supports
    note: str = ""  # the submission note (fact, source, why, working)
    links: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # local artifact paths
    source_url: str = ""
    source_title: str = ""
    source_published_at: str | None = None
    retrieved_at: str = field(default_factory=utcnow)
    language: str = "en"
    query: str = ""
    # scoring
    relevance: float = 0.0
    novelty: float = 0.0
    verifiability: float = 0.0
    forecaster_delta: float = 0.0
    score: float = 0.0
    score_rationale: str = ""
    simhash: str = ""
    # workflow
    status: str = "draft"  # draft | approved | submitted | discarded | blocked
    block_reason: str = ""
    submission_id: str | None = None
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    demo: bool = False

    @property
    def item_count(self) -> int:
        """Official rule: note + each link + each file count as 1 item; max 3."""
        return (1 if self.note.strip() else 0) + len(self.links) + len(self.files)

    def content_hash(self) -> str:
        payload = json.dumps(
            {"note": self.note, "links": sorted(self.links), "market": self.market_address},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def idempotency_key(self) -> str:
        """Stable per (candidate, content). Editing the note after a failed send
        produces a new key; retrying the identical body reuses the old one."""
        return f"tc-{self.id}-{self.content_hash()}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Submission:
    """Local receipt mirror of GET /v1/submissions/{id}."""

    id: str  # Delphi submission id (or local mock id)
    candidate_id: str = ""
    market_address: str = ""
    idempotency_key: str = ""
    stance: str = "NEUTRAL"
    claim: str = ""
    note: str = ""
    links: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    status: str = "queued"  # queued|judging|judged|rejected|failed|unmatched
    verdict: str | None = None
    reason: str | None = None
    code: str | None = None
    bounty_amount: str = "0"
    bounty_state: str = "none"  # held|paid|voided|none
    zero_pay_reason: str | None = None
    settlement_up_to: str | None = None
    can_retry: bool = False
    request_id: str | None = None
    mode: str = "mock"  # which client produced this: mock|dry-run|live
    created_at: str = field(default_factory=utcnow)
    judged_at: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def bounty_usdc(self) -> float:
        return usdc(self.bounty_amount)

    @property
    def settlement_up_to_usdc(self) -> float:
        return usdc(self.settlement_up_to)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def counts_for_qualifier(self) -> bool:
        """Any verdict except manipulation counts once judged; queued does not."""
        return self.status in {"judged", "rejected"} and self.verdict != "manipulation"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FeedbackItem:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    area: str = "api"  # api|keys|markets|judging|rewards|docs|site|other
    title: str = ""
    text: str = ""
    market_id: str | None = None
    submission_id: str | None = None
    status: str = "draft"  # draft|sent|failed
    remote_id: str | None = None
    created_at: str = field(default_factory=utcnow)
    sent_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Event:
    id: int | None = None
    ts: str = field(default_factory=utcnow)
    kind: str = "info"
    message: str = ""
    market_address: str | None = None
    ref_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
