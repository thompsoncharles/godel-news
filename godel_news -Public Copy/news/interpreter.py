"""
Deterministic news interpretation for BTC research.

Pure keyword/rule based - same headline in, same fields out, no network, no
LLM, no randomness. Produces the interpretation fields and the research
coefficients that can later be joined to BTC observations. SCORING.md (project
root) explains every column with worked examples.

Keywords match WHOLE words only, plus the regular plural ('war' hits "war" and
"wars" but not "software", "toward" or "wares"). In the sentiment lists the
LONGEST phrase wins across all of them, so "ban lifted" counts as bullish
rather than as the bearish "ban", and NEUTRAL phrases ("pre-war", "emergency
fund") switch a word off. Every term was checked against the stored articles
(SCORING.md). Sentiment is decided by the HEADLINE; the article only breaks a
neutral headline with clear evidence (decide_sentiment), and
`sentiment_basis` says which text decided. Rows carry `scoring_version`;
rows scored by older rules are re-scored from the raw CSV (news.store).

RESEARCH ONLY - HARD GUARANTEE:
    trade_impact_enabled is a module-level constant False. Nothing in this
    program trades, and the trading bot never reads these files.
    Coefficients are logged for future research and do not touch any
    trading decision.
"""
from __future__ import annotations

import functools
import hashlib
import re
from typing import Dict, Iterable, List, Optional

TRADE_IMPACT_ENABLED = False   # research only - never flip without a fresh OOS judge + user sign-off
SCORING_VERSION = 5            # 1 = substring (before 2026-10-31), 2 = whole words,
                               # 3 = strict lists + longest phrase wins,
                               # 4 = the headline decides sentiment,
                               # 5 = strong words weigh 2, score = net weight
SENTIMENT_FULL_SCALE = 4       # net keyword weight at which the score reaches +-1
ARTICLE_MAX_SCORE = 0.25       # an article-decided score is capped at one MILD
                               # headline word: articles hold many words, so their
                               # net weight would otherwise outrank headlines
NOVELTY_DUPLICATE = 0.3        # novelty_coeff of a row flagged as a duplicate (news.duplicates)

# The article only breaks a headline with no sentiment terms (or a tie), and
# only with clear evidence - the same rule as dow30/classify.py. One passing
# word in 5,000 characters used to decide most rows.
ARTICLE_MIN_RISK_TERMS = 2     # distinct risk terms in the article -> risk_event
ARTICLE_MIN_TERMS = 3          # winning side needs at least this many terms
ARTICLE_MAJORITY = 2           # ... and more than this many times the other side

