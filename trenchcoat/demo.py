"""Seed the Detective Board with a clearly-labelled DEMO case load.

Uses the real wave-1 market questions (public feed) with synthetic evidence
drafts and mock receipts, so the board is alive on first run without any keys
or network. Demo candidates are flagged and can never be submitted in live mode.
"""

from __future__ import annotations

from .config import Config
from .delphi import get_client
from .guardrails import simhash64
from .models import Candidate, FeedbackItem
from .store import Store

DEMO_CLAIMS = [
    # (market index, stance, claim, note, score, status)
    (0, "NO",
     "Swiss Federal Chancellery's turnout series shows 4 of the last 5 federal votes below 50%.",
     "The Federal Chancellery's official turnout series (bk.admin.ch, retrieved 2026-11-27) records turnout of 46.9%, 45.2%, 48.1%, 44.7% and 51.3% for the last five federal votes. Four of five fell below the 50.0% threshold this market uses. The market resolves on the Chancellery's official figure for the Nov 29 vote; the base rate from the same primary series is 4/5 below threshold. Source: Federal Chancellery turnout table, retrieved 2026-11-27 02:00 UTC.",
     0.72, "draft"),
    (0, "YES",
     "SRF's cantonal pre-vote survey projects 52% participation for Nov 29.",
     "DEMO draft — synthetic survey claim for board illustration. A cantonal pre-vote survey projects 52% participation for the Nov 29 federal vote, above the 50.0% resolution threshold. Source: SRF survey desk, published 2026-11-26, retrieved 2026-11-27.",
     0.58, "draft"),
    (2, "NO",
     "Spanish CIS November barometer puts Vox at 14.8% vote share, implying ~45-52 seats under current d'Hondt maps.",
     "The CIS November barometer (published 2026-11-20, cis.es) gives Vox a 14.8% national vote share. Applying the 2023 provincial d'Hondt conversion to that share yields 45-52 seats, below the 60-seat threshold in the resolution criteria. Working: provincial seat allocation at 14.8% uniform swing from 2023 results; inputs and per-province table in the attached CSV. Source: CIS barometer 3423, retrieved 2026-11-27.",
     0.81, "draft"),
    (3, "YES",
     "Destatis October VPI final confirms 2.7% YoY; November energy base effects add ~0.15pp.",
     "Destatis's final October 2026 VPI release (published 2026-11-14, destatis.de) confirms 2.7% YoY. November 2025 energy prices fell 3.1% MoM (base effect); repeating the 2025 November MoM pattern on the October 2026 index yields 2.85% YoY for November 2026, above the 2.8% threshold. Working: (1 + 0.027) × (Nov25 index / Oct25 index) − 1 = 2.85%. Source: Destatis VPI table 61111, retrieved 2026-11-27.",
     0.84, "draft"),
    (4, "NO",
     "Swiss FSO's November LIK consumer sentiment index fell to -38, consistent with sub-1.6% CPI prints.",
     "DEMO draft — synthetic. The FSO/SECO consumer sentiment index (LIK) for November 2026 fell to -38 (published 2026-11-25). Historically, LIK readings below -35 have coincided with CPI YoY prints under 1.6% in 7 of 9 cases since 2015. Source: SECO sentiment table, retrieved 2026-11-27.",
     0.63, "draft"),
]


def seed_demo(cfg: Config, store: Store) -> dict:
    client = get_client(cfg, store)
    markets = client.fetch_markets()
    for m in markets:
        store.upsert_market(m)

    # accept mock terms so the demo flow works end-to-end (mock only!)
    if client.mode == "mock":
        terms = client.get_terms().data
        if not terms.get("acceptedAt"):
            client.accept_terms(terms["version"])

    created = 0
    submitted = 0
    for idx, stance, claim, note, score, status in DEMO_CLAIMS:
        if idx >= len(markets):
            continue
        m = markets[idx]
        cand = Candidate(
            market_address=m.address, claim=claim, stance=stance, note=note,
            links=[m.data_sources[0] if m.data_sources else "https://example.org"],
            source_url=m.data_sources[0] if m.data_sources else "https://example.org",
            source_title="DEMO primary source", source_published_at="2026-11-20",
            relevance=score, novelty=score - 0.05, verifiability=0.8,
            forecaster_delta=score - 0.1, score=score,
            score_rationale="DEMO seed — synthetic evidence for board illustration",
            simhash=simhash64(claim + note), demo=True, status=status,
        )
        store.save_candidate(cand)
        created += 1

    # submit two demo candidates (different markets) through the mock judge so
    # receipts exist — never in live mode.
    from .submit import poll_receipts, submit_candidate

    if cfg.delphi_mode != "live":
        drafts = [c for c in store.candidates() if c.demo and c.status == "draft"]
        used_markets: set[str] = set()
        for cand in drafts:
            if submitted >= 2:
                break
            if cand.market_address in used_markets:
                continue
            res = submit_candidate(cfg, store, cand.id)
            if res.get("ok"):
                submitted += 1
                used_markets.add(cand.market_address)
        poll_receipts(cfg, store)

    # a couple of seeded quirks for the feedback bounty tracker
    store.save_feedback(FeedbackItem(
        area="judging", title="Judge fetch fails on JS-rendered pages",
        text="The judge opens links with a basic HTTP fetch. JS-rendered pages (and X/Reddit) "
             "return no content, so sourced claims come back unverifiable unless the key text "
             "is pasted into the note and a screenshot is attached. Suggest documenting a "
             "recommended capture format. (DEMO quirk — replace with real observations.)",
    ))
    store.save_feedback(FeedbackItem(
        area="docs", title="Item-budget rule is easy to misread",
        text="The 3-item budget counts the note + each link + each file. Several community "
             "repos read it as '3 attachments'. A worked example in evidence-api.md would "
             "prevent 400 invalid_request on first submit. (DEMO quirk.)",
    ))
    store.log("demo", f"Seeded {created} demo candidates, {submitted} mock submissions")
    return {"candidates": created, "submitted": submitted, "markets": len(markets)}
