"""
Confluence Engine
-----------------
Aggregates multiple indicator signals into a single insight.
"""
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd

from core.analytics.models import ConfluenceInsight, IndicatorResult, Bias, ConfluenceSignal
from core.analytics.indicators.ema import EMA
from core.analytics.indicators.rsi import RSI
from core.analytics.indicators.macd import MACD
from core.analytics.indicators.ut_bot import UTBot
from core.analytics.indicators.vwap import VWAP
from core.analytics.indicators.adx import ADX
from core.analytics.indicators.atr import ATR

class ConfluenceEngine:
    """
    Combines indicator facts into actionable insights.
    """

    def __init__(self):
        self.indicators = {
            'EMA_20': EMA(20),
            'EMA_50': EMA(50),
            'RSI': RSI(14),
            'MACD': MACD(),
            'UT_BOT': UTBot(),
            'VWAP': VWAP(),
            'ADX': ADX(14),
            'ATR': ATR(14)
        }

    def generate_insight(self, symbol: str, df: pd.DataFrame) -> Optional[ConfluenceInsight]:
        """
        Calculates all indicators and determines overall bias.
        """
        if len(df) < 50:
            return None
        
        # This remains for real-time single-bar processing
        return self._process_indicators(symbol, df)

    def generate_insights_bulk(self, symbol: str, df: pd.DataFrame) -> List[ConfluenceInsight]:
        """
        Vectorized calculation of insights for a range of bars.
        MUCH faster than bar-by-bar generation.
        """
        if len(df) < 50:
            return []
            
        # 1. Calculate all indicators in vectorized fashion for the whole DF
        ema20_series = self.indicators['EMA_20'].calculate(df)
        ema50_series = self.indicators['EMA_50'].calculate(df)
        rsi_series = self.indicators['RSI'].calculate(df)
        macd_df = self.indicators['MACD'].calculate(df)
        ut_df = self.indicators['UT_BOT'].calculate(df)
        vwap_df = self.indicators['VWAP'].calculate(df, anchor="Session", market="NSE")
        adx_series = self.indicators['ADX'].calculate(df)
        atr_series = self.indicators['ATR'].calculate(df)
        
        insights = []
        # We start from index 50 to ensure indicators have enough data
        for i in range(50, len(df)):
            row_ts = df['timestamp'].iloc[i]
            row_close = df['close'].iloc[i]
            
            results = []
            
            # EMA
            e20 = ema20_series.iloc[i]
            e50 = ema50_series.iloc[i]
            ema_bias = Bias.BULLISH if e20 > e50 else Bias.BEARISH
            results.append(IndicatorResult("EMA_Cross", ema_bias, e20, {"ema50": e50}))
            
            # RSI
            r_val = rsi_series.iloc[i]
            r_bias = Bias.NEUTRAL
            if r_val > 60: r_bias = Bias.BULLISH
            elif r_val < 40: r_bias = Bias.BEARISH
            results.append(IndicatorResult("RSI", r_bias, r_val, {
                "overbought": r_val > 70,
                "oversold": r_val < 30
            }))
            
            # MACD
            m_val = macd_df['macd'].iloc[i] if 'macd' in macd_df.columns else 0.0
            m_sig = macd_df['signal'].iloc[i] if 'signal' in macd_df.columns else 0.0
            m_hist = macd_df['hist'].iloc[i] if 'hist' in macd_df.columns else 0.0
            m_hist_prev = macd_df['hist'].iloc[i-1] if 'hist' in macd_df.columns and i > 0 else 0.0
            
            m_bullish = m_val > m_sig
            m_increasing = m_hist > m_hist_prev
            
            m_bias = Bias.BULLISH if m_bullish else Bias.BEARISH
            results.append(IndicatorResult("MACD", m_bias, m_val, {
                "signal_line": m_sig, "bullish": m_bullish, "increasing": m_increasing
            }))
            
            # UT Bot
            ut_stop = ut_df['stop'].iloc[i]
            ut_buy = row_close > ut_stop
            ut_sell = row_close < ut_stop
            ut_bias = Bias.BULLISH if ut_buy else (Bias.BEARISH if ut_sell else Bias.NEUTRAL)
            results.append(IndicatorResult("UT_BOT", ut_bias, ut_stop, {
                "buy_signal": ut_buy, "sell_signal": ut_sell, "current_stop": ut_stop, "current_price": row_close
            }))
            
            # VWAP
            v_val = vwap_df['vwap'].iloc[i]
            above_v = row_close > v_val
            below_v = row_close < v_val
            v_bias = Bias.BULLISH if above_v else (Bias.BEARISH if below_v else Bias.NEUTRAL)
            results.append(IndicatorResult("VWAP", v_bias, v_val, {
                "above_vwap": above_v, "below_vwap": below_v, "close_price": row_close
            }))

            # ADX & ATR
            cur_adx = adx_series.iloc[i]
            cur_atr = atr_series.iloc[i]
            results.append(IndicatorResult("ADX", Bias.NEUTRAL, cur_adx, {}))
            results.append(IndicatorResult("ATR", Bias.NEUTRAL, cur_atr, {}))
            
            # Premium Flags (Enhanced with ADX and Momentum)
            p_buy = ut_buy and m_bullish and m_increasing and r_bias == Bias.BULLISH and r_val <= 70 and above_v and cur_adx > 25
            p_sell = ut_sell and not m_bullish and not m_increasing and r_bias == Bias.BEARISH and r_val >= 30 and below_v and cur_adx > 25
            
            results.append(IndicatorResult("premium_flags", Bias.NEUTRAL, 0.0, {
                "premiumBuy": p_buy, "premiumSell": p_sell,
                "ut_buy": ut_buy, "ut_sell": ut_sell,
                "macd_bullish": m_bullish, "macd_increasing": m_increasing,
                "rsi_bullish": r_bias == Bias.BULLISH,
                "above_vwap": above_v, "adx": cur_adx, "atr": cur_atr
            }))
            
            # Aggregation
            bullish_count = sum(1 for r in results if r.bias == Bias.BULLISH)
            bearish_count = sum(1 for r in results if r.bias == Bias.BEARISH)
            
            total = len(results)
            confidence = max(bullish_count, bearish_count) / total if total > 0 else 0.0
            
            overall_bias = Bias.NEUTRAL
            if bullish_count > bearish_count: overall_bias = Bias.BULLISH
            elif bearish_count > bullish_count: overall_bias = Bias.BEARISH
            
            signal = ConfluenceSignal.NEUTRAL
            if p_buy:
                signal = ConfluenceSignal.BUY
                overall_bias = Bias.BULLISH
                confidence = 0.9
            elif p_sell:
                signal = ConfluenceSignal.SELL
                overall_bias = Bias.BEARISH
                confidence = 0.9
                
            insights.append(ConfluenceInsight(
                timestamp=row_ts, symbol=symbol, bias=overall_bias, confidence_score=confidence,
                indicator_results=results, signal=signal, agreement_level=confidence
            ))
            
        return insights

    def _process_indicators(self, symbol: str, df: pd.DataFrame) -> ConfluenceInsight:
        results = []
        last_row = df.iloc[-1]

        # EMA Bias
        ema20 = self.indicators['EMA_20'].calculate(df).iloc[-1]
        ema50 = self.indicators['EMA_50'].calculate(df).iloc[-1]

        ema_bias = Bias.BULLISH if ema20 > ema50 else Bias.BEARISH
        results.append(IndicatorResult("EMA_Cross", ema_bias, ema20, {"ema50": ema50}))

        # RSI Bias
        rsi_series = self.indicators['RSI'].calculate(df)
        rsi_val = rsi_series.iloc[-1]
        rsi_overbought = rsi_val > 70
        rsi_oversold = rsi_val < 30
        rsi_bias = Bias.NEUTRAL
        if rsi_val > 60: rsi_bias = Bias.BULLISH
        elif rsi_val < 40: rsi_bias = Bias.BEARISH
        results.append(IndicatorResult("RSI", rsi_bias, rsi_val, {
            "overbought": rsi_overbought,
            "oversold": rsi_oversold
        }))

        # MACD Bias
        macd_result = self.indicators['MACD'].calculate(df)
        macd_bullish = macd_increasing = False
        if isinstance(macd_result, pd.DataFrame) and 'macd' in macd_result.columns:
            macd_val = macd_result['macd'].iloc[-1]
            macd_signal = macd_result['signal'].iloc[-1]
            macd_hist = macd_result['hist'].iloc[-1] if 'hist' in macd_result.columns else 0.0
            macd_hist_prev = macd_result['hist'].iloc[-2] if 'hist' in macd_result.columns and len(macd_result) > 1 else 0.0
            
            macd_bullish = macd_val > macd_signal
            macd_increasing = macd_hist > macd_hist_prev
            
            macd_bias = Bias.BULLISH if macd_bullish else Bias.BEARISH
            results.append(IndicatorResult("MACD", macd_bias, macd_val, {
                "signal_line": macd_signal,
                "bullish": macd_bullish,
                "increasing": macd_increasing
            }))
        elif isinstance(macd_result, pd.Series):
            macd_val = macd_result.iloc[-1]
            macd_bias = Bias.BULLISH if macd_val > 0 else Bias.BEARISH
            macd_bullish = macd_val > 0
            results.append(IndicatorResult("MACD", macd_bias, macd_val, {
                "bullish": macd_bullish
            }))

        # UT Bot
        ut_buy = ut_sell = False
        try:
            ut_result = self.indicators['UT_BOT'].calculate(df)
            if isinstance(ut_result, pd.DataFrame):
                current_close = df['close'].iloc[-1]
                current_stop = ut_result['stop'].iloc[-1]
                ut_buy = current_close > current_stop
                ut_sell = current_close < current_stop
                ut_bias = Bias.BULLISH if ut_buy else (Bias.BEARISH if ut_sell else Bias.NEUTRAL)
                results.append(IndicatorResult("UT_BOT", ut_bias, current_stop, {
                    "buy_signal": ut_buy, "sell_signal": ut_sell,
                    "current_stop": current_stop, "current_price": current_close
                }))
        except:
            pass

        # VWAP
        above_vwap = below_vwap = False
        try:
            vwap_result = self.indicators['VWAP'].calculate(df, anchor="Session", market="NSE")
            if isinstance(vwap_result, pd.DataFrame) and 'vwap' in vwap_result.columns:
                vwap_val = vwap_result['vwap'].iloc[-1]
                close_price = df['close'].iloc[-1]
                above_vwap = close_price > vwap_val
                below_vwap = close_price < vwap_val
                vwap_bias = Bias.BULLISH if above_vwap else (Bias.BEARISH if below_vwap else Bias.NEUTRAL)
                results.append(IndicatorResult("VWAP", vwap_bias, vwap_val, {
                    "above_vwap": above_vwap, "below_vwap": below_vwap, "close_price": close_price
                }))
        except:
            pass

        # ADX & ATR
        adx_val = atr_val = 0.0
        try:
            adx_val = self.indicators['ADX'].calculate(df).iloc[-1]
            atr_val = self.indicators['ATR'].calculate(df).iloc[-1]
            results.append(IndicatorResult("ADX", Bias.NEUTRAL, adx_val, {}))
            results.append(IndicatorResult("ATR", Bias.NEUTRAL, atr_val, {}))
        except:
            pass

        # Premium Flags
        rsi_bullish = rsi_val > 60
        rsi_bearish = rsi_val < 40
        premium_buy = ut_buy and macd_bullish and macd_increasing and rsi_bullish and not rsi_overbought and above_vwap and adx_val > 25
        premium_sell = ut_sell and not macd_bullish and not macd_increasing and rsi_bearish and not rsi_oversold and below_vwap and adx_val > 25

        premium_result = IndicatorResult(
            name="premium_flags",
            bias=Bias.NEUTRAL,
            value=0.0,
            metadata={
                "premiumBuy": premium_buy, "premiumSell": premium_sell,
                "ut_buy": ut_buy, "ut_sell": ut_sell,
                "macd_bullish": macd_bullish, "macd_increasing": macd_increasing,
                "rsi_bullish": rsi_bullish, "rsi_bearish": rsi_bearish,
                "rsi_overbought": rsi_overbought, "rsi_oversold": rsi_oversold,
                "above_vwap": above_vwap, "adx": adx_val, "atr": atr_val
            }
        )
        results.append(premium_result)

        bullish_count = sum(1 for r in results if r.bias == Bias.BULLISH)
        bearish_count = sum(1 for r in results if r.bias == Bias.BEARISH)
        total = len(results)
        confidence = max(bullish_count, bearish_count) / total if total > 0 else 0.0
        
        overall_bias = Bias.NEUTRAL
        if bullish_count > bearish_count: overall_bias = Bias.BULLISH
        elif bearish_count > bullish_count: overall_bias = Bias.BEARISH

        signal = ConfluenceSignal.NEUTRAL
        if premium_buy:
            signal, overall_bias, confidence = ConfluenceSignal.BUY, Bias.BULLISH, 0.9
        elif premium_sell:
            signal, overall_bias, confidence = ConfluenceSignal.SELL, Bias.BEARISH, 0.9

        return ConfluenceInsight(
            timestamp=df['timestamp'].iloc[-1] if 'timestamp' in df.columns else datetime.now(),
            symbol=symbol, bias=overall_bias, confidence_score=confidence,
            indicator_results=results, signal=signal, agreement_level=confidence
        )
