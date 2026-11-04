"""Copy to local_settings.py (gitignored) and fill in. Environment variables
with the same names override anything set here."""

GODEL_USERNAME = "your_email@example.com"
GODEL_PASSWORD = "changeme"

# "attach" (default) = your own Brave, opened by `run_news.bat login`;
# "launch" = ChromeDriver Chrome (cannot pass a Cloudflare check)
GODEL_BROWSER_MODE = "attach"
GODEL_NEWS_DEFAULT_ASSET = "btc"


# exact Godel watchlist / layout-tab names (the NEWS dropdown lists watchlists)
# GODEL_GROUP_BTC = "AB4.1"
# GODEL_GROUP_SPY = "SP400"
# GODEL_NEWS_LAYOUT = "AB4.1"