# ----------------------------------------------------------------- lexicons
# Sentiment lists, version 3. Strict = the term reads the same way in almost
# every context. Single words that misfired in the stored articles were
# replaced by phrases: approval/approved (legal boilerplate), launch, adoption,
# partnership, upgrade/downgrade, bought, acquire(d), integration, easing,
# tightening, default ("the default choice"), charges (fees), exploited. Mild
# price verbs (falls, drops, declined, jumps, climbs) are left out on purpose:
# "attorney declined to comment", "Treasury yield climbed" read the wrong way.
BULLISH = {
    # price action / technicals
    "rally", "rallies", "rallied", "rallying", "surge", "surges", "surged", "surging",
    "soar", "soars", "soared", "soaring", "skyrocket", "skyrockets", "skyrocketed",
    "skyrocketing", "rebound", "rebounds", "rebounded", "rebounding",
    "all-time high", "all time high", "record high", "new high", "ath",
    "52-week high", "52 week high", "breaks above", "broke above", "breaks out",
    "broke out", "golden cross", "uptrend", "bull run", "bull market", "bullish",
    "short squeeze", "extends gains", "extended gains", "outperforms", "outperformed",
    # flows / adoption / crypto
    "inflow", "inflows", "etf inflows", "institutional demand", "accumulation",
    "mass adoption", "institutional adoption", "growing adoption", "rising adoption",
    "adoption grows", "adoption accelerates", "buys", "adopts bitcoin", "adopted bitcoin",
    "bitcoin reserve", "legal tender", "halving",
    # analysts
    "analyst upgrade", "rating upgrade", "credit upgrade", "upgraded to buy",
    "upgrades to buy", "upgraded to outperform", "upgrades to outperform",
    "upgraded to overweight", "upgrades to overweight", "upgraded to hold", "upgraded by",
    "buy rating", "outperform rating", "overweight rating", "raises price target",
    "raised price target", "boosts price target", "lifts price target",
    "price target raised", "price target increased", "reiterates buy",
    # earnings / guidance
    "beats estimates", "beat estimates", "beats expectations", "beat expectations",
    "tops estimates", "topped estimates", "tops expectations", "exceeds expectations",
    "exceeded expectations", "exceeds estimates", "better-than-expected",
    "better than expected", "record revenue", "record profit", "record earnings",
    "raises guidance", "raised guidance", "boosts guidance", "lifts guidance",
    "guidance raised", "raises outlook", "raised outlook", "raises forecast",
    "raised forecast",
    # capital return
    "buyback", "share buyback", "stock buyback", "share repurchase", "stock repurchase",
    "repurchase program", "raises dividend", "dividend increase", "dividend hike",
    "hikes dividend", "special dividend",
    # approvals
    "approves", "wins approval", "receives approval", "secures approval",
    "granted approval", "grants approval", "gains approval", "approval granted",
    "final approval", "charter approval", "etf approval", "wins regulatory approval",
    "receives regulatory approval", "granted regulatory approval",
    "secures regulatory approval", "green light", "greenlights", "greenlit",
    # partnerships / launches
    "partners with", "teams up with", "strategic partnership", "announces partnership",
    "forms partnership", "formed a partnership", "enters partnership",
    "partnership agreement", "etf launch", "launches etf", "mainnet launch", "expands",
    # macro
    "rate cut", "cuts rates", "cut rates", "cuts interest rates", "dovish", "stimulus",
    "quantitative easing", "monetary easing", "easing cycle", "policy easing",
    "fed easing", "liquidity injection",
    # legal reversals - outrank the bearish word inside them
    "lawsuit dismissed", "dismisses lawsuit", "dismissed the lawsuit", "case dismissed",
    "charges dropped", "drops charges", "dropped charges", "drops lawsuit",
    "dropped lawsuit", "drops case", "dropped the case", "ban lifted", "lifts ban",
    "lifted ban", "lifted the ban", "sanctions lifted", "lifts sanctions", "wins lawsuit",
    "wins case", "court victory", "favorable ruling", "acquitted", "not guilty",
    "bidding war",
}
BEARISH = {
    # price action / technicals
    "crash", "crashed", "crashing", "plunge", "plunges", "plunged", "plunging",
    "plummet", "plummets", "plummeted", "plummeting", "tumble", "tumbles", "tumbled",
    "tumbling", "slump", "slumps", "slumped", "slumping", "nosedive", "nosedives",
    "nosedived", "selloff", "sell-off", "market rout", "stock rout", "crypto rout",
    "bond rout", "bloodbath", "wipeout", "wiped out", "meltdown", "capitulation",
    "panic selling", "bear market", "bearish", "death cross", "downtrend",
    "breaks below", "broke below", "falls below", "fell below", "drops below",
    "dropped below", "52-week low", "52 week low", "all-time low", "all time low",
    "extends losses", "extended losses", "deepens losses", "underperforms",
    "underperformed", "outflow", "outflows",
    # analysts
    "analyst downgrade", "rating downgrade", "credit downgrade", "downgraded to sell",
    "downgrades to sell", "downgraded to underperform", "downgrades to underperform",
    "downgraded to underweight", "downgrades to underweight", "downgraded to hold",
    "downgrades to hold", "downgraded by", "sell rating", "underperform rating",
    "underweight rating", "cuts price target", "cut price target", "lowers price target",
    "lowered price target", "slashes price target", "price target cut",
    "price target lowered", "price target reduced",
    # earnings / guidance / corporate
    "misses estimates", "missed estimates", "misses expectations", "missed expectations",
    "falls short of estimates", "falls short of expectations", "below expectations",
    "below estimates", "worse-than-expected", "worse than expected", "profit warning",
    "cuts guidance", "cut guidance", "lowers guidance", "lowered guidance",
    "slashes guidance", "guidance cut", "cuts outlook", "lowers outlook", "lowered outlook",
    "cuts forecast", "lowers forecast", "lowered forecast", "write-down", "writedown",
    "disappointing results", "disappointing earnings", "disappointing guidance",
    "going concern", "layoffs", "lays off", "laid off", "job cuts", "cuts jobs", "cut jobs",
    "dilution", "dilutive", "delisting", "delisted", "class action", "class-action",
    # legal / enforcement
    "lawsuit", "sues", "sued", "fraud", "fraudulent", "probe", "under investigation",
    "investigation into", "opens investigation", "launches investigation",
    "launched an investigation", "subpoena", "subpoenaed", "wells notice",
    "cease and desist", "cease-and-desist", "enforcement action", "charged with",
    "faces charges", "criminal charges", "federal charges", "fraud charges", "sec charges",
    "cftc charges", "doj charges", "indicted", "indictment", "arrested", "pleads guilty",
    "pleaded guilty", "guilty plea", "convicted", "sentenced", "fined", "money laundering",
    "embezzlement", "embezzled", "misappropriated", "misappropriation", "ponzi",
    "pyramid scheme", "scam", "scammer", "rug pull", "rug-pull", "rugpull", "exit scam",
    "pump and dump", "dump", "crackdown", "ban", "bans", "banned", "banning",
    # crypto / funding stress (most also RISK_EVENT)
    "hack", "hacked", "exploit", "breach", "cyberattack", "cyber-attack", "ransomware",
    "flash crash", "drained", "stolen", "heist", "liquidation",
    "liquidations", "liquidated", "margin call", "forced selling", "liquidation cascade",
    "depeg", "depegged", "depegging", "de-peg", "de-pegged", "loses peg", "lost its peg",
    "insolvency", "insolvent", "bankruptcy", "bankruptcies", "bankrupt", "chapter 11",
    "contagion", "bank run", "liquidity crisis", "withdrawal halt", "halts withdrawals",
    "suspends withdrawals", "suspended withdrawals", "withdrawals suspended",
    "pauses withdrawals", "paused withdrawals", "withdrawals paused",
    "freezes withdrawals", "withdrawals frozen", "frozen withdrawals", "freezes assets",
    "assets frozen", "funds frozen", "frozen funds", "seized", "shutdown",
    "government shutdown", "defaulted", "defaults on", "default on", "debt default",
    "loan default", "bond default", "sovereign default", "payment default",
    "missed payment",
    # macro
    "rate hike", "raises rates", "raised rates", "hikes rates", "hawkish",
    "recession fears", "recession risk", "stagflation", "quantitative tightening",
    "monetary tightening", "tightening cycle", "policy tightening", "fed tightening",
    "liquidity tightening", "tightening liquidity",
    # competition / tone
    "price war", "fee war", "downturn", "turmoil", "collapse", "collapses", "collapsed",
    "headwinds",
}
RISK_EVENT = {
    # security
    "hack", "hacked", "exploit", "breach", "cyberattack", "cyber-attack", "ransomware",
    "phishing", "drained", "stolen", "heist", "compromised", "zero-day",
    "critical vulnerability", "security vulnerability", "rug pull", "rug-pull",
    "rugpull", "exit scam",
    # funding / market structure
    "insolvency", "insolvent", "bankruptcy", "bankruptcies", "bankrupt", "chapter 11",
    "bank run", "contagion", "liquidity crisis", "systemic risk", "black swan",
    "liquidation cascade", "flash crash", "circuit breaker", "halt", "halts", "halted",
    "trading suspended", "suspends trading", "depeg", "depegged", "depegging", "de-peg",
    "de-pegged", "loses peg", "lost its peg", "seized", "withdrawal halt",
    "halts withdrawals", "suspends withdrawals", "suspended withdrawals",
    "withdrawals suspended", "pauses withdrawals", "paused withdrawals",
    "withdrawals paused", "freezes withdrawals", "withdrawals frozen",
    "frozen withdrawals", "defaulted", "defaults on", "default on", "debt default",
    "loan default", "bond default", "sovereign default", "payment default",
    "government shutdown",
    # operational
    "outage", "service disruption",
    # geopolitical
    "war", "attack", "attacked", "invasion", "invades", "invaded", "missile", "airstrike",
    "air strike", "drone strike", "military strike", "bombing", "terrorist", "terrorism",
    "coup", "martial law", "assassination", "assassinated", "sanctions", "nuclear test",
    "nuclear strike", "blockade", "emergency",
    # natural / health
    "earthquake", "tsunami", "hurricane", "outbreak",
}
# Strong terms weigh 2 (every other term weighs 1): large moves and decisive
# events - a crash, a bankruptcy, charges dropped - against mild or opinion
# words like "bearish" or "expands".
STRONG = {
    # bullish
    "soar", "soars", "soared", "soaring", "skyrocket", "skyrockets", "skyrocketed",
    "skyrocketing", "all-time high", "all time high", "ath", "short squeeze",
    "etf approval", "raises guidance", "raised guidance", "boosts guidance",
    "lifts guidance", "guidance raised", "quantitative easing", "bitcoin reserve",
    "legal tender", "lawsuit dismissed", "dismisses lawsuit", "dismissed the lawsuit",
    "case dismissed", "charges dropped", "drops charges", "dropped charges", "drops lawsuit",
    "dropped lawsuit", "drops case", "dropped the case", "ban lifted", "lifts ban",
    "lifted ban", "lifted the ban", "sanctions lifted", "lifts sanctions", "acquitted",
    # bearish
    "crash", "crashed", "crashing", "plunge", "plunges", "plunged", "plunging", "plummet",
    "plummets", "plummeted", "plummeting", "nosedive", "nosedives", "nosedived",
    "market rout", "stock rout", "crypto rout", "bond rout", "bloodbath", "wipeout",
    "wiped out", "meltdown", "capitulation", "panic selling", "all-time low", "all time low",
    "flash crash", "collapse", "collapses", "collapsed", "profit warning", "cuts guidance",
    "cut guidance", "lowers guidance", "lowered guidance", "slashes guidance",
    "guidance cut", "going concern", "criminal charges", "fraud charges", "sec charges",
    "cftc charges", "doj charges", "indicted", "indictment", "wells notice",
    "pleads guilty", "pleaded guilty", "guilty plea", "convicted", "ponzi",
    "pyramid scheme", "rug pull", "rug-pull", "rugpull", "exit scam", "delisted",
    "delisting", "quantitative tightening", "missed payment",
    # bearish + risk
    "hack", "hacked", "exploit", "breach", "cyberattack", "cyber-attack", "ransomware",
    "drained", "stolen", "heist", "liquidation cascade", "depeg", "depegged", "depegging",
    "de-peg", "de-pegged", "loses peg", "lost its peg", "insolvency", "insolvent",
    "bankruptcy", "bankruptcies", "bankrupt", "chapter 11", "contagion", "bank run",
    "liquidity crisis", "withdrawal halt", "halts withdrawals", "suspends withdrawals",
    "suspended withdrawals", "withdrawals suspended", "pauses withdrawals",
    "paused withdrawals", "withdrawals paused", "freezes withdrawals",
    "withdrawals frozen", "frozen withdrawals", "defaulted", "defaults on", "default on",
    "debt default", "loan default", "bond default", "sovereign default", "payment default",
    # risk
    "war", "invasion", "invades", "invaded", "missile", "airstrike", "air strike",
    "drone strike", "military strike", "bombing", "terrorist", "terrorism", "coup",
    "martial law", "assassination", "assassinated", "nuclear strike", "nuclear test",
    "systemic risk", "black swan",
}


