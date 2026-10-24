"""Find the reported revenue for the quarter the guidance pointed at.

Source: SEC XBRL company facts. Companies switch revenue tags over the years
(e.g. Revenues -> RevenueFromContractWithCustomer...), so facts from all revenue tags are
merged per period. Guidance in a release filed on date T targets the next fiscal quarter,
so the actual is the first ~3-month period ending after T. Q4 is often reported only as a
full year; it is then derived as full year minus the 9-month year-to-date figure.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .schema import Actual

CONCEPTS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]
MAX_GAP_DAYS = 130


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def all_facts(companyfacts: dict) -> list[dict]:
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    out = []
    for c in CONCEPTS:
        for f in gaap.get(c, {}).get("units", {}).get("USD", []):
            if f.get("start") and f.get("end"):
                out.append({**f, "concept": c})
    return out


def by_period(facts: list[dict], lo: int, hi: int) -> dict[tuple[str, str], list[dict]]:
    """Group facts whose duration is within [lo, hi] days by (start, end), oldest filing first."""
    g: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in facts:
        if lo <= _days(f["start"], f["end"]) <= hi:
            g[(f["start"], f["end"])].append(f)
    for k in g:
        g[k].sort(key=lambda f: (f["filed"], f.get("fy") or 0))
    return g


def find_actual(companyfacts: dict, guidance_filed: str) -> Actual:
    facts = all_facts(companyfacts)
    if not facts:
        return Actual(value=None, match_notes=["no revenue facts in XBRL"])

    quarters = by_period(facts, 80, 100)
    ahead = sorted((k for k in quarters if k[1] > guidance_filed), key=lambda k: k[1])
    q = None
    if ahead and _days(guidance_filed, ahead[0][1]) <= MAX_GAP_DAYS:
        q = _from_facts(quarters[ahead[0]], notes=[], conf=1.0)
    q4 = _derive_q4(facts, guidance_filed)
    # Q4 is often only reported inside the full year; take whichever period ends first.
    if q4 is not None and (q is None or q4.period_end < q.period_end):
        return q4
    if q is not None:
        return q
    return Actual(value=None, match_notes=[f"no reported quarter ends within {MAX_GAP_DAYS} days of the guidance"])


def _from_facts(fs: list[dict], notes: list[str], conf: float) -> Actual:
    first, last = fs[0], fs[-1]
    vals = {round(f["val"]) for f in fs}
    a = Actual(value=float(first["val"]), period_start=first["start"], period_end=first["end"],
               filed=first["filed"], form=first.get("form", ""), accession=first.get("accn", ""),
               concept=first["concept"], match_confidence=conf, match_notes=list(notes))
    if abs(last["val"] - first["val"]) > 0.001 * abs(first["val"]):
        a.restated_from = float(first["val"])
        a.value = float(last["val"])
        a.match_notes.append("value changed in a later filing")
    elif len(vals) > 1:
        a.match_notes.append("tiny differences between filings ignored")
    return a


def _derive_q4(facts: list[dict], after: str) -> Actual | None:
    years = by_period(facts, 350, 380)
    nines = by_period(facts, 260, 285)
    fy = sorted((k for k in years if k[1] > after and _days(after, k[1]) <= MAX_GAP_DAYS), key=lambda k: k[1])
    if not fy:
        return None
    y_key = fy[0]
    n_key = next((k for k in nines if k[0] == y_key[0]), None)
    if n_key is None:
        return None
    y, n = years[y_key][0], nines[n_key][0]  # as first reported
    val = float(y["val"] - n["val"])
    a = Actual(value=val, period_start=n_key[1], period_end=y_key[1], filed=y["filed"], form=y.get("form", ""),
               accession=y.get("accn", ""), concept=y["concept"], match_confidence=0.8,
               match_notes=["quarter derived as full year minus 9-month year to date"])
    y_last, n_last = years[y_key][-1], nines[n_key][-1]
    later = float(y_last["val"] - n_last["val"])
    if abs(later - val) > 0.001 * abs(val):
        a.restated_from, a.value = val, later
        a.match_notes.append("value changed in a later filing")
    return a
