import streamlit as st
from app.finance import fetch_data, compute_indicators, fetch_fundamentals, resolve_name_to_ticker
from app.dsl_engine import DSLEngine
from app.db import init_db, save_analysis, get_history
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json

st.set_page_config(page_title='Traid - Analyseur', layout='wide')

# --- Caching Configuration ---

# Cache the DSL engine resource
@st.cache_resource
def get_engine():
    return DSLEngine('app/rules.dsl')

# Cache the data-fetching functions
# Note: decorators are applied to the imported functions
fetch_data = st.cache_data(fetch_data)
fetch_fundamentals = st.cache_data(fetch_fundamentals)
resolve_name_to_ticker = st.cache_data(resolve_name_to_ticker)
get_history = st.cache_data(get_history)


# --- App Initialization ---
init_db()
engine = get_engine()


def generate_advice(decision: str, triggered: list, indicators: dict) -> str:
    """Generates a user-friendly advice string based on the analysis results."""
    advice_parts = []
    
    # 1. Main decision statement
    if decision == 'BUY':
        advice_parts.append("📈 **Notre analyse suggère une opportunité d'achat.**")
    elif decision == 'SELL':
        advice_parts.append("📉 **Notre analyse suggère une opportunité de vente.**")
    else:
        advice_parts.append("⚖️ **Il est conseillé de conserver la position pour le moment.**")

    advice_parts.append("\n**Arguments clés :**")

    # 2. Explain triggered rules
    if not triggered:
        advice_parts.append("\n- Aucun signal technique majeur n'a été déclenché par vos règles.")
    else:
        for rule in triggered:
            comment = rule.get('comment', '').lower()
            expr = rule.get('expr', '')
            # Create more descriptive reasons
            if 'oversold' in comment or 'rsi <' in expr.lower():
                advice_parts.append(f"\n- Le RSI ({indicators.get('RSI', 0):.1f}) est en zone de survente, ce qui peut indiquer un rebond.")
            elif 'overbought' in comment or 'rsi >' in expr.lower():
                advice_parts.append(f"\n- Le RSI ({indicators.get('RSI', 0):.1f}) est en zone de surachat, signalant un risque de correction.")
            elif 'sma20 > sma50' in expr.lower():
                advice_parts.append("\n- La moyenne mobile à 20 jours est au-dessus de celle à 50 jours, confirmant une tendance haussière.")
            elif 'sma20 < sma50' in expr.lower():
                advice_parts.append("\n- La moyenne mobile à 20 jours est passée sous celle à 50 jours, un signal de tendance baissière.")
            elif 'close < bbl' in expr.lower():
                advice_parts.append("\n- Le prix a touché la bande de Bollinger inférieure, une zone de support potentielle.")
            elif 'close > bbu' in expr.lower():
                advice_parts.append("\n- Le prix a dépassé la bande de Bollinger supérieure, indiquant une forte volatilité.")
            elif 'macd' in expr.lower():
                 advice_parts.append(f"\n- Le MACD montre un momentum qui appuie la décision (Règle: `{expr}`).")
            else:
                advice_parts.append(f"\n- Signal déclenché par la règle : `{expr}` ({comment}).")

    # 3. Add contextual advice from other indicators
    advice_parts.append("\n\n**Contexte général du marché pour cet actif :**")
    
    trend = indicators.get('trend', 'Unknown')
    if trend != 'Unknown':
        advice_parts.append(f"\n- La tendance de fond est actuellement classée comme **{trend}**.")

    if indicators.get('hs_found'):
        hs_type = indicators.get('hs_type', 'standard')
        conf = indicators.get('hs_confidence', 0)
        msg = f"\n- **Attention :** Une figure chartiste **Épaule-Tête-Épaule ({hs_type})** a été détectée avec une confiance de {conf:.0%}. C'est un signal de retournement important."
        advice_parts.append(msg)

    if indicators.get('candlestick_bull_engulf'):
        advice_parts.append("\n- Une figure 'Englobante Haussière' a été détectée, renforçant les perspectives de hausse.")
    if indicators.get('candlestick_bear_engulf'):
        advice_parts.append("\n- Une figure 'Englobante Baissière' a été détectée, un signal de faiblesse à court terme.")

    # 4. Disclaimer
    advice_parts.append("\n\n---\n*Ces informations sont générées automatiquement à titre indicatif et ne constituent pas un conseil en investissement.*")

    return "\n".join(advice_parts)


