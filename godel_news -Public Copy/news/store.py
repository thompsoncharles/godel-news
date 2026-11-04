"""
CSV storage for Godel news (research only).

    data/news/godel_news_raw.csv          every headline as scraped
    data/news/godel_news_interpreted.csv  + interpretation + coefficients
                                          + duplicate flags (news.duplicates)
    data/news/weekly/<week>.csv           the interpreted rows again, one file
                                          per Monday-Sunday week of news date
    data/news/routing_quarantine.csv      AMBIGUOUS-association rows
                                                (audit only - raw rows above
                                                are never removed or copied)

Append-only with dedup by headline_hash. Strategy code never reads these
files; they exist for reporting and future research joins.

This is the CANONICAL raw article store for this repo's own asset - one
row per headline_hash, never duplicated. Each row also carries a computed
BTC/SPY/BOTH/AMBIGUOUS `association` (news.asset_routing), so report
generation can filter by association without ever touching the raw record
itself (see news.excel_report).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Set

import pandas as pd

from news import config
from news.asset_routing import classify_association
from news.duplicates import DUP_COLUMNS, event_time, flag_duplicates
from news.outcomes import OUTCOME_COLUMNS
from news.interpreter import (NOVELTY_DUPLICATE, SCORING_VERSION, coefficients,
                              headline_hash, interpret, news_alpha)

NEWS_DIR = config.DATA_DIR / "news"
RAW_CSV = NEWS_DIR / "godel_news_raw.csv"
INTERPRETED_CSV = NEWS_DIR / "godel_news_interpreted.csv"
ROUTING_QUARANTINE_CSV = NEWS_DIR / "routing_quarantine.csv"
WEEKLY_DIR = NEWS_DIR / "weekly"

# column order for the interpreted CSV and the weekly files (any other
# column a row carries is kept, after these)
INTERPRETED_COLUMNS = [
    "captured_at", "headline_hash", "headline", "date", "time", "ticker",
    "source", "detail_status", *DUP_COLUMNS,
    "sentiment", "sentiment_basis", "btc_relevance", "impact_direction", "impact_horizon",
    "confidence", "categories", "entities", "explanation",
    "news_sentiment_score", "btc_relevance_coeff", "source_reliability_coeff",
    "novelty_coeff", "urgency_coeff", "final_news_alpha_coeff",
    "trade_impact_enabled", "scoring_version", *OUTCOME_COLUMNS,
    "requested_asset", "requested_group", "selected_group", "capture_time",
    "association", "association_confidence", "association_reason"]

# columns interpret() + coefficients() own - replaced when a row is re-scored
SCORE_COLUMNS = [
    "sentiment", "sentiment_basis", "btc_relevance", "impact_direction", "impact_horizon",
    "confidence", "categories", "entities", "explanation",
    "news_sentiment_score", "btc_relevance_coeff", "source_reliability_coeff",
    "novelty_coeff", "urgency_coeff", "final_news_alpha_coeff",
    "trade_impact_enabled", "scoring_version"]

RAW_COLUMNS = ["captured_at", "headline_hash", "headline", "date", "time",
               "ticker", "source", "detail_status", "detail_text",
               "detail_text_chars",
               "expected_headline", "captured_title", "article_match_score",
               "article_body_hash",
               "requested_asset", "requested_group", "selected_group",
               "capture_time",
               "association", "association_confidence", "association_reason"]


# capture-quality ranking: when the same headline is seen again, only a
# BETTER capture replaces the stored one (never downgrade a full article
# to a failed retry)
STATUS_RANK = {
    "ok": 5,
    "headline_only": 4,
    "container_not_found_partial_capture": 3,
    "expand_failed": 2,
    "article_container_not_found": 2,  # retryable: article node was not found
    "article_identity_mismatch": 2,   # retryable: wrong article was open
    "suspicious_same_body": 2,        # retryable: stale previous body detected
    "blocked_by_overlay": 1,
    "return_failed": 1,
}


def _rank(status) -> int:
    return STATUS_RANK.get(str(status), 0)


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame()


def known_hashes() -> Set[str]:
    """Every headline hash persisted in EITHER csv (raw or interpreted)."""
    hashes: Set[str] = set()
    for path in (RAW_CSV, INTERPRETED_CSV):
        df = _load(path)
        if "headline_hash" in df.columns:
            hashes |= set(df["headline_hash"])
    return hashes


def captured_hashes() -> Set[str]:
    """
    Hashes whose stored capture is already decent (ok / headline_only /
    partial). The harvest seeds its skip-set from THESE, so a headline that
    previously failed (expand_failed / blocked / return_failed) gets one
    more chance on later runs and upgrades in place via STATUS_RANK.
    """
    df = _load(RAW_CSV)
    if "headline_hash" not in df.columns:
        return set()
    if "detail_status" not in df.columns:
        return set(df["headline_hash"])
    keep = df["detail_status"].map(_rank) >= _rank("container_not_found_partial_capture")
    return set(df.loc[keep, "headline_hash"])


def append_news(rows: List[dict]) -> dict:
    """
    Dedup + interpret + persist a batch of scraped rows. Each input row:
    {headline, date, time, ticker, source, detail_status, detail_text}.

    Dedup contract:
      - one row per headline_hash across BOTH csvs, always
      - an incoming duplicate is skipped unless its detail_status outranks
        the stored one (STATUS_RANK), in which case it UPGRADES in place
        (e.g. a retried article that finally captured full text replaces
        the earlier headline_only row)
    """
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")

    raw_by_hash = {r["headline_hash"]: r
                   for r in _load(RAW_CSV).to_dict("records")
                   if r.get("headline_hash")}
    interp_by_hash = {r["headline_hash"]: r
                      for r in _load(INTERPRETED_CSV).to_dict("records")
                      if r.get("headline_hash")}

    new_count = upgraded = skipped = 0
    changed: List[str] = []
    quarantine_rows: List[dict] = []
    for row in rows:
        if not row.get("headline"):
            skipped += 1
            continue
        h = headline_hash(row.get("headline", ""), row.get("date", ""),
                          row.get("time", ""), row.get("ticker", ""))
        status = row.get("detail_status", "headline_only")
        prior = raw_by_hash.get(h)
        if prior is not None and _rank(status) <= _rank(prior.get("detail_status")):
            skipped += 1
            continue
        detail_text = row.get("detail_text") or ""
        selected_group = row.get("selected_group") or ""
        assoc = classify_association(
            headline=row.get("headline", ""), detail_text=detail_text,
            ticker=row.get("ticker", ""), selected_group=selected_group or None,
            group_verified=bool(selected_group))
        raw_rec = {
            "captured_at": now, "headline_hash": h,
            "headline": row.get("headline", ""), "date": row.get("date", ""),
            "time": row.get("time", ""), "ticker": row.get("ticker", ""),
            "source": row.get("source", ""),
            "detail_status": status,
            # CSV is the lossless source of truth. Excel applies its own
            # 32,767-character cell limit only when rendering the workbook.
            "detail_text": detail_text,
            "detail_text_chars": len(detail_text),
            # article-identity diagnostics (stale-capture forensics)
            "expected_headline": row.get("expected_headline", row.get("headline", "")),
            "captured_title": row.get("captured_title", ""),
            "article_match_score": row.get("article_match_score", ""),
            "article_body_hash": row.get("article_body_hash", ""),
            # NEWS-panel group-switch capture metadata (empty for rows
            # captured before group-switching existed, or by legacy paths)
            "requested_asset": row.get("requested_asset", ""),
            "requested_group": row.get("requested_group", ""),
            "selected_group": selected_group,
            "capture_time": row.get("capture_time", ""),
            # deterministic BTC/SPY/BOTH/AMBIGUOUS routing (report filters
            # read this; the record above is never duplicated or dropped)
            "association": assoc["association"],
            "association_confidence": assoc["confidence"],
            "association_reason": assoc["reason"],
        }
        interp = interpret(raw_rec["headline"], raw_rec["detail_text"],
                           raw_rec["source"], raw_rec["ticker"])
        # novelty (and with it the alpha) is set from the duplicate flags in
        # finalize_interpreted, once the whole store is known
        coeff = coefficients(interp, raw_rec["source"])
        interp_rec = {
            **{k: raw_rec[k] for k in ("captured_at", "headline_hash", "headline",
                                       "date", "time", "ticker", "source",
                                       "detail_status", "requested_asset",
                                       "requested_group", "selected_group",
                                       "capture_time", "association",
                                       "association_confidence",
                                       "association_reason")},
            **{k: v for k, v in interp.items() if not k.startswith("_")},
            **coeff,
        }
        if prior is None:
            new_count += 1
        else:
            upgraded += 1
        raw_by_hash[h] = raw_rec
        interp_by_hash[h] = interp_rec
        changed.append(h)
        if assoc["association"] == "AMBIGUOUS":
            quarantine_rows.append({
                "quarantined_at": now, "headline_hash": h,
                "headline": raw_rec["headline"], "reason": assoc["reason"],
                "confidence": assoc["confidence"]})

    derived, interp_new = {}, []
    if new_count or upgraded:
        # full rewrite keeps the one-row-per-hash invariant on disk.
        # Interpreted FIRST: if it is open in Excel nothing is saved and the
        # rows are simply captured again next run. Raw-first would leave
        # them in raw only, and the dedup would then skip them forever.
        raw_df = pd.DataFrame(list(raw_by_hash.values()))
        interp_df = finalize_interpreted(pd.DataFrame(list(interp_by_hash.values())),
                                         raw_df)
        interp_new = interp_df[interp_df["headline_hash"].isin(changed)].to_dict("records")
        for path, frame in ((INTERPRETED_CSV, interp_df), (RAW_CSV, raw_df)):
            try:
                frame.to_csv(path, index=False)
            except PermissionError as e:
                raise RuntimeError(
                    f"{path.name} is open in Excel (or locked) - close it and "
                    "rerun; this run's new headlines will be captured again"
                    + (" (interpreted CSV was already updated)"
                       if path == RAW_CSV else "")) from e
        # weekly files live beside the interpreted CSV they are cut from
        derived = {"duplicates_flagged": int(interp_df["is_duplicate"].sum()),
                   **write_weekly_csvs(interp_df,
                                       INTERPRETED_CSV.parent / WEEKLY_DIR.name)}
    if quarantine_rows:
        _append_quarantine(quarantine_rows)
    return {"scraped": len(rows), "new": new_count, "upgraded": upgraded,
            "duplicates_skipped": skipped, "interpreted": interp_new,
            "quarantined": len(quarantine_rows), **derived}


def finalize_interpreted(df: pd.DataFrame,
                         raw: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    The one step every interpreted CSV write goes through:
      1. re-score rows from an older scoring_version, from their raw record
         (headline + article text, source, ticker) when `raw` is given
      2. recompute duplicate flags over the whole store
      3. novelty_coeff from the flags (NOVELTY_DUPLICATE for copies, else
         1.0) and final_news_alpha_coeff to match
      4. INTERPRETED_COLUMNS order (unknown extra columns go last)
    """
    if not df.empty:
        df = _rescore_stale(df, raw)
    bodies = (dict(zip(raw["headline_hash"], raw["detail_text"].fillna("").astype(str)))
              if raw is not None and {"headline_hash", "detail_text"} <= set(raw.columns)
              else None)
    df = flag_duplicates(df, bodies)
    if not df.empty:
        df = _apply_novelty(df)
    cols = [c for c in INTERPRETED_COLUMNS if c in df.columns]
    return df[cols + [c for c in df.columns if c not in cols]]


