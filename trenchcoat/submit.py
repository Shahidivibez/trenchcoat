"""Submission service: the single guarded path to the Evidence API.

Co-Pilot approve, Autopilot and CLI all call `submit_candidate`. It re-checks
every guardrail at send time (state may have changed since the draft), reuses
the stable Idempotency-Key on retries, locks the stance ledger on first send,
and records the receipt. `poll_receipts` refreshes pending judgments respecting
Retry-After, and auto-logs API/judge quirks for the feedback bounty tracker.
"""

from __future__ import annotations

import time

from .config import Config
from .delphi import DelphiAPIError, TermsNotAccepted, get_client
from .guardrails import acceptance_guard_tripped, check_candidate
from .models import Submission, utcnow
from .store import Store


def submit_candidate(cfg: Config, store: Store, candidate_id: str,
                     *, autopilot: bool = False) -> dict:
    cand = store.candidate(candidate_id)
    if cand is None:
        return {"ok": False, "error": "candidate not found"}
    if cand.status == "submitted":
        return {"ok": False, "error": "already submitted"}
    if cand.status == "discarded":
        return {"ok": False, "error": "candidate was discarded"}
    if cand.demo and cfg.delphi_mode == "live":
        return {"ok": False, "error": "demo candidates can never be submitted in live mode"}

    # Idempotent replay: identical content already sent -> return the prior receipt.
    # This is the safe retry path after a timeout; it never spends a second slot.
    key = cand.idempotency_key()
    prior = store.submission_by_key(key)
    if prior is not None:
        cand.status = "submitted"
        cand.submission_id = prior.id
        store.save_candidate(cand)
        store.log("replay", f"Idempotent replay of {prior.id} (key reused, no new slot)",
                  market=cand.market_address, ref=prior.id)
        return {"ok": True, "submission_id": prior.id, "replayed": True}

    market = store.market(cand.market_address)
    guard = check_candidate(cfg, store, cand, market, for_autopilot=autopilot)
    if not guard.ok:
        cand.status = "blocked"
        cand.block_reason = "; ".join(guard.violations)
        store.save_candidate(cand)
        store.log("blocked", cand.block_reason, market=cand.market_address, ref=cand.id)
        return {"ok": False, "error": "guardrails", "violations": guard.violations,
                "warnings": guard.warnings}

    if autopilot:
        tripped = acceptance_guard_tripped(store)
        if tripped:
            return {"ok": False, "error": f"autopilot paused: {tripped}"}

    client = get_client(cfg, store)

    # live pre-flight: terms + entry-state + hourly limits
    if client.mode == "live":
        try:
            terms = client.get_terms().data
            if not terms.get("acceptedAt"):
                return {"ok": False, "error": "terms_not_accepted",
                        "terms": terms,
                        "hint": "Read the terms and accept them explicitly in the board or CLI."}
            entry = client.get_entry_state(cand.market_address).data
            if not entry.get("canSubmit"):
                return {"ok": False, "error": f"entry-state: {entry.get('reason')}"}
            limits = client.get_limits().data
            if limits.get("submitsLeftThisHour", 1) <= 0:
                return {"ok": False, "error": "hourly limit reached",
                        "retry_after": limits.get("hourEndsAt")}
        except TermsNotAccepted as e:
            return {"ok": False, "error": "terms_not_accepted", "terms": e.terms}
        except DelphiAPIError as e:
            _log_quirk(store, "api", f"{e.code} during pre-flight", str(e),
                       request_id=e.request_id, market=cand.market_address)
            return {"ok": False, "error": f"{e.code}: {e.message}"}

    try:
        res = client.submit_evidence(
            market=cand.market_address, note=cand.note, links=cand.links,
            file_paths=cand.files, idempotency_key=key,
        )
    except TermsNotAccepted as e:
        return {"ok": False, "error": "terms_not_accepted", "terms": e.terms}
    except DelphiAPIError as e:
        _log_quirk(store, "api", f"submit failed: {e.code}", e.message,
                   request_id=e.request_id, market=cand.market_address)
        if e.code == "timeout":
            # The request may have landed. Keep the key; reconcile on next poll.
            store.log("timeout", f"Submit timed out; key {key} retained for safe retry",
                      market=cand.market_address, ref=cand.id)
            return {"ok": False, "error": "timeout — retry will reuse the same Idempotency-Key",
                    "idempotency_key": key, "retryable": True}
        return {"ok": False, "error": f"{e.code}: {e.message}", "retryable": e.retryable}

    data = res.data or {}
    sid = data.get("id")
    if not sid:
        return {"ok": False, "error": "API returned no submission id"}

    sub = Submission(
        id=sid, candidate_id=cand.id, market_address=cand.market_address,
        idempotency_key=key, stance=cand.stance, claim=cand.claim,
        note=cand.note, links=list(cand.links), files=list(cand.files),
        status=data.get("status", "queued"), request_id=res.request_id,
        mode=client.mode, raw=data,
    )
    store.save_submission(sub)
    cand.status = "submitted"
    cand.submission_id = sid
    store.save_candidate(cand)
    if cand.stance in {"YES", "NO"}:
        store.lock_stance(cand.market_address, cand.stance, sid)
    store.log("submitted", f"[{cand.stance}] {cand.claim[:70]}… -> {sid} ({client.mode})",
              market=cand.market_address, ref=sid)
    return {"ok": True, "submission_id": sid, "status": sub.status,
            "warnings": guard.warnings, "mode": client.mode}


