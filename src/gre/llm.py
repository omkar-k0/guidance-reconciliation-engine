"""LLM adjudicator for the ambiguous residue.

The model must return a typed decision through a tool call, and every citation must be
an exact quote from the text it was given. Quotes that are not found verbatim are
dropped; a decision left with no valid guidance citation becomes UNVERIFIABLE.
Responses are cached on disk so the demo and re-runs cost nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .rules import actual_quote
from .schema import Actual, Citation, Decision, GuidanceItem, Label, Route

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache" / "llm"
MODEL = os.environ.get("GRE_MODEL", "claude-haiku-5-5")

TOOL = {
    "name": "submit_decision",
    "description": "Submit the reconciliation decision for one guidance item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": [l.value for l in Label]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "description": "One or two plain sentences."},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "enum": ["guidance", "actual"]},
                        "quote": {"type": "string", "description": "Exact text copied from the source block."},
                    },
                    "required": ["source", "quote"],
                },
            },
        },
        "required": ["label", "confidence", "reason", "citations"],
    },
}

SYSTEM = """You reconcile company revenue guidance with reported results.
Labels: MET (reported total revenue >= low end of the guided range for the guided period),
MISSED (below the low end), RESTATED (the reported figure was later changed),
UNVERIFIABLE (the guidance is not total revenue, the period does not match, or the evidence is insufficient).
Rules: use only the text provided. Quote exactly; do not paraphrase inside quotes.
If you are not sure the guidance and the reported figure refer to the same metric and period, answer UNVERIFIABLE.
Never guess."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def build_prompt(g: GuidanceItem, context: str, a: Actual, rule_notes: list[str]) -> str:
    return (
        f"Company: {g.ticker}. Guidance filed {g.filed}.\n\n"
        f"<guidance_source>\n{context}\n</guidance_source>\n\n"
        f"Sentence flagged as guidance: \"{g.text}\"\n"
        f"Parsed range: low={g.low}, high={g.high} USD. Period hint: '{g.period_hint or 'none'}'.\n\n"
        f"<actual_source>\n{actual_quote(a) or 'NO MATCHING REPORTED FIGURE'}\n</actual_source>\n"
        f"Restated from: {a.restated_from}\n\n"
        f"Why the rule layer was unsure: {'; '.join(rule_notes) or 'n/a'}\n\n"
        "Call submit_decision."
    )


def verify_citations(raw: list[dict], context: str, actual_text: str, url: str) -> list[Citation]:
    ok = []
    for c in raw:
        src = context if c.get("source") == "guidance" else actual_text
        q = c.get("quote", "")
        if q and _norm(q) in _norm(src):
            ok.append(Citation(c["source"], q, url if c["source"] == "guidance" else ""))
    return ok


def adjudicate(g: GuidanceItem, context: str, a: Actual, rule_notes: list[str], client=None) -> Decision:
    prompt = build_prompt(g, context, a, rule_notes)
    key = hashlib.sha256((MODEL + SYSTEM + prompt).encode()).hexdigest()[:20]
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{key}.json"

    if path.exists():
        out = json.loads(path.read_text())
    else:
        if client is None:
            import anthropic  # imported lazily so the rule layer runs without it
            client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=600, system=SYSTEM,
            tools=[TOOL], tool_choice={"type": "tool", "name": "submit_decision"},
            messages=[{"role": "user", "content": prompt}],
        )
        block = next(b for b in msg.content if getattr(b, "type", "") == "tool_use")
        out = dict(block.input)
        out["_usage"] = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens, "model": MODEL}
        path.write_text(json.dumps(out, indent=1))

    cites = verify_citations(out.get("citations", []), context, actual_quote(a), g.url)
    label = Label(out["label"])
    conf = float(out["confidence"])
    reason = out["reason"]
    if label != Label.UNVERIFIABLE and not any(c.source == "guidance" for c in cites):
        label, conf = Label.UNVERIFIABLE, 0.0
        reason = "Model gave no exact quote from the guidance, so the decision is withheld. " + reason
    return Decision(g.item_id, label, round(conf, 3), Route.LLM, reason, cites)
