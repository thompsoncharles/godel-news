"""
Duplicate flagging for the news store (research/reporting only).

FLAGS, NEVER DELETES. Every interpreted row gets five columns:

    is_duplicate          True on every copy after the first; False on originals
    duplicate_type        "exact" | "near" | "" (blank on originals)
    duplicate_of          headline_hash of the group's original row ("" on originals)
    duplicate_similarity  1.0 for exact, rarity-weighted word overlap for near
    duplicate_group_size  rows in the group, original included (1 = unique)

"The original" is the earliest copy by NEWS time (date + time columns), ties
broken by capture time then hash, so the result does not depend on the order
rows were scraped in. Flags are recomputed over the whole store on every
write, so adding an older copy later can move the "original" to it.

EXACT - same headline text after lower-casing, dropping punctuation and a
trailing "[Source Name]" tag, within DUP_WINDOW_HOURS of the original. Any
ticker/source. (Same text does not prove the same document: e.g. dozens of
SEC 424B2 filings share the headline "424B2 - JPMORGAN CHASE & CO".)

NEAR - a reworded copy of the same story. All of:
  1. within DUP_WINDOW_HOURS of the group's original
  2. same Godel ticker tag (when both rows have one)
  3. same facts: when both headlines contain numbers or month names, the
     sets must be identical ("$150" vs "$55", "20 APR" vs "4 MAY" = different)
  4. rarity-weighted word overlap >= NEAR_DUP_MIN_SIMILARITY: each word
     weighs log((N+1)/(n+0.5)), N = distinct headlines in the store, n =
     headlines containing the word, so boilerplate ("announces", "price
     target") counts little and names/places count a lot
  5. not a template: if the two headlines open with the same phrase of at
     least TEMPLATE_PREFIX_MIN_CHARS characters and more than
     TEMPLATE_MAX_HEADLINES headlines in the store open that way, they are a
     template ("GOLDMAN SACHS INTERNATIONAL Announces Outstanding Short
     Selling Positions of ...") and never near-duplicates of each other
A row may match any earlier copy in a group (not only the original), so the
four rewordings of one story end up in one group.

Tuned on the stores as of 2026-10-31: every near pair flagged in the BTC
store was a genuine rewording; thresholds trade recall for precision so a
filter on is_duplicate never hides distinct news.
"""
from __future__ import annotations

import bisect
import math
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

DUP_COLUMNS = ["is_duplicate", "duplicate_type", "duplicate_of",
               "duplicate_similarity", "duplicate_group_size"]

DUP_WINDOW_HOURS = 72
NEAR_DUP_MIN_SIMILARITY = 0.70
NEAR_DUP_CROSS_TICKER = 0.85     # headline overlap that matches across ticker tags
ARTICLE_MIN_SIMILARITY = 0.62    # article-text similarity for an 'article' duplicate
                                 # (Zacks' templated daily recaps of different
                                 # days score 0.60)
ARTICLE_MIN_CHARS = 300          # shorter bodies are too thin to compare
TEMPLATE_PREFIX_MIN_CHARS = 25
TEMPLATE_MAX_HEADLINES = 8

_STOPWORDS = frozenset(
    "the a an and or of to in on for at by with from as is are was be its it "
    "this that after over into amid vs via new says said will has have not "
    "but than more about up down out off per".split())
_MONTHS = frozenset(
    "jan feb mar apr may jun jul aug sep sept oct nov dec january february "
    "march april june july august september october november december".split())
_SOURCE_TAG = re.compile(r"\s*\[[^\]]*\]?\s*$")     # "... [Al Bawaba Business]"
_DATE_FORMATS = ("%m/%d/%y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M",
                 "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                 "%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d")


def event_time(row: dict) -> Optional[datetime]:
    """News time from the date/time columns (Godel writes 7/30/26; Excel
    re-saves it as 7/30/2026), falling back to captured_at."""
    stamp = f"{row.get('date', '')} {row.get('time', '')}".strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(row.get("captured_at", "")))
    except ValueError:
        return None


def normalized_text(headline: str) -> str:
    """Comparison form: no trailing [Source] tag, lower case, punctuation
    and repeated spaces collapsed."""
    text = _SOURCE_TAG.sub("", headline or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def content_words(norm: str) -> frozenset:
    return frozenset(w for w in norm.split() if len(w) >= 2 and w not in _STOPWORDS)


def month_words(norm: str) -> frozenset:
    return frozenset(w for w in norm.split() if w in _MONTHS)


_NUMBER = re.compile(r"(?<![a-z0-9.])(\d+(?:,\d{3})*(?:\.\d+)?)\s*"
                     r"(%|percent|k|thousand|m|mn|mln|million|b|bn|billion|t|tn|trillion)?"
                     r"(?![a-z0-9])")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "mln": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "tn": 1e12, "trillion": 1e12}


