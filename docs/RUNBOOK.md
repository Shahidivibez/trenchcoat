# Runbook — day-2 operations

## Daily loop (competition window)

1. `trenchcoat doctor` — key valid? terms accepted? hourly/daily limits?
2. `trenchcoat scout --co-pilot` (or the board's **Run scout**) — research top markets.
3. Review the queue on the board: **Approve** (guarded submit), **Edit** (fix note/stance/links), **Discard**.
4. `trenchcoat poll` — refresh verdicts; read reasons.
5. `trenchcoat status` — qualifier progress + earnings.
6. Log/send feedback for anything broken (`/feedback` page) — 5 × $250 bounties.

Spend slots on **3 different facts** per market, not 3 tries at one. Prefer
calculations on official data and local-language primary records over headline racing.

## GitHub Actions

`.github/workflows/scout.yml` runs every 6 h (`17 */6 * * *`) + manual dispatch.

**Repo secrets** (Settings → Secrets and variables → Actions):
`DELPHI_API_ACCESS_KEY`, `DELPHI_WALLET_ADDRESS`, `GEMINI_API_KEY`,
optional `GROQ_API_KEY`, `GH_MODELS_TOKEN`, `SERPER_API_KEY`, `TAVILY_API_KEY`.

**Repo variables**: `DELPHI_MODE` (`mock`/`dry-run`/`live`), `TRENCHCOAT_AUTOPILOT` (`0`/`1`),
optional `GEMINI_MODEL`, `DELPHI_AGENT_MODEL`.

- **Accept terms first (one-time):** the scout reads the real `entry-state`, which returns
  `terms_not_accepted` until the account accepts. In pure Path B, run
  **Actions → "Accept Delphi Terms" → Run workflow**, read the terms URL printed in the
  "Show terms" step, then re-run with the **confirm** box checked. (Locally: `trenchcoat accept-terms --yes`.)
- State (SQLite + outbox + artifacts) persists via `actions/cache` (`trenchcoat-state-*`)
  and uploads as a per-run artifact. Download the artifact to inspect receipts/offline.
- Manual dispatch inputs: mode, autopilot, max_markets, market filter, install_browser.
- The job summary shows qualifier progress + earnings after every run.
- **Co-Pilot approvals** happen on the board (local/Docker). For headless approval,
  download the state artifact, run `trenchcoat review` / `approve <id>` locally with
  the same data dir, or dispatch with autopilot=true once your accept rate is healthy.
- After 11 Dec 17:00 UTC: disable the schedule (delete the cron or the workflow).

## Hugging Face Spaces ($0)

1. Create a Space → Docker SDK → blank.
2. Push this repo (or the Dockerfile + package) to the Space remote.
3. Space README front-matter:
   ```yaml
   ---
   title: Trenchcoat Detective Board
   emoji: 🕵️
   colorFrom: gray
   colorTo: red
   sdk: docker
   app_port: 7860
   ---
   ```
4. Space secrets: `DELPHI_API_ACCESS_KEY`, `DELPHI_WALLET_ADDRESS`, `GEMINI_API_KEY`,
   **`BOARD_TOKEN`** (required — a public Space exposes the board; the token gates it),
   `DELPHI_MODE=dry-run` to start.
5. Persistent storage: attach a Space persistent disk at `/data` (or accept ephemeral
   state and export receipts regularly via `/api/export`).
6. Open `https://<space>.hf.space/?token=<BOARD_TOKEN>`.

The image installs Chromium by default; build with `--build-arg INSTALL_BROWSER=0`
for a lighter text-only image.

## VPS (~$5)

```bash
git clone <repo> && cd trenchcoat
cp .env.example .env   # fill keys; set BOARD_TOKEN if the port is public
docker compose up -d --build
# board on :7860, state in the trenchcoat-data volume
# cron scout on the host or inside the container:
docker compose exec board trenchcoat scout --co-pilot
```

Put the board behind a reverse proxy with TLS, or keep it bound to localhost and
use an SSH tunnel: `ssh -L 8000:127.0.0.1:7860 user@vps`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 unauthorized` | Key is wrong/testnet, or the wallet has no Delphi account. Follow https://competition.delphi.fyi/key-recovery.md — do **not** mint a new key blindly. |
| `404 not_found` on every route | DoraHacks wallet ≠ Delphi profile wallet, or sync pending (~15 min). |
| `403 terms_not_accepted` | Read the terms URL, accept explicitly (board Settings / `trenchcoat terms --accept V --yes`). |
| `400 invalid_request field=links[]` | More than 3 items (note + links + files). Drop one. |
| `409 idempotency_conflict` | Same key, different body. Edit mints a new key automatically; only happens if you reuse keys manually. |
| `429 cap_reached` | Market's 3 slots spent. Move to another market. |
| `429 rate_limited limit=hourly` | Wait for `hourEndsAt`; never loop. |
| Verdict `unverifiable` | Judge couldn't fetch the source. Attach the document/screenshot + paste key text; submit a **new** fact (resending the same fact earns nothing). |
| Verdict `duplicate` | Same fact already accepted. Slot returned (≤10/24h). Move on. |
| Accepted, 0 pay (`below_minimum`) | Moved the forecast too little; still counts for settlement. Don't resend. |
| Autopilot paused | Acceptance guard (<50% of last 6) or manipulation strike in 24h. Review notes; fix quality before re-enabling. |
| No screenshots | `python -m playwright install chromium` (CI: `--with-deps`). Runs continue text-only without it. |
| Search empty | DDG may rate-limit; RSS still works. Add `SERPER_API_KEY`/`TAVILY_API_KEY` free tiers for boost. |
| Board 401 | `BOARD_TOKEN` set — pass `?token=…` or `X-Board-Token` header. |

## Key recovery (official flow)

Key shows Active but API rejects it → the key is fine; its wallet has no Delphi
account yet. Sign in once at delphi.fyi the same way you signed in on the API keys
page, wait a minute, retry. Full guide: https://competition.delphi.fyi/key-recovery.md.

## Export & audit

- `trenchcoat export` / board `/api/export` — full receipts, stance ledger, feedback, candidates.
- `data/trenchcoat.db` — SQLite; inspect with any SQLite client.
- `data/outbox/*.json` — exact dry-run payloads (what would have been sent).
- Every API call's request id, warnings and verdicts are in the `events` table / board activity feed.
