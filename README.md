# 🕵️ Trenchcoat

**Autonomous evidence-mining agent for the [Gensyn / Delphi Agent Arena Competition: Evidence Mining](https://dorahacks.io/hackathon/2479/).**

Trenchcoat finds dated, sourced, public facts that Delphi's forecaster doesn't already know, drafts submission notes, and sells them to the Delphi Evidence API for USDC.e bounties. **Co-Pilot by default** — every submission waits for your one-click approval on the Detective Board — with a guarded Autopilot toggle when you trust the pipeline.

> Competition window: **25 Nov 2026 17:00 UTC → 11 Dec 2026 17:00 UTC**. Prize pool $7,250 (1st $3,000 / 2nd $2,000 / 3rd $1,000 / 5 × $250 feedback bounties), on top of per-evidence USDC.e rewards.

```
┌────────────────────────────────────────────────────────────────────────┐
│                          TRENCHCOAT v1                                  │
│                                                                         │
│  markets.json ─► rank ─► entry-state ─► plan queries (Gemini/Groq/GH)   │
│                                            │                            │
│              DDG + Google/Bing News RSS (multilingual) + Serper/Tavily  │
│                                            │                            │
│              trafilatura / pypdf extraction ─► Playwright screenshots   │
│                                            │                            │
│              LLM evaluate (relevance·novelty·Δforecaster·verifiability) │
│                                            │                            │
│              novelty gate (simhash) ─► note draft ─► GUARDRAILS         │
│                                            │                            │
│                     ┌──────────────────────┴─────────────────────┐      │
│                     │  Co-Pilot: Detective Board review queue    │      │
│                     │  Autopilot: guarded auto-submit            │      │
│                     └──────────────────────┬─────────────────────┘      │
│                                            │                            │
│              Delphi Evidence API (/v1) ─► receipts ─► earnings ledger   │
│              mock │ dry-run │ live        stance ledger · slot budget   │
│                                           feedback bounty tracker       │
└────────────────────────────────────────────────────────────────────────┘
```

## The six edges

| # | Edge | Where |
|---|------|-------|
| 1 | **Co-Pilot mode by default** — 1-click Approve / Edit / Discard on the Detective Board; Autopilot toggle | `board/`, `submit.py` |
| 2 | **Stance Guardrail / Ledger** — first submission locks a market to YES or NO; opposing evidence is blocked (it cancels out in the ranking) | `guardrails.py`, `store.py` |
| 3 | **Slot & Idempotency Protection** — strict 3 submissions/market, **3 items total per submission (note + each link + each file = 1 item each)**, stable `Idempotency-Key` reused on every retry | `guardrails.py`, `delphi/client.py` |
| 4 | **Forecaster Delta & Novelty Gate** — hunts primary records + local-language news the baseline misses; simhash self-duplicate detection; LLM Δ-forecaster scoring | `scout.py`, `prompts.py`, `guardrails.py` |
| 5 | **Receipts, Earnings & Feedback Bounty Tracker** — 10-submission / 3-market qualifier progress, USDC.e ledger (held/paid/settlement upside), auto-logged API/judge quirks for the 5 × $250 bounties | `store.py`, `board/`, `submit.py` |
| 6 | **Clean Delphi API Adapter** — all 8 official `/v1` routes + markets feed + feedback API, with `mock` / `dry-run` / `live` modes | `delphi/client.py` |

## Quickstart ($0, no keys needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

# Optional but recommended — screenshots for sites the Judge can't fetch:
python -m playwright install chromium

cp .env.example .env          # defaults to DELPHI_MODE=mock
trenchcoat seed-demo          # labelled demo case load + mock receipts
trenchcoat board              # → http://127.0.0.1:8000
```

Open the **Detective Board**: case files per market, evidence cards with score breakdowns, the review queue, receipts ledger, stance locks, slot meters, and the feedback bounty tracker. Everything runs offline in mock mode.

### CLI

```bash
trenchcoat scout [--max-markets N] [--market 0x…] [--autopilot|--co-pilot]
trenchcoat review                 # list drafts awaiting approval
trenchcoat approve <candidate-id> # guarded submit
trenchcoat discard <candidate-id>
trenchcoat poll                   # refresh pending receipts
trenchcoat status [--json|--markdown]
trenchcoat doctor                 # key / terms / limits check
trenchcoat terms [--accept VERSION --yes]
trenchcoat feedback [--add TEXT --area judging | --send ID]
trenchcoat export                 # receipts + ledger + feedback JSON
```

## Going live (checklist)

1. **Delphi account** at [delphi.fyi](https://delphi.fyi) → note the wallet on your profile.
2. **Register** that wallet + agent name `Trenchcoat` on [DoraHacks](https://dorahacks.io/hackathon/2479/) (sync ≈ 15 min).
3. **Mainnet API key** at [api-access.delphi.fyi](https://api-access.delphi.fyi/) (keep *Mainnet* selected; same sign-in as Delphi) → `DELPHI_API_ACCESS_KEY` in `.env`. Never commit it.
4. `DELPHI_WALLET_ADDRESS` = your Delphi profile wallet (needed for feedback bounties).
5. `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/) (free tier) — Groq / GitHub Models are automatic fallbacks. Without any key the pipeline runs in clearly-labelled offline heuristic mode.
6. `trenchcoat doctor` → verify limits + terms.
7. **Read & accept the terms** (board Settings page or `trenchcoat terms --accept <version> --yes`). Trenchcoat never accepts without your explicit consent.
8. `DELPHI_MODE=dry-run` → scout → inspect `data/outbox/*.json` payloads (reads hit the real API; nothing is sent).
9. `DELPHI_MODE=live` → Co-Pilot-approve your first real submission.
10. Watch receipts; keep Autopilot off until your accept rate is healthy (a low rate drops the account to the Restricted tier: 1 submission/market).

