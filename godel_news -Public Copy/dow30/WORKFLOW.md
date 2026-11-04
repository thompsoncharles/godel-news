# Godel Terminal — Dow 30 News Workflow

> **Moved (Oct 31, 2026).** This workflow now lives in the standalone `godel_news` program. Path changes from the text below:
> `python dow30_capture.py …` → `python -m news dow30 …` · `python dow30_news.py …` → `python -m news dow30-classify …` ·
> `V4.1algobot-master\quant_data_dow30\` → `godel_news\data_dow30\` · `V4.1algobot-master\quant_data\news\` → `godel_news\data\news\`.
> The trading repo no longer contains any news code.

**Separate from the Value Line analysis.** Nothing in this document feeds the Value Line ranking, and no Value Line figure is used here. Keep the two data sets distinct: Value Line reports are dated April–July 2026 research; this is live news capture.

**Investigated:** July 30, 2026
**Code inspected:** `%USERPROFILE%\OneDrive\Documents\Desktop\V4.1algobot-master\quant\news\` and `quant\pipeline.py`

---

## 1. Answer to the "bitcoin_news" question

**There is no function named `bitcoin_news`.** The news entry point in your codebase is the pipeline step **`godel-news`**, implemented in `quant/pipeline.py:476` (`cmd_godel_news`) over `quant/news/godel_reader.py`.

**It is not limited to Bitcoin.** The "Bitcoin" character comes from *which Godel NEWS-panel ticker group is selected*, not from the tool. The reader documents the distinction explicitly at `quant/news/godel_reader.py:216`:

> The NEWS panel carries its OWN ticker-group dropdown (AB4.1 = BTC/crypto universe, SP400 = SPY + 399 largest SPY holdings).

```python
ASSET_GROUPS = {"btc": "AB4.1", "spy": "SP400"}   # godel_reader.py:223
```

So the correct general-market news function is **the same function**, invoked with a non-crypto group — `--asset spy` for **SP400**, or the **DOW30** watchlist described below, which is now the better option.

**Why SP400 covers the Dow.** SP400 is "SPY + the 399 largest SPY holdings." Every Dow 30 constituent is a large-cap S&P 500 member, so **all 30 Dow tickers fall inside the SP400 group**. You do not need a new data source — you need the right group plus a Dow-30 filter.

**Empirical confirmation.** Your existing store at `V4.1algobot-master\quant_data\news\godel_news_raw.csv` holds 344 captured rows (dated 7/9/26–7/10/26). Even though every row predates group-switching (the `selected_group` column is blank on all 344), **17 rows already carry Dow 30 tickers** across AAPL, AMGN, GS, HON, JPM, MRK, MSFT and NVDA. The panel demonstrably surfaces Dow equity news.

### Two real constraints

**(a) `resolve_group()` rejects unknown assets.** It raises rather than guessing (`godel_reader.py:241`), and `switch_news_group` **fails closed** — if the exact group label cannot be verified it returns `status="unavailable"` instead of scraping whatever happens to be showing. That is good design, but it means `--asset dow` only works once the group is registered on both sides:
   1. A news group named `DOW30` exists in the Godel Terminal UI itself, and
   2. `"dow": "DOW30"` is present in `ASSET_GROUPS`.

**As of July 30, 2026 the `DOW30` watchlist exists**, so step 1 is done. `dow30_capture.py` performs step 2 **at runtime**, leaving the trading repo unmodified:

```python
godel_reader.ASSET_GROUPS["dow"] = "DOW30"   # -> {'btc': 'AB4.1', 'spy': 'SP400', 'dow': 'DOW30'}
```

This is now the preferred path — the panel returns only Dow names, so the SP400 noise problem disappears. `--asset spy` remains a fallback.

Label matching is strict. Verified behavior:

| Displayed label | Matches `DOW30`? |
|---|---|
| `DOW30` | yes |
| `DOW30 (30)` | yes — trailing decoration allowed |
| `DOW300` | **no** — fails closed, by design |

If the dropdown reads anything else, run `--list-groups` and pass the exact string via `--group`.

**(b) Godel's own ticker tags are unreliable.** Verified against your live capture:

| Godel ticker tag | Actual headline |
|---|---|
| `MSFT` | "Why Is AMD Stock Surging Before August Earnings?" |
| `GS` | "University of Portsmouth celebrates inspirational figures with Honorary Doctorates" |

Filtering on the ticker column alone produces false positives. The script therefore records a **`match_basis`** for every row (`both` / `headline_name` / `ticker_tag`) and offers `--strict` to drop tag-only matches.

### What the existing interpreter does *not* do

`quant/news/interpreter.py` produces `sentiment`, `btc_relevance`, `impact_direction`, `impact_horizon`, `confidence`, `categories`. Two gaps for Dow 30 work:

- **Relevance is BTC-specific.** `btc_relevance` scores against `BTC_DIRECT`/`CRYPTO_GENERAL` lexicons. A Boeing certification headline scores 0.
- **Association is BTC/SPY only.** `quant/news/asset_routing.py` classifies to `BTC`, `SPY`, `BOTH`, or `AMBIGUOUS` — there is no per-Dow-ticker routing.
- **No 1–5 impact score.** The existing coefficients are continuous floats in [-1, 1] designed for BTC research joins.

`dow30_news.py` supplies the equity-side classification the existing interpreter lacks. It does **not** modify the trading repo — `TRADE_IMPACT_ENABLED = False` and the paper-observation safety scans are untouched.

---

## 2. Correct Dow 30 ticker list

| # | Ticker | Company | # | Ticker | Company |
|---|--------|---------|---|--------|---------|
| 1 | AAPL | Apple | 16 | JPM | JPMorgan Chase |
| 2 | AMGN | Amgen | 17 | KO | Coca-Cola |
| 3 | AMZN | Amazon | 18 | MCD | McDonald's |
| 4 | AXP | American Express | 19 | MMM | 3M |
| 5 | BA | Boeing | 20 | MRK | Merck |
| 6 | CAT | Caterpillar | 21 | MSFT | Microsoft |
| 7 | CRM | Salesforce | 22 | NKE | Nike |
| 8 | CSCO | Cisco | 23 | NVDA | NVIDIA |
| 9 | CVX | Chevron | 24 | PG | Procter & Gamble |
| 10 | DIS | Disney | 25 | SHW | Sherwin-Williams |
| 11 | GS | Goldman Sachs | 26 | TRV | Travelers |
| 12 | HD | Home Depot | 27 | UNH | UnitedHealth |
| 13 | HON | Honeywell | 28 | V | Visa |
| 14 | IBM | IBM | 29 | **VZ** | **Verizon** |
| 15 | JNJ | Johnson & Johnson | 30 | WMT | Walmart |

**VZ is included here.** It is a current Dow constituent; it is only missing from the *Value Line* folder, not from the index.

---

## 3. Exact commands

### Step 0 — one-time session setup

The reader supports two browser modes. **Attach mode is the better path for you** — it uses your already-logged-in Brave session and never handles credentials (`godel_reader.py:85`).

Start Brave with remote debugging enabled. **In PowerShell you need the call operator `&`** — a leading quoted string is parsed as a value, not a command:

```powershell
& "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222
```

Chromium only accepts that flag **at process startup**, and relaunching while Brave is still alive silently hands off to the existing instance and drops it. Brave must be fully quit first — background processes included:

```powershell
Get-Process brave -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 2; & "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222
```

Then log into `app.godelterminal.com` in that window and leave the news panel visible. Verify the port:

```powershell
(Invoke-WebRequest -Uri http://127.0.0.1:9222/json/version -UseBasicParsing).Content
```

Note that a force-quit drops the existing Godel session, so you will need to log in again. Attach mode never enters credentials — `_start_attach` raises "attached Godel tab is not logged in" rather than authenticating. Launch mode is the opposite: it calls `controller.login()` with the values in `parameters.py`, which is a further reason to prefer attach.

The fallback is launch mode with a persistent profile, seeded once:

```bash
python -m quant.pipeline godel-news --login
```

### Step 1 — capture from the DOW30 watchlist (preferred)

Run from `%USERPROFILE%\OneDrive\Documents\Desktop\valueline`:

```powershell
python dow30_capture.py --attach --headlines-only
```

This registers the `DOW30` group at runtime, verifies the NEWS panel dropdown reads DOW30 (fail-closed), reads the news table, writes to an **isolated store** at `V4.1algobot-master\quant_data_dow30\news\`, and runs the Dow 30 classification pass automatically. It **clicks nothing** — no layout tabs, no article rows.

**Why `--headlines-only` is the working mode.** Godel's article view replaces the list inside the panel, and returning depends on an in-app Back control the reader can no longer locate. Every expanding run therefore stops after one article (`return_failed` / `blocked_by_overlay`). Reading rows needs no clicks at all, and got 1,600 rows versus 1 — see §6. Rows are stored as `detail_status=headline_only` (STATUS_RANK 4), so if the Back control is fixed later, a full-text run upgrades them in place rather than duplicating.

**Layout tabs are never touched.** `--allow-layout-switch` is off by default, and the guard patches `_click_layout_tab` itself rather than just `open_news_layout` — because the article-return ladder (`godel_article_back → escape → layout_tab → browser_back`) also clicks it. On the first live run that rung fired and yanked the panel off DOW30 onto AB4.1 mid-harvest, which is what broke that run.

Discover the exact dropdown label if the switch fails:

```powershell
python dow30_capture.py --attach --list-groups
```

Useful variants:

```powershell
python dow30_capture.py --attach --headlines-only --min-impact 3
```

```powershell
python dow30_capture.py --attach --headlines-only --no-classify --shared-store
```

Re-classify already-captured rows without touching the browser:

```powershell
python dow30_news.py --strict --raw "%USERPROFILE%\OneDrive\Documents\Desktop\V4.1algobot-master\quant_data_dow30\news\godel_news_raw.csv"
```

**Why an isolated store.** `quant_data_dow30/` keeps Dow rows out of `quant_data/news/godel_news_raw.csv`. That matters because the BTC Excel report's `filter_by_association` (`excel_report.py:43`) shows rows whose association is BTC, BOTH, **or blank** — and Dow rows classified by `asset_routing` would often land as blank or AMBIGUOUS, surfacing inside your BTC paper-observation report. Isolation removes the question entirely. `--shared-store` opts back in.

### Step 1b — SP400 fallback

If the DOW30 group is unavailable, run from `%USERPROFILE%\OneDrive\Documents\Desktop\V4.1algobot-master`:

```bash
python -m quant.pipeline godel-news --asset spy
```

This switches the NEWS panel to the **SP400** group (verified, fail-closed), harvests new headlines with full article text, deduplicates by headline hash, and writes:

- `quant_data/news/godel_news_raw.csv`
- `quant_data/news/godel_news_interpreted.csv`
- `quant_data/godel_news_result.json`

To capture both crypto and equity groups sequentially:

```bash
python -m quant.pipeline godel-news --asset all
```

### Step 2 — filter and classify to Dow 30

Run from `%USERPROFILE%\OneDrive\Documents\Desktop\valueline`:

```bash
python dow30_news.py --strict
```

Options:

```bash
python dow30_news.py --min-impact 3 --out dow30_material.csv
```

```bash
python dow30_news.py --raw "C:\path\to\godel_news_raw.csv"
```

### Full sequence

Capture and classify in one command (PowerShell uses `;` not `&&`):

```powershell
cd "%USERPROFILE%\OneDrive\Documents\Desktop\valueline"; python dow30_capture.py --attach --headlines-only --min-impact 2
```

Or via the SP400 fallback, in two steps:

```bash
cd "%USERPROFILE%\OneDrive\Documents\Desktop\V4.1algobot-master" && python -m quant.pipeline godel-news --asset spy
```

```bash
cd "%USERPROFILE%\OneDrive\Documents\Desktop\valueline" && python dow30_news.py --strict --min-impact 2
```

### Programmatic form

```python
from quant.news.godel_reader import run_news_scrape
result = run_news_scrape(asset="spy")     # -> {"status": "ok", "rows_new": N, ...}

import sys; sys.path.insert(0, r"%USERPROFILE%\OneDrive\Documents\Desktop\valueline")
from dow30_news import classify, match_dow
sym, basis = match_dow(ticker="", headline="Boeing wins $4B order from Emirates")
print(sym, basis, classify("Boeing wins $4B order from Emirates"))
```

### Blocker: `constants/parameters.py` does not parse

`constants\parameters.py:17` reads `IBKR_PORT =` with **no value**, which is a `SyntaxError`. Because `quant/config.py:10` imports that module, **every pipeline entry point is currently broken** — `godel-news`, `btc-daily-routine`, all of it, not just this workflow. `dow30_capture.py` detects this and reports it rather than emitting a raw traceback.

Set the port before running anything:

```python
IBKR_PORT = 7497   # 7497 = TWS paper, 7496 = TWS live
```

`PAPER_TRADING = True` on line 19, and `ibkr.connection` aborts on a port/flag mismatch, so **7497 is the value consistent with the rest of the file** — but this decides paper versus live money, so set it deliberately yourself rather than taking my word for it.

Separately, that same file holds your Godel email and password in plaintext. It is gitignored, but attach mode (`GODEL_BROWSER_MODE=attach`) never reads credentials at all — so once you are on attach mode you can blank both values.

### If you want a native `--asset dow` in the repo itself

Create a `DOW30` news group in the Godel UI, then:

```python
# quant/news/godel_reader.py:223
ASSET_GROUPS = {"btc": "AB4.1", "spy": "SP400", "dow": "DOW30"}
```

and add `"dow"` to the `--asset` choices in `quant/pipeline.py:874`. Note that `quant/news/asset_routing.py` also carries `GROUP_TO_ASSET = {"AB4.1": "BTC", "SP400": "SPY"}` and would need a matching entry. **This edits a repo that is under paper-trade observation** — worth a separate, deliberate change rather than folding it into this task.

---

## 4. Classification schema

Output columns in `dow30_news_classified.csv`:

| Column | Values | Meaning |
|--------|--------|---------|
| `ticker` | Dow 30 symbol | Resolved constituent |
| `match_basis` | `both` / `headline_name` / `ticker_tag` | How the row was attributed. `ticker_tag` is least reliable — see §1(b) |
| `date`, `time` | as captured | Godel's timestamp |
| `headline` | text | Article headline |
| `source`, `source_tier` | text, 0–3 | 3 = Reuters/Bloomberg/WSJ/FT/AP; 2 = CNBC/Barron's/MarketWatch; 1 = other; 0 = social |
| `sentiment` | `bullish` / `bearish` / `neutral` | Headline-driven; body only breaks ties |
| `impact` | 1–5 | See scale below |
| `event_types` | pipe-delimited | `earnings`, `guidance`, `analyst_action`, `litigation`, `regulation`, `management`, `m_and_a`, `product`, `capital_return`, `general` |
| `horizon` | `next_day` / `next_week` / `next_quarter` / `unclear` | When the item likely matters |
| `signal_terms` | pipe-delimited | Which keywords drove the sentiment call — makes every classification auditable |
| `detail_status` | `ok`, `headline_only`, … | Capture quality from the reader's own status ranking |

### Impact scale

| Score | Meaning |
|-------|---------|
| **5** | Multiple strong directional signals plus a high-severity event (halt, recall, fraud, criminal matter, CEO departure, major M&A) |
| **4** | Clear directional signal on earnings/guidance, or a significant legal/regulatory/M&A development |
| **3** | A recognized event type with moderate directional language |
| **2** | Weak directional signal, or an event type with neutral framing |
| **1** | Mention-only; no directional or event content |

### Horizon mapping

- `next_day` — earnings, guidance, analyst actions
- `next_week` — capital-return announcements, management changes
- `next_quarter` — products, M&A, litigation, regulation
- `unclear` — no recognized event type, so no horizon claim is made

---

## 5. Verified bootstrap (offline)

The runtime wiring in `dow30_capture.py` is tested; only the browser step needs a live session:

```
DATA_DIR      : quant_data_dow30
store RAW_CSV : ...\V4.1algobot-master\quant_data_dow30\news\godel_news_raw.csv
ASSET_GROUPS  : {'btc': 'AB4.1', 'spy': 'SP400', 'dow': 'DOW30'}
resolve dow   : DOW30
exact / decorated / near-miss : True True False
isolated from BTC store : True
```

## 6. Verified live run — July 30, 2026

Executed against the live `DOW30` watchlist over attach mode:

```
browser mode: attach  (layout tab will NOT be touched)
group_switch: requested=DOW30 displayed=DOW30 reason=already_selected
              row_ticker_consistency = 1.0 over 1594 tickers
mode: headlines_only
rows_visible: 1600   rows_new: 1598   upgraded: 2

scanned 1600 captured rows -> 564 Dow 30 items   (--strict)
match basis: {'both': 554, 'headline_name': 10}
sentiment:   {'bullish': 59, 'bearish': 11, 'neutral': 494}
horizon:     {'next_day': 58, 'next_week': 14, 'next_quarter': 107, 'unclear': 385}
tickers covered (27/30)
no news today: TRV, V, VZ
```

**All 1,600 rows carry a single date: 7/30/26.** This is one trading day's feed, not a rolling window, so per-ticker counts reflect who happened to have news today rather than any sustained trend.

Net sentiment by ticker (bullish minus bearish), top of the distribution:

| Ticker | Items | Bull | Bear | Neutral | Net |
|---|---|---|---|---|---|
| MSFT | 110 | 31 | 2 | 77 | **+29** |
| AAPL | 69 | 10 | 3 | 56 | +7 |
| IBM | 41 | 5 | 0 | 36 | +5 |
| AMZN | 30 | 5 | 0 | 25 | +5 |
| GS | 113 | 1 | 3 | 109 | −2 |
| JPM | 97 | 1 | 2 | 94 | −1 |

MSFT dominates because Microsoft reported that morning — Azure crossing $100 billion, revenue and earnings beats, shares up ~10%. GS and JPM carry high item counts but almost entirely neutral: both names appear constantly in market-wrap headlines that say nothing specific about the company.

### Two classifier bugs found and fixed during this run

1. **Substring matching on the sentiment lexicon.** "Stifel Nicolaus **Issues** Positive Forecast for Microsoft" scored *bearish* — `sues` matched inside `Issues`. Same defect class as `Nike` inside `Nikesh Arora`. Both lexicon and event keywords are now word-bounded regexes; a real "Boeing sues supplier" still scores bearish.
2. **Merck name collision.** "Merck KGaA, Darmstadt, Germany" is the German Merck (EMD Serono in the US), a different listed company from Merck & Co. `NEGATIVE_CONTEXT` now excludes it while keeping Merck & Co.

## 7. Verified classifier run against the older BTC-group store


```
scanned 344 captured rows -> 15 Dow 30 items   (--strict)

match basis: {'both': 6, 'headline_name': 9}
sentiment:   {'bearish': 3, 'neutral': 9, 'bullish': 3}
horizon:     {'next_quarter': 6, 'unclear': 9}
tickers covered (7/30): AAPL AMGN JPM MRK NVDA UNH V
no news captured (23): AMZN AXP BA CAT CRM CSCO CVX DIS GS HD HON IBM
                       JNJ KO MCD MMM MSFT NKE PG SHW TRV VZ WMT
```

Without `--strict` the same store yields 26 items, of which 11 are `ticker_tag`-only — the noisy ones.

**Read this as a plumbing test, not as market intelligence.** These 344 rows were captured on 9–10 July 2026 from the **AB4.1 crypto group**, before group switching existed. Dow names appear only incidentally, which is why 23 of 30 tickers have no coverage and why the surviving items are patent filings and passing mentions rather than market-moving events. **A genuine Dow 30 news sweep requires running Step 1 with `--asset spy` first.** The classifier is validated; the input is not yet the right input.

---

## 8. Known limitations

1. **`constants/parameters.py` must parse before anything runs** — see the blocker in §3. This is the current hard stop.
2. **Requires a live Godel session.** The reader fails closed with `status="unavailable"` if the group cannot be verified. No session means no new data.
3. **Godel's ticker tagging is loose** — use `--strict` for anything you intend to act on. Less of an issue on the DOW30 group than on SP400, but the tag is still not proof.
4. **Keyword classification, not comprehension.** Deterministic and auditable (every call exposes its `signal_terms`), but it will miss sarcasm, novel phrasing, and context. A headline reading "Boeing halts 737 line" and one reading "Boeing halts 737 line pause" score identically.
5. **Article bodies cannot currently be captured.** The in-app Back control is not being found, so expanding runs stop after one article. `--headlines-only` is the working mode; classification is headline-based, which it was designed for anyway. Fixing the Back selector (`godel-news --debug-back` dumps candidates) would restore full-text capture and auto-upgrade the stored rows.
6. **No per-ticker relevance weighting.** A headline naming five companies scores identically for each — see the `AMD Stock Jumps as Microsoft…` row, which is really an AMD story.
7. **One capture = one day.** All 1,600 rows in the July 30 run share that single date. Build history by running daily; the store deduplicates by headline hash.
8. **Verizon coverage is unconfirmed** — VZ produced no rows on July 30, along with TRV and V. That is likely just no news that day, not a universe gap, but it has not been positively confirmed.
9. **Neutral dominates (494 of 564).** Keyword sentiment is deliberately conservative; most headlines genuinely carry no directional language. Use `--min-impact 3` to cut to the items that scored on a recognized event type.
