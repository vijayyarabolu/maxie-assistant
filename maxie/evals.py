"""The 120-command eval: expected route + action for typed and transcribed-style commands.

    python -m maxie.evals                 # offline router (rules + lexicon), what CI runs
    python -m maxie.evals --router laya   # on the Mac, against the local Laya server
    python -m maxie.evals --router haiku  # Haiku fallback (spends ~1 cent)
    python -m maxie.evals --check         # fail if accuracy is below evals/baseline.json

A command passes when the route matches AND the direct action matches (expected none means
"must go to Claude": a wrong direct action is a failure, not a near miss).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from .budget import Budget
from .db import DB
from .router import Router

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ROOT / "evals" / "commands.jsonl"
BASELINE = ROOT / "evals" / "baseline.json"


def load(path: Path = COMMANDS) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _args_ok(expected: dict, got: dict) -> bool:
    for k, v in expected.items():
        g = got.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            try:
                if abs(float(g) - v) > 1e-6:
                    return False
            except (TypeError, ValueError):
                return False
        elif str(v).lower() != str(g).lower():
            return False
    return True


def score(case: dict, decision) -> dict:
    route_ok = decision.route == case["route"]
    action_ok = decision.action == case.get("action") and _args_ok(case.get("args", {}), decision.args)
    return {"id": case["id"], "text": case["text"], "expected": [case["route"], case.get("action")],
            "got": [decision.route, decision.action], "args": decision.args, "source": decision.source,
            "route_ok": route_ok, "pass": route_ok and action_ok, "latency_ms": round(decision.latency_ms, 2)}


def run(router: Router, budget: Budget, cases: list[dict]) -> dict:
    before = budget.status().month_usd
    results = [score(c, router.route(c["text"])) for c in cases]
    cost = budget.status().month_usd - before
    lat = [r["latency_ms"] for r in results]
    by_style = {s: [r for r, c in zip(results, cases, strict=True) if c["style"] == s] for s in ("typed", "spoken")}
    confusion = Counter(f"{r['expected'][0]}->{r['got'][0]}" for r in results if not r["route_ok"])
    return {
        "n": len(results),
        "accuracy": round(sum(r["pass"] for r in results) / len(results), 4),
        "route_accuracy": round(sum(r["route_ok"] for r in results) / len(results), 4),
        "by_style": {s: round(sum(r["pass"] for r in rs) / len(rs), 4) for s, rs in by_style.items() if rs},
        "latency_ms": {"p50": round(statistics.median(lat), 2), "p95": round(sorted(lat)[int(len(lat) * 0.95) - 1], 2)},
        "cost_usd_total": round(cost, 6),
        "cost_usd_per_command": round(cost / len(results), 6),
        "sources": dict(Counter(r["source"] for r in results)),
        "confusion": dict(confusion.most_common()),
        "failures": [r for r in results if not r["pass"]],
    }


def build_router(kind: str, db: DB, budget: Budget) -> Router:
    if kind == "offline":
        return Router(db)
    from .config import Settings, secret
    if kind == "laya":
        from .router.laya import LayaClient
        settings = Settings.load()
        laya = LayaClient(settings.laya_url, secret("LAYA_API_KEY"), timeout=10)
        if not laya.healthy():
            sys.exit(f"Laya is not answering at {settings.laya_url}")
        from .router.calibrate import Calibration
        from .router.core import ROUTES
        # Raw Laya: every route accepted, whatever its probability (the hub itself gates on calibration).
        r = Router(db, laya=laya)
        r.calibration = Calibration(thresholds=dict.fromkeys(ROUTES, 0.0))
        return r
    if kind == "haiku":
        import anthropic

        from .router.haiku import HaikuRouter
        return Router(db, haiku=HaikuRouter(anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY")), budget))
    raise SystemExit(f"unknown router {kind}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--router", default="offline", choices=["offline", "laya", "haiku"])
    ap.add_argument("--check", action="store_true", help="exit 1 if accuracy is below the baseline")
    ap.add_argument("--out", default=str(ROOT / "evals" / "last_report.json"))
    args = ap.parse_args(argv)

    db = DB()
    budget = Budget(db)
    report = run(build_router(args.router, db, budget), budget, load())
    report["router"] = args.router
    Path(args.out).write_text(json.dumps(report, indent=2))

    print(f"router={args.router}  accuracy={report['accuracy']:.0%}  route={report['route_accuracy']:.0%}  "
          f"typed={report['by_style'].get('typed', 0):.0%} spoken={report['by_style'].get('spoken', 0):.0%}  "
          f"p50={report['latency_ms']['p50']}ms p95={report['latency_ms']['p95']}ms  "
          f"cost/cmd=${report['cost_usd_per_command']:.5f}")
    for f in report["failures"]:
        print(f"  FAIL #{f['id']:>3} {f['text']!r}: expected {f['expected']} got {f['got']} ({f['source']})")

    if args.check:
        baseline = json.loads(BASELINE.read_text())[args.router]
        if report["accuracy"] < baseline:
            print(f"accuracy {report['accuracy']:.2%} is below the baseline {baseline:.2%}")
            return 1
        print(f"ok: at or above the baseline {baseline:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
