"""Confidence gate: the rule layer decides when it is sure; the rest goes to the LLM."""
from __future__ import annotations

import os

from . import llm, rules
from .schema import Actual, Decision, GuidanceItem, Label, Route

THRESHOLD = float(os.environ.get("GRE_THRESHOLD", "0.75"))


def route(g: GuidanceItem, a: Actual, context: str, use_llm: bool = True,
          threshold: float = THRESHOLD, client=None) -> Decision:
    r = rules.decide(g, a)
    if r.confidence >= threshold:
        return r
    notes = g.parse_notes + a.match_notes
    if not use_llm:
        d = Decision(g.item_id, Label.UNVERIFIABLE, 0.0, Route.RULE,
                     f"Below threshold ({r.confidence:.2f} < {threshold:.2f}) and LLM disabled. Rule said {r.label.value}.",
                     r.citations)
    else:
        try:
            d = llm.adjudicate(g, context, a, notes, client=client)
        except Exception as e:  # network, auth or model errors must not kill the run
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            if not getattr(route, "_warned", False):
                print(f"LLM call failed, items will be marked UNVERIFIABLE. First error:\n  {msg}")
                route._warned = True
            d = Decision(g.item_id, Label.UNVERIFIABLE, 0.0, Route.LLM, f"LLM call failed ({msg}). Rule said {r.label.value}.",
                         r.citations)
    d.rule_label, d.rule_confidence = r.label, r.confidence
    return d
