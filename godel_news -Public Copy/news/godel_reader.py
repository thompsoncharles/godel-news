"""
Godel Terminal news reader (browser automation, research/reporting ONLY).

Reads the news panel on the AB4.1 layout tab of app.godelterminal.com:
discovers new headline rows, left-clicks to expand each new one, captures
the expanded detail text (scrolling the detail container itself, not the
whole page), and returns structured rows for news.store.

CREDENTIALS / SESSION (documented per build requirement 2):
  - Username/password come from local_settings.py GODEL_USERNAME /
    GODEL_PASSWORD (gitignored; see local_settings.example.py).
  - Environment variables GODEL_USERNAME / GODEL_PASSWORD override the file.
  - A persistent Chrome profile is kept in godel_profile/ (gitignored) so an
    authenticated session survives between runs and login is usually skipped.
  - If credentials are placeholders AND no live session exists in the
    profile, the reader fails gracefully with a clear message - it never
    hardcodes or logs secrets.

BROWSER MODES (GODEL_BROWSER_MODE env var or local_settings.py):
  attach (default) - connect to YOUR already-running Brave/Chrome over CDP
                     at GODEL_CDP_URL (default http://127.0.0.1:9222).
                     `python -m news login` opens that browser for you
                     (start_debug_browser); pass Cloudflare and log in there.
                     The reader finds an existing app.godelterminal.com tab
                     (or opens one), uses your logged-in AB4.1 session, and
                     NEVER attempts credential login. If remote debugging is
                     not reachable it fails gracefully with setup
                     instructions; close() detaches without touching your
                     tabs or closing your browser.
  launch           - own ChromeDriver Chrome with godel_profile/ (above).
                     Cloudflare's check cannot be passed in this browser,
                     so it only works while Cloudflare is not challenging.

SAFETY: imports selenium + news.controller only. No IBKR/order code, no
trading. The BTC paper routine treats every failure here as "news
unavailable" and continues.

DOM SELECTORS: the login/layout selectors are proven (reused from
core/controller.py, which the live PRT bot used). The news-panel selectors
below are BEST-EFFORT GUESSES pending a live DOM inspection - each is
marked TODO_SELECTOR and centralised in SELECTORS so tuning them after
inspecting the real AB4.1 layout is a one-place edit.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from news import config as _cfg

PROJECT_ROOT = _cfg.PROJECT_ROOT
PROFILE_DIR = _cfg.PROFILE_DIR
NEWS_LAYOUT = str(_cfg.setting("GODEL_NEWS_LAYOUT", "AB4.1"))   # bottom layout tab name
MAX_DETAIL_SCROLLS = 30
MAX_ARTICLES_PER_RUN = 25      # hard bound on the harvest loop (never spins)
MAX_RETURN_RETRIES = 4         # one per ladder rung: app-back, browser-back, Esc, AB4.1 tab
MAX_BLOCKED_RETRIES = 2        # per-headline attempts when an overlay eats the click
RETURN_WAIT_SECONDS = 8        # wait for the list to reappear after going back
PLACEHOLDER_MARKERS = ("your_email", "your_password", "example.com", "changeme")

# --- browser mode: "launch" (own Chrome + godel_profile/) or "attach"
# (connect to an ALREADY-RUNNING Brave/Chrome over CDP - your logged-in
# session, your tabs; no credential login is ever attempted in attach mode).
# Resolution order: env var > local_settings.py > default.
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
# a tab counts as the Godel tab if its URL contains this (covers e.g.
# https://app.godelterminal.com/?page=register&_gl=1*... - tracking params
# and page fragments vary, the host is the stable part)
GODEL_TAB_MATCH = "app.godelterminal.com"


def _setting(name: str, default: str) -> str:
    return str(_cfg.setting(name, default))


def browser_mode() -> str:
    """'attach' or 'launch' (GODEL_BROWSER_MODE; anything else -> attach,
    the only mode that survives a Cloudflare challenge)."""
    mode = _setting("GODEL_BROWSER_MODE", "attach").strip().lower()
    return mode if mode in ("launch", "attach") else "attach"


def browser_back_allowed() -> bool:
    """
    GODEL_ALLOW_BROWSER_BACK, default FALSE: driver.back() is never called
    unless explicitly enabled. The article view lives INSIDE the AB4.1
    panel, so browser history back navigates the (possibly attached, i.e.
    the user's own) browser tab without closing the article - all return
    navigation goes through in-app controls instead.
    """
    return _setting("GODEL_ALLOW_BROWSER_BACK", "false").strip().lower() in \
        ("true", "1", "yes")


def cdp_url() -> str:
    return _setting("GODEL_CDP_URL", DEFAULT_CDP_URL).rstrip("/")


def find_brave_exe(candidates: Optional[List[str]] = None) -> Optional[str]:
    """First existing Brave executable among the standard install locations
    (per-machine x64, per-machine x86, per-user). Returns None when Brave is
    not installed - callers fall back to the canonical Program Files path in
    printed instructions."""
    if candidates is None:
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LocalAppData", "")]
        candidates = [
            os.path.join(r, "BraveSoftware", "Brave-Browser", "Application",
                         "brave.exe") for r in roots if r]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _cdp_port(url: Optional[str] = None) -> str:
    """Port component of the CDP endpoint ('9222' when unparseable)."""
    tail = (url or cdp_url()).rsplit(":", 1)[-1].strip("/")
    return tail if tail.isdigit() else "9222"


def attach_instructions(url: Optional[str] = None) -> str:
    """Exact, copy-pasteable recovery steps when the CDP endpoint is down.
    PowerShell syntax on purpose - that is the shell this project documents."""
    endpoint = (url or cdp_url())
    port = _cdp_port(endpoint)
    brave = find_brave_exe() or (
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")
    return (
        f"Remote debugging is not reachable at {endpoint}. Attach mode needs "
        "Brave (or Chrome) started WITH remote debugging - it cannot attach to "
        "an ordinary already-running window retroactively.\n"
        "1. Close ALL Brave windows first (a running instance makes the new "
        "one hand off and exit, silently ignoring the flag).\n"
        "2. Relaunch with the debugging port (PowerShell):\n"
        f'   & "{brave}" --remote-debugging-port={port}\n'
        "3. If the port still never opens: Chromium 136+ (incl. current "
        "Brave) refuses CDP on the DEFAULT profile. Use a dedicated "
        "debugging profile instead - log in to Godel there once, it stays "
        "logged in:\n"
        f'   & "{brave}" --remote-debugging-port={port} '
        '--user-data-dir="$env:LOCALAPPDATA\\BraveSoftware\\Brave-CDP"\n'
        f"4. Verify it answers:  curl.exe http://127.0.0.1:{port}/json/version\n"
        "then rerun this command. Attach mode never enters credentials and "
        "never closes your browser or tabs. Alternatively use "
        "--browser launch (or GODEL_BROWSER_MODE=launch) for the bot's own "
        "persistent godel_profile/ Chrome instead."
    )


def cdp_available(url: Optional[str] = None, timeout: float = 2.0) -> bool:
    """True when a debuggable browser answers at the CDP endpoint."""
    import requests
    try:
        r = requests.get(f"{url or cdp_url()}/json/version", timeout=timeout)
        return r.ok
    except Exception:
        return False


def cdp_browser_version(url: Optional[str] = None) -> Optional[str]:
    """Chromium version of the browser on the CDP port, e.g. '156.0.8078.80'
    (Brave reports its Chromium version here, not its own)."""
    import requests
    try:
        info = requests.get(f"{url or cdp_url()}/json/version", timeout=2).json()
    except Exception:
        return None
    m = re.search(r"/(\d+(?:\.\d+)+)", info.get("Browser", ""))
    return m.group(1) if m else None


def installed_majors(exe: str) -> set:
    """Chromium majors of an installed browser, read from the version
    folders next to its exe (Brave 156.1.98.63 -> 156, Chrome
    157.0.8103.26 -> 157; two folders exist mid-update)."""
    try:
        names = os.listdir(os.path.dirname(exe))
    except OSError:
        return set()
    return {n.split(".")[0] for n in names
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", n)}


def attach_binary(version: Optional[str]) -> Optional[str]:
    """
    The installed browser exe whose version matches the browser on the CDP
    port. Selenium Manager picks ChromeDriver from this binary's version;
    left unset it matches the installed Chrome instead, and attaching a
    Chrome-157 driver to Brave-156 fails with an empty
    SessionNotCreatedException. GODEL_CDP_BROWSER_BINARY overrides.
    """
    override = _setting("GODEL_CDP_BROWSER_BINARY", "")
    if override:
        return override
    if not version:
        return None
    major = version.split(".")[0]
    for exe in (find_brave_exe(), find_chrome_exe()):
        if exe and major in installed_majors(exe):
            return exe
    return None


def match_godel_tab(urls: List[str], needle: str = GODEL_TAB_MATCH) -> Optional[int]:
    """Index of the first tab URL that is a Godel Terminal tab, else None."""
    for i, u in enumerate(urls):
        if needle in (u or ""):
            return i
    return None


def choose_godel_tab(urls: List[str], news_visible,
                     needle: str = GODEL_TAB_MATCH):
    """
    Pick the tab to attach to. Among tabs whose URL matches the Godel host,
    prefer the first where the AB4.1 news panel is already visible
    (news_visible(i) -> bool, called only on matching tabs); otherwise the
    first Godel tab; (None, 'no_godel_tab') when there are none.
    Returns (index_or_None, reason).
    """
    idxs = [i for i, u in enumerate(urls) if needle in (u or "")]
    if not idxs:
        return None, "no_godel_tab"
    for i in idxs:
        try:
            if news_visible(i):
                return i, "news_panel_visible"
        except Exception:
            continue
    return idxs[0], "first_godel_tab"


def normalize_label(text: Optional[str]) -> str:
    """Whitespace-normalized element text for layout-tab comparisons."""
    return " ".join((text or "").split())


def should_skip_layout_click(news_visible: bool, active_layout: Optional[str],
                             wanted: str = NEWS_LAYOUT) -> bool:
    """
    The whole point of attach mode: if the attached page already shows the
    news panel - or the active layout tab already reads AB4.1 - proceed
    without requiring a successful tab click. Clicking is only for getting
    somewhere we are not.
    """
    if news_visible:
        return True
    return wanted.lower() in normalize_label(active_layout).lower()


# --------------------------------------------------- ticker-group routing
# The NEWS panel carries its OWN ticker-group dropdown (AB4.1 = BTC/crypto
# universe, SP400 = SPY + 399 largest SPY holdings). This is a DIFFERENT
# control from anything in Quote Monitor and from the bottom layout tabs
# (NEWS_LAYOUT/_click_layout_tab above) - group-switch code below is scoped
# to search only inside the container whose heading reads exactly "NEWS"
# and must never fall back to a document-wide search.
NEWS_PANEL_HEADING = "NEWS"
# The dropdown lists your Godel WATCHLISTS by name, so these must be the
# exact watchlist names in your account (e.g. GODEL_GROUP_BTC = "quantAB4.1").
ASSET_GROUPS = {"btc": _setting("GODEL_GROUP_BTC", "AB4.1"),
                "spy": _setting("GODEL_GROUP_SPY", "SP400")}
# Groups the dropdown may be SHOWING when we go looking for it - used only to
# recognise the control, never to choose what to scrape (selection still
# needs an exact match on the requested group and fails closed). DOW30 is
# here because `python -m news dow30` leaves the panel on that watchlist;
# add others with GODEL_EXTRA_GROUP_LABELS = "A,B".
KNOWN_GROUP_LABELS_EXTRA = ("DOW30",)
GROUP_SWITCH_WAIT_SECONDS = 12
GROUP_STABILIZE_WAIT_SECONDS = 8
DEFAULT_ASSET_FALLBACK = "btc"

# Crypto tickers commonly seen on the AB4.1 group - used only as a SANITY
# check on captured rows after a verified group switch, never as the
# switch verification itself (the dropdown's own displayed label is
# authoritative for that).
CRYPTO_TICKER_HINTS = {
    "BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "BNB", "LTC", "AVAX", "DOT",
    "MATIC", "LINK", "SHIB", "TRX", "XLM", "ATOM", "UNI", "ETC", "FIL",
    "APT", "ARB", "OP", "NEAR", "ICP", "HBAR", "SUI", "COIN", "MSTR",
    "MARA", "RIOT", "CLSK", "HUT", "BITF", "CIFR", "WULF", "IBIT", "FBTC",
    "GBTC", "ETHE", "BITO", "BTCUSD", "ETHUSD", "XBT", "BITI",
}


def known_group_labels() -> Tuple[str, ...]:
    """Every group label the NEWS dropdown is recognised by."""
    extra = [s.strip() for s in _setting("GODEL_EXTRA_GROUP_LABELS", "").split(",")
             if s.strip()]
    return tuple(dict.fromkeys(
        [*ASSET_GROUPS.values(), *KNOWN_GROUP_LABELS_EXTRA, *extra]))


def resolve_group(asset: str) -> str:
    """asset -> Godel NEWS-panel ticker-group label. Raises for anything
    unrecognized rather than guessing a default group."""
    key = (asset or "").strip().lower()
    if key not in ASSET_GROUPS:
        raise ValueError(
            f"unknown asset '{asset}' - expected one of {sorted(ASSET_GROUPS)}")
    return ASSET_GROUPS[key]


def default_asset() -> str:
    """Per-repo default asset (GODEL_NEWS_DEFAULT_ASSET env var or
    local_settings.py; 'btc' if unset)."""
    return _setting("GODEL_NEWS_DEFAULT_ASSET", DEFAULT_ASSET_FALLBACK).strip().lower()


def group_label_matches(displayed: Optional[str], wanted_group: str) -> bool:
    """
    Exact match after whitespace normalization and case-folding, OR
    wanted_group as a standalone leading token followed by a
    non-alphanumeric boundary (a dropdown may decorate the label, e.g.
    'SP400 (400)'). NEVER a bare substring match - 'SP4000' or
    'ABAB4.1X' must not match 'SP400'/'AB4.1'. Fail-closed by
    construction: ambiguous/empty input never matches.
    """
    norm = normalize_label(displayed).lower()
    wanted = normalize_label(wanted_group).lower()
    if not norm or not wanted:
        return False
    if norm == wanted:
        return True
    if norm.startswith(wanted):
        rest = norm[len(wanted):]
        return not rest or not rest[0].isalnum()
    return False


def row_group_consistency(rows: List[dict], wanted_group: str,
                          spy_universe: Optional[set] = None) -> Tuple[float, int]:
    """
    Fraction of rows whose ticker plausibly belongs to wanted_group's
    universe, and the number of tickers judged (0 tickers -> (0.0, 0), not
    a failure). A SANITY signal only - the verified dropdown label is
    authoritative; this never overrides it and is not itself sufficient
    proof of group identity, only a defense against a UI that updates its
    own label before the underlying row data has actually refreshed.
    """
    tickers = [(r.get("ticker") or "").strip().upper() for r in rows]
    tickers = [t for t in tickers if t]
    if not tickers:
        return 0.0, 0
    if wanted_group == ASSET_GROUPS["btc"]:
        hits = sum(1 for t in tickers if t in CRYPTO_TICKER_HINTS)
    elif spy_universe:
        hits = sum(1 for t in tickers if t in spy_universe)
    else:
        hits = sum(1 for t in tickers if t not in CRYPTO_TICKER_HINTS)
    return hits / len(tickers), len(tickers)


# ------------------------------------------------------------- TODO selectors
# Centralised so a live DOM inspection only needs to edit this dict.
SELECTORS = {
    # TODO_SELECTOR: container of the news window on the AB4.1 layout.
    # Godel windows are divs with id ending in "-window" (see DOMMonitor);
    # the news window is assumed to contain a table/list of headline rows.
    "news_window": "div.resize.inline-block.absolute[id$='-window']",
    # TODO_SELECTOR: one headline row inside the news window.
    "news_row": "tr, div[class*='news-row'], div[role='row']",
    # TODO_SELECTOR: cells within a row: headline/date/time/ticker/source.
    "row_cells": "td, div[class*='cell']",
    # TODO_SELECTOR: expanded detail container after clicking a row
    # (drawer / modal / inline expansion).
    "detail_panel": "div[class*='drawer'], div[role='dialog'], div[class*='expanded'], div[class*='detail']",
    # TODO_SELECTOR: the app's own back control inside an article detail view
    # (preferred over browser history back when present).
    "app_back": ("button[aria-label*='back' i], button[title*='back' i], "
                 "div[class*='back-button'], button[class*='back']"),
}


# ----------------------------------------------------- pure, testable helpers
def merge_scroll_chunks(chunks: List[str]) -> str:
    """
    Combine text captured across scroll steps, appending only content not
    already captured (scroll windows overlap, so later chunks repeat earlier
    lines). Line-based: a line is added once, order preserved.
    """
    seen, out = set(), []
    for chunk in chunks:
        for line in (chunk or "").splitlines():
            key = line.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(line.strip())
    return "\n".join(out)


def capture_scrolling_text(get_text, scroll_once,
                           max_steps: int = MAX_DETAIL_SCROLLS) -> Tuple[str, int]:
    """
    Scroll a container until the bottom (scroll position stops changing) or
    max_steps, capturing text after each step. Duck-typed for testability:
      get_text()    -> currently visible text of the container
      scroll_once() -> new scrollTop (int) after one scroll step
    Returns (merged_text, steps_used).
    """
    chunks = [get_text()]
    last_pos = -1
    steps = 0
    for _ in range(max_steps):
        pos = scroll_once()
        steps += 1
        chunks.append(get_text())
        if pos == last_pos:      # bottom reached / no movement
            break
        last_pos = pos
    return merge_scroll_chunks(chunks), steps


# ------------------------------------------------- article identity (pure)
def normalize_fuzzy(text: Optional[str]) -> str:
    """Lowercase, strip punctuation, collapse whitespace - for fuzzy compares."""
    import re
    text = (text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def title_similarity(expected: str, captured: str) -> float:
    """
    Fuzzy similarity of headline vs captured article title in [0, 1].
    Handles truncation ('...' titles) by also comparing the shorter string
    against the aligned prefix of the longer one.
    """
    from difflib import SequenceMatcher
    a, b = normalize_fuzzy(expected), normalize_fuzzy(captured)
    if not a or not b:
        return 0.0
    full = SequenceMatcher(None, a, b).ratio()
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    prefix = SequenceMatcher(None, short, long_[:len(short) + 10]).ratio()
    contained = 1.0 if short in long_ else 0.0
    return max(full, prefix, contained)


def body_signature(text: Optional[str]) -> str:
    """Stable signature of an article body: hash of the normalized opening
    (first viewport is a prefix of the full capture, so signatures computed
    pre- and post-scroll agree)."""
    import hashlib
    return hashlib.sha256(normalize_fuzzy(text)[:800].encode()).hexdigest()[:16]


def extract_article_view(text: Optional[str]) -> dict:
    """
    Parse Godel's article view text into {title, matched, source}. Live
    format (from the --debug-back DOM dump):
        Back / Export PDF                (header controls)
        <article title>
        Matched:COIN
        Source: Cryptopolitan ...
        <body...>
    """
    title, matched, source = "", "", ""
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s in ("Back", "Export PDF", "News"):
            continue
        low = s.lower()
        if low.startswith("matched:"):
            matched = s.split(":", 1)[1].strip()
        elif low.startswith("source:"):
            source = s.split(":", 1)[1].strip()
        elif not title:
            if _looks_like_photo_credit(s):
                continue      # hero-image caption above the real title
            title = s
        if title and matched and source:
            break
    return {"title": title, "matched": matched, "source": source}


_CREDIT_PREFIXES = ("photo by ", "photo:", "photograph:", "image:",
                    "image by ", "image credit", "photo credit",
                    "illustration by ", "(photo", "(image", "credit:")
_CREDIT_AGENCIES = ("getty images", "unsplash", "shutterstock",
                    "istock", "afp via", "associated press photo")


def _looks_like_photo_credit(line: str) -> bool:
    """Live AB4.1 failure mode (2026-07-19): the scrollable article body
    starts with the hero image's credit line ('Photo by Colin Watts')
    ABOVE the real title, which broke title-identity scoring. Short
    credit/caption lines are never article titles."""
    low = line.strip().lower()
    if len(low) > 80:
        return False
    return low.startswith(_CREDIT_PREFIXES) or \
        any(a in low for a in _CREDIT_AGENCIES)


MATCH_SCORE_THRESHOLD = 0.75
OK_SCORE_FLOOR = 0.60          # no capture may be status=ok below this score
TABLE_HEADER_FINGERPRINT = "headline date time ticker source"


def looks_like_news_table(text: Optional[str]) -> bool:
    """
    True when a candidate container is the news TABLE, not an article:
    either the column-header line ('Headline Date Time Ticker Source') is
    present, or the text is dominated by row-like 'date time' patterns.
    The live failure mode captured 166k+ chars of table this way.
    """
    if not text:
        return False
    import re
    if TABLE_HEADER_FINGERPRINT in normalize_fuzzy(text[:600]):
        return True
    datish = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}", text[:4000])
    return len(datish) >= 5


def article_container_rejection_reason(
        text: Optional[str], expected_headline: str = "") -> Optional[str]:
    """Return why a DOM candidate is not an article container, or None."""
    text = text or ""
    if not text.strip():
        return "empty"
    if looks_like_news_table(text):
        return "news_table_fingerprint"

    view = extract_article_view(text)
    normalized_title = normalize_fuzzy(view["title"])
    if TABLE_HEADER_FINGERPRINT in normalized_title:
        return "news_table_title"

    opening = normalize_fuzzy(text[:2500])
    has_article_metadata = any(marker in opening for marker in (
        "matched", "source", "publication date",
    ))
    score = title_similarity(expected_headline, view["title"])
    if not has_article_metadata and score < OK_SCORE_FLOOR:
        return "missing_article_markers"
    return None


def detail_text_for_status(status: str, text: str) -> str:
    """Only validated article captures are allowed into CSV/Excel output."""
    return text if status == "ok" else ""


def article_matches_expected(expected: dict, article_view: dict) -> Tuple[bool, float]:
    """
    Does the open article belong to the clicked headline? Fuzzy, not exact:
    title similarity >= threshold OR (source agreement AND the expected
    date/time appearing in the view) counts as a match.
    Returns (matched, title_similarity_score).
    """
    score = title_similarity(expected.get("headline", ""),
                             article_view.get("title", ""))
    if score >= MATCH_SCORE_THRESHOLD:
        return True, score
    exp_source = normalize_fuzzy(expected.get("source", ""))
    got_source = normalize_fuzzy(article_view.get("source", ""))
    source_ok = bool(exp_source) and (exp_source in got_source or got_source in exp_source)
    text_norm = normalize_fuzzy(article_view.get("text", ""))
    date_ok = bool(expected.get("date")) and \
        normalize_fuzzy(expected["date"]) in text_norm
    if source_ok and date_ok:
        return True, score
    return False, score


def classify_capture(expected: dict, captured_title: str, text: str,
                     prev_body_sig: Optional[str],
                     same_headline_as_prev: bool = False) -> Tuple[str, float, str]:
    """
    Decide the detail_status of a finished capture (pure, unit-tested):
      - captured text is the news TABLE (header fingerprint / row-dominated)
                                    -> article_container_not_found
      - article view doesn't belong to the clicked headline, or the match
        score is below OK_SCORE_FLOOR (0.60 - nothing weaker may be 'ok')
                                    -> article_identity_mismatch
      - body identical to the PREVIOUS article's under a different headline
                                    -> suspicious_same_body
      - otherwise                   -> ok
    Returns (status, match_score, body_sig).
    """
    sig = body_signature(text)
    matched, score = article_matches_expected(
        expected, {"title": captured_title, "text": text})
    if looks_like_news_table(text) or \
            TABLE_HEADER_FINGERPRINT in normalize_fuzzy(captured_title):
        return "article_container_not_found", score, sig
    if not text.strip():
        return "headline_only", score, sig
    if not matched or score < OK_SCORE_FLOOR:
        return "article_identity_mismatch", score, sig
    if prev_body_sig is not None and sig == prev_body_sig and not same_headline_as_prev:
        return "suspicious_same_body", score, sig
    return "ok", score, sig


def row_hash(row: dict) -> str:
    """Headline-identity hash for dedup-driven iteration (never row index)."""
    from news.interpreter import headline_hash
    return headline_hash(row.get("headline", ""), row.get("date", ""),
                         row.get("time", ""), row.get("ticker", ""))


def next_unprocessed(rows: List[dict], processed: set) -> Optional[dict]:
    """First visible row whose headline hash is not processed yet."""
    for row in rows:
        if row_hash(row) not in processed:
            return row
    return None


def harvest_articles(reader, processed: set,
                     max_articles: int = MAX_ARTICLES_PER_RUN) -> List[dict]:
    """
    Read new articles one at a time, dedup-driven and stale-element-proof:

        loop (bounded by max_articles):
            re-query visible rows            <- fresh DOM every pass
            pick first row not yet processed (by headline hash, not index)
            expand + capture it
            if the article view replaced the news list:
                return to the list (app back > browser back, bounded retries)
                on failure: mark detail_status=return_failed, stop cleanly

    `reader` is duck-typed (read_news_rows / expand_and_capture /
    return_to_news_list) so this loop is unit-testable without a browser.
    Never raises: the BTC paper routine depends on that.
    """
    results: List[dict] = []
    processed = set(processed)
    blocked_attempts: Dict[str, int] = {}
    loops = 0
    while len(results) < max_articles and loops < max_articles * (MAX_BLOCKED_RETRIES + 1):
        loops += 1
        try:
            rows = reader.read_news_rows()
        except Exception as e:
            logger.warning("news row re-query failed ({}); stopping harvest", e)
            break
        row = next_unprocessed(rows, processed)
        if row is None:
            break
        h = row_hash(row)
        try:
            rec = reader.expand_and_capture(row)
        except Exception as e:
            logger.warning("expand raised ({}); recording expand_failed", e)
            rec = {**{k: v for k, v in row.items() if k != "_element"},
                   "detail_status": "expand_failed", "detail_text": ""}

        if rec.get("detail_status") == "blocked_by_overlay":
            # an article/detail overlay owns the click - clear it and retry
            # this SAME headline (by hash) with freshly-queried elements
            blocked_attempts[h] = blocked_attempts.get(h, 0) + 1
            try:
                cleared = reader.return_to_news_list()
            except Exception:
                cleared = False
            if blocked_attempts[h] < MAX_BLOCKED_RETRIES and cleared:
                continue                        # not processed - try again
            if not cleared:
                rec["detail_status"] = "return_failed"
            processed.add(h)
            results.append(rec)
            logger.warning("row stayed {} after {} attempt(s); stopping "
                           "harvest cleanly with {} article(s) kept",
                           rec["detail_status"], blocked_attempts[h], len(results))
            break

        processed.add(h)
        results.append(rec)
        try:
            returned = reader.return_to_news_list()
        except Exception as e:
            logger.warning("return navigation raised ({}); stopping harvest", e)
            returned = False
        if not returned:
            rec["detail_status"] = "return_failed"
            logger.warning("could not return to the {} news list; stopping "
                           "harvest cleanly after {} article(s)",
                           NEWS_LAYOUT, len(results))
            break
    return results


def parse_news_row(cells: List[str]) -> Optional[dict]:
    """
    Map a row's cell texts to a structured record. Godel's column order is
    assumed headline-first with date/time/ticker/source in the remaining
    cells (TODO_SELECTOR: confirm column order on the live AB4.1 layout).
    Rows without a plausible headline are dropped.
    """
    cells = [c.strip() for c in cells if c and c.strip()]
    if not cells:
        return None
    header_tokens = set(normalize_fuzzy(" ".join(cells)).split())
    column_names = {"headline", "date", "time", "ticker", "source"}
    if len(header_tokens & column_names) >= 4 and len(header_tokens) <= 8:
        return None                         # table header, never an article
    headline = max(cells, key=len)          # longest cell is the headline
    if len(headline) < 8:
        return None
    rest = [c for c in cells if c != headline]
    date = next((c for c in rest if any(ch.isdigit() for ch in c) and
                 ("-" in c or "/" in c)), "")
    time_s = next((c for c in rest if ":" in c and sum(ch.isdigit() for ch in c) >= 3), "")
    ticker = next((c for c in rest if c.isupper() and 2 <= len(c) <= 6), "")
    source = next((c for c in rest if c not in (date, time_s, ticker) and
                   not any(ch.isdigit() for ch in c)), "")
    return {"headline": headline, "date": date, "time": time_s,
            "ticker": ticker, "source": source}


# ------------------------------------------------------------- credentials
def load_credentials() -> Optional[Tuple[str, str]]:
    """Env vars first, then gitignored local_settings.py. None = not configured."""
    user = _setting("GODEL_USERNAME", "")
    pw = _setting("GODEL_PASSWORD", "")
    blob = f"{user}{pw}".lower()
    if not user or not pw or any(m in blob for m in PLACEHOLDER_MARKERS):
        return None
    return user, pw


# ------------------------------------------------------- Cloudflare check
# Godel sits behind Cloudflare. When Cloudflare decides a browser needs
# checking it serves its own page ("Just a moment..." / "Verify you are
# human") INSTEAD of the app - no Login button, no terminal - so the old
# flow waited 15 s for a Login button that never came and died with a bare
# "Login timeout". The check is for a person to pass, and it cannot be
# passed inside a ChromeDriver-launched browser: Cloudflare sees the
# automation and loops the checkbox forever, even for a human click. So
# launch mode just reports it; the fix is attach mode, where YOU open a
# normal browser (start_debug_browser), pass the check and log in, and the
# reader only attaches afterwards. Nothing here tries to get past the check.
CLOUDFLARE_TITLE_MARKERS = ("just a moment", "attention required",
                            "verify you are human", "security check")
CLOUDFLARE_DOM_MARKERS = ("challenges.cloudflare.com", "cf-turnstile",
                          "cf-chl-widget", "cf_chl_opt")
CLOUDFLARE_MESSAGE = (
    "cloudflare_challenge: Godel is showing Cloudflare's 'verify you are "
    "human' page instead of the terminal. A browser started by ChromeDriver "
    "cannot pass it (the checkbox loops even when you click it). Use attach "
    "mode: run `run_news.bat login`, pass the check and log in in the Brave "
    "window it opens, leave that window open, then run the scrape. "
    "local_settings.py: GODEL_BROWSER_MODE = \"attach\".")
CLOUDFLARE_ATTACH_MESSAGE = (
    "cloudflare_challenge: the attached Godel tab is on Cloudflare's 'verify "
    "you are human' page. Complete the check and log in in that browser "
    "window, then re-run.")


def cdp_profile_dir() -> str:
    """Dedicated profile for the debuggable browser. Chromium 136+ ignores
    --remote-debugging-port on the DEFAULT profile, so it must be separate;
    it keeps its own Godel login between runs."""
    default = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                           "BraveSoftware", "Brave-CDP")
    return _setting("GODEL_CDP_PROFILE_DIR", default)


def find_chrome_exe() -> Optional[str]:
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LocalAppData", "")]
    for r in roots:
        c = os.path.join(r, "Google", "Chrome", "Application", "chrome.exe")
        if r and os.path.isfile(c):
            return c
    return None


def start_debug_browser(url: str = None, wait_s: float = 20.0) -> dict:
    """
    Open an ordinary Brave (Chrome if Brave is missing) window on the
    dedicated profile with the CDP port enabled - a plain program launch,
    not ChromeDriver, so it is the same browser you would open yourself.
    No-op when something already answers on the CDP port.
    """
    import subprocess

    if cdp_available():
        return {"started": False, "reason": "already running", "cdp": cdp_url()}
    exe = find_brave_exe() or find_chrome_exe()
    if exe is None:
        raise RuntimeError("neither Brave nor Chrome found - install one, or "
                           "set GODEL_CDP_URL to a browser you started yourself")
    profile = cdp_profile_dir()
    if "brave" not in exe.lower() and "BraveSoftware" in profile:
        profile = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                               "Google", "Chrome-CDP")
    subprocess.Popen([exe, f"--remote-debugging-port={_cdp_port()}",
                      f"--user-data-dir={profile}", "--new-window",
                      url or _cfg.GODEL_URL])
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if cdp_available(timeout=1.0):
            return {"started": True, "browser": exe, "profile": profile,
                    "cdp": cdp_url()}
        time.sleep(1)
    raise RuntimeError(f"started {exe} but nothing answers at {cdp_url()} - "
                       "if Brave was already open on that profile without "
                       "the debug port, close it and retry.\n"
                       + attach_instructions())


def session_state(driver) -> str:
    """'terminal' (logged in), 'login' (Godel login page), 'cloudflare'
    (challenge page or widget blocking the app) or 'unknown' (still loading,
    or a page this code does not recognise)."""
    from selenium.webdriver.common.by import By
    try:
        login = driver.find_elements(By.XPATH, "//button[text()='Login']")
        if driver.find_elements(By.ID, "terminal-input") and not login:
            return "terminal"
        title = (driver.title or "").lower()
        if any(m in title for m in CLOUDFLARE_TITLE_MARKERS):
            return "cloudflare"
        if login:
            return "login"
        # checked last: only counts when neither the app nor its login
        # page is showing, so an embedded widget can't mask a usable page
        if cloudflare_widget_present(driver):
            return "cloudflare"
    except Exception:
        pass
    return "unknown"


def cloudflare_widget_present(driver) -> bool:
    try:
        src = driver.page_source or ""
    except Exception:
        return False
    return any(m in src for m in CLOUDFLARE_DOM_MARKERS)


def settle_session_state(driver, timeout_s: float = 12.0) -> str:
    """Poll until the page shows something recognisable. Cloudflare's
    non-interactive check often clears by itself within a few seconds."""
    deadline = time.time() + timeout_s
    state = "unknown"
    while time.time() < deadline:
        state = session_state(driver)
        if state in ("terminal", "login"):
            return state
        time.sleep(1)
    return state


# ------------------------------------------------------------- the reader
class GodelNewsReader:
    """
    Browser session over the existing Godel controller. Two modes:
      launch           - own Chrome with the persistent godel_profile/
      attach (default) - CDP-attach to an already-running Brave/Chrome
                         (GODEL_BROWSER_MODE=attach, GODEL_CDP_URL endpoint);
                         uses YOUR logged-in session, never enters credentials
    """

    def __init__(self, headless: Optional[bool] = None, mode: Optional[str] = None):
        from news.controller import GodelTerminalController

        self.mode = (mode or browser_mode()).lower()
        self._last_body_sig: Optional[str] = None   # stale-capture detector

        class _ProfileController(GodelTerminalController):
            # identical to the proven controller, plus a persistent profile
            def connect(self):
                from selenium import webdriver
                from news.controller import DOMMonitor
                options = webdriver.ChromeOptions()
                if self.headless:
                    options.add_argument("--headless=new")
                options.add_argument(f"--user-data-dir={PROFILE_DIR}")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--window-size=1920,1080")
                options.add_argument("--log-level=3")
                options.add_experimental_option("excludeSwitches", ["enable-logging"])
                self.driver = webdriver.Chrome(options=options)
                self.driver.get(self.url)
                self.dom_monitor = DOMMonitor(self.driver)
                time.sleep(3)

        class _AttachController(GodelTerminalController):
            # attach to a running browser over CDP: no profile, no navigation
            # of the user's current tab, no window management on connect
            def connect(self):
                from selenium import webdriver
                from news.controller import DOMMonitor
                options = webdriver.ChromeOptions()
                address = cdp_url().replace("http://", "").replace("https://", "")
                options.add_experimental_option("debuggerAddress", address)
                version = cdp_browser_version()
                binary = attach_binary(version)
                if binary:
                    options.binary_location = binary   # driver version only
                try:
                    self.driver = webdriver.Chrome(options=options)
                except Exception as e:
                    raise RuntimeError(
                        f"could not attach to the browser at {cdp_url()} "
                        f"(browser version {version or 'unknown'}, driver "
                        f"matched to {binary or 'installed Chrome'}): "
                        f"{type(e).__name__}: {str(e).strip() or 'no message'}. "
                        "This is usually a ChromeDriver/browser version "
                        "mismatch - set GODEL_CDP_BROWSER_BINARY in "
                        "local_settings.py to the .exe of the browser you "
                        "opened.") from e
                self.dom_monitor = DOMMonitor(self.driver)

        if self.mode == "attach":
            self.controller = _AttachController(headless=False)
        else:
            PROFILE_DIR.mkdir(exist_ok=True)
            self.controller = _ProfileController(headless=headless)

    # -- session -----------------------------------------------------------
    def start(self) -> dict:
        return self._start_attach() if self.mode == "attach" else self._start_launch()

    def _start_launch(self) -> dict:
        """Own browser; log in only if the saved profile session has expired."""
        self.controller.connect()
        drv = self.controller.driver
        state = settle_session_state(drv)
        if state == "cloudflare":
            raise RuntimeError(CLOUDFLARE_MESSAGE)
        if state == "terminal":
            logger.info("Godel session restored from saved profile - no login needed")
            return {"session": "restored"}
        creds = load_credentials()
        if creds is None:
            raise RuntimeError(
                "Godel credentials not configured: set GODEL_USERNAME/GODEL_PASSWORD "
                "in local_settings.py (gitignored) or as environment variables, "
                "or switch to attach mode and log in with `run_news.bat login`.")
        try:
            self.controller.login(*creds)
        except Exception as e:
            # a Turnstile check can also appear inside the login form
            if session_state(drv) == "cloudflare" or cloudflare_widget_present(drv):
                raise RuntimeError(CLOUDFLARE_MESSAGE) from e
            raise RuntimeError(
                f"Godel login failed (page state: {session_state(drv)}, "
                f"title: {drv.title!r}). Switch to attach mode and log in "
                "with `run_news.bat login`.") from e
        return {"session": "logged_in"}

    def _start_attach(self) -> dict:
        """Attach to the user's running Brave/Chrome. NEVER enters credentials."""
        from selenium.webdriver.common.by import By

        if not cdp_available():
            raise RuntimeError("no debuggable browser is open - run "
                               "`run_news.bat login` first.\n" + attach_instructions())
        self.controller.connect()
        drv = self.controller.driver

        # find existing Godel tabs (URL host match); among several, prefer
        # the one where the AB4.1 news panel is actually visible
        urls = []
        for handle in drv.window_handles:
            drv.switch_to.window(handle)
            urls.append(drv.current_url)

        def _news_visible_on(i: int) -> bool:
            drv.switch_to.window(drv.window_handles[i])
            return self.news_table_visible()

        chosen, reason = choose_godel_tab(urls, _news_visible_on)
        opened = False
        if chosen is not None:
            drv.switch_to.window(drv.window_handles[chosen])
        else:
            drv.switch_to.new_window("tab")
            drv.get("https://app.godelterminal.com/")
            opened = True
        time.sleep(3)
        logger.info("attach: tab selection={}; attached_tab_url={} "
                    "news_table_visible={}", reason,
                    drv.current_url, self.news_table_visible())

        # attach mode does not log in - the session must already be live
        if session_state(drv) == "cloudflare":
            raise RuntimeError(CLOUDFLARE_ATTACH_MESSAGE)
        if drv.find_elements(By.XPATH, "//button[text()='Login']"):
            raise RuntimeError(
                "attached Godel tab is not logged in. Log in manually in the "
                "attached browser (it stays logged in for future runs) - attach "
                "mode never enters credentials.")
        return {"session": "attached", "tab": "existing" if not opened else "opened",
                "cdp": cdp_url()}

    def open_news_layout(self) -> bool:
        """
        Get onto the AB4.1 news layout, cheapest first:
          1. news table already visible            -> proceed, no click
          2. active tab already reads AB4.1        -> proceed, no click
          3. click a matching layout tab (normalized/contains matching over
             button/span/div/[role=tab], bottom-most first; scrolls the tab
             strip horizontally before the second search pass)
          4. the controller's original strict selector as a last resort
          5. even after a failed click: proceed if the table showed up
        """
        visible = self.news_table_visible()
        active = self.active_layout_name()
        try:
            url = self.controller.driver.current_url
        except Exception:
            url = "unknown"
        skip = should_skip_layout_click(visible, active)
        logger.info("godel layout check: attached_tab_url={} "
                    "news_table_visible={} active_layout_detected={} "
                    "load_layout={}", url, visible, active or "none",
                    "skipped" if skip else "attempting")
        if skip:
            return True

        if self._click_layout_tab(NEWS_LAYOUT):
            # event-driven: wake on the first DOM mutation after the tab
            # click instead of a fixed poll; the visibility re-check stays
            # authoritative and the total wait is still bounded
            deadline = time.time() + RETURN_WAIT_SECONDS
            while time.time() < deadline:
                if self.news_table_visible():
                    return True
                self.wait_for_dom_change(
                    timeout_s=min(1.0, max(0.1, deadline - time.time())))
            return True   # tab clicked; panel selectors may just need tuning
        try:
            if self.controller.load_layout(NEWS_LAYOUT):
                return True
        except Exception as e:
            logger.debug("strict load_layout failed: {}", e)
        return self.news_table_visible()

    def active_layout_name(self) -> Optional[str]:
        """Best-effort text of the currently-active layout tab (bottom nav)."""
        from selenium.webdriver.common.by import By
        try:
            drv = self.controller.driver
            vh = drv.execute_script("return window.innerHeight;") or 0
            candidates = []
            for el in drv.find_elements(
                    By.CSS_SELECTOR,
                    "[aria-selected='true'], [data-state='active'], "
                    "[class*='active'], [class*='selected']"):
                try:
                    if not el.is_displayed():
                        continue
                    label = normalize_label(el.text)
                    if not label or len(label) > 24:
                        continue          # active *tabs* have short labels
                    candidates.append((el.location.get("y", 0), label))
                except Exception:
                    continue
            if not candidates:
                return None
            # layout tabs live along the bottom - bottom-most wins
            candidates.sort(reverse=True)
            y, label = candidates[0]
            if vh and y < vh * 0.5:
                return None               # nothing tab-like near the bottom
            return label
        except Exception:
            return None

    def _click_layout_tab(self, name: str) -> bool:
        """
        Click a layout tab by label with forgiving matching: normalized
        whitespace, exact then contains, across button/span/div/[role=tab],
        preferring bottom-of-viewport elements. If nothing matches, scroll
        any horizontally-scrollable bottom strip and search once more.
        """
        from selenium.webdriver.common.by import By
        drv = self.controller.driver
        xpaths = (
            f"//*[self::button or self::span or self::div or @role='tab']"
            f"[normalize-space(text())='{name}']",
            f"//*[self::button or self::span or self::div or @role='tab']"
            f"[contains(normalize-space(.), '{name}')]",
        )
        for search_pass in range(2):
            for xp in xpaths:
                try:
                    vh = drv.execute_script("return window.innerHeight;") or 0
                    candidates = []
                    for el in drv.find_elements(By.XPATH, xp):
                        try:
                            if not el.is_displayed():
                                continue
                            label = normalize_label(el.text)
                            # guard: contains-match must not grab a huge
                            # ancestor container that merely includes AB4.1
                            if len(label) > len(name) + 8:
                                continue
                            candidates.append((el.location.get("y", 0), el))
                        except Exception:
                            continue
                    candidates.sort(key=lambda t: -t[0])   # bottom-most first
                    for y, el in candidates:
                        if vh and y < vh * 0.4:
                            continue                        # not bottom nav
                        try:
                            el.click()
                            time.sleep(1.5)
                            logger.info("clicked layout tab '{}' (y={})", name, y)
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
            if search_pass == 0:
                # tabs may be scrolled out of a horizontal strip - reveal them
                try:
                    drv.execute_script(
                        "for (const n of document.querySelectorAll('div,nav,footer')) {"
                        "  const r = n.getBoundingClientRect();"
                        "  if (r.top > window.innerHeight * 0.7 &&"
                        "      n.scrollWidth > n.clientWidth + 10) {"
                        "    n.scrollLeft = n.scrollWidth;"
                        "  }"
                        "}")
                    time.sleep(0.5)
                except Exception:
                    break
        return False

    # -- navigation --------------------------------------------------------
    def news_table_visible(self) -> bool:
        """Is the AB4.1 news list currently on screen (window + >=1 row)?"""
        from selenium.webdriver.common.by import By
        try:
            drv = self.controller.driver
            return bool(drv.execute_script(
                "for (const win of document.querySelectorAll(arguments[0])) {"
                "  const wr = win.getBoundingClientRect();"
                "  const ws = getComputedStyle(win);"
                "  if (wr.width <= 0 || wr.height <= 0 || ws.display === 'none' ||"
                "      ws.visibility === 'hidden') continue;"
                "  for (const row of win.querySelectorAll('tr')) {"
                "    const tokens = new Set((row.innerText || '').toLowerCase()"
                "      .replace(/[^a-z]+/g, ' ').trim().split(/\\s+/));"
                "    if (['headline','date','time','ticker'].every(t => tokens.has(t)))"
                "      return true;"
                "  }"
                "}"
                "return false;", SELECTORS["news_window"]))
        except Exception as e:
            logger.debug("batched news visibility check failed; using fallback: {}", e)
        try:
            drv = self.controller.driver
            for win in drv.find_elements(By.CSS_SELECTOR, SELECTORS["news_window"]):
                if win.is_displayed() and win.find_elements(
                        By.CSS_SELECTOR, SELECTORS["news_row"]):
                    return True
            return False
        except Exception:
            return False

    def wait_for_dom_change(self, timeout_s: float = 8.0,
                            css_scope: str = "body") -> dict:
        """
        Event-driven wait: a MutationObserver resolves on the FIRST DOM
        mutation under css_scope, with a hard in-page timeout fallback -
        replaces fixed sleeps without ever waiting longer than before.
        Returns {'changed': bool, 'mode': 'observer'|'timeout'|'error'}.
        Correctness note: callers must still verify the condition they
        care about (e.g. news_table_visible / article identity) - a
        mutation only means "something happened", never "it worked".
        """
        drv = self.controller.driver
        try:
            ms = max(100, int(timeout_s * 1000))
            changed = drv.execute_async_script(
                "const done = arguments[arguments.length - 1];"
                "const scope = document.querySelector(arguments[0]) || document.body;"
                "let settled = false;"
                "const obs = new MutationObserver(() => {"
                "  if (settled) return; settled = true;"
                "  obs.disconnect(); done(true);"
                "});"
                "obs.observe(scope, {childList: true, subtree: true,"
                "                    characterData: true});"
                "setTimeout(() => {"
                "  if (settled) return; settled = true;"
                "  obs.disconnect(); done(false);"
                "}, arguments[1]);", css_scope, ms)
            return {"changed": bool(changed),
                    "mode": "observer" if changed else "timeout"}
        except Exception as e:
            logger.debug("MutationObserver wait unavailable ({}); "
                         "bounded poll fallback", e)
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                time.sleep(0.5)
                if self.news_table_visible():
                    return {"changed": True, "mode": "error"}
            return {"changed": False, "mode": "error"}

    # -- ticker-group switching (NEWS panel ONLY - see module docstring) ---
    def _find_news_panel(self):
        """
        The window container whose visible heading text is exactly 'NEWS'.
        TODO_SELECTOR: heading tag/class pending live DOM confirmation -
        scans common heading-ish elements inside each Godel window and
        matches on exact (not contains) text, so a Quote Monitor panel
        that merely mentions "news" somewhere cannot match. Returns the
        WebElement, or None - callers MUST fail closed rather than search
        the whole document.
        """
        drv = self.controller.driver
        try:
            return drv.execute_script(
                "const wanted = arguments[1].trim().toLowerCase();"
                "for (const win of document.querySelectorAll(arguments[0])) {"
                "  const wr = win.getBoundingClientRect();"
                "  const ws = getComputedStyle(win);"
                "  if (wr.width <= 0 || wr.height <= 0 || ws.display === 'none' ||"
                "      ws.visibility === 'hidden') continue;"
                "  const heads = win.querySelectorAll("
                "    \"[class*='title'],[class*='header'],[class*='heading'],\" +"
                "    \"[class*='tab-title'],div,span,h1,h2,h3\");"
                "  for (const h of heads) {"
                "    const t = (h.innerText || h.textContent || '').trim().toLowerCase();"
                "    if (t === wanted) return win;"
                "  }"
                "}"
                "return null;", SELECTORS["news_window"], NEWS_PANEL_HEADING)
        except Exception as e:
            logger.debug("news panel lookup failed: {}", e)
            return None

    def _find_group_dropdown(self, panel):
        """
        The ticker-group dropdown INSIDE the given NEWS panel element only
        - never document-wide, so Quote Monitor's own dropdown is
        unreachable from here by construction. Tries a native <select>
        first (most reliable), then common custom-dropdown patterns.
        Returns (kind, element) or None.
        TODO_SELECTOR: custom-dropdown markup pending live DOM confirmation.
        """
        from selenium.webdriver.common.by import By
        wanted_labels = known_group_labels()
        try:
            for s in panel.find_elements(By.CSS_SELECTOR, "select"):
                try:
                    opts = [o.text for o in s.find_elements(By.TAG_NAME, "option")]
                except Exception:
                    continue
                normed = [normalize_label(o) for o in opts]
                if any(g in normed for g in wanted_labels):
                    return ("select", s)
        except Exception as e:
            logger.debug("native <select> group-dropdown scan failed: {}", e)
        try:
            for sel in ("[role='combobox']", "button[aria-haspopup='listbox']",
                       "div[class*='dropdown']", "div[class*='select']",
                       "button[class*='select']"):
                for el in panel.find_elements(By.CSS_SELECTOR, sel):
                    try:
                        if not el.is_displayed():
                            continue
                        label = normalize_label(el.text)
                    except Exception:
                        continue
                    if any(group_label_matches(label, g) for g in wanted_labels):
                        return ("custom", el)
        except Exception as e:
            logger.debug("custom group-dropdown scan failed: {}", e)
        return None

    def _dropdown_candidate_texts(self, panel, limit: int = 12) -> List[str]:
        """What the dropdown-like controls in the NEWS panel display - logged
        when no known group label matched, so the real label can be added
        to GODEL_EXTRA_GROUP_LABELS instead of guessed."""
        from selenium.webdriver.common.by import By
        texts: List[str] = []
        for sel in ("select", "[role='combobox']", "button[aria-haspopup='listbox']",
                    "div[class*='dropdown']", "div[class*='select']",
                    "button[class*='select']"):
            try:
                for el in panel.find_elements(By.CSS_SELECTOR, sel):
                    if not el.is_displayed():
                        continue
                    t = " ".join((el.text or "").split())[:60]
                    if t and t not in texts:
                        texts.append(t)
                    if len(texts) >= limit:
                        return texts
            except Exception:
                continue
        return texts

    def _current_group_label(self, kind, el) -> str:
        """Currently displayed value of the group dropdown."""
        try:
            if kind == "select":
                from selenium.webdriver.support.ui import Select
                return Select(el).first_selected_option.text
            return el.text
        except Exception:
            return ""

    def _select_group_native(self, select_el, wanted_group: str) -> bool:
        from selenium.webdriver.support.ui import Select
        try:
            sel = Select(select_el)
            for opt in sel.options:
                if group_label_matches(opt.text, wanted_group):
                    sel.select_by_visible_text(opt.text)
                    return True
        except Exception as e:
            logger.debug("native group select failed: {}", e)
        return False

    def _select_group_custom(self, dropdown_el, wanted_group: str) -> bool:
        """
        Open a custom (non-<select>) dropdown and click the option whose
        text matches wanted_group EXACTLY (group_label_matches), scoped to
        elements that appear near the trigger's position after opening it
        - never a document-wide click.
        TODO_SELECTOR: option-list container pending live DOM confirmation.
        """
        drv = self.controller.driver
        try:
            dropdown_el.click()
            time.sleep(0.4)
        except Exception as e:
            logger.debug("group dropdown trigger click failed: {}", e)
            return False
        try:
            loc = dropdown_el.location
            candidates = drv.execute_script(
                "const x0 = arguments[0], y0 = arguments[1];"
                "const out = [];"
                "for (const el of document.querySelectorAll("
                "    \"[role='option'],li,div[class*='option'],\" +"
                "    \"div[class*='menu-item'],button\")) {"
                "  const r = el.getBoundingClientRect();"
                "  if (r.width <= 0 || r.height <= 0) continue;"
                "  const dy = Math.abs(r.top - y0);"
                "  if (dy > 400) continue;"
                "  const t = (el.innerText || el.textContent || '').trim();"
                "  if (!t || t.length > 24) continue;"
                "  out.push({el, text: t, dy});"
                "}"
                "out.sort((a, b) => a.dy - b.dy);"
                "return out.slice(0, 40);", loc.get("x", 0), loc.get("y", 0)) or []
            for cand in candidates:
                if group_label_matches(cand.get("text"), wanted_group):
                    cand["el"].click()
                    return True
        except Exception as e:
            logger.debug("group option lookup/click failed: {}", e)
        return False

    def _row_fingerprint(self) -> Tuple[int, str]:
        """Cheap signature of the currently visible rows (count + hash of
        the first 15 headlines) - detects that the row set actually
        changed after a group switch. Never used to verify group identity,
        only to detect a stale/mid-transition read."""
        import hashlib as _hashlib
        rows = self.read_news_rows()
        heads = "|".join(sorted(r.get("headline", "") for r in rows[:15]))
        return len(rows), _hashlib.sha256(heads.encode()).hexdigest()[:12]

    def switch_news_group(self, asset: str) -> dict:
        """
        Physically switch the NEWS panel's ticker-group dropdown to the
        group for `asset` ('btc' -> AB4.1, 'spy' -> SP400). Scoped
        strictly to the NEWS panel container - never Quote Monitor, the
        bottom layout tabs, or browser navigation.

        FAILS CLOSED: on any lookup/selection/verification failure this
        returns status='unavailable' with a `reason`, and the caller MUST
        NOT scrape whatever group happens to be showing. Only a verified
        dropdown label (group_label_matches on the POST-selection reading)
        counts as success; row-set stabilization and ticker consistency
        are additional sanity gates, not substitutes for that.
        """
        wanted_group = resolve_group(asset)
        result = {"requested_asset": asset, "requested_group": wanted_group,
                  "selected_group": None, "status": "unavailable",
                  "reason": "", "switched_at": None,
                  "row_ticker_consistency": None}
        panel = self._find_news_panel()
        if panel is None:
            result["reason"] = "news_panel_not_found"
            logger.warning("group switch failed: NEWS panel not found "
                           "(heading '{}')", NEWS_PANEL_HEADING)
            return result

        found = self._find_group_dropdown(panel)
        if found is None:
            candidates = self._dropdown_candidate_texts(panel)
            result["reason"] = "group_dropdown_not_found"
            result["known_group_labels"] = list(known_group_labels())
            result["dropdown_candidates"] = candidates
            logger.warning("group switch failed: no ticker-group dropdown "
                           "inside the NEWS panel shows a known group {} - "
                           "dropdown-like controls show: {}. Switch the "
                           "panel to {} by hand, or add its label to "
                           "GODEL_EXTRA_GROUP_LABELS.",
                           list(known_group_labels()), candidates, wanted_group)
            return result
        kind, el = found
        displayed = self._current_group_label(kind, el)
        already_selected = group_label_matches(displayed, wanted_group)

        if not already_selected:
            before_sig = self._row_fingerprint()
            try:
                selected = (self._select_group_native(el, wanted_group)
                           if kind == "select"
                           else self._select_group_custom(el, wanted_group))
            except Exception as e:
                result["reason"] = f"selection_raised:{e}"
                logger.warning("group switch selection raised: {}", e)
                return result
            if not selected:
                result["reason"] = "requested_option_not_found"
                logger.warning("group switch failed: option '{}' not found "
                               "in the NEWS panel dropdown", wanted_group)
                return result

            deadline = time.time() + GROUP_SWITCH_WAIT_SECONDS
            displayed = ""
            while time.time() < deadline:
                panel2 = self._find_news_panel() or panel
                redo = self._find_group_dropdown(panel2)
                if redo is not None:
                    displayed = self._current_group_label(redo[0], redo[1])
                    if group_label_matches(displayed, wanted_group):
                        break
                time.sleep(0.3)
            if not group_label_matches(displayed, wanted_group):
                result["reason"] = f"verification_failed_displayed={displayed!r}"
                logger.warning("group switch NOT verified: dropdown reads "
                               "{!r}, wanted {!r} - refusing to proceed",
                               displayed, wanted_group)
                return result

            self.wait_for_dom_change(timeout_s=3.0)
            stable_deadline = time.time() + GROUP_STABILIZE_WAIT_SECONDS
            last = before_sig
            while time.time() < stable_deadline:
                cur = self._row_fingerprint()
                if cur == last and cur != before_sig:
                    break
                if cur != before_sig:
                    last = cur
                time.sleep(0.5)

        rows_now = self.read_news_rows()
        consistency, judged = row_group_consistency(rows_now, wanted_group)
        result["row_ticker_consistency"] = {"fraction": round(consistency, 3),
                                            "tickers_judged": judged}
        if judged >= 5 and consistency < 0.3:
            result["reason"] = (
                f"row_ticker_consistency_low ({consistency:.2f} over "
                f"{judged} tickers) - dropdown reads {wanted_group!r} but "
                "visible rows don't look like that universe; refusing to "
                "scrape")
            logger.warning(result["reason"])
            return result

        result.update(selected_group=displayed, status="ok",
                      reason=result["reason"] or
                      ("already_selected" if already_selected else "switched"),
                      switched_at=datetime.now().isoformat(timespec="seconds"))
        logger.info("news group ready: requested={} displayed={} "
                    "row_consistency={}", wanted_group, displayed,
                    result["row_ticker_consistency"])
        return result

    def _row_receives_click(self, element) -> bool:
        """
        Authoritative overlay check: scroll the row to center and ask the
        browser what elementFromPoint at the row's center would actually
        receive a click. DOM presence is NOT enough - the article detail
        overlay leaves rows in the DOM while intercepting their clicks
        (observed live: <p class="my-3 text-[#999999]"> ate the click).
        """
        drv = self.controller.driver
        try:
            drv.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                element)
            time.sleep(0.2)
            return bool(drv.execute_script(
                "const r = arguments[0].getBoundingClientRect();"
                "if (r.width === 0 || r.height === 0) return false;"
                "const el = document.elementFromPoint(r.left + r.width / 2,"
                "                                     r.top + r.height / 2);"
                "return el !== null && (arguments[0] === el || "
                "arguments[0].contains(el));",
                element))
        except Exception:
            return False   # stale/detached element cannot receive a click

    def _find_article_back(self):
        """
        Godel's in-app article back control, CONFIRMED via --debug-back on
        the live DOM (data/news/godel_back_candidates.json,
        2026-07-11):

            <div class="absolute top-[60px] ... z-[100] ... flex ...">
                <button class="mt-2 ml-4">Back</button>
                <button ...>Export PDF</button>
            </div>

        Priority: (1) the fingerprinted button - exact text 'Back' whose
        header bar also contains 'Export PDF' (unambiguous, article view
        only); (2) any visible exact-text 'Back' button; (3) the legacy
        generic scan as a safety net for future UI drift.
        """
        from selenium.webdriver.common.by import By
        try:
            drv = self.controller.driver
            # 1. fingerprinted: Back button beside Export PDF (article header)
            for xp_exact in (
                "//button[normalize-space(.)='Back']"
                "[../*[contains(normalize-space(.), 'Export PDF')]]",
                "//button[normalize-space(.)='Back']",
            ):
                for el in drv.find_elements(By.XPATH, xp_exact):
                    try:
                        if el.is_displayed():
                            return el
                    except Exception:
                        continue
            # 2. future-UI fallback. Keep this inside the browser process;
            # Selenium-wide XPath scans took ~27 seconds once Back disappeared.
            el = drv.execute_script(
                "let best = null; let bestScore = -1;"
                "for (const n of document.querySelectorAll('button,a,[role=button]')) {"
                "  const label = (n.innerText || n.textContent || '').trim()"
                "    .replace(/\\s+/g, ' ');"
                "  const canonical = label.toLowerCase()"
                "    .replace(/[<\\u2039\\u2190]/g, '').trim();"
                "  if (canonical !== 'back') continue;"
                "  const r = n.getBoundingClientRect(); const s = getComputedStyle(n);"
                "  if (r.width <= 0 || r.height <= 0 || s.display === 'none' ||"
                "      s.visibility === 'hidden') continue;"
                "  let score = n.tagName === 'BUTTON' ? 10 : 0;"
                "  const parentText = n.parentElement ?"
                "    (n.parentElement.innerText || '') : '';"
                "  if (parentText.includes('Export PDF')) score += 20;"
                "  if (score > bestScore) { best = n; bestScore = score; }"
                "}"
                "return best;")
            if el is not None:
                return el
        except Exception:
            pass
        return None

    def _list_clickable(self, target_element=None) -> bool:
        """News list is USABLE: article view closed ('<Back' gone), visible
        rows, AND a row actually takes clicks."""
        if self._find_article_back() is not None:
            return False               # article view still open over the list
        if not self.news_table_visible():
            return False
        try:
            if target_element is None:
                rows = self.read_news_rows()
                if rows:
                    target_element = rows[0].get("_element")
            if target_element is None:
                return False
            return self._row_receives_click(target_element)
        except Exception:
            return False

    # --- individual return rungs (each returns True if it ATTEMPTED an action)
    def _return_via_article_back(self) -> bool:
        el = self._find_article_back()
        if el is None:
            return False
        try:
            el.click()
            return True
        except Exception:
            return False

    def _return_via_escape(self) -> bool:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        try:
            self.controller.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            return True
        except Exception:
            return False

    def _return_via_layout_tab(self) -> bool:
        try:
            if self._click_layout_tab(NEWS_LAYOUT):
                return True
            return bool(self.controller.load_layout(NEWS_LAYOUT))
        except Exception:
            return False

    def _return_via_browser_back(self) -> bool:
        # DISABLED BY DEFAULT (GODEL_ALLOW_BROWSER_BACK=false): in attach
        # mode this navigates the user's own Brave tab, and the article view
        # lives INSIDE the Godel panel where history back does nothing
        # useful. Guarded here too in case of a direct call.
        if not browser_back_allowed():
            logger.debug("browser back skipped (GODEL_ALLOW_BROWSER_BACK=false)")
            return False
        try:
            self.controller.driver.back()
            return True
        except Exception:
            return False

    def ensure_news_list_clickable(self, target_element=None,
                                   max_retries: int = MAX_RETURN_RETRIES) -> bool:
        """
        Make the news list actually usable again. Rung order matters:
        Godel's own '<Back' control inside the article view first, then Esc,
        then re-clicking the AB4.1 layout tab; browser history back is the
        very last resort. After an attempted rung, wait until a headline row
        can genuinely receive a click (elementFromPoint), not merely exist.
        Logs return_method for diagnosis.
        """
        if self._list_clickable(target_element):
            return True
        rungs = [
            ("godel_article_back", self._return_via_article_back),
            ("escape", self._return_via_escape),
            ("layout_tab", self._return_via_layout_tab),
        ]
        if browser_back_allowed():
            rungs.append(("browser_back", self._return_via_browser_back))
        rungs = rungs[:max(1, max_retries)]
        for name, action in rungs:
            try:
                attempted = action()
            except Exception as e:
                logger.debug("return rung {} errored: {}", name, e)
                attempted = False
            if not attempted:
                continue                       # rung not applicable - next now
            deadline = time.time() + RETURN_WAIT_SECONDS
            while time.time() < deadline:
                probe = target_element if name == "godel_article_back" else None
                if self._list_clickable(probe):
                    logger.info("return_method: {}", name)
                    return True
                time.sleep(0.5)
        if self._list_clickable(None):
            logger.info("return_method: late_recovery")
            return True
        logger.warning("return_method: failed (all rungs exhausted)")
        return False

    def return_to_news_list(self, max_retries: int = MAX_RETURN_RETRIES) -> bool:
        """Post-article navigation: the list must be clickable, not just present."""
        return self.ensure_news_list_clickable(max_retries=max_retries)

    # -- scraping ----------------------------------------------------------
    def read_news_rows(self) -> List[dict]:
        """Visible headline rows, read in one DOM round trip when possible."""
        from selenium.webdriver.common.by import By
        drv = self.controller.driver
        rows_out: List[dict] = []

        # Reading .text and cell elements through Selenium one row at a time
        # took roughly 100 seconds for Godel's 200-row virtualized table. Keep
        # the WebElements for clicking, but collect all cell text in one JS call.
        try:
            payload = drv.execute_script(
                "const out = [];"
                "for (const win of document.querySelectorAll(arguments[0])) {"
                "  const wr = win.getBoundingClientRect();"
                "  const ws = getComputedStyle(win);"
                "  if (wr.width <= 0 || wr.height <= 0 || ws.display === 'none' ||"
                "      ws.visibility === 'hidden') continue;"
                "  const trs = Array.from(win.querySelectorAll('tr'));"
                "  const isNewsWindow = trs.some(row => {"
                "    const tokens = new Set((row.innerText || '').toLowerCase()"
                "      .replace(/[^a-z]+/g, ' ').trim().split(/\\s+/));"
                "    return ['headline','date','time','ticker'].every(t => tokens.has(t));"
                "  });"
                "  if (!isNewsWindow) continue;"
                "  for (const row of trs.filter(r => r.querySelectorAll('td').length >= 2)) {"
                "    const rr = row.getBoundingClientRect();"
                "    const rs = getComputedStyle(row);"
                "    if (rr.width <= 0 || rr.height <= 0 || rs.display === 'none' ||"
                "        rs.visibility === 'hidden') continue;"
                "    const cells = Array.from(row.querySelectorAll(arguments[2]))"
                "      .map(c => (c.innerText || c.textContent || '').trim())"
                "      .filter(Boolean);"
                "    const rowText = (row.innerText || row.textContent || '').trim();"
                "    out.push({element: row, cells: cells.length ? cells : [rowText]});"
                "  }"
                "}"
                "return out;", SELECTORS["news_window"], SELECTORS["news_row"],
                SELECTORS["row_cells"]) or []
            for item in payload:
                rec = parse_news_row(item.get("cells") or [])
                if rec and item.get("element") is not None:
                    rec["_element"] = item["element"]
                    rows_out.append(rec)
            if rows_out:
                return rows_out
        except Exception as e:
            logger.debug("batched news-row read failed; using Selenium fallback: {}", e)

        # Compatibility fallback for a future Godel DOM/CSP change.
        windows = drv.find_elements(By.CSS_SELECTOR, SELECTORS["news_window"])
        for win in windows:
            for row in win.find_elements(By.CSS_SELECTOR, SELECTORS["news_row"]):
                try:
                    cells = [c.text for c in row.find_elements(
                        By.CSS_SELECTOR, SELECTORS["row_cells"])] or [row.text]
                    rec = parse_news_row(cells)
                    if rec:
                        rec["_element"] = row
                        rows_out.append(rec)
                except Exception:
                    continue   # stale/virtualised row - skip
        return rows_out

    def _click_row_safely(self, element) -> str:
        """
        Click a headline row only when it is truly the click target:
        scroll centered, wait clickable, verify elementFromPoint resolves to
        the row (or a child). If an overlay owns the point, clear it once
        via ensure_news_list_clickable and re-verify. JS click is used only
        AFTER the row is confirmed as the intended target (native click
        intercepted anyway - e.g. by a transient tooltip).
        Returns "clicked" or "blocked".
        """
        from selenium.common.exceptions import ElementClickInterceptedException
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        drv = self.controller.driver
        if not self._row_receives_click(element):
            self.ensure_news_list_clickable(element)
            if not self._row_receives_click(element):
                return "blocked"                # harvest re-queries and retries
        try:
            WebDriverWait(drv, 5).until(EC.element_to_be_clickable(element))
        except Exception:
            pass                                # elementFromPoint already vouched
        try:
            element.click()                     # mouse button 1 per spec
            return "clicked"
        except ElementClickInterceptedException:
            if self._row_receives_click(element):
                try:                            # confirmed target - JS fallback
                    drv.execute_script("arguments[0].click();", element)
                    return "clicked"
                except Exception:
                    return "blocked"
            return "blocked"
        except Exception:
            return "blocked"

    def _validate_article_container(self, element, origin: str,
                                    expected: Optional[dict] = None):
        """Reject table/UI candidates before the capture loop can scroll them."""
        try:
            if element is None or not element.is_displayed():
                return None
            text = element.text or ""
            class_name = element.get_attribute("class") or ""
        except Exception as e:
            logger.debug("article container unreadable: origin={} error={}", origin, e)
            return None

        reason = article_container_rejection_reason(
            text, (expected or {}).get("headline", ""))
        preview = " ".join(text.split())[:180]
        if reason:
            logger.debug("article container rejected: origin={} reason={} "
                         "class='{}' preview='{}'", origin, reason,
                         class_name[:160], preview)
            return None
        logger.debug("article container selected: origin={} class='{}' "
                     "preview='{}'", origin, class_name[:160], preview)
        return element

    def _find_article_container(self, expected: Optional[dict] = None):
        """
        The scrollable element that holds the article text, tried in order:
          1. the in-app Back header's next sibling (confirmed live DOM)
          2. explicit detail-panel selectors
          3. scrollable fallbacks inside the news window

        Every candidate is validated against the news-table fingerprint.
        The live Godel DOM keeps the headline table mounted behind the article,
        so choosing the largest scrollable node without this check is unsafe.
        """
        from selenium.webdriver.common.by import By
        drv = self.controller.driver

        # Confirmed live structure (2026-07-12):
        #   article shell
        #     div[Back, Export PDF]       <- header
        #     div.overflow-y-scroll       <- full article body
        #     ...
        #     div[Headline table]         <- remains mounted underneath
        try:
            back = self._find_article_back()
            if back is not None:
                candidates = drv.execute_script(
                    "const back = arguments[0];"
                    "const header = back.parentElement;"
                    "if (!header) return [];"
                    "const out = []; const seen = new Set();"
                    "const add = n => { if (n && !seen.has(n)) { seen.add(n); out.push(n); } };"
                    "add(header.nextElementSibling);"
                    "if (header.nextElementSibling) {"
                    "  for (const n of header.nextElementSibling.querySelectorAll('div,article,main')) {"
                    "    const s = getComputedStyle(n);"
                    "    if (n.scrollHeight > n.clientHeight + 20 ||"
                    "        s.overflowY === 'auto' || s.overflowY === 'scroll') add(n);"
                    "  }"
                    "}"
                    "if (header.parentElement) {"
                    "  for (const n of header.parentElement.children) add(n);"
                    "}"
                    "return out;", back) or []
                for i, candidate in enumerate(candidates):
                    valid = self._validate_article_container(
                        candidate, f"back_anchor[{i}]", expected)
                    if valid is not None:
                        return valid
        except Exception as e:
            logger.debug("Back-anchored article lookup failed: {}", e)

        try:
            panels = [p for p in drv.find_elements(
                By.CSS_SELECTOR, SELECTORS["detail_panel"]) if p.is_displayed()]
            for i, panel in enumerate(reversed(panels)):
                valid = self._validate_article_container(
                    panel, f"detail_selector[{i}]", expected)
                if valid is not None:
                    return valid
        except Exception:
            pass
        try:
            candidates = drv.execute_script(
                "for (const w of document.querySelectorAll(arguments[0])) {"
                "  const found = [];"
                "  for (const n of w.querySelectorAll('div')) {"
                "    if (n.scrollHeight > n.clientHeight + 20 && n.clientHeight > 80) {"
                "      found.push(n);"
                "    }"
                "  }"
                "  found.sort((a, b) => b.scrollHeight - a.scrollHeight);"
                "  if (found.length) return found.slice(0, 20);"
                "}"
                "return [];", SELECTORS["news_window"]) or []
            for i, candidate in enumerate(candidates):
                valid = self._validate_article_container(
                    candidate, f"scrollable_fallback[{i}]", expected)
                if valid is not None:
                    return valid
        except Exception:
            pass
        return None

    def _shell_title(self) -> str:
        """Title candidate from the article SHELL (the Back control's
        enclosing header area), for layouts where the scrollable body
        starts below the title. Chrome and credit lines are skipped; the
        caller re-runs the normal identity thresholds on the result."""
        try:
            back = self._find_article_back()
            if back is None:
                return ""
            shell_text = self.controller.driver.execute_script(
                "const b = arguments[0];"
                "const shell = b.parentElement && b.parentElement.parentElement"
                "  ? b.parentElement.parentElement : b.parentElement;"
                "return shell ? (shell.innerText || '').slice(0, 1500) : '';",
                back) or ""
            view = extract_article_view(shell_text)
            return view["title"]
        except Exception as e:
            logger.debug("shell title lookup failed: {}", e)
            return ""

    def _wait_for_article_update(self, expected: dict, timeout: float = 8.0):
        """
        After a row click, do NOT read the panel immediately - the previous
        article may still be rendered. Poll (bounded) until the view either
        fuzzy-matches the clicked headline or its body signature differs
        from the last captured article. Returns (container, reason).
        """
        deadline = time.time() + timeout
        container = None
        while time.time() < deadline:
            container = self._find_article_container(expected)
            if container is not None:
                try:
                    text = container.text or ""
                except Exception:
                    text = ""
                if text.strip():
                    view = extract_article_view(text)
                    matched, _ = article_matches_expected(
                        expected, {**view, "text": text})
                    if matched:
                        return container, "matched"
                    if self._last_body_sig is not None and \
                            body_signature(text) != self._last_body_sig:
                        return container, "body_changed"
            time.sleep(0.6)
        return container, "timeout"

    def expand_and_capture(self, row: dict) -> dict:
        """
        Left-click a row, WAIT until the article view actually shows the
        clicked headline's article (not the previous one), capture with
        container scrolling, validate identity, and record diagnostics
        (expected_headline / captured_title / article_match_score /
        article_body_hash). Never raises.
        """
        from selenium.webdriver.common.by import By
        drv = self.controller.driver
        element = row.pop("_element", None)
        expected = {k: row.get(k, "") for k in
                    ("headline", "date", "time", "ticker", "source")}
        diag = {"expected_headline": expected["headline"],
                "captured_title": "", "article_match_score": 0.0,
                "article_body_hash": ""}
        if element is None:
            return {**row, **diag, "detail_status": "headline_only",
                    "detail_text": ""}
        try:
            logger.info("clicking headline: {}", expected["headline"][:70])
            main_handle = drv.current_window_handle
            if self._click_row_safely(element) != "clicked":
                return {**row, **diag, "detail_status": "blocked_by_overlay",
                        "detail_text": ""}

            # full article opened in a new tab/window?
            time.sleep(1.0)
            if len(drv.window_handles) > 1:
                for h in drv.window_handles:
                    if h != main_handle:
                        drv.switch_to.window(h)
                        text = drv.find_element(By.TAG_NAME, "body").text
                        drv.close()
                drv.switch_to.window(main_handle)
                view = extract_article_view(text)
                status, score, sig = classify_capture(
                    expected, view["title"], text, self._last_body_sig)
                diag.update(captured_title=view["title"],
                            article_match_score=round(score, 3),
                            article_body_hash=sig)
                if status == "ok":
                    self._last_body_sig = sig
                logger.info("captured (new tab): '{}' score={} status={}",
                            view["title"][:60], round(score, 2), status)
                return {**row, **diag, "detail_status": status,
                        "detail_text": detail_text_for_status(status, text)}

            # in-panel article: wait for the view to update, retry the click
            # once if the panel still shows the previous article
            panel, reason = self._wait_for_article_update(expected)
            if reason == "timeout" and element is not None:
                try:
                    element.click()          # optional second click per spec
                    panel, reason = self._wait_for_article_update(expected, timeout=5.0)
                except Exception:
                    pass
            logger.debug("article view wait: {}", reason)

            if panel is None:
                logger.warning("article container not found for '{}' - keeping "
                               "headline metadata only", expected["headline"][:70])
                return {**row, **diag,
                        "detail_status": "article_container_not_found",
                        "detail_text": ""}

            def get_text():
                try:
                    return panel.text
                except Exception:
                    return ""

            def scroll_once():
                # scroll the ARTICLE CONTAINER, never the browser window -
                # the article is longer than the visible panel area
                return drv.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollTop + "
                    "arguments[0].clientHeight; return arguments[0].scrollTop;",
                    panel)

            text, steps = capture_scrolling_text(get_text, scroll_once)
            view = extract_article_view(text)
            status, score, sig = classify_capture(
                expected, view["title"], text, self._last_body_sig)
            if status == "article_identity_mismatch":
                # Live AB4.1 (2026-07-19): some article layouts render the
                # title in the shell ABOVE the scrollable body (body starts
                # at a photo caption). Retry identity with the title read
                # from the article shell - the SAME thresholds decide, so
                # nothing weaker than before can become 'ok'.
                shell_title = self._shell_title()
                if shell_title:
                    s2, sc2, sig2 = classify_capture(
                        expected, shell_title, text, self._last_body_sig)
                    if s2 == "ok":
                        logger.info("identity recovered from shell title: "
                                    "'{}'", shell_title[:60])
                        status, score, sig = s2, sc2, sig2
                        view["title"] = shell_title
            diag.update(captured_title=view["title"],
                        article_match_score=round(score, 3),
                        article_body_hash=sig)
            if status == "ok":
                self._last_body_sig = sig
            # NEVER store table/stale/mismatched text under this headline.
            detail_text = detail_text_for_status(status, text)
            logger.info("captured: '{}' score={} status={} ({} scroll steps, "
                        "{} chars)", view["title"][:60], round(score, 2),
                        status, steps, len(text))
            return {**row, **diag, "detail_status": status,
                    "detail_text": detail_text}
        except Exception as e:
            logger.warning("headline expansion failed ({}); continuing", e)
            return {**row, **diag, "detail_status": "expand_failed",
                    "detail_text": ""}

    def close(self) -> None:
        try:
            if self.mode == "attach":
                # Detach only: chromedriver never kills a browser it didn't
                # launch, and we deliberately avoid close() on the user's
                # tabs - their Brave stays exactly as they left it.
                drv = getattr(self.controller, "driver", None)
                if drv is not None:
                    drv.quit()
            else:
                self.controller.disconnect()
        except Exception:
            pass


