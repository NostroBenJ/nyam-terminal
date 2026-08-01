@echo off
REM NYAM Terminal — double-click launcher.
REM Boots in mock mode. Set the vars below to go live.

cd /d "%~dp0"

REM --- data mode -------------------------------------------------------------
REM Mock (default): synthetic data, no keys, no cost.
set NYAM_MOCK=1

REM Live free path — ~15-min delayed chains, no OI change / flow / dark pool:
REM   set NYAM_MOCK=0
REM   set NYAM_PROVIDER=yahoo

REM Live Unusual Whales — probe the key first:
REM   cd engine ^&^& python -m data.unusual_whales probe SPY
REM   set NYAM_MOCK=0
REM   set NYAM_PROVIDER=uw
REM   set UW_API_KEY=your-key-here

REM In-app chat and the written brief (optional):
REM   set ANTHROPIC_API_KEY=sk-ant-...

echo Starting NYAM Terminal...
call npm run tauri dev
