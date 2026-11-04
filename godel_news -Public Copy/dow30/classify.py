"""
Dow 30 news workflow over a Godel Terminal capture.

Reads the Dow 30 news store written by `python -m news dow30`
(data_dow30/news), filters to Dow 30 constituents, and classifies each
item: bullish / bearish / neutral, impact 1-5, event type, and the horizon
over which it is likely to matter.

Usage (from the godel_news folder):
    python -m news dow30-classify                        # default store path
    python -m news dow30-classify --raw <path-to-csv>
    python -m news dow30-classify --min-impact 3         # only material items
    python -m news dow30-classify --out dow30_news.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW = _ROOT / "data_dow30" / "news" / "godel_news_raw.csv"
DEFAULT_OUT = _ROOT / "data_dow30" / "dow30_news_classified.csv"

# ---------------------------------------------------------------- universe
# Dow Jones Industrial Average constituents. Names are matched against the
# headline when the capture's ticker column is blank, which is the common
# case for rows captured before group-switching existed.
DOW30 = {
    "AAPL": ("Apple",),
    "AMGN": ("Amgen",),
    "AMZN": ("Amazon",),
    "AXP":  ("American Express", "AmEx"),
    "BA":   ("Boeing",),
    "CAT":  ("Caterpillar",),
    "CRM":  ("Salesforce",),
    "CSCO": ("Cisco",),
    "CVX":  ("Chevron",),
    "DIS":  ("Disney",),
    "GS":   ("Goldman Sachs", "Goldman"),
    "HD":   ("Home Depot",),
    "HON":  ("Honeywell",),
    "IBM":  ("IBM", "International Business Machines"),
    "JNJ":  ("Johnson & Johnson", "Johnson and Johnson"),
    "JPM":  ("JPMorgan", "JP Morgan", "JPMorgan Chase"),
    "KO":   ("Coca-Cola", "Coca Cola"),
    "MCD":  ("McDonald's", "McDonalds"),
    "MMM":  ("3M",),
    "MRK":  ("Merck",),
    "MSFT": ("Microsoft",),
    "NKE":  ("Nike",),
    "NVDA": ("NVIDIA", "Nvidia"),
    "PG":   ("Procter & Gamble", "Procter and Gamble", "P&G"),
    "SHW":  ("Sherwin-Williams", "Sherwin Williams"),
    "TRV":  ("Travelers",),
    "UNH":  ("UnitedHealth", "United Health"),
    "V":    ("Visa",),
    "VZ":   ("Verizon",),
    "WMT":  ("Walmart",),
}

# ---------------------------------------------------------------- lexicons
BULLISH = (
    "beat", "beats", "tops estimates", "raises guidance", "raised guidance",
    "upgrade", "upgraded", "outperform", "buy rating", "price target raised",
    "record high", "all-time high", "surge", "surges", "jumps", "rally",
    "approval", "approved", "wins", "awarded", "new contract", "expands",
    "launch", "launches", "partnership", "buyback", "repurchase",
    "dividend increase", "raises dividend", "hikes dividend", "acquires",
    "strong demand", "backlog", "breakthrough", "milestone", "settlement reached",
)
BEARISH = (
    "miss", "misses", "cuts guidance", "lowered guidance", "warns", "warning",
    "downgrade", "downgraded", "underperform", "sell rating",
    "price target cut", "plunge", "plunges", "slides", "tumbles", "falls",
    "selloff", "sell-off", "lawsuit", "sues", "sued", "probe", "investigation",
    "subpoena", "fine", "fined", "penalty", "recall", "halt", "halts",
    "layoffs", "job cuts", "restructuring charge", "writedown", "write-down",
    "impairment", "delay", "delayed", "shortfall", "antitrust", "fraud",
    "resigns", "steps down", "ousted", "bankruptcy", "default",
)

EVENT_TYPES = {
    "earnings": ("earnings", "eps", "quarterly results", "q1", "q2", "q3", "q4",
                 "revenue", "beats estimates", "misses estimates", "profit"),
    "guidance": ("guidance", "outlook", "forecast", "raises full-year",
                 "cuts full-year", "reaffirms"),
    "analyst_action": ("upgrade", "downgrade", "price target", "initiated coverage",
                       "reiterates", "analyst", "rating"),
    "litigation": ("lawsuit", "sues", "sued", "court", "verdict", "settlement",
                   "jury", "damages", "class action", "appeal"),
    "regulation": ("sec", "ftc", "doj", "regulator", "antitrust", "probe",
                   "investigation", "compliance", "fda", "epa", "tariff",
                   "subpoena", "fine"),
    "management": ("ceo", "cfo", "chief executive", "chairman", "resigns",
                   "steps down", "appoints", "names", "successor", "board"),
    "m_and_a": ("acquire", "acquires", "acquisition", "merger", "buyout",
                "takeover", "divest", "spinoff", "spin-off", "stake", "deal"),
    "product": ("launch", "launches", "unveils", "introduces", "new product",
                "approval", "approved", "certification", "patent", "recall"),
    "capital_return": ("dividend", "buyback", "repurchase", "split"),
}

# Event types that tend to move a stock immediately vs. over longer windows.
HORIZON_BY_EVENT = {
    "earnings": "next_day",
    "guidance": "next_day",
    "analyst_action": "next_day",
    "capital_return": "next_week",
    "management": "next_week",
    "product": "next_quarter",
    "m_and_a": "next_quarter",
    "litigation": "next_quarter",
    "regulation": "next_quarter",
}

SOURCE_TIER = {
    "reuters": 3, "bloomberg": 3, "wsj": 3, "wall street journal": 3,
    "ft": 3, "financial times": 3, "ap": 3, "associated press": 3,
    "cnbc": 2, "barron's": 2, "barrons": 2, "marketwatch": 2, "forbes": 2,
    "business insider": 1, "seeking alpha": 1, "benzinga": 1, "zacks": 1,
    "reddit": 0, "twitter": 0, "x": 0,
}


# Same-name companies that are NOT the Dow constituent. Checked before the
# name match: "Merck KGaA, Darmstadt" is the German Merck (EMD Serono in the
# US), a different listed company from Merck & Co (MRK).
NEGATIVE_CONTEXT = {
    "MRK": ("merck kgaa", "merck group", "emd serono", "darmstadt"),
}


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9&'. -]", " ", (text or "").lower()) + " "


def _compile_lexicon(terms):
    """Word-bounded patterns. Plain substring matching is wrong here: it
    finds 'sues' inside 'Issues' and 'deal' inside 'dealer'."""
    return [(t, re.compile(r"(?<!\w)" + re.escape(t).strip().replace(r"\ ", r"\s+")
                           + r"(?!\w)", re.IGNORECASE))
            for t in terms]


def _hits(text: str, compiled) -> list:
    return [t for t, pat in compiled if pat.search(text)]


_BULL_RE = None
_BEAR_RE = None
_EVENT_RE = None


def _lexicons():
    global _BULL_RE, _BEAR_RE, _EVENT_RE
    if _BULL_RE is None:
        _BULL_RE = _compile_lexicon(BULLISH)
        _BEAR_RE = _compile_lexicon(BEARISH)
        _EVENT_RE = {e: _compile_lexicon(kws) for e, kws in EVENT_TYPES.items()}
    return _BULL_RE, _BEAR_RE, _EVENT_RE


_NAME_RE = {
    sym: [re.compile(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])",
                     re.IGNORECASE)
          for n in names]
    for sym, names in DOW30.items()
}


def name_in_headline(headline: str) -> str | None:
    """Company-name match with word boundaries. Boundaries matter: a bare
    substring search matches 'Nike' inside 'Nikesh Arora' and 'Visa' inside
    'Visakhapatnam'."""
    low = (headline or "").lower()
    for sym, patterns in _NAME_RE.items():
        if any(bad in low for bad in NEGATIVE_CONTEXT.get(sym, ())):
            continue
        for pat in patterns:
            if pat.search(headline or ""):
                return sym
    return None


def match_dow(ticker: str, headline: str, strict: bool = False):
    """
    Resolve a captured row to a Dow 30 ticker.

    Returns (symbol, basis) or (None, None). Basis is one of:
      both          - Godel's ticker tag and the headline name agree
      headline_name - only the headline names the company
      ticker_tag    - only Godel's ticker column says so (least reliable:
                      the live feed tags loosely, e.g. an AMD headline
                      carrying an MSFT tag)

    strict=True drops ticker_tag-only rows.
    """
    tag = (ticker or "").strip().upper()
    tag = tag if tag in DOW30 else None
    named = name_in_headline(headline)

    if tag and named and tag == named:
        return tag, "both"
    if named:
        return named, "headline_name"
    if tag and not strict:
        return tag, "ticker_tag"
    return None, None


def classify(headline: str, detail: str = "") -> dict:
    """
    Deterministic sentiment / impact / event-type classification.

    The headline decides; the article body is only a tiebreaker. Scoring
    both equally lets a long body full of incidental market words swamp
    what the headline actually says.
    """
    bull_re, bear_re, event_re = _lexicons()
    head = _norm(headline)
    body = _norm(detail)

    bull = _hits(head, bull_re)
    bear = _hits(head, bear_re)

    if len(bull) > len(bear):
        sentiment = "bullish"
    elif len(bear) > len(bull):
        sentiment = "bearish"
    else:
        # headline is neutral or balanced - let the body break the tie
        b_bull = len(_hits(body, bull_re))
        b_bear = len(_hits(body, bear_re))
        if b_bull > b_bear * 2 and b_bull >= 3:
            sentiment = "bullish"
        elif b_bear > b_bull * 2 and b_bear >= 3:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

    text = head
    events = sorted(e for e, compiled in event_re.items()
                    if _hits(head, compiled))

    # Impact 1-5: signal density, event severity, and headline specificity.
    impact = 1
    impact += min(2, len(bull) + len(bear))
    if {"earnings", "guidance"} & set(events):
        impact += 1
    if {"litigation", "regulation", "m_and_a", "management"} & set(events):
        impact += 1
    if any(k in text for k in ("halt", "recall", "fraud", "bankruptcy",
                               "criminal", "resigns", "ceo")):
        impact += 1
    impact = max(1, min(5, impact))

    # No recognized event type means no basis for a horizon claim.
    horizon = "next_quarter" if events else "unclear"
    for ev in events:
        h = HORIZON_BY_EVENT.get(ev)
        if h == "next_day":
            horizon = "next_day"
            break
        if h == "next_week" and horizon != "next_day":
            horizon = "next_week"

    return {
        "sentiment": sentiment,
        "impact": impact,
        "event_types": "|".join(events) or "general",
        "horizon": horizon,
        "signal_terms": "|".join((bull + bear)[:5]),
    }


def source_tier(source: str) -> int:
    return SOURCE_TIER.get((source or "").strip().lower(), 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW,
                    help="godel_news_raw.csv produced by the godel-news step")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-impact", type=int, default=1, choices=range(1, 6))
    ap.add_argument("--strict", action="store_true",
                    help="drop rows matched only by Godel's ticker tag "
                         "(the live feed tags loosely); require the headline "
                         "to name the company")
    args = ap.parse_args(argv)

    if not args.raw.exists():
        print(f"news store not found: {args.raw}\n"
              f"Run the capture first:  python -m news dow30",
              file=sys.stderr)
        return 1

    with args.raw.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    out_rows = []
    for r in rows:
        headline = (r.get("headline") or "").strip()
        if not headline:
            continue
        sym, basis = match_dow(r.get("ticker", ""), headline, strict=args.strict)
        if sym is None:
            continue
        c = classify(headline, r.get("detail_text", ""))
        if c["impact"] < args.min_impact:
            continue
        out_rows.append({
            "ticker": sym,
            "match_basis": basis,
            "date": r.get("date", ""),
            "time": r.get("time", ""),
            "headline": headline,
            "source": r.get("source", ""),
            "source_tier": source_tier(r.get("source", "")),
            "sentiment": c["sentiment"],
            "impact": c["impact"],
            "event_types": c["event_types"],
            "horizon": c["horizon"],
            "signal_terms": c["signal_terms"],
            "detail_status": r.get("detail_status", ""),
        })

    basis_rank = {"both": 0, "headline_name": 1, "ticker_tag": 2}
    out_rows.sort(key=lambda x: (basis_rank[x["match_basis"]], -x["impact"],
                                 x["ticker"]))

    with args.out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()) if out_rows
                           else ["ticker", "date", "headline"])
        w.writeheader()
        w.writerows(out_rows)

    # ------------------------------------------------------------ summary
    print(f"scanned {len(rows)} captured rows -> {len(out_rows)} Dow 30 items")
    print(f"written: {args.out.resolve()}\n")

    if not out_rows:
        print("No Dow 30 headlines in this capture. If you expected some, the "
              "capture was probably taken on the AB4.1 (crypto) news group - "
              "re-run with `python -m news dow30`.")
        return 0

    print("match basis:", dict(collections.Counter(r["match_basis"]
                                                   for r in out_rows)))
    print("sentiment: ", dict(collections.Counter(r["sentiment"]
                                                  for r in out_rows)))
    print("horizon:   ", dict(collections.Counter(r["horizon"]
                                                  for r in out_rows)))
    covered = sorted({r["ticker"] for r in out_rows})
    print(f"tickers covered ({len(covered)}/30):", " ".join(covered))
    missing = sorted(set(DOW30) - set(covered))
    if missing:
        print(f"no news captured ({len(missing)}):", " ".join(missing))

    confirmed = [r for r in out_rows if r["match_basis"] != "ticker_tag"]
    print(f"\ntop headline-confirmed items ({len(confirmed)} of {len(out_rows)}):")
    for r in sorted(confirmed, key=lambda x: -x["impact"])[:15]:
        print(f"  [{r['impact']}] {r['ticker']:5s} {r['sentiment']:8s} "
              f"{r['horizon']:13s} {r['headline'][:84]}")
    if len(confirmed) < len(out_rows):
        print(f"\n{len(out_rows) - len(confirmed)} row(s) matched only by "
              f"Godel's ticker tag - review before trusting, or re-run "
              f"with --strict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