def term_weight(term: str) -> int:
    return 2 if term in STRONG else 1


def _weight(terms) -> int:
    return sum(term_weight(t) for t in terms)


# Phrases that take their words out of play: they count in no list. Each was
# a misfire in the stored articles ("pre-war levels", "Gears of War", "an
# emergency fund", MarketBeat's "has a 52-week low of $8.00 and a 52-week
# high of $23.61").
NEUTRAL = {
    "pre-war", "post-war", "war chest", "legal war", "protocol war", "talent war",
    "console war", "culture war", "gears of war", "star wars", "emergency fund",
    "emergency savings", "snow crash", "hack vc", "52-week low of", "52-week high of",
    "52 week low of", "52 week high of", "subject to final approval",
    "pending final approval", "analyst upgrades and downgrades", "upgrades and downgrades",
}
URGENT = {"breaking", "just in", "urgent", "alert", "developing", "halted", "emergency"}

CATEGORY_KEYWORDS = {
    "regulation": {"sec", "cftc", "regulation", "regulator", "lawsuit", "ban",
                   "compliance", "license", "crackdown", "legislation", "bill",
                   "regulatory", "licensed", "banning",
                   "securities and exchange commission"},
    "ETF": {"etf", "spot etf", "ishares", "grayscale", "inflow", "outflow", "aum"},
    "macro": {"cpi", "inflation", "gdp", "unemployment", "recession", "treasury",
              "yield", "dollar", "dxy", "tariff",
              "inflationary"},
    "Fed": {"fed", "fomc", "powell", "rate cut", "rate hike", "federal reserve"},
    "exchange": {"binance", "coinbase", "kraken", "okx", "bybit", "exchange",
                 "listing", "delisting", "withdrawal"},
    "hack": {"hack", "hacked", "exploit", "breach", "stolen", "attacker",
             "hacker", "exploited"},
    "liquidation": {"liquidation", "liquidated", "margin call", "leverage",
                    "leveraged"},
    "mining": {"miner", "miners", "mining", "hashrate", "difficulty"},
    "institutional": {"blackrock", "fidelity", "microstrategy", "strategy",
                      "institutional", "treasury allocation", "pension", "sovereign"},
    "earnings": {"earnings", "revenue", "eps", "guidance", "quarterly"},
    "equity_crypto_proxy": {"coin", "mstr", "riot", "mara", "hood", "sq", "pypl",
                            "coinbase", "robinhood", "marathon digital"},
    "market_structure": {"futures", "options", "open interest", "funding rate",
                         "basis", "spot volume", "order book"},
}

