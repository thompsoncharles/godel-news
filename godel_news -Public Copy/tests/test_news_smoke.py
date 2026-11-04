"""
Offline smoke tests for the Godel news program - synthetic data only, no
network, no browser. Moved here from the trading bot's tests/test_quant_smoke.py
when news became its own program.
Run: venv\Scripts\python.exe -m tests.test_news_smoke
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from tests.test_news_routing import isolate_from_local_settings
    isolate_from_local_settings()
    failures = []

    def check(name, fn):
        try:
            fn()
            print(f"  OK   {name}")
        except Exception as e:
            failures.append((name, e))
            print(f"  FAIL {name}: {e}")

    # --- news: row parsing + dedup
    def _news_parse_dedup():
        from types import SimpleNamespace
        from news.godel_reader import GodelNewsReader, parse_news_row
        from news.interpreter import headline_hash
        rec = parse_news_row(["09:31", "2026-07-04", "BTC", "Reuters",
                              "SEC approves new spot bitcoin ETF listing standards"])
        assert rec and rec["headline"].startswith("SEC approves")
        assert rec["ticker"] == "BTC" and rec["source"] == "Reuters"
        assert rec["time"] == "09:31" and rec["date"] == "2026-07-04"
        assert parse_news_row(["", "  "]) is None          # junk row dropped
        assert parse_news_row(
            ["Headline", "Date", "Time", "Ticker", "Source"]) is None
        assert parse_news_row(["Headline Date Time Ticker"]) is None

        # Batched DOM payload keeps its clickable element while parsing all
        # row text in one browser round trip.
        marker = object()
        class BatchDriver:
            def __init__(self):
                self.calls = 0
            def execute_script(self, *args):
                self.calls += 1
                return [{"element": marker, "cells": [
                    "Bitcoin batch headline is long enough", "7/12/26",
                    "01:00", "BTC", "Reuters"]}]
            def find_elements(self, *args):
                raise AssertionError("Selenium fallback should not run")
        driver = BatchDriver()
        reader = object.__new__(GodelNewsReader)
        reader.controller = SimpleNamespace(driver=driver)
        rows = reader.read_news_rows()
        assert len(rows) == 1 and rows[0]["_element"] is marker
        assert rows[0]["headline"].startswith("Bitcoin batch")
        assert driver.calls == 1

        h1 = headline_hash("Bitcoin rallies", "2026-07-04", "09:00", "BTC")
        h2 = headline_hash("Bitcoin rallies", "2026-07-04", "09:00", "BTC")
        h3 = headline_hash("Bitcoin rallies", "2026-07-05", "09:00", "BTC")
        assert h1 == h2 and h1 != h3                       # stable + date-sensitive
    check("news row parsing + dedup hashing", _news_parse_dedup)

    # --- news: deterministic interpretation + coefficients
    def _news_interpret():
        from news.interpreter import TRADE_IMPACT_ENABLED, coefficients, interpret
        assert TRADE_IMPACT_ENABLED is False
        bull = interpret("Spot bitcoin ETF sees record inflows as BlackRock buys",
                         source="Reuters", ticker="BTC")
        assert bull["sentiment"] == "bullish" and bull["btc_relevance"] >= 90
        assert bull["impact_direction"] == "up"
        assert "ETF" in bull["categories"] and "institutional" in bull["categories"]
        risk = interpret("BREAKING: major crypto exchange halts withdrawals after hack")
        assert risk["sentiment"] == "risk_event" and risk["impact_direction"] == "down"
        assert risk["impact_horizon"] == "intraday"
        assert risk["btc_relevance"] >= 60      # crypto-wide event is BTC-relevant
        # determinism: same input -> identical output
        assert interpret("Fed signals rate cut") == interpret("Fed signals rate cut")
        c = coefficients(bull, source="Reuters", novelty=1.0)
        assert c["trade_impact_enabled"] is False
        assert 0 < c["final_news_alpha_coeff"] <= 1
        assert c["source_reliability_coeff"] == 0.9
        cb = coefficients(risk, source="twitter", novelty=0.3)
        assert cb["final_news_alpha_coeff"] < 0 and cb["novelty_coeff"] == 0.3
    check("news interpretation + coefficients (deterministic)", _news_interpret)

    # --- scoring v2: whole words only (v1 matched inside other words)
    def _whole_word_matching():
        from news.interpreter import interpret
        sw = interpret("Microsoft software sales move toward record", "")
        assert sw["sentiment"] == "neutral", sw          # 'war' in software/toward
        bank = interpret("Bank sector update: billions in deposits, second quarter")
        assert bank["sentiment"] == "neutral" and bank["categories"] == "general", bank
        war = interpret("War escalates as exchange halts withdrawals")
        assert war["sentiment"] == "risk_event" and war["impact_direction"] == "down"
        etfs = interpret("Spot bitcoin ETFs see inflows")              # plural still counts
        assert "ETF" in etfs["categories"] and etfs["btc_relevance"] == 100, etfs
        partner = interpret("Coinbase partners with Visa on new token")
        assert partner["sentiment"] == "bullish" and partner["btc_relevance"] == 60
        assert "equity_crypto_proxy" in partner["categories"]
        hood = interpret("Robinhood shares rise")
        assert hood["btc_relevance"] == 50, hood
        athletic = interpret("Athletic brand path to growth rather strong")
        assert athletic["sentiment"] == "neutral", athletic           # 'ath' inside words
    check("scoring v2: keywords match whole words (+plural), not inside other words",
          _whole_word_matching)

    # --- scoring v3: strict lists, longest phrase wins, neutral phrases
    def _strict_v3():
        from news.interpreter import _variants, interpret
        def s(text):
            return interpret(text)["sentiment"]
        # reversals outrank the bearish word inside them
        for text in ("Court lifts ban on crypto exchange", "SEC drops charges against founder",
                     "Judge: lawsuit dismissed", "Sanctions lifted on exchange"):
            assert s(text) == "bullish", text
        # misfires seen in the stored articles stay neutral
        for text in ("Supply routes reopen after strike", "Vendors sell their wares online",
                     "Wallet enabled by default for users", "GBTC charges 1.5% in fees",
                     "Selling pressure is easing", "CleanSpark has a 52-week low of $8.00 "
                     "and a 52-week high of $23.61", "New Gears of War game announced",
                     "Oil above pre-war levels", "Saving for an emergency fund",
                     "No securities regulatory authority has approved or disapproved",
                     "Analyst Upgrades and Downgrades", "Attorney declined to comment",
                     "Treasury yield climbed", "Memecoins launched by celebrities"):
            assert s(text) == "neutral", (text, interpret(text)["explanation"])
        # strict phrases still fire
        assert s("Stock hits 52-week low") == "bearish"
        assert s("Meta sparks price war") == "bearish"               # competition, not risk
        assert s("Argentina debt default looms") == "risk_event"
        assert s("Coinbase upgraded to buy at Citi") == "bullish"
        assert s("Microsoft beats estimates, raises guidance") == "bullish"
        assert s("Fed signals quantitative easing") == "bullish"
        flash = interpret("Flash crash hits altcoins")
        assert flash["sentiment"] == "risk_event" and flash["impact_direction"] == "down"
        # regular plurals only
        assert _variants("war") == {"war", "wars"} and _variants("crash") == {"crash", "crashes"}
    check("scoring v3: strict lists, reversals win, misfires neutral, plural rules",
          _strict_v3)

    # --- scoring v4: the headline decides; the article only breaks a neutral headline
    def _headline_decides():
        from news.interpreter import coefficients, interpret
        noisy = ("Analysts warned of a crash, a selloff and outflows. Some called it a "
                 "bloodbath and a meltdown. ")
        # headline wins even against a strongly bearish article
        up = interpret("Coinbase beats estimates", noisy)
        assert (up["sentiment"], up["sentiment_basis"]) == ("bullish", "headline"), up
        # neutral headline: the article decides only with clear evidence
        clear = interpret("Coinbase quarterly update", noisy)
        assert (clear["sentiment"], clear["sentiment_basis"]) == ("bearish", "article"), clear
        assert clear["confidence"] == 0.9                     # 5 article terms decided it
        for article in ("Shares rallied after the selloff.",              # 1 v 1
                        "A rally, a surge, then a crash and an outflow",  # 2 v 2
                        "It soared and rallied, then a selloff came."):    # 2 v 1: < 3 terms
            row = interpret("Coinbase quarterly update", article)
            assert (row["sentiment"], row["sentiment_basis"]) == ("neutral", "none"), (article, row)
        one_risk = interpret("Exchange update", "Analysts mentioned the war briefly.")
        assert one_risk["sentiment"] == "neutral", one_risk     # one passing risk word
        two_risk = interpret("Exchange update", "The hack drained wallets during an outage.")
        assert (two_risk["sentiment"], two_risk["sentiment_basis"]) == ("risk_event", "article")
        # a headline risk term still decides, whatever the article says
        risk = interpret("Exchange hacked", "Prices rallied and surged to an all-time high.")
        assert (risk["sentiment"], risk["sentiment_basis"]) == ("risk_event", "headline")
        assert risk["impact_direction"] == "down"             # the article's bullish words don't count
        # a tied headline defers to the article
        tie = interpret("Rally fades into selloff", "It soared, rallied and surged again.")
        assert (tie["sentiment"], tie["sentiment_basis"]) == ("bullish", "article"), tie
        # neutral rows carry no signal: score 0, alpha 0
        flat = interpret("Coinbase quarterly update", "Shares rallied after the selloff.")
        c = coefficients(flat, "Reuters News")
        assert c["news_sentiment_score"] == 0 and c["final_news_alpha_coeff"] == 0
        assert flat["explanation"].startswith("neutral (no strong keywords) with")
    check("scoring v4: headline decides; article breaks only a neutral headline, clearly",
          _headline_decides)

    # --- scoring v5: strong words weigh 2; the score is net weight / 4
    def _keyword_weights():
        from news import interpreter as I
        assert I.STRONG <= (I.BULLISH | I.BEARISH | I.RISK_EVENT)
        def score(text):
            o = I.interpret(text)
            return o["sentiment"], I.coefficients(o)["news_sentiment_score"], o["confidence"]
        assert score("Bitcoin turns bearish") == ("bearish", -0.25, 0.4)       # mild
        assert score("Bitcoin bloodbath") == ("bearish", -0.5, 0.55)           # strong
        assert score("Bitcoin bloodbath as Fed turns hawkish") == ("bearish", -0.75, 0.7)
        assert score("Bitcoin rallies despite crash")[0] == "bearish"          # 1 v 2
        assert score("Coinbase beats estimates, raises guidance")[:2] == ("bullish", 0.75)
        assert score("Exchange hacked")[1] == -1.0                             # bearish + risk, strong
        assert score("Rally and selloff")[:2] == ("neutral", 0.0)              # 1 v 1
        # the article tie-break still counts words: one strong word can't decide
        one = I.interpret("Coinbase quarterly update", "Some feared a crash.")
        assert one["sentiment"] == "neutral", one
        # an article-decided score never outranks a headline word (cap 0.25)
        loud = I.interpret("Coinbase quarterly update", "A crash, a bloodbath, a meltdown, "
                           "a selloff and record outflows.")
        assert loud["sentiment_basis"] == "article"
        assert I.coefficients(loud)["news_sentiment_score"] == -I.ARTICLE_MAX_SCORE == -0.25
    check("scoring v5: strong words weigh 2; score = net weight / 4, capped at 1",
          _keyword_weights)

    # --- scoring v2: Godel's long source names reach the reliability table
    def _source_names():
        from news.interpreter import source_reliability
        expect = {"Reuters News": 0.9, "Associated Press": 0.85, "CNBC TV Transcripts": 0.8,
                  "Block, The": 0.7, "reuters": 0.9, "FT": 0.85, "X": 0.3,
                  "V": 0.5, "Ft. Worth Star-Telegram": 0.5, "Benzinga Lightning Feed": 0.5,
                  "": 0.5, "Cheap Talk Weekly": 0.5}
        got = {s: source_reliability(s) for s in expect}
        assert got == expect, {s: (got[s], expect[s]) for s in expect if got[s] != expect[s]}
    check("scoring v2: source names like 'Reuters News' get the table score",
          _source_names)

    # --- scoring v2: one word counts once; explanation identical every run
    def _confidence_and_explanation():
        import os
        import subprocess
        from news.interpreter import interpret
        seized = interpret("U.S. Government Moves Seized Bitcoin To Exchange")
        assert seized["confidence"] == 0.4, seized       # bearish+risk word = 1 signal
        assert seized["explanation"] == \
            "risk_event (seized) from headline with BTC relevance 90", seized
        code = ("from news.interpreter import interpret;"
                "print(interpret('Bitcoin rally surges after ETF approval, bullish breakout')"
                "['explanation'])")
        root = str(Path(__file__).resolve().parent.parent)
        outs = {subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                               text=True, env={**os.environ, "PYTHONHASHSEED": seed}
                               ).stdout.strip() for seed in ("1", "2", "3", "4")}
        assert len(outs) == 1 and next(iter(outs)), outs
    check("scoring v2: confidence counts distinct words; explanation is stable",
          _confidence_and_explanation)

    # --- old rows are re-scored from raw; novelty follows the duplicate flags
    def _rescore_and_novelty():
        import pandas as pd
        from news.interpreter import NOVELTY_DUPLICATE, SCORING_VERSION, news_alpha
        from news.store import finalize_interpreted
        old_row = {"headline_hash": "v1", "headline": "Bitcoin rally continues",
                   "date": "10/27/26", "time": "09:00:00", "source": "Reuters News",
                   "ticker": "BTC", "sentiment": "risk_event", "btc_relevance": "90",
                   "news_sentiment_score": "-1", "novelty_coeff": "1"}
        copy = {**old_row, "headline_hash": "v1b", "time": "15:00:00"}
        raw = pd.DataFrame([{"headline_hash": "v1", "headline": "Bitcoin rally continues",
                             "detail_text": "Analysts saw a software update toward ...",
                             "source": "Reuters News", "ticker": "BTC"}])
        out = finalize_interpreted(pd.DataFrame([old_row, copy]), raw).set_index("headline_hash")
        first, dup = out.loc["v1"], out.loc["v1b"]
        assert first["sentiment"] == "bullish", first.to_dict()    # 'war' in body ignored
        assert str(first["scoring_version"]) == str(SCORING_VERSION)
        assert first["source_reliability_coeff"] == 0.9
        assert first["novelty_coeff"] == 1.0 and dup["novelty_coeff"] == NOVELTY_DUPLICATE
        assert bool(dup["is_duplicate"]) and dup["duplicate_type"] == "exact"
        assert dup["final_news_alpha_coeff"] == news_alpha(
            float(dup["news_sentiment_score"]), float(dup["btc_relevance_coeff"]),
            float(dup["source_reliability_coeff"]), float(dup["urgency_coeff"]),
            NOVELTY_DUPLICATE)
        again = finalize_interpreted(out.reset_index(), raw)        # current rows untouched
        assert again.set_index("headline_hash").loc["v1", "sentiment"] == "bullish"
    check("re-score: old-version rows re-scored from raw text; novelty from duplicates",
          _rescore_and_novelty)

    # --- news: expanded-article scroll capture (mocked container)
    def _news_scroll():
        from news.godel_reader import capture_scrolling_text, merge_scroll_chunks
        assert merge_scroll_chunks(["a\nb", "b\nc", "c\nd"]) == "a\nb\nc\nd"
        pages = ["line1\nline2", "line2\nline3", "line3\nline3-end", "line3\nline3-end"]
        state = {"i": 0}
        def get_text():
            return pages[min(state["i"], len(pages) - 1)]
        def scroll_once():
            state["i"] += 1
            return min(state["i"], 2) * 100   # scrollTop stops changing at bottom
        text, steps = capture_scrolling_text(get_text, scroll_once, max_steps=20)
        assert text == "line1\nline2\nline3\nline3-end"
        assert steps <= 4                      # stopped on scrollTop stall, not max
        # max-steps cap honored on an endless container
        _, steps = capture_scrolling_text(lambda: "x", lambda: state.__setitem__("i", state["i"] + 1) or state["i"] * 100, max_steps=5)
        assert steps == 5
    check("news article scroll capture (merge + stall + cap)", _news_scroll)

    # --- news: store dedup round-trip on scratch CSVs + Excel export
    def _news_store_excel():
        import news.store as ns
        from news import config as qc
        scratch_dir = qc.DATA_DIR / "news_smoke_test"
        old = (ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV)
        ns.NEWS_DIR = scratch_dir
        ns.RAW_CSV = scratch_dir / "raw.csv"
        ns.INTERPRETED_CSV = scratch_dir / "interp.csv"
        try:
            long_detail = "full article paragraph\n" * 2000
            rows = [{"headline": "Bitcoin surges as ETF inflows hit record",
                     "date": "2026-07-04", "time": "09:00", "ticker": "BTC",
                     "source": "Reuters", "detail_status": "ok",
                     "detail_text": long_detail}]
            r1 = ns.append_news(rows)
            r2 = ns.append_news(rows)              # same row again -> dedup
            assert r1["new"] == 1 and r2["new"] == 0 and r2["duplicates_skipped"] == 1
            df = ns.load_interpreted()
            assert len(df) == 1
            assert df.iloc[0]["trade_impact_enabled"] in ("False", False)
            raw_df = ns._load(ns.RAW_CSV)
            assert raw_df.iloc[0]["detail_text"] == long_detail
            assert int(raw_df.iloc[0]["detail_text_chars"]) == len(long_detail)
            # Excel export against the scratch store AND a scratch workbook
            # (never touches the real report file - avoids OneDrive locks)
            import importlib
            import news.excel_report as xr
            importlib.reload(xr)                    # rebind RAW_CSV/INTERPRETED_CSV
            excel_raw = xr._excel_safe_raw(raw_df)
            assert len(excel_raw.iloc[0]["detail_text"]) == xr.EXCEL_CELL_TEXT_LIMIT
            assert excel_raw.iloc[0]["detail_text_truncated_for_excel"] in (True, "True")
            xr.RAW_CSV, xr.INTERPRETED_CSV = ns.RAW_CSV, ns.INTERPRETED_CSV
            xr.XLSX_PATH = scratch_dir / "godel_news_report_smoke.xlsx"
            res = xr.write_news_excel()
            if res["excel_path"] is not None:
                assert res["excel_status"] == "written"
                import openpyxl
                wb = openpyxl.load_workbook(res["excel_path"])
                assert wb.sheetnames == ["Summary", "Raw Headlines",
                                         "Interpreted Headlines",
                                         "High Relevance BTC News", "Risk Events",
                                         "Source Counts", "Coefficient History"]
        finally:
            ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV = old
            import shutil
            shutil.rmtree(scratch_dir, ignore_errors=True)
    check("news store dedup + Excel export (7 sheets)", _news_store_excel)

    # --- news: graceful failure without credentials/session
    def _news_graceful():
        import os
        from news.godel_reader import load_credentials
        saved = {k: os.environ.pop(k, None) for k in ("GODEL_USERNAME", "GODEL_PASSWORD")}
        try:
            os.environ["GODEL_USERNAME"] = "your_email@example.com"
            os.environ["GODEL_PASSWORD"] = "your_password"
            assert load_credentials() is None      # placeholders rejected
            os.environ["GODEL_USERNAME"] = "real@user.com"
            os.environ["GODEL_PASSWORD"] = "hunter2secret"
            assert load_credentials() == ("real@user.com", "hunter2secret")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("news graceful failure without credentials", _news_graceful)

    # --- news: attach-to-existing-Brave mode (all offline)
    def _news_attach_mode():
        import os
        from news.godel_reader import (
            attach_instructions, browser_mode, cdp_available, cdp_url,
            match_godel_tab, run_news_scrape,
        )
        from news import config as cfg
        saved = {k: os.environ.pop(k, None)
                 for k in ("GODEL_BROWSER_MODE", "GODEL_CDP_URL")}
        saved_local, cfg._local = cfg._local, None   # ignore local_settings.py
        try:
            # mode resolution: default attach, env override, junk -> attach
            assert browser_mode() == "attach"
            os.environ["GODEL_BROWSER_MODE"] = "launch"
            assert browser_mode() == "launch"
            os.environ["GODEL_BROWSER_MODE"] = "bogus"
            assert browser_mode() == "attach"
            # tab matching: register-URL with tracking params still matches
            urls = ["https://news.ycombinator.com",
                    "https://app.godelterminal.com/?page=register&_gl=1*1wvv5xu*_gcl_au*MjE0NTQ5OTM4My4xNzgzODE2MzY0",
                    "https://mail.google.com"]
            assert match_godel_tab(urls) == 1
            assert match_godel_tab(["https://example.com"]) is None
            assert match_godel_tab([]) is None
            # CDP endpoint config + graceful unavailability (dead port)
            os.environ["GODEL_CDP_URL"] = "http://127.0.0.1:59999"
            assert cdp_url() == "http://127.0.0.1:59999"
            assert cdp_available(timeout=0.5) is False
            instr = attach_instructions()
            # instructions reflect the ACTUALLY configured port (59999 here),
            # not a hardcoded default - a dead port must not print bad advice
            assert "--remote-debugging-port=59999" in instr
            assert "launch" in instr                     # fallback documented
            # end-to-end: attach mode + dead CDP -> graceful unavailable,
            # no browser is launched, routine-safe
            os.environ["GODEL_BROWSER_MODE"] = "attach"
            out = run_news_scrape()
            assert out["status"] == "unavailable"
            assert "remote-debugging-port" in out["reason"]
            assert "run_news.bat login" in out["reason"]
        finally:
            cfg._local = saved_local
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("news attach mode (env config, tab match, graceful CDP-down)", _news_attach_mode)

    # --- news: harvest loop returns to list, dedups by hash, stops cleanly
    def _news_harvest_navigation():
        from news.godel_reader import harvest_articles, next_unprocessed, row_hash

        rows = [
            {"headline": f"Bitcoin headline number {i} with enough length",
             "date": "2026-07-11", "time": f"0{i}:00", "ticker": "BTC",
             "source": "Reuters"} for i in range(4)
        ]

        # next_unprocessed picks by hash, not index
        processed = {row_hash(rows[0])}
        assert next_unprocessed(rows, processed)["time"] == "01:00"
        assert next_unprocessed(rows, {row_hash(r) for r in rows}) is None

        class StubReader:
            """Simulates: every article replaces the list; return works
            twice, then breaks (the exact reported failure mode)."""
            def __init__(self):
                self.returns = 0
                self.requeries = 0
            def read_news_rows(self):
                self.requeries += 1
                return [dict(r) for r in rows]      # fresh objects = fresh DOM
            def expand_and_capture(self, row):
                return {**row, "detail_status": "ok", "detail_text": "body"}
            def return_to_news_list(self):
                self.returns += 1
                return self.returns <= 2
        stub = StubReader()
        out = harvest_articles(stub, set(), max_articles=10)
        # read 3 articles: two returned fine, third marked return_failed, stop
        assert len(out) == 3
        assert [o["detail_status"] for o in out] == ["ok", "ok", "return_failed"]
        assert out[2]["detail_text"] == "body"      # captured text preserved
        assert stub.requeries == 3                  # re-queried before each pick
        # no duplicates: each processed headline picked exactly once
        assert len({row_hash(o) for o in out}) == 3

        # max_articles bound: endless identical-looking feed cannot spin
        class EndlessReader(StubReader):
            def read_news_rows(self):
                self.requeries += 1
                return [{"headline": f"unique headline {self.requeries} padded out",
                         "date": "d", "time": "t", "ticker": "BTC", "source": "s"}]
            def return_to_news_list(self):
                return True
        out = harvest_articles(EndlessReader(), set(), max_articles=5)
        assert len(out) == 5

        # a raising reader must not propagate (routine safety)
        class BrokenReader:
            def read_news_rows(self):
                raise RuntimeError("browser died")
        assert harvest_articles(BrokenReader(), set()) == []
    check("news harvest navigation (return, hash dedup, clean stop)", _news_harvest_navigation)

    # --- news: overlay-intercepted clicks (regression for live-observed
    #     ElementClickInterceptedException from <p class="my-3 text-[#999999]">)
    def _news_overlay_regression():
        from news.godel_reader import harvest_articles, row_hash

        rows = [
            {"headline": f"Bitcoin overlay case number {i} long enough to parse",
             "date": "2026-07-11", "time": f"1{i}:00", "ticker": "BTC",
             "source": "Reuters"} for i in range(3)
        ]

        class OverlayReader:
            """Article detail stays open over the table after each capture:
            rows still exist in the DOM, but a click while the overlay is
            open would be intercepted -> expand reports blocked_by_overlay.
            return_to_news_list() is what actually closes the overlay."""
            def __init__(self, return_clears=True):
                self.overlay_open = False
                self.return_calls = 0
                self.events = []
                self.return_clears = return_clears
            def read_news_rows(self):
                self.events.append("requery")
                return [dict(r) for r in rows]      # rows always in DOM
            def expand_and_capture(self, row):
                if self.overlay_open:               # click would be intercepted
                    self.events.append("blocked")
                    return {**row, "detail_status": "blocked_by_overlay",
                            "detail_text": ""}
                self.events.append("captured")
                self.overlay_open = True            # detail view stays open
                return {**row, "detail_status": "ok", "detail_text": "article body"}
            def return_to_news_list(self):
                self.events.append("return")
                self.return_calls += 1
                if self.return_clears:
                    self.overlay_open = False
                return True

        # healthy path: overlay cleared between articles -> every capture ok,
        # and return_to_news_list is called BEFORE each next row selection
        r = OverlayReader(return_clears=True)
        out = harvest_articles(r, set(), max_articles=10)
        assert [o["detail_status"] for o in out] == ["ok", "ok", "ok"]
        for i in range(len(out) - 1):               # return between captures
            c1 = r.events.index("captured")
            assert "return" in r.events[c1:], "no return before next row"
        assert r.return_calls >= len(out)
        # dedup by hash: every article distinct, none re-captured
        assert len({row_hash(o) for o in out}) == 3

        # stuck-overlay path: return "succeeds" but the overlay never closes
        # -> bounded retries, blocked_by_overlay recorded, clean stop, and
        # the previously captured article text is kept
        r2 = OverlayReader(return_clears=False)
        out2 = harvest_articles(r2, set(), max_articles=10)
        assert out2[0]["detail_status"] == "ok"
        assert out2[0]["detail_text"] == "article body"   # text preserved
        assert out2[-1]["detail_status"] == "blocked_by_overlay"
        assert len(out2) == 2                             # stopped cleanly
        assert r2.events.count("blocked") == 2            # bounded retries
    check("news overlay interception regression (clear before next row)", _news_overlay_regression)

    # --- news: attach layout detection proceeds without a clickable AB4.1 tab
    def _news_layout_detection():
        import os
        from news.godel_reader import (
            GodelNewsReader, normalize_label, should_skip_layout_click,
        )
        # pure decision logic
        assert should_skip_layout_click(True, None) is True          # table visible
        assert should_skip_layout_click(False, "AB4.1") is True      # exact tab
        assert should_skip_layout_click(False, "  AB4.1\n") is True  # whitespace
        assert should_skip_layout_click(False, "Layout AB4.1 (live)") is True
        assert should_skip_layout_click(False, "dev") is False
        assert should_skip_layout_click(False, None) is False
        assert normalize_label("  AB4.1 \n news ") == "AB4.1 news"

        # attach-mode reader with the news table already visible: proceeds
        # even though every tab-lookup path raises (stubbed to fail hard)
        saved = os.environ.pop("GODEL_BROWSER_MODE", None)
        try:
            os.environ["GODEL_BROWSER_MODE"] = "attach"
            reader = GodelNewsReader()                # no browser launched
            assert reader.mode == "attach"
            reader.news_table_visible = lambda: True
            reader.active_layout_name = lambda: None
            def _boom(*a, **k):
                raise AssertionError("tab lookup must not be required")
            reader._click_layout_tab = _boom
            reader.controller.load_layout = _boom
            assert reader.open_news_layout() is True   # skipped, no click
            # and when the table is NOT visible but active tab reads AB4.1
            reader.news_table_visible = lambda: False
            reader.active_layout_name = lambda: "AB4.1"
            assert reader.open_news_layout() is True
        finally:
            os.environ.pop("GODEL_BROWSER_MODE", None)
            if saved is not None:
                os.environ["GODEL_BROWSER_MODE"] = saved
    check("news attach layout detection (skip click when already there)", _news_layout_detection)

    # --- news: in-app '<Back' preferred, browser back last resort
    def _news_return_ladder():
        import os
        from news.godel_reader import GodelNewsReader
        saved = os.environ.pop("GODEL_BROWSER_MODE", None)
        try:
            os.environ["GODEL_BROWSER_MODE"] = "attach"
            reader = GodelNewsReader()            # offline - no browser
            calls = []
            state = {"article_open": True}

            def article_back():
                calls.append("godel_article_back")
                state["article_open"] = False     # in-app back closes the view
                return True
            def escape():
                calls.append("escape"); return True
            def layout_tab():
                calls.append("layout_tab"); return True
            def browser_back():
                calls.append("browser_back"); return True
            reader._return_via_article_back = article_back
            reader._return_via_escape = escape
            reader._return_via_layout_tab = layout_tab
            reader._return_via_browser_back = browser_back
            reader._list_clickable = lambda target_element=None: not state["article_open"]

            import news.godel_reader as gr
            saved_wait = gr.RETURN_WAIT_SECONDS
            gr.RETURN_WAIT_SECONDS = 0.6          # keep the poll loops fast
            try:
                assert reader.ensure_news_list_clickable() is True
            finally:
                gr.RETURN_WAIT_SECONDS = saved_wait
            # the in-app back was used; browser back was NEVER touched
            assert calls == ["godel_article_back"]
            assert "browser_back" not in calls

            # when no in-app back exists, ladder escalates; browser back is
            # reached only with GODEL_ALLOW_BROWSER_BACK explicitly enabled
            # AND after every in-app method failed
            calls.clear()
            state["article_open"] = True
            os.environ["GODEL_ALLOW_BROWSER_BACK"] = "true"
            reader._return_via_article_back = lambda: (calls.append("godel_article_back_na"), False)[1]
            def browser_back_works():
                calls.append("browser_back")
                state["article_open"] = False
                return True
            reader._return_via_escape = lambda: (calls.append("escape"), True)[1]
            reader._return_via_layout_tab = lambda: (calls.append("layout_tab"), True)[1]
            reader._return_via_browser_back = browser_back_works
            gr.RETURN_WAIT_SECONDS = 0.6
            try:
                assert reader.ensure_news_list_clickable() is True
            finally:
                gr.RETURN_WAIT_SECONDS = saved_wait
                os.environ.pop("GODEL_ALLOW_BROWSER_BACK", None)
            assert calls.index("escape") < calls.index("layout_tab") < calls.index("browser_back")
        finally:
            os.environ.pop("GODEL_BROWSER_MODE", None)
            if saved is not None:
                os.environ["GODEL_BROWSER_MODE"] = saved
    check("news return ladder (in-app Back first, browser back last)", _news_return_ladder)

    # --- news: long-article scroll capture (first paragraph is NOT the article)
    def _news_long_article_scroll():
        from news.godel_reader import capture_scrolling_text
        # a 4-viewport article: only para1 is visible before scrolling
        viewports = ["para1 first visible paragraph",
                     "para2 continues the article",
                     "para3 more detail",
                     "para4 the conclusion",
                     "para4 the conclusion"]       # bottom: text stops changing
        state = {"pos": 0}
        def get_text():
            return viewports[min(state["pos"], len(viewports) - 1)]
        def scroll_once():
            state["pos"] += 1
            return min(state["pos"], 3) * 500      # scrollTop stalls at bottom
        text, steps = capture_scrolling_text(get_text, scroll_once, max_steps=30)
        # every paragraph captured - visible-first-paragraph-only would fail this
        for para in ("para1", "para2", "para3", "para4"):
            assert para in text, f"lost {para} - partial capture!"
        assert text != viewports[0]                # not just the first screen
        assert steps < 30                          # stopped on stall, not cap
    check("news long-article scroll capture (all paragraphs)", _news_long_article_scroll)

    # --- news: browser back is GATED OFF by default in attach mode
    def _news_browser_back_gate():
        import os
        import news.godel_reader as gr
        from news.godel_reader import GodelNewsReader, browser_back_allowed
        saved = {k: os.environ.pop(k, None)
                 for k in ("GODEL_BROWSER_MODE", "GODEL_ALLOW_BROWSER_BACK")}
        try:
            assert browser_back_allowed() is False          # default: off
            os.environ["GODEL_ALLOW_BROWSER_BACK"] = "true"
            assert browser_back_allowed() is True
            os.environ.pop("GODEL_ALLOW_BROWSER_BACK", None)

            os.environ["GODEL_BROWSER_MODE"] = "attach"
            reader = GodelNewsReader()                      # offline
            calls = []
            reader._return_via_article_back = lambda: (calls.append("app"), False)[1]
            reader._return_via_escape = lambda: (calls.append("esc"), False)[1]
            reader._return_via_layout_tab = lambda: (calls.append("tab"), False)[1]
            reader._return_via_browser_back = lambda: (calls.append("browser_back"), True)[1]
            reader._list_clickable = lambda target_element=None: False
            saved_wait = gr.RETURN_WAIT_SECONDS
            gr.RETURN_WAIT_SECONDS = 0.3
            try:
                # default: every in-app rung fails, ladder exhausts, browser
                # back is NEVER invoked, return fails cleanly
                assert reader.ensure_news_list_clickable() is False
                assert "browser_back" not in calls
                # the direct method is guarded too
                reader2 = GodelNewsReader()
                assert reader2._return_via_browser_back() is False
                # explicitly enabled -> the rung joins the ladder
                os.environ["GODEL_ALLOW_BROWSER_BACK"] = "true"
                calls.clear()
                reader.ensure_news_list_clickable()
                assert "browser_back" in calls
            finally:
                gr.RETURN_WAIT_SECONDS = saved_wait
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("news browser-back gate (off by default in attach mode)", _news_browser_back_gate)

    # --- news: dedup in-run + across runs + best-status upgrade
    def _news_dedup_upgrade():
        import news.store as ns
        from news import config as qc
        from news.godel_reader import harvest_articles, row_hash
        scratch = qc.DATA_DIR / "news_dedup_test"
        old = (ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV)
        ns.NEWS_DIR, ns.RAW_CSV = scratch, scratch / "raw.csv"
        ns.INTERPRETED_CSV = scratch / "interp.csv"
        try:
            base = {"headline": "Bitcoin ETF ruling due at the district court",
                    "date": "2026-07-11", "time": "09:00", "ticker": "BTC",
                    "source": "Reuters"}
            # run 1: capture FAILED -> persisted, known, but retryable
            r1 = ns.append_news([{**base, "detail_status": "return_failed"}])
            assert r1["new"] == 1
            h = row_hash(base)
            assert h in ns.known_hashes()
            assert h not in ns.captured_hashes()      # failed -> retry allowed
            # run 2: full capture -> UPGRADES the same row, no duplicate
            r2 = ns.append_news([{**base, "detail_status": "ok",
                                  "detail_text": "full article text"}])
            assert r2["upgraded"] == 1 and r2["new"] == 0
            df = ns.load_interpreted()
            assert len(df) == 1 and df.iloc[0]["detail_status"] == "ok"
            assert h in ns.captured_hashes()          # now captured for good
            # run 3: a worse capture NEVER downgrades
            r3 = ns.append_news([{**base, "detail_status": "expand_failed"}])
            assert r3["duplicates_skipped"] == 1 and r3["upgraded"] == 0
            assert ns.load_interpreted().iloc[0]["detail_status"] == "ok"

            # across-run skip at harvest level: the captured headline is
            # never clicked again; only the unseen one is expanded
            other = {"headline": "Ethereum staking flows shift dramatically",
                     "date": "2026-07-11", "time": "10:00", "ticker": "ETH",
                     "source": "Reuters"}
            expanded = []
            class Stub:
                def read_news_rows(self):
                    return [dict(base), dict(other)]
                def expand_and_capture(self, row):
                    expanded.append(row["headline"])
                    return {**row, "detail_status": "ok", "detail_text": "t"}
                def return_to_news_list(self):
                    return True
            out = harvest_articles(Stub(), ns.captured_hashes(), max_articles=10)
            assert expanded == [other["headline"]]    # base was never clicked
            assert len(out) == 1
        finally:
            ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV = old
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
    check("news dedup (in-run, across runs, best-status upgrade)", _news_dedup_upgrade)

    # --- news: Excel PermissionError fallback (workbook open in Excel)
    def _news_excel_locked():
        import importlib
        import news.excel_report as xr
        from news import config as qc
        importlib.reload(xr)
        scratch = qc.DATA_DIR / "news_excel_lock_test"
        scratch.mkdir(parents=True, exist_ok=True)
        primary = scratch / "godel_news_report.xlsx"
        xr.XLSX_PATH = primary
        real_writer = xr.pd.ExcelWriter

        class LockedPrimaryWriter:
            def __new__(cls, path, **kw):
                if str(path) == str(primary):
                    raise PermissionError(13, "Permission denied", str(path))
                return real_writer(path, **kw)
        try:
            xr.pd.ExcelWriter = LockedPrimaryWriter
            res = xr.write_news_excel()               # must not raise
            assert res["excel_status"] == "locked_fallback"
            assert res["excel_path"] is not None
            assert "godel_news_report_" in res["excel_path"]   # timestamped

            xr.pd.ExcelWriter = lambda *a, **k: (_ for _ in ()).throw(
                PermissionError(13, "Permission denied"))
            res = xr.write_news_excel()               # both locked -> skip
            assert res["excel_status"] == "locked_skipped"
            assert res["excel_path"] is None
        finally:
            xr.pd.ExcelWriter = real_writer
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
            importlib.reload(xr)
    check("news Excel PermissionError (fallback workbook, no crash)", _news_excel_locked)

    # --- news: back-candidate ranking (debug-back diagnostic ordering)
    def _news_back_ranking():
        from news.godel_reader import rank_back_candidate
        real_back = {"tag": "button", "text": "‹Back", "class": "flex items-center",
                     "aria_label": "", "displayed": True, "n_children": 1}
        css_false_friend = {"tag": "div", "text": "", "class": "background-dark",
                            "aria_label": "", "displayed": True, "n_children": 8}
        hidden_back = {**real_back, "displayed": False}
        prose = {"tag": "p", "text": "prices bounce back after selloff",
                 "class": "my-3", "aria_label": "", "displayed": True,
                 "n_children": 0}
        aria_back = {"tag": "a", "text": "", "class": "", "aria_label": "Go back",
                     "displayed": True, "n_children": 0}
        # the literal '<Back' control must outrank every distractor
        assert rank_back_candidate(real_back) > rank_back_candidate(css_false_friend)
        assert rank_back_candidate(real_back) > rank_back_candidate(hidden_back)
        assert rank_back_candidate(real_back) > rank_back_candidate(prose)
        assert rank_back_candidate(real_back) > rank_back_candidate(aria_back)
        # aria-labelled control still beats css 'background' noise
        assert rank_back_candidate(aria_back) > rank_back_candidate(css_false_friend)
    check("news back-candidate ranking (debug-back)", _news_back_ranking)

    # --- news: article identity validation (stale-capture regression)
    def _news_article_identity():
        from news.godel_reader import (
            article_container_rejection_reason, article_matches_expected,
            body_signature, classify_capture, detail_text_for_status,
            extract_article_view, looks_like_news_table, title_similarity,
        )
        # fuzzy title match: punctuation/spacing/case differences PASS
        assert title_similarity(
            "Nium Acquires Cypher to Expand Fiat-to-On-Chain Infrastructure",
            "nium acquires cypher, to expand   fiat to on chain infrastructure") > 0.9
        # truncated title still matches
        assert title_similarity(
            "Bitcoin ETF inflows hit record high as institutions pile in",
            "Bitcoin ETF inflows hit record high as...") > 0.75
        # unrelated headline does NOT match
        assert title_similarity(
            "Fed holds rates steady in July meeting",
            "Solana outage resolved after 4 hours") < 0.5

        # article view parsing (live Godel format from the DOM dump)
        view = extract_article_view(
            "Back\nExport PDF\nNium Acquires Cypher to Expand\n"
            "Matched:COIN\nSource: Cryptopolitan\nBody paragraph one...")
        assert view["title"].startswith("Nium Acquires")
        assert view["matched"] == "COIN" and view["source"] == "Cryptopolitan"

        # source+date agreement matches even when the title is missing
        ok, _ = article_matches_expected(
            {"headline": "Something completely different", "source": "Reuters",
             "date": "7/10/26"},
            {"title": "", "source": "Reuters", "text": "published 7/10/26 body"})
        assert ok

        # STALE CAPTURE: new headline, but the panel still shows the OLD
        # article -> identity mismatch, and the old text is NOT saved
        old_body = "Back\nExport PDF\nOld Article About Solana\nMatched:SOL\nSource: X\nold body text here"
        old_sig = body_signature(old_body)
        status, score, sig = classify_capture(
            {"headline": "Brand new Bitcoin ETF headline", "source": "Reuters",
             "date": "7/11/26"},
            "Old Article About Solana", old_body, prev_body_sig=old_sig)
        assert status == "article_identity_mismatch"
        assert sig == old_sig                       # forensics preserved

        # SAME BODY under a DIFFERENT headline whose title somehow matches
        # (e.g. two near-identical headlines) -> suspicious_same_body
        dup_body = "Back\nExport PDF\nBitcoin rallies on ETF news\nMatched:BTC\nSource: R\nsame body"
        dup_sig = body_signature(dup_body)
        status, _, _ = classify_capture(
            {"headline": "Bitcoin rallies on ETF news", "source": "R",
             "date": ""},
            "Bitcoin rallies on ETF news", dup_body, prev_body_sig=dup_sig,
            same_headline_as_prev=False)
        assert status == "suspicious_same_body"
        # ...but the SAME headline re-captured is fine (dedup path, not stale)
        status, _, _ = classify_capture(
            {"headline": "Bitcoin rallies on ETF news", "source": "R",
             "date": ""},
            "Bitcoin rallies on ETF news", dup_body, prev_body_sig=dup_sig,
            same_headline_as_prev=True)
        assert status == "ok"
        # clean capture: matching title, fresh body -> ok
        status, score, _ = classify_capture(
            {"headline": "Bitcoin rallies on ETF news", "source": "R",
             "date": ""},
            "Bitcoin rallies on ETF news", dup_body, prev_body_sig="different")
        assert status == "ok" and score > 0.9

        # rerender wait predicate: old text rejected, new text accepted
        expected = {"headline": "Fresh headline about mining", "source": "AP",
                    "date": "7/11/26"}
        stale_view = {"title": "Old Article About Solana", "text": old_body}
        assert article_matches_expected(expected, stale_view)[0] is False
        fresh = "Back\nExport PDF\nFresh headline about mining\nMatched:MARA\nSource: AP\nnew body"
        assert article_matches_expected(
            expected, {"title": "Fresh headline about mining", "text": fresh})[0] is True
        assert body_signature(fresh) != body_signature(old_body)

        # LIVE REGRESSION: the generic scroll finder selected the mounted news
        # table, whose "title" was the column header. It is now rejected before
        # scrolling and can never be persisted as article text.
        table_blob = (
            "Headline Date Time Ticker Source\n"
            "Bitcoin row one 7/11/2026 10:30 BTC Reuters\n"
            "Bitcoin row two 7/11/2026 10:31 BTC Reuters\n"
            "Bitcoin row three 7/11/2026 10:32 BTC Reuters\n"
            "Bitcoin row four 7/11/2026 10:33 BTC Reuters\n"
            "Bitcoin row five 7/11/2026 10:34 BTC Reuters\n"
        ) * 500
        assert looks_like_news_table(table_blob)
        assert article_container_rejection_reason(table_blob, "Bitcoin row one")
        status, _, _ = classify_capture(
            {"headline": "Bitcoin row one", "source": "Reuters", "date": ""},
            "Headline Date Time Ticker Source", table_blob,
            prev_body_sig=None)
        assert status == "article_container_not_found"
        assert detail_text_for_status(status, table_blob) == ""

        # A low title match is never allowed to become status=ok, even when
        # the selected node has article-like metadata.
        mismatch = ("Unrelated Federal Reserve decision\nMatched:SPX\n"
                    "Source: Reuters\nPublication Date: 7/11/2026\nbody")
        status, score, _ = classify_capture(
            {"headline": "Bitcoin ETF inflows set a new record", "source": "",
             "date": ""},
            "Unrelated Federal Reserve decision", mismatch,
            prev_body_sig=None)
        assert score < 0.60 and status == "article_identity_mismatch"
        assert detail_text_for_status(status, mismatch) == ""

        # The real Back-anchored article shape remains valid.
        valid_article = ("Bitcoin ETF inflows set a new record\nMatched:BTC\n"
                         "Source: Reuters\nPublication Date: 7/11/2026\nbody")
        assert article_container_rejection_reason(
            valid_article, "Bitcoin ETF inflows set a new record") is None

        # identity statuses are retryable in the store (rank below partial)
        from news.store import STATUS_RANK
        assert STATUS_RANK["article_container_not_found"] < STATUS_RANK["container_not_found_partial_capture"]
        assert STATUS_RANK["article_identity_mismatch"] < STATUS_RANK["container_not_found_partial_capture"]
        assert STATUS_RANK["suspicious_same_body"] < STATUS_RANK["container_not_found_partial_capture"]
    check("news article identity (stale capture, fuzzy match, same-body)", _news_article_identity)

    # --- news: a rejected article container does not stop later headlines
    def _news_missing_article_container_continues():
        from news.godel_reader import harvest_articles

        rows = [
            {"headline": "Bitcoin first container is unavailable today",
             "date": "7/11/26", "time": "10:00", "ticker": "BTC",
             "source": "Reuters"},
            {"headline": "Bitcoin second article has a valid detail panel",
             "date": "7/11/26", "time": "10:01", "ticker": "BTC",
             "source": "Reuters"},
        ]

        class StubReader:
            def read_news_rows(self):
                return [dict(row) for row in rows]

            def expand_and_capture(self, row):
                if "first" in row["headline"]:
                    return {**row, "detail_status": "article_container_not_found",
                            "detail_text": ""}
                return {**row, "detail_status": "ok", "detail_text": "body"}

            def return_to_news_list(self):
                return True

        out = harvest_articles(StubReader(), set(), max_articles=5)
        assert [row["detail_status"] for row in out] == [
            "article_container_not_found", "ok"]
        assert out[0]["detail_text"] == "" and out[1]["detail_text"] == "body"
    check("news missing article container (discard blob, continue harvest)",
          _news_missing_article_container_continues)

    # --- Cloudflare challenge page: recognised, never mistaken for login
    def _cloudflare_detection():
        from news import godel_reader as gr

        class FakeDriver:
            def __init__(self, title="", src="", terminal=False, login=False):
                self.title, self.page_source = title, src
                self._terminal, self._login = terminal, login

            def find_elements(self, by, value):
                if value == "terminal-input":
                    return [object()] if self._terminal else []
                if "Login" in value:
                    return [object()] if self._login else []
                return []

        # the 2026-10-31 failure: interstitial, no Login button, no terminal
        cf = FakeDriver(title="Just a moment...",
                        src="<script src='/cdn-cgi/challenge-platform/h/b/orchestrate'></script>")
        assert gr.session_state(cf) == "cloudflare"
        assert gr.session_state(FakeDriver(src="<div class='cf-turnstile'></div>")) == "cloudflare"
        # normal pages carry Cloudflare's script too - that alone is not a challenge
        script_only = "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script>"
        assert gr.session_state(FakeDriver(src=script_only, login=True)) == "login"
        assert gr.session_state(FakeDriver(src=script_only)) == "unknown"
        assert gr.session_state(FakeDriver(terminal=True)) == "terminal"
        assert gr.session_state(FakeDriver(terminal=True, login=True)) == "login"

        # launch mode, headless: fails fast with the actionable message
        reader = gr.GodelNewsReader.__new__(gr.GodelNewsReader)
        reader.mode = "launch"
        reader.controller = type("C", (), {"headless": True, "driver": cf,
                                           "connect": lambda self: None})()
        saved = gr.settle_session_state
        gr.settle_session_state = lambda d, timeout_s=12.0: gr.session_state(d)
        try:
            reader._start_launch()
            raise AssertionError("expected cloudflare_challenge")
        except RuntimeError as e:
            assert str(e).startswith("cloudflare_challenge"), e
            assert "run_news.bat login" in str(e)
        finally:
            gr.settle_session_state = saved

        # attach mode: a Godel tab sitting on the challenge says so plainly
        class FakeSwitch:
            def window(self, handle):
                pass

        cf.window_handles = ["tab-1"]
        cf.switch_to = FakeSwitch()
        cf.current_url = "https://app.godelterminal.com/"
        reader.mode = "attach"
        reader.news_table_visible = lambda: False
        saved_cdp = gr.cdp_available
        gr.cdp_available = lambda *a, **k: True
        try:
            reader._start_attach()
            raise AssertionError("expected cloudflare_challenge in attach mode")
        except RuntimeError as e:
            assert e.args[0] == gr.CLOUDFLARE_ATTACH_MESSAGE, e
        finally:
            gr.cdp_available = saved_cdp
    check("Cloudflare challenge detection (launch + attach, clear message)", _cloudflare_detection)

    # --- attach driver matches the OPEN browser, not the installed Chrome
    def _attach_binary_match():
        import os
        import tempfile
        from news import config as cfg
        from news import godel_reader as gr
        with tempfile.TemporaryDirectory() as tmp:
            brave = os.path.join(tmp, "brave", "brave.exe")
            chrome = os.path.join(tmp, "chrome", "chrome.exe")
            for exe, ver in ((brave, "156.1.98.63"), (chrome, "157.0.8103.26")):
                os.makedirs(os.path.join(os.path.dirname(exe), ver))
                open(exe, "w").close()
            saved = (gr.find_brave_exe, gr.find_chrome_exe, cfg._local,
                     os.environ.pop("GODEL_CDP_BROWSER_BINARY", None))
            gr.find_brave_exe, gr.find_chrome_exe = lambda: brave, lambda: chrome
            cfg._local = None
            try:
                assert gr.installed_majors(brave) == {"156"}
                assert gr.attach_binary("156.0.8078.80") == brave   # the 2026-10-31 case
                assert gr.attach_binary("157.0.8103.26") == chrome
                assert gr.attach_binary("999.0.0.1") is None
                assert gr.attach_binary(None) is None
                os.environ["GODEL_CDP_BROWSER_BINARY"] = "X:/custom.exe"
                assert gr.attach_binary("156.0.1.2") == "X:/custom.exe"
            finally:
                gr.find_brave_exe, gr.find_chrome_exe, cfg._local, old = saved
                os.environ.pop("GODEL_CDP_BROWSER_BINARY", None)
                if old is not None:
                    os.environ["GODEL_CDP_BROWSER_BINARY"] = old
    check("attach picks the driver for the open browser (Brave 156 vs Chrome 157)",
          _attach_binary_match)

    # --- duplicate flagging (flags only, never removes a row)
    def _duplicate_flags():
        import pandas as pd
        from news.duplicates import flag_duplicates

        def row(h, date, time, ticker="CLSK", source="X"):
            return {"headline_hash": f"h{len(rows)}", "headline": h, "date": date,
                    "time": time, "ticker": ticker, "source": source,
                    "captured_at": "2026-10-31T00:00:00"}
        rows = []
        # exact: listed out of order - the EARLIEST news time is the original
        rows.append(row("CleanSpark Signs Data Centre Lease Worth $6.6 Billion", "7/18/26", "07:56:33"))
        rows.append(row("CleanSpark Signs Data Centre Lease Worth $6.6 Billion", "7/17/26", "05:38:16"))
        rows.append(row("CleanSpark signs data centre lease worth $6.6 billion [InvestingChannel]",
                        "7/17/26", "13:08:12", ticker="GLXY"))           # tag + case + ticker ignored
        rows.append(row("CleanSpark Signs Data Centre Lease Worth $6.6 Billion", "7/22/26", "09:00:00"))  # 5 days on
        # near: same story reworded, same ticker (real pair from 2026-07)
        rows.append(row("Polymarket in high-stakes race to win back trust as it recommits "
                        "to U.S. market", "7/20/26", "10:00:00", ticker="COIN"))
        rows.append(row("Polymarket in high-stakes race to win back trust as it recommits "
                        "to US market", "7/20/26", "12:00:00", ticker="COIN"))
        # guards: other ticker / other numbers / other month -> not duplicates
        rows.append(row("Polymarket in high-stakes race to win back trust as it recommits "
                        "to US market now", "7/20/26", "13:00:00", ticker="HOOD"))
        rows.append(row("Jefferies Adjusts Tapestry Price Target to $150 From $142", "7/20/26", "10:00:00", ticker="TPR"))
        rows.append(row("Jefferies Adjusts Tapestry Price Target to $155 From $150", "7/20/26", "11:00:00", ticker="TPR"))
        rows.append(row("UNITED STATES INSURANCE 16 APR 26_EN", "7/20/26", "10:00:00", ticker="TRV"))
        rows.append(row("UNITED STATES INSURANCE 16 JUL 26_EN", "7/20/26", "10:05:00", ticker="TRV"))
        # template: same opening on many headlines, different company each time
        for i, co in enumerate(["S-Pool,Inc", "TRaaS On Product", "Ishikawa Seisakusho",
                                "COLOWIDE CO.,LTD", "Jibannet Holdings", "Prored Partners",
                                "KOSHIDAKA HOLDINGS", "SKYLARK HOLDINGS", "Denka Company", "ROHM CO"]):
            rows.append(row("GOLDMAN SACHS INTERNATIONAL Announces Outstanding Short Selling "
                            f"Positions of {co}", "7/30/26", f"10:{i:02d}:00", ticker="GS"))
        out = flag_duplicates(pd.DataFrame(rows)).set_index("headline_hash")

        assert not out.loc["h1", "is_duplicate"]                      # earliest = original
        for h in ("h0", "h2"):
            assert out.loc[h, "is_duplicate"] and out.loc[h, "duplicate_type"] == "exact"
            assert out.loc[h, "duplicate_of"] == "h1" and out.loc[h, "duplicate_similarity"] == 1.0
        assert out.loc["h1", "duplicate_group_size"] == 3 and out.loc["h1", "duplicate_of"] == ""
        assert not out.loc["h3", "is_duplicate"]                      # outside the 72h window
        assert out.loc["h5", "duplicate_type"] == "near" and out.loc["h5", "duplicate_of"] == "h4"
        assert 0.7 <= out.loc["h5", "duplicate_similarity"] < 1.0
        # near-identical (>= 0.85) matches across ticker tags
        assert out.loc["h6", "duplicate_type"] == "near" and out.loc["h6", "duplicate_of"] == "h4"
        for h in ("h8", "h10"):                                       # numbers / month guards
            assert not out.loc[h, "is_duplicate"], (h, out.loc[h].to_dict())
        gs = out[out["ticker"] == "GS"]
        assert not gs["is_duplicate"].any(), "template filings flagged as duplicates"
        assert list(flag_duplicates(pd.DataFrame(rows))["headline_hash"]) == [r["headline_hash"] for r in rows]
        assert len(out) == len(rows)                                  # nothing dropped
    check("duplicate flags: exact + near, original = earliest, guards, templates",
          _duplicate_flags)

    # --- cross-outlet copies: number formats, ticker tags, article text
    def _cross_outlet_duplicates():
        import pandas as pd
        from news.duplicates import flag_duplicates
        story = ("Hackers drained the exchange's hot wallets overnight, moving ether and "
                 "stablecoins through a cross-chain bridge. The exchange paused deposits, "
                 "said customer balances are covered by its reserve fund, and hired "
                 "Chainalysis and Mandiant to trace the stolen assets across Tron and "
                 "Ethereum while regulators in Dubai and Singapore asked for reports. ")
        other = ("The central bank kept its benchmark rate unchanged, citing sticky services "
                 "inflation and a resilient labour market, while two members voted for a "
                 "cut and the bank lowered its growth forecast for next year citing "
                 "weaker exports, softer consumer spending and tighter credit conditions. ")
        rows = [
            {"headline_hash": "a", "headline": "Binance hit by $6.3B hack", "ticker": "COIN",
             "date": "10/30/26", "time": "09:00:00"},
            # heavy rewording, same ticker, same facts in the article -> 'article'
            {"headline_hash": "b", "headline": "Hackers drain $6.3 billion from top exchange",
             "ticker": "COIN", "date": "10/30/26", "time": "11:00:00"},
            # rounded number + reworded + article -> still the same story
            {"headline_hash": "c", "headline": "Exchange loses $6 billion to attackers",
             "ticker": "COIN", "date": "10/30/26", "time": "12:00:00"},
            # same article text but a different ticker -> not merged
            {"headline_hash": "d", "headline": "Crypto exchange security questioned",
             "ticker": "MSTR", "date": "10/30/26", "time": "13:00:00"},
            # same ticker, unrelated article -> not merged
            {"headline_hash": "e", "headline": "Exchange shares steady after rate decision",
             "ticker": "COIN", "date": "10/30/26", "time": "14:00:00"},
        ]
        bodies = {"a": story, "b": story + "Analysts expect withdrawals to resume.",
                  "c": "Update: " + story, "d": story, "e": other}
        # word rarity is learned from the whole store, so give it a realistic
        # background of unrelated articles (weeks earlier, outside the window)
        vocab = ("mining revenue guidance tariff lawsuit merger dividend chip cloud "
                 "rally oil gold yen bond pension index token payroll housing retail "
                 "airline vaccine satellite energy solar nickel copper wheat lumber").split()
        for k in range(25):
            words = " ".join(vocab[(k + i) % len(vocab)] for i in range(0, 60, 3))
            rows.append({"headline_hash": f"bg{k}", "headline": f"Background story {k}",
                         "ticker": "BG", "date": "9/1/26", "time": f"{k % 24:02d}:00:00"})
            bodies[f"bg{k}"] = f"Report {k}: " + words * 3
        out = flag_duplicates(pd.DataFrame(rows), bodies).set_index("headline_hash")
        assert out.loc["b", "duplicate_type"] == "article" and out.loc["b", "duplicate_of"] == "a"
        assert out.loc["b", "duplicate_similarity"] >= 0.62
        assert out.loc["c", "duplicate_of"] == "a", out.loc["c"].to_dict()
        assert not out.loc["d", "is_duplicate"] and not out.loc["e", "is_duplicate"]
        # without article text the rewordings are not caught (headline rules only)
        plain = flag_duplicates(pd.DataFrame(rows)).set_index("headline_hash")
        assert not plain.loc[["a", "b", "c", "d", "e"], "is_duplicate"].any()
    check("cross-outlet duplicates: article text, rounded numbers, ticker rule",
          _cross_outlet_duplicates)

    # --- one CSV per Monday-Sunday week; unchanged weeks untouched; locks survive
    def _weekly_csvs():
        import tempfile
        import pandas as pd
        from pathlib import Path as _P
        from news import store as ns
        df = ns.finalize_interpreted(pd.DataFrame([
            {"headline_hash": "a", "headline": "Sunday story", "date": "11/1/26", "time": "23:00:00"},
            {"headline_hash": "b", "headline": "Monday story", "date": "10/26/26", "time": "09:00:00"},
            {"headline_hash": "c", "headline": "Next Monday", "date": "11/2/2026", "time": "00:00:01"},
        ]))
        with tempfile.TemporaryDirectory() as tmp:
            weekly = _P(tmp) / "weekly"
            res = ns.write_weekly_csvs(df, weekly)
            names = sorted(p.name for p in weekly.iterdir())
            assert names == ["2026-W44_2026-10-26_to_2026-11-01.csv",
                             "2026-W45_2026-11-02_to_2026-11-08.csv"], names
            assert res["weekly_files_written"] == 2
            w44 = pd.read_csv(weekly / names[0], dtype=str)
            assert list(w44["headline_hash"]) == ["b", "a"]           # news-time order
            assert "is_duplicate" in w44.columns
            assert ns.write_weekly_csvs(df, weekly)["weekly_files_written"] == 0   # unchanged
            raw = (weekly / names[0]).read_bytes()
            assert raw.startswith(b"\xef\xbb\xbf") and b"\r\r\n" not in raw   # Excel-friendly
            assert len(pd.read_csv(weekly / names[0])) == 2                    # no blank rows
            real_write = _P.write_bytes
            def locked(self, *a, **k):
                if self.name.startswith("2026-W45"):
                    raise PermissionError("open in Excel")
                return real_write(self, *a, **k)
            _P.write_bytes = locked
            try:
                df2 = pd.concat([df, ns.finalize_interpreted(pd.DataFrame([
                    {"headline_hash": "d", "headline": "Tuesday", "date": "11/3/26",
                     "time": "08:00:00"}]))])
                res = ns.write_weekly_csvs(df2, weekly)
            finally:
                _P.write_bytes = real_write
            assert res["weekly_files_locked"] == [names[1]], res
    check("weekly CSVs: Mon-Sun files, news-time order, unchanged untouched, "
          "locked file reported", _weekly_csvs)

    # --- append_news writes flags + weekly files beside ITS interpreted CSV
    def _append_writes_flags_and_weeks():
        import tempfile
        import pandas as pd
        from pathlib import Path as _P
        from news import store as ns
        with tempfile.TemporaryDirectory() as tmp:
            scratch = _P(tmp)
            old = (ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV, ns.ROUTING_QUARANTINE_CSV)
            ns.NEWS_DIR, ns.RAW_CSV = scratch, scratch / "raw.csv"
            ns.INTERPRETED_CSV = scratch / "interp.csv"
            ns.ROUTING_QUARANTINE_CSV = scratch / "q.csv"
            try:
                res = ns.append_news([
                    {"headline": "Bitcoin ETF inflows hit record", "date": "10/27/26",
                     "time": "09:00:00", "ticker": "BTC", "source": "Reuters"},
                    {"headline": "Bitcoin ETF inflows hit record", "date": "10/27/26",
                     "time": "15:00:00", "ticker": "BTC", "source": "Reuters"}])
            finally:
                ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV, ns.ROUTING_QUARANTINE_CSV = old
            assert res["new"] == 2 and res["duplicates_flagged"] == 1, res
            it = pd.read_csv(scratch / "interp.csv", dtype=str)
            assert list(it.columns[8:13]) == ["is_duplicate", "duplicate_type", "duplicate_of",
                                             "duplicate_similarity", "duplicate_group_size"]
            assert sorted(it["is_duplicate"]) == ["False", "True"]
            assert [p.name for p in (scratch / "weekly").iterdir()] == \
                ["2026-W44_2026-10-26_to_2026-11-01.csv"]
    check("append_news: duplicate flags + weekly CSV written beside the store",
          _append_writes_flags_and_weeks)

    # --- interpreted CSV open in Excel: nothing saved, so nothing is orphaned
    def _locked_interpreted_saves_nothing():
        import tempfile
        import pandas as pd
        from pathlib import Path as _P
        from news import store as ns
        with tempfile.TemporaryDirectory() as tmp:
            scratch = _P(tmp)
            old = (ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV, ns.ROUTING_QUARANTINE_CSV)
            ns.NEWS_DIR, ns.RAW_CSV = scratch, scratch / "raw.csv"
            ns.INTERPRETED_CSV = scratch / "interp.csv"
            ns.ROUTING_QUARANTINE_CSV = scratch / "q.csv"
            real_to_csv = pd.DataFrame.to_csv
            def to_csv(self, path=None, *a, **k):
                if path is not None and _P(path).name == "interp.csv":
                    raise PermissionError("open in Excel")
                return real_to_csv(self, path, *a, **k)
            pd.DataFrame.to_csv = to_csv
            try:
                ns.append_news([{"headline": "Bitcoin ETF inflows hit record",
                                 "date": "10/27/26", "time": "09:00:00", "ticker": "BTC"}])
                raise AssertionError("expected a clear 'open in Excel' error")
            except RuntimeError as e:
                assert "open in Excel" in str(e), e
            finally:
                pd.DataFrame.to_csv = real_to_csv
                ns.NEWS_DIR, ns.RAW_CSV, ns.INTERPRETED_CSV, ns.ROUTING_QUARANTINE_CSV = old
            # raw untouched -> the headline is simply captured again next run
            assert not (scratch / "raw.csv").exists()
    check("locked interpreted CSV: clear error, raw not written (no orphan rows)",
          _locked_interpreted_saves_nothing)

    # --- outcomes: price move AFTER the headline, from the first bar after it
    def _forward_outcomes():
        import pandas as pd
        from news import outcomes as O
        idx = pd.date_range("2026-07-10 00:00", periods=24 * 9, freq="h", tz="UTC")
        prices = pd.DataFrame({"open": [100.0 + i for i in range(len(idx))],
                               "close": [100.5 + i for i in range(len(idx))]}, index=idx)
        t = O.news_time_utc({"date": "7/10/26", "time": "09:30:00"})     # US Eastern
        assert str(t) == "2026-07-10 13:30:00+00:00", t
        now = pd.Timestamp("2026-07-30", tz="UTC")
        out = O.forward_outcome(t, prices, now)
        # entry = 14:00 bar (the 13:00 bar already contains pre-headline moves)
        assert out["outcome_entry_utc"].startswith("2026-07-10T14:00"), out
        entry = 100.0 + 14
        assert out["fwd_return_1d_pct"] == round(((100.5 + 38) / entry - 1) * 100, 3)
        assert out["fwd_return_7d_pct"] == round(((100.5 + 14 + 168) / entry - 1) * 100, 3)
        assert out["outcome_status"] == "ok"
        late = O.forward_outcome(pd.Timestamp("2026-07-17 12:00", tz="UTC"), prices, now)
        assert late["outcome_status"] == "pending_7d" and late["fwd_return_7d_pct"] is None
        assert O.signal_band("-0.75") == "strong negative" and O.signal_band(0) == "neutral"
        assert O.signal_band(0.25) == "mild positive" and O.signal_band("") == ""
    check("outcomes: entry is the first bar after the headline; 1d/7d returns; pending",
          _forward_outcomes)

    # --- calibration: one vote per day, duplicates out, numbers only when enough days
    def _calibration():
        import tempfile
        import pandas as pd
        from pathlib import Path as _P
        from news import outcomes as O
        idx = pd.date_range("2026-07-01", periods=24 * 60, freq="h", tz="UTC")
        rise = pd.DataFrame({"open": [100.0 + i * 0.1 for i in range(len(idx))],
                             "close": [100.0 + i * 0.1 for i in range(len(idx))]}, index=idx)
        calls = []
        def loader(sym, start, end):
            calls.append(sym)
            return rise
        rows = []
        for d in range(1, 6):                       # 5 days, 3 bullish headlines each
            for k in range(3):
                rows.append({"headline_hash": f"b{d}{k}", "date": f"7/{d}/26",
                             "time": f"1{k}:00:00", "news_sentiment_score": "0.5",
                             "btc_relevance": "90", "is_duplicate": "False",
                             "association": "BTC", "headline": f"h{d}{k}"})
        rows.append({**rows[0], "headline_hash": "dup", "is_duplicate": "True"})
        rows.append({**rows[0], "headline_hash": "irrelevant", "btc_relevance": "0",
                     "date": "7/20/26"})
        df = pd.DataFrame(rows)
        saved = O.CALIBRATION_MIN_DAYS
        with tempfile.TemporaryDirectory() as tmp:
            try:
                now = pd.Timestamp("2026-08-30", tz="UTC")
                done = O.attach_outcomes(df, "btc", loader=loader, now=now, price_dir=_P(tmp))
                assert calls == ["BTC-USD"] and set(done["outcome_status"]) == {"ok"}
                again = O.attach_outcomes(done, "btc", loader=loader, now=now, price_dir=_P(tmp))
                assert calls == ["BTC-USD"]                          # measured rows: no refetch
                O.CALIBRATION_MIN_DAYS = 6
                table = O.calibration_table(again, "btc")
                strong = table[(table.band == "strong positive") & (table.horizon == "1d")].iloc[0]
                assert strong.independent_days == 5 and strong.headlines == 15, strong
                assert not strong.calibrated                         # 5 < 6 days
                cal = O.apply_calibration(again, table)
                assert cal.loc[0, "prob_up_1d"] == "" and "1d 5/6 days" in cal.loc[0, "calibration_note"]
                O.CALIBRATION_MIN_DAYS = 5
                cal = O.apply_calibration(again, O.calibration_table(again, "btc"))
                assert cal.loc[0, "prob_up_1d"] == 100.0             # rising prices every day
                assert float(cal.loc[0, "expected_move_1d_pct"]) > 0
                assert "1d calibrated on 5 days" in cal.loc[0, "calibration_note"]
            finally:
                O.CALIBRATION_MIN_DAYS = saved
    check("calibration: one vote per day, duplicates out, blank until enough days",
          _calibration)

    # --- research only: nothing in this program can trade
    def _news_no_trade_impact():
        from news.interpreter import TRADE_IMPACT_ENABLED
        assert TRADE_IMPACT_ENABLED is False
    check("news has NO trade impact flag", _news_no_trade_impact)

    print()
    if failures:
        print(f"{len(failures)} FAILURES")
        return 1
    print("ALL NEWS SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
