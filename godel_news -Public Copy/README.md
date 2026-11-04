# Godel news

Scrapes the NEWS panel in Godel Terminal, interprets each headline, and writes
CSVs plus an Excel report. Research and reporting only.

This used to live inside the trading bot (`V4.1algobot-master/quant/news`) and
ran as step 4 of `btc-daily-routine`. It is now its own program with its own
venv, data, and browser profile. It does not import from the bot, read the bot's
files, or write into them, and the bot no longer runs it.

## Run it

Double-click `run_news.bat`, or from this folder:

```
venv\Scripts\python.exe -m news scrape                 # BTC (AB4.1) news
venv\Scripts\python.exe -m news scrape --asset all     # BTC + SPY (SP400)
venv\Scripts\python.exe -m news dow30 --headlines-only # Dow 30 -> data_dow30/
venv\Scripts\python.exe -m news dow30-classify         # classify Dow 30 store
venv\Scripts\python.exe -m news status                 # is the saved session usable?
venv\Scripts\python.exe -m news login                  # pass Cloudflare + log in once
venv\Scripts\python.exe -m news rebuild                # redo scores, duplicate flags, weekly CSVs, outcomes
venv\Scripts\python.exe -m news outcomes               # price moves after each headline + calibration
```

Every scrape also measures what prices did after each headline (1 and 7
days) and, once a score band has 20 independent days of history, fills in
`prob_up_1d` / `prob_up_7d`: the % chance the price was higher. See
SCORING.md section 5.

How every interpreted column is calculated, with keyword lists, formulas,
worked examples and known problems, is in **[SCORING.md](SCORING.md)**.

`run_news.bat <command> ...` passes everything through, e.g. `run_news.bat login`.

## Cloudflare "verify you are human"

Godel is behind Cloudflare. Sometimes Cloudflare shows its check page in place
of the app, so the page has no Login button and no terminal. The old code waited
15 s for a Login button and failed with a bare `Login timeout`.

You have to pass the check yourself, and you **can't** pass it in a Chrome that
ChromeDriver started. Cloudflare detects the automation, and the checkbox loops
even when you click it. So this program uses **attach mode** by default:

1. Run `run_news.bat login`. It opens an ordinary Brave window (Chrome if Brave
   isn't installed) on a dedicated profile (`%LOCALAPPDATA%\BraveSoftware\Brave-CDP`)
   with the debugging port on. Nothing is connected to it while you work in it.
2. In that window, pass the Cloudflare check, log in to Godel, and open the
   AB4.1 news layout. Then press Enter in the console. It checks that you're
   logged in.
3. Leave the window open and run `run_news.bat`. The scraper attaches to that
   tab, reads the news panel, and detaches. It never enters credentials or
   closes your window.

The profile stays logged in, so the next time you only need step 1 to reopen
the window (and step 2 if Cloudflare asks again). `run_news.bat status` shows
what the attached tab is showing.

Launch mode (`GODEL_BROWSER_MODE = "launch"`, ChromeDriver with `godel_profile/`)
still exists. It only works while Cloudflare isn't challenging. When it is, the
run stops with `cloudflare_challenge: ...` and doesn't hang. Nothing here tries
to get around the check.

## Watchlist names

The NEWS panel's group dropdown lists your **Godel watchlists** by name. Each
asset maps to one exact watchlist name in `local_settings.py`:

```
GODEL_GROUP_BTC = "quantAB4.1"     # default "AB4.1"
GODEL_GROUP_SPY = "SP400"          # needs an SP400 watchlist to exist
GODEL_NEWS_LAYOUT = "quantAB4.1"   # bottom layout tab holding the NEWS panel
```

A scrape only runs after the dropdown reads exactly that name. If a watchlist
gets renamed, run `run_news.bat inspect-panel`. It clicks nothing and writes the
dropdown's real options to `data/news/news_panel_inspect.json`.

## Layout

| Path | What |
|---|---|
| `news/` | reader (selenium), store, interpreter, routing, Excel report, CLI |
| `news/controller.py` | Godel browser controller (copy of the bot's `core/controller.py`) |
| `dow30/` | Dow 30 capture (DOW30 watchlist) and classifier |
| `data/news/` | BTC/SPY store: `godel_news_raw.csv`, `godel_news_interpreted.csv`, `routing_quarantine.csv` |
| `data/news/weekly/` | one CSV per Monday–Sunday week (interpreted rows + duplicate flags) |
| `data/news/calibration.csv` | per score band: independent days, % of days up, average move (1d / 7d) |
| `data/prices/` | cached hourly prices (Yahoo Finance) used for outcomes |
| `data/reports/godel_news_report.xlsx` | Excel report |
| `data_dow30/news/` | Dow 30 store, kept separate from BTC/SPY |
| `godel_profile/` | persistent Chrome profile (session cookies) |
| `local_settings.py` | credentials and settings (gitignored; template in `local_settings.example.py`) |

## Tests

```
venv\Scripts\python.exe -m tests.test_news_routing
```
