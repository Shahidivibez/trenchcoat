"""The scout: market selection → query planning → search → extraction →
evaluation → novelty gate → note drafting → candidate queue (Co-Pilot) or
guarded auto-submission (Autopilot)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .config import Config, resolve_autopilot
from .delphi import DelphiAPIError, get_client
from .guardrails import check_candidate, simhash64
from .llm import LLMError, LLMRouter
from .models import Candidate, Market
from .prompts import EVALUATE, NOTE, QUERIES, SYSTEM
from .research import search_all
from .research.extract import fetch_and_extract
from .research.screenshots import capture, capture_text
from .store import Store

# domains the Judge's basic fetch usually fails on → screenshot + paste key text
HARD_FETCH_DOMAINS = ("x.com", "twitter.com", "reddit.com", "instagram.com",
                      "facebook.com", "threads.net", "tiktok.com", "linkedin.com")


@dataclass
class ScoutReport:
    markets_seen: int = 0
    markets_researched: int = 0
    queries_run: int = 0
    results_seen: int = 0
    candidates_created: int = 0
    candidates_blocked: int = 0
    auto_submitted: int = 0
    errors: list[str] = field(default_factory=list)
    provider: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def rank_markets(cfg: Config, store: Store, markets: list[Market]) -> list[Market]:
    """Prefer: open, odds 20-80%, cutoff headroom, slots left, judgement pot,
    local-language/official-statistics categories (forecaster blind spots)."""
    scored = []
    for m in markets:
        if m.status != "open" or m.settled:
            continue
        h = m.cutoff_hours
        if h is None or h < cfg.min_cutoff_hours:
            continue
        if not (0.2 <= m.odds_yes <= 0.8):
            continue
        if store.slots_used(m.address) >= cfg.max_submissions_per_market:
            continue
        uncertainty = 1.0 - abs(m.odds_yes - 0.5) * 2  # peaks at 50/50
        urgency = max(0.2, min(1.0, 48.0 / max(h, 1.0)))  # sooner cutoff = act now
        pot = min(1.0, m.judgement_pot_usdc / 20.0)
        blind_spot = 1.2 if m.category in {"local_language", "official_statistics"} else 1.0
        slots_left = cfg.max_submissions_per_market - store.slots_used(m.address)
        score = uncertainty * urgency * pot * blind_spot * (1 + 0.15 * slots_left)
        scored.append((score, m))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [m for _, m in scored]


def plan_queries(llm: LLMRouter, m: Market) -> list[dict]:
    prompt = QUERIES.format(question=m.question, criteria=m.resolution_criteria[:2000],
                            sources=", ".join(m.data_sources), category=m.category)
    try:
        data, provider = llm.json_task(prompt, SYSTEM)
        queries = data.get("queries", []) if isinstance(data, dict) else []
        out = [{"q": str(q.get("q", "")).strip(), "lang": str(q.get("lang", "en"))[:2]}
               for q in queries if isinstance(q, dict) and q.get("q")]
        if out:
            return out[:6]
    except LLMError:
        pass
    # deterministic fallback
    words = re.findall(r"[A-Za-zÀ-ÿ0-9']+", m.question)
    stop = {"will", "the", "be", "in", "of", "at", "on", "a", "an", "per", "its", "and", "or", "for", "to"}
    base = " ".join(w for w in words if w.lower() not in stop)[:120]
    return [{"q": base, "lang": "en"}, {"q": f"{base} official statement", "lang": "en"}]


def _hard_to_fetch(url: str) -> bool:
    return any(d in url.lower() for d in HARD_FETCH_DOMAINS)


def evaluate_source(llm: LLMRouter, m: Market, url: str, title: str,
                    text: str) -> dict | None:
    prompt = EVALUATE.format(question=m.question, criteria=m.resolution_criteria[:2000],
                             sources=", ".join(m.data_sources), url=url, title=title,
                             text=text[:6000])
    try:
        data, _ = llm.json_task(prompt, SYSTEM)
        if isinstance(data, dict) and "claim" in data:
            return data
    except LLMError:
        pass
    return None


def compose_note(llm: LLMRouter, m: Market, ev: dict, url: str, title: str,
                 hard_fetch: bool) -> str:
    retrieved = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    prompt = NOTE.format(question=m.question, criteria=m.resolution_criteria[:1500],
                         claim=ev.get("claim", ""), stance=ev.get("stance", "NEUTRAL"),
                         source_title=title, url=url,
                         published_at=ev.get("published_at") or "unknown",
                         key_quote=ev.get("key_quote", "")[:400],
                         why=ev.get("why_it_matters", ""), retrieved=retrieved)
    try:
        data, provider = llm.json_task(prompt, SYSTEM, max_tokens=1024)
        note = data.get("note", "") if isinstance(data, dict) else ""
        if note and provider != "heuristic":
            return note[:7500]
    except LLMError:
        pass
    # deterministic composition (official note order: fact → source → why → working)
    parts = [ev.get("claim", "").strip()]
    pub = ev.get("published_at")
    parts.append(f"Source: {title or url}" + (f", published {pub}." if pub else ".") +
                 f" Retrieved {retrieved}.")
    if hard_fetch and ev.get("key_quote"):
        parts.append(f'Key text (verbatim): "{ev["key_quote"]}"')
    if ev.get("why_it_matters"):
        parts.append(ev["why_it_matters"])
    return "\n\n".join(p for p in parts if p)[:7500]


def scout_run(cfg: Config, store: Store, max_markets: int | None = None,
              autopilot: bool | None = None, market_filter: str | None = None) -> ScoutReport:
    from .submit import poll_receipts, submit_candidate

    report = ScoutReport()
    client = get_client(cfg, store)
    llm = LLMRouter(cfg, store)
    autopilot = resolve_autopilot(cfg, store, autopilot)
    max_markets = max_markets or cfg.max_markets_per_run
    store.log("scout-mode", f"{'AUTOPILOT' if autopilot else 'CO-PILOT'} · delphi={cfg.delphi_mode}")

    # 1. markets
    try:
        markets = client.fetch_markets()
    except Exception as e:
        report.errors.append(f"market feed failed: {e}")
        store.log("scout-error", report.errors[-1])
        return report
    for m in markets:
        store.upsert_market(m)
    report.markets_seen = len(markets)
    store.log("scout", f"Fetched {len(markets)} markets ({client.mode} mode)")

    ranked = rank_markets(cfg, store, markets)
    if market_filter:
        ranked = [m for m in ranked if market_filter.lower() in m.address.lower()
                  or market_filter.lower() in m.question.lower()]
    ranked = ranked[:max_markets]

    submitted_this_run = 0
    for m in ranked:
        # entry-state before research (official guidance: don't research a dead market)
        try:
            entry = client.get_entry_state(m.address).data
            if not entry.get("canSubmit", True):
                store.log("scout-skip", f"{m.question[:60]}… entry-state: {entry.get('reason')}",
                          market=m.address)
                continue
        except DelphiAPIError as e:
            report.errors.append(f"entry-state {m.address[:10]}: {e.code}")
            if e.code in {"terms_not_accepted", "unauthorized", "not_found"}:
                break  # account-level problem; stop the whole run
            continue

        report.markets_researched += 1
        queries = plan_queries(llm, m)
        report.provider = llm.last_provider
        seen_urls: set[str] = set()
        market_candidates = 0

        for q in queries:
            report.queries_run += 1
            results = search_all(cfg, store, q["q"], q["lang"], limit=6)
            report.results_seen += len(results)
            for r in results:
                if r.url in seen_urls or market_candidates >= 3:
                    continue
                seen_urls.add(r.url)
                cand = _build_candidate(cfg, store, llm, m, r.url, r.title, q["q"], r.language)
                if cand is None:
                    continue
                report.candidates_created += 1
                market_candidates += 1
                guard = check_candidate(cfg, store, cand, m, for_autopilot=autopilot)
                if not guard.ok:
                    cand.status = "blocked"
                    cand.block_reason = "; ".join(guard.violations)
                    report.candidates_blocked += 1
                    store.save_candidate(cand)
                    store.log("blocked", f"{cand.claim[:70]}… — {cand.block_reason}",
                              market=m.address, ref=cand.id)
                    continue
                store.save_candidate(cand)
                store.log("candidate", f"[{cand.stance} {cand.score:.2f}] {cand.claim[:80]}",
                          market=m.address, ref=cand.id)
                if autopilot and submitted_this_run < cfg.max_submissions_per_run:
                    res = submit_candidate(cfg, store, cand.id, autopilot=True)
                    if res.get("ok"):
                        report.auto_submitted += 1
                        submitted_this_run += 1
            if market_candidates >= 3:
                break
            time.sleep(0.3)

    # refresh receipts for anything still pending
    try:
        poll_receipts(cfg, store)
    except Exception as e:
        report.errors.append(f"poll: {e}")

    store.log("scout-done",
              f"{report.markets_researched} markets, {report.candidates_created} candidates, "
              f"{report.auto_submitted} auto-submitted")
    return report


def _build_candidate(cfg: Config, store: Store, llm: LLMRouter, m: Market,
                     url: str, title: str, query: str, lang: str) -> Candidate | None:
    ext = fetch_and_extract(url)
    text = ext.text
    hard = _hard_to_fetch(url) or ext.fetch_blocked or ext.needs_browser

    # JS/blocked pages: render with Playwright for text + screenshot attachment
    screenshot_path = None
    if hard and cfg.playwright_enabled:
        rendered = capture_text(url, cfg, store)
        if rendered and len(rendered) > len(text or ""):
            text = rendered
        dest = cfg.artifacts_dir / f"{m.address[:10]}_{abs(hash(url)) % 10**8}.png"
        shot = capture(url, dest, cfg, store)
        if shot:
            screenshot_path = str(shot)

    if not text or len(text) < 200:
        return None

    ev = evaluate_source(llm, m, url, ext.title or title, text)
    if ev is None:
        return None
    relevance = float(ev.get("relevance", 0))
    delta = float(ev.get("forecaster_delta", 0))
    if relevance < 0.3 or delta < 0.2:
        return None  # not worth a slot

    novelty = float(ev.get("novelty", 0.5))
    verif = float(ev.get("verifiability", 0.5))
    if hard and screenshot_path:
        verif = max(verif, 0.75)  # screenshot restores verifiability
    score = round(0.30 * relevance + 0.30 * delta + 0.20 * novelty + 0.20 * verif, 3)

    note = compose_note(llm, m, ev, url, ext.title or title, hard)
    # item budget: note(1) + primary link(1) + optional screenshot(1) = 3
    files = [screenshot_path] if screenshot_path else []
    cand = Candidate(
        market_address=m.address,
        claim=str(ev.get("claim", ""))[:300],
        stance=str(ev.get("stance", "NEUTRAL")).upper(),
        note=note,
        links=[url],
        files=files,
        source_url=url,
        source_title=ext.title or title,
        source_published_at=ev.get("published_at") or ext.published_at,
        language=lang,
        query=query,
        relevance=relevance, novelty=novelty, verifiability=verif,
        forecaster_delta=delta, score=score,
        score_rationale=str(ev.get("rationale", ""))[:500],
        simhash=simhash64(str(ev.get("claim", "")) + " " + note[:2000]),
    )
    return cand
