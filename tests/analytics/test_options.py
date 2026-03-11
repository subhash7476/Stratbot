"""
Tests for Options Structural Engine
-----------------------------------
Tests for OptionsProvider and OptionsAnalytics.

Run: pytest tests/analytics/test_options.py -v
"""

import pytest
from datetime import datetime, date
from unittest.mock import patch, MagicMock

from core.data.options_provider import OptionsProvider, OptionChainRow, UnderlyingData
from core.analytics.options_analytics import (
    OptionsAnalytics,
    PCRResult,
    GEXResult,
    OIAnalysisResult,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_option_chain():
    """Sample option chain for testing."""
    return [
        # CE contracts
        OptionChainRow(
            strike=22400, option_type="CE",
            instrument_key="NSE_FO|54710", tradingsymbol="NIFTY04MAR2622400CE",
            expiry="2026-03-04",
            ltp=185.50, oi=45100, oi_change=8500, volume=125000,
            iv=0.142, delta=0.52, gamma=0.0012, theta=-8.5, vega=12.3,
            lot_size=75, underlying_ltp=22450.30
        ),
        OptionChainRow(
            strike=22500, option_type="CE",
            instrument_key="NSE_FO|54720", tradingsymbol="NIFTY04MAR2622500CE",
            expiry="2026-03-04",
            ltp=125.00, oi=25300, oi_change=-3200, volume=98000,
            iv=0.138, delta=0.42, gamma=0.0015, theta=-7.2, vega=11.8,
            lot_size=75, underlying_ltp=22450.30
        ),
        # PE contracts
        OptionChainRow(
            strike=22400, option_type="PE",
            instrument_key="NSE_FO|54711", tradingsymbol="NIFTY04MAR2622400PE",
            expiry="2026-03-04",
            ltp=142.30, oi=52300, oi_change=3200, volume=98000,
            iv=0.140, delta=-0.48, gamma=0.0012, theta=-7.8, vega=12.1,
            lot_size=75, underlying_ltp=22450.30
        ),
        OptionChainRow(
            strike=22500, option_type="PE",
            instrument_key="NSE_FO|54721", tradingsymbol="NIFTY04MAR2622500PE",
            expiry="2026-03-04",
            ltp=95.50, oi=15400, oi_change=-1500, volume=75000,
            iv=0.135, delta=-0.38, gamma=0.0015, theta=-6.5, vega=11.5,
            lot_size=75, underlying_ltp=22450.30
        ),
    ]


@pytest.fixture
def provider():
    """OptionsProvider instance with mocked DB."""
    with patch.object(OptionsProvider, '_init_db', return_value=None):
        return OptionsProvider()


# ─────────────────────────────────────────────────────────────
# Tests: OptionsProvider
# ─────────────────────────────────────────────────────────────

class TestOptionsProvider:
    
    def test_get_weekly_expiry_nifty(self, provider):
        """Nifty expires on Tuesday."""
        # With instrument master, returns actual expiry from database
        expiry = provider.get_weekly_expiry("NSE_INDEX|Nifty 50", date(2026, 3, 2))
        # Should return 2026-03-10 (first available expiry >= 2026-03-02)
        assert expiry == "2026-03-10"
        
    def test_get_weekly_expiry_banknifty(self, provider):
        """Banknifty expires on Wednesday."""
        # With instrument master, returns actual expiry from database
        expiry = provider.get_weekly_expiry("NSE_INDEX|Nifty Bank", date(2026, 3, 2))
        # Should return 2026-03-04 or 2026-03-30 depending on instrument master
        assert expiry in ["2026-03-04", "2026-03-30"]  # Wednesday expiries
    
    def test_get_index_mapping(self, provider):
        """Test index name to underlying symbol mapping."""
        assert provider.get_index("NIFTY") == "NSE_INDEX|Nifty 50"
        assert provider.get_index("BANKNIFTY") == "NSE_INDEX|Nifty Bank"
        assert provider.get_index("UNKNOWN") == ""
    
    def test_get_available_expiries(self, provider):
        """Should return 4 weekly expiries."""
        expiries = provider.get_available_expiries("NSE_INDEX|Nifty 50", count=4)
        assert len(expiries) == 4
        
        # All should be Tuesdays for Nifty
        for exp in expiries:
            exp_date = datetime.strptime(exp, "%Y-%m-%d")
            assert exp_date.weekday() == 1  # Tuesday
    
    @patch('core.data.options_provider.requests.get')
    @patch('core.auth.credentials.credentials')
    def test_fetch_option_chain_mock(self, mock_creds, mock_get, provider):
        """Test fetching option chain with mocked API."""
        # Mock V2 API response (list format with nested market_data)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "expiry": "2026-03-10",
                    "strike_price": 22400,
                    "underlying_key": "NSE_INDEX|Nifty 50",
                    "underlying_spot_price": 22450.30,
                    "call_options": {
                        "instrument_key": "NSE_FO|54710",
                        "tradingsymbol": "NIFTY04MAR2622400CE",
                        "market_data": {
                            "ltp": 185.50,
                            "oi": 45100,
                            "prev_oi": 36600,
                            "volume": 125000,
                            "close_price": 180.0
                        },
                        "option_greeks": {
                            "iv": 0.142,
                            "delta": 0.52,
                            "gamma": 0.0012,
                            "theta": -8.5,
                            "vega": 12.3
                        }
                    },
                    "put_options": {
                        "instrument_key": "NSE_FO|54711",
                        "tradingsymbol": "NIFTY04MAR2622400PE",
                        "market_data": {
                            "ltp": 142.30,
                            "oi": 52300,
                            "prev_oi": 49100,
                            "volume": 98000,
                            "close_price": 140.0
                        },
                        "option_greeks": {
                            "iv": 0.140,
                            "delta": -0.48,
                            "gamma": 0.0012,
                            "theta": -7.8,
                            "vega": 12.1
                        }
                    }
                }
            ]
        }
        mock_get.return_value = mock_response
        mock_creds.get.return_value = "test_token"

        chain, underlying = provider._fetch_from_upstox(
            "NSE_INDEX|Nifty 50", "2026-03-10"
        )

        assert len(chain) == 2  # 1 CE + 1 PE
        assert chain[0].option_type == "CE"
        assert chain[1].option_type == "PE"
        assert underlying.ltp == 22450.30
        assert chain[0].ltp == 185.50
        assert chain[0].oi == 45100


