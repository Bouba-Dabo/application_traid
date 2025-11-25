from app.finance import fetch_data, compute_indicators
import pandas as pd
from plotly.subplots import make_subplots
import plotly.graph_objects as go

if __name__ == '__main__':
    df = fetch_data('DSY.PA', period='60d', interval='1d')
    ind = compute_indicators(df)
    df_plot = df.copy()
    df_plot['SMA20'] = df_plot['Close'].rolling(20).mean()
    df_plot['SMA50'] = df_plot['Close'].rolling(50).mean()
    ma = df_plot['Close'].rolling(20).mean()
    sd = df_plot['Close'].rolling(20).std()
    df_plot['BBU'] = ma + 2 * sd
    df_plot['BBL'] = ma - 2 * sd
    df_plot['returns_cum'] = (df_plot['Close'].pct_change().fillna(0) + 1.0).cumprod() - 1.0

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7,0.3])
    fig.add_trace(go.Candlestick(x=df_plot.index, open=df_plot['Open'], high=df_plot['High'], low=df_plot['Low'], close=df_plot['Close'], name='OHLC'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['SMA20'], mode='lines', name='SMA20'), row=1, col=1)
    fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['Volume'], name='Volume'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['returns_cum']*100.0, mode='lines', name='Cumulative Return %'), row=2, col=1)
    fig.update_layout(height=600)
    fig.write_html('tests/plot_smoke.html')
    print('WROTE tests/plot_smoke.html')
