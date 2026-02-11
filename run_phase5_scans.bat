@echo off
REM Phase 5: Timeframe Optimization - Full Walk-Forward Validation
REM Runs 3 full scans (15m, 10m, 5m) across all 198 symbols
REM Estimated runtime: 9-12 hours total
REM
REM Train: Oct 2024 - May 2025 (8 months)
REM Test:  Jun 2025 - Dec 2025 (7 months)

echo ================================================================
echo PHASE 5: TIMEFRAME OPTIMIZATION - OVERNIGHT SCAN
echo ================================================================
echo.
echo This will run 3 full scans sequentially:
echo   1. 15m baseline (3-4 hours)
echo   2. 10m test     (3-4 hours)
echo   3. 5m test      (3-4 hours)
echo.
echo Total estimated runtime: 9-12 hours
echo.
echo Results will be saved to: data/scanner/scanner_index.db
echo.
pause

echo.
echo ================================================================
echo SCAN 1/3: 15m Baseline
echo ================================================================
echo Start time: %time%
echo.

python scripts\run_symbol_scan.py --timeframe 15m

if %errorlevel% neq 0 (
    echo ERROR: 15m scan failed with code %errorlevel%
    pause
    exit /b %errorlevel%
)

echo.
echo 15m scan completed at %time%
echo.
timeout /t 5 /nobreak

echo.
echo ================================================================
echo SCAN 2/3: 10m Test
echo ================================================================
echo Start time: %time%
echo.

python scripts\run_symbol_scan.py --timeframe 10m

if %errorlevel% neq 0 (
    echo ERROR: 10m scan failed with code %errorlevel%
    pause
    exit /b %errorlevel%
)

echo.
echo 10m scan completed at %time%
echo.
timeout /t 5 /nobreak

echo.
echo ================================================================
echo SCAN 3/3: 5m Test
echo ================================================================
echo Start time: %time%
echo.

python scripts\run_symbol_scan.py --timeframe 5m

if %errorlevel% neq 0 (
    echo ERROR: 5m scan failed with code %errorlevel%
    pause
    exit /b %errorlevel%
)

echo.
echo ================================================================
echo ALL SCANS COMPLETED SUCCESSFULLY
echo ================================================================
echo End time: %time%
echo.
echo Results saved to: data\scanner\scanner_index.db
echo.
echo Next steps:
echo   1. Open Flask UI: http://localhost:5000/backtest
echo   2. Go to Scanner tab
echo   3. Compare results across all 3 timeframes
echo.
pause