BTC_DIRECT = {"btc", "bitcoin", "satoshi", "sats"}
CRYPTO_GENERAL = {"crypto", "cryptocurrency", "digital asset", "eth", "ethereum",
                  "altcoin", "stablecoin", "defi", "blockchain", "web3", "token",
                  "cryptocurrencies", "ether", "tokenized", "tokenised",
                  "tokenization", "tokenisation"}

SOURCE_RELIABILITY = {
    "reuters": 0.9, "bloomberg": 0.9, "wsj": 0.9, "ap": 0.85, "cnbc": 0.8,
    "ft": 0.85, "financial times": 0.85, "coindesk": 0.7, "cointelegraph": 0.65,
    "the block": 0.7, "decrypt": 0.6, "twitter": 0.3, "x": 0.3, "reddit": 0.3,
}
# Godel names outlets in long form ("Reuters News", "Associated Press",
# "CNBC TV Transcripts", "Block, The"): a table name (or one of these
# aliases) counts when it appears as whole words in the source name.
SOURCE_ALIASES = {"associated press": "ap", "wall street journal": "wsj"}
SOURCE_EXACT_ONLY = {"x", "ft"}    # too short to find inside a name ("Ft. Worth ...")

_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
_KNOWN_TICKERS = {"BTC", "ETH", "SOL", "XRP", "SPY", "QQQ", "COIN", "MSTR",
                  "RIOT", "MARA", "GBTC", "IBIT", "FBTC", "HOOD", "SQ", "PYPL"}


