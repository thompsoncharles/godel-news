"""
Godel news program - command line.

    python -m news scrape [--asset btc|spy|all]   capture + interpret + Excel report
    python -m news status                          is the saved session usable? (no scraping)
    python -m news login                           open your own Brave: pass Cloudflare + log in
    python -m news dow30 [options]                 Dow 30 capture -> data_dow30/ (see --help)
    python -m news dow30-classify [options]        classify the Dow 30 store
    python -m news repair-routing [--apply]        re-run BTC/SPY association on the store
    python -m news rebuild                         recompute duplicate flags + weekly CSVs (no browser)
    python -m news outcomes                        price moves after each headline + calibration
    python -m news debug-back                      attach + dump in-app back-button candidates
    python -m news inspect-panel                   attach + dump the NEWS group-dropdown markup

Research/reporting only. Completely separate from the trading bot: nothing
here imports from it, reads its data, or writes into it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from news import config

# each store and the price series its rows are measured against
STORES = ((config.DATA_DIR / "news", "btc"), (config.DOW30_DATA_DIR / "news", "ticker"))


def update_all_outcomes() -> list:
    """Price outcomes + calibration for both stores. Best effort: a failure
    is reported, never raised, so it can't break a scrape."""
    from news.outcomes import update_outcomes

    results = []
    for news_dir, kind in STORES:
        try:
            results.append(update_outcomes(news_dir, kind))
        except Exception as e:
            results.append({"store": str(news_dir), "error": f"outcomes failed: {e}"})
    return results


def cmd_scrape(args) -> int:
    from news.excel_report import write_news_excel
    from news.godel_reader import ASSET_GROUPS, default_asset, run_news_scrape

    asset_arg = (args.asset or default_asset()).strip().lower()
    assets = list(ASSET_GROUPS) if asset_arg == "all" else [asset_arg]
    per_asset = {a: run_news_scrape(asset=a) for a in assets}
    if len(assets) == 1:
        result = per_asset[assets[0]]
    else:
        result = {"status": "ok" if all(r.get("status") == "ok"
                                        for r in per_asset.values())
                 else "partial", "per_asset": per_asset}
    scraped = result.get("status") in ("ok", "partial")
    outcomes = update_all_outcomes() if scraped else []
    excel = (write_news_excel() if scraped
             else {"excel_status": None, "excel_path": None})
    report = {**result, "trade_impact": "disabled - research only",
              "outcomes": outcomes,
              "raw_csv": str(config.DATA_DIR / "news" / "godel_news_raw.csv"),
              "interpreted_csv": str(config.DATA_DIR / "news" / "godel_news_interpreted.csv"),
              **excel}
    out = config.DATA_DIR / "godel_news_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    print(f"full result: {out}")
    return 0 if result.get("status") in ("ok", "partial") else 1


def cmd_status(args) -> int:
    """Report the Godel page state in the configured browser mode. Clicks
    nothing; in attach mode it detaches and leaves your browser open."""
    from news.godel_reader import (GodelNewsReader, browser_mode, cdp_available,
                                   session_state, settle_session_state)

    mode = browser_mode()
    if mode == "attach" and not cdp_available():
        print("attach mode: no debuggable browser is open - run "
              "`run_news.bat login` first.")
        return 1
    reader = GodelNewsReader(headless=False, mode=mode)
    try:
        if mode == "attach":
            try:
                reader.start()          # picks the Godel tab
            except RuntimeError as e:
                if reader.controller.driver is None:   # could not attach at all
                    print(f"attach failed: {e}")
                    return 1
                # otherwise not logged in / Cloudflare: reported below
            drv = reader.controller.driver
            state = session_state(drv)
        else:
            reader.controller.connect()
            drv = reader.controller.driver
            state = settle_session_state(drv)
        print(json.dumps({"mode": mode, "state": state, "title": drv.title,
                          "url": drv.current_url}, indent=2))
        window = "the Brave window" if mode == "attach" else "a normal browser"
        hints = {
            "terminal": "logged in - `run_news.bat` will scrape",
            "login": f"not logged in - log in to Godel in {window}",
            "cloudflare": f"Cloudflare check showing - complete it in {window}"
                          + ("" if mode == "attach" else
                             " (set GODEL_BROWSER_MODE = \"attach\" and run "
                             "`run_news.bat login`)"),
            "unknown": "page not recognised - look at the browser window",
        }
        print(hints[state])
        return 0 if state == "terminal" else 1
    finally:
        reader.close()


def cmd_login(args) -> int:
    """Open your own Brave (debug port on, dedicated profile) so YOU pass the
    Cloudflare check and log in. Nothing attaches to the browser until you
    press Enter, so the check sees an ordinary browser. That profile stays
    logged in for later runs; leave the window open while scraping."""
    from news.godel_reader import browser_mode, start_debug_browser

    info = start_debug_browser()
    if info["started"]:
        print(f"Opened {info['browser']}\n  profile: {info['profile']}\n"
              f"  debug endpoint: {info['cdp']}")
    else:
        print(f"Debuggable browser already running at {info['cdp']}.")
    if browser_mode() != "attach":
        print("NOTE: GODEL_BROWSER_MODE is not 'attach' - scrapes will NOT use "
              "this window. Set it to \"attach\" in local_settings.py.")
    print("\nIn that window: complete the Cloudflare check if it shows, log in "
          "to Godel, and open the AB4.1 news layout.\nLeave the window open.")
    if sys.stdin and sys.stdin.isatty():
        input("Press Enter here when you're logged in... ")
    else:
        time.sleep(120)
    return cmd_status(args)


