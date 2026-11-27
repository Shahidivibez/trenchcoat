"""Delphi Evidence API adapter.

Contract source: gensyn-delphi-skills reference/evidence-api.md (docs v1.0) and
@gensyn-ai/gensyn-delphi-sdk 2.4.1 EvidenceClient. Base: https://delphi.fyi/v1

Three interchangeable clients:
  LiveClient   — real HTTP, all 8 routes + competition markets feed + feedback API
  MockClient   — in-process simulation with a deterministic heuristic judge
  DryRunClient — live reads when a key exists; writes captured to data/outbox/

Every submit carries a stable Idempotency-Key so a timeout retry can never burn
a second slot. Agent headers (X-Agent-Id/Harness/Model) and a real User-Agent
are sent on every call, as the official docs require.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import Config
from ..models import Market, Submission, utcnow
from ..store import Store

USER_AGENT = "Trenchcoat/0.1 (evidence-mining agent; +https://github.com/Shahidivibez/trenchcoat)"


class DelphiAPIError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False,
                 retry_after: int | None = None, field: str | None = None,
                 request_id: str | None = None, limit: str | None = None):
        super().__init__(f"[{status} {code}] {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after
        self.field = field
        self.request_id = request_id
        self.limit = limit


class TermsNotAccepted(DelphiAPIError):
    def __init__(self, terms: dict, **kw):
        super().__init__(403, "terms_not_accepted", "Account has not accepted the current terms.", **kw)
        self.terms = terms


@dataclass
class ApiResult:
    data: Any
    request_id: str | None = None
    warnings: str | None = None
    retry_after: int | None = None
    status_code: int = 200


# ─────────────────────────────────────────────────────────────────────────────
# Live client
# ─────────────────────────────────────────────────────────────────────────────
class LiveClient:
    mode = "live"

    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.base = cfg.delphi_base_url
        self._http = httpx.Client(timeout=60, headers=self._headers(), follow_redirects=True)

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.cfg.delphi_api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-Agent-Id": self.cfg.agent_id,
            "X-Agent-Harness": self.cfg.agent_harness,
            "X-Agent-Model": self.cfg.agent_model,
        }
        return h

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None, multipart: dict | None = None,
                 files: list[tuple] | None = None, extra_headers: dict | None = None) -> ApiResult:
        headers = dict(self._headers())
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = self._http.request(
                method, self.base + path, json=json_body, params=params,
                data=multipart, files=files, headers=headers,
            )
        except httpx.TimeoutException as e:
            # The request may have landed. Caller must retry with the SAME Idempotency-Key.
            raise DelphiAPIError(0, "timeout", f"Request timed out: {e}", retryable=True) from e
        except httpx.HTTPError as e:
            raise DelphiAPIError(0, "network", str(e), retryable=True) from e

        request_id = resp.headers.get("X-Request-Id")
        warnings = resp.headers.get("X-Delphi-Warnings")
        retry_after = resp.headers.get("Retry-After")
        retry_after_i = int(retry_after) if retry_after and retry_after.isdigit() else None
        if warnings:
            self.store.log("api-warning", f"{path}: {warnings}", ref=request_id)

        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}

        if resp.status_code >= 400:
            err = body.get("error") or {}
            code = err.get("code", "server_error")
            if resp.status_code == 403 and code == "terms_not_accepted":
                raise TermsNotAccepted(err.get("terms") or body.get("terms") or {},
                                       request_id=request_id)
            raise DelphiAPIError(
                resp.status_code, code,
                err.get("message", f"HTTP {resp.status_code}: {resp.text[:200]}"),
                retryable=err.get("retryable", resp.status_code >= 500),
                retry_after=err.get("retryAfterSeconds", retry_after_i),
                field=err.get("field"), request_id=body.get("requestId", request_id),
                limit=err.get("limit"),
            )
        return ApiResult(body, request_id=request_id, warnings=warnings,
                         retry_after=retry_after_i, status_code=resp.status_code)

    # ── the 8 routes ──────────────────────────────────────────────────
    def get_limits(self) -> ApiResult:
        return self._request("GET", "/limits")

    def get_terms(self) -> ApiResult:
        return self._request("GET", "/terms")

    def accept_terms(self, version: str) -> ApiResult:
        return self._request("POST", "/terms/acceptance", json_body={"termsVersion": version})

    def get_entry_state(self, market: str) -> ApiResult:
        return self._request("GET", f"/markets/{market}/entry-state")

    def list_submissions(self, market: str | None = None, agent_id: str | None = None,
                         cursor: str | None = None, limit: int | None = None) -> ApiResult:
        params = {k: v for k, v in
                  {"market": market, "agentId": agent_id, "cursor": cursor, "limit": limit}.items()
                  if v is not None}
        return self._request("GET", "/submissions", params=params)

    def get_submission(self, sid: str) -> ApiResult:
        return self._request("GET", f"/submissions/{sid}")

    def retry_submission(self, sid: str) -> ApiResult:
        return self._request("POST", f"/submissions/{sid}/retry")

    def submit_evidence(self, market: str, note: str, links: list[str],
                        file_paths: list[str], idempotency_key: str) -> ApiResult:
        # Multipart: note, links[] (one field per link), files, marketAddress.
        data: list[tuple[str, str]] = [("marketAddress", market), ("note", note)]
        for link in links:
            data.append(("links[]", link))
        files = []
        opened = []
        try:
            for p in file_paths:
                path = Path(p)
                fh = open(path, "rb")  # noqa: SIM115
                opened.append(fh)
                files.append(("files", (path.name, fh, _content_type(path))))
            return self._request(
                "POST", "/submissions", multipart=data, files=files,
                extra_headers={"Idempotency-Key": idempotency_key},
            )
        finally:
            for fh in opened:
                fh.close()

    # ── competition feed + feedback (outside /v1) ─────────────────────
    def fetch_markets(self) -> list[Market]:
        resp = httpx.get(self.cfg.markets_url, timeout=30,
                         headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        resp.raise_for_status()
        return [Market.from_feed(d) for d in resp.json()]

    def send_feedback(self, *, wallet: str, area: str, text: str, title: str | None = None,
                      market_id: str | None = None, submission_id: str | None = None) -> ApiResult:
        body: dict[str, Any] = {"wallet": wallet, "area": area, "text": text}
        if title:
            body["title"] = title[:120]
        if market_id:
            body["marketId"] = market_id
        if submission_id:
            body["submissionId"] = submission_id
        headers = {
            "x-api-key": self.cfg.delphi_api_key,  # feedback uses x-api-key, NOT Bearer
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Agent-Id": self.cfg.agent_id,
            "X-Agent-Harness": self.cfg.agent_harness,
            "X-Agent-Model": self.cfg.agent_model,
        }
        resp = httpx.post(self.cfg.feedback_url, json=body, headers=headers, timeout=30)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            err = data.get("error") or {}
            raise DelphiAPIError(resp.status_code, err.get("code", "error"),
                                 err.get("message", resp.text[:200]),
                                 retryable=resp.status_code >= 500,
                                 request_id=data.get("requestId"))
        return ApiResult(data, status_code=resp.status_code)


def _content_type(path: Path) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".pdf": "application/pdf", ".txt": "text/plain",
        ".csv": "text/csv", ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(path.suffix.lower(), "application/octet-stream")


# ─────────────────────────────────────────────────────────────────────────────
# Mock client — deterministic simulation of the same contract
# ─────────────────────────────────────────────────────────────────────────────
class MockClient:
    """Simulates the Evidence API in-process. The judge is a deterministic
    heuristic over note quality so demos and tests behave reproducibly."""

    mode = "mock"

    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self._judge_delay_s = 0  # instant judging in mock; receipts still flow through poll

    # state kept in the shared store so board + CLI see the same world
    def _state(self) -> dict:
        raw = self.store.kv_get("mock_state")
        return json.loads(raw) if raw else {"accepted_terms": None, "hour_used": 0, "hour": ""}

    def _save_state(self, s: dict) -> None:
        self.store.kv_set("mock_state", json.dumps(s))

    def get_limits(self) -> ApiResult:
        s = self._state()
        hour = utcnow()[:13]
        used = s["hour_used"] if s.get("hour") == hour else 0
        return ApiResult({
            "submitsLeftThisHour": max(0, 10 - used),
            "hourEndsAt": f"{hour}:59:59Z",
            "dailyCapLeft": "20000000",
            "dailyCapResetAt": None,
        })

    def get_terms(self) -> ApiResult:
        s = self._state()
        return ApiResult({
            "version": "2026-11-25",
            "url": "https://competition.delphi.fyi/terms",
            "acceptedAt": s.get("accepted_terms"),
        })

    def accept_terms(self, version: str) -> ApiResult:
        if version != "2026-11-25":
            raise DelphiAPIError(400, "invalid_request", "termsVersion is not current",
                                 field="termsVersion")
        s = self._state()
        s["accepted_terms"] = s.get("accepted_terms") or utcnow()
        self._save_state(s)
        return ApiResult({"version": version, "acceptedAt": s["accepted_terms"]})

    def get_entry_state(self, market: str) -> ApiResult:
        m = self.store.market(market)
        if m is None:
            raise DelphiAPIError(404, "market_not_found", "No such market", field="marketAddress")
        s = self._state()
        used = self.store.slots_used(market)
        remaining = max(0, self.cfg.max_submissions_per_market - used)
        reason = None
        can = True
        if not s.get("accepted_terms"):
            can, reason = False, "terms_not_accepted"
        elif m.status != "open":
            can, reason = False, "not_open"
        elif (m.cutoff_hours or 0) <= 0:
            can, reason = False, "closed"
        elif remaining == 0:
            can, reason = False, "cap_reached"
        return ApiResult({
            "canSubmit": can, "reason": reason, "remainingOnMarket": remaining,
            "judgementPotRemainingBaseUnits": m.judgement_pot,
            "rateLimit": {"resetAt": f"{utcnow()[:13]}:59:59Z"},
        })

    def list_submissions(self, market: str | None = None, agent_id: str | None = None,
                         cursor: str | None = None, limit: int | None = None) -> ApiResult:
        subs = [s.to_dict() for s in self.store.submissions(market)]
        return ApiResult({"items": subs[: limit or 100], "nextCursor": None})

    def get_submission(self, sid: str) -> ApiResult:
        s = self.store.submission(sid)
        if s is None:
            raise DelphiAPIError(404, "not_found", "No such submission")
        if not s.is_terminal:
            s = self._judge(s)
            self.store.save_submission(s)
        return ApiResult(s.raw or s.to_dict(), retry_after=None if s.is_terminal else 5)

    def retry_submission(self, sid: str) -> ApiResult:
        s = self.store.submission(sid)
        if s is None:
            raise DelphiAPIError(404, "not_found", "No such submission")
        if s.status != "failed" or not s.can_retry:
            raise DelphiAPIError(429, "rate_limited", "Not retryable yet", retry_after=60)
        s.status = "queued"
        self.store.save_submission(s)
        return self.get_submission(sid)

    def submit_evidence(self, market: str, note: str, links: list[str],
                        file_paths: list[str], idempotency_key: str) -> ApiResult:
        s = self._state()
        if not s.get("accepted_terms"):
            raise TermsNotAccepted({"version": "2026-11-25",
                                    "url": "https://competition.delphi.fyi/terms"})
        # idempotent replay
        prior = self.store.submission_by_key(idempotency_key)
        if prior is not None:
            return ApiResult(prior.raw or prior.to_dict(), status_code=202)

        items = (1 if note.strip() else 0) + len(links) + len(file_paths)
        if items > self.cfg.max_items_per_submission:
            raise DelphiAPIError(400, "invalid_request",
                                 "A submission holds at most 3 items", field="links[]")
        if len(note) > self.cfg.max_note_chars:
            raise DelphiAPIError(400, "invalid_request", "note too long", field="note")
        entry = self.get_entry_state(market).data
        if not entry["canSubmit"]:
            if entry["reason"] == "cap_reached":
                raise DelphiAPIError(429, "cap_reached", "All submissions used on this market")
            raise DelphiAPIError(400, "invalid_request",
                                 f"Market not submittable: {entry['reason']}", field="marketAddress")

        hour = utcnow()[:13]
        used = s["hour_used"] if s.get("hour") == hour else 0
        if used >= 10:
            raise DelphiAPIError(429, "rate_limited", "Hourly limit reached",
                                 retryable=True, retry_after=600, limit="hourly")
        s["hour"], s["hour_used"] = hour, used + 1
        self._save_state(s)

        sid = f"mock_{uuid.uuid4().hex[:16]}"
        sub = Submission(
            id=sid, market_address=market, idempotency_key=idempotency_key,
            note=note, links=list(links), files=[Path(p).name for p in file_paths],
            status="queued", mode="mock",
            raw={"id": sid, "createdAt": utcnow(), "status": "queued", "markets": []},
        )
        self.store.save_submission(sub)
        return ApiResult(sub.raw, status_code=202, retry_after=5)

    def _judge(self, sub: Submission) -> Submission:
        """Deterministic heuristic judge: scores the note like the real judge
        would read it — dated fact + source + relevance + working."""
        cand = self.store.candidate(sub.candidate_id) if sub.candidate_id else None
        note = sub.note.lower()
        h = int(hashlib.sha256(sub.id.encode()).hexdigest(), 16)
        rng = random.Random(h)

        has_date = any(tok in note for tok in ("2026", "2025", "published", "dated"))
        has_source = bool(sub.links) or bool(sub.files) or "http" in note
        has_number = any(ch.isdigit() for ch in note)
        quality = (cand.score if cand else 0.5)

        if not has_source:
            verdict, reason = "unverifiable", "No readable source was provided with the claim."
            bounty = 0
        elif not has_date or not has_number:
            verdict, reason = "no_information", "The note lacks a dated, quantified fact."
            bounty = 0
        elif quality < 0.35:
            verdict, reason = "irrelevant", "The fact does not bear on the resolution criteria."
            bounty = 0
        elif rng.random() < 0.12:
            verdict, reason = "duplicate", "This fact was already accepted on this market."
            bounty = 0
        else:
            verdict = "accepted"
            reason = "The evidence was new to the forecaster and moved the forecast."
            move = min(0.5, max(0.02, quality - 0.3)) * (0.7 + 0.6 * rng.random())
            m = self.store.market(sub.market_address)
            pot = int(m.judgement_pot) if m else 20_000_000
            bounty = min(int(pot * 0.5), int(move * 8_000_000))
            if bounty < 100_000:
                bounty = 0

        sub.status = "judged"
        sub.verdict = verdict
        sub.reason = reason
        sub.judged_at = utcnow()
        sub.bounty_amount = str(bounty)
        sub.bounty_state = "held" if bounty > 0 else "none"
        sub.zero_pay_reason = None if bounty > 0 else ("below_minimum" if verdict == "accepted" else None)
        sub.settlement_up_to = str(int(int(sub.bounty_amount or 0) * 1.5)) if verdict == "accepted" else None
        sub.raw = {
            "id": sub.id, "createdAt": sub.created_at, "status": "judged",
            "note": sub.note, "links": sub.links,
            "markets": [{
                "marketAddress": sub.market_address, "status": "accepted" if verdict == "accepted" else "not_accepted",
                "verdict": verdict, "reason": reason,
                "bounty": {"amount": sub.bounty_amount, "state": sub.bounty_state},
                "settlementUpTo": sub.settlement_up_to, "zeroPayReason": sub.zero_pay_reason,
            }],
        }
        return sub

    def fetch_markets(self) -> list[Market]:
        from .fixtures import FIXTURE_MARKETS

        return [Market.from_feed(d) for d in FIXTURE_MARKETS]

    def send_feedback(self, **kw) -> ApiResult:
        return ApiResult({"id": f"mock_fb_{uuid.uuid4().hex[:10]}", "createdAt": utcnow()},
                         status_code=201)


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run client — live reads, captured writes
# ─────────────────────────────────────────────────────────────────────────────
class DryRunClient:
    """Reads go to the real API when a key is configured (so you can verify
    access, terms and entry-state today). Writes never leave the machine:
    they are serialized to data/outbox/ and judged by the mock judge so the
    board's receipt flow is fully exercisable before going live."""

    mode = "dry-run"

    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self._mock = MockClient(cfg, store)
        self._live = LiveClient(cfg, store) if cfg.delphi_api_key else None

    def _read(self, name: str, *a, **kw) -> ApiResult:
        if self._live is not None:
            try:
                return getattr(self._live, name)(*a, **kw)
            except DelphiAPIError as e:
                self.store.log("dryrun-read-fallback",
                               f"{name}: live read failed ({e.code}); using mock")
        return getattr(self._mock, name)(*a, **kw)

    def get_limits(self):
        return self._read("get_limits")

    def get_terms(self):
        return self._read("get_terms")

    def get_entry_state(self, market: str):
        return self._read("get_entry_state", market)

    def list_submissions(self, market=None, agent_id=None, cursor=None, limit=None):
        return self._read("list_submissions", market, agent_id, cursor, limit)

    def accept_terms(self, version: str):
        # Never accept terms for the human, even in dry-run.
        raise DelphiAPIError(403, "terms_not_accepted",
                             "Dry-run refuses to accept terms. Use live mode with explicit human consent.")

    def get_submission(self, sid: str):
        sub = self.store.submission(sid)
        if sub is not None and sub.mode == "dry-run":
            return self._mock.get_submission(sid)
        return self._read("get_submission", sid)

    def retry_submission(self, sid: str):
        return self._mock.retry_submission(sid)

    def submit_evidence(self, market: str, note: str, links: list[str],
                        file_paths: list[str], idempotency_key: str) -> ApiResult:
        capture = {
            "captured_at": utcnow(), "market": market, "note": note, "links": links,
            "files": [str(p) for p in file_paths], "idempotency_key": idempotency_key,
            "agent": {"id": self.cfg.agent_id, "harness": self.cfg.agent_harness,
                      "model": self.cfg.agent_model},
        }
        out = self.cfg.outbox_dir / f"{idempotency_key}.json"
        out.write_text(json.dumps(capture, indent=2))
        self.store.log("outbox", f"Captured submission to {out.name} (not sent)", market=market)
        res = self._mock.submit_evidence(market, note, links, file_paths, idempotency_key)
        # re-tag the mock submission as dry-run so it never counts as real activity
        sub = self.store.submission(res.data["id"])
        if sub:
            sub.mode = "dry-run"
            self.store.save_submission(sub)
            res.data["mode"] = "dry-run"
        return res

    def fetch_markets(self) -> list[Market]:
        if self._live is not None:
            try:
                return self._live.fetch_markets()
            except (httpx.HTTPError, ValueError) as e:
                self.store.log("dryrun-read-fallback", f"markets feed failed: {e}; using fixtures")
        # Even without a key the public feed is worth trying once.
        try:
            resp = httpx.get(self.cfg.markets_url, timeout=20,
                             headers={"User-Agent": USER_AGENT}, follow_redirects=True)
            resp.raise_for_status()
            return [Market.from_feed(d) for d in resp.json()]
        except (httpx.HTTPError, ValueError):
            return self._mock.fetch_markets()

    def send_feedback(self, **kw) -> ApiResult:
        out = self.cfg.outbox_dir / f"feedback_{uuid.uuid4().hex[:8]}.json"
        out.write_text(json.dumps({"captured_at": utcnow(), **kw}, indent=2))
        self.store.log("outbox", f"Captured feedback to {out.name} (not sent)")
        return self._mock.send_feedback(**kw)


def get_client(cfg: Config, store: Store) -> LiveClient | MockClient | DryRunClient:
    if cfg.delphi_mode == "live":
        if not cfg.delphi_api_key:
            raise DelphiAPIError(0, "config", "DELPHI_MODE=live requires DELPHI_API_ACCESS_KEY")
        return LiveClient(cfg, store)
    if cfg.delphi_mode == "dry-run":
        return DryRunClient(cfg, store)
    return MockClient(cfg, store)
