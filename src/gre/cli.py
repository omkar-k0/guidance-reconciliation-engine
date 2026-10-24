"""Command line: gre fetch | run | label | eval | export"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    p = argparse.ArgumentParser(prog="gre", description="Guidance reconciliation engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download filings and XBRL facts from SEC EDGAR")
    f.add_argument("--since", default="2022-01-01")
    f.add_argument("--only", default="", help="comma-separated tickers, e.g. NVDA,AMD")
    r = sub.add_parser("run", help="extract, match and decide")
    r.add_argument("--no-llm", action="store_true", help="rule layer only; low-confidence items become UNVERIFIABLE")
    r.add_argument("--threshold", type=float, default=None)
    sub.add_parser("label", help="write the blind labelling worksheet")
    e = sub.add_parser("eval", help="score against your labels and calibrate the threshold")
    e.add_argument("--target-precision", type=float, default=0.95)
    sub.add_parser("export", help="copy results and eval into site/data for the demo")
    a = p.parse_args()

    if a.cmd == "fetch":
        from .edgar import fetch_all
        fetch_all(a.since, [t.strip().upper() for t in a.only.split(",") if t.strip()] or None)
    elif a.cmd == "run":
        from .gate import THRESHOLD
        from .pipeline import run
        run(use_llm=not a.no_llm, threshold=a.threshold if a.threshold is not None else THRESHOLD)
    elif a.cmd == "label":
        from .evaluate import write_worksheet
        print(f"worksheet -> {write_worksheet()}")
    elif a.cmd == "eval":
        from .evaluate import evaluate
        print(json.dumps(evaluate(target_precision=a.target_precision), indent=1))
    elif a.cmd == "export":
        dst = ROOT / "site" / "data"
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("results.json", "eval.json"):
            src = ROOT / "data" / "processed" / name
            if src.exists():
                shutil.copy(src, dst / name)
                print(f"copied {name}")
            else:
                print(f"missing {name}, skipped")


if __name__ == "__main__":
    main()
