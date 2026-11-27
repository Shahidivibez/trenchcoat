"""Pre-submission guardrails. Every path to the API goes through `check_candidate`.

Enforced (official rules, docs v1.0):
  1. Stance ledger — never submit YES and NO evidence on the same market (they cancel).
  2. Slot budget — max 3 submissions per market; every id counts whatever the verdict,
     duplicates give the slot back. Local ledger + live entry-state both checked.
  3. Item budget — note + each link + each file = 1 item each, max 3 total.
  4. Note ≤ 8000 chars; files ≤ 10 MB total; PDFs ≤ 20 pages; allowed types only.
  5. Evidence cutoff — never submit after the market's cutoff (minus safety margin).
  6. Idempotency — stable key per candidate+content; replay-safe retries.
  7. Novelty gate — simhash against our own prior submissions on the market.
  8. Acceptance guard — autopilot pauses when recent accept rate < 50% (Restricted tier risk).
  9. Manipulation guard — any manipulation verdict in 24h stops autopilot.
 10. Terms gate — submissions require accepted terms; acceptance needs explicit human yes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .models import Candidate, Market
from .store import Store

ALLOWED_FILE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".txt", ".doc", ".docx", ".csv"}

# Patterns that try to steer the judge — strikes risk account blocking.
_MANIPULATION_PATTERNS = [
    r"\bdear judge\b", r"\bto the judge\b", r"\bjudge,?\s+(please|you should|must)\b",
    r"\byou (should|must|need to) (conclude|rule|find|accept)\b",
    r"\bignore (previous|prior|all) instructions\b", r"\bsettle (this|the market) (as|to)\b",
    r"\bupdate your (probability|forecast|odds) to\b", r"\bthe correct verdict is\b",
]


@dataclass
class GuardResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "violations": self.violations, "warnings": self.warnings}


def simhash64(text: str) -> str:
    """Classic 64-bit simhash over word 3-grams; hex string."""
    tokens = re.findall(r"\w+", text.lower())
    if len(tokens) < 3:
        grams = [" ".join(tokens)] if tokens else [""]
    else:
        grams = [" ".join(tokens[i : i + 3]) for i in range(len(tokens) - 2)]
    vec = [0] * 64
    for g in grams:
        h = int(hashlib.md5(g.encode()).hexdigest(), 16) & ((1 << 64) - 1)
        for i in range(64):
            vec[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if vec[i] > 0:
            out |= 1 << i
    return f"{out:016x}"


def hamming_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    x = int(a, 16) ^ int(b, 16)
    return 1.0 - (bin(x).count("1") / 64.0)


def check_files(cfg: Config, files: list[str]) -> tuple[list[str], list[str]]:
    violations, warnings = [], []
    total = 0
    for p in files:
        path = Path(p)
        if not path.exists():
            violations.append(f"Attachment missing on disk: {path.name}")
            continue
        if path.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
            violations.append(f"Attachment type not accepted: {path.suffix} ({path.name})")
        size = path.stat().st_size
        total += size
        if path.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader

                pages = len(PdfReader(str(path)).pages)
                if pages > cfg.max_pdf_pages:
                    violations.append(f"PDF {path.name} has {pages} pages (max {cfg.max_pdf_pages})")
            except Exception as e:  # unreadable PDF is a verifiability problem
                warnings.append(f"Could not read PDF {path.name}: {e}")
    if total > cfg.max_file_bytes:
        violations.append(f"Attachments total {total / 1e6:.1f} MB (max 10 MB)")
    return violations, warnings


def check_candidate(cfg: Config, store: Store, cand: Candidate, market: Market | None,
                    *, for_autopilot: bool = False) -> GuardResult:
    v: list[str] = []
    w: list[str] = []

    # 1. stance ledger
    locked = store.locked_stance(cand.market_address)
    if cand.stance not in {"YES", "NO", "NEUTRAL"}:
        v.append(f"Invalid stance {cand.stance!r}")
    if locked and locked != "NEUTRAL" and cand.stance in {"YES", "NO"} and cand.stance != locked:
        v.append(
            f"Stance conflict: market already locked to {locked}; this draft argues {cand.stance}. "
            "Opposing evidence cancels out in the ranking — discard or rewrite."
        )
    if cand.stance == "NEUTRAL":
        w.append("Neutral stance: fine for pure facts, but a clear direction earns settlement rewards.")

    # 2. slot budget (local ledger; live entry-state is checked again at submit time)
    used = store.slots_used(cand.market_address)
    if used >= cfg.max_submissions_per_market:
        v.append(f"Slot budget exhausted: {used}/{cfg.max_submissions_per_market} used on this market")

    # 3. item budget — the official 3-item rule
    if cand.item_count > cfg.max_items_per_submission:
        v.append(
            f"Item budget exceeded: {cand.item_count} items (note + {len(cand.links)} links + "
            f"{len(cand.files)} files); max {cfg.max_items_per_submission}. Drop an item."
        )
    if not cand.note.strip() and not cand.links and not cand.files:
        v.append("Empty submission: needs a note, links or files")

    # 4. note + files
    if len(cand.note) > cfg.max_note_chars:
        v.append(f"Note is {len(cand.note)} chars (max {cfg.max_note_chars})")
    fv, fw = check_files(cfg, cand.files)
    v += fv
    w += fw
    for link in cand.links:
        if not re.match(r"^https?://\S+$", link):
            v.append(f"Invalid link: {link!r}")

    # 5. cutoff
    if market is not None:
        h = market.cutoff_hours
        if h is not None and h <= 0:
            v.append("Evidence cutoff has passed for this market")
        elif h is not None and h < 0.5:
            w.append(f"Cutoff in {h * 60:.0f} minutes — submit immediately or skip")
        if market.status != "open":
            v.append(f"Market status is {market.status!r}, not open")

    # 6. idempotency — a prior submission with the same key means already sent
    prior = store.submission_by_key(cand.idempotency_key())
    if prior is not None:
        v.append(f"Already submitted with this exact content (submission {prior.id})")

    # 7. novelty vs our own prior work on this market
    if cand.simhash:
        for s in store.submissions(cand.market_address):
            if s.candidate_id == cand.id or s.mode == "dry-run":
                continue
            prior_c = store.candidate(s.candidate_id) if s.candidate_id else None
            if prior_c and prior_c.simhash:
                sim = hamming_similarity(cand.simhash, prior_c.simhash)
                if sim >= cfg.novelty_simhash_threshold:
                    v.append(
                        f"Self-duplicate: {sim:.0%} similar to our submission {s.id} on this market"
                    )
    if cand.source_url:
        for s in store.submissions(cand.market_address):
            if s.candidate_id != cand.id and cand.source_url in s.links:
                w.append(f"Source URL already used in our submission {s.id} — make sure the fact is new")

    # 8/9. autopilot behavioural guards
    if for_autopilot:
        stats = store.stats()
        if stats["manipulation_strikes_24h"] > 0:
            v.append("Manipulation verdict in the last 24h — autopilot halted (3 strikes block the account)")
        rate = stats["recent_accept_rate"]
        if rate is not None and rate < 0.5 and stats["judged_total"] >= 6:
            v.append(f"Acceptance guard: only {rate:.0%} of last 6 judged were accepted — autopilot paused")
        if cand.score < cfg.min_score:
            v.append(f"Score {cand.score:.2f} below autopilot floor {cfg.min_score:.2f}")

    # 10. content safety — never address or steer the judge
    lowered = cand.note.lower()
    for pat in _MANIPULATION_PATTERNS:
        if re.search(pat, lowered):
            v.append(f"Note matches judge-steering pattern ({pat!r}) — rewrite as plain facts")
            break

    # quality warnings (co-pilot sees these, autopilot treats score floor as hard)
    if cand.note and not re.search(r"\b20\d\d\b", cand.note):
        w.append("Note has no year/date — dated facts earn; undated ones usually don't")
    if cand.note and not cand.links and not cand.files:
        w.append("No source link or attachment — the judge must be able to verify the fact")

    return GuardResult(ok=not v, violations=v, warnings=w)


def acceptance_guard_tripped(store: Store) -> str | None:
    stats = store.stats()
    if stats["manipulation_strikes_24h"] > 0:
        return "manipulation verdict in last 24h"
    rate = stats["recent_accept_rate"]
    if rate is not None and rate < 0.5 and stats["judged_total"] >= 6:
        return f"accept rate {rate:.0%} over last 6 judged"
    return None
