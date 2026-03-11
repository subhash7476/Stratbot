from core.backtest.usdinr_attribution import build_usdinr_attribution_report


def test_usdinr_attribution_report_differences():
    without_filter = [-100.0, 50.0, -80.0, 30.0, -20.0]
    with_filter = [40.0, 20.0, -10.0, 35.0]
    report = build_usdinr_attribution_report(
        pnls_without_usdinr_filter=without_filter,
        pnls_with_usdinr_filter=with_filter,
    )
    assert report.strategy_without_usdinr_filter.total_trades == 5
    assert report.strategy_with_usdinr_filter.total_trades == 4
    assert report.win_rate_difference > 0.0
    assert report.expectancy_difference > 0.0