def _significant_digits(written: str) -> int:
    digits = written.replace(",", "").replace(".", "").lstrip("0")
    if "." not in written:
        digits = digits.rstrip("0")       # "150", "100,000": trailing zeros are rounding
    return max(1, len(digits))


def numbers(headline: str) -> tuple:
    """(kind, value, significant digits) for every number in a headline, scale
    words applied: "$6.3B", "$6.3 billion" and "6.3bn" are all ('n', 6.3e9, 2);
    "15%" is ('%', 15, 2). Digits glued to letters ("Q2", "x402") are names."""
    text = _SOURCE_TAG.sub("", headline or "").lower()
    out = []
    for value, unit in _NUMBER.findall(text):
        v = float(value.replace(",", ""))
        kind = "%" if unit in ("%", "percent") else "n"
        out.append((kind, v if kind == "%" else v * _SCALE.get(unit, 1.0),
                    _significant_digits(value)))
    return tuple(out)


def _round_sig(value: float, sig: int) -> float:
    if value == 0:
        return 0.0
    return round(value, sig - int(math.floor(math.log10(abs(value)))) - 1)


ROUNDING_MIN_AMOUNT = 1e6       # only amounts this large are written rounded
ROUNDING_MAX_GAP = 0.10         # ... and never by more than 10%


def _same_number(x: tuple, y: tuple) -> bool:
    """Equal, or a large amount written rounded: "$6 billion" ~ "$6.3B"
    (6.3 rounds to 6, 5% apart). Small numbers - dates, prices, counts,
    percentages - must match exactly: "30 JUN 26" is not "26 JUN 26"."""
    if x[0] != y[0]:
        return False
    if math.isclose(x[1], y[1], rel_tol=1e-9):
        return True
    if x[0] != "n" or min(abs(x[1]), abs(y[1])) < ROUNDING_MIN_AMOUNT:
        return False
    if abs(x[1] - y[1]) > ROUNDING_MAX_GAP * max(abs(x[1]), abs(y[1])):
        return False
    rough, exact = (x, y) if x[2] < y[2] else (y, x)
    return rough[2] < exact[2] and math.isclose(_round_sig(exact[1], rough[2]), rough[1],
                                                rel_tol=1e-9)


def numbers_compatible(a: tuple, b: tuple) -> bool:
    """Different numbers mean different events ($150 vs $55 price targets).
    When both headlines have numbers, every number in the one with fewer
    must appear in the other (allowing rounding: "$6B" ~ "$6.3B")."""
    if not a or not b:
        return True
    few, many = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(_same_number(x, y) for y in many) for x in few)


