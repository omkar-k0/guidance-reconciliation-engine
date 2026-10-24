"""Typed records that flow through the pipeline.

Every decision carries a label, a confidence in [0, 1], the route that produced it
(rule or llm), and citations: exact quotes from the source text that back the label.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Label(str, Enum):
    MET = "MET"
    MISSED = "MISSED"
    RESTATED = "RESTATED"
    UNVERIFIABLE = "UNVERIFIABLE"


class Route(str, Enum):
    RULE = "rule"
    LLM = "llm"


@dataclass
class Citation:
    source: str  # "guidance" or "actual"
    quote: str   # exact text span from the source
    url: str = ""


@dataclass
class GuidanceItem:
    item_id: str
    ticker: str
    cik: str
    filed: str            # filing date of the 8-K that carried the guidance, YYYY-MM-DD
    accession: str
    url: str
    text: str             # the sentence(s) containing the guidance
    metric: str = "revenue"
    low: float | None = None    # USD
    high: float | None = None   # USD
    period_hint: str = ""       # e.g. "third quarter of fiscal 2026", if stated
    parse_confidence: float = 0.0
    parse_notes: list[str] = field(default_factory=list)


@dataclass
class Actual:
    value: float | None         # USD
    period_start: str = ""
    period_end: str = ""
    filed: str = ""
    form: str = ""
    accession: str = ""
    concept: str = ""
    restated_from: float | None = None  # earlier filed value for the same period, if different
    match_confidence: float = 0.0
    match_notes: list[str] = field(default_factory=list)


@dataclass
class Decision:
    item_id: str
    label: Label
    confidence: float
    route: Route
    reason: str
    citations: list[Citation] = field(default_factory=list)
    rule_label: Label | None = None
    rule_confidence: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label.value
        d["route"] = self.route.value
        d["rule_label"] = self.rule_label.value if self.rule_label else None
        return d
