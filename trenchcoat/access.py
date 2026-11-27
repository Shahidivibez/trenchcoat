"""Official two-call access diagnosis.

Implements step 1 of https://competition.delphi.fyi/key-recovery.md:

  Call A: GET {base}/limits        with Authorization: Bearer <key>
  Call B: GET api.delphi.fyi/me    with x-api-key: <key>

Read together they tell a bad key apart from a wallet with no Delphi account,
and from an account that simply has no evidence access yet (404).

The key is never printed or logged. Call B returns the wallet the key was
issued to, which is the address to compare against the DoraHacks registration.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import Config

UA = "Trenchcoat/0.1 (evidence-mining agent; access-check)"


@dataclass
class Probe:
    status: int
    body: dict
    text: str

    @property
    def code(self) -> str:
        err = self.body.get("error") or {}
        return err.get("code") or ""

    @property
    def request_id(self) -> str:
        return self.body.get("requestId") or ""


def _probe(url: str, headers: dict, timeout: float = 25) -> Probe:
    try:
        resp = httpx.get(url, headers={**headers, "User-Agent": UA},
                         timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        return Probe(0, {}, f"network error: {type(e).__name__}: {e}")
    try:
        body = resp.json()
        if not isinstance(body, dict):
            body = {}
    except ValueError:
        body = {}
    return Probe(resp.status_code, body, resp.text[:400])


def diagnose(a: Probe, b: Probe) -> tuple[str, str, list[str]]:
    """Return (verdict, explanation, next_steps) per the official recovery table."""
    wallet = b.body.get("wallet") or ""
    a_blocked = a.status == 403 and "1010" in a.text

    if a_blocked:
        return ("client_blocked",
                "The HTTP client was blocked before reaching the API (error code 1010).",
                ["Send a real User-Agent header (Trenchcoat already does)."])
    if a.status == 503 or b.status == 503:
        return ("service_down", "A Delphi service is briefly down.",
                ["Wait a minute and run access-check again."])
    if a.status == 200:
        return ("ok", "Evidence API access is working.",
                ["Proceed: accept terms if needed, then scout."])
    if a.status == 403 and a.code == "terms_not_accepted":
        return ("terms_needed", "Key and account work; the competition terms are not accepted.",
                ["Run the 'Accept Delphi Terms' workflow with the confirm box checked."])
    if a.status == 404:
        steps = [
            "Confirm you registered on DoraHacks for THIS competition with either your Delphi "
            "profile wallet or the wallet you sign in to Delphi with.",
            "Compare that registered address to the 'issued to wallet' below — they must match "
            "(for MetaMask sign-in these are two different addresses; both are valid).",
            "Check the registration form: both confirmation questions 'Yes', wallet field contains "
            "ONLY the address.",
            "If you registered <20 min ago, wait and re-run (access syncs every few minutes).",
            "If it still 404s well after registering, send a feedback entry with the requestId.",
        ]
        if b.status == 200:
            return ("no_evidence_access",
                    "Key and Delphi account are fine, but the account has no evidence access yet.",
                    steps)
        return ("no_evidence_access_unconfirmed",
                "Evidence API returned 404 and the key service did not confirm the wallet.",
                steps)
    if a.status == 401 and b.status == 401:
        return ("bad_key",
                "The key itself is wrong, inactive, or a Testnet key (both calls reject it).",
                ["Sign in at delphi.fyi first, then create a MAINNET key at api-access.delphi.fyi.",
                 "Update the DELPHI_API_ACCESS_KEY secret."])
    if a.status in (401,) or (a.status == 403 and a.code == "no_delphi_account"):
        if b.status == 200 and wallet:
            return ("no_delphi_account",
                    "The key is valid, but its wallet has no Delphi account yet.",
                    ["Sign in ONCE at delphi.fyi using the SAME method used on the API keys page "
                     "(wallet / email / Google).",
                     "Wait 60s, then re-run. Do NOT create a new key."])
        return ("key_problem",
                "The evidence API rejected the key and the key service did not confirm it.",
                ["Verify the key is Mainnet and copied in full into the secret."])
    return ("unknown",
            f"Unclassified response (A={a.status} {a.code}, B={b.status}).",
            ["Re-run; if it persists send feedback with the requestId."])


def run_access_check(cfg: Config) -> dict:
    if not cfg.delphi_api_key:
        return {"error": "DELPHI_API_ACCESS_KEY is not set"}
    a = _probe(cfg.delphi_base_url.rstrip("/") + "/limits",
               {"Authorization": f"Bearer {cfg.delphi_api_key}", "Accept": "application/json"})
    b = _probe(cfg.key_service_url, {"x-api-key": cfg.delphi_api_key, "Accept": "application/json"})
    verdict, explanation, steps = diagnose(a, b)
    wallet = b.body.get("wallet") or ""
    return {
        "verdict": verdict,
        "explanation": explanation,
        "next_steps": steps,
        "call_a": {"url": "/limits", "status": a.status, "code": a.code,
                   "request_id": a.request_id},
        "call_b": {"url": cfg.key_service_url, "status": b.status, "code": b.code},
        # public address, not a secret; needed to compare against DoraHacks
        "issued_to_wallet": wallet,
        "wallet_short": (wallet[:6] + "…" + wallet[-4:]) if len(wallet) > 12 else wallet,
    }
