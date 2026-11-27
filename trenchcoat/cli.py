"""Trenchcoat CLI: python -m trenchcoat <command> (or `trenchcoat` after pip install)."""

from __future__ import annotations

import argparse
import json
import sys

from .config import get_config
from .store import Store


def _store() -> Store:
    return Store(get_config().db_path)


def cmd_scout(args) -> int:
    from .scout import scout_run

    cfg = get_config()
    store = _store()
    autopilot = None
    if args.autopilot:
        autopilot = True
    if args.co_pilot:
        autopilot = False
    report = scout_run(cfg, store, max_markets=args.max_markets,
                       autopilot=autopilot, market_filter=args.market)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.errors else 1


def cmd_board(args) -> int:
    import uvicorn

    from .board.app import create_app

    cfg = get_config()
    host = args.host or cfg.board_host
    port = args.port or cfg.board_port
    app = create_app(cfg)
    print(f"Trenchcoat Detective Board -> http://{host}:{port}  (mode={cfg.delphi_mode})")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def cmd_status(args) -> int:
    cfg = get_config()
    store = _store()
    stats = store.stats()
    if args.json:
        print(json.dumps({"config": cfg.redacted_summary(), "stats": stats}, indent=2))
        return 0
    if args.markdown:
        print(status_markdown(cfg, stats))
        return 0
    print(f"Trenchcoat — mode={cfg.delphi_mode} autopilot={cfg.autopilot}")
    print(f"  judged submissions : {stats['judged_total']}  (qualifier: {stats['qualifier_submissions']}/10)")
    print(f"  markets covered    : {stats['markets_covered']}  (qualifier: {stats['qualifier_markets']}/3)")
    print(f"  qualified          : {'YES' if stats['qualified'] else 'not yet'}")
    print(f"  earned             : {stats['earned_usdc']:.2f} USDC (paid {stats['paid_usdc']:.2f}, held {stats['held_usdc']:.2f})")
    print(f"  settlement upside  : {stats['settlement_potential_usdc']:.2f} USDC")
    print(f"  accept rate (last 6): {stats['recent_accept_rate']}")
    return 0


def status_markdown(cfg, stats) -> str:
    lines = [
        "## Trenchcoat run summary",
        f"- mode: `{cfg.delphi_mode}` · autopilot: `{cfg.autopilot}`",
        f"- judged submissions: **{stats['judged_total']}** (qualifier {stats['qualifier_submissions']}/10)",
        f"- markets covered: **{stats['markets_covered']}** (qualifier {stats['qualifier_markets']}/3)",
        f"- qualified: **{'yes' if stats['qualified'] else 'not yet'}**",
        f"- earned: **{stats['earned_usdc']:.2f} USDC** (paid {stats['paid_usdc']:.2f} / held {stats['held_usdc']:.2f})",
        f"- settlement upside: {stats['settlement_potential_usdc']:.2f} USDC",
    ]
    return "\n".join(lines)


def cmd_review(args) -> int:
    store = _store()
    cands = store.candidates(status="draft")
    if not cands:
        print("No drafts awaiting review.")
        return 0
    for c in cands:
        m = store.market(c.market_address)
        q = (m.question[:70] + "…") if m else c.market_address
        print(f"[{c.id}] {c.stance:7s} score={c.score:.2f} items={c.item_count}  {q}")
        print(f"    {c.claim[:110]}")
    return 0


def cmd_approve(args) -> int:
    from .submit import submit_candidate

    res = submit_candidate(get_config(), _store(), args.id)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


def cmd_discard(args) -> int:
    store = _store()
    cand = store.candidate(args.id)
    if cand is None:
        print("candidate not found")
        return 1
    cand.status = "discarded"
    cand.block_reason = args.reason or "discarded by operator"
    store.save_candidate(cand)
    store.log("discarded", cand.claim[:80], ref=cand.id)
    print(f"discarded {cand.id}")
    return 0


def cmd_poll(args) -> int:
    from .submit import poll_receipts

    print(json.dumps(poll_receipts(get_config(), _store()), indent=2))
    return 0


