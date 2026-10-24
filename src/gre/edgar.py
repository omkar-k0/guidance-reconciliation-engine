"""Download what the pipeline needs from SEC EDGAR.

Run this on your own machine. SEC requires a User-Agent with a contact email and
allows at most 10 requests per second; we stay well under that.

What it fetches per company:
- submissions JSON, to list 8-K filings
- the earnings press release exhibit (EX-99.1) of each 8-K with Item 2.02
- XBRL company facts, for reported revenue (the "actual")
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from html import unescape
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
SLEEP = 0.25


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        raise SystemExit(
            "Set SEC_USER_AGENT to 'Your Name your@email.com' (SEC requires a contact)."
        )
    return ua


def _get(url: str, session: requests.Session) -> requests.Response:
    time.sleep(SLEEP)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r


def load_companies(path: Path | None = None) -> list[dict]:
    path = path or ROOT / "companies.csv"
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def fetch_company(c: dict, since: str, session: requests.Session) -> dict:
    cik10 = c["cik"].zfill(10)
    out = RAW / c["ticker"]
    out.mkdir(parents=True, exist_ok=True)

    sub = _get(f"https://data.sec.gov/submissions/CIK{cik10}.json", session).json()
    name = sub.get("name", "")
    if c.get("expected_name") and c["expected_name"].lower() not in name.lower():
        raise SystemExit(f"CIK check failed for {c['ticker']}: SEC says '{name}'")
    (out / "submissions.json").write_text(json.dumps(sub))

    facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json", session).json()
    (out / "companyfacts.json").write_text(json.dumps(facts))

    filings = _all_filings(sub, cik10, since, session)
    index, skipped = [], {"no_index": 0, "no_exhibit": 0, "no_guidance_words": 0}
    for f in filings:
        acc = f["accessionNumber"]
        acc_nodash = acc.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(c['cik'])}/{acc_nodash}"
        try:
            idx = _get(f"{base}/index.json", session).json()
        except requests.HTTPError:
            skipped["no_index"] += 1
            continue
        doc = pick_exhibit(idx["directory"]["item"], f.get("primaryDocument", ""))
        if not doc:
            skipped["no_exhibit"] += 1
            continue
        text = html_to_text(_get(f"{base}/{doc}", session).text)
        if not re.search(r"revenue|net sales", text, re.I):
            skipped["no_guidance_words"] += 1
            continue
        fname = f"{f['filingDate']}_{acc_nodash}.txt"
        (out / fname).write_text(text)
        index.append({"file": fname, "filed": f["filingDate"], "accession": acc, "url": f"{base}/{doc}"})
    (out / "releases.json").write_text(json.dumps(index, indent=1))
    return {"ticker": c["ticker"], "sec_name": name, "8k_2.02": len(filings), "releases": len(index), "skipped": skipped}


def _rows(block: dict) -> list[dict]:
    keys = ["accessionNumber", "filingDate", "form", "items", "primaryDocument"]
    n = len(block.get("form", []))
    return [{k: (block.get(k) or [""] * n)[i] for k in keys} for i in range(n)]


def _all_filings(sub: dict, cik10: str, since: str, session: requests.Session) -> list[dict]:
    """8-K filings with Item 2.02 since `since`, including older pages beyond 'recent'."""
    rows = _rows(sub["filings"]["recent"])
    for extra in sub["filings"].get("files", []):
        if extra.get("filingTo", "9999") < since:
            continue
        rows += _rows(_get(f"https://data.sec.gov/submissions/{extra['name']}", session).json())
    seen, out = set(), []
    for r in rows:
        if r["form"] != "8-K" or r["filingDate"] < since or "2.02" not in (r["items"] or ""):
            continue
        if r["accessionNumber"] in seen:
            continue
        seen.add(r["accessionNumber"])
        out.append(r)
    return sorted(out, key=lambda r: r["filingDate"])


def pick_exhibit(items: list[dict], primary: str) -> str | None:
    """Choose the press release inside an 8-K filing folder.

    Prefer a file named like EX-99.1; otherwise the largest .htm that is not the
    8-K cover document, an index page, or an XBRL viewer file.
    """
    docs = [it for it in items if it["name"].lower().endswith((".htm", ".html", ".txt"))
            and it["name"] != primary and "index" not in it["name"].lower()
            and not re.match(r"^R\d+\.htm$", it["name"]) and not it["name"].lower().endswith("-index.htm")
            and not re.fullmatch(r"\d{10}-\d{2}-\d{6}\.txt", it["name"])]
    if not docs:
        return None
    named = sorted([d for d in docs if re.search(r"(ex|exhibit)[-_]?99[-_.]?0?1|ex991|ex-99", d["name"], re.I)],
                   key=lambda d: d["name"])
    if named:
        return named[0]["name"]
    prs = [d for d in docs if re.search(r"(^|[^a-z])(pr|press|earnings|release)", d["name"], re.I)]
    pool = prs or docs

    def size(d: dict) -> int:
        try:
            return int(d.get("size") or 0)
        except ValueError:
            return 0
    return max(pool, key=size)["name"]


def fetch_all(since: str = "2022-01-01", only: list[str] | None = None) -> list[dict]:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"})
    results = []
    for c in load_companies():
        if only and c["ticker"] not in only:
            continue
        print(f"fetching {c['ticker']} ...", flush=True)
        results.append(fetch_company(c, since, session))
        print(f"  {results[-1]}")
    return results