# ─────────────────────────────────────────────────────────────
# Tests: OptionsAnalytics
# ─────────────────────────────────────────────────────────────

class TestOptionsAnalytics:
    
    def test_pcr_calculation(self, sample_option_chain):
        """Test PCR calculation."""
        result = OptionsAnalytics.calculate_pcr(sample_option_chain)
        
        total_ce_oi = 45100 + 25300  # 70400
        total_pe_oi = 52300 + 15400  # 67700
        expected_pcr = total_pe_oi / total_ce_oi  # ~0.96
        
        assert result.pcr == pytest.approx(expected_pcr, rel=0.01)
        assert result.total_ce_oi == total_ce_oi
        assert result.total_pe_oi == total_pe_oi
        assert result.sentiment == "Neutral"  # 0.7 < 0.96 < 1.2
    
    def test_pcr_sentiment_bullish(self):
        """Test bullish PCR sentiment."""
        chain = [
            OptionChainRow(strike=22400, option_type="CE", instrument_key="NSE_FO|1", tradingsymbol="TESTCE", expiry="2026-03-04", oi=10000),
            OptionChainRow(strike=22400, option_type="PE", instrument_key="NSE_FO|2", tradingsymbol="TESTPE", expiry="2026-03-04", oi=15000),
        ]
        result = OptionsAnalytics.calculate_pcr(chain)
        assert result.pcr > 1.2
        assert result.sentiment == "Bullish"
    
    def test_pcr_sentiment_bearish(self):
        """Test bearish PCR sentiment."""
        chain = [
            OptionChainRow(strike=22400, option_type="CE", instrument_key="NSE_FO|1", tradingsymbol="TESTCE", expiry="2026-03-04", oi=20000),
            OptionChainRow(strike=22400, option_type="PE", instrument_key="NSE_FO|2", tradingsymbol="TESTPE", expiry="2026-03-04", oi=10000),
        ]
        result = OptionsAnalytics.calculate_pcr(chain)
        assert result.pcr < 0.7
        assert result.sentiment == "Bearish"
    
    def test_pcr_change(self, sample_option_chain):
        """Test PCR change calculation."""
        result = OptionsAnalytics.calculate_pcr(sample_option_chain, previous_pcr=0.90)
        assert result.pcr_change is not None
        assert result.pcr_change > 0  # Current PCR ~0.96 > 0.90
    
    def test_oi_analysis_resistance_support(self, sample_option_chain):
        """Test OI analysis identifies resistance and support."""
        result = OptionsAnalytics.analyze_oi_changes(sample_option_chain, underlying_ltp=22450.30)
        
        # Highest CE OI is at 22400 (45100) - resistance
        # Highest PE OI is at 22400 (52300) - support
        assert result.resistance_strike == 22400
        assert result.support_strike == 22400
    
    def test_oi_pattern_identification(self):
        """Test OI buildup pattern identification."""
        assert OptionsAnalytics._identify_pattern(5000, 2.1) == "Long Buildup"
        assert OptionsAnalytics._identify_pattern(5000, -1.5) == "Short Buildup"
        assert OptionsAnalytics._identify_pattern(-3000, -2.0) == "Long Unwinding"
        assert OptionsAnalytics._identify_pattern(-3000, 1.5) == "Short Covering"
        assert OptionsAnalytics._identify_pattern(0, 0.0) == "No Change"
    
    def test_gex_calculation(self, sample_option_chain):
        """Test Net Gamma Exposure calculation."""
        result = OptionsAnalytics.calculate_gex(sample_option_chain, underlying_ltp=22450.30)
        
        # CE gamma is positive, PE gamma is negative
        # Net = CE_gamma * CE_oi * lot - PE_gamma * PE_oi * lot
        # CE: (0.0012 * 45100 * 75) + (0.0015 * 25300 * 75) = 4059 + 2846 = 6905
        # PE: -(0.0012 * 52300 * 75) - (0.0015 * 15400 * 75) = -4707 - 1732 = -6439
        # Net: 6905 - 6439 = 466 (positive)
        
        assert result.net_gamma_ce > 0
        assert result.net_gamma_pe < 0
        assert isinstance(result.net_gamma_total, float)
    
    def test_gex_regime(self, sample_option_chain):
        """Test GEX regime interpretation."""
        result = OptionsAnalytics.calculate_gex(sample_option_chain, underlying_ltp=22450.30)
        
        # With our sample data, net gamma should be slightly positive
        if result.net_gamma_total > 0:
            assert result.regime == "Positive GEX (Stable)"
        elif result.net_gamma_total < 0:
            assert result.regime == "Negative GEX (Volatile)"
        else:
            assert result.regime == "Neutral"
    
    def test_max_pain_calculation(self, sample_option_chain):
        """Test Max Pain calculation."""
        result = OptionsAnalytics.calculate_max_pain(sample_option_chain, spot_price=22450.30)
        
        # Max pain should be one of the strikes
        assert result.max_pain_strike in [22400, 22500]
        assert result.spot_price == 22450.30
        assert result.distance_from_spot == 22450.30 - result.max_pain_strike
    
    def test_atm_strike_detection(self, sample_option_chain):
        """Test ATM strike detection."""
        atm = OptionsAnalytics._find_atm_strike(sample_option_chain, 22450.30)
        # 22450.30 is closer to 22400 or 22500
        assert atm in [22400, 22500]
    
    def test_build_structural_snapshot(self, sample_option_chain):
        """Test complete structural snapshot building."""
        analytics = OptionsAnalytics()
        snapshot = analytics.build_structural_snapshot(
            option_chain=sample_option_chain,
            underlying="NSE_INDEX|Nifty 50",
            underlying_ltp=22450.30,
            expiry="2026-03-04",
            previous_pcr=0.90,
            include_max_pain=True
        )
        
        assert snapshot.underlying == "NSE_INDEX|Nifty 50"
        assert snapshot.underlying_ltp == 22450.30
        assert snapshot.expiry == "2026-03-04"
        assert snapshot.pcr.pcr > 0
        assert snapshot.gex.net_gamma_total is not None
        assert snapshot.oi_analysis.resistance_strike is not None
        assert snapshot.max_pain is not None
        assert snapshot.atm_strike is not None


# ─────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────

class TestIntegration:
    """Integration tests for Options Structural Engine."""
    
    def test_full_flow(self, sample_option_chain):
        """Test complete flow from chain to structural data."""
        # 1. Calculate analytics
        analytics = OptionsAnalytics()
        snapshot = analytics.build_structural_snapshot(
            option_chain=sample_option_chain,
            underlying="NSE_INDEX|Nifty 50",
            underlying_ltp=22450.30,
            expiry="2026-03-04"
        )
        
        # 2. Verify all metrics are present
        assert snapshot.pcr.pcr > 0
        assert snapshot.gex.net_gamma_total is not None
        assert snapshot.oi_analysis.highest_ce_oi_strikes
        assert snapshot.atm_strike is not None
        
        # 3. Test facade dict conversion (mock provider to avoid DB init)
        from app_facade.options_facade import OptionsFacade
        
        with patch.object(OptionsProvider, '_init_db', return_value=None):
            facade = OptionsFacade()
        data_dict = facade.to_dict(snapshot)
        
        assert "pcr" in data_dict
        assert "gex" in data_dict
        assert "oi_analysis" in data_dict
