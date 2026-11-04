"""
Forward price outcomes and score calibration (research only).

A score only means something once it is measured against what prices did
next. For every interpreted row this module records the price path AFTER
the headline, then groups rows by score band and reports, per band, how
often the price was higher 1 and 7 days later and by how much on average.
Those measured numbers - not the keyword score - are the readable output:

    prob_up_1d = 63  ->  after past headlines in this band, the price was
                         higher one day later on 63% of independent days

Rules (SCORING.md section 6 explains them with your numbers):
  * the clock starts at the FIRST hourly bar at or after the headline's
    timestamp, so headlines that report a move ("Stock Soars 15%") are not
    credited with the move they describe
  * BTC/SPY store: BTC-USD (SPY for SPY-associated rows); Dow 30 store: the
    row's own ticker. Hourly bars from Yahoo Finance, cached in data/prices/
  * Godel's date/time columns are read in NEWS_TIMEZONE (default US Eastern)
  * calibration skips flagged duplicates and, for BTC, rows with relevance
    below MIN_BTC_RELEVANCE; all rows of a band entering on the same DAY count
    as ONE independent observation (same-day prices move together - one
    BTC price, or 29 Dow stocks riding one market day)
  * a band reports numbers only with CALIBRATION_MIN_DAYS independent days;
    until then calibration_note says how far along it is
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd
from loguru import logger

from news import config
from news.duplicates import event_time

HORIZONS = {"1d": timedelta(days=1), "7d": timedelta(days=7)}
NEWS_TIMEZONE = str(config.setting("NEWS_TIMEZONE", "America/New_York"))
CALIBRATION_MIN_DAYS = int(config.setting("CALIBRATION_MIN_DAYS", 20))
MIN_BTC_RELEVANCE = 40
PRICE_DIR = config.PROJECT_ROOT / "data" / "prices"
BANDS = ("strong negative", "mild negative", "neutral", "mild positive", "strong positive")

OUTCOME_COLUMNS = [
    "signal_band", "prob_up_1d", "expected_move_1d_pct", "prob_up_7d",
    "expected_move_7d_pct", "calibration_note", "outcome_symbol", "outcome_status",
    "outcome_entry_utc", "fwd_return_1d_pct", "fwd_return_7d_pct"]

# one store directory -> how its rows map to a price series
STORE_KINDS = {"btc": "BTC-USD (SPY for SPY-associated rows)",
               "ticker": "the row's own ticker"}

PriceLoader = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


def signal_band(score) -> str:
    """news_sentiment_score -> band. Version 5 scores step by 0.25."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return ""
    if math.isnan(s):
        return ""
    if s <= -0.5:
        return "strong negative"
    if s < 0:
        return "mild negative"
    if s == 0:
        return "neutral"
    if s < 0.5:
        return "mild positive"
    return "strong positive"


def outcome_symbol(row: dict, kind: str) -> str:
    if kind == "btc":
        return "SPY" if str(row.get("association", "")).upper() == "SPY" else "BTC-USD"
    return str(row.get("ticker", "") or "").strip().upper()


def news_time_utc(row: dict) -> Optional[pd.Timestamp]:
    t = event_time(row)
    if t is None:
        return None
    try:
        local = pd.Timestamp(t).tz_localize(NEWS_TIMEZONE, ambiguous=True,
                                            nonexistent="shift_forward")
    except Exception:
        return None
    return local.tz_convert("UTC")


