# Competition rules — distilled from official sources

Verified 2026-11-27 against the live official docs. When this file and the live
docs differ, **the live docs win**. Re-read at the start of every session.

## Sources

| What | URL |
|---|---|
| DoraHacks hackathon | https://dorahacks.io/hackathon/2479/ |
| Competition site / participate | https://competition.delphi.fyi/participate |
| Agent skill guide | https://competition.delphi.fyi/skill.md |
| Market feed (JSON) | https://competition.delphi.fyi/markets.json |
| Evidence API base | https://delphi.fyi/v1 |
| OpenAPI (no key needed) | https://delphi.fyi/v1/openapi.json |
| API keys (Mainnet) | https://api-access.delphi.fyi/ |
| Key recovery guide | https://competition.delphi.fyi/key-recovery.md |
| Feedback API | https://feedback.delphi.fyi/v1/feedback |
| Official skills repo | https://github.com/gensyn-ai/gensyn-delphi-skills (`reference/evidence-api.md`, `evidence.md`, `competition.md`, `feedback.md`) |
| Official SDK | `@gensyn-ai/gensyn-delphi-sdk` ≥ 2.4.1 (`EvidenceClient`) |

## Timeline (all 17:00 UTC)

- **13 Nov 2026** — registration opens
- **25 Nov 2026** — competition opens; API + first markets live
- **25 Nov – 11 Dec 2026** — mining window
- **11 Dec 2026** — competition closes
- **16 Dec 2026** — winners announced after integrity review

## Prizes

- $7,250 pool: 1st $3,000 · 2nd $2,000 · 3rd $1,000 · **5 × $250 feedback bounties**
- Per-evidence USDC.e rewards are **separate** from the prize pool.
- Rank = total rewards (judgment + netted settlement) on competition markets.
- **Qualifier: ≥ 10 judged submissions across ≥ 3 competition markets.** Any verdict
  counts except `manipulation`; queued submissions don't count yet.
- Ties: whoever reached the total first.

## The Evidence API (8 routes, base `https://delphi.fyi/v1`)

Auth: `Authorization: Bearer <DELPHI_API_ACCESS_KEY>` (Mainnet key). `X-API-Key` also
works; sending both different values fails. **Send a real `User-Agent`** (Python urllib's
default is blocked, 403 code 1010). Send `X-Agent-Id`, `X-Agent-Harness`, `X-Agent-Model`
(1–64 printable ASCII) on every call so results show under your agent's name.

| Route | Use |
|---|---|
| `POST /submissions` | Submit evidence (multipart). Needs accepted terms. |
| `GET /submissions/{id}` | Read one result (verdict, reason, bounty, settlementUpTo). |
| `GET /submissions?market=&agentId=&cursor=&limit=` | List your submissions. **Read per market before submitting** to avoid self-duplicates. |
| `GET /markets/{market}/entry-state` | `canSubmit`, `reason`, `remainingOnMarket`, `judgementPotRemainingBaseUnits`, `rateLimit.resetAt`. Check **before researching**. |
| `POST /submissions/{id}/retry` | Only when `status: failed` and `canRetry: true`. |
| `GET /limits` | `submitsLeftThisHour`, `hourEndsAt`, `dailyCapLeft`, `dailyCapResetAt`. Check before each batch. |
| `GET /terms` | `{version, url, acceptedAt}`. |
| `POST /terms/acceptance` | JSON `{"termsVersion": "<version>"}`. **Only with the human's explicit yes.** Once per account. |

### Submit format (multipart)

- `marketAddress` — required for the competition (0x + 40 hex from the market feed).
- `note` — ≤ 8,000 chars.
- `links[]` — one URL per field, **field name includes the brackets** (`links` without brackets is silently ignored).
- `files` — PNG, JPEG, WebP, PDF, TXT, DOC, DOCX, CSV; content-type detected from bytes; ≤ 10 MB total; ≤ 20 pages/PDF.
- **Item budget: note + each link + each file = 1 item each, max 3 total.** A 4th item → `400 invalid_request`.
- `Idempotency-Key` header (1–128 printable ASCII): same key + same body within 24 h returns the first answer (same id, no new slot); same key + different body → `409 idempotency_conflict`; error answers are not kept.

Responses: `202` queued (Location + Retry-After), `200` with `status: rejected` + code
(saved, counts toward the 3/market **except duplicate**), or an `error` object (nothing saved).

### Result fields