def cmd_debug_back(args) -> int:
    from news.godel_reader import debug_back_candidates
    print(json.dumps(debug_back_candidates(), indent=2, default=str))
    return 0


def cmd_rebuild(args) -> int:
    """Re-score, duplicate flags, weekly CSVs, then price outcomes, for both
    stores, from what is already saved. Safe to rerun; nothing is scraped and
    no row is removed (prices are fetched for rows still missing them)."""
    from news.store import refresh_derived

    results = [refresh_derived(d / "news")
               for d in (config.DATA_DIR, config.DOW30_DATA_DIR)]
    results += update_all_outcomes()
    print(json.dumps(results, indent=2, default=str))
    return 1 if any("error" in r for r in results) else 0


def cmd_outcomes(args) -> int:
    results = update_all_outcomes()
    print(json.dumps(results, indent=2, default=str))
    return 1 if any("error" in r for r in results) else 0


def cmd_inspect_panel(args) -> int:
    from news.godel_reader import inspect_group_dropdown
    result = inspect_group_dropdown()
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") == "ok" else 1


def cmd_repair_routing(args) -> int:
    """
    One-time (idempotent) repair of BTC/SPY association routing on EXISTING
    captured news in data/news. Moves ONLY the association fields, never raw
    record content. Backs up CSV news artifacts before any --apply write and
    writes a full audit CSV either way. Dry run unless --apply.
    """
    import pandas as pd

    import news.store as store
    from news.repair_routing import backup_artifacts, repair_row, write_audit_csv

    dry_run = not args.apply

    df = store._load(store.RAW_CSV)
    if df.empty:
        print(f"no rows in {store.RAW_CSV} - nothing to repair")
        return 0
    records = df.to_dict("records")
    audit_rows = [repair_row(
        row_id=rec.get("headline_hash", ""),
        headline=rec.get("headline", ""),
        detail_text=rec.get("detail_text", ""),
        ticker=rec.get("ticker", ""),
        selected_group=(rec.get("selected_group") or None),
        old_association=rec.get("association", ""))
        for rec in records]

    audit_path = config.DATA_DIR / "news" / \
        f"routing_repair_audit_{'dry_run' if dry_run else 'applied'}.csv"
    write_audit_csv(audit_rows, audit_path)
    changed = sum(1 for a in audit_rows if a["action"] != "no_change")
    print(f"routing repair: {len(audit_rows)} row(s) examined, {changed} "
          f"would change ({'DRY RUN - nothing else written' if dry_run else 'APPLYING'})")
    print(f"audit: {audit_path}")
    if dry_run:
        return 0

    backup_root = config.DATA_DIR / "_migration_backups"
    dest = backup_artifacts(
        [store.RAW_CSV, store.INTERPRETED_CSV, store.ROUTING_QUARANTINE_CSV,
         config.DATA_DIR / "reports"], backup_root)
    print(f"backup: {dest}")

    by_id = {a["row_id"]: a for a in audit_rows}
    interp_records = store._load(store.INTERPRETED_CSV).to_dict("records")
    for rec in records + interp_records:
        a = by_id.get(rec.get("headline_hash", ""))
        if a is None:
            continue
        rec["association"] = a["new_association"]
        rec["association_confidence"] = a["confidence"]
        rec["association_reason"] = a["reason"]
    pd.DataFrame(records).to_csv(store.RAW_CSV, index=False)
    if interp_records:
        pd.DataFrame(interp_records).to_csv(store.INTERPRETED_CSV, index=False)
    print(f"applied: {changed} row(s) updated in {store.RAW_CSV.name} and "
          f"{store.INTERPRETED_CSV.name}")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # the Dow 30 commands own their argument parsing (and must run before
    # news.store is imported - see dow30.capture.bootstrap)
    if argv and argv[0] == "dow30":
        from dow30 import capture
        return capture.main(argv[1:])
    if argv and argv[0] == "dow30-classify":
        from dow30 import classify
        return classify.main(argv[1:])

    p = argparse.ArgumentParser(prog="python -m news", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scrape", help="capture + interpret + Excel report")
    s.add_argument("--asset", choices=["btc", "spy", "all"], default=None,
                   help="NEWS-panel ticker group (btc->AB4.1, spy->SP400, "
                        "all->both). Default: GODEL_NEWS_DEFAULT_ASSET or btc")
    sub.add_parser("status", help="report whether the saved session is usable")
    sub.add_parser("login", help="open your own Brave to pass Cloudflare and log in")
    sub.add_parser("debug-back", help="attach, click nothing, dump back-button candidates")
    sub.add_parser("inspect-panel", help="attach, click nothing, dump the NEWS "
                                         "panel's group-dropdown markup")
    sub.add_parser("rebuild", help="re-score, duplicate flags, weekly CSVs, outcomes; no browser")
    sub.add_parser("outcomes", help="price moves after each headline + calibration")
    r = sub.add_parser("repair-routing", help="re-run BTC/SPY association on the store")
    r.add_argument("--apply", action="store_true",
                   help="actually write the repaired CSVs (default: dry run)")
    sub.add_parser("dow30", help="Dow 30 capture (run `python -m news dow30 --help`)")
    sub.add_parser("dow30-classify", help="classify the Dow 30 store")
    args = p.parse_args(argv)

    return {"scrape": cmd_scrape, "status": cmd_status, "login": cmd_login,
            "debug-back": cmd_debug_back, "inspect-panel": cmd_inspect_panel,
            "rebuild": cmd_rebuild, "outcomes": cmd_outcomes,
            "repair-routing": cmd_repair_routing}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
