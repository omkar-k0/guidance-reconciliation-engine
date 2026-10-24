"""Find quarterly total-revenue guidance in an earnings release and parse it into a range.

Approach (v2, built from how the 12 companies actually write their releases):
1. Work line by line. Find "outlook anchors": lines that open a guidance block
   ("Business Outlook", "Financial Targets", "estimates the following results",
   "revenue is expected", "quarter ... revenue guidance", ...).
2. In the anchor line and the few lines after it, find a TOTAL revenue label
   (segment rows such as "QCT revenues" or "Data Center revenue" are skipped) and parse
   the dollar range that follows it.
3. Decide the period from the anchor text: quarterly is in scope; full-year is
   dropped and counted; unnamed lowers confidence.
4. Preliminary-results releases are skipped: they report a closed quarter.

Every confidence deduction is written to parse_notes, so the reason an item went to
the LLM is always visible.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

from .schema import GuidanceItem

SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "m": 1e6, "mm": 1e6, "b": 1e9, "bn": 1e9}
UNIT = r"(thousand|million|billion|bn|mm|m|b)?\b"
NUM = r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*" + UNIT
NUM_OPT = r"\$?\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*" + UNIT

ANCHOR = re.compile(
    r"business outlook|financial targets|following (results|targets|guidance)|"
    r"guidance for the|estimates the following|expects to achieve|current guidance|"
    r"revenue (is|are) expected|expects? (net |total )?revenues?|revenues? (outlook|guidance|target)|"
    r"(quarter|Q[1-4]|FQ[1-4]).{0,60}(outlook|guidance|targets?|estimates)|"
    r"outlook (is|for)|expected to be in the range|"
    r"\bQ[1-4]\s*[’']\s?\d{2}\s+revenue\s*~?\s*\$",  # slide style: Q3’24 Revenue ~$6.7 Billion
    re.I,
)
REV_LABEL = re.compile(r"\b(total revenues?|net revenues?|revenues?|net sales)\b", re.I)
SEGMENT_BEFORE = re.compile(
    r"(segment|data center|subscription|support|cloud|gaming|client|division|QCT|QTL|"
    r"digital (media|experience)|professional services|automotive|IoT|handsets?|"
    r"H20|product|services?|semiconductor systems|AGS|display|license)\W{0,3}$",
    re.I,
)
FORWARD = re.compile(r"expect|outlook|guidance|target|estimate|anticipat|forecast|project", re.I)
QUARTERLY = re.compile(r"\bquarter\b|\bQ[1-4]\b|\bFQ[1-4]\b|\bQ[1-4]\s*(FY|’|')", re.I)
ANNUAL = re.compile(r"full[- ]year|fiscal (year )?20\d\d(?!\s*(first|second|third|fourth))|\bFY\s?'?\d{2,4}\b|annual|for the year", re.I)
PERIOD_TEXT = re.compile(
    r"((first|second|third|fourth)\s+(fiscal\s+)?quarter(\s+of)?(\s+fiscal)?(\s+(year\s+)?\d{4})?|"
    r"Q[1-4]\s*(FY)?\s*'?’?\d{2,4}|FQ[1-4][-\s]?\d{2,4})",
    re.I,
)
PRELIMINARY = re.compile(r"preliminary", re.I)
PAST = re.compile(r"\b(was|were|achieved|reported|grew|increased|decreased|declined|delivered|record|up \d|down \d)\b", re.I)
IN_MILLIONS = re.compile(r"in millions", re.I)
LEAD_CHARS = 140  # text just before the revenue label used to decide the period
ADJUST = re.compile(r"constant currency|excluding|organic|pro forma", re.I)

PLUS_MINUS_PCT = re.compile(NUM + r",?\s*(?:plus or minus|\+/-|±)\s*(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
PLUS_MINUS_ABS = re.compile(NUM + r",?\s*(?:plus or minus|\+/-|±)\s*" + NUM_OPT, re.I)
RANGE_TO = re.compile(NUM + r"\s*(?:to|-|–|—)\s*" + NUM_OPT, re.I)
POINT = re.compile(NUM, re.I)

WINDOW_LINES = 8     # lines after an anchor to search for the revenue row
AFTER_LABEL = 110    # characters after the revenue label to search for the amount
POINT_TOL = 0.01     # a point estimate ("approximately $X") counts as met from X * (1 - 1%)


def _amount(num: str, unit: str | None, fallback_unit: str | None = None) -> float:
    v = float(num.replace(",", ""))
    u = (unit or fallback_unit or "").lower()
    return v * SCALE.get(u, 1.0)


def parse_range(s: str, default_unit: str | None = None) -> tuple[float | None, float | None, list[str], float]:
    """Parse the first money range in s. Returns (low, high, notes, confidence deduction).

    default_unit applies to bare numbers, e.g. a table headed "(In millions)".
    """
    cands = []
    for pat, kind in ((PLUS_MINUS_PCT, "pct"), (PLUS_MINUS_ABS, "abs"), (RANGE_TO, "range"), (POINT, "point")):
        m = pat.search(s)
        if m:
            cands.append((m.start(), -len(m.group(0)), kind, m))
    if not cands:
        return None, None, ["no dollar amount parsed"], 1.0
    _, _, kind, m = sorted(cands)[0]  # earliest match; on ties the longest
    if kind == "pct":
        mid = _amount(m.group(1), m.group(2), default_unit)
        pct = float(m.group(3)) / 100
        return mid * (1 - pct), mid * (1 + pct), ["midpoint plus or minus percent"], 0.0
    if kind == "abs":
        mid = _amount(m.group(1), m.group(2), default_unit)
        band = _amount(m.group(3), m.group(4), m.group(2) or default_unit)
        return mid - band, mid + band, ["midpoint plus or minus amount"], 0.0
    if kind == "range":
        lo = _amount(m.group(1), m.group(2) or m.group(4), default_unit)
        hi = _amount(m.group(3), m.group(4), m.group(2) or default_unit)
        if lo > hi:
            return hi, lo, ["explicit range (reversed)"], 0.1
        return lo, hi, ["explicit range"], 0.0
    v = _amount(m.group(1), m.group(2), default_unit)
    return v * (1 - POINT_TOL), v, [f"point estimate, met from {100 - POINT_TOL * 100:.0f}% of it"], 0.15


def item_id(ticker: str, accession: str, low: float, high: float) -> str:
    h = hashlib.sha1(f"{ticker}|{accession}|{low:.0f}|{high:.0f}".encode()).hexdigest()[:8]
    return f"{ticker}-{accession[-6:]}-{h}"


def lines_of(text: str) -> list[str]:
    out = []
    for ln in text.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip(" •·\t")
        if ln:
            out.append(ln)
    return out


def split_sentences(text: str) -> list[str]:  # kept for the pipeline's context helper
    return lines_of(text)


def find_revenue_amount(block: str) -> tuple[int, str] | None:
    """Return (label position, text after the label) for the first TOTAL revenue label with money after it."""
    for m in REV_LABEL.finditer(block):
        before = block[max(0, m.start() - 30): m.start()]
        if SEGMENT_BEFORE.search(before) and not re.search(r"total\s*$", before, re.I):
            continue
        after = block[m.end(): m.end() + AFTER_LABEL]
        # stop at the next metric label so we don't read gross margin or EPS amounts
        cut = re.search(r"\b(gross margin|EPS|earnings|operating|tax|share|ARR|EBITDA|margin)\b", after, re.I)
        if cut:
            after = after[: cut.start()]
        if "$" in after:
            return m.start(), after
    return None


def extract(text: str, ticker: str, cik: str, filed: str, accession: str, url: str,
            stats: Counter | None = None) -> list[GuidanceItem]:
    stats = stats if stats is not None else Counter()
    head = text[:800]
    if PRELIMINARY.search(head):
        stats["skipped: preliminary-results release"] += 1
        return []

    lines = lines_of(text)
    found: dict[tuple[int, int], GuidanceItem] = {}
    for i, ln in enumerate(lines):
        if not ANCHOR.search(ln):
            continue
        block = " ".join(lines[i: i + WINDOW_LINES])
        hit = find_revenue_amount(block)
        if not hit:
            continue
        pos, after = hit
        default_unit = "million" if IN_MILLIONS.search(block[: pos + len(after) + 5]) else None
        low, high, notes, deduct = parse_range(after, default_unit)
        if low is None:
            continue
        if low < 1e8:
            stats["skipped: amount too small for total revenue"] += 1
            continue

        lead = block[max(0, pos - LEAD_CHARS): pos]  # text just before the label decides the period
        amt = re.search(r"\$", after)
        near = block[max(0, pos - 45): pos + (amt.start() if amt else 0) + 45]
        is_point = notes[0].startswith("point")
        if is_point and not FORWARD.search(block[max(0, pos - 45): pos + (amt.start() if amt else 0)]):
            stats["skipped: point figure with no guidance word (likely a reported result)"] += 1
            continue
        if PAST.search(near) and not FORWARD.search(near):
            stats["skipped: past-tense result, not guidance"] += 1
            continue
        quarterly, annual = QUARTERLY.search(lead), ANNUAL.search(lead)
        if annual and not quarterly:
            stats["skipped: full-year guidance (out of scope)"] += 1
            continue
        conf = 1.0 - deduct
        if not quarterly:
            conf -= 0.15
            notes.append("period not named, assumed next quarter")
        if ADJUST.search(after):
            conf -= 0.25
            notes.append("adjusted basis (currency or exclusions)")
        if not FORWARD.search(block[: pos + 40]):
            conf -= 0.3
            notes.append("no forward-looking word near the figure")

        key = (round(low / 1e6), round(high / 1e6))
        if key in found:
            continue  # same range repeated (headline bullet + outlook section, GAAP + non-GAAP columns)
        pm = PERIOD_TEXT.search(lead)
        quote = block[max(0, pos - 160): pos + len(after) + 20].strip()
        found[key] = GuidanceItem(
            item_id=item_id(ticker, accession, low, high), ticker=ticker, cik=cik, filed=filed,
            accession=accession, url=url, text=quote, low=low, high=high,
            period_hint=pm.group(0) if pm else "", parse_confidence=round(max(conf, 0.0), 3),
            parse_notes=notes,
        )
        stats["extracted"] += 1
    items = list(found.values())
    ranges = [g for g in items if g.high > g.low * (1 + POINT_TOL * 1.5)]
    kept = []
    for g in items:
        is_point = g.parse_notes and g.parse_notes[0].startswith("point")
        if is_point and any(r.low <= g.high <= r.high for r in ranges if r is not g):
            stats["dropped: headline point repeating the outlook range"] += 1
            continue
        kept.append(g)
    if not kept:
        stats["filings with no quarterly revenue guidance found"] += 1
    stats["extracted"] = stats.get("extracted", 0) - (len(items) - len(kept))
    return kept
