# Phase 6: Corrected Walk-Forward Scan (All 198 symbols, 15m)
# All 4 backtest bugs fixed:
#   1. Causal swing detection (no look-ahead)
#   2. Position tracker updated on paper fills
#   3. Position stacking guard (max 1 per symbol)
#   4. Shared position tracker (runner + handler same instance)
#
# Train: Oct 2024 - May 2025 (8 months)
# Test:  Jun 2025 - Dec 2025 (7 months)
# Estimated runtime: 6-10 hours for 198 symbols

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "PHASE 6: CORRECTED WALK-FORWARD SCAN (ALL 198 SYMBOLS)" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Bug fixes applied:" -ForegroundColor Green
Write-Host "  [x] Causal swing detection (no look-ahead bias)"
Write-Host "  [x] Position tracker updated on paper fills"
Write-Host "  [x] Position stacking guard (max 1 per symbol)"
Write-Host "  [x] Shared position tracker (exits + entries use same state)"
Write-Host ""
Write-Host "Scan config:" -ForegroundColor Yellow
Write-Host "  Symbols:   All 198 (full universe)"
Write-Host "  Timeframe: 15m"
Write-Host "  Capital:   Rs 1,00,000"
Write-Host "  Train:     2024-10-17 -> 2025-05-31"
Write-Host "  Test:      2025-06-01 -> 2025-12-31"
Write-Host ""
Write-Host "Estimated runtime: 6-10 hours" -ForegroundColor Yellow
Write-Host ""

$start_time = Get-Date
Write-Host "Start time: $start_time" -ForegroundColor Green
Write-Host ""

python scripts\run_symbol_scan.py --timeframe 15m

$exit_code = $LASTEXITCODE
$end_time = Get-Date
$duration = $end_time - $start_time

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "SCAN COMPLETE" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "End time:  $end_time" -ForegroundColor Green
Write-Host "Duration:  $($duration.ToString('hh\:mm\:ss'))" -ForegroundColor Green
Write-Host ""
Write-Host "Results saved to: data\scanner\scanner_index.db" -ForegroundColor Cyan
Write-Host ""

if ($exit_code -ne 0) {
    Write-Host "WARNING: Scan exited with code $exit_code" -ForegroundColor Red
}

Write-Host "Next: review results in Flask UI or run:" -ForegroundColor Yellow
Write-Host "  python scripts\run_symbol_scan.py --timeframe 15m --no-save" -ForegroundColor Yellow
Write-Host ""
