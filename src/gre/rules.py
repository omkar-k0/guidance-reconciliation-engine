"""Deterministic rule layer. Handles the clean cases and says how sure it is.

Label definitions (v1, revenue only):
- MET:          reported revenue >= low end of the guided range (a beat counts as met)
- MISSED:       reported revenue <  low end of the guided range
- RESTATED:     the quarter's revenue was changed in a later filing
- UNVERIFIABLE: no matching reported figure, or the guidance cannot be pinned down
"""
from __future__ import annotations

from .schema import Actual, Citation, Decision, GuidanceItem, Label, Route

BORDER = 0.005  # within 0.5% of the low end counts as borderline


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"${v / 1e9:.3f}B" if abs(v) >= 1e9 else f"${v / 1e6:.1f}M"


def actual_quote(a: Actual) -> str:
    """The exact string shown to reviewers (and the LLM) for the reported figure."""
    if a.value is None:
        return ""
    return f"{a.concept} {a.period_start} to {a.period_end}: {a.value:.0f} USD ({a.form}, filed {a.filed})"


def decide(g: GuidanceItem, a: Actual) -> Decision:
    cites = [Citation("guidance", g.text, g.url)]
    if a.value is None:
        return Decision(g.item_id, Label.UNVERIFIABLE, round(0.9 * g.parse_confidence, 3), Route.RULE,
                        "No reported revenue matched the guided period. " + "; ".join(a.match_notes), cites)

    cites.append(Citation("actual", actual_quote(a)))
    base = min(g.parse_confidence, a.match_confidence)

    if a.restated_from is not None:
        return Decision(g.item_id, Label.RESTATED, round(base, 3), Route.RULE,
                        f"Reported {fmt_usd(a.restated_from)}, later changed to {fmt_usd(a.value)}.", cites)

    low = g.low if g.low is not None else 0.0
    gap = (a.value - low) / low if low else 0.0
    conf = base
    if abs(gap) < BORDER:
        conf -= 0.2
    label = Label.MET if a.value >= low else Label.MISSED
    reason = (f"Guided {fmt_usd(g.low)} to {fmt_usd(g.high)}, reported {fmt_usd(a.value)} "
              f"({gap:+.1%} vs low end).")
    return Decision(g.item_id, label, round(max(conf, 0.0), 3), Route.RULE, reason, cites)
