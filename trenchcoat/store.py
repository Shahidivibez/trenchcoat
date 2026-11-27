"""SQLite persistence: markets, candidates, receipts, stance ledger, feedback, events.

The stance ledger and slot counters live here so guardrails hold across sessions,
processes and agents sharing the same data dir (GitHub Actions cache included).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import Candidate, Event, FeedbackItem, Market, Submission, utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    address TEXT PRIMARY KEY,
    json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    market_address TEXT NOT NULL,
    status TEXT NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_market ON candidates(market_address, status);
CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT,
    market_address TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_market ON submissions(market_address);
CREATE TABLE IF NOT EXISTS stance_ledger (
    market_address TEXT PRIMARY KEY,
    stance TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    locked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    market_address TEXT,
    ref_id TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── kv ───────────────────────────────────────────────────────────
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ── events ────────────────────────────────────────────────────────
    def log(self, kind: str, message: str, market: str | None = None, ref: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO events(ts,kind,message,market_address,ref_id) VALUES(?,?,?,?,?)",
                (utcnow(), kind, message, market, ref),
            )

    def events(self, limit: int = 100) -> list[Event]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            Event(id=r["id"], ts=r["ts"], kind=r["kind"], message=r["message"],
                  market_address=r["market_address"], ref_id=r["ref_id"])
            for r in rows
        ]

    # ── markets ──────────────────────────────────────────────────────
    def upsert_market(self, m: Market) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO markets(address,json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(address) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
                (m.address, json.dumps(m.to_dict()), utcnow()),
            )

    def markets(self) -> list[Market]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT json FROM markets ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            d = json.loads(r["json"])
            out.append(Market(**d))
        return out

    def market(self, address: str) -> Market | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT json FROM markets WHERE address=?", (address,)).fetchone()
        return Market(**json.loads(row["json"])) if row else None

    # ── candidates ────────────────────────────────────────────────────
    def save_candidate(self, cand: Candidate) -> None:
        cand.updated_at = utcnow()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO candidates(id,market_address,status,json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, json=excluded.json, updated_at=excluded.updated_at",
                (cand.id, cand.market_address, cand.status, json.dumps(cand.to_dict()),
                 cand.created_at, cand.updated_at),
            )

    def candidate(self, cid: str) -> Candidate | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT json FROM candidates WHERE id=?", (cid,)).fetchone()
        return Candidate(**json.loads(row["json"])) if row else None

    def candidates(self, market: str | None = None, status: str | None = None,
                   limit: int = 500) -> list[Candidate]:
        q, args = "SELECT json FROM candidates", []
        clauses = []
        if market:
            clauses.append("market_address=?")
            args.append(market)
        if status:
            clauses.append("status=?")
            args.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [Candidate(**json.loads(r["json"])) for r in rows]

    # ── submissions ───────────────────────────────────────────────────
    def save_submission(self, s: Submission) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO submissions(id,candidate_id,market_address,idempotency_key,status,json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, json=excluded.json, updated_at=excluded.updated_at",
                (s.id, s.candidate_id, s.market_address, s.idempotency_key or None, s.status,
                 json.dumps(s.to_dict()), s.created_at, utcnow()),
            )

    def submission(self, sid: str) -> Submission | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT json FROM submissions WHERE id=?", (sid,)).fetchone()
        return Submission(**json.loads(row["json"])) if row else None

    def submission_by_key(self, key: str) -> Submission | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT json FROM submissions WHERE idempotency_key=?", (key,)).fetchone()
        return Submission(**json.loads(row["json"])) if row else None

    def submissions(self, market: str | None = None, limit: int = 500) -> list[Submission]:
        q, args = "SELECT json FROM submissions", []
        if market:
            q += " WHERE market_address=?"
            args.append(market)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [Submission(**json.loads(r["json"])) for r in rows]

    def slots_used(self, market: str) -> int:
        """Every submission that got an id uses a slot, whatever the verdict —
        except a judged/rejected duplicate, which gives its slot back."""
        subs = self.submissions(market)
        used = 0
        for s in subs:
            if s.mode == "dry-run":
                continue  # outbox captures never touched the real account
            if s.verdict == "duplicate" or s.code == "duplicate":
                continue
            used += 1
        return used

    # ── stance ledger ─────────────────────────────────────────────────
    def locked_stance(self, market: str) -> str | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT stance FROM stance_ledger WHERE market_address=?", (market,)).fetchone()
        return row["stance"] if row else None

    def lock_stance(self, market: str, stance: str, submission_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO stance_ledger(market_address,stance,submission_id,locked_at) "
                "VALUES(?,?,?,?)",
                (market, stance, submission_id, utcnow()),
            )

    def stance_ledger(self) -> dict[str, dict]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM stance_ledger").fetchall()
        return {r["market_address"]: dict(r) for r in rows}

    # ── feedback ──────────────────────────────────────────────────────
    def save_feedback(self, f: FeedbackItem) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO feedback(id,status,json,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, json=excluded.json",
                (f.id, f.status, json.dumps(f.to_dict()), f.created_at),
            )

    def feedback_items(self, limit: int = 200) -> list[FeedbackItem]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT json FROM feedback ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [FeedbackItem(**json.loads(r["json"])) for r in rows]

    def feedback_item(self, fid: str) -> FeedbackItem | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT json FROM feedback WHERE id=?", (fid,)).fetchone()
        return FeedbackItem(**json.loads(row["json"])) if row else None

    # ── aggregate stats ───────────────────────────────────────────────
    def stats(self) -> dict:
        subs = self.submissions()
        real = [s for s in subs if s.mode != "dry-run"]
        judged = [s for s in real if s.counts_for_qualifier]
        markets_covered = {s.market_address for s in judged}
        accepted = [s for s in judged if s.verdict == "accepted"]
        earned = sum(s.bounty_usdc for s in real if s.bounty_state in {"held", "paid"})
        paid = sum(s.bounty_usdc for s in real if s.bounty_state == "paid")
        held = sum(s.bounty_usdc for s in real if s.bounty_state == "held")
        settlement_potential = sum(s.settlement_up_to_usdc for s in accepted)
        recent = judged[:6]
        recent_accept_rate = (
            sum(1 for s in recent if s.verdict == "accepted") / len(recent) if recent else None
        )
        return {
            "submissions_total": len(real),
            "judged_total": len(judged),
            "markets_covered": len(markets_covered),
            "accepted_total": len(accepted),
            "earned_usdc": round(earned, 6),
            "paid_usdc": round(paid, 6),
            "held_usdc": round(held, 6),
            "settlement_potential_usdc": round(settlement_potential, 6),
            "qualifier_submissions": min(len(judged), 10),
            "qualifier_markets": min(len(markets_covered), 3),
            "qualified": len(judged) >= 10 and len(markets_covered) >= 3,
            "recent_accept_rate": recent_accept_rate,
            "manipulation_strikes_24h": self._manipulation_strikes(real),
        }

    @staticmethod
    def _manipulation_strikes(subs: list[Submission]) -> int:
        from .models import hours_until

        n = 0
        for s in subs:
            if s.verdict == "manipulation":
                h = hours_until(s.judged_at or s.created_at)
                if h is not None and h > -24:
                    n += 1
        return n
