@echo off
setlocal

rem Godel news program - separate from the trading bot.
rem   run_news.bat              BTC news scrape (default)
rem   run_news.bat login        open Brave: pass Cloudflare + log in, leave it open
rem   run_news.bat status       check the saved session, scrape nothing
rem   run_news.bat dow30 ...    Dow 30 capture (see: run_news.bat dow30 --help)
rem   run_news.bat scrape --asset all

set "PROJECT=%~dp0"
set "PY=%PROJECT%venv\Scripts\python.exe"

if not exist "%PY%" (
    echo ERROR: virtualenv not found: %PY%
    echo Create it with:  py -3.13 -m venv venv  ^&^&  venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

cd /d "%PROJECT%"

if "%~1"=="" (
    "%PY%" -m news scrape
) else (
    "%PY%" -m news %*
)
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" echo News run finished with errors (exit %RC%). Research only - the trading bot is unaffected.
pause
exit /b %RC%