def headline_hash(headline: str, date: str = "", time_s: str = "",
                  ticker: str = "") -> str:
    """Stable dedup key for a news row."""
    raw = f"{headline.strip().lower()}|{date}|{time_s}|{ticker}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _variants(term: str) -> set:
    """The term plus its regular plural: -es after s/x/z/ch/sh, else -s
    ('crashes', 'wars'). Never 'wares' for 'war' or 'routes' for 'rout'."""
    return {term, term + ("es" if term.endswith(("s", "x", "z", "ch", "sh")) else "s")}


@functools.lru_cache(maxsize=None)
def _matcher(lists: tuple):
    """(pattern, form -> listed term) for one or more term sets. Longest form
    first, so at any position the longest listed phrase is the one found."""
    forms: Dict[str, str] = {t: t for terms in lists for t in terms}
    for terms in lists:
        for t in terms:
            for v in _variants(t):
                forms.setdefault(v, t)
    alts = "|".join(re.escape(f) for f in sorted(forms, key=lambda f: (-len(f), f)))
    return re.compile(rf"(?<![a-z0-9])({alts})(?![a-z0-9])"), forms


def _hits(text: str, lexicon: Iterable[str]) -> List[str]:
    """Distinct lexicon entries found in (lower-case) text as whole words,
    sorted. The regular plural still counts ('etfs', 'wars')."""
    pattern, forms = _matcher((frozenset(lexicon),))
    return sorted({forms[m.group(1)] for m in pattern.finditer(text)})