# ------------------------------------------------------------------ prices
def yahoo_hourly(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Hourly open/close from Yahoo Finance, UTC index (yfinance keeps ~730 days)."""
    import yfinance as yf
    hist = yf.Ticker(symbol).history(start=start.strftime("%Y-%m-%d"),
                                     end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                                     interval="1h", auto_adjust=False)
    if hist is None or hist.empty:
        return pd.DataFrame(columns=["open", "close"])
    hist.index = pd.DatetimeIndex(hist.index).tz_convert("UTC")
    return hist[["Open", "Close"]].rename(columns={"Open": "open", "Close": "close"})


def _cache_path(symbol: str, price_dir: Path) -> Path:
    return price_dir / f"{''.join(c if c.isalnum() else '_' for c in symbol)}_1h.csv"


def _read_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["open", "close"])
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "close"]].astype(float)


def prices_for(symbol: str, start: pd.Timestamp, end: pd.Timestamp, fetch: bool = True,
               loader: Optional[PriceLoader] = None,
               price_dir: Path = PRICE_DIR) -> pd.DataFrame:
    """Cached hourly prices covering [start, end]; fetches and merges when
    asked. A failed fetch falls back to whatever is cached."""
    path = _cache_path(symbol, price_dir)
    cached = _read_cache(path)
    if fetch:
        try:
            fresh = (loader or yahoo_hourly)(symbol, start, end)
            if not fresh.empty:
                merged = pd.concat([cached, fresh])
                cached = merged[~merged.index.duplicated(keep="last")].sort_index()
                price_dir.mkdir(parents=True, exist_ok=True)
                cached.to_csv(path, index_label="time_utc")
        except Exception as e:
            logger.warning("price fetch failed for {} ({}); using cache", symbol, e)
    return cached.sort_index()


def forward_outcome(news_utc: pd.Timestamp, prices: pd.DataFrame,
                    now: pd.Timestamp) -> dict:
    """Entry = open of the first bar at or after the headline; each horizon's
    exit = close of the last bar starting at or before entry + horizon."""
    out = {"outcome_entry_utc": "", "fwd_return_1d_pct": None,
           "fwd_return_7d_pct": None}
    after = prices[prices.index >= news_utc] if not prices.empty else prices
    if after.empty:
        out["outcome_status"] = "pending" if news_utc > now - timedelta(hours=2) else "no_price"
        return out
    entry_t, entry_px = after.index[0], float(after["open"].iloc[0])
    out["outcome_entry_utc"] = entry_t.isoformat()
    last_bar = prices.index[-1]
    done = []
    for name, delta in HORIZONS.items():
        target = entry_t + delta
        if target > last_bar:
            done.append(False)          # not elapsed yet, or not in the data yet
            continue
        exit_px = float(prices.loc[prices.index <= target, "close"].iloc[-1])
        out[f"fwd_return_{name}_pct"] = round((exit_px / entry_px - 1) * 100, 3)
        done.append(True)
    out["outcome_status"] = ("ok" if all(done) else
                             "pending_7d" if done[0] else "pending")
    return out


def attach_outcomes(df: pd.DataFrame, kind: str, fetch: bool = True,
                    loader: Optional[PriceLoader] = None, now: Optional[pd.Timestamp] = None,
                    price_dir: Path = PRICE_DIR) -> pd.DataFrame:
    """Fill the outcome columns for every row whose outcome isn't final yet.
    Rows already 'ok' keep their stored numbers and cost no fetch."""
    df = df.copy()
    now = now or pd.Timestamp(datetime.now(timezone.utc))
    for col in OUTCOME_COLUMNS:
        df[col] = df[col].astype(object) if col in df.columns else \
            pd.Series("", index=df.index, dtype=object)
    todo: Dict[str, list] = {}
    for idx, row in df.iterrows():
        sym = outcome_symbol(row, kind)
        if str(row.get("outcome_status", "")) == "ok" and row.get("outcome_symbol") == sym:
            continue
        df.at[idx, "outcome_symbol"] = sym
        t = news_time_utc(row)
        if not sym or t is None:
            df.at[idx, "outcome_status"] = "no_symbol" if not sym else "no_time"
            continue
        todo.setdefault(sym, []).append((idx, t))
    for sym, items in todo.items():
        times = [t for _, t in items]
        start = min(times) - timedelta(hours=2)
        end = min(max(times) + max(HORIZONS.values()) + timedelta(days=1), now)
        prices = prices_for(sym, start, end, fetch=fetch, loader=loader, price_dir=price_dir)
        for idx, t in items:
            for col, val in forward_outcome(t, prices, now).items():
                df.at[idx, col] = "" if val is None else val
    return df


# -------------------------------------------------------------- calibration
def calibration_table(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Per band and horizon: independent days, % of days the price was
    higher, mean and median move. Each entry DAY counts once - its rows'
    returns are averaged first. Not per symbol: stocks on the same day move
    with the market, so 29 Dow stocks on one day are one observation."""
    rows = df.copy()
    rows["signal_band"] = rows["news_sentiment_score"].map(signal_band)
    keep = rows["signal_band"] != ""
    if "is_duplicate" in rows.columns:
        keep &= ~rows["is_duplicate"].astype(str).str.lower().isin(["true", "1"])
    if kind == "btc" and "btc_relevance" in rows.columns:
        keep &= pd.to_numeric(rows["btc_relevance"], errors="coerce").fillna(0) >= MIN_BTC_RELEVANCE
    rows = rows[keep]
    rows["_day"] = rows["outcome_entry_utc"].astype(str).str[:10]
    out = []
    for name in HORIZONS:
        col = f"fwd_return_{name}_pct"
        r = rows.assign(_ret=pd.to_numeric(rows[col], errors="coerce")).dropna(subset=["_ret"])
        by_band = r.groupby(["signal_band", "_day"])["_ret"].mean()
        by_day = r.groupby("_day")["_ret"].mean()
        for band in (*BANDS, "all"):
            vals = by_day if band == "all" else by_band[by_band.index.get_level_values(0) == band]
            n = len(vals)
            out.append({
                "horizon": name, "band": band, "independent_days": n,
                "headlines": int(len(r)) if band == "all" else int((r["signal_band"] == band).sum()),
                "prob_up_pct": round(float((vals > 0).mean()) * 100, 1) if n else None,
                "mean_move_pct": round(float(vals.mean()), 3) if n else None,
                "median_move_pct": round(float(vals.median()), 3) if n else None,
                "calibrated": n >= CALIBRATION_MIN_DAYS})
    return pd.DataFrame(out)


def apply_calibration(df: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """Copy each band's measured numbers onto its rows; blank until the band
    has CALIBRATION_MIN_DAYS independent days."""
    df = df.copy()
    df["signal_band"] = df["news_sentiment_score"].map(signal_band)
    lookup = {(r.horizon, r.band): r for r in table.itertuples()}
    for col in ("prob_up_1d", "expected_move_1d_pct", "prob_up_7d",
                "expected_move_7d_pct", "calibration_note"):
        df[col] = pd.Series("", index=df.index, dtype=object)
    for idx, band in df["signal_band"].items():
        if not band:
            continue
        parts = []
        for name in HORIZONS:
            r = lookup.get((name, band))
            days = int(r.independent_days) if r is not None else 0
            if r is not None and r.calibrated:
                df.at[idx, f"prob_up_{name}"] = r.prob_up_pct
                df.at[idx, f"expected_move_{name}_pct"] = r.mean_move_pct
                parts.append(f"{name} calibrated on {days} days")
            else:
                parts.append(f"{name} {days}/{CALIBRATION_MIN_DAYS} days")
        df.at[idx, "calibration_note"] = "; ".join(parts)
    return df


def update_outcomes(news_dir: Path, kind: str, fetch: bool = True,
                    loader: Optional[PriceLoader] = None, now: Optional[pd.Timestamp] = None,
                    price_dir: Path = PRICE_DIR) -> dict:
    """Outcomes + calibration for one store, written back to its interpreted
    CSV, weekly files and calibration.csv. Never raises past a locked file."""
    from news import store

    path = news_dir / store.INTERPRETED_CSV.name
    df = store._load(path)
    if df.empty:
        return {"store": str(news_dir), "rows": 0}
    # current scores and duplicate flags first, so bands match final scores
    raw = store._load(news_dir / store.RAW_CSV.name)
    df = store.finalize_interpreted(df, raw)
    df = attach_outcomes(df, kind, fetch=fetch, loader=loader, now=now, price_dir=price_dir)
    table = calibration_table(df, kind)
    df = store.finalize_interpreted(apply_calibration(df, table), raw)   # column order
    status = df["outcome_status"].value_counts().to_dict()
    try:
        df.to_csv(path, index=False)
        table.to_csv(news_dir / "calibration.csv", index=False)
    except PermissionError:
        return {"store": str(news_dir), "rows": len(df), "outcome_status": status,
                "error": f"{path.name} or calibration.csv is open in Excel - close it and rerun"}
    weekly = store.write_weekly_csvs(df, news_dir / store.WEEKLY_DIR.name)
    calibrated = table[table["calibrated"]]
    return {"store": str(news_dir), "rows": len(df), "outcome_status": status,
            "calibrated_bands": [f"{r.band} ({r.horizon})" for r in calibrated.itertuples()
                                 if r.band != "all"],
            "calibration_csv": str(news_dir / "calibration.csv"), **weekly}
