"""Eval harness on a tiny synthetic run (logic check only)."""
import csv
import json

from gre import evaluate as E


def test_worksheet_and_eval(tmp_path):
    results = []
    for i, (lab, rl, rc, route) in enumerate([
        ("MET", "MET", 0.95, "rule"), ("MISSED", "MISSED", 0.9, "rule"),
        ("MET", "MET", 0.5, "llm"), ("UNVERIFIABLE", "MET", 0.3, "llm"),
    ]):
        results.append({
            "guidance": {"item_id": f"i{i}", "ticker": "XYZ", "filed": "2025-01-01", "text": "t",
                         "low": 1, "high": 2, "url": "u"},
            "actual": {"value": 1.5, "period_start": "a", "period_end": "b"},
            "decision": {"label": lab, "route": route, "confidence": rc,
                         "rule_label": rl if route == "llm" else None,
                         "rule_confidence": rc if route == "llm" else None},
        })
    rp = tmp_path / "results.json"
    rp.write_text(json.dumps(results))
    ws = E.write_worksheet(rp, tmp_path / "ws.csv")
    rows = list(csv.DictReader(open(ws)))
    assert "gold_label" in rows[0] and "system_label" not in rows[0]  # blind
    gold = ["MET", "MISSED", "MET", "MISSED"]
    with open(ws, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=E.FIELDS)
        w.writeheader()
        for r, g in zip(rows, gold):
            r["gold_label"] = g
            w.writerow(r)
    E.ROOT = tmp_path
    (tmp_path / "data" / "processed").mkdir(parents=True)
    rep = E.evaluate(rp, ws)
    assert rep["n_labelled"] == 4
    assert rep["accuracy"] == 0.75
    assert rep["escalation_rate"] == 0.5