# ------------------------------------------------------------- back diagnostics
_BACK_DUMP_JS = """
const needles = ['back', '\\u2039', '<', 'chevron', 'arrow', 'return'];
const out = [];
for (const el of document.querySelectorAll('body *')) {
  const r = el.getBoundingClientRect();
  const text = (el.innerText || el.textContent || '').trim().slice(0, 80);
  const aria = el.getAttribute('aria-label') || '';
  const title = el.getAttribute('title') || '';
  const cls = (typeof el.className === 'string' ? el.className : '').slice(0, 140);
  const href = el.getAttribute('href') || '';
  const hay = (text + ' ' + aria + ' ' + title + ' ' + cls + ' ' + href).toLowerCase();
  if (!needles.some(n => hay.includes(n))) continue;
  if (text.length > 60) continue;                 // controls, not containers
  if (r.width === 0 && r.height === 0) continue;
  const style = window.getComputedStyle(el);
  const displayed = style.display !== 'none' && style.visibility !== 'hidden'
                    && r.width > 0 && r.height > 0;
  const p = el.parentElement;
  out.push({
    tag: el.tagName.toLowerCase(), text: text, "class": cls,
    aria_label: aria, title: title, href: href,
    x: Math.round(r.left), y: Math.round(r.top),
    width: Math.round(r.width), height: Math.round(r.height),
    displayed: displayed, enabled: !el.disabled,
    n_children: el.children.length,
    parent_tag: p ? p.tagName.toLowerCase() : null,
    parent_class: p ? (typeof p.className === 'string' ? p.className : '').slice(0, 140) : null,
    parent_text: p ? (p.innerText || '').trim().slice(0, 100) : null,
    prev_sibling_text: el.previousElementSibling ?
        (el.previousElementSibling.innerText || '').trim().slice(0, 60) : null,
    next_sibling_text: el.nextElementSibling ?
        (el.nextElementSibling.innerText || '').trim().slice(0, 60) : null,
  });
  if (out.length >= 400) break;
}
return out;
"""


