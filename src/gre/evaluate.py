"""Labelling worksheet, evaluation, and confidence calibration.

Workflow:
1. `gre label` writes data/labels/worksheet.csv from the latest run.
2. You fill the `gold_label` column by reading each filing (MET/MISSED/RESTATED/UNVERIFIABLE).
3. `gre eval` scores the system against your labels and calibrates the gate threshold.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "data" / "processed" / "results.json"
LABELS = ROOT / "data" / "labels"
WORKSHEET = LABELS / "worksheet.csv"
LABEL_SET = {"MET", "MISSED", "RESTATED", "UNVERIFIABLE"}

FIELDS = ["item_id", "ticker", "filed", "guidance_text", "low_usd", "high_usd", "actual_usd",
          "actual_period", "filing_url", "gold_label", "notes"]


def write_worksheet(results_path: Path = RESULTS, out: Path = WORKSHEET) -> Path:
    """Blind worksheet: the system's own label is NOT shown, so labels stay independent."""
    results = json.loads(results_path.read_text())
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if out.exists():
        with open(out, newline="") as f:
            existing = {r["item_id"]: r for r in csv.DictReader(f)}
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in results:
            g, a = r["guidance"], r["actual"]
            prev = existing.get(g["item_id"], {})
            w.writerow({
                "item_id": g["item_id"], "ticker": g["ticker"], "filed": g["filed"],
                "guidance_text": g["text"], "low_usd": g["low"], "high_usd": g["high"],
                "actual_usd": a["value"], "actual_period": f"{a['period_start']} to {a['period_end']}",
                "filing_url": g["url"], "gold_label": prev.get("gold_label", ""), "notes": prev.get("notes", ""),
            })
    return out


def load_gold(path: Path = WORKSHEET) -> dict[str, str]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    gold = {}
    for r in rows:
        lab = r["gold_label"].strip().upper()
        if lab:
            if lab not in LABEL_SET:
                raise SystemExit(f"bad label '{lab}' for {r['item_id']}")
            gold[r["item_id"]] = lab
    return gold


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def fit_temperature(conf: list[float], correct: list[int]) -> float:
    """Single-parameter temperature scaling on confidence logits, by grid search on NLL."""
    best_t, best = 1.0, float("inf")
    for i in range(1, 101):
        t = i / 20  # 0.05 .. 5.0
        nll = 0.0
        for c, y in zip(conf, correct):
            p = min(max(_sig(_logit(c) / t), 1e-6), 1 - 1e-6)
            nll -= y * math.log(p) + (1 - y) * math.log(1 - p)
        if nll < best:
            best_t, best = t, nll
    return best_t


def ece(conf: list[float], correct: list[int], bins: int = 10) -> float:
    n = len(conf)
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if idx:
            acc = sum(correct[i] for i in idx) / len(idx)
            avg = sum(conf[i] for i in idx) / len(idx)
            total += len(idx) / n * abs(acc - avg)
    return total


def evaluate(results_path: Path = RESULTS, gold_path: Path = WORKSHEET, target_precision: float = 0.95) -> dict:
    results = json.loads(results_path.read_text())
    gold = load_gold(gold_path)
    rows = [r for r in results if r["guidance"]["item_id"] in gold]
    if not rows:
        raise SystemExit("No labelled items yet. Fill gold_label in data/labels/worksheet.csv")

    y = [gold[r["guidance"]["item_id"]] for r in rows]
    pred = [r["decision"]["label"] for r in rows]
    routes = [r["decision"]["route"] for r in rows]
    correct = [int(p == t) for p, t in zip(pred, y)]

    # Rule-layer confidence for every item (what the gate sees), used to pick the threshold.
    rconf = [r["decision"]["rule_confidence"] if r["decision"]["rule_confidence"] is not None
             else r["decision"]["confidence"] for r in rows]
    rlabel = [r["decision"]["rule_label"] or r["decision"]["label"] for r in rows]
    rcorrect = [int(p == t) for p, t in zip(rlabel, y)]
    t = fit_temperature(rconf, rcorrect)
    scaled = [_sig(_logit(c) / t) for c in rconf]

    # Lowest threshold at which rule-routed items still reach target precision.
    best_thr = None
    for thr in sorted(set(round(s, 3) for s in scaled)):
        kept = [rc for s, rc in zip(scaled, rcorrect) if s >= thr]
        if kept and sum(kept) / len(kept) >= target_precision:
            best_thr = thr
            break

    by_route = {}
    for rt in ("rule", "llm"):
        idx = [i for i, x in enumerate(routes) if x == rt]
        if idx:
            by_route[rt] = {"n": len(idx), "accuracy": round(sum(correct[i] for i in idx) / len(idx), 3)}

    report = {
        "n_labelled": len(rows),
        "accuracy": round(sum(correct) / len(rows), 3),
        "by_route": by_route,
        "escalation_rate": round(routes.count("llm") / len(rows), 3),
        "gold_distribution": dict(Counter(y)),
        "confusion": dict(Counter(f"{t_}->{p}" for t_, p in zip(y, pred))),
        "rule_calibration": {
            "temperature": t,
            "ece_before": round(ece(rconf, rcorrect), 3),
            "ece_after": round(ece(scaled, rcorrect), 3),
            "suggested_threshold_scaled": best_thr,
            "target_precision": target_precision,
        },
    }
    out = ROOT / "data" / "processed" / "eval.json"
    out.write_text(json.dumps(report, indent=1))
    return report