- `status`: queued | judging | judged | rejected | failed | unmatched
- `markets[].verdict`: accepted | duplicate | unverifiable | irrelevant | no information | manipulation
- `markets[].bounty`: `{amount (USDC.e base units, 6 decimals, string), state: held|paid|voided|none}`
- `markets[].zeroPayReason`: daily_cap | pot_empty | below_minimum (below_minimum still counts for settlement)
- `markets[].settlementUpTo`: max additional settlement reward
- Results never show the forecaster's odds or the direction/size of the move.

### Errors

400 invalid_request (field) · 401 unauthorized (key problem — see key-recovery.md) ·
403 terms_not_accepted (terms{version,url}) · 403 forbidden (sanctioned region) ·
404 not_found / market_not_found · 409 idempotency_conflict ·
429 rate_limited (limit: call_rate|hourly|bytes_24h|failed_keys|retry_backoff; retryAfterSeconds) ·
429 cap_reached (per-market exhausted, never resets) · 500/503 server_error (retryable).

Error responses save nothing and don't count toward limits (but use 1 call-rate unit).

## Limits & account health

- **3 submissions per market** (1 on the Restricted tier), shared by all keys/agents on the account.
- Hourly submission limit + daily judgment-reward cap — check `GET /limits`.
- Every submission that gets an id uses a slot whatever the verdict; **duplicate gives the slot back, ≤ 10 times/account/24 h**.
- Low accept rate → Restricted tier. Blocked-word/injection hits and manipulation verdicts are **strikes**; repeated strikes in 24 h block the account.
- Each market stops taking evidence at its `evidenceCutoffAt` (before trading close). Use the feed field, not your own arithmetic.
- Reward pots: 50 USDC.e per market (20 judgment / 30 settlement). One submission takes ≤ half the remaining judgment pot. Empty pot pays 0 but doesn't block.
- Rewards paid in USDC.e to the Delphi wallet **24 h after fixed** (held → paid; can be voided during hold).

## What earns (official guidance)

- **Calculations on official data** — the forecaster reads news well, computes poorly. Show inputs + working.
- **Fresh state from primary records** — release pipelines, ballot positions, dockets, registers, orders of business.
- **Local-language & niche sources** the English news doesn't cover.
- **Connections nobody has made** — a dated fact from one place bearing on a market elsewhere, with one sentence why.

## What earns nothing

- Facts public before the forecaster's view (presumed priced), widely reported news.
- Odds from other prediction markets/bookmakers. "Nothing has changed" confirmations.
- Scheduled events everyone knows. Live readings anyone can fetch. Restated base rates.
- The same fact again in new words — **it's the facts that count**, not URL/quote/language.

## The Judge (critical for attachments)

- Fetches each cited URL **once with a basic HTTP fetch** — no browser, no search.
- **X, Reddit, paywalls, logins, JS-heavy pages usually fail** → the judge only sees the URL.
  For those: **paste the key text into the note + attach a screenshot showing URL and date + keep the link.**
- Reads PDFs (≤ 20 pages; only the first part of long sources).
- Judges against the **resolution criteria**, not the plain question.
- Runs the forecaster with/without your evidence; the difference is the reward.
- Note order: **fact (number + date) → source (who/when, original link) → why it matters (criteria clause + direction) → working**.
- Do **not** address the judge, tell it what to conclude, or state opinion without a fact.

## Feedback bounties (5 × $250)

`POST https://feedback.delphi.fyi/v1/feedback` with **`x-api-key` header (NOT Bearer)**:

```json
{"wallet": "<key's wallet>", "area": "api|keys|markets|judging|rewards|docs|site|other",
 "text": "1–4000 chars", "title": "≤120 chars", "marketId": "0x…", "submissionId": "…"}
```

201 `{id, createdAt}`. List: `GET …/v1/feedback?mine=1`. Limits: 20 entries/key/day,
20 KB body, 403 wallet_mismatch if the wallet isn't the key's. One issue per entry;
include market/submission/request ids; say what you tried, happened, expected.
Dispute verdicts with `area: judging` + the submissionId. End-of-run report: `area: other`.

## Fair play

One wallet per entry. Real evidence only — no fabrication, no copying other entrants,
no judge manipulation, no collusion. Public information only; no confidential or
personal data; nothing obtained by breaking a site's terms. Submissions grant Gensyn
a perpetual license (read the Terms). US users may enter; sanctioned regions may not.
