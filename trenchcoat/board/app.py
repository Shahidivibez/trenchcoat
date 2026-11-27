"""Detective Board: FastAPI + Jinja2 + vanilla JS. Co-Pilot review queue,
case files per market, receipts ledger, feedback bounty tracker, ops controls.

Optional BOARD_TOKEN gates every route when the board is exposed beyond
localhost (e.g. a public HF Space). Never renders secret values.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config, get_config
from ..models import FeedbackItem, hours_until, usdc
from ..store import Store

HERE = Path(__file__).parent

_run_lock = threading.Lock()
_run_state: dict = {"running": False, "kind": None, "started": None, "last_report": None}


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    store = Store(cfg.db_path)
    app = FastAPI(title="Trenchcoat Detective Board", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["usdc"] = usdc
    templates.env.filters["hours_until"] = hours_until
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # ── optional token gate ───────────────────────────────────────────
    @app.middleware("http")
    async def token_gate(request: Request, call_next):
        if cfg.board_token:
            path = request.url.path
            if not path.startswith("/static") and path != "/healthz":
                supplied = (request.headers.get("X-Board-Token")
                            or request.query_params.get("token")
                            or request.cookies.get("board_token"))
                if supplied != cfg.board_token:
                    return JSONResponse({"error": "board token required"}, status_code=401)
        return await call_next(request)

    def ctx(request: Request, **extra) -> dict:
        stats = store.stats()
        base = {
            "request": request, "cfg": cfg, "stats": stats,
            "mode": cfg.delphi_mode, "autopilot": _autopilot(cfg, store),
            "run_state": _run_state, "markets": store.markets(),
        }
        base.update(extra)
        return base

    # ── pages ─────────────────────────────────────────────────────────
    @app.get("/")
    def dashboard(request: Request):
        events = store.events(40)
        drafts = store.candidates(status="draft")
        return templates.TemplateResponse(request, "dashboard.html", ctx(
            request, events=events, drafts=drafts,
            ledger=store.stance_ledger(),
        ))

    @app.get("/market/{address}")
    def market_page(request: Request, address: str):
        m = store.market(address)
        if m is None:
            raise HTTPException(404, "market not found")
        cands = store.candidates(market=address)
        subs = store.submissions(market=address)
        return templates.TemplateResponse(request, "market.html", ctx(
            request, m=m, candidates=cands, submissions=subs,
            candidates_json=[c.to_dict() for c in cands],
            locked_stance=store.locked_stance(address),
            slots_used=store.slots_used(address),
        ))

    @app.get("/receipts")
    def receipts_page(request: Request):
        return templates.TemplateResponse(request, "receipts.html", ctx(
            request, submissions=store.submissions(), ledger=store.stance_ledger(),
        ))

    @app.get("/feedback")
    def feedback_page(request: Request):
        return templates.TemplateResponse(request, "feedback.html", ctx(
            request, items=store.feedback_items(),
        ))

    @app.get("/settings")
    def settings_page(request: Request):
        terms = None
        limits = None
        api_error = None
        from ..delphi import DelphiAPIError, get_client

        try:
            client = get_client(cfg, store)
            terms = client.get_terms().data
            limits = client.get_limits().data
        except DelphiAPIError as e:
            api_error = str(e)
        return templates.TemplateResponse(request, "settings.html", ctx(
            request, summary=cfg.redacted_summary(), terms=terms, limits=limits,
            api_error=api_error,
        ))

    # ── JSON API ──────────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "mode": cfg.delphi_mode}

    @app.get("/api/state")
    def api_state():
        return {"stats": store.stats(), "run_state": _run_state,
                "autopilot": _autopilot(cfg, store), "mode": cfg.delphi_mode,
                "config": cfg.redacted_summary()}

    @app.post("/api/scout")
    def api_scout(payload: dict | None = None):
        payload = payload or {}
        if _run_state["running"]:
            raise HTTPException(409, "a run is already in progress")

        def work():
            from ..scout import scout_run

            with _run_lock:
                _run_state.update(running=True, kind="scout", started=_now())
                try:
                    report = scout_run(
                        cfg, store,
                        max_markets=payload.get("max_markets"),
                        autopilot=payload.get("autopilot"),
                        market_filter=payload.get("market"),
                    )
                    _run_state["last_report"] = report.to_dict()
                except Exception as e:  # keep the board alive
                    store.log("scout-error", f"{type(e).__name__}: {e}")
                    _run_state["last_report"] = {"errors": [str(e)]}
                finally:
                    _run_state.update(running=False, kind=None)

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "started": True}

    @app.post("/api/poll")
    def api_poll():
        from ..submit import poll_receipts

        return poll_receipts(cfg, store)

    @app.post("/api/candidates/{cid}/approve")
    def api_approve(cid: str):
        from ..submit import submit_candidate

        res = submit_candidate(cfg, store, cid)
        if not res.get("ok"):
            return JSONResponse(res, status_code=400)
        # fetch the fresh receipt right away (mock judges instantly; live may be queued)
        from ..submit import poll_receipts

        poll_receipts(cfg, store, max_polls=3)
        return res

    @app.post("/api/candidates/{cid}/discard")
    def api_discard(cid: str, payload: dict | None = None):
        cand = store.candidate(cid)
        if cand is None:
            raise HTTPException(404, "candidate not found")
        cand.status = "discarded"
        cand.block_reason = (payload or {}).get("reason") or "discarded by operator"
        store.save_candidate(cand)
        store.log("discarded", cand.claim[:80], ref=cand.id)
        return {"ok": True}

    @app.post("/api/candidates/{cid}/edit")
    def api_edit(cid: str, payload: dict):
        cand = store.candidate(cid)
        if cand is None:
            raise HTTPException(404, "candidate not found")
        if cand.status == "submitted":
            raise HTTPException(400, "cannot edit a submitted candidate")
        if "note" in payload:
            cand.note = str(payload["note"])[: cfg.max_note_chars]
        if "stance" in payload and payload["stance"] in {"YES", "NO", "NEUTRAL"}:
            cand.stance = payload["stance"]
        if "claim" in payload:
            cand.claim = str(payload["claim"])[:300]
        if "links" in payload and isinstance(payload["links"], list):
            cand.links = [str(u) for u in payload["links"] if str(u).strip()][:2]
        # recompute simhash + item budget after edit
        from ..guardrails import check_candidate, simhash64

        cand.simhash = simhash64(cand.claim + " " + cand.note[:2000])
        m = store.market(cand.market_address)
        guard = check_candidate(cfg, store, cand, m)
        cand.status = "draft" if guard.ok else "blocked"
        cand.block_reason = "" if guard.ok else "; ".join(guard.violations)
        store.save_candidate(cand)
        store.log("edited", f"{cand.claim[:70]} (rev content -> new idempotency key)", ref=cand.id)
        return {"ok": guard.ok, "violations": guard.violations, "warnings": guard.warnings}

    @app.post("/api/autopilot")
    def api_autopilot(payload: dict):
        enabled = bool(payload.get("enabled"))
        store.kv_set("autopilot", "1" if enabled else "0")
        store.log("autopilot", f"Autopilot {'ENABLED' if enabled else 'disabled'} by operator")
        return {"ok": True, "autopilot": enabled}

    @app.post("/api/feedback")
    def api_feedback_add(payload: dict):
        f = FeedbackItem(
            area=payload.get("area", "api"),
            title=str(payload.get("title", ""))[:120],
            text=str(payload.get("text", ""))[:4000],
            market_id=payload.get("market_id") or None,
            submission_id=payload.get("submission_id") or None,
        )
        if not f.text.strip():
            raise HTTPException(400, "text required")
        store.save_feedback(f)
        return {"ok": True, "id": f.id}

    @app.post("/api/feedback/{fid}/send")
    def api_feedback_send(fid: str):
        from ..submit import send_feedback

        res = send_feedback(cfg, store, fid)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    @app.post("/api/terms/accept")
    def api_terms_accept(payload: dict):
        from ..delphi import DelphiAPIError, get_client

        if not payload.get("confirm"):
            raise HTTPException(400, "explicit confirm=true required (human consent)")
        version = payload.get("version")
        if not version:
            raise HTTPException(400, "version required")
        try:
            res = get_client(cfg, store).accept_terms(version)
        except DelphiAPIError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        store.log("terms", f"Terms {version} accepted by operator")
        return {"ok": True, "data": res.data}

    @app.post("/api/seed-demo")
    def api_seed_demo():
        from ..demo import seed_demo

        if cfg.delphi_mode == "live":
            raise HTTPException(400, "demo seeding is disabled in live mode")
        return seed_demo(cfg, store)

    @app.get("/artifacts/{name}")
    def artifact(name: str):
        # path traversal guard: only plain filenames inside artifacts dir
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(400, "bad path")
        path = cfg.artifacts_dir / name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/api/export")
    def api_export():
        return {
            "stats": store.stats(),
            "submissions": [s.to_dict() for s in store.submissions()],
            "stance_ledger": store.stance_ledger(),
            "feedback": [f.to_dict() for f in store.feedback_items()],
            "candidates": [c.to_dict() for c in store.candidates()],
        }

    return app


def _autopilot(cfg: Config, store: Store) -> bool:
    v = store.kv_get("autopilot")
    if v is None:
        return cfg.autopilot
    return v == "1"


def _now() -> str:
    from ..models import utcnow

    return utcnow()
