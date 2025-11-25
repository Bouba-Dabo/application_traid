import yfinance as yf
import pandas as pd
import pandas_ta as pta
import requests
from typing import List, Dict, Any

# #############################################################################
# DATA FETCHING
# #############################################################################

def fetch_data(symbol: str, period: str = "60d", interval: str = "1d") -> pd.DataFrame:
    """Fetches historical data for a given symbol from Yahoo Finance."""
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data for symbol {symbol}")
    df = df.dropna()
    return df

def fetch_fundamentals(symbol: str) -> dict:
    """Fetches a subset of fundamental data for a given symbol."""
    t = yf.Ticker(symbol)
    info = t.info if hasattr(t, 'info') else {}
    fields = [
        'trailingPE', 'forwardPE', 'priceToBook', 'marketCap', 'debtToEquity',
        'totalDebt', 'ebitda', 'earningsQuarterlyGrowth', 'dividendYield'
    ]
    return {k: info.get(k) for k in fields}

def resolve_name_to_ticker(name: str, limit: int = 5) -> List[Dict[str, str]]:
    """Resolves a company name or free text to possible tickers using Yahoo Finance search API."""
    url = 'https://query1.finance.yahoo.com/v1/finance/search'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        r = requests.get(url, params={'q': name, 'quotesCount': limit, 'newsCount': 0}, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        quotes = data.get('quotes', [])
        return [
            {'symbol': q['symbol'], 'name': q.get('shortname') or q.get('longname'), 'exchange': q.get('exchDisp')}
            for q in quotes if q.get('symbol')
        ]
    except (requests.RequestException, KeyError, IndexError):
        return []

# #############################################################################
# INDICATOR HELPERS
# #############################################################################

def _get_last(series: pd.Series, default: Any = 0.0) -> Any:
    """Safely get the last value of a Series."""
    if series is not None and not series.empty:
        return series.iloc[-1]
    return default

def _calculate_basic_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """Calculates RSI, Stochastic, and Bollinger Bands."""
    out = {}
    close = df['Close']
    try:
        out['RSI'] = float(_get_last(pta.rsi(close, length=14)))
        
        bb = pta.bbands(close, length=20)
        if bb is not None and not bb.empty:
            out['BBL'] = float(_get_last(bb[bb.columns[0]])) # BBL_20_2.0
            out['BBM'] = float(_get_last(bb[bb.columns[1]])) # BBM_20_2.0
            out['BBU'] = float(_get_last(bb[bb.columns[2]])) # BBU_20_2.0

        stoch = pta.stoch(df['High'], df['Low'], df['Close'])
        if stoch is not None and not stoch.empty:
            out['STOCH_K'] = float(_get_last(stoch[stoch.columns[0]])) # STOCHk_14_3_3
            out['STOCH_D'] = float(_get_last(stoch[stoch.columns[1]])) # STOCHd_14_3_3
    except (IndexError, KeyError, TypeError) as e:
        print(f"Warning: Could not calculate basic indicators: {e}")
    return out

def _calculate_trend_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """Calculates Moving Averages, MACD, and ADX."""
    out = {}
    close = df['Close']
    try:
        out['SMA20'] = float(_get_last(pta.sma(close, length=20)))
        out['SMA50'] = float(_get_last(pta.sma(close, length=50)))
        out['EMA12'] = float(_get_last(pta.ema(close, length=12)))
        out['EMA26'] = float(_get_last(pta.ema(close, length=26)))

        macd = pta.macd(close, fast=12, slow=26)
        if macd is not None and not macd.empty:
            out['MACD'] = float(_get_last(macd[macd.columns[0]]))        # MACD_12_26_9
            out['MACD_SIGNAL'] = float(_get_last(macd[macd.columns[2]])) # MACDs_12_26_9

        adx = pta.adx(df['High'], df['Low'], df['Close'])
        if adx is not None and not adx.empty:
            out['ADX'] = float(_get_last(adx[adx.columns[0]]))     # ADX_14
            out['DI_PLUS'] = float(_get_last(adx[adx.columns[1]])) # DMP_14
            out['DI_MINUS'] = float(_get_last(adx[adx.columns[2]]))# DMN_14
    except (IndexError, KeyError, TypeError) as e:
        print(f"Warning: Could not calculate trend indicators: {e}")
    return out

def _detect_candlestick_patterns(df: pd.DataFrame) -> Dict[str, bool]:
    """Detects a few simple, recent candlestick patterns."""
    out = {
        'candlestick_hammer': False, 'candlestick_doji': False,
        'candlestick_bull_engulf': False, 'candlestick_bear_engulf': False
    }
    try:
        last = df.iloc[-1]
        o, h, l, c = last['Open'], last['High'], last['Low'], last['Close']
        body = abs(c - o)
        candle_range = h - l if h - l > 1e-9 else 1.0
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)

        out['candlestick_hammer'] = (lower_wick > 2 * body) and (upper_wick < body * 0.5)
        out['candlestick_doji'] = (body <= 0.1 * candle_range)

        if len(df) >= 2:
            prev = df.iloc[-2]
            o1, c1 = prev['Open'], prev['Close']
            body1 = abs(c1 - o1)
            # Bullish engulfing: prev bearish, current bullish, body engulfs
            out['candlestick_bull_engulf'] = (c > o) and (c1 < o1) and (body > body1) and (o < c1) and (c > o1)
            # Bearish engulfing
            out['candlestick_bear_engulf'] = (c < o) and (c1 > o1) and (body > body1) and (o > c1) and (c < o1)
    except (IndexError, KeyError):
        pass # Not enough data
    return out

