"""
Godel news Excel workbook (research/reporting only).

    data/reports/godel_news_report.xlsx

Sheets: Summary / Raw Headlines / Interpreted Headlines /
High Relevance BTC News / Risk Events / Source Counts / Coefficient History.
Falls back gracefully (CSVs remain the source of truth) if openpyxl is
missing. News coefficients shown here are research-only:
trade_impact_enabled is False on every row.

Identical file in both repos - report filtering is ASSET-PARAMETERIZED
(news.godel_reader.default_asset()) rather than hardcoded, so this
repo's report only ever shows rows associated with its own asset (or
BOTH); rows associated only with the other asset, or AMBIGUOUS, are
excluded here but never removed from the underlying CSV store (see
news.asset_routing / store.append_news).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from news import config
from news.store import INTERPRETED_CSV, RAW_CSV, _load

XLSX_PATH = config.DATA_DIR / "reports" / "godel_news_report.xlsx"
EXCEL_CELL_TEXT_LIMIT = 32767

COEFF_COLS = ["captured_at", "headline_hash", "headline",
              "news_sentiment_score", "btc_relevance_coeff",
              "source_reliability_coeff", "novelty_coeff", "urgency_coeff",
              "final_news_alpha_coeff", "trade_impact_enabled"]


def _asset_label(asset: Optional[str] = None) -> str:
    from news.godel_reader import default_asset
    a = (asset or default_asset()).strip().lower()
    return {"btc": "BTC", "spy": "SPY"}.get(a, a.upper())


def filter_by_association(df: pd.DataFrame, asset_label: str) -> pd.DataFrame:
    """
    Rows this repo's report may show: association == asset_label or BOTH,
    OR no association computed yet (rows captured before this session's
    routing existed and not yet repaired - included for backward
    compatibility rather than silently going blank; run
    `godel-news-repair-routing --apply` to classify them). Rows associated
    ONLY with the other asset, or AMBIGUOUS, are excluded from the report
    but never removed from the CSV store itself.
    """
    if "association" not in df.columns:
        return df
    assoc = df["association"].fillna("")
    return df[assoc.isin([asset_label, "BOTH", ""])]


def _excel_safe_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """Preserve full text in CSV while respecting Excel's per-cell limit."""
    out = raw.copy()
    if "detail_text" not in out.columns:
        return out
    detail = out["detail_text"].fillna("").astype(str)
    lengths = detail.str.len()
    if "detail_text_chars" not in out.columns:
        out["detail_text_chars"] = lengths
    out["detail_text_truncated_for_excel"] = lengths > EXCEL_CELL_TEXT_LIMIT
    out["detail_text"] = detail.str.slice(0, EXCEL_CELL_TEXT_LIMIT)
    return out


def write_news_excel(asset: Optional[str] = None) -> dict:
    """
    Returns {"excel_status": ..., "excel_path": ...} and NEVER raises:
      written          -> the standard workbook path
      locked_fallback  -> workbook was open/locked; wrote a timestamped copy
      locked_skipped   -> both paths locked; CSVs remain the source of truth
      skipped_no_openpyxl

    `asset` (default: this repo's own default_asset()) selects which
    association this report is for - see filter_by_association above.
    """
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("NEWS EXCEL SKIPPED: openpyxl not installed - CSVs under "
              "data/news/ are unaffected.")
        return {"excel_status": "skipped_no_openpyxl", "excel_path": None}

    asset_label = _asset_label(asset)
    raw_all = _load(RAW_CSV)
    interp_all = _load(INTERPRETED_CSV)
    excluded_other_asset = excluded_ambiguous = 0
    if "association" in raw_all.columns:
        assoc = raw_all["association"].fillna("")
        excluded_ambiguous = int((assoc == "AMBIGUOUS").sum())
        excluded_other_asset = int((~assoc.isin(
            [asset_label, "BOTH", "", "AMBIGUOUS"])).sum())
    raw = filter_by_association(raw_all, asset_label)
    interp = filter_by_association(interp_all, asset_label)
    raw_excel = _excel_safe_raw(raw)

    n = len(interp)
    def _num(col):
        return pd.to_numeric(interp[col], errors="coerce") if col in interp.columns else pd.Series(dtype=float)

    relevance = _num("btc_relevance")
    alpha = _num("final_news_alpha_coeff")
    statuses = raw["detail_status"].value_counts() if "detail_status" in raw.columns \
        else pd.Series(dtype=int)
    summary = pd.DataFrame([
        ("headlines_captured_total", len(raw)),
        ("headlines_interpreted", n),
        ("latest_capture", raw["captured_at"].max() if len(raw) else "none"),
        ("high_relevance_btc_count", int((relevance >= 70).sum()) if n else 0),
        ("risk_events", int((interp.get("sentiment") == "risk_event").sum()) if n else 0),
        ("failure_article_identity_mismatch", int(statuses.get("article_identity_mismatch", 0))),
        ("failure_suspicious_same_body", int(statuses.get("suspicious_same_body", 0))),
        ("clean_ok_captures", int(statuses.get("ok", 0))),
        ("mean_news_alpha", round(float(alpha.mean()), 4) if n else None),
        ("trade_impact_enabled", "FALSE - research only, never feeds trading"),
        ("report_asset", asset_label),
        ("rows_excluded_other_asset", excluded_other_asset),
        ("rows_excluded_ambiguous", excluded_ambiguous),
    ], columns=["metric", "value"])

    high_rel = interp[relevance >= 70] if n else interp
    risk = interp[interp.get("sentiment", pd.Series(dtype=str)) == "risk_event"] if n else interp
    sources = (raw["source"].replace("", "unknown").value_counts()
               .rename_axis("source").reset_index(name="headlines")
               if len(raw) else pd.DataFrame(columns=["source", "headlines"]))
    coeff_hist = interp[[c for c in COEFF_COLS if c in interp.columns]] if n else \
        pd.DataFrame(columns=COEFF_COLS)

    def _write(path):
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            summary.to_excel(xw, sheet_name="Summary", index=False)
            (raw_excel if len(raw_excel) else pd.DataFrame(columns=["headline"])).to_excel(
                xw, sheet_name="Raw Headlines", index=False)
            (interp if n else pd.DataFrame(columns=["headline"])).to_excel(
                xw, sheet_name="Interpreted Headlines", index=False)
            high_rel.to_excel(xw, sheet_name="High Relevance BTC News", index=False)
            risk.to_excel(xw, sheet_name="Risk Events", index=False)
            sources.to_excel(xw, sheet_name="Source Counts", index=False)
            coeff_hist.to_excel(xw, sheet_name="Coefficient History", index=False)

    XLSX_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write(XLSX_PATH)
        return {"excel_status": "written", "excel_path": str(XLSX_PATH)}
    except PermissionError:
        # the workbook is open in Excel - write a timestamped copy instead
        from datetime import datetime
        fallback = XLSX_PATH.with_name(
            f"godel_news_report_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        try:
            _write(fallback)
            print(f"NEWS EXCEL LOCKED: {XLSX_PATH.name} is open in Excel - "
                  f"wrote {fallback.name} instead. CSVs are unaffected.")
            return {"excel_status": "locked_fallback", "excel_path": str(fallback)}
        except PermissionError:
            print("NEWS EXCEL LOCKED: workbook (and fallback) not writable - "
                  "skipped. CSVs under data/news/ are unaffected.")
            return {"excel_status": "locked_skipped", "excel_path": None}
