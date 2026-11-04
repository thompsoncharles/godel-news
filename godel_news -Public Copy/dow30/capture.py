"""
Capture Dow 30 news from the Godel Terminal DOW30 watchlist.

Wraps this program's news reader:

  * registers the DOW30 ticker group in ASSET_GROUPS at runtime, so
    resolve_group("dow") works and the dropdown finder will recognize it
  * redirects the news store to data_dow30/ BEFORE news.store is
    imported, so Dow rows never enter the BTC/SPY store or its Excel report

Usage (from the godel_news folder):
    python -m news dow30 --list-groups     # discover dropdown labels
    python -m news dow30                   # capture, then classify
    python -m news dow30 --no-classify     # capture only
    python -m news dow30 --group DOW30E    # override the label
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

DOW_GROUP = os.environ.get("GODEL_DOW_GROUP", "DOW30")
DOW_ASSET = "dow"


def bootstrap(group_label: str, isolated: bool = True) -> Path:
    """
    Point the store at the isolated Dow data dir and register the Dow
    group. Order matters: news.store binds NEWS_DIR = config.DATA_DIR /
    "news" at import time, so DATA_DIR has to be patched before that
    module is first imported.

    Returns the raw-CSV path the capture will write to.
    """
    sys.path.insert(0, str(REPO))

    if "news.store" in sys.modules:
        raise SystemExit("news.store was imported before bootstrap() - "
                         "the store would write to the shared BTC/SPY data "
                         "dir. Import order bug.")

    from news import config

    if isolated:
        config.DATA_DIR = config.DOW30_DATA_DIR
    (config.DATA_DIR / "news").mkdir(parents=True, exist_ok=True)

    from news import godel_reader
    godel_reader.ASSET_GROUPS[DOW_ASSET] = group_label

    from news import store
    return store.RAW_CSV


def install_no_layout_switch() -> None:
    """
    Replace open_news_layout with a check-only version that NEVER clicks a
    bottom layout tab.

    The stock method is already detection-first (it skips the click when the
    news table is visible), but it still falls through to clicking the
    AB4.1 tab if detection fails. This hard-guards that: if the news table
    is not already on screen, refuse rather than navigate away from
    whatever layout the user has open.
    """
    from loguru import logger

    from news.godel_reader import GodelNewsReader

    # The article-return ladder also clicks layout tabs
    # (godel_article_back -> escape -> LAYOUT_TAB -> browser_back), so
    # neutralize the click itself, not just open_news_layout.
    def blocked_click_layout_tab(self, wanted: str = "") -> bool:
        logger.info("layout-tab click suppressed (wanted={!r})", wanted)
        return False

    GodelNewsReader._click_layout_tab = blocked_click_layout_tab

    def check_only(self) -> bool:
        visible = self.news_table_visible()
        active = self.active_layout_name()
        try:
            url = self.controller.driver.current_url
        except Exception:
            url = "unknown"
        logger.info("layout check (no-switch mode): url={} "
                    "news_table_visible={} active_layout={} load_layout=never",
                    url, visible, active or "none")
        if not visible:
            logger.warning("news table not visible on the current layout and "
                           "layout switching is disabled - open the news "
                           "panel yourself, then re-run")
        return visible

    GodelNewsReader.open_news_layout = check_only


def capture_headlines_only(group_label: str) -> dict:
    """
    Read the news table and store headlines WITHOUT opening any article.

    Godel's article view replaces the list inside the panel, and returning
    from it depends on an in-app Back control that the reader currently
    cannot locate - so every expanding run stops after one article. Reading
    rows needs no clicks at all, which sidesteps that entirely and leaves
    the layout untouched.

    Rows are stored with detail_status='headline_only' (STATUS_RANK 4), so
    a later run that does capture full text upgrades them in place rather
    than duplicating.
    """
    from datetime import datetime

    from news.godel_reader import GodelNewsReader
    from news.store import append_news

    reader = GodelNewsReader()
    try:
        session = reader.start()
        if not reader.open_news_layout():
            return {"status": "unavailable",
                    "reason": "news table not visible on the current layout"}
        group = reader.switch_news_group(DOW_ASSET)
        if group["status"] != "ok":
            return {"status": "unavailable", "reason": group["reason"],
                    "group_switch": group}

        rows = reader.read_news_rows()
        captured_at = datetime.now().isoformat(timespec="seconds")
        for r in rows:
            r["detail_status"] = "headline_only"
            r["detail_text"] = ""
            r["requested_asset"] = group["requested_asset"]
            r["requested_group"] = group["requested_group"]
            r["selected_group"] = group["selected_group"]
            r["capture_time"] = captured_at
        result = append_news(rows)
        return {"status": "ok", **session, "group_switch": group,
                "mode": "headlines_only",
                "rows_visible": len(rows), "rows_new": result["new"],
                "upgraded": result.get("upgraded", 0),
                "duplicates_skipped": result["duplicates_skipped"],
                **{k: result[k] for k in ("duplicates_flagged",
                                          "weekly_files_written",
                                          "weekly_files_locked") if k in result}}
    except Exception as e:
        return {"status": "unavailable", "reason": str(e)}
    finally:
        reader.close()


def list_groups(group_label: str) -> int:
    """
    Attach to the live session and print every option in the NEWS panel's
    ticker-group dropdown. Use when the exact label is unknown - the
    switcher matches labels exactly and fails closed, so a near-miss like
    'Dow 30' vs 'DOW30' silently yields requested_option_not_found.
    """
    from selenium.webdriver.common.by import By

    from news.godel_reader import ASSET_GROUPS, GodelNewsReader

    reader = GodelNewsReader()
    try:
        reader.start()
        if not reader.open_news_layout():
            print("news layout not found - is the Godel tab open and logged in?",
                  file=sys.stderr)
            return 1
        panel = reader._find_news_panel()
        if panel is None:
            print("NEWS panel not found", file=sys.stderr)
            return 1
        found = reader._find_group_dropdown(panel)
        if found is None:
            print("ticker-group dropdown not found inside the NEWS panel.\n"
                  f"It is located by matching known labels {sorted(ASSET_GROUPS.values())}; "
                  "if the panel is currently showing an unknown group, switch "
                  "it to AB4.1 or SP400 in the UI and re-run.", file=sys.stderr)
            return 1

        kind, el = found
        print(f"dropdown kind: {kind}")
        print(f"currently displayed: {reader._current_group_label(kind, el)!r}\n")

        labels = []
        if kind == "select":
            labels = [o.text.strip()
                      for o in el.find_elements(By.TAG_NAME, "option")]
        else:
            el.click()
            reader.wait_for_dom_change(timeout_s=2.0)
            for sel in ("[role='option']", "li", "div[class*='option']"):
                for o in panel.find_elements(By.CSS_SELECTOR, sel):
                    try:
                        if o.is_displayed() and o.text.strip():
                            labels.append(o.text.strip())
                    except Exception:
                        continue
                if labels:
                    break

        if not labels:
            print("no option labels readable - the dropdown may render in a "
                  "portal outside the panel. Read the label off the screen "
                  "and pass it with --group.", file=sys.stderr)
            return 1

        print("available groups:")
        for lab in dict.fromkeys(labels):
            mark = "  <-- configured" if lab.strip() == group_label else ""
            print(f"  {lab!r}{mark}")
        if group_label not in [l.strip() for l in labels]:
            print(f"\nWARNING: configured label {group_label!r} is not in the "
                  f"list. Re-run with --group set to the exact string above.")
        return 0
    finally:
        reader.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", default=DOW_GROUP,
                    help=f"exact dropdown label of the Dow watchlist "
                         f"(default: {DOW_GROUP})")
    ap.add_argument("--list-groups", action="store_true",
                    help="print the dropdown's available labels and exit")
    ap.add_argument("--shared-store", action="store_true",
                    help="write into the shared BTC/SPY data/ store instead of "
                         "the isolated data_dow30/")
    ap.add_argument("--attach", action="store_true",
                    help="attach to your running Brave over CDP instead of "
                         "launching a browser; never reads credentials")
    ap.add_argument("--cdp-url", default=None,
                    help="CDP endpoint for --attach (default http://127.0.0.1:9222)")
    ap.add_argument("--allow-layout-switch", action="store_true",
                    help="permit clicking the bottom layout tab to reach the "
                         "news panel. Off by default: the current layout is "
                         "left exactly as you have it.")
    ap.add_argument("--headlines-only", action="store_true",
                    help="read the news table without opening any article. "
                         "Clicks nothing, so it cannot get stuck in the "
                         "article view; stores detail_status=headline_only.")
    ap.add_argument("--no-classify", action="store_true",
                    help="capture only; skip the Dow 30 classification pass")
    ap.add_argument("--min-impact", type=int, default=1, choices=range(1, 6))
    ap.add_argument("--strict", action="store_true", default=True,
                    help="drop rows matched only by Godel's ticker tag (default on)")
    ap.add_argument("--no-strict", action="store_false", dest="strict")
    args = ap.parse_args(argv)

    if args.attach:
        os.environ["GODEL_BROWSER_MODE"] = "attach"
        if args.cdp_url:
            os.environ["GODEL_CDP_URL"] = args.cdp_url

    raw_csv = bootstrap(args.group, isolated=not args.shared_store)

    if not args.allow_layout_switch:
        install_no_layout_switch()

    if args.list_groups:
        return list_groups(args.group)

    from news.godel_reader import browser_mode, run_news_scrape

    print(f"browser mode: {browser_mode()}"
          f"{'  (layout tab will NOT be touched)' if not args.allow_layout_switch else ''}")
    print(f"capturing group {args.group!r} -> {raw_csv}")
    result = (capture_headlines_only(args.group) if args.headlines_only
              else run_news_scrape(asset=DOW_ASSET))
    print(json.dumps(result, indent=2, default=str))

    if result.get("status") != "ok":
        reason = str(result.get("reason", ""))
        print(f"\ncapture unavailable: {reason}", file=sys.stderr)
        if "requested_option_not_found" in reason:
            print("-> the DOW30 label was not in the dropdown. Run "
                  "--list-groups for the exact string.", file=sys.stderr)
        elif "not found" in reason and "layout" in reason.lower():
            print("-> layout switching is disabled, so the news panel has to "
                  "be visible already. Open it in Godel and re-run, or pass "
                  "--allow-layout-switch.", file=sys.stderr)
        elif "cdp" in reason.lower() or "attach" in reason.lower():
            print("-> Brave must be started with "
                  "--remote-debugging-port=9222. Chromium only accepts that "
                  "flag at startup, so fully quit Brave first (all windows "
                  "AND background processes), then relaunch with it.",
                  file=sys.stderr)
        else:
            print("-> the switcher fails closed on purpose; it will not "
                  "scrape a group it cannot verify.", file=sys.stderr)
        return 1

    if args.no_classify:
        return 0

    print("\n--- Dow 30 classification ---")
    from dow30 import classify

    return classify.main([
        "--raw", str(raw_csv),
        "--min-impact", str(args.min_impact),
        *(["--strict"] if args.strict else []),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