def poll_receipts(cfg: Config, store: Store, max_polls: int = 20) -> dict:
    """Refresh non-terminal submissions. Respects Retry-After; stops after a
    bounded number of reads (official guidance: don't loop past ~30 min)."""
    client = get_client(cfg, store)
    pending = [s for s in store.submissions() if not s.is_terminal]
    updated, still_pending = 0, 0
    for s in pending[:max_polls]:
        try:
            res = client.get_submission(s.id)
        except DelphiAPIError as e:
            _log_quirk(store, "api", f"poll failed: {e.code}", e.message,
                       request_id=e.request_id, submission_id=s.id)
            continue
        data = res.data or {}
        new = _merge_receipt(store, s, data)
        if new.status != s.status or new.verdict != s.verdict:
            updated += 1
            if new.is_terminal:
                _on_terminal(store, new)
        else:
            still_pending += 1
        if res.retry_after and not new.is_terminal:
            time.sleep(min(res.retry_after, 5))  # bounded in-process; scheduler re-runs later
    return {"updated": updated, "pending": still_pending}


def _merge_receipt(store: Store, s: Submission, data: dict) -> Submission:
    s.status = data.get("status", s.status)
    s.raw = data
    markets = data.get("markets") or []
    line = next((m for m in markets if m.get("marketAddress") == s.market_address), None)
    if line is None and markets:
        line = markets[0]
    if line:
        s.verdict = (line.get("verdict") or s.verdict or "").lower().replace(" ", "_") or None
        s.reason = line.get("reason") or s.reason
        bounty = line.get("bounty") or {}
        s.bounty_amount = str(bounty.get("amount", s.bounty_amount or "0"))
        s.bounty_state = bounty.get("state", s.bounty_state or "none")
        s.zero_pay_reason = line.get("zeroPayReason")
        s.settlement_up_to = line.get("settlementUpTo")
    s.code = data.get("code") or s.code
    s.can_retry = bool(data.get("canRetry"))
    if s.is_terminal and not s.judged_at:
        s.judged_at = utcnow()
    store.save_submission(s)
    return s


def _on_terminal(store: Store, s: Submission) -> None:
    store.log("verdict", f"{s.verdict or s.status}: {s.reason or ''} "
              f"(bounty {s.bounty_usdc:.2f} USDC, {s.bounty_state})",
              market=s.market_address, ref=s.id)
    if s.verdict == "manipulation":
        store.log("alert", "MANIPULATION verdict — autopilot halted; review the note before continuing",
                  market=s.market_address, ref=s.id)
    if s.verdict == "unverifiable":
        _log_quirk(store, "judging", "Unverifiable verdict on a sourced submission",
                   f"Submission {s.id} came back unverifiable. Note had links={len(s.links)}, "
                   f"files={len(s.files)}. Consider attaching the document/screenshot and resubmitting "
                   f"a NEW fact (a retry of the same fact earns nothing).",
                   market=s.market_address, submission_id=s.id)
    if s.status == "failed" and s.can_retry:
        store.log("retryable", f"Submission {s.id} failed but canRetry=true", ref=s.id)


def _log_quirk(store: Store, area: str, title: str, text: str,
               request_id: str | None = None, market: str | None = None,
               submission_id: str | None = None) -> None:
    """Auto-log API/judge quirks for the 5×$250 feedback bounties (dedup by title)."""
    from .models import FeedbackItem

    existing = {f.title for f in store.feedback_items()}
    if title in existing:
        return
    body = text
    if request_id:
        body += f"\nRequest id: {request_id}"
    f = FeedbackItem(area=area, title=title[:120], text=body[:4000],
                     market_id=market, submission_id=submission_id)
    store.save_feedback(f)
    store.log("quirk", f"Logged {area} quirk: {title}", ref=f.id)


def send_feedback(cfg: Config, store: Store, feedback_id: str) -> dict:
    f = store.feedback_item(feedback_id)
    if f is None:
        return {"ok": False, "error": "feedback not found"}
    if f.status == "sent":
        return {"ok": True, "remote_id": f.remote_id, "already": True}
    if not cfg.delphi_wallet and cfg.delphi_mode == "live":
        return {"ok": False, "error": "DELPHI_WALLET_ADDRESS required for feedback in live mode"}
    client = get_client(cfg, store)
    try:
        res = client.send_feedback(
            wallet=cfg.delphi_wallet or "0x" + "0" * 40,
            area=f.area, text=f.text, title=f.title,
            market_id=f.market_id, submission_id=f.submission_id,
        )
    except DelphiAPIError as e:
        f.status = "failed"
        store.save_feedback(f)
        return {"ok": False, "error": f"{e.code}: {e.message}"}
    f.status = "sent"
    f.remote_id = (res.data or {}).get("id")
    f.sent_at = utcnow()
    store.save_feedback(f)
    store.log("feedback-sent", f"{f.title} -> {f.remote_id}", ref=f.id)
    return {"ok": True, "remote_id": f.remote_id, "mode": client.mode}