def _sentiment_hits(text: str):
    """(bullish, bearish, risk) terms found, matched in ONE pass over all three
    lists plus NEUTRAL so the longest phrase wins: "ban lifted" is bullish, not
    the bearish "ban"; "pre-war" counts nowhere. A term on two lists ("hack")
    counts on both."""
    pattern, forms = _matcher((frozenset(BULLISH), frozenset(BEARISH),
                               frozenset(RISK_EVENT), frozenset(NEUTRAL)))
    found = {forms[m.group(1)] for m in pattern.finditer(text)}
    return sorted(found & BULLISH), sorted(found & BEARISH), sorted(found & RISK_EVENT)


def decide_sentiment(headline: str, detail: str = ""):
    """
    (sentiment, basis, bull, bear, risk). The headline decides: any risk term
    -> risk_event, else the heavier side by keyword weight (STRONG = 2). A
    headline with neither (or a tie) defers to the article, which needs
    ARTICLE_MIN_RISK_TERMS distinct risk terms, or ARTICLE_MIN_TERMS terms on
    one side and more than ARTICLE_MAJORITY times the other side - counted,
    not weighted, so one strong passing word can't decide a row. The terms
    returned are the ones from the text that decided (headline terms when
    nothing did), so score, confidence and explanation describe that text.
    """
    bull, bear, risk = _sentiment_hits(headline.lower())
    if risk:
        return "risk_event", "headline", bull, bear, risk
    if _weight(bull) != _weight(bear):
        side = "bullish" if _weight(bull) > _weight(bear) else "bearish"
        return side, "headline", bull, bear, risk
    if detail:
        a_bull, a_bear, a_risk = _sentiment_hits(detail.lower())
        if len(a_risk) >= ARTICLE_MIN_RISK_TERMS:
            return "risk_event", "article", a_bull, a_bear, a_risk
        for side, this, other in (("bullish", a_bull, a_bear), ("bearish", a_bear, a_bull)):
            if len(this) >= ARTICLE_MIN_TERMS and len(this) > ARTICLE_MAJORITY * len(other):
                return side, "article", a_bull, a_bear, a_risk
    return "neutral", "none", bull, bear, risk


def source_reliability(source: str) -> float:
    name = (source or "").strip().lower()
    if name in SOURCE_RELIABILITY:
        return SOURCE_RELIABILITY[name]
    name = re.sub(r"^(.*?),\s*the\b", r"the \1", name)      # "block, the" -> "the block"
    words = " " + re.sub(r"[^a-z0-9]+", " ", name).strip() + " "
    for key in sorted([*SOURCE_RELIABILITY, *SOURCE_ALIASES], key=lambda k: (-len(k), k)):
        if key not in SOURCE_EXACT_ONLY and f" {key} " in words:
            return SOURCE_RELIABILITY[SOURCE_ALIASES.get(key, key)]
    return 0.5