def cmd_doctor(args) -> int:
    from .delphi import DelphiAPIError, get_client

    cfg = get_config()
    store = _store()
    print("Trenchcoat doctor")
    print(json.dumps(cfg.redacted_summary(), indent=2))
    client = get_client(cfg, store)
    try:
        limits = client.get_limits().data
        print(f"limits: {json.dumps(limits)}")
        terms = client.get_terms().data
        print(f"terms: version={terms.get('version')} acceptedAt={terms.get('acceptedAt')}")
        if not terms.get("acceptedAt"):
            print(f"  -> read {terms.get('url')} and accept explicitly before live submissions")
    except DelphiAPIError as e:
        print(f"API check failed: {e}")
        if e.code == "unauthorized":
            print("  -> check the key is a MAINNET key from api-access.delphi.fyi, same sign-in as delphi.fyi")
        if e.code == "not_found":
            print("  -> DoraHacks registration wallet must match the Delphi profile wallet; sync takes ~15 min")
        return 1
    return 0


def cmd_terms(args) -> int:
    from .delphi import DelphiAPIError, get_client

    cfg = get_config()
    client = get_client(cfg, _store())
    try:
        terms = client.get_terms().data
    except DelphiAPIError as e:
        print(f"terms check failed: {e}")
        return 1
    print(json.dumps(terms, indent=2))
    if args.accept:
        if not args.yes:
            print("Refusing to accept terms without --yes (explicit human consent required).")
            print(f"Read {terms.get('url')} first.")
            return 1
        res = client.accept_terms(args.accept)
        print(json.dumps(res.data, indent=2))
    return 0


def cmd_feedback(args) -> int:
    from .models import FeedbackItem
    from .submit import send_feedback

    cfg = get_config()
    store = _store()
    if args.add:
        f = FeedbackItem(area=args.area, title=args.title or args.add[:80], text=args.add,
                         market_id=args.market, submission_id=args.submission)
        store.save_feedback(f)
        print(f"logged {f.id}")
        return 0
    if args.send:
        print(json.dumps(send_feedback(cfg, store, args.send), indent=2))
        return 0
    items = store.feedback_items()
    if not items:
        print("No feedback items logged.")
    for f in items:
        print(f"[{f.id}] {f.status:6s} {f.area:8s} {f.title}")
    return 0


def cmd_seed_demo(args) -> int:
    from .demo import seed_demo

    print(json.dumps(seed_demo(get_config(), _store()), indent=2))
    return 0


def cmd_export(args) -> int:
    store = _store()
    data = {
        "stats": store.stats(),
        "submissions": [s.to_dict() for s in store.submissions()],
        "stance_ledger": store.stance_ledger(),
        "feedback": [f.to_dict() for f in store.feedback_items()],
    }
    print(json.dumps(data, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trenchcoat", description="Trenchcoat evidence-mining agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scout", help="run a research cycle")
    s.add_argument("--max-markets", type=int, default=None)
    s.add_argument("--market", type=str, default=None, help="filter by address or question substring")
    s.add_argument("--autopilot", action="store_true")
    s.add_argument("--co-pilot", action="store_true")
    s.set_defaults(fn=cmd_scout)

    s = sub.add_parser("board", help="run the Detective Board web UI")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(fn=cmd_board)

    s = sub.add_parser("status", help="qualifier + earnings summary")
    s.add_argument("--json", action="store_true")
    s.add_argument("--markdown", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("review", help="list drafts awaiting review")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("approve", help="approve + submit a draft")
    s.add_argument("id")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("discard", help="discard a draft")
    s.add_argument("id")
    s.add_argument("--reason", default=None)
    s.set_defaults(fn=cmd_discard)

    s = sub.add_parser("poll", help="refresh pending receipts")
    s.set_defaults(fn=cmd_poll)

    s = sub.add_parser("doctor", help="check config, key, terms, limits")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("terms", help="read/accept terms (accept needs --yes)")
    s.add_argument("--accept", metavar="VERSION", default=None)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_terms)

    s = sub.add_parser("feedback", help="list/log/send feedback (bounty tracker)")
    s.add_argument("--add", metavar="TEXT", default=None)
    s.add_argument("--area", default="api")
    s.add_argument("--title", default=None)
    s.add_argument("--market", default=None)
    s.add_argument("--submission", default=None)
    s.add_argument("--send", metavar="ID", default=None)
    s.set_defaults(fn=cmd_feedback)

    s = sub.add_parser("seed-demo", help="seed labelled demo data (mock mode)")
    s.set_defaults(fn=cmd_seed_demo)

    s = sub.add_parser("export", help="export receipts/ledger/feedback as JSON")
    s.set_defaults(fn=cmd_export)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
