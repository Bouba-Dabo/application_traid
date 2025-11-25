from app.finance import fetch_data, compute_indicators, fetch_fundamentals
from app.db import init_db, save_analysis

if __name__ == '__main__':
    df = fetch_data('AAPL', period='30d', interval='1d')
    ind = compute_indicators(df)
    f = fetch_fundamentals('AAPL')
    init_db()
    save_analysis('AAPL', 'TEST', 'smoke', ind, f)
    print('SMOKE_OK')
