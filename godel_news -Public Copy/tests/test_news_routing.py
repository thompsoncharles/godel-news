"""
Offline tests for NEWS-panel ticker-group switching + BTC/SPY association
routing - synthetic data only, no network, no browser.
Run: venv\\Scripts\\python.exe -m tests.test_news_routing
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def isolate_from_local_settings() -> None:
    """Tests check the built-in defaults, never your local_settings.py or
    GODEL_* environment (must run before news.godel_reader is imported)."""
    import os
    from news import config
    config._local = None
    for k in [k for k in os.environ if k.startswith("GODEL_")]:
        os.environ.pop(k)


def main() -> int:
    isolate_from_local_settings()
    failures = []

    def check(name, fn):
        try:
            fn()
            print(f"  OK   {name}")
        except Exception as e:
            failures.append((name, e))
            print(f"  FAIL {name}: {e}")

    # ============================================== group resolution/match
    def _resolve_group():
        from news.godel_reader import ASSET_GROUPS, resolve_group

        assert resolve_group("btc") == "AB4.1"
        assert resolve_group("spy") == "SP400"
        assert resolve_group("BTC") == "AB4.1"       # case-insensitive
        assert resolve_group(" spy ") == "SP400"     # whitespace-tolerant
        for bad in ("", "eth", "all", None):
            try:
                resolve_group(bad)
                raise AssertionError(f"resolve_group({bad!r}) should have raised")
            except ValueError:
                pass
        assert set(ASSET_GROUPS) == {"btc", "spy"}
    check("resolve_group: btc->AB4.1, spy->SP400, else raises", _resolve_group)

    def _group_label_matches():
        from news.godel_reader import group_label_matches

        assert group_label_matches("AB4.1", "AB4.1") is True
        assert group_label_matches("  AB4.1  ", "AB4.1") is True   # whitespace
        assert group_label_matches("SP400 (400)", "SP400") is True  # decorated
        assert group_label_matches("SP400", "sp400") is True        # case-fold
        # NEVER a bare substring match
        assert group_label_matches("SP4000", "SP400") is False
        assert group_label_matches("ABAB4.1X", "AB4.1") is False
        assert group_label_matches("XSP400", "SP400") is False
        assert group_label_matches("", "AB4.1") is False
        assert group_label_matches(None, "AB4.1") is False
        assert group_label_matches("AB4.1", "") is False
    check("group_label_matches: exact/decorated match, never bare substring",
         _group_label_matches)

    def _row_group_consistency():
        from news.godel_reader import row_group_consistency

        crypto_rows = [{"ticker": "BTC"}, {"ticker": "COIN"}, {"ticker": "ETH"}]
        frac, n = row_group_consistency(crypto_rows, "AB4.1")
        assert n == 3 and frac == 1.0

        mixed_rows = [{"ticker": "BTC"}, {"ticker": "AAPL"}]
        frac, n = row_group_consistency(mixed_rows, "AB4.1")
        assert n == 2 and abs(frac - 0.5) < 1e-9

        equity_rows = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
        frac, n = row_group_consistency(equity_rows, "SP400",
                                        spy_universe={"AAPL", "MSFT", "SPY"})
        assert n == 2 and frac == 1.0

        # no universe supplied for SP400: falls back to "not a known crypto
        # ticker" as a weak positive signal
        frac, n = row_group_consistency(equity_rows, "SP400")
        assert n == 2 and frac == 1.0

        # no tickers to judge -> (0.0, 0), not a failure
        assert row_group_consistency([{"ticker": ""}], "AB4.1") == (0.0, 0)
        assert row_group_consistency([], "AB4.1") == (0.0, 0)
    check("row_group_consistency: crypto/equity sanity signal, never a hard "
         "requirement on empty input", _row_group_consistency)

    # ==================================================== asset_routing.py
    def _classify_capture_metadata_authoritative():
        from news.asset_routing import classify_association

        r = classify_association(headline="Some ordinary headline",
                                 selected_group="AB4.1", group_verified=True)
        assert r["association"] == "BTC" and r["confidence"] > 0.9

        r = classify_association(headline="Some ordinary headline",
                                 selected_group="SP400", group_verified=True)
        assert r["association"] == "SPY" and r["confidence"] > 0.9

        # verified BTC group but content clearly also equity-flavored ->
        # upgrades to BOTH rather than silently staying single-asset
        r = classify_association(
            headline="Bitcoin ETF inflows lift S&P 500 futures",
            detail_text="stock market rally as SPY ETF gains",
            selected_group="AB4.1", group_verified=True)
        assert r["association"] == "BOTH"

        # verified group but macro content also upgrades to BOTH
        r = classify_association(
            headline="Federal Reserve holds rates steady",
            selected_group="AB4.1", group_verified=True)
        assert r["association"] == "BOTH"
    check("classify_association: verified capture group is authoritative, "
         "opposite/macro content upgrades to BOTH", _classify_capture_metadata_authoritative)

    def _classify_content_only_fallback():
        from news.asset_routing import classify_association

        r = classify_association(headline="Bitcoin halving drives crypto rally")
        assert r["association"] == "BTC"

        r = classify_association(headline="S&P 500 hits record on strong earnings")
        assert r["association"] == "SPY"

        r = classify_association(
            headline="Bitcoin ETF and S&P 500 both rally on Fed news")
        assert r["association"] == "BOTH"

        r = classify_association(headline="Local weather forecast for Tuesday")
        assert r["association"] == "AMBIGUOUS" and r["confidence"] == 0.0

        # macro-only, no asset-specific lexicon hit -> BOTH (cross-market),
        # not silently attributed to either asset
        r = classify_association(headline="Federal Reserve signals rate cut")
        assert r["association"] == "BOTH"

        # ticker hint alone (no keyword) still counts as crypto content
        r = classify_association(headline="Update on the market today",
                                 ticker="MSTR")
        assert r["association"] == "BTC"
    check("classify_association: content-only fallback for historical/"
         "unverified rows, AMBIGUOUS never guessed", _classify_content_only_fallback)

    def _classify_never_substring_group():
        from news.asset_routing import classify_association
        # an unrecognized selected_group must NOT be treated as verified
        # metadata for either asset (fail closed, falls to content)
        r = classify_association(headline="Local weather forecast",
                                 selected_group="SOMETHING_ELSE",
                                 group_verified=True)
        assert r["association"] == "AMBIGUOUS"
    check("classify_association: unrecognized group label never silently "
         "maps to an asset", _classify_never_substring_group)

    # ============================================== BTC store integration
    def _store_association_roundtrip():
        import news.store as ns
        import news.excel_report as xr
        from news import config as qc

        scratch = qc.DATA_DIR / "news_routing_test"
        scratch.mkdir(parents=True, exist_ok=True)
        saved = {"RAW_CSV": ns.RAW_CSV, "INTERPRETED_CSV": ns.INTERPRETED_CSV,
                "ROUTING_QUARANTINE_CSV": ns.ROUTING_QUARANTINE_CSV}
        ns.RAW_CSV = scratch / "raw.csv"
        ns.INTERPRETED_CSV = scratch / "interp.csv"
        ns.ROUTING_QUARANTINE_CSV = scratch / "quarantine.csv"
        try:
            rows = [
                {"headline": "Bitcoin surges past key level", "date": "2026-07-20",
                 "time": "09:00", "ticker": "BTC", "source": "Reuters",
                 "detail_status": "ok", "detail_text": "crypto rally",
                 "requested_asset": "btc", "requested_group": "AB4.1",
                 "selected_group": "AB4.1", "capture_time": "2026-07-20T09:00:05"},
                {"headline": "Local weather forecast for the weekend",
                 "date": "2026-07-20", "time": "09:05", "ticker": "",
                 "source": "WeatherWire", "detail_status": "ok",
                 "detail_text": "sunny skies expected"},
            ]
            out = ns.append_news(rows)
            assert out["new"] == 2
            assert out["quarantined"] == 1   # the weather headline

            df = ns._load(ns.RAW_CSV)
            assoc_by_headline = dict(zip(df["headline"], df["association"]))
            assert assoc_by_headline["Bitcoin surges past key level"] == "BTC"
            assert assoc_by_headline["Local weather forecast for the weekend"] \
                == "AMBIGUOUS"
            # AMBIGUOUS row is NOT deleted from the canonical raw store
            assert len(df) == 2

            q = ns.load_routing_quarantine()
            assert len(q) == 1 and q.iloc[0]["headline"] == \
                "Local weather forecast for the weekend"

            counts = ns.association_counts()
            assert counts.get("BTC") == 1 and counts.get("AMBIGUOUS") == 1

            # report filter: BTC report shows the BTC row, excludes AMBIGUOUS
            btc_label = xr._asset_label("btc")
            filtered = xr.filter_by_association(df, btc_label)
            assert len(filtered) == 1
            assert filtered.iloc[0]["headline"] == "Bitcoin surges past key level"
        finally:
            ns.RAW_CSV, ns.INTERPRETED_CSV, ns.ROUTING_QUARANTINE_CSV = \
                saved["RAW_CSV"], saved["INTERPRETED_CSV"], \
                saved["ROUTING_QUARANTINE_CSV"]
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
    check("store.append_news: association computed + persisted, AMBIGUOUS "
         "quarantined-but-kept, report filter excludes it", _store_association_roundtrip)

    # ============================================ repair_routing idempotence
    def _repair_routing_idempotent():
        from news.repair_routing import repair_row

        a1 = repair_row(row_id="h1", headline="Bitcoin rallies on ETF news",
                        detail_text="", ticker="BTC", selected_group=None,
                        old_association="")
        assert a1["action"] == "initial_classification"
        assert a1["new_association"] == "BTC"

        # second pass with old_association now reflecting the applied
        # result must be a no-op (idempotent)
        a2 = repair_row(row_id="h1", headline="Bitcoin rallies on ETF news",
                        detail_text="", ticker="BTC", selected_group=None,
                        old_association=a1["new_association"])
        assert a2["action"] == "no_change"
        assert a2["new_association"] == a1["new_association"]

        # capture metadata present -> reclassification can change the result
        a3 = repair_row(row_id="h2", headline="Ordinary headline",
                        detail_text="", ticker="", selected_group="SP400",
                        old_association="AMBIGUOUS")
        assert a3["action"] == "reclassified"
        assert a3["new_association"] == "SPY"
        assert a3["capture_metadata_used"] is True
    check("repair_routing.repair_row: idempotent on a second pass, "
         "capture-metadata-aware", _repair_routing_idempotent)

    def _backup_artifacts():
        from news.repair_routing import backup_artifacts

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "raw.csv"
            src.write_text("a,b\n1,2\n", encoding="utf-8")
            dest = backup_artifacts([src, base / "does_not_exist.csv"],
                                    base / "_backups")
            assert (dest / "raw.csv").exists()
            assert (dest / "raw.csv").read_text(encoding="utf-8") == \
                "a,b\n1,2\n"
            # a nonexistent path is silently skipped, never crashes
            assert not (dest / "does_not_exist.csv").exists()
    check("repair_routing.backup_artifacts: copies existing files, skips "
         "missing ones, never raises", _backup_artifacts)

    # ==================================== NEWS-panel scoping (duck-typed)
    def _news_panel_scoping_never_document_wide():
        """
        _find_group_dropdown must search ONLY inside the panel element it
        is given - never fall back to self.controller.driver. A fake
        'panel' whose find_elements returns a Quote-Monitor-lookalike
        dropdown proves the function still uses it (scoped), and a
        driver stand-in that would raise if ever touched proves no
        document-wide fallback occurs.
        """
        from news.godel_reader import GodelNewsReader

        class ExplodingDriver:
            """Any attribute access means scoping was violated."""
            def __getattr__(self, name):
                raise AssertionError(
                    f"_find_group_dropdown touched the driver directly "
                    f"via .{name} instead of staying scoped to `panel`")

        class FakeOption:
            def __init__(self, text):
                self.text = text

        class FakeSelect:
            """Looks like a native <select> with AB4.1/SP400 options."""
            def find_elements(self, by, sel):
                if sel == "option":
                    return [FakeOption("AB4.1"), FakeOption("SP400")]
                return []
            def is_displayed(self):
                return True

        class FakePanel:
            """Scoped subtree containing only the news-group <select>."""
            def find_elements(self, by, sel):
                if sel == "select":
                    return [FakeSelect()]
                return []   # no custom-dropdown candidates either

        reader = GodelNewsReader.__new__(GodelNewsReader)   # skip __init__
        reader.controller = type("C", (), {"driver": ExplodingDriver()})()
        found = reader._find_group_dropdown(FakePanel())
        assert found is not None and found[0] == "select"
    check("_find_group_dropdown: scoped strictly to the given panel, never "
         "touches the driver/document directly", _news_panel_scoping_never_document_wide)

    def _dropdown_found_while_showing_dow30():
        """2026-10-31: the panel was left on DOW30 by a Dow 30 capture and
        the dropdown (recognised only by AB4.1/SP400) was 'not found'."""
        from news.godel_reader import GodelNewsReader, known_group_labels

        class FakeCustom:
            def __init__(self, text):
                self.text = text
            def is_displayed(self):
                return True

        class FakePanel:
            def __init__(self, text):
                self.el = FakeCustom(text)
            def find_elements(self, by, sel):
                return [self.el] if sel == "[role='combobox']" else []

        reader = GodelNewsReader.__new__(GodelNewsReader)
        assert "DOW30" in known_group_labels()
        found = reader._find_group_dropdown(FakePanel("DOW30"))
        assert found is not None and found[0] == "custom"
        # an unknown label is still not guessed at - but it is reported
        unknown = FakePanel("My Watchlist")
        assert reader._find_group_dropdown(unknown) is None
        assert reader._dropdown_candidate_texts(unknown) == ["My Watchlist"]
    check("_find_group_dropdown: recognised while showing DOW30; unknown "
          "labels reported, not guessed", _dropdown_found_while_showing_dow30)

    def _configured_watchlist_name():
        """2026-10-31: the BTC watchlist is named 'quantAB4.1' in Godel, so
        the exact-match switch never found 'AB4.1'. The name is a setting;
        it must reach selection, the sanity check and routing - and the
        historical/near-miss labels must still not cross-match."""
        import os
        import subprocess
        code = (
            "from news.godel_reader import resolve_group, row_group_consistency, "
            "group_label_matches, known_group_labels\n"
            "from news.asset_routing import GROUP_TO_ASSET, classify_association\n"
            "assert resolve_group('btc') == 'quantAB4.1'\n"
            "assert 'quantAB4.1' in known_group_labels()\n"
            "assert row_group_consistency([{'ticker': 'BTC'}], 'quantAB4.1') == (1.0, 1)\n"
            "assert GROUP_TO_ASSET['quantAB4.1'] == 'BTC' and GROUP_TO_ASSET['AB4.1'] == 'BTC'\n"
            "assert classify_association(headline='x', selected_group='quantAB4.1', "
            "group_verified=True)['association'] == 'BTC'\n"
            "assert not group_label_matches('quantAB4.1', 'AB4.1')\n"
            "assert not group_label_matches('AB4.1', 'quantAB4.1')\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GODEL_")}
        env["GODEL_GROUP_BTC"] = "quantAB4.1"
        root = str(Path(__file__).resolve().parent.parent)
        # -c with cwd=root: local_settings.py is importable there, but the
        # env var wins over it, so the assertion is about the setting itself
        out = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr[-600:]
    check("GODEL_GROUP_BTC: configured watchlist name reaches switch, "
          "sanity check and routing", _configured_watchlist_name)

    def _news_panel_lookup_returns_none_is_fail_closed():
        """When the NEWS heading can't be found, switch_news_group must
        fail closed with a clear reason - never proceed to scrape."""
        from news.godel_reader import GodelNewsReader

        reader = GodelNewsReader.__new__(GodelNewsReader)
        reader.controller = type("C", (), {"driver": None})()
        reader._find_news_panel = lambda: None
        out = reader.switch_news_group("btc")
        assert out["status"] == "unavailable"
        assert out["reason"] == "news_panel_not_found"
        assert out["selected_group"] is None
    check("switch_news_group: fails closed when the NEWS panel isn't found",
         _news_panel_lookup_returns_none_is_fail_closed)

    def _group_switch_fails_closed_on_verification_mismatch():
        """Selection succeeds but the dropdown reads back something else
        (or decoration that isn't a real match) -> unavailable, never a
        silent pass-through to whatever is currently showing."""
        from news.godel_reader import GodelNewsReader

        reader = GodelNewsReader.__new__(GodelNewsReader)
        reader.controller = type("C", (), {"driver": None})()
        reader._find_news_panel = lambda: object()
        reader._find_group_dropdown = lambda panel: ("select", object())
        reader._current_group_label = lambda kind, el: "SP400"  # wrong group
        reader._select_group_native = lambda el, wanted: True   # "succeeds"
        reader.wait_for_dom_change = lambda **kw: {"changed": False}
        reader._row_fingerprint = lambda: (0, "x")
        out = reader.switch_news_group("btc")   # wants AB4.1, dropdown says SP400
        assert out["status"] == "unavailable"
        assert "verification_failed" in out["reason"]
    check("switch_news_group: fails closed when the post-selection label "
         "doesn't verify, never scrapes the wrong group", _group_switch_fails_closed_on_verification_mismatch)

    def _group_switch_low_ticker_consistency_fails_closed():
        """Dropdown verifies correctly but the visible rows look nothing
        like the requested universe - treated as a fail-closed signal."""
        from news.godel_reader import GodelNewsReader

        reader = GodelNewsReader.__new__(GodelNewsReader)
        reader.controller = type("C", (), {"driver": None})()
        reader._find_news_panel = lambda: object()
        reader._find_group_dropdown = lambda panel: ("select", object())
        reader._current_group_label = lambda kind, el: "AB4.1"  # already selected
        equity_rows = [{"ticker": t} for t in
                      ("AAPL", "MSFT", "GOOGL", "AMZN", "TSLA")]
        reader.read_news_rows = lambda: equity_rows
        out = reader.switch_news_group("btc")
        assert out["status"] == "unavailable"
        assert "row_ticker_consistency_low" in out["reason"]
    check("switch_news_group: low row/group ticker consistency fails closed "
         "even after a verified dropdown label", _group_switch_low_ticker_consistency_fails_closed)

    if failures:
        print(f"\n{len(failures)} FAILURE(S)")
        for name, e in failures:
            print(f"  - {name}: {type(e).__name__}: {e}")
        return 1
    print("\nALL NEWS ROUTING TESTS PASSED (offline, no browser)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
