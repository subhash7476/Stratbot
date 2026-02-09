@echo off
echo Starting Trading Platform...

:: Start Market Ingestor in new window
start "Market Ingestor" cmd /k "cd /d D:\BOT\root && python scripts/market_ingestor.py"

:: Wait 2 seconds for ingestor to initialize
timeout /t 2 /nobreak > nul

:: Start Flask in current window
echo Starting Flask App...
cd /d D:\BOT\root
python scripts/run_flask.py
