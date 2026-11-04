"""
Settings and paths for the standalone Godel news program.

Resolution order for every setting: environment variable > local_settings.py
(project root, gitignored - copy local_settings.example.py) > default below.
Nothing here reads from, or writes into, the trading bot.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    import local_settings as _local  # optional, gitignored
except ImportError:
    _local = None


def setting(name: str, default):
    val = os.environ.get(name)
    if val:
        return val
    return getattr(_local, name, default)


# --- storage --------------------------------------------------------------
# BTC/SPY share data/news (rows carry a computed association); the Dow 30
# capture writes to its own data_dow30/news so the two never mix.
DATA_DIR = Path(setting("NEWS_DATA_DIR", PROJECT_ROOT / "data"))
DOW30_DATA_DIR = Path(setting("NEWS_DOW30_DATA_DIR", PROJECT_ROOT / "data_dow30"))
PROFILE_DIR = Path(setting("GODEL_PROFILE_DIR", PROJECT_ROOT / "godel_profile"))

# --- Godel Terminal / browser ----------------------------------------------
GODEL_URL = setting("GODEL_URL", "https://app.godelterminal.com/")
GODEL_USERNAME = setting("GODEL_USERNAME", "")
GODEL_PASSWORD = setting("GODEL_PASSWORD", "")

TERMINAL_LOAD_TIMEOUT = 15      # seconds to wait for terminal to load
COMMAND_EXECUTION_TIMEOUT = 30  # seconds to wait for command execution
WINDOW_DETECTION_TIMEOUT = 10   # seconds to wait for new window creation
BROWSER_HEADLESS = str(setting("BROWSER_HEADLESS", "false")).lower() in ("true", "1", "yes")
BROWSER_WINDOW_SIZE = (1920, 1080)