def rank_back_candidate(c: dict) -> float:
    """
    Heuristic ordering for the console dump: visible, short-labelled,
    back-ish controls first. Pure function (unit-tested offline).
    """
    text = (c.get("text") or "").strip()
    lower = text.lower()
    score = 0.0
    if c.get("displayed"):
        score += 10
    if "‹" in text or text.startswith("<"):          # the literal '<Back'
        score += 6
    if "back" in lower:
        score += 5
    if lower in ("back", "‹back", "‹ back", "< back", "<back"):
        score += 6                                         # exact control label
    if 0 < len(text) <= 8:
        score += 3
    if c.get("tag") in ("button", "a"):
        score += 2
    if "back" in (c.get("aria_label") or "").lower():
        score += 4
    if "backdrop" in (c.get("class") or "").lower() or \
       "background" in (c.get("class") or "").lower():
        score -= 6                                         # css false friends
    if (c.get("n_children") or 0) > 4:
        score -= 3                                         # container, not control
    return score


def debug_back_candidates(headless: Optional[bool] = None) -> dict:
    """
    godel-news --debug-back: attach to the current Godel tab (the user has an
    article view open manually), CLICK NOTHING, and dump every element that
    could plausibly be the in-app back control to
    data/news/godel_back_candidates.json for selector identification.
    """
    import json as _json

    from news import config as _config

    reader = None
    try:
        # ALWAYS attach mode: the diagnostic inspects the user's already-open
        # article view and must never launch a browser or attempt a login
        reader = GodelNewsReader(headless=headless, mode="attach")
        session = reader.start()          # attach: tab selection only, no clicks
        drv = reader.controller.driver
        url = drv.current_url
        candidates = drv.execute_script(_BACK_DUMP_JS) or []
        candidates.sort(key=rank_back_candidate, reverse=True)

        out_path = _config.DATA_DIR / "news" / "godel_back_candidates.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime as _dt
        out_path.write_text(_json.dumps({
            "captured_at": _dt.now().isoformat(timespec="seconds"),
            "attached_tab_url": url,
            "news_table_visible": reader.news_table_visible(),
            "candidate_count": len(candidates),
            "candidates": candidates,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"\n{len(candidates)} back-button candidate(s) on {url}")
        print(f"full dump: {out_path}\n")
        print(f"{'score':>5}  {'tag':<7} {'x':>5} {'y':>5} {'disp':<5} "
              f"{'text':<20} {'aria':<14} class")
        for c in candidates[:15]:
            print(f"{rank_back_candidate(c):>5.1f}  {c['tag']:<7} "
                  f"{c['x']:>5} {c['y']:>5} {str(c['displayed']):<5} "
                  f"{(c['text'] or '')[:20]:<20} "
                  f"{(c['aria_label'] or '')[:14]:<14} "
                  f"{(c['class'] or '')[:60]}")
        return {"status": "ok", **session, "candidates": len(candidates),
                "dump": str(out_path)}
    except Exception as e:
        return {"status": "unavailable", "reason": str(e)}
    finally:
        if reader is not None:
            reader.close()


# Read-only DOM dump for the NEWS panel's ticker-group control. Two views:
# every innermost visible element whose text IS a known group label (with
# its ancestors, so the real dropdown wrapper shows up), and the controls in
# the top strip of the panel _find_news_panel returns.
_GROUP_DUMP_JS = r"""
const panel = arguments[0];
const labels = arguments[1].map(s => s.toLowerCase());
function cls(el) {
  const c = el.getAttribute('class') || '';
  return c.slice(0, 160);
}
function desc(el) {
  const r = el.getBoundingClientRect();
  return {tag: el.tagName.toLowerCase(), id: el.id || null, class: cls(el),
          role: el.getAttribute('role'),
          aria_haspopup: el.getAttribute('aria-haspopup'),
          title: el.getAttribute('title'),
          text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
          x: Math.round(r.x), y: Math.round(r.y),
          w: Math.round(r.width), h: Math.round(r.height),
          in_panel: panel ? panel.contains(el) : null};
}
function ancestors(el, n) {
  const out = [];
  for (let e = el.parentElement, i = 0; e && i < n; e = e.parentElement, i++)
    out.push(desc(e));
  return out;
}
const label_hits = [];
for (const el of document.querySelectorAll('body *')) {
  const t = (el.innerText || '').trim().toLowerCase();
  if (!t || t.length > 40) continue;
  if (!labels.some(l => t === l || t.startsWith(l + ' ') || t.startsWith(l + '(')))
    continue;
  if ([...el.children].some(c => (c.innerText || '').trim().toLowerCase() === t))
    continue;                                   // keep the innermost only
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) continue;
  label_hits.push({element: desc(el), ancestors: ancestors(el, 8)});
  if (label_hits.length >= 25) break;
}
let panel_info = null;
const header_controls = [];
if (panel) {
  panel_info = desc(panel);
  panel_info.text = panel_info.text.slice(0, 40);
  const pr = panel.getBoundingClientRect();
  for (const el of panel.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.y - pr.y > 90) continue;
    const t = (el.innerText || '').trim();
    if (t.length > 40) continue;
    if (el.matches('select,button,input,[role],[aria-haspopup],[class*=elect],' +
                   '[class*=ropdown],[class*=icker]') ||
        (t && el.children.length === 0))
      header_controls.push(desc(el));
    if (header_controls.length >= 80) break;
  }
}
const selects = panel ? [...panel.querySelectorAll('select')].map(s => ({
  class: cls(s), selected: s.selectedIndex >= 0 ? s.options[s.selectedIndex].text : null,
  options: [...s.options].map(o => o.text)})) : [];
return {panel: panel_info, header_controls: header_controls,
        label_hits: label_hits, panel_selects: selects};
"""


def inspect_group_dropdown() -> dict:
    """
    `python -m news inspect-panel`: attach to the Godel tab, CLICK NOTHING,
    and dump how the NEWS panel's ticker-group control is actually built
    (data/news/news_panel_inspect.json), so _find_group_dropdown can be
    pointed at real markup instead of guessed selectors.
    """
    import json as _json

    from news import config as _config

    reader = None
    try:
        reader = GodelNewsReader(mode="attach")
        session = reader.start()
        drv = reader.controller.driver
        panel = reader._find_news_panel()
        labels = list(known_group_labels())
        dump = drv.execute_script(_GROUP_DUMP_JS, panel, labels) or {}
        found = reader._find_group_dropdown(panel) if panel is not None else None
        report = {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "attached_tab_url": drv.current_url,
            "known_group_labels": labels,
            "news_panel_found": panel is not None,
            "dropdown_found_by_current_code": (
                None if found is None else
                {"kind": found[0], "text": normalize_label(found[1].text)}),
            **dump,
        }
        out_path = _config.DATA_DIR / "news" / "news_panel_inspect.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_json.dumps(report, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        return {"status": "ok", **session, "dump": str(out_path),
                "news_panel_found": report["news_panel_found"],
                "dropdown_found_by_current_code":
                    report["dropdown_found_by_current_code"],
                "label_hits": len(dump.get("label_hits", [])),
                "header_controls": len(dump.get("header_controls", []))}
    except Exception as e:
        return {"status": "unavailable", "reason": str(e)}
    finally:
        if reader is not None:
            reader.close()


def run_news_scrape(headless: Optional[bool] = None,
                    asset: Optional[str] = None) -> dict:
    """
    Full cycle: session -> NEWS panel -> VERIFIED ticker-group switch
    (AB4.1 for 'btc', SP400 for 'spy') -> read rows -> expand new ones ->
    store. `asset` selects the group; defaults to this repo's own asset
    (default_asset()). The group switch FAILS CLOSED - if the exact group
    cannot be verified, this returns status='unavailable' rather than
    scraping whatever group happens to be showing. Returns a status dict;
    NEVER raises past this function (the caller may be the BTC paper
    routine, which must not fail on news problems).
    """
    from news.store import append_news, captured_hashes

    asset = (asset or default_asset()).strip().lower()
    reader = None
    try:
        reader = GodelNewsReader(headless=headless)
        session = reader.start()
        if not reader.open_news_layout():
            return {"status": "unavailable",
                    "reason": f"layout {NEWS_LAYOUT} not found - check the "
                              "bottom layout tabs in Godel"}
        group = reader.switch_news_group(asset)
        if group["status"] != "ok":
            return {"status": "unavailable", "reason": group["reason"],
                    "group_switch": group}
        rows_visible = len(reader.read_news_rows())
        # dedup-driven harvest: skip-set is every hash already CAPTURED in
        # the csvs (headlines that previously failed get one retry and
        # upgrade in place); re-queries rows after every article so stale
        # elements never bite; returns to the list automatically; stops
        # cleanly (detail_status=return_failed) if navigation breaks
        captured = harvest_articles(reader, captured_hashes())
        capture_time = datetime.now().isoformat(timespec="seconds")
        for rec in captured:
            rec["requested_asset"] = group["requested_asset"]
            rec["requested_group"] = group["requested_group"]
            rec["selected_group"] = group["selected_group"]
            rec["capture_time"] = capture_time
        result = append_news(captured)
        from collections import Counter
        status_counts = Counter(c.get("detail_status", "unknown") for c in captured)
        return {"status": "ok", **session, "group_switch": group,
                "rows_visible": rows_visible, "rows_new": result["new"],
                "upgraded": result.get("upgraded", 0),
                "duplicates_skipped": result["duplicates_skipped"],
                "failure_reason_counts": {
                    k: v for k, v in status_counts.items() if k != "ok"},
                "capture_status_counts": dict(status_counts),
                "return_failed": any(c.get("detail_status") == "return_failed"
                                     for c in captured),
                **{k: result[k] for k in ("duplicates_flagged",
                                          "weekly_files_written",
                                          "weekly_files_locked") if k in result}}
    except Exception as e:
        return {"status": "unavailable", "reason": str(e)}
    finally:
        if reader is not None:
            reader.close()
