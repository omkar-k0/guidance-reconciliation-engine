"""Unit tests on synthetic inputs. These test logic only; they are not results."""
from gre import extract, rules
from gre.actuals import find_actual
from gre.gate import route
from gre.llm import verify_citations
from gre.schema import Actual, GuidanceItem, Label, Route


def item(text, low, high, conf=1.0, notes=None):
    return GuidanceItem("X-1", "XYZ", "1", "2025-02-01", "0000000000-25-000001", "u", text,
                        low=low, high=high, parse_confidence=conf, parse_notes=notes or [])


# ---------- extraction ----------

def test_plus_minus_percent():
    s = "For the second quarter of fiscal 2026, revenue is expected to be $10.0 billion, plus or minus 2%."
    [g] = extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u")
    assert round(g.low / 1e9, 2) == 9.8 and round(g.high / 1e9, 2) == 10.2
    assert g.parse_confidence == 1.0
    assert "second quarter of fiscal 2026" in g.period_hint


def test_range_with_unit_on_second_number():
    s = "The company expects revenue for the third quarter to be in the range of $5.1 to $5.4 billion."
    [g] = extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u")
    assert g.low == 5.1e9 and g.high == 5.4e9


def test_plus_minus_absolute():
    s = "In the fourth quarter, we expect revenue to be approximately $7.5 billion, plus or minus $300 million."
    [g] = extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u")
    assert g.low == 7.2e9 and g.high == 7.8e9


def test_full_year_guidance_out_of_scope():
    s = "Cisco estimates the following results for fiscal 2026:\nFY 2026\nRevenue\n$60.2 billion - $61.0 billion"
    assert extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u") == []


def test_segment_row_skipped_total_row_kept():
    s = ("Current Guidance\nQ2 FY24 Estimates\nQCT revenues $7.6B - $8.2B\n")
    assert extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u") == []
    s = ("Current Guidance\nQ2 FY24 Estimates\nRevenues $8.9B - $9.7B\nQCT revenues $7.6B - $8.2B")
    [g] = extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u")
    assert (g.low, g.high) == (8.9e9, 9.7e9) and g.parse_confidence == 1.0


def test_table_in_millions():
    s = ("Business Outlook\nApplied's total revenue for the third quarter of fiscal 2026 are expected to be as follows:\n"
         "Q3 FY2026\n(In millions, except per share amounts)\nTotal revenue $ 8,950 +/- $ 500")
    [g] = extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u")
    assert (g.low, g.high) == (8.45e9, 9.45e9)


def test_slide_style_and_past_result_ignored():
    s = "Revenue of $5.8 billion increased 9% y/y\nQ3’24 Revenue ~$6.7 Billion, +/- $300 Million Gross Margin ~53.5%"
    [g] = extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u")
    assert (g.low, g.high) == (6.4e9, 7.0e9)


def test_preliminary_release_skipped():
    s = "NVIDIA Announces Preliminary Financial Results\nRevenue is expected to be $8.1 billion, plus or minus 2%."
    assert extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u") == []


def test_backward_looking_sentence_ignored():
    s = "Revenue for the quarter was $9.9 billion, up 12% year over year."
    assert extract.extract(s, "XYZ", "1", "2025-02-01", "acc-000001", "u") == []


# ---------- actuals ----------

def facts(entries):
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


def f(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form, "accn": "a"}


def test_next_quarter_matched():
    a = find_actual(facts([f("2025-01-01", "2025-03-31", 10.1e9, "2025-05-01")]), "2025-02-01")
    assert a.value == 10.1e9 and a.match_confidence == 1.0 and a.restated_from is None


def test_restatement_detected():
    a = find_actual(facts([f("2025-01-01", "2025-03-31", 10.1e9, "2025-05-01"),
                           f("2025-01-01", "2025-03-31", 9.7e9, "2026-05-01")]), "2025-02-01")
    assert a.restated_from == 10.1e9 and a.value == 9.7e9


def test_q4_derived_from_full_year():
    a = find_actual(facts([
        f("2024-01-01", "2024-12-31", 40e9, "2025-02-20", "10-K"),
        f("2024-01-01", "2024-09-30", 29e9, "2024-11-01"),
        f("2024-07-01", "2024-09-30", 10e9, "2024-11-01"),
    ]), "2024-11-05")
    assert a.value == 11e9
    assert a.match_confidence < 1.0


def test_no_actual():
    a = find_actual({"facts": {}}, "2025-02-01")
    assert a.value is None


# ---------- rules and gate ----------

def test_met_and_missed():
    a = Actual(1.0e10, "2025-01-01", "2025-03-31", match_confidence=1.0, concept="Revenues")
    assert rules.decide(item("t", 9.8e9, 10.2e9), a).label == Label.MET
    assert rules.decide(item("t", 10.5e9, 10.9e9), a).label == Label.MISSED


