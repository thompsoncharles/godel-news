"""
Deterministic BTC/SPY asset-association classifier for captured news
(research/reporting only - never feeds trading, sizing, or orders).

Identical file in both repos (V4.1algobot-master and V4.3ABSP399) - this
is the "compatible" half of news routing: both repos classify an article
the same way, even though each repo's own canonical store and reports
stay physically isolated (quant_data/ never crosses the repo boundary).

Association is decided in two stages, in this order:
  1. CAPTURE METADATA (authoritative when the NEWS-panel group switch was
     verified): the group the article was captured under - AB4.1 -> BTC,
     SP400 -> SPY (see news.godel_reader.switch_news_group). Content
     is still scanned for the OTHER asset's lexicon to catch genuinely
     cross-market stories and upgrade to "BOTH" - the capture group only
     ever ADDS to a strong opposite-content signal, never silently
     discards it.
  2. CONTENT-ONLY FALLBACK, used when group metadata is absent/unverified
     (all historical rows captured before group-switching existed fall
     through to this stage entirely): a deterministic keyword lexicon.
     No hits on either side -> AMBIGUOUS - never guessed.

Possible associations: "BTC", "SPY", "BOTH", "AMBIGUOUS". AMBIGUOUS rows
are meant to be routed to a review/quarantine table by callers and
excluded from both reports - never deleted, never silently dropped.
"""
from __future__ import annotations

import re
from typing import Optional

try:
    from news.godel_reader import ASSET_GROUPS, CRYPTO_TICKER_HINTS
except Exception:                       # pragma: no cover - import safety
    ASSET_GROUPS, CRYPTO_TICKER_HINTS = {}, set()

# historical labels stay mapped (old rows carry them); configured watchlist
# names (GODEL_GROUP_BTC / GODEL_GROUP_SPY) are added on top
GROUP_TO_ASSET = {"AB4.1": "BTC", "SP400": "SPY",
                  ASSET_GROUPS.get("btc", "AB4.1"): "BTC",
                  ASSET_GROUPS.get("spy", "SP400"): "SPY"}

# Cross-market / macro terms that plausibly move BOTH a crypto and an
# equity book (Fed policy, rates, inflation, broad risk sentiment).
MACRO_KEYWORDS = (
    "federal reserve", "fomc", "fed chair", "fed funds", "interest rate",
    "rate cut", "rate hike", "rate decision", "consumer price index",
    " cpi ", "inflation", "jobs report", "nonfarm payroll",
    "unemployment rate", "treasury yield", "10-year yield", "10 year yield",
    "bond market", "recession", "gdp growth", " gdp ", "pce index",
    "powell", "yellen", "fiscal policy", "debt ceiling", "risk-on",
    "risk on", "risk-off", "risk off", "market selloff", "central bank",
    "rate path", "quantitative easing", "quantitative tightening",
)

CRYPTO_KEYWORDS = (
    "bitcoin", "crypto", "cryptocurrency", "blockchain", "ethereum",
    "altcoin", "stablecoin", "defi", "web3", "satoshi", "halving",
    "mining rig", "hash rate", "digital asset", "spot etf",
)

EQUITY_KEYWORDS = (
    "s&p 500", "spy etf", "spdr", "equity market", "stock market",
    "large-cap", "large cap", "earnings season", "quarterly earnings",
    "shareholder", "dividend", "buyback", "index fund", "wall street",
    "dow jones", "nasdaq composite",
)


def _norm(text: Optional[str]) -> str:
    """Lowercase, punctuation-light, padded so substring lexicon terms
    (some start/end with a space to require a word boundary) match
    reliably at string edges too."""
    return " " + re.sub(r"[^a-z0-9&%. ]", " ", (text or "").lower()) + " "


def _keyword_hits(text: str, keywords) -> list:
    return [k.strip() for k in keywords if k in text]


def classify_association(headline: str = "", detail_text: str = "",
                         ticker: str = "", selected_group: Optional[str] = None,
                         group_verified: bool = False) -> dict:
    """
    Deterministic BTC/SPY association for one captured article.

    Returns {association, confidence, reason, matched_terms} where
    association is one of "BTC", "SPY", "BOTH", "AMBIGUOUS".
    """
    text = _norm(f"{headline} {detail_text}")
    tick = (ticker or "").strip().upper()

    crypto_hits = _keyword_hits(text, CRYPTO_KEYWORDS)
    if tick in CRYPTO_TICKER_HINTS:
        crypto_hits = crypto_hits + [f"ticker:{tick}"]
    equity_hits = _keyword_hits(text, EQUITY_KEYWORDS)
    macro_hits = _keyword_hits(text, MACRO_KEYWORDS)

    content_says_btc = bool(crypto_hits)
    content_says_spy = bool(equity_hits)
    cross_market = bool(macro_hits)

    base_asset = GROUP_TO_ASSET.get(selected_group) if group_verified else None

    if base_asset is not None:
        other_signal = content_says_spy if base_asset == "BTC" else content_says_btc
        other = "SPY" if base_asset == "BTC" else "BTC"
        if other_signal or cross_market:
            return {"association": "BOTH", "confidence": 0.7,
                    "reason": f"captured_group={selected_group} ({base_asset}) "
                             f"+ content indicates {other}/macro relevance",
                    "matched_terms": crypto_hits + equity_hits + macro_hits}
        return {"association": base_asset, "confidence": 0.95,
                "reason": f"verified_capture_group={selected_group}",
                "matched_terms": []}

    # no verified capture metadata (historical row, or switch unverified) -
    # content-only classification
    if content_says_btc and content_says_spy:
        return {"association": "BOTH", "confidence": 0.55,
                "reason": "content matches both crypto and equity lexicons",
                "matched_terms": crypto_hits + equity_hits}
    if content_says_btc:
        conf = 0.5 if cross_market else 0.6
        return {"association": "BTC", "confidence": conf,
                "reason": "content matches crypto lexicon",
                "matched_terms": crypto_hits + macro_hits}
    if content_says_spy:
        conf = 0.5 if cross_market else 0.6
        return {"association": "SPY", "confidence": conf,
                "reason": "content matches equity lexicon",
                "matched_terms": equity_hits + macro_hits}
    if cross_market:
        return {"association": "BOTH", "confidence": 0.4,
                "reason": "macro-only content, no asset-specific lexicon "
                         "hit - treated as cross-market",
                "matched_terms": macro_hits}
    return {"association": "AMBIGUOUS", "confidence": 0.0,
            "reason": "no verified capture group and no lexicon match",
            "matched_terms": []}
