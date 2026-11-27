# Architecture

## Data flow

```
competition.delphi.fyi/markets.json
        │  (LiveClient.fetch_markets / fixtures in mock)
        ▼
   rank_markets()          odds 20–80% · cutoff headroom · pot · slots left ·
        │                  local_language/official_statistics blind-spot bonus
        ▼
   GET /markets/{a}/entry-state     ← don't research a dead market
        ▼
   plan_queries()          Gemini → Groq → GitHub Models → heuristic fallback
        │                  multilingual, primary-source-hunting queries
        ▼
   search_all()            DDG HTML + Google/Bing News RSS (hl/gl/ceid presets)
        │                  + optional Serper/Tavily; dedupe by URL; fail-soft
        ▼
   fetch_and_extract()     trafilatura (HTML, metadata/date) · pypdf (≤20 pages)
        │                  hard-to-fetch? → Playwright render + screenshot
        ▼                  (URL+timestamp banner injected before capture)
   evaluate_source()       LLM scores: relevance · novelty · verifiability ·
        │                  forecaster_delta · claim · stance · key_quote
        ▼
   novelty gate            simhash64 vs our prior submissions on the market;
        │                  URL reuse warning; score floor
        ▼
   compose_note()          fact → source → why → working (official order);
        │                  judge-steering lint; ≤8000 chars
        ▼
   Candidate (draft) ──────► Detective Board review queue   [Co-Pilot default]
        │                        Approve / Edit / Discard
        │  Autopilot ON: score floor + acceptance guard + manipulation guard
        ▼
   check_candidate()       10 guardrails re-checked at send time
        ▼
   submit_candidate()      stable Idempotency-Key · terms gate · entry-state ·
        │                  hourly limits · stance lock on first send
        ▼
   POST /v1/submissions    multipart: marketAddress, note, links[], files
        ▼
   poll_receipts()         GET /v1/submissions/{id} · Retry-After respected ·
        │                  bounded reads · verdict/bounty/settlementUpTo merged
        ▼
   SQLite ledger           receipts · stance ledger · slot counters · earnings ·
                           qualifier progress · auto-logged quirks → feedback API
```

## Modes

| Mode | Reads | Writes | Judge | Use |
|---|---|---|---|---|
| `mock` | fixtures (real wave-1 market shapes) | in-process | deterministic heuristic | demos, tests, offline dev |
| `dry-run` | real API when key set (limits/terms/entry-state/markets), else fixtures | captured to `data/outbox/*.json`, never sent | local mock judge on captured payloads | verify access + inspect exact payloads before going live |
| `live` | real API | real API | Delphi's judge | competition play |

`DryRunClient` refuses to accept terms (human consent must happen in live mode).
Demo candidates are flagged and rejected by `submit_candidate` in live mode.

## State model (SQLite, WAL)

- `markets` — feed snapshot per address (question, criteria, odds, cutoff, pots, sources).
- `candidates` — drafts with scores, stance, note, links, files, simhash, status
  (`draft|approved|submitted|discarded|blocked`), demo flag.
- `submissions` — receipt mirror keyed by Delphi id; unique `idempotency_key`;
  status/verdict/bounty/state/settlementUpTo/raw; `mode` tags mock/dry-run/live.
- `stance_ledger` — first submitted stance per market (YES/NO), locked forever.
- `feedback` — quirks (auto-logged from API errors/warnings/unverifiable verdicts,
  or manual), status draft→sent, remote id.
- `events` — audit log surfaced on the board's activity feed.
- `kv` — autopilot toggle, mock terms/hour state.

Slot accounting: every submission with an id counts, whatever the verdict; a
`duplicate` verdict/code gives the slot back; `dry-run` captures never count.
Live sends additionally re-check `entry-state.remainingOnMarket`.

## Idempotency

`key = "tc-" + candidate_id + "-" + sha256(note + sorted(links) + market)[:32]`

- Identical content retry → same key → API returns the first answer (no second slot).
- Edited content → new key (avoids `409 idempotency_conflict`).
- Timeout → key retained; next poll/retry reconciles via `GET /submissions`.
- The key is persisted on the submission row before any network call completes.

## LLM router

`GeminiProvider` (generativelanguage.googleapis.com v1beta, x-goog-api-key) →
`OpenAICompatProvider("groq")` → `OpenAICompatProvider("github-models")` →
`HeuristicProvider`. Each failure falls through and is logged. The heuristic
provider handles the same `TASK:` prompts deterministically so the pipeline runs
with zero keys; its outputs are tagged `provider=heuristic` and the board shows
when no LLM is configured. JSON extraction tolerates fences/prose.

## Research providers

- **DDG**: `html.duckduckgo.com/html/` POST, regex-parsed result blocks, `uddg` unwrap. No key.
- **Google News RSS**: `news.google.com/rss/search?q=&hl=&gl=&ceid=` with 14 locale presets.
- **Bing News RSS**: `bing.com/news/search?q=&format=rss&mkt=`.
- **Serper/Tavily**: optional free-tier boosters when keys exist.
- All fail soft; an empty provider never kills a run. Politeness sleep between queries.

## Screenshots

Playwright sync Chromium, `--no-sandbox`, 1280×900 viewport, 2.5 s render wait,
fixed banner injected with `URL + capture timestamp + agent name` before capture.
Used when the domain is in the hard-fetch list (X/Reddit/social) or extraction
flags JS/blocked pages. The screenshot restores verifiability score and becomes
the 3rd item (note + link + screenshot). Graceful no-op when Playwright/Chromium
is absent (logged, run continues text-only).

## Board

FastAPI + Jinja2 + vanilla JS, no build step. Pages: Case Board (dashboard),
Case File (market detail + evidence cards + edit modal), Receipts, Feedback,
Settings. JSON API under `/api/*` mirrors every action; scout runs in a daemon
thread with a run-state lock the UI polls. Optional `BOARD_TOKEN` gates all
routes except `/static` and `/healthz` (header, query, or cookie). Artifacts
served through a path-traversal-guarded route. Secrets are never rendered —
only presence booleans.

## Testing

53 offline tests: guardrails (stance/slots/items/cutoff/idempotency/novelty/
manipulation-lint), mock client contract (terms gate, replay, cap, 404, limits),
submit flow (lock, opposing block, replay, demo quarantine, qualifier, strikes),
board API (pages, actions, token gate, export), LLM router + search parsing +
market ranking. No network required.
