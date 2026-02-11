# Phase 5: Timeframe Optimization - Full Walk-Forward Validation
# Runs 3 full scans (15m, 10m, 5m) across all 198 symbols
# Estimated runtime: 9-12 hours total
#
# Train: Oct 2024 - May 2025 (8 months)
# Test:  Jun 2025 - Dec 2025 (7 months)

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "PHASE 5: TIMEFRAME OPTIMIZATION - OVERNIGHT SCAN" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This will run 3 full scans sequentially:"
Write-Host "  1. 15m baseline (3-4 hours)" -ForegroundColor Yellow
Write-Host "  2. 10m test     (3-4 hours)" -ForegroundColor Yellow
Write-Host "  3. 5m test      (3-4 hours)" -ForegroundColor Yellow
Write-Host ""
Write-Host "Total estimated runtime: 9-12 hours" -ForegroundColor Yellow
Write-Host ""
Write-Host "Results will be saved to: data/scanner/scanner_index.db"
Write-Host ""

$start_time = Get-Date
Write-Host "Overall start time: $start_time" -ForegroundColor Green
Write-Host ""

# Scan 1: 15m
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "SCAN 1/3: 15m Baseline" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
$scan1_start = Get-Date
Write-Host "Start time: $scan1_start" -ForegroundColor Green
Write-Host ""

python scripts\run_symbol_scan.py --timeframe 15m

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: 15m scan failed with code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

$scan1_end = Get-Date
$scan1_duration = $scan1_end - $scan1_start
Write-Host ""
Write-Host "15m scan completed in $($scan1_duration.ToString('hh\:mm\:ss'))" -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 5

# Scan 2: 10m
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "SCAN 2/3: 10m Test" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
$scan2_start = Get-Date
Write-Host "Start time: $scan2_start" -ForegroundColor Green
Write-Host ""

python scripts\run_symbol_scan.py --timeframe 10m

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: 10m scan failed with code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

$scan2_end = Get-Date
$scan2_duration = $scan2_end - $scan2_start
Write-Host ""
Write-Host "10m scan completed in $($scan2_duration.ToString('hh\:mm\:ss'))" -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 5

# Scan 3: 5m
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "SCAN 3/3: 5m Test" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
$scan3_start = Get-Date
Write-Host "Start time: $scan3_start" -ForegroundColor Green
Write-Host ""

python scripts\run_symbol_scan.py --timeframe 5m

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: 5m scan failed with code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

$scan3_end = Get-Date
$scan3_duration = $scan3_end - $scan3_start
Write-Host ""
Write-Host "5m scan completed in $($scan3_duration.ToString('hh\:mm\:ss'))" -ForegroundColor Green
Write-Host ""

# Summary
$end_time = Get-Date
$total_duration = $end_time - $start_time

Write-Host "================================================================" -ForegroundColor Green
Write-Host "ALL SCANS COMPLETED SUCCESSFULLY" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "End time: $end_time" -ForegroundColor Green
Write-Host "Total duration: $($total_duration.ToString('hh\:mm\:ss'))" -ForegroundColor Green
Write-Host ""
Write-Host "Individual scan durations:"
Write-Host "  15m: $($scan1_duration.ToString('hh\:mm\:ss'))"
Write-Host "  10m: $($scan2_duration.ToString('hh\:mm\:ss'))"
Write-Host "  5m:  $($scan3_duration.ToString('hh\:mm\:ss'))"
Write-Host ""
Write-Host "Results saved to: data\scanner\scanner_index.db" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Open Flask UI: http://localhost:5000/backtest"
Write-Host "  2. Go to Scanner tab"
Write-Host "  3. Compare results across all 3 timeframes"
Write-Host ""