def test_borderline_lowers_confidence():
    a = Actual(9.81e9, "2025-01-01", "2025-03-31", match_confidence=1.0, concept="Revenues")
    d = rules.decide(item("t", 9.8e9, 10.2e9), a)
    assert d.label == Label.MET and d.confidence <= 0.8


def test_gate_keeps_confident_rule_decision():
    a = Actual(1.0e10, "2025-01-01", "2025-03-31", match_confidence=1.0, concept="Revenues")
    d = route(item("t", 9.8e9, 10.2e9), a, "t", use_llm=False)
    assert d.route == Route.RULE and d.label == Label.MET


def test_gate_withholds_when_unsure_and_no_llm():
    a = Actual(1.0e10, "2025-01-01", "2025-03-31", match_confidence=1.0, concept="Revenues")
    d = route(item("t", 9.8e9, 10.2e9, conf=0.4), a, "t", use_llm=False)
    assert d.label == Label.UNVERIFIABLE and d.rule_label == Label.MET


class FakeClient:
    """Stands in for the Anthropic client; returns a fixed tool call."""
    def __init__(self, payload):
        self.payload = payload
        self.messages = self

    def create(self, **kw):
        class B:  # tool_use block
            type = "tool_use"
            input = self.payload
        class U:
            input_tokens, output_tokens = 10, 10
        class M:
            content = [B()]
            usage = U()
        return M()


def test_llm_without_valid_quote_is_withheld(tmp_path, monkeypatch):
    import gre.llm as L
    monkeypatch.setattr(L, "CACHE", tmp_path)
    g = item("We expect total revenue of $9.8 to $10.2 billion.", 9.8e9, 10.2e9, conf=0.4)
    a = Actual(1.0e10, "2025-01-01", "2025-03-31", match_confidence=1.0, concept="Revenues")
    fake = FakeClient({"label": "MET", "confidence": 0.9, "reason": "r",
                       "citations": [{"source": "guidance", "quote": "an invented quote"}]})
    d = route(g, a, g.text, use_llm=True, client=fake)
    assert d.route == Route.LLM and d.label == Label.UNVERIFIABLE and d.confidence == 0.0


def test_llm_with_valid_quote_kept(tmp_path, monkeypatch):
    import gre.llm as L
    monkeypatch.setattr(L, "CACHE", tmp_path)
    g = item("We expect total revenue of $9.8 to $10.2 billion.", 9.8e9, 10.2e9, conf=0.4)
    a = Actual(1.0e10, "2025-01-01", "2025-03-31", match_confidence=1.0, concept="Revenues")
    fake = FakeClient({"label": "MET", "confidence": 0.8, "reason": "r",
                       "citations": [{"source": "guidance", "quote": "total revenue of $9.8 to $10.2 billion"}]})
    d = route(g, a, g.text, use_llm=True, client=fake)
    assert d.label == Label.MET and len(d.citations) == 1


def test_verify_citations_whitespace_insensitive():
    ok = verify_citations([{"source": "guidance", "quote": "revenue   of $5"}], "Expected revenue of $5 billion", "", "u")
    assert len(ok) == 1


# ---------- exhibit picking ----------

def test_pick_exhibit_named_and_unnamed():
    from gre.edgar import pick_exhibit
    items = [{"name": "d8k.htm", "size": "5000"}, {"name": "dex991.htm", "size": "90000"},
             {"name": "0001-index.htm", "size": "1"}]
    assert pick_exhibit(items, "d8k.htm") == "dex991.htm"
    items = [{"name": "nvda-20250226.htm", "size": "30000"}, {"name": "q4fy25pr.htm", "size": "250000"},
             {"name": "q4fy25cfocommentary.htm", "size": "120000"}, {"name": "R1.htm", "size": "999999"}]
    assert pick_exhibit(items, "nvda-20250226.htm") == "q4fy25pr.htm"
    assert pick_exhibit([{"name": "a.htm", "size": "1"}], "a.htm") is None


def test_llm_failure_is_contained(tmp_path, monkeypatch):
    import gre.llm as L
    monkeypatch.setattr(L, "CACHE", tmp_path)

    class Boom:
        messages = None
        def __init__(self):
            self.messages = self
        def create(self, **kw):
            raise RuntimeError("401 invalid x-api-key")
    g = item("We expect total revenue of $9.8 to $10.2 billion.", 9.8e9, 10.2e9, conf=0.4)
    a = Actual(1.0e10, "2025-01-01", "2025-03-31", match_confidence=1.0, concept="Revenues")
    d = route(g, a, g.text, use_llm=True, client=Boom())
    assert d.label == Label.UNVERIFIABLE and "LLM call failed" in d.reason and d.rule_label == Label.MET