# Light CSS for a cleaner, modern look
st.markdown(
    """
    <style>
    .header {font-family: 'Segoe UI', Roboto, sans-serif;}
    .card {background:#f8f9fb; padding:12px; border-radius:8px;}
    .decision-buy{background:#e6f4ea;padding:10px;border-radius:8px;color:#06632a}
    .decision-sell{background:#fdecea;padding:10px;border-radius:8px;color:#8a1f11}
    .decision-hold{background:#eef3ff;padding:10px;border-radius:8px;color:#1f3a93}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("<h1 class='header'>Traid — Analyse automatique (yfinance)</h1>", unsafe_allow_html=True)

with st.sidebar:
    st.header('Paramètres')
    refresh = st.slider('Fréquence de rafraîchissement (sec)', min_value=5, max_value=3600, value=60)
    period = st.selectbox('Période historique', ['7d','30d','60d','180d','1y','2y'], index=2)
    interval = st.selectbox('Interval', ['1m','2m','5m','15m','1d'], index=4)
    st.markdown('---')
    st.markdown('Entrez un nom d\'entreprise (ex: TotalEnergies) ou un ticker (ex: TTE.PA)')

# Instead of free text, provide a dropdown of the 5 French companies requested
companies = [
    ("Hermès", "RMS.PA"),
    ("TotalEnergies", "TTE.PA"),
    ("Airbus", "AIR.PA"),
    ("Sopra Steria", "SOP.PA"),
    ("Dassault Systèmes", "DSY.PA"),
]

choice = st.selectbox('Choisir une entreprise française', [c[0] for c in companies])
symbol = dict(companies)[choice]
st.info(f"Symbole sélectionné: {symbol}")

if st.button('Analyser'):
    try:
        if not symbol:
            st.error('Aucun symbole résolu à analyser')
            st.stop()
        df = fetch_data(symbol, period=period, interval=interval)
    except Exception as e:
        st.error(f'Erreur récupération: {e}')
        st.stop()

    # Prepare indicators and series
    indicators = compute_indicators(df)
    indicators['Close'] = float(df['Close'].iloc[-1])

    # Add series for plotting (moving averages, bollinger)
    df_plot = df.copy()
    df_plot['SMA20'] = df_plot['Close'].rolling(20).mean()
    df_plot['SMA50'] = df_plot['Close'].rolling(50).mean()
    ma = df_plot['Close'].rolling(20).mean()
    sd = df_plot['Close'].rolling(20).std()
    df_plot['BBU'] = ma + 2 * sd
    df_plot['BBL'] = ma - 2 * sd

    fundamentals = fetch_fundamentals(symbol)

    # Evaluate rules
    result = engine.evaluate(indicators, fundamentals)

    # Layout: left metrics, thin divider, right chart
    col1, col_div, col2 = st.columns([1, 0.02, 2])
    # draw a thin vertical divider in the small middle column
    try:
        col_div.markdown("<div style='height:100%;border-left:1px solid #e6e6e6;margin:0 8px;'></div>", unsafe_allow_html=True)
    except Exception:
        # fallback: simple empty spacer
        col_div.write('')

    with col1:
        st.markdown(f"**{symbol}** — Dernier cours: {indicators['Close']:.2f}")
        # show metrics
        price = indicators['Close']
        prev = float(df['Close'].iloc[-2]) if len(df) >= 2 else price
        change = price - prev
        st.metric(label='Prix', value=f"{price:.2f} EUR", delta=f"{change:.2f}")
        st.metric(label='RSI', value=f"{indicators.get('RSI',0):.1f}")
        st.metric(label='MACD', value=f"{indicators.get('MACD',0):.3f}")
        # Rendements
        try:
            r1d = indicators.get('return_1d_pct', 0.0)
            rp = indicators.get('return_period_pct', 0.0)
            st.metric(label='Rendement 1j', value=f"{r1d:.2f}%", delta=f"{r1d:.2f}%")
            st.metric(label='Rendement période', value=f"{rp:.2f}%", delta=f"{rp:.2f}%")
        except Exception:
            pass

        # Decision badge
        dec = result['decision']
        if dec == 'BUY':
            st.markdown("<div class='decision-buy'><b>RECOMMANDATION: ACHETER</b></div>", unsafe_allow_html=True)
        elif dec == 'SELL':
            st.markdown("<div class='decision-sell'><b>RECOMMANDATION: VENDRE</b></div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='decision-hold'><b>RECOMMANDATION: NE RIEN FAIRE</b></div>", unsafe_allow_html=True)

        st.markdown('**Analyse et conseils :**')
        advice = generate_advice(result['decision'], result['triggered'], indicators)
        st.markdown(advice)

        # Technical signals
        st.markdown('---')
        st.markdown('**Signaux techniques**')
        adx = indicators.get('ADX')
        di_plus = indicators.get('DI_PLUS')
        di_minus = indicators.get('DI_MINUS')
        st.write(f"ADX: {adx:.2f}" if adx is not None else "ADX: N/A")
        if di_plus is not None and di_minus is not None:
            st.write(f"+DI: {di_plus:.2f}  |  -DI: {di_minus:.2f}")
        st.write(f"Tendance: {indicators.get('trend','Unknown')}")

        # Candlestick signals
        cs = []
        if indicators.get('candlestick_hammer'):
            cs.append('🔔 Hammer')
        if indicators.get('candlestick_bull_engulf'):
            cs.append('📈 Bull Engulfing')
        if indicators.get('candlestick_bear_engulf'):
            cs.append('📉 Bear Engulfing')
        if indicators.get('candlestick_doji'):
            cs.append('⚪ Doji')
        if cs:
            st.write(' / '.join(cs))
        else:
            st.write('Aucune figure chandelier notable')

        # Head and Shoulders
        hs = indicators.get('head_and_shoulders')
        if hs:
            score = indicators.get('head_and_shoulders_score', 0.0)
            st.warning(f"Pattern Head & Shoulders détecté (score: {score:.2f})")
        else:
            st.write('H&S: aucun')
        # Head & Shoulders
        hs_found = indicators.get('hs_found')
        if hs_found:
            hs_type = indicators.get('hs_type') or 'regular'
            conf = indicators.get('hs_confidence', 0.0)
            pos = indicators.get('hs_positions')
            st.markdown(f"**Figure détectée :** {hs_type} (confiance {conf:.2f})")
            if pos:
                st.write(f"Positions (indices): {pos}")
        else:
            st.write('Pas de figure Épaule‑Tête‑Épaule détectée')

        st.markdown('---')
        st.markdown('**Données fondamentales**')
        if fundamentals:
            def humanize_number(x):
                try:
                    n = float(x)
                except Exception:
                    return str(x)
                absn = abs(n)
                if absn >= 1e12:
                    return f"{n/1e12:.2f}T"
                if absn >= 1e9:
                    return f"{n/1e9:.2f}B"
                if absn >= 1e6:
                    return f"{n/1e6:.2f}M"
                if absn >= 1e3:
                    return f"{n/1e3:.0f}k"
                return f"{n:g}"

            def fmt_float(x, digits=2):
                try:
                    return f"{float(x):.{digits}f}"
                except Exception:
                    return 'N/A'

            # Top-row metrics
            mcap = humanize_number(fundamentals.get('marketCap'))
            fpe = fmt_float(fundamentals.get('forwardPE'))
            tpe = fmt_float(fundamentals.get('trailingPE'))
            col_a, col_b, col_c = st.columns(3)
            col_a.metric('Market Cap', mcap)
            col_b.metric('Forward P/E', fpe)
            col_c.metric('Trailing P/E', tpe)

            # Second row
            dte = fmt_float(fundamentals.get('debtToEquity'))
            td = humanize_number(fundamentals.get('totalDebt'))
            ebitda = humanize_number(fundamentals.get('ebitda'))
            c1, c2, c3 = st.columns(3)
            c1.metric('Debt / Equity', dte)
            c2.metric('Total Debt', td)
            c3.metric('EBITDA', ebitda)

            # Show other interesting fields in a compact table
            keys_to_show = ['priceToBook', 'earningsQuarterlyGrowth', 'dividendYield']
            extra = {k: fundamentals.get(k) for k in keys_to_show if fundamentals.get(k) is not None}
            if extra:
                st.write('Autres métriques')
                st.table({k: (humanize_number(v) if isinstance(v, (int,float)) else v) for k,v in extra.items()})

            # Keep raw JSON available for debugging
            with st.expander('Voir JSON brut'):
                st.json(fundamentals)
        else:
            st.write('Aucune donnée fondamentale trouvée')

    with col2:
        st.subheader(f'Graphique {symbol}')
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['Close'], mode='lines', name='Close', line={'color': '#111111'}))
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['SMA20'], mode='lines', name='SMA20', line={'color': '#1f77b4'}))
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['SMA50'], mode='lines', name='SMA50', line={'color': '#ff7f0e'}))
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['BBU'], mode='lines', name='BBU', line={'color': 'rgba(31,119,180,0.3)'}))
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['BBL'], mode='lines', name='BBL', line={'color': 'rgba(31,119,180,0.3)'}))
        fig.update_layout(margin={'l': 20, 'r': 20, 't': 30, 'b': 20}, height=500)
        st.plotly_chart(fig, use_container_width=True)

    # Save analysis
    save_analysis(symbol, result['decision'], result['reason'], indicators, fundamentals)

    # History
    st.markdown('---')
    st.subheader('Historique des analyses')
    hist = get_history(200)
    if hist:
        df_hist = pd.DataFrame(hist)
        st.dataframe(df_hist)
    else:
        st.write('Aucune analyse enregistrée')