def _article_vectors(texts: List[str]) -> List[Optional[Dict[str, float]]]:
    """Rarity-weighted (tf-idf) unit vectors of article bodies; None for
    rows without enough text."""
    docs = [Counter(w for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", t.lower())
                    if len(w) >= 3 and w not in _STOPWORDS)
            if len(t) >= ARTICLE_MIN_CHARS else None for t in texts]
    real = [d for d in docs if d]
    df = Counter(w for d in real for w in d)
    n = len(real)
    out = []
    for d in docs:
        if not d:
            out.append(None)
            continue
        v = {w: (1 + math.log(c)) * math.log((n + 1) / (df[w] + 0.5)) for w, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    prefix = a[:n]
    # whole words only, so "short selling positions of s" -> "...of"
    return prefix if n == min(len(a), len(b)) else prefix.rsplit(" ", 1)[0]


class _Corpus:
    """Word weights and template lookups over the store's distinct headlines."""

    def __init__(self, norms: List[str]):
        distinct = sorted(set(norms))
        self.sorted_texts = distinct
        n = len(distinct)
        counts = Counter(w for t in distinct for w in content_words(t))
        self.weight = {w: math.log((n + 1) / (c + 0.5)) for w, c in counts.items()}

    def words_weight(self, words) -> float:
        return sum(self.weight.get(w, 0.0) for w in words)

    def headlines_starting_with(self, prefix: str) -> int:
        lo = bisect.bisect_left(self.sorted_texts, prefix)
        hi = bisect.bisect_left(self.sorted_texts, prefix + "￿")
        return hi - lo

    def is_template(self, a: str, b: str) -> bool:
        prefix = _common_prefix(a, b)
        return (len(prefix) >= TEMPLATE_PREFIX_MIN_CHARS and
                self.headlines_starting_with(prefix) > TEMPLATE_MAX_HEADLINES)


def flag_duplicates(df: pd.DataFrame,
                    bodies: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """Return a copy of df with the DUP_COLUMNS (re)computed. Row order and
    every other column are left exactly as they were. `bodies` (headline_hash
    -> article text, from the raw CSV) enables 'article' duplicates."""
    out = df.copy()
    if out.empty or "headline" not in out.columns:
        for c in DUP_COLUMNS:
            out[c] = pd.Series(dtype=object)
        return out

    records = out.to_dict("records")
    n = len(records)
    times = [event_time(r) for r in records]
    norms = [normalized_text(str(r.get("headline", ""))) for r in records]
    words = [content_words(t) for t in norms]
    weights = None                      # computed lazily: exact-only stores skip it
    corpus = None
    tickers = [str(r.get("ticker", "") or "").strip().upper() for r in records]
    nums = [numbers(str(r.get("headline", ""))) for r in records]
    months = [month_words(t) for t in norms]
    articles = _article_vectors([(bodies or {}).get(str(r.get("headline_hash", "")), "")
                                 for r in records])
    order = sorted(range(n), key=lambda i: (times[i] or datetime.max,
                                            str(records[i].get("captured_at", "")),
                                            str(records[i].get("headline_hash", ""))))
    window = timedelta(hours=DUP_WINDOW_HOURS)

    original: Dict[int, int] = {}       # row -> its group's original row
    kind: Dict[int, str] = {}
    similarity: Dict[int, float] = {}
    by_text: Dict[str, int] = {}        # normalized text -> latest group original
    seen: List[int] = []                # rows already placed, in time order

    def in_window(i: int, orig: int) -> bool:
        return (times[i] is not None and times[orig] is not None
                and times[i] - times[orig] <= window)

    start = 0
    for i in order:
        # exact: same text as a recent group
        g = by_text.get(norms[i])
        if g is not None and in_window(i, g):
            original[i], kind[i], similarity[i] = g, "exact", 1.0
            seen.append(i)
            continue

        # near: best reworded match among recent rows
        if corpus is None:
            corpus = _Corpus(norms)
            weights = [corpus.words_weight(w) for w in words]
        while start < len(seen) and not in_window(i, original[seen[start]]):
            start += 1
        best, best_sim, best_kind = None, 0.0, ""
        for j in seen[start:]:
            g = original[j]
            if not in_window(i, g) or norms[j] == norms[i]:
                continue
            if not numbers_compatible(nums[i], nums[j]):
                continue
            if months[i] and months[j] and months[i] != months[j]:
                continue
            same_ticker = not (tickers[i] and tickers[j] and tickers[i] != tickers[j])
            sim, match = 0.0, ""
            lo, hi = sorted((weights[i], weights[j]))
            if hi > 0 and lo / hi >= NEAR_DUP_MIN_SIMILARITY:   # else overlap can't reach it
                union = corpus.words_weight(words[i] | words[j])
                sim = corpus.words_weight(words[i] & words[j]) / union if union else 0.0
            if sim >= (NEAR_DUP_MIN_SIMILARITY if same_ticker else NEAR_DUP_CROSS_TICKER):
                match = "near"
            elif same_ticker and articles[i] and articles[j]:
                sim = _cosine(articles[i], articles[j])
                match = "article" if sim >= ARTICLE_MIN_SIMILARITY else ""
            if not match or sim <= best_sim:
                continue
            if corpus.is_template(norms[i], norms[j]):
                continue
            best, best_sim, best_kind = g, sim, match
        if best is not None:
            original[i], kind[i], similarity[i] = best, best_kind, round(best_sim, 2)
        else:
            original[i], kind[i] = i, ""
        by_text[norms[i]] = original[i]
        seen.append(i)

    size = Counter(original.values())
    hashes = [str(r.get("headline_hash", "")) for r in records]
    out["is_duplicate"] = [original[i] != i for i in range(n)]
    out["duplicate_type"] = [kind[i] for i in range(n)]
    out["duplicate_of"] = [hashes[original[i]] if original[i] != i else ""
                           for i in range(n)]
    out["duplicate_similarity"] = [similarity.get(i, "") for i in range(n)]
    out["duplicate_group_size"] = [size[original[i]] for i in range(n)]
    return out
