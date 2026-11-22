# Guidance Reconciliation Engine

**Did companies hit their own revenue guidance?** This engine reads forward-looking revenue guidance from SEC earnings releases, finds what was actually reported, and labels each item **MET**, **MISSED**, **RESTATED** or **UNVERIFIABLE**, with a confidence score and exact citations.

Live demo: **[guidance-reconciliation-engine.vercel.app](https://guidance-reconciliation-engine.vercel.app)** · Case study: **[omkarkhadilkar.dev/work/guidance-reconciliation-engine](https://omkarkhadilkar.dev/work/guidance-reconciliation-engine/)**

## The idea: confidence gating

Most cases are routine and a few transparent rules get them right. Only the ambiguous residue needs an LLM. So:

1. **Rules first.** A deterministic layer parses the range, matches the reported quarter and labels it. It also scores its own confidence and writes down every reason it lost some.
2. **Gate.** If rule confidence clears a calibrated threshold, the rule decision stands.
3. **LLM for the rest.** Below the threshold, Claude gets the filing context and returns a typed decision through a tool call.
4. **No quote, no label.** Every LLM citation must appear verbatim in the source. If none do, the decision becomes UNVERIFIABLE. The system says "I can't tell" instead of guessing.

```
8-K EX-99.1 ──► extract guidance ──► match XBRL actual ──► rule layer ──► confidence ≥ threshold? ──yes──► decision
                                                                               │
                                                                               no
                                                                               ▼
                                                              Claude (typed tool call) ──► quote check ──► decision
```

## Results

Run of 24 Oct 2026: 185 earnings releases from 11 US tech companies, January 2022 to October 2026. Scored on 20 Nov 2026 against 188 blind hand labels (`data/labels/worksheet.csv`, one labeller) with `gre eval` (`data/processed/eval.json`).

| Figure | Value | Evidence |
| --- | --- | --- |
| Guidance items processed | 188 | measured |
| Escalation rate to LLM | 3.7% (7 of 188) | measured |
| Accuracy vs hand labels | 97.9% (184 of 188) | measured |
| Baseline: label everything MET | 91.5% (172 of 188) | measured |
| Rule layer, confidence ≥ 0.95 | 100% (110 of 110) | measured |
| Rule layer, confidence 0.75–0.95 | 95.8% (68 of 71) | measured |
| LLM-decided items | 85.7% (6 of 7) | measured |
| Calibration error (ECE), rule layer | 0.062 → 0.009 after temperature scaling (T = 0.45) | measured |

Gold labels: 172 MET, 2 MISSED, 14 UNVERIFIABLE. On the 16 items that are not plain MET, the engine got 13 right.

The 4 errors:

- 3 extra items the extractor created from a second dollar figure in the same release (Cisco full-year revenue, Broadcom full-year AI revenue, a Broadcom buyback). The rules gave each a confident MET or MISSED at 0.80–0.85. Fix: one quarterly total-revenue item per release.
- 1 AMD item (October 2024) the LLM declined as UNVERIFIABLE because of a wrong period label from the extractor. The hand label is MET. A refusal, not a wrong answer, but it counts against accuracy.

Not yet applied: the calibrated threshold (0.711 on the scaled score for 95% precision). It needs a second labeller and more companies first.

Every figure is tagged **measured** (on hand-labelled items) or **pending**. Nothing here is simulated.

## Label definitions (v1)

| Label | Meaning |
| --- | --- |
| MET | Reported total revenue ≥ low end of the guided range. A beat counts as met. |
| MISSED | Reported total revenue < low end. |
| RESTATED | The quarter's revenue was changed in a later filing. |
| UNVERIFIABLE | No matching reported figure, or the guidance can't be pinned to total revenue for a specific quarter. |

## Scope

- Metric: total revenue only.
- Companies: 11 large-cap US tech firms that publish quarterly revenue guidance in their releases (`companies.csv`).
- Period: earnings releases since 2022.
- Out of scope in v1, and counted rather than guessed: full-year guidance, growth-% guidance without a dollar figure, segment or subscription-only guidance (why Workday was dropped), preliminary-results releases.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # add your SEC contact and Anthropic key
set -a && source .env && set +a

pytest                           # logic tests on synthetic inputs
gre fetch                        # download releases + XBRL facts from SEC (a few minutes)
gre run --no-llm                 # rule layer only, free
gre run                          # with Claude on the low-confidence residue
gre label                        # writes data/labels/worksheet.csv (blind: no system labels shown)
#   fill gold_label for each row by reading the filing
gre eval                         # accuracy, escalation rate, calibration, suggested threshold
gre export                       # copies results into site/data for the demo
```

## Repo layout

```
src/gre/
  edgar.py      SEC download (8-K Item 2.02 releases, XBRL company facts)
  extract.py    guidance sentence finder + range parser, confidence deductions
  actuals.py    next-quarter matching, Q4 derivation, restatement detection
  rules.py      deterministic labels
  gate.py       confidence gate
  llm.py        Claude adjudicator, typed tool output, quote verification, disk cache
  evaluate.py   blind labelling sheet, metrics, temperature scaling, threshold pick
site/           static demo (deployed on Vercel), reads site/data/*.json
tests/          unit tests on synthetic inputs
```

## Known limits

- "Next quarter" is assumed when a release doesn't name the period. Those items lose confidence and get escalated.
- Fiscal calendars differ; matching is by period end date after the filing date, not by fiscal label.
- Segment or full-year guidance is flagged, not handled. It usually ends up UNVERIFIABLE in v1.
- LLM confidence is not trusted raw. The gate threshold is calibrated on labelled data.

## Data

SEC EDGAR filings and XBRL data are public. Requests follow SEC's fair-access rules (contact User-Agent, under 10 requests/second). Raw data is not committed.