## Deploy

**GitHub Actions** (`.github/workflows/scout.yml`): scheduled scout every 6 h + manual trigger with mode/autopilot/market inputs. State persists via `actions/cache`; receipts + outbox upload as artifacts; run summary lands in the job summary. Add repo secrets `DELPHI_API_ACCESS_KEY`, `DELPHI_WALLET_ADDRESS`, `GEMINI_API_KEY` (+ optional `GROQ_API_KEY`, `GH_MODELS_TOKEN`, `SERPER_API_KEY`, `TAVILY_API_KEY`) and repo variable `DELPHI_MODE`.

**Docker / HF Spaces / VPS**:

```bash
docker compose up --build        # board on :7860, state in a named volume
```

For a free Hugging Face Space: Docker SDK, `app_port: 7860`, keys as Space secrets, and **set `BOARD_TOKEN`** (a public Space exposes the board; the token gates every route). See `docs/RUNBOOK.md`.

## Repository layout

```
trenchcoat/
├── config.py            # env-driven settings (keys never logged)
├── models.py            # Market / Candidate / Submission / FeedbackItem
├── store.py             # SQLite: receipts, stance ledger, slots, quirks, events
├── guardrails.py        # the 10 pre-submission checks
├── scout.py             # market ranking → research → evaluate → draft
├── submit.py            # single guarded path to the API + receipt polling
├── prompts.py           # LLM task templates (queries / evaluate / note)
├── llm.py               # Gemini → Groq → GitHub Models → heuristic router
├── demo.py              # labelled demo seed (never submittable in live mode)
├── cli.py               # trenchcoat <command>
├── delphi/
│   ├── client.py        # LiveClient / MockClient / DryRunClient (8 routes)
│   └── fixtures.py      # real wave-1 markets for offline mock mode
├── research/
│   ├── search.py        # DDG + multilingual Google/Bing News RSS + boosters
│   ├── extract.py       # trafilatura + pypdf
│   └── screenshots.py   # Playwright Chromium w/ URL+date banner
└── board/
    ├── app.py           # FastAPI: pages + JSON API + optional token gate
    ├── templates/       # noir detective UI (Jinja2)
    └── static/          # board.css / board.js (no build step)
tests/                   # 53 offline tests (guardrails, contract, flow, board)
docs/                    # COMPETITION.md · ARCHITECTURE.md · RUNBOOK.md
```

## Safety rails (why this agent won't burn your account)

- **3 items per submission** — the note, each link and each file count as 1 item each; a 4th is a `400`. The board shows `n/3 items` live.
- **Every id spends a slot** whatever the verdict; duplicates give it back (≤ 10/24 h). Tracked locally and re-checked against live `entry-state`.
- **Idempotency-Key is stable per candidate+content** — a timeout retry reuses it (same id returned, no second slot); editing content mint a new key (avoids `409 idempotency_conflict`).
- **Stance ledger** — opposing YES/NO evidence on one market cancels in the ranking; blocked at draft and at send.
- **Acceptance guard** — Autopilot pauses when < 50% of the last 6 judged were accepted; any manipulation verdict in 24 h halts it outright.
- **Judge-steering lint** — notes that address or instruct the judge are blocked (strikes risk account blocking).
- **Terms gate** — acceptance requires an explicit human click/flag, once per account.
- **Demo data is quarantined** — labelled `DEMO`, never submittable in live mode.

## Docs

- [`docs/COMPETITION.md`](docs/COMPETITION.md) — official rules distilled, with sources
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — data flow, modes, state model
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — day-2 ops, GitHub Actions, HF Spaces, troubleshooting

## License

MIT. Competition submissions grant Gensyn the license described in the competition Terms — read them before accepting.
