# How the news columns are scored

Every score in `godel_news_interpreted.csv` (and the weekly CSVs) comes from
fixed keyword rules in [`news/interpreter.py`](news/interpreter.py). No AI
model, no network lookups, no randomness. Each row records the rules that
scored it in `scoring_version`. This document describes **version 5**, in use
since 2026-10-31; [section 9](#9-version-history) lists what changed in each
version.

A row is scored from four inputs:

| input | where it comes from |
|---|---|
| **text** | headline + the article body (if one was captured), joined and lower-cased |
| **source** | Godel's Source column, e.g. `Reuters News` |
| **ticker** | Godel's Ticker column, e.g. `COIN` |
| **duplicate flags** | whether the row repeats an earlier copy of the story (novelty only) |

**The scores have no units of their own.** What they mean in price terms is
measured, not assumed: [section 5](#5-what-the-numbers-mean-price-outcomes)
records what prices did after each headline and turns score bands into a
"% chance the price was higher" once there is enough history.

The numbers are for research only. `trade_impact_enabled` is `FALSE` on every
row, and nothing in this program or in the trading bot reads these files to
make a trade.

Figures marked *(your data)* were measured on the stores on 2026-10-31:
369 BTC/SPY rows and 1,600 Dow 30 rows.

---

## 1. How a keyword "hits"

Every column below depends on these rules.

- A keyword hits only as a **whole word**: `war` hits "war" and "wars" but
  not "software", "toward" or "wares". The regular plural counts: **-es**
  after s, x, z, ch or sh, otherwise **-s** ("crashes", "etfs").
- Other forms of a word count only if they are listed ("plunged",
  "plunging" are listed separately).
- The three sentiment lists (bullish, bearish, risk) and a **neutral** list
  are matched together in **one pass, and the longest phrase wins**:
  - "ban lifted" is one bullish hit, not the bearish "ban"
  - "price war" is bearish (competition), not the risk word "war"
  - "pre-war" and "emergency fund" are neutral: they count nowhere and
    switch off the word inside them
- A term on two lists counts on both: "hack" is bearish **and** risk.
- **Strong terms weigh 2**, every other term weighs 1
  ([strong terms](#strong-terms-weight-2)): "bloodbath" outweighs "bearish".
- Each keyword counts **once** per text, however often it appears.
- For **sentiment, the headline and the article are matched separately and
  the headline decides** ([`sentiment`](#sentiment)). Articles are long
  (median about 5,300 characters *(your data)*), and one passing word used to
  decide most rows. Relevance, categories and urgency still read headline and
  article together.

"Strict" means a term reads the same way in almost every context. Every term
was run over the stored articles (headlines and bodies) and its matches read
before it went in. Terms that read the wrong way in real text were left out
or turned into phrases ([section 9](#9-version-history) lists them with
examples).

---

## 2. The keyword lists

### Bullish (180 terms)

| theme | terms |
|---|---|
| price action | rally, rallies, rallied, rallying, surge, surges, surged, surging, soar, soars, soared, soaring, skyrocket, skyrockets, skyrocketed, skyrocketing, rebound, rebounds, rebounded, rebounding, extends gains, extended gains, outperforms, outperformed |
| highs / technicals | all-time high, all time high, ath, record high, new high, 52-week high, 52 week high, breaks above, broke above, breaks out, broke out, golden cross, uptrend, bull run, bull market, bullish, short squeeze |
| flows / crypto | inflow, inflows, etf inflows, institutional demand, accumulation, mass adoption, institutional adoption, growing adoption, rising adoption, adoption grows, adoption accelerates, buys, adopts bitcoin, adopted bitcoin, bitcoin reserve, legal tender, halving |
| analysts | analyst upgrade, rating upgrade, credit upgrade, upgraded to buy / outperform / overweight / hold, upgrades to buy / outperform / overweight, upgraded by, buy rating, outperform rating, overweight rating, raises / raised / boosts / lifts price target, price target raised, price target increased, reiterates buy |
| earnings / guidance | beats / beat / tops / topped estimates, beats / beat / tops expectations, exceeds / exceeded expectations, exceeds estimates, better-than-expected, better than expected, record revenue, record profit, record earnings, raises / raised / boosts / lifts guidance, guidance raised, raises / raised outlook, raises / raised forecast |
| capital return | buyback, share buyback, stock buyback, share repurchase, stock repurchase, repurchase program, raises dividend, dividend increase, dividend hike, hikes dividend, special dividend |
| approvals | approves, wins / receives / secures / gains approval, granted approval, grants approval, approval granted, final approval, charter approval, etf approval, wins / receives / granted / secures regulatory approval, green light, greenlights, greenlit |
| partnerships / launches | partners with, teams up with, strategic partnership, announces partnership, forms partnership, formed a partnership, enters partnership, partnership agreement, etf launch, launches etf, mainnet launch, expands |
| macro | rate cut, cuts rates, cut rates, cuts interest rates, dovish, stimulus, quantitative easing, monetary easing, easing cycle, policy easing, fed easing, liquidity injection |
| legal reversals | lawsuit dismissed, dismisses lawsuit, dismissed the lawsuit, case dismissed, charges dropped, drops / dropped charges, drops / dropped lawsuit, drops case, dropped the case, ban lifted, lifts ban, lifted ban, lifted the ban, sanctions lifted, lifts sanctions, wins lawsuit, wins case, court victory, favorable ruling, acquitted, not guilty, bidding war |

### Bearish (256 terms)

| theme | terms |
|---|---|
| price action | crash, crashed, crashing, plunge, plunges, plunged, plunging, plummet, plummets, plummeted, plummeting, tumble, tumbles, tumbled, tumbling, slump, slumps, slumped, slumping, nosedive, nosedives, nosedived, selloff, sell-off, market / stock / crypto / bond rout, bloodbath, wipeout, wiped out, meltdown, capitulation, panic selling, extends losses, extended losses, deepens losses, underperforms, underperformed, outflow, outflows |
| lows / technicals | bear market, bearish, death cross, downtrend, breaks below, broke below, falls below, fell below, drops below, dropped below, 52-week low, 52 week low, all-time low, all time low |
| analysts | analyst downgrade, rating downgrade, credit downgrade, downgraded to sell / underperform / underweight / hold, downgrades to sell / underperform / underweight / hold, downgraded by, sell rating, underperform rating, underweight rating, cuts / cut / lowers / lowered / slashes price target, price target cut, price target lowered, price target reduced |
| earnings / corporate | misses / missed estimates, misses / missed expectations, falls short of estimates / expectations, below expectations, below estimates, worse-than-expected, worse than expected, profit warning, cuts / cut / lowers / lowered / slashes guidance, guidance cut, cuts / lowers / lowered outlook, cuts / lowers / lowered forecast, write-down, writedown, disappointing results / earnings / guidance, going concern, layoffs, lays off, laid off, job cuts, cuts / cut jobs, dilution, dilutive, delisting, delisted, class action, class-action |
| legal / enforcement | lawsuit, sues, sued, fraud, fraudulent, probe, under investigation, investigation into, opens investigation, launches investigation, launched an investigation, subpoena, subpoenaed, wells notice, cease and desist, cease-and-desist, enforcement action, charged with, faces charges, criminal charges, federal charges, fraud charges, sec / cftc / doj charges, indicted, indictment, arrested, pleads guilty, pleaded guilty, guilty plea, convicted, sentenced, fined, money laundering, embezzlement, embezzled, misappropriated, misappropriation, ponzi, pyramid scheme, scam, scammer, rug pull, rug-pull, rugpull, exit scam, pump and dump, dump, crackdown, ban, bans, banned, banning |
| crypto / funding stress | hack, hacked, exploit, breach, cyberattack, cyber-attack, ransomware, flash crash, drained, stolen, heist, liquidation, liquidations, liquidated, margin call, forced selling, liquidation cascade, depeg, depegged, depegging, de-peg, de-pegged, loses peg, lost its peg, insolvency, insolvent, bankruptcy, bankruptcies, bankrupt, chapter 11, contagion, bank run, liquidity crisis, withdrawal halt, halts / suspends / pauses / freezes withdrawals, suspended / paused withdrawals, withdrawals suspended / paused / frozen, frozen withdrawals, freezes assets, assets frozen, funds frozen, frozen funds, seized, shutdown, government shutdown |
| default (strict) | defaulted, defaults on, default on, debt default, loan default, bond default, sovereign default, payment default, missed payment |
| macro | rate hike, raises rates, raised rates, hikes rates, hawkish, recession fears, recession risk, stagflation, quantitative tightening, monetary tightening, tightening cycle, policy tightening, fed tightening, liquidity tightening, tightening liquidity |
| competition / tone | price war, fee war, downturn, turmoil, collapse, collapses, collapsed, headwinds |

### Risk event (95 terms)

| theme | terms |
|---|---|
| security | hack, hacked, exploit, breach, cyberattack, cyber-attack, ransomware, phishing, drained, stolen, heist, compromised, zero-day, critical vulnerability, security vulnerability, rug pull, rug-pull, rugpull, exit scam |
| funding / market structure | insolvency, insolvent, bankruptcy, bankruptcies, bankrupt, chapter 11, bank run, contagion, liquidity crisis, systemic risk, black swan, liquidation cascade, flash crash, circuit breaker, halt, halts, halted, trading suspended, suspends trading, depeg (and its forms), seized, the withdrawal-freeze phrases above, the strict default phrases above, government shutdown |
| operational | outage, service disruption |
| geopolitical | war, attack, attacked, invasion, invades, invaded, missile, airstrike, air strike, drone strike, military strike, bombing, terrorist, terrorism, coup, martial law, assassination, assassinated, sanctions, nuclear test, nuclear strike, blockade, emergency |
| natural / health | earthquake, tsunami, hurricane, outbreak |

Financial-loss events (hacks, insolvency, frozen withdrawals, defaults) are on
the bearish **and** risk lists, like `hack` always was. Geopolitical, natural
and operational events are risk only.

### Strong terms (weight 2)

Large moves and decisive events. Every other term weighs 1.

| side | strong terms |
|---|---|
| bullish | soar(s/ed/ing), skyrocket(s/ed/ing), all-time high, all time high, ath, short squeeze, etf approval, raises / raised / boosts / lifts guidance, guidance raised, quantitative easing, bitcoin reserve, legal tender, every legal reversal (lawsuit dismissed, charges dropped, ban lifted, sanctions lifted, acquitted, …) |
| bearish | crash(ed/ing), plunge(s/d/ing), plummet(s/ed/ing), nosedive(s/d), market / stock / crypto / bond rout, bloodbath, wipeout, wiped out, meltdown, capitulation, panic selling, all-time low, all time low, flash crash, collapse(s/d), profit warning, cuts / cut / lowers / lowered / slashes guidance, guidance cut, going concern, criminal / fraud / sec / cftc / doj charges, indicted, indictment, wells notice, pleads / pleaded guilty, guilty plea, convicted, ponzi, pyramid scheme, rug pull, exit scam, delisted, delisting, quantitative tightening, missed payment |
| bearish + risk | hack, hacked, exploit, breach, cyberattack, ransomware, drained, stolen, heist, liquidation cascade, depeg (all forms), insolvency, insolvent, bankruptcy, bankrupt, chapter 11, contagion, bank run, liquidity crisis, every withdrawal-halt / freeze phrase, every strict default phrase |
| risk | war, invasion, invades, invaded, missile, airstrike, air strike, drone strike, military strike, bombing, terrorist, terrorism, coup, martial law, assassination, assassinated, nuclear strike, nuclear test, systemic risk, black swan |

### Neutral (count nowhere; each was a misfire in your articles)

pre-war, post-war, war chest, legal war, protocol war, talent war, console
war, culture war, gears of war, star wars, emergency fund, emergency savings,
snow crash, hack vc, 52-week low of, 52-week high of, 52 week low of, 52 week
high of, subject to final approval, pending final approval, analyst upgrades
and downgrades, upgrades and downgrades

### Other lists (unchanged)

| list | keywords |
|---|---|
| **URGENT** | breaking, just in, urgent, alert, developing, halted, emergency |
| **BTC_DIRECT** | btc, bitcoin, satoshi, sats |
| **CRYPTO_GENERAL** | crypto, cryptocurrency, cryptocurrencies, digital asset, eth, ether, ethereum, altcoin, stablecoin, defi, blockchain, web3, token, tokenized, tokenised, tokenization, tokenisation |

Category keywords are under [`categories`](#categories).

---

## 3. The interpretation columns

### `sentiment`
**The headline decides.** Count the bullish, bearish and risk terms in the
headline and take the first rule that applies:

1. any risk term → `risk_event`
2. bullish terms outweigh bearish terms → `bullish`
3. bearish terms outweigh bullish terms → `bearish`

"Outweigh" uses the keyword weights (strong = 2): *"Bitcoin rallies despite
crash"* is bearish, 1 against 2.

If none applies (the headline has no sentiment terms, or a tie), **the
article may break it, but only with clear evidence**:

4. at least **2 different risk terms** in the article → `risk_event`
5. at least **3 bullish terms, and more than twice** the bearish ones → `bullish`
6. at least **3 bearish terms, and more than twice** the bullish ones → `bearish`
7. otherwise → `neutral`

The article rules **count** distinct terms instead of weighing them, so one
strong word in passing still can't decide a row. This is the same rule the Dow 30 classifier (`dow30/classify.py`) uses. The
thresholds are `ARTICLE_MIN_RISK_TERMS`, `ARTICLE_MIN_TERMS` and
`ARTICLE_MAJORITY` in `interpreter.py`.

### `sentiment_basis`
Which text decided: `headline`, `article`, or `none` (neutral). Filter on
`headline` for the strictest signals. *(your data, BTC/SPY)*: 60 headline,
47 article, 262 none.

The columns below that use "the terms" (direction, confidence, explanation,
`news_sentiment_score`) take them from **the text that decided**: the
headline, or the article when it decided. On a neutral row that is the
headline, so a neutral row always scores 0.

### `btc_relevance` (0–100)
Built up in this order:

1. `90` if a BTC_DIRECT keyword hits **or** the ticker is exactly `BTC`;
   otherwise `60` if a CRYPTO_GENERAL keyword hits; otherwise `0`
2. a **Fed** or **macro** category keyword raises it to at least `40`
3. an **equity_crypto_proxy** keyword (coin, coinbase, robinhood, mstr, riot,
   mara, marathon digital, hood, sq, pypl) raises it to at least `50`
4. an **ETF** keyword adds `+10` (capped at 100), but only if the value is
   already 40 or more

Possible values: 0, 40, 50, 60, 70, 90, 100. The ticker column only counts
when it is `BTC`.

### `impact_direction`

| sentiment | direction |
|---|---|
| bullish | `up` |
| bearish | `down` |
| risk_event | `down`, or `mixed` if the deciding text also had a bullish term |
| neutral | `none` if btc_relevance < 30, else `mixed` |

`mixed` therefore means two different things: "conflicting signals" (risk +
bullish), or "relevant but no direction words" (neutral and relevance ≥ 30).

### `impact_horizon`
A fixed label per kind of news, first match wins:

1. category **hack** or **liquidation** → `intraday`
2. category **Fed** or **macro** → `1w`
3. category **ETF** or **regulation** → `3d`
4. sentiment is not neutral → `1d`
5. otherwise → `unknown`

These are built-in assumptions, not measured from price data.

### `confidence`
Adds up the **weights** of the distinct bullish, bearish and risk terms in
the deciding text (strong = 2, others = 1). A term on two lists ("hack")
counts once. Urgent words don't count.

| total weight | 0 | 1 | 2 | 3 | 4 | 5+ |
|---|---|---|---|---|---|---|
| confidence | 0.20 | 0.40 | 0.55 | 0.70 | 0.85 | 0.90 |

Formula: `0.2` with none, else `min(0.9, 0.25 + 0.15 × weight)`. One mild
word gives 0.40 and one strong word 0.55. This is "how much signal fired",
not a probability of being right. An article-decided row needs at least 2–3
terms, so it shows 0.55 or more, but that doesn't make it more reliable
than a headline; use `sentiment_basis` for that.

### `categories`
Every category with at least one keyword hit, comma-separated and sorted
(capitalized names sort first). `general` if none.

| category | keywords |
|---|---|
| regulation | sec, securities and exchange commission, cftc, regulation, regulator, regulatory, lawsuit, ban, banning, compliance, license, licensed, crackdown, legislation, bill |
| ETF | etf, spot etf, ishares, grayscale, inflow, outflow, aum |
| macro | cpi, inflation, inflationary, gdp, unemployment, recession, treasury, yield, dollar, dxy, tariff |
| Fed | fed, fomc, powell, rate cut, rate hike, federal reserve |
| exchange | binance, coinbase, kraken, okx, bybit, exchange, listing, delisting, withdrawal |
| hack | hack, hacked, hacker, exploit, exploited, breach, stolen, attacker |
| liquidation | liquidation, liquidated, margin call, leverage, leveraged |
| mining | miner, miners, mining, hashrate, difficulty |
| institutional | blackrock, fidelity, microstrategy, strategy, institutional, treasury allocation, pension, sovereign |
| earnings | earnings, revenue, eps, guidance, quarterly |
| equity_crypto_proxy | coin, coinbase, robinhood, mstr, riot, mara, marathon digital, hood, sq, pypl |
| market_structure | futures, options, open interest, funding rate, basis, spot volume, order book |

### `entities`
2–5-letter ALL-CAPS words in the text that are one of 16 known tickers (BTC,
ETH, SOL, XRP, SPY, QQQ, COIN, MSTR, RIOT, MARA, GBTC, IBIT, FBTC, HOOD, SQ,
PYPL), plus the row's own ticker; sorted, comma-joined.

### `explanation`
`<sentiment> (<up to 3 terms>) from <headline|article> with BTC relevance <n>`,
e.g. `bullish (charter approval, soars) from headline with BTC relevance 100`.
The terms are the deciding text's bullish ones, then bearish, then risk, each
group alphabetical, duplicates removed. A neutral row reads
`neutral (no strong keywords) with BTC relevance <n>`. The same row always
gets the same text.

---

## 4. The coefficient columns

### `news_sentiment_score` (−1 to +1)
The **net keyword weight** of the deciding text, divided by 4 and capped:

```
score = (bullish weight − bearish weight − risk weight) / 4,   capped at ±1
```

| headline | net weight | score |
|---|---|---|
| "Bitcoin turns bearish" | −1 | −0.25 |
| "Bitcoin bloodbath" | −2 | −0.50 |
| "Bitcoin bloodbath as Fed turns hawkish" | −3 | −0.75 |
| "Coinbase beats estimates, raises guidance" | +1 +2 = +3 | +0.75 |
| "Exchange hacked" (bearish **and** risk, strong) | −2 −2 = −4 | −1.00 |

- A term on both the bearish and risk lists subtracts on **both** (unlike
  `confidence`). One "seized" scores (−1 −1) / 4 = −0.5.
- An **article-decided** row is capped at **±0.25**, the strength of one mild
  headline word. Articles hold many words, and without the cap their net
  weight would outrank headlines (`ARTICLE_MAX_SCORE`).
- A neutral row always scores 0.

### `btc_relevance_coeff` (0 to 1)
`btc_relevance / 100`.

### `source_reliability_coeff`
A source gets a table score when the table name (or an alias) appears as
whole words in Godel's long-form name. A trailing ", The" is moved to the
front first. Anything else gets `0.5`.

| source name contains | score |
|---|---|
| reuters, bloomberg, wsj / wall street journal | 0.90 |
| ap / associated press, financial times | 0.85 |
| cnbc | 0.80 |
| coindesk, the block | 0.70 |
| cointelegraph | 0.65 |
| decrypt | 0.60 |
| twitter, reddit | 0.30 |
| exactly `X` (0.30) or exactly `FT` (0.85) | |
| anything else | 0.50 |

*(your data)*: Reuters News → 0.9 (78 rows), Associated Press → 0.85 (7),
CNBC TV Transcripts → 0.8 (2). Every other source is 0.5.

### `novelty_coeff` (0.3 or 1.0)
`0.3` if the row is a flagged duplicate (`is_duplicate = TRUE`), otherwise
`1.0`. It is recomputed with the flags on every write.

### `urgency_coeff`
URGENT hits: none → `0.1`, one → `0.65`, two or more → `1.0`.

### `final_news_alpha_coeff` (−1 to +1)

```
alpha = news_sentiment_score
      × btc_relevance_coeff
      × source_reliability_coeff
      × (0.5 + 0.5 × urgency_coeff)      ← 0.55 none, 0.825 one, 1.0 two+
      × novelty_coeff
```

The result is clamped to ±1 and rounded to 4 decimals. Largest possible size:
±0.9. Observed on BTC/SPY: −0.297 to +0.2063, non-zero on 89 of 369 rows
*(your data)*. Neutral rows are always 0.

### `trade_impact_enabled`
Always `FALSE`. A fixed constant marking these numbers as research-only.

### `scoring_version`
Which rule set scored the row (`5` = this document). When the rules change,
older rows are re-scored automatically from the raw CSV, which keeps the full
article text, on the next scrape or `run_news.bat rebuild`.

---

## 5. What the numbers mean (price outcomes)

The keyword score ranks headlines. It doesn't say how much prices move. That
link is **measured** from what happened after past headlines, by
[`news/outcomes.py`](news/outcomes.py), after every scrape and on
`run_news.bat outcomes`.

**How a headline's outcome is measured**

- **Price series:** BTC/SPY store → BTC-USD (SPY for SPY-associated rows);
  Dow 30 store → the row's own ticker. Hourly bars from Yahoo Finance,
  cached in `data/prices/`.
- **Start:** the first hourly bar **at or after** the headline's
  timestamp. Headlines that report a move ("Stock Soars 15%") are therefore
  not credited with the move they describe. Godel's times are read as US
  Eastern (`NEWS_TIMEZONE` in `local_settings.py`).
- **Horizons:** 1 day and 7 days after the start.

| column | meaning |
|---|---|
| `fwd_return_1d_pct`, `fwd_return_7d_pct` | this row's own price move after the headline, in % |
| `outcome_symbol`, `outcome_entry_utc` | which price and the bar the clock started on |
| `outcome_status` | `ok`, `pending_7d` / `pending` (window not over yet), `no_price`, `no_time`, `no_symbol` |
| `signal_band` | strong negative (score ≤ −0.5), mild negative, neutral (0), mild positive, strong positive (≥ 0.5) |
| `prob_up_1d`, `prob_up_7d` | **0–100: % of past independent days the price was higher** after headlines in this band (50 = coin flip) |
| `expected_move_1d_pct`, `expected_move_7d_pct` | average move after headlines in this band, in % |
| `calibration_note` | how much history the band has, e.g. `1d 11/20 days` |

**Rules that keep the numbers honest**

- **A day is one observation.** All headlines in a band that start on the
  same day share one market move, so they are averaged into one number. 30
  BTC headlines on one day, or 29 Dow stocks on one day, are **one**
  observation, not 30.
- **Only once there's enough history:** `prob_up_*` and `expected_move_*`
  stay blank until a band has **20 independent days**
  (`CALIBRATION_MIN_DAYS`).
- **What's left out:** flagged duplicates, and for BTC, rows with relevance
  below 40.
- **20 days is a minimum, not proof.** With 20 days, a measured 65% still has
  a margin of about ±22 points, so it isn't distinguishable from 50%. Treat
  40–60+ days as decision-grade.

**Where it stands *(your data, 2026-10-31)*.** Nothing is calibrated yet.
`calibration.csv` in each store holds the full table:

| BTC/SPY, 1 day | days | % up | avg move |
|---|---|---|---|
| strong negative | 6 | 50.0 | −0.49% |
| mild negative | 8 | 75.0 | +0.21% |
| neutral | 11 | 63.6 | +0.08% |
| mild positive | 3 | 66.7 | +0.29% |
| strong positive | 3 | 66.7 | −0.01% |

These early numbers are noise at this sample size. The Dow 30 store has
headlines from **one** day (Jul 30), so every band shows 100% "up", which is
just how the market did on Jul 31. Each day you scrape adds at most one day
to each band that had headlines that day. Neutral fills fastest; the strong
bands need days with strong headlines.

**Limits**

- **Dow 30 moves include the market.** A stock's move is measured in absolute
  terms. Measuring it against SPY would isolate what the news itself did.
- **BTC rows measure BTC** even when the news is about a proxy stock (COIN,
  MSTR).
- **7-day windows overlap:** consecutive days share most of their 7 days, so
  7-day observations are less independent than 1-day ones.

---

## 6. Duplicate columns and weekly files

Duplicates are **flagged, never removed**. These five columns sit right
after `detail_status`:

| column | meaning |
|---|---|
| `is_duplicate` | `True` on every copy after the first, `False` on the original |
| `duplicate_type` | `exact`, `near`, `article`, or blank on originals |
| `duplicate_of` | `headline_hash` of the original row |
| `duplicate_similarity` | 1.0 for exact; weighted word overlap for near |
| `duplicate_group_size` | rows in the group including the original (1 = unique) |

The original is the earliest copy by news date and time. To see each story
once, filter `is_duplicate = False`.

- **exact**: same headline text, ignoring case, punctuation and a trailing
  `[Source Name]` tag, within 72 hours of the original.
- **near**: the same story reworded (from any outlet). It must pass all of:
  1. within 72 hours of the original
  2. the same ticker, unless the headlines overlap 85% or more
  3. compatible numbers: when both headlines have numbers, the ones in the
     shorter list must appear in the other. "$6.3B", "$6.3 billion" and "$6
     billion" match, because large amounts may be rounded by up to 10%.
     Dates, prices and percentages must match exactly ("30 JUN" is not
     "16 JUN"; a $150 target is not a $155 one). Month names must match too.
  4. at least 70% word overlap, with words weighted by rarity
  5. not a template (more than 8 headlines sharing the opening, like
     "GOLDMAN SACHS INTERNATIONAL Announces Outstanding Short Selling
     Positions of …")
- **article**: a heavily reworded copy caught through the **article text**.
  Same window, same ticker, compatible headline numbers and template guard
  as above, with at least 62% rarity-weighted similarity between the two
  articles. Example: *"NIUM Acquires CypherD Wallet"* and *"Nium Acquires
  Cypher to Expand Fiat-to-On-Chain Infrastructure"* (0.91). Only rows with
  at least 300 characters of article text take part (Dow 30 rows have none).

  On 2026-10-31 every flagged pair was checked by reading it *(your data)*:
  BTC/SPY 15 exact, 6 near and 7 article; Dow 30 122 exact and 17 near. The
  0.62 article threshold leaves out Zacks' templated daily recaps of
  different days (0.60), at the cost of one real pair at 0.60.

**Weekly files** are in `data/news/weekly/` (Dow 30: `data_dow30/news/weekly/`),
one per Monday–Sunday week of news date, named like
`2026-W44_2026-10-26_to_2026-11-01.csv`. They have the same columns as
the interpreted CSV, in news-time order, and are rebuilt on every scrape. A
week open in Excel is skipped and caught up next run.

---

## 7. Worked examples (your data, version 5)

**Circle Internet (CRCL) Stock Soars 15% Following Federal Bank Charter
Approval** (Blockonomi)

- headline: bullish `charter approval` (1) + `soars` (2, strong) → `bullish`
  from headline → `up`; weight 3 → confidence 0.70
- score 3 / 4 = 0.75; relevance 100
- alpha = 0.75 × 1.0 × 0.5 × 0.55 × 1.0 = **+0.2063** (the highest in the store)

**Binance's $6.3B Hack Sends Bitcoin ETF Tumbling 5%, but This ETF Is Up 21%**

- headline: `hack` (strong, bearish **and** risk) and `tumbling` (bearish) →
  `risk_event` from headline → `down`
- net −2 −2 −1 = −5 → score capped at −1; relevance 100
- alpha = −1 × 1.0 × 0.5 × 0.55 × 1.0 = **−0.275**

**U.S. Government Moves Seized Bitcoin And Ethereum To Coinbase Exchange**

- headline: `seized` (mild, bearish **and** risk) → `risk_event` → `down`
- score (−1 −1) / 4 = −0.5; confidence 0.40
- alpha = −0.5 × 0.9 × 0.5 × 0.55 × 1.0 = **−0.1238**

**Bitcoin has plummeted nearly 30% this year. Why is it falling and will it
rebound?**

- headline: `plummeted` (2) against `rebound` (1) → `bearish` from headline.
  Under version 4 this was a 1–1 tie and neutral
- score (1 − 2) / 4 = −0.25; alpha **−0.0619**

**8 Undervalued Software Stocks to Buy Now**

- headline: no sentiment terms. The article has 3 bullish terms (`inflow`,
  `outperform rating`, `overweight rating`) against 1 bearish (`rate hike`)
  → `bullish` **from article**
- net +2 → 0.5, capped to **0.25** for an article-decided row; relevance 60
- alpha = 0.25 × 0.6 × 0.5 × 0.55 × 1.0 = **+0.0413**

**Analyst Says Bitcoin Bear Market Nearing End as Momentum Slows**

- headline: `bear market` (mild) → `bearish` from headline, score −0.25.
  Keyword counting can't read "nearing end"
- urgency 0.65 from "breaking" in the article (see section 8);
  alpha = −0.25 × 0.9 × 0.5 × 0.825 × 1.0 = **−0.0928**

**From libertarian dream to political weapon: How the far right
appropriated the crypto wave**

- headline: no sentiment terms. The article's 4 bearish terms against 2
  bullish are not "more than twice" → `neutral`, alpha 0

---

## 8. Known limits

1. **Urgency still reads the article.** In the BTC/SPY store, 60 rows get
   their urgency only from article words ("breaking" 31, "developing" 16,
   "alert" 12, …), against 3 with an urgent word in the headline
   *(your data)*. That raises their alpha by half (0.55 → 0.825), including
   the "Bear Market Nearing End" row above.
2. **Keywords can't read context.** "Bear Market Nearing End" counts as
   bearish; "will it rebound?" counts as bullish. Only the listed reversals
   ("ban lifted", "charges dropped", …) are understood.
3. **`mixed` has two meanings**, and **risk terms weigh double** in
   `news_sentiment_score` when they are also bearish.
4. **Relevance ignores crypto-proxy tickers** without a keyword in the text.
5. **Horizons and categories come from the whole article**, so long articles
   collect many categories (Circle's `intraday` above).

---

## 9. Version history

**Version 1** (all rows before 2026-10-31) found keywords anywhere inside
other words: `war` in "software" (199 BTC/SPY rows), `ath` in "path" (148),
`sec` in "sector" (145), `eth` in "whether" (136), `bill` in "billion" (103),
`ban` in "bank" (94).

**Version 2** matched whole words, matched Godel's long source names to the
reliability table, counted a two-list word once in `confidence`, made
`explanation` stable, and took novelty from the duplicate flags.

**Version 3**:

- the sentiment lists grew from 35 / 41 / 18 terms to 180 / 256 / 95
  (bullish / bearish / risk), each checked against the stored articles
- the longest phrase wins across all lists, which makes reversals and
  neutral phrases possible
- plurals follow English rules (so no more "routes" for "rout" or "wares"
  for "war")
- original words that misfired were replaced by strict phrases or dropped:

| dropped word | how your articles used it | replaced by |
|---|---|---|
| approval, approved | "No securities regulatory authority has **approved** or disapproved…", "subject to receipt of regulatory **approvals**" | approves, wins / granted / final / charter approval, etf approval |
| launch, launched, launching | "memecoins **launched** by Donald Trump", "before **launching** his firm" | etf launch, launches etf, mainnet launch |
| adoption, adopts | "**adoption** was limited", "Resort **adopts** AI Concierge" | mass / institutional / growing adoption, adopts bitcoin |
| partnership | "partnerships, licensing arrangements…" (a list) | partners with, strategic partnership, announces partnership |
| upgrade(d), downgrade(d) | "Analyst Upgrades and Downgrades" (a section header), "protocol upgrade" | upgraded to buy, analyst upgrade, downgraded to sell, price target phrases |
| bought, acquire(d) | "It **bought** over 50 global licenses" | (dropped; `buys` kept) |
| integration | "accumulated the **integrations**" | (dropped) |
| easing | "Micron **easing** 1.6%" (a price drop) | quantitative / monetary / policy easing, easing cycle |
| tightening | "supply may be **tightening**" (bullish for price) | quantitative / monetary / policy tightening |
| default | "remains the **default** gateway to Ethereum" | defaulted, debt default, defaults on, … |
| charges | "GBTC **charges** 1.5%" (a fee) | charged with, criminal / federal / fraud charges, sec charges |
| exploited | "a blockchain can also be **exploited** to layer functionality" | (dropped; `exploit` kept) |

Also left out after checking: mild verbs that read either way (falls, fell,
drops, dropped, declined, declines, jumps, climbs; e.g. "attorney declined to
comment", "Treasury yield climbed"), "strong buy"/"strong sell" (Zacks
boilerplate), "exodus" (a wallet brand), "theft" (often intellectual-property
theft), "sanctioned" (also means approved), "pandemic" (mostly history), and
"financial crisis" (mostly the 2008 one).

**Version 4**: the headline decides sentiment, and the
article only breaks a neutral or tied headline with clear evidence (rules
4–7 under `sentiment`). The new column `sentiment_basis` shows which text
decided. 95 BTC/SPY rows changed. The Dow 30 store has headlines only, so
none of its rows changed.

**Version 5** (this document): 165 strong terms weigh 2 and all other terms
weigh 1. A headline's side is decided by weight, so rows that were tied can
now lean one way (2 BTC/SPY rows and 1 Dow 30 row did). `news_sentiment_score` became the net weight ÷ 4
(it used to be a ratio, so any single word gave ±1). Confidence counts
weight, and article-decided scores are capped at ±0.25.

Effect on the BTC/SPY store (369 rows) *(data)*:

| | v1 | v2 | v3 | v4 | v5 |
|---|---|---|---|---|---|
| risk_event | 226 | 64 | 59 | 31 | 31 |
| bullish | 33 | 134 | 75 | 48 | 48 |
| bearish | 27 | 25 | 57 | 28 | 30 |
| neutral | 83 | 146 | 178 | 262 | 260 |
| non-zero alpha | 207 | 188 | 161 | 88 | 89 |

Version 5 scores spread out instead of piling up at ±1. On BTC/SPY, v4 had
80 of 107 non-zero scores at exactly ±1; v5 has −1 ×5, −0.75 ×1, −0.5 ×7,
−0.25 ×48, +0.25 ×41, +0.5 ×4, +0.75 ×2 (headline and article rows
together).

**Outcomes and calibration** (2026-10-31, not a scoring change; `scoring_version`
stays 5): section 5.

Backups of every earlier version are in `data/_migration_backups/` and
`data_dow30/_migration_backups/`.
