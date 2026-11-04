# What is not in this repository, and why

Four things are held back. Everything else ships, including the scoring
model, the reader, and the captured outputs under `data/` and `data_dow30/`.

The rule applied here was narrow: **omit login material and anything
carrying a username, keep everything else.** Nothing was withheld for being
proprietary.

## Login material

| path | what it is |
|---|---|
| `local_settings.py` | Holds `GODEL_USERNAME` and `GODEL_PASSWORD` in plaintext. Copy `local_settings.example.py` over it and fill in your own; the example lists every key with placeholder values, so nothing but the two secrets is missing. |
| `godel_profile/` | A persistent browser profile — cookies and session tokens for a logged-in Godel Terminal account. This is the more dangerous of the two: a password can be changed, and anyone holding this directory is already signed in without needing one. It is recreated automatically on first run. |

Note that `GODEL_BROWSER_MODE=attach` never reads the credentials at all. On
attach mode the two values can be left empty.

## Carries a username, and is rebuildable anyway

| path | what it is |
|---|---|
| `venv/` | A virtualenv bakes absolute paths into `pyvenv.cfg` and into every executable in `Scripts/`, so it carries the username of whoever created it. Rebuild with `pip install -r requirements.txt`. |
| `__pycache__/` | A `.pyc` records the absolute path of the source it was compiled from, so it carries the same username. Regenerates on first import. |

## One edit inside a file that did ship

`data/godel_news_result.json` contained two absolute paths with a username in
them, JSON-escaped (`C:\\Users\\...`). Those two strings were replaced; no
other part of that file, and no other output file, was changed.

`dow30/WORKFLOW.md` had nine absolute paths of the same kind. They now read
`%USERPROFILE%\...`, which still works verbatim in a shell and names nobody.

## What was deliberately *not* held back

Worth stating plainly, so nobody assumes these were missed:

- `SCORING.md` — the full scoring model, including the keyword lists and
  every coefficient formula.
- `news/godel_reader.py` — the working reader for the Godel Terminal NEWS
  panel, selectors and all.
- `data/`, `data_dow30/` — captured news output.

These are published by choice. If that choice is ever revisited, the
`.gitignore` is where to start: the outputs are not listed there, and adding
them is a one-line change.