def _rescore_stale(df: pd.DataFrame, raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Re-run interpret() + coefficients() on every row not scored by the
    current SCORING_VERSION. Capture metadata, association and everything
    else on the row is left alone."""
    version = (df["scoring_version"].astype(str).str.replace(r"\.0$", "", regex=True)
               if "scoring_version" in df.columns else pd.Series("", index=df.index))
    stale = version != str(SCORING_VERSION)
    if not stale.any():
        return df
    raw_by_hash = ({r["headline_hash"]: r for r in raw.fillna("").to_dict("records")}
                   if raw is not None and "headline_hash" in raw.columns else {})
    df = df.copy()
    for col in SCORE_COLUMNS:   # object, not pandas' str dtype: scores are numbers
        df[col] = (df[col].astype(object) if col in df.columns
                   else pd.Series("", index=df.index, dtype=object))
    for idx in df.index[stale]:
        row = df.loc[idx]
        src = raw_by_hash.get(row.get("headline_hash"), row)
        headline, detail = str(src.get("headline", "")), str(src.get("detail_text", "") or "")
        source, ticker = str(src.get("source", "")), str(src.get("ticker", ""))
        interp = interpret(headline, detail, source, ticker)
        scores = {**{k: v for k, v in interp.items() if not k.startswith("_")},
                  **coefficients(interp, source)}
        for col in SCORE_COLUMNS:
            df.at[idx, col] = scores[col]
    return df


def _apply_novelty(df: pd.DataFrame) -> pd.DataFrame:
    """novelty_coeff = NOVELTY_DUPLICATE on flagged copies, 1.0 on originals,
    and final_news_alpha_coeff recomputed with it."""
    df = df.copy()
    novelty = [NOVELTY_DUPLICATE if d else 1.0 for d in df["is_duplicate"]]

    def num(col):
        return pd.to_numeric(df[col], errors="coerce").fillna(0.0).tolist()

    df["novelty_coeff"] = novelty
    df["final_news_alpha_coeff"] = [
        news_alpha(s, r, rel, u, n) for s, r, rel, u, n in
        zip(num("news_sentiment_score"), num("btc_relevance_coeff"),
            num("source_reliability_coeff"), num("urgency_coeff"), novelty)]
    return df


def week_file_name(monday: datetime) -> str:
    """'2026-W44_2026-10-26_to_2026-11-01.csv' - ISO week, Monday to Sunday."""
    year, week, _ = monday.isocalendar()
    sunday = monday + timedelta(days=6)
    return f"{year}-W{week:02d}_{monday:%Y-%m-%d}_to_{sunday:%Y-%m-%d}.csv"


def write_weekly_csvs(df: pd.DataFrame, weekly_dir: Path) -> dict:
    """
    One CSV per Monday-Sunday week of NEWS date (event_time: the date/time
    columns, falling back to captured_at), rows in news-time order, same
    columns as the interpreted CSV. Rewritten from the store each time, so
    upgrades and duplicate flags reach older weeks too; a file is only
    touched when its content changed. A week open in Excel is skipped and
    reported - never fatal; the next run catches it up.
    """
    if df.empty:
        return {"weekly_files_written": 0, "weekly_files_locked": []}
    weekly_dir.mkdir(parents=True, exist_ok=True)
    times = [event_time(r) for r in df.to_dict("records")]
    stamped = df.assign(_t=times)
    stamped = stamped[stamped["_t"].notna()]
    mondays = stamped["_t"].map(lambda t: datetime(t.year, t.month, t.day)
                                - timedelta(days=t.weekday()))
    written, locked = 0, []
    for monday, week in stamped.groupby(mondays, sort=True):
        path = weekly_dir / week_file_name(monday)
        # bytes, not text mode: to_csv() already uses Windows line endings and
        # a text-mode write would double them (blank row after every row).
        # utf-8-sig so Excel shows non-ASCII characters correctly.
        data = week.sort_values("_t", kind="stable").drop(columns="_t") \
            .to_csv(index=False).encode("utf-8-sig")
        try:
            if path.exists() and path.read_bytes() == data:
                continue
            path.write_bytes(data)
            written += 1
        except PermissionError:
            locked.append(path.name)
    if locked:
        print(f"NEWS WEEKLY CSV LOCKED (open in Excel?) - not updated: "
              f"{', '.join(locked)}. The next run catches them up.")
    return {"weekly_files_written": written, "weekly_files_locked": locked,
            "weekly_dir": str(weekly_dir)}


def refresh_derived(news_dir: Path = NEWS_DIR) -> dict:
    """Recompute duplicate flags and rewrite the weekly CSVs for the store
    in news_dir from its existing interpreted CSV. No browser, no scraping
    (`python -m news rebuild`)."""
    path = news_dir / INTERPRETED_CSV.name
    df = _load(path)
    if df.empty:
        return {"store": str(news_dir), "rows": 0}
    stale = int((df.get("scoring_version", pd.Series("", index=df.index)).astype(str)
                 .str.replace(r"\.0$", "", regex=True) != str(SCORING_VERSION)).sum())
    df = finalize_interpreted(df, _load(news_dir / RAW_CSV.name))
    try:
        df.to_csv(path, index=False)
    except PermissionError:
        return {"store": str(news_dir), "rows": len(df),
                "error": f"{path.name} is open in Excel - close it and rerun"}
    return {"store": str(news_dir), "rows": len(df), "rescored": stale,
            "scoring_version": SCORING_VERSION,
            "duplicates_flagged": int(df["is_duplicate"].sum()),
            "exact": int((df["duplicate_type"] == "exact").sum()),
            "near": int((df["duplicate_type"] == "near").sum()),
            "article": int((df["duplicate_type"] == "article").sum()),
            **write_weekly_csvs(df, news_dir / WEEKLY_DIR.name)}


def _append_quarantine(rows: List[dict]) -> None:
    """Append-only audit trail of AMBIGUOUS-association rows. This is a
    diagnostic sidecar only - it never removes or copies the raw record,
    it just makes ambiguous rows easy to find and review."""
    existing = _load(ROUTING_QUARANTINE_CSV)
    combined = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True) \
        if not existing.empty else pd.DataFrame(rows)
    combined.to_csv(ROUTING_QUARANTINE_CSV, index=False)


def load_routing_quarantine() -> pd.DataFrame:
    return _load(ROUTING_QUARANTINE_CSV)


def association_counts() -> dict:
    """BTC/SPY/BOTH/AMBIGUOUS row counts in the current raw store - used
    by the report filter and the repair-routing audit."""
    df = _load(RAW_CSV)
    if "association" not in df.columns:
        return {}
    return df["association"].value_counts().to_dict()


def load_interpreted() -> pd.DataFrame:
    return _load(INTERPRETED_CSV)