def _classify_trend(indicators: Dict[str, Any]) -> str:
    """Classifies the trend based on SMAs and ADX."""
    try:
        sma20 = indicators.get('SMA20')
        sma50 = indicators.get('SMA50')
        if sma20 is None or sma50 is None:
            return 'Unknown'
        
        diff = sma20 - sma50
        pct_diff = diff / sma20 if sma20 != 0 else 0
        adx = indicators.get('ADX')
        is_strong = (adx is not None and adx >= 25)

        if pct_diff > 0.005:
            return 'Up (strong)' if is_strong else 'Up'
        elif pct_diff < -0.005:
            return 'Down (strong)' if is_strong else 'Down'
        else:
            return 'Sideways'
    except TypeError:
        return 'Unknown'

def detect_head_and_shoulders(close: pd.Series, lookback: int = 120, shoulder_tolerance: float = 0.05, head_margin: float = 0.03) -> Dict[str, Any]:
    """Heuristic detector for Head-and-Shoulders (H&S) and Inverse H&S patterns."""
    res = {'hs_found': False, 'hs_type': None, 'hs_confidence': 0.0, 'hs_positions': None}
    if close is None or len(close) < 30:
        return res
    
    s = close.dropna()
    arr = s.values[-lookback:]
    if len(arr) < 30:
        return res
        
    idx_offset = len(s) - len(arr)
    n = len(arr)

    # Find simple local peaks (strict greater than neighbors)
    peaks = [(i, arr[i]) for i in range(1, n - 1) if arr[i] > arr[i-1] and arr[i] > arr[i+1]]
    if len(peaks) < 3:
        return res

    # Examine last triple of peaks for a potential H&S
    for j in range(len(peaks) - 3, -1, -1):
        i1, p1 = peaks[j]
        i2, p2 = peaks[j+1]
        i3, p3 = peaks[j+2]

        if not (i1 < i2 < i3): continue

        shoulders_sim = abs(p1 - p3) / max(p1, p3, 1e-9)
        head_over_left = (p2 - p1) / p1 if p1 != 0 else 0
        head_over_right = (p2 - p3) / p3 if p3 != 0 else 0

        # Regular H&S: head higher than shoulders
        if p2 > p1 and p2 > p3 and shoulders_sim <= shoulder_tolerance and (head_over_left >= head_margin or head_over_right >= head_margin):
            conf = min(1.0, (head_over_left + head_over_right) / 0.2)
            res.update({'hs_found': True, 'hs_type': 'regular', 'hs_confidence': float(conf), 
                        'hs_positions': (int(i1 + idx_offset), int(i2 + idx_offset), int(i3 + idx_offset))})
            return res

        # Inverse H&S: head lower than shoulders
        if p2 < p1 and p2 < p3 and shoulders_sim <= shoulder_tolerance and ((p1 - p2)/p1 >= head_margin or (p3 - p2)/p3 >= head_margin):
            conf = 0.5 * min(1.0, ((p1 - p2)/p1 + (p3 - p2)/p3) / 0.1)
            res.update({'hs_found': True, 'hs_type': 'inverse', 'hs_confidence': float(conf),
                        'hs_positions': (int(i1 + idx_offset), int(i2 + idx_offset), int(i3 + idx_offset))})
            return res
            
    return res

# #############################################################################
# MAIN INDICATOR COMPUTATION
# #############################################################################

def compute_indicators(df: pd.DataFrame) -> dict:
    """
    Computes all technical indicators for the given DataFrame.
    This function orchestrates calls to smaller, specialized helper functions.
    """
    if df.empty:
        return {}

    # Calculate groups of indicators
    indicators = {}
    indicators.update(_calculate_basic_indicators(df))
    indicators.update(_calculate_trend_indicators(df))
    indicators.update(_detect_candlestick_patterns(df))
    
    # Classify trend based on calculated indicators
    indicators['trend'] = _classify_trend(indicators)
    
    # Detect complex patterns like Head and Shoulders
    indicators.update(detect_head_and_shoulders(df['Close']))

    # Add latest closing price for convenience
    indicators['Close'] = float(_get_last(df['Close']))

    return indicators