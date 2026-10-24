"""Glue: raw files -> guidance items -> actuals -> gated decisions -> JSON."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from . import extract
from .actuals import find_actual
from .edgar import RAW, load_companies
from .gate import THRESHOLD, route

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed"


def context_for(text: str, quote: str, chars: int = 500) -> str:
    """The guidance quote plus surrounding text, whitespace-normalised, for the LLM."""
    flat = re.sub(r"\s+", " ", " ".join(extract.lines_of(text)))
    i = flat.find(quote)
    if i < 0:
        return quote
    return flat[max(0, i - chars): i + len(quote) + chars]


def build_items(stats: Counter | None = None) -> list[dict]:
    stats = stats if stats is not None else Counter()
    rows = []
    for c in load_companies():
        d = RAW / c["ticker"]
        if not (d / "releases.json").exists():
            print(f"skip {c['ticker']}: not fetched")
            continue
        facts = json.loads((d / "companyfacts.json").read_text())
        for rel in json.loads((d / "releases.json").read_text()):
            path = d / rel["file"]
            if not path.exists():
                stats["missing release file"] += 1
                continue
            text = path.read_text()
            for g in extract.extract(text, c["ticker"], c["cik"], rel["filed"], rel["accession"], rel["url"], stats):
                a = find_actual(facts, g.filed)
                rows.append({"guidance": g, "actual": a, "context": context_for(text, g.text)})
    return rows


def run(use_llm: bool = True, threshold: float = THRESHOLD) -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    stats: Counter = Counter()
    for r in build_items(stats):
        d = route(r["guidance"], r["actual"], r["context"], use_llm=use_llm, threshold=threshold)
        results.append({
            "guidance": asdict(r["guidance"]),
            "actual": asdict(r["actual"]),
            "decision": d.to_dict(),
        })
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    print(f"{len(results)} decisions -> {OUT / 'results.json'}")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    return results