def interpret(headline: str, detail: str = "", source: str = "",
              ticker: str = "") -> dict:
    """Deterministic interpretation fields for one headline (+optional detail).
    Sentiment comes from the headline first (decide_sentiment); relevance,
    categories and entities describe the topic, so they read the full text."""
    text = f"{headline} {detail}".lower()

    sentiment, basis, bull, bear, risk = decide_sentiment(headline, detail)
    urgent = _hits(text, URGENT)

    # --- BTC relevance 0-100
    relevance = 0
    if _hits(text, BTC_DIRECT) or ticker.upper() == "BTC":
        relevance = 90
    elif _hits(text, CRYPTO_GENERAL):
        relevance = 60
    if _hits(text, CATEGORY_KEYWORDS["Fed"]) or _hits(text, CATEGORY_KEYWORDS["macro"]):
        relevance = max(relevance, 40)
    if _hits(text, CATEGORY_KEYWORDS["equity_crypto_proxy"]):
        relevance = max(relevance, 50)
    if _hits(text, CATEGORY_KEYWORDS["ETF"]) and relevance >= 40:
        relevance = min(100, relevance + 10)

    # --- direction / horizon
    if sentiment == "bullish":
        direction = "up"
    elif sentiment == "bearish":
        direction = "down"
    elif sentiment == "risk_event":
        direction = "down" if not bull else "mixed"
    else:
        direction = "none" if relevance < 30 else "mixed"

    cats = sorted(c for c, kws in CATEGORY_KEYWORDS.items() if _hits(text, kws)) or ["general"]
    if "hack" in cats or "liquidation" in cats:
        horizon = "intraday"
    elif "Fed" in cats or "macro" in cats:
        horizon = "1w"
    elif "ETF" in cats or "regulation" in cats:
        horizon = "3d"
    elif sentiment != "neutral":
        horizon = "1d"
    else:
        horizon = "unknown"

    # a word on both the bearish and risk lists ("seized") is ONE signal word;
    # a strong word counts as two
    signal = list(dict.fromkeys(bull + bear + risk))
    confidence = round(min(0.9, 0.25 + 0.15 * _weight(signal)), 2) if signal else 0.2

    entities = sorted({t for t in _TICKER_RE.findall(f"{headline} {detail}")
                       if t in _KNOWN_TICKERS} | ({ticker.upper()} if ticker else set()))

    explanation = (f"{sentiment} ({', '.join(signal[:3]) or 'no strong keywords'})"
                   f"{'' if basis == 'none' else ' from ' + basis} "
                   f"with BTC relevance {relevance}")

    return {
        "sentiment": sentiment,
        "sentiment_basis": basis,
        "btc_relevance": relevance,
        "impact_direction": direction,
        "impact_horizon": horizon,
        "confidence": confidence,
        "categories": ",".join(cats),
        "entities": ",".join(entities),
        "explanation": explanation,
        "_bull_weight": _weight(bull), "_bear_weight": _weight(bear),
        "_risk_weight": _weight(risk), "_urgent_hits": len(urgent),
    }


def news_alpha(sentiment_score: float, relevance_coeff: float, reliability: float,
               urgency: float, novelty: float) -> float:
    """final_news_alpha_coeff - the one place the formula lives."""
    alpha = sentiment_score * relevance_coeff * reliability * (0.5 + 0.5 * urgency) * novelty
    return round(max(-1.0, min(1.0, alpha)), 4)


def coefficients(interp: dict, source: str = "",
                 novelty: float = 1.0) -> dict:
    """
    Research coefficients (joinable to BTC observations later). Deterministic
    formula, all clamped, and trade_impact_enabled is ALWAYS False. The store
    sets novelty from the duplicate flags (NOVELTY_DUPLICATE for copies).
    """
    # net keyword weight, scaled so SENTIMENT_FULL_SCALE reaches +-1: one mild
    # word is +-0.25, one strong word +-0.5 (a risk term that is also bearish
    # subtracts on both lists)
    net = interp["_bull_weight"] - interp["_bear_weight"] - interp["_risk_weight"]
    cap = ARTICLE_MAX_SCORE if interp.get("sentiment_basis") == "article" else 1.0
    sentiment_score = max(-cap, min(cap, net / SENTIMENT_FULL_SCALE))

    relevance_coeff = interp["btc_relevance"] / 100.0
    reliability = source_reliability(source)
    urgency = min(1.0, 0.3 + 0.35 * interp["_urgent_hits"]) if interp["_urgent_hits"] else 0.1
    novelty = max(0.0, min(1.0, novelty))

    return {
        "news_sentiment_score": round(sentiment_score, 4),
        "btc_relevance_coeff": round(relevance_coeff, 4),
        "source_reliability_coeff": round(reliability, 4),
        "novelty_coeff": round(novelty, 4),
        "urgency_coeff": round(urgency, 4),
        "final_news_alpha_coeff": news_alpha(sentiment_score, relevance_coeff,
                                             reliability, urgency, novelty),
        "trade_impact_enabled": TRADE_IMPACT_ENABLED,   # always False
        "scoring_version": SCORING_VERSION,
    }
