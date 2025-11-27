import streamlit as st
import os
import sys
# Ensure repository root is on sys.path so `import app.*` works in hosted environments
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.finance import fetch_data, compute_indicators, fetch_fundamentals, resolve_name_to_ticker
from app.news import fetch_feed
import urllib.parse
from app.dsl_engine import DSLEngine
from app.db import init_db, save_analysis, get_history
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit.components.v1 as components

# Thresholds for RSI interpretation (can be tuned)
RSI_OVERSOLD = 30.0
RSI_CAUTION = 60.0
RSI_OVERBOUGHT = 70.0

st.set_page_config(page_title='Traid - Analyseur', layout='wide')

# Landing control: show a cover page before accessing the analysis
if 'show_landing' not in st.session_state:
    st.session_state.show_landing = True

# Small sidebar control to force-show the landing page (useful during development)
try:
    if st.sidebar.button("Afficher la page d'accueil"):
        st.session_state.show_landing = True
        try:
            from streamlit.runtime.scriptrunner.script_runner import RerunException
            raise RerunException()
        except Exception:
            try:
                st.experimental_rerun()
            except Exception:
                pass
except Exception:
    # If sidebar isn't ready yet for some Streamlit builds, ignore silently
    pass

@st.cache_resource
def get_engine():
    return DSLEngine('app/rules.dsl')

# cache helpers
fetch_data = st.cache_data(fetch_data)
fetch_fundamentals = st.cache_data(fetch_fundamentals)
resolve_name_to_ticker = st.cache_data(resolve_name_to_ticker)
get_history = st.cache_data(get_history)

init_db()
engine = get_engine()

# --- Helpers for scoring & UI ---
def _clamp_score(v: int) -> int:
    try:
        iv = int(v)
    except Exception:
        iv = 0
    if iv < 0:
        return 0
    if iv > 5:
        return 5
    return iv


def compute_indicator_scores(indicators: dict) -> dict:
    out = {}
    try:
        macd = indicators.get('MACD')
        macd_s = indicators.get('MACD_SIGNAL')
        if macd is None or macd_s is None:
            out['MACD'] = 3
        else:
            diff = float(macd) - float(macd_s)
            if diff > 0 and float(macd) > 0:
                out['MACD'] = 5
            elif diff > 0:
                out['MACD'] = 4
            elif abs(diff) < 1e-8:
                out['MACD'] = 3
            elif diff < 0 and float(macd) < 0:
                out['MACD'] = 1
            else:
                out['MACD'] = 2
    except Exception:
        out['MACD'] = 3

    try:
        trend = str(indicators.get('trend', 'Sideways'))
        if 'Up (strong)' in trend:
            out['TREND'] = 5
        elif 'Up' in trend:
            out['TREND'] = 4
        elif 'Sideways' in trend or 'Unknown' in trend:
            out['TREND'] = 3
        elif 'Down' in trend and 'strong' in trend:
            out['TREND'] = 1
        elif 'Down' in trend:
            out['TREND'] = 2
        else:
            out['TREND'] = 3
    except Exception:
        out['TREND'] = 3

    try:
        if indicators.get('hs_found'):
            conf = float(indicators.get('hs_confidence', 0.0))
            if conf >= 0.7:
                out['HNS'] = 1
            elif conf >= 0.4:
                out['HNS'] = 2
            else:
                out['HNS'] = 3
        else:
            out['HNS'] = 5
    except Exception:
        out['HNS'] = 5

    return out

def _score_color(score: int) -> str:
    s = _clamp_score(score)
    cmap = {
        0: '#7f1d1d',
        1: '#ef4444',
        2: '#f59e0b',
        3: '#fbbf24',
        4: '#06b6d4',
        5: '#10b981'
    }
    return cmap.get(s, '#64748b')

def render_score_card(col, label: str, score: int):
    color = _score_color(score)
    text = _score_label(score)
    html = f"""
    <div class='score-card' style='padding:8px;border-radius:10px;margin-bottom:8px'>
      <div style='display:flex;justify-content:space-between;align-items:center'>
        <div style='font-size:13px;color:#6b7280'>{label}</div>
        <div style='background:{color};color:#fff;padding:6px 10px;border-radius:14px;font-weight:700'>{score}/5 — {text}</div>
      </div>
    </div>
    """
    col.markdown(html, unsafe_allow_html=True)

def generate_advice(decision: str, triggered: list, indicators: dict, fundamentals: dict | None = None) -> str:
    advice_parts = []
    if decision == 'BUY':
        advice_parts.append("📈 **Notre analyse suggère une opportunité d'achat.**")
    elif decision == 'SELL':
        advice_parts.append("📉 **Notre analyse suggère une opportunité de vente.**")
    else:
        advice_parts.append("⚖️ **Il est conseillé de conserver la position pour le moment.**")
    advice_parts.append("\n**Arguments clés :**")
    # Evaluate triggered rules and relate them to current RSI using thresholds
    try:
        rsi_val = None
        try:
            rsi_val = float(indicators.get('RSI', 0.0))
        except Exception:
            rsi_val = None

        if not triggered:
            advice_parts.append("\n- Aucun signal technique majeur n'a été déclenché par vos règles.")
        else:
            for rule in triggered:
                comment = (rule.get('comment', '') or '').lower()
                expr = (rule.get('expr', '') or '')
                expr_l = expr.lower()
                # Handle RSI-related rules more precisely using thresholds
                if 'rsi <' in expr_l or 'oversold' in comment:
                    if rsi_val is not None and rsi_val <= RSI_OVERSOLD:
                        advice_parts.append(f"\n- Le RSI ({rsi_val:.1f}) est en zone de survente (≤{RSI_OVERSOLD:.0f}), ce qui peut indiquer un rebond.")
                    else:
                        cur = f"{rsi_val:.1f}" if rsi_val is not None else 'N/A'
                        advice_parts.append(f"\n- Règle déclenchée : `{expr}` — RSI actuel = {cur} (pas strictement en survente).")
                elif 'rsi >' in expr_l or 'overbought' in comment:
                    if rsi_val is not None and rsi_val >= RSI_OVERBOUGHT:
                        advice_parts.append(f"\n- Le RSI ({rsi_val:.1f}) est en zone de surachat (≥{RSI_OVERBOUGHT:.0f}), signalant un risque de correction.")
                    elif rsi_val is not None and rsi_val >= RSI_CAUTION:
                        advice_parts.append(f"\n- Le RSI ({rsi_val:.1f}) est modérément élevé ({RSI_CAUTION:.0f}–{RSI_OVERBOUGHT:.0f}) — prudence requise.")
                    else:
                        cur = f"{rsi_val:.1f}" if rsi_val is not None else 'N/A'
                        advice_parts.append(f"\n- Règle déclenchée : `{expr}` — RSI actuel = {cur}.")
                elif 'sma20 > sma50' in expr_l or ('sma20' in expr_l and 'sma50' in expr_l):
                    advice_parts.append("\n- La moyenne mobile à 20 jours est au-dessus de celle à 50 jours, confirmant une tendance haussière.")
                else:
                    advice_parts.append(f"\n- Signal déclenché par la règle : `{expr}` ({comment}).")
    except Exception:
        advice_parts.append("\n- Erreur lors de l'analyse des règles déclenchées.")
    # Contre-arguments / points de vigilance (indicateurs contraires)
    contra = []
    try:
        rsi_val = float(indicators.get('RSI', 0.0))
        if rsi_val >= 65:
            contra.append(f"Le RSI est élevé ({rsi_val:.1f}), signe d'une zone potentielle de sur-achat à court terme.")
    except Exception:
        pass
    try:
        macd = indicators.get('MACD')
        macd_s = indicators.get('MACD_SIGNAL')
        if macd is not None and macd_s is not None and float(macd) < float(macd_s):
            contra.append("Le momentum (MACD) est orienté à la baisse.")
    except Exception:
        pass
    try:
        sma20 = indicators.get('SMA20')
        sma50 = indicators.get('SMA50')
        if sma20 is not None and sma50 is not None and float(sma20) < float(sma50):
            contra.append("La SMA20 est en dessous de la SMA50, ce qui est un signal technique baissier." )
    except Exception:
        pass
    if contra:
        advice_parts.append("\n**Points de vigilance :**")
        for c in contra:
            advice_parts.append(f"\n- {c}")

    # Volatilité (Bandes de Bollinger width)
    try:
        bw = indicators.get('BB_WIDTH_PCT')
        if bw is not None:
            if bw > 0.06:
                advice_parts.append("\n- La volatilité est élevée (Bandes de Bollinger larges). Attendez-vous à des mouvements de prix amples.")
            elif bw < 0.03:
                advice_parts.append("\n- La volatilité est faible (Bandes de Bollinger étroites) — phase de consolidation probable.")
    except Exception:
        pass

    # Contexte fondamental
    if fundamentals:
        try:
            pe = fundamentals.get('trailingPE') or fundamentals.get('forwardPE') or fundamentals.get('pe')
            if pe is not None:
                try:
                    pef = float(pe)
                    if decision == 'BUY' and pef > 0:
                        if pef <= 15:
                            advice_parts.append(f"\n**Contexte fondamental :** Le PER est de {pef:.1f}, ce qui peut indiquer une valorisation raisonnable et renforce le signal technique.")
                        elif pef >= 30:
                            advice_parts.append(f"\n**Contexte fondamental :** Le PER est élevé ({pef:.1f}), ce qui invite à la prudence malgré le signal technique.")
                        else:
                            advice_parts.append(f"\n**Contexte fondamental :** PER = {pef:.1f}. Aucune anomalie manifeste dans la valorisation.")
                    elif decision == 'SELL' and pef > 0:
                        advice_parts.append(f"\n**Contexte fondamental :** PER = {pef:.1f}. Considérez le contexte de valorisation dans votre décision.")
                except Exception:
                    pass
        except Exception:
            pass

    advice_parts.append("\n\n---\n*Ces informations sont générées automatiquement à titre indicatif et ne constituent pas un conseil en investissement.*")
    # Add a short ranked list of most influential triggered rules (by absolute score)
    try:
        if triggered:
            sorted_tr = sorted(triggered, key=lambda x: abs(int(x.get('score', 0))), reverse=True)
            advice_parts.append("\n**Paramètres les plus influents :**")
            for t in sorted_tr[:5]:
                sc = int(t.get('score', 0))
                expr = t.get('expr', '')
                comment = t.get('comment', '')
                sign = '+' if sc >= 0 else ''
                advice_parts.append(f"\n- {sign}{sc}: `{expr}` {f'— {comment}' if comment else ''}")
        else:
            # Even if no rules triggered, show key raw indicators that may still matter
            key_params = []
            try:
                rsi_val = float(indicators.get('RSI', 0.0))
                key_params.append(f"RSI = {rsi_val:.1f}")
            except Exception:
                pass
            try:
                macd = indicators.get('MACD')
                macd_s = indicators.get('MACD_SIGNAL')
                if macd is not None and macd_s is not None:
                    diff = float(macd) - float(macd_s)
                    key_params.append(f"MACD diff = {diff:.3f}")
            except Exception:
                pass
            try:
                sma20 = indicators.get('SMA20')
                sma50 = indicators.get('SMA50')
                if sma20 is not None and sma50 is not None:
                    key_params.append(f"SMA20/SMA50 = {float(sma20):.2f}/{float(sma50):.2f}")
            except Exception:
                pass
            if key_params:
                advice_parts.append("\n**Paramètres clés :**")
                for kp in key_params:
                    advice_parts.append(f"\n- {kp}")
    except Exception:
        pass

    return "\n".join(advice_parts)

# --- CSS: improved hero styling and high-contrast light theme ---
st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;800&display=swap');
        :root{--bg:#f8fafc; --card:#ffffff; --muted:#6b7280; --accent:#6b8cff; --accent-2:#7de1d1; --success:#0f9d58; --danger:#d9230f}
        html, body {background:var(--bg); color:#0f1724; font-family: 'Poppins', Inter, 'Segoe UI', Roboto, sans-serif}
        .header {font-weight:800; color:#0f1724}
        .card{background:var(--card); padding:14px; border-radius:10px; box-shadow:0 6px 28px rgba(12,18,30,0.06); border:1px solid rgba(12,18,30,0.04)}
        .score-card{background:var(--card); padding:8px;border-radius:10px;margin-bottom:6px;border:1px solid rgba(0,0,0,0.03)}
        .header-sub{color:#0f1724;font-weight:700}
        select, input, textarea, button {background: #fff !important; color: #0f1724 !important; border:1px solid rgba(0,0,0,0.06) !important}

        /* Hero styles (polished) */
        .hero{background-position:center; background-size:cover; background-repeat:no-repeat; min-height:480px; display:flex; align-items:center; border-radius:14px; margin-bottom:32px; position:relative; overflow:hidden}
        .hero::before{ content: ''; position:absolute; inset:0; background: linear-gradient(90deg, rgba(4,9,30,0.55) 0%, rgba(10,20,40,0.28) 40%, rgba(255,255,255,0.02) 100%); mix-blend-mode: multiply }
        .hero-inner{position:relative; z-index:2; max-width:1100px; margin:0 auto; display:flex; flex-direction:column; gap:18px; padding:30px; border-radius:12px; backdrop-filter: blur(6px); background: linear-gradient(180deg, rgba(255,255,255,0.88), rgba(255,255,255,0.80));}
        .hero-title{display:flex;align-items:center;gap:20px}
        .logo{background:linear-gradient(135deg,var(--accent),var(--accent-2)); color:white; width:88px;height:88px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:36px;box-shadow:0 10px 30px rgba(6,30,60,0.08)}
        .app-name{font-size:36px;font-weight:800;color:transparent;background:linear-gradient(90deg,#3A8BFF,#06B6D4);-webkit-background-clip:text;background-clip:text;letter-spacing:-0.5px}
        .mini-slogan{font-size:12px;color:#64748b;margin-top:4px;font-weight:600}
        .app-tag{font-size:15px;color:#334155;margin-top:6px;font-weight:600}
        .hero-desc{color:#334155;margin:0;max-width:980px;font-size:15px;line-height:1.6}
        .schools{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:14px}
        .badge{background:linear-gradient(90deg,#eef2ff,#f0f9ff);padding:6px 10px;border-radius:999px;color:#1e40af;margin-left:6px;font-weight:700;border:1px solid rgba(30,64,175,0.08)}

        .team{margin-top:6px}
        .members{display:flex;gap:16px;flex-wrap:wrap}
        .member{display:flex;flex-direction:column;align-items:center;width:90px}
        .avatar{background:linear-gradient(135deg,#3A8BFF,#7C3AED);width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:18px;box-shadow:0 8px 18px rgba(3,12,30,0.12);transition:transform .18s ease}
        .avatar:hover{transform:translateY(-4px)}
        .name{font-size:13px;color:#0f1724;margin-top:8px;text-align:center}

        .cta-wrap{display:flex;justify-content:center;margin-top:14px}
        .cta-button{background:linear-gradient(90deg,var(--accent),var(--accent-2)); color:white;padding:14px 26px;border-radius:14px;font-weight:800;border:none;box-shadow:0 12px 34px rgba(6,30,60,0.14); cursor:pointer; font-size:16px}
        .cta-button:hover{transform:translateY(-3px);transition:all .18s ease}

        /* Small visual tweaks for Streamlit default button below hero */
        .stButton>button{border-radius:12px;padding:14px 22px;background:linear-gradient(90deg,#3A8BFF,#06B6D4);color:#fff;font-weight:700;border:none;box-shadow:0 10px 30px rgba(58,139,255,0.18)}
        .stButton>button::after{content:' →';margin-left:8px}

        /* Features */
        .features{display:flex;gap:12px;margin-top:14px}
        .feature{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,0.9);padding:8px 12px;border-radius:10px;box-shadow:0 6px 18px rgba(3,12,30,0.04);font-weight:600;color:#0f1724}
        .f-icon{background:linear-gradient(90deg,#3A8BFF,#7C3AED);width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800}

        /* Illustration on the right */
        .hero-top{display:flex;gap:22px;align-items:stretch}
        .hero-left{flex:1}
        .hero-illustration{width:42%; background-size:cover;background-position:right center;border-radius:12px;filter:blur(2px) saturate(1.05);opacity:0.95}

        /* Animations */
        @keyframes fadeInUp { from {opacity:0; transform:translateY(18px);} to {opacity:1; transform:none;} }
        @keyframes popIn { from {opacity:0; transform:scale(.98);} to {opacity:1; transform:scale(1);} }
        .animate-up{animation:fadeInUp .7s ease both}
        .logo, .member, .feature{animation:popIn .6s ease both}

        /* Responsive */
        @media (max-width: 900px){
            .hero-inner{padding:18px}
            .app-name{font-size:24px}
            .logo{width:56px;height:56px;font-size:20px}
            .members{gap:10px}
            .hero{min-height:380px}
            .hero-illustration{display:none}
            .features{flex-direction:column}
        }

        </style>
        """,
        unsafe_allow_html=True,
)

if st.session_state.get('show_landing', True):
    # Try to load a local image from the app folder and inline it as base64 for the hero background
    try:
        import base64
        img_path = os.path.join(os.path.dirname(__file__), "Analyse financière dans un monde futuriste.png")
        with open(img_path, 'rb') as _f:
            _b = _f.read()
        _b64 = base64.b64encode(_b).decode('ascii')
        bg_url = f"data:image/png;base64,{_b64}"
    except Exception:
        bg_url = "https://images.unsplash.com/photo-1559526324-593bc073d938?auto=format&fit=crop&w=1650&q=80"

    import textwrap

    hero_html = textwrap.dedent(f"""
    <style>
    .hero{{background-position:center;background-size:cover;background-repeat:no-repeat;display:flex;align-items:center;position:relative;overflow:hidden;left:50%;right:50%;margin-left:-50vw;margin-right:-50vw;width:100vw;height:100vh}}
    .hero-inner{{position:relative;z-index:2;max-width:1100px;margin:0 auto;display:flex;flex-direction:column;gap:18px;padding:30px;border-radius:12px;backdrop-filter:blur(4px);background:linear-gradient(180deg, rgba(0,0,0,0.45), rgba(0,0,0,0.32));color:#e6eef8}}
    .hero-title{{display:flex;align-items:center;gap:20px}}
    .logo{{background:linear-gradient(135deg,#3A8BFF,#06B6D4);color:white;width:72px;height:72px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:28px}}
    .app-name{{font-size:32px;font-weight:800;color:#fff}}
    .mini-slogan{{font-size:13px;color:#cbd5e1;margin-top:4px;font-weight:600}}
    .app-tag{{font-size:14px;color:#cbd5e1;margin-top:6px}}
    .features{{display:flex;gap:12px;margin-top:14px}}
    .feature{{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,0.03);padding:8px 12px;border-radius:10px;font-weight:600;color:#e6eef8}}
    .f-icon{{background:linear-gradient(90deg,#3A8BFF,#7C3AED);width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800}}
    .team{{margin-top:6px}}
    .members{{display:flex;gap:16px;flex-wrap:wrap}}
    .member{{display:flex;flex-direction:column;align-items:center;width:110px}}
    .avatar{{background:linear-gradient(135deg,#3A8BFF,#7C3AED);width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:18px}}
    .name{{font-size:13px;color:#e6eef8;margin-top:8px;text-align:center}}
    </style>

    <section class='hero' style="background-image:url('{bg_url}');">
        <div class='hero-inner animate-up'>
            <div class='hero-top'>
                <div class='hero-left'>
                    <div class='hero-title'>
                        <span class='logo'>T</span>
                        <div>
                            <div style='display:flex;align-items:center;gap:12px'>
                                <div class='app-name'>Traid_analyzer</div>
                                <div class='schools'>
                                    <div style='font-size:14px;color:var(--muted);font-weight:700'>ESIGELEC</div>
                                    <div class='badge'>Projet d'analyse financière</div>
                                </div>
                            </div>
                            <div class='mini-slogan'>Décisions éclairées par l'IA — Analyse financière instantanée.</div>
                            <div class='app-tag'>L’outil développé par l’équipe ESIGELEC pour analyser les marchés et prendre des décisions plus informées.</div>
                        </div>
                    </div>

                    <div class='features' aria-hidden='true'>
                        <div class='feature'><div class='f-icon'>📈</div><div>Analyse technique automatisée</div></div>
                        <div class='feature'><div class='f-icon'>⚖️</div><div>Analyse fondamentale instantanée</div></div>
                        <div class='feature'><div class='f-icon'>💡</div><div>Recommandations d'achat/vente</div></div>
                    </div>

                    <div style='margin-top:14px'>
                        <strong style='font-size:15px'>Bienvenue — Entrer dans l'analyse</strong>
                        <div style='color:var(--muted);margin-top:6px'>Découvrez des signaux clairs, un scoring d'indicateurs et des conseils actionnables.</div>
                    </div>
                </div>
                <div class='hero-illustration' style="background-image:url('{bg_url}');"></div>
            </div>

            <div class='team' style='margin-top:18px;'>
                <strong>Équipe :</strong>
                <div class='members'>
                    <div class='member'><div class='avatar'>BD</div><div class='name'>Boubacar Dabo </div></div>
                    <div class='member'><div class='avatar'>MA</div><div class='name'>Malo </div></div>
                    <div class='member'><div class='avatar'>BA</div><div class='name'>Baptiste </div></div>
                    <div class='member'><div class='avatar'>KE</div><div class='name'>Kevyn </div></div>
                    <div class='member'><div class='avatar'>YX</div><div class='name'>YuXuan </div></div>
                </div>
            </div>
        </div>
    </section>
    """)
    # Remove any leading newlines/spaces that can make Markdown treat this block as a code fence
    hero_html = hero_html.lstrip()
    # Also strip common leading indentation on every line to avoid accidental code-block formatting
    hero_html = "\n".join([ln.lstrip() for ln in hero_html.splitlines()])

    # Render the hero using a Streamlit component (iframe) so the HTML/CSS isn't escaped
    # and the visual full-bleed styling is preserved.
    try:
        components.html(hero_html, height=760, scrolling=True)
    except Exception:
        # Fallback to markdown if components isn't available for some reason
        st.markdown(hero_html, unsafe_allow_html=True)

    

    cols = st.columns([1, 0.5, 1])
    with cols[1]:
        if st.button("Accéder à l'analyse", key='enter_app'):
            st.session_state.show_landing = False
            # Attempt to rerun the Streamlit script. Use st.experimental_rerun when available,
            # otherwise raise the internal RerunException as a fallback for older/newer Streamlit builds.
            try:
                # Prefer raising the internal RerunException to force a rerun in many Streamlit versions.
                from streamlit.runtime.scriptrunner.script_runner import RerunException
                raise RerunException()
            except Exception:
                # Fallback: try the public API if available
                try:
                    st.experimental_rerun()
                except Exception:
                    # Last resort: no-op; UI will update on next interaction
                    pass

    st.stop()

with st.sidebar:
    st.header('Paramètres')
    refresh = st.slider('Fréquence de rafraîchissement (sec)', min_value=5, max_value=3600, value=60)
    period = st.selectbox('Période historique', ['7d','30d','60d','180d','1y','2y'], index=2)
    interval = st.selectbox('Interval', ['1m','2m','5m','15m','1d'], index=4)
    st.markdown('---')
    st.markdown('Entrez un nom d\'entreprise (ex: TotalEnergies) ou un ticker (ex: TTE.PA)')
    with st.expander('Affichages graphiques', expanded=True):
        show_sma = st.checkbox('Afficher SMA20/SMA50', value=True, key='show_sma')
        show_bb = st.checkbox('Afficher Bollinger Bands', value=True, key='show_bb')
        show_volume = st.checkbox('Afficher Volume', value=True, key='show_volume')
        show_returns = st.checkbox('Afficher Rendements cumulés', value=True, key='show_returns')

companies = [
    ("Hermès", "RMS.PA"),
    ("TotalEnergies", "TTE.PA"),
    ("Airbus", "AIR.PA"),
    ("Sopra Steria", "SOP.PA"),
    ("Dassault Systèmes", "DSY.PA"),
]

choice = st.selectbox('Choisir une entreprise française', [c[0] for c in companies])
symbol = dict(companies)[choice]

st.markdown(f"<div class='muted'>Symbole sélectionné: <b>{symbol}</b></div>", unsafe_allow_html=True)

auto_analyze = st.checkbox('Analyse automatique', value=True, help='Lancer automatiquement l\'analyse lors du chargement ou du changement de symbole')
run_analysis = auto_analyze or st.button('Analyser')

if not run_analysis:
    st.info("Appuyez sur 'Analyser' ou activez 'Analyse automatique' pour lancer l'analyse.")

if run_analysis:
    with st.spinner('Analyse en cours — récupération des données et calcul des indicateurs...'):
        try:
            if not symbol:
                st.error('Aucun symbole résolu à analyser')
                st.stop()
            df = fetch_data(symbol, period=period, interval=interval)
        except Exception as e:
            st.error(f'Erreur récupération: {e}')
            st.stop()

        indicators = compute_indicators(df)
        indicators['Close'] = float(df['Close'].iloc[-1])

    df_plot = df.copy()
    df_plot['SMA20'] = df_plot['Close'].rolling(20).mean()
    df_plot['SMA50'] = df_plot['Close'].rolling(50).mean()
    ma = df_plot['Close'].rolling(20).mean()
    sd = df_plot['Close'].rolling(20).std()
    df_plot['BBU'] = ma + 2 * sd
    df_plot['BBL'] = ma - 2 * sd

    # Expose a few derived values into indicators for advice generation
    try:
        indicators['SMA20'] = float(df_plot['SMA20'].iloc[-1])
    except Exception:
        indicators['SMA20'] = None
    try:
        indicators['SMA50'] = float(df_plot['SMA50'].iloc[-1])
    except Exception:
        indicators['SMA50'] = None
    try:
        latest_bbu = float(df_plot['BBU'].iloc[-1])
        latest_bbl = float(df_plot['BBL'].iloc[-1])
        indicators['BB_WIDTH_PCT'] = (latest_bbu - latest_bbl) / indicators.get('Close', 1.0)
    except Exception:
        indicators['BB_WIDTH_PCT'] = None

    fundamentals = fetch_fundamentals(symbol)
    result = engine.evaluate(indicators, fundamentals)

    col1, col_div, col2 = st.columns([1, 0.02, 2])
    try:
        col_div.markdown("<div style='height:100%;border-left:1px solid #e6e6e6;margin:0 8px;'></div>", unsafe_allow_html=True)
    except Exception:
        col_div.write('')

    with col1:
        price = indicators['Close']
        prev = float(df['Close'].iloc[-2]) if len(df) >= 2 else price
        change = price - prev
        pct = (change / prev * 100.0) if prev != 0 else 0.0
        price_html = f"""
        <div class='card'>
          <div style='display:flex;justify-content:space-between;align-items:center'>
            <div>
              <div class='header-sub'>{symbol}</div>
              <div style='font-size:32px;font-weight:800'>{price:.2f} €</div>
              <div style='color:#6b7280'>{change:+.2f} EUR ({pct:+.2f}%)</div>
            </div>
            <div style='text-align:right'>
              <div class='metric-label'>RSI</div>
              <div style='font-weight:700'>{indicators.get('RSI',0):.1f}</div>
              <div style='height:8px'></div>
              <div class='metric-label'>MACD</div>
              <div style='font-weight:700'>{indicators.get('MACD',0):.3f}</div>
            </div>
          </div>
        </div>
        """
        st.markdown(price_html, unsafe_allow_html=True)

        try:
            scores = compute_indicator_scores(indicators)
            st.markdown("<div class='card'><div class='header-sub'>Scores (0–5)</div>", unsafe_allow_html=True)
            sc_l, sc_r = st.columns([1,1])
            render_score_card(sc_l, 'RSI', scores.get('RSI', 0))
            render_score_card(sc_l, 'MACD', scores.get('MACD', 0))
            render_score_card(sc_l, 'ADX', scores.get('ADX', 0))
            render_score_card(sc_r, 'Tendance', scores.get('TREND', 0))
            render_score_card(sc_r, 'H&S', scores.get('HNS', 0))
            st.markdown('</div>', unsafe_allow_html=True)
        except Exception:
            pass

        advice = generate_advice(result['decision'], result['triggered'], indicators, fundamentals)
        with st.expander('Conseils et détails', expanded=False):
            st.markdown(advice)

        st.markdown("<div class='card'><div class='header-sub'>Signaux techniques</div>", unsafe_allow_html=True)
        adx = indicators.get('ADX')
        di_plus = indicators.get('DI_PLUS')
        di_minus = indicators.get('DI_MINUS')
        sigs = []
        if adx is not None:
            sigs.append(f"ADX: {adx:.1f}")
        if di_plus is not None and di_minus is not None:
            sigs.append(f"+DI: {di_plus:.1f} | -DI: {di_minus:.1f}")
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
            sigs.append(' / '.join(cs))
        st.markdown('<div style="color:var(--muted);font-size:13px">' + ' · '.join(sigs) + '</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        with st.expander('Données fondamentales', expanded=False):
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

                mcap = humanize_number(fundamentals.get('marketCap'))
                fpe = fmt_float(fundamentals.get('forwardPE'))
                tpe = fmt_float(fundamentals.get('trailingPE'))
                col_a, col_b, col_c = st.columns(3)
                col_a.metric('Market Cap', mcap)
                col_b.metric('Forward P/E', fpe)
                col_c.metric('Trailing P/E', tpe)

                label_map = [
                    ('Dividende / action', 'dividendRate'),
                    ('Rendement (div)', 'dividendYield'),
                    ('EPS', 'earningsPerShare'),
                    ('PER (trailing)', 'trailingPE'),
                    ('Price / Book', 'priceToBook')
                ]
                rows = []
                for label, key in label_map:
                    val = fundamentals.get(key)
                    if val is None:
                        display = 'N/A'
                    else:
                        if isinstance(val, (int, float)):
                            if key in ('dividendYield',):
                                try:
                                    display = f"{float(val)*100:.2f}%"
                                except Exception:
                                    display = f"{val}"
                            elif key in ('dividendRate','earningsPerShare'):
                                display = f"{float(val):.2f}"
                            else:
                                display = str(val)
                        else:
                            display = str(val)
                    rows.append({'Champ': label, 'Valeur': display})

    with col2:
        st.subheader(f'Graphique {symbol}')
        df_plot = df_plot.copy()
        try:
            df_plot['returns_cum'] = (df_plot['Close'].pct_change().fillna(0) + 1.0).cumprod() - 1.0
        except Exception:
            df_plot['returns_cum'] = 0.0

        show_sma = st.session_state.get('show_sma', True)
        show_bb = st.session_state.get('show_bb', True)
        show_volume = st.session_state.get('show_volume', True)
        show_returns = st.session_state.get('show_returns', True)

        rows_heights = [0.7, 0.3]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                            row_heights=rows_heights, specs=[[{"secondary_y": False}], [{"secondary_y": False}]])

        fig.add_trace(go.Candlestick(x=df_plot.index,
                                     open=df_plot['Open'], high=df_plot['High'], low=df_plot['Low'], close=df_plot['Close'],
                                     name='OHLC', increasing_line_color='#0f9d58', decreasing_line_color='#d9230f'), row=1, col=1)

        if show_sma:
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['SMA20'], mode='lines', name='SMA20', line={'color': '#1f77b4'}), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['SMA50'], mode='lines', name='SMA50', line={'color': '#ff7f0e'}), row=1, col=1)
        if show_bb:
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['BBU'], mode='lines', name='BBU', line={'color': 'rgba(31,119,180,0.2)'}), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['BBL'], mode='lines', name='BBL', line={'color': 'rgba(31,119,180,0.2)'}), row=1, col=1)

        if show_volume and 'Volume' in df_plot.columns:
            fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['Volume'], name='Volume', marker_color='rgba(100,100,120,0.6)'), row=2, col=1)

        if show_returns:
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['returns_cum'] * 100.0, mode='lines', name='Cumulative Return %', line={'color': '#444444'}), row=2, col=1)

        pos = indicators.get('hs_positions')
        if pos and isinstance(pos, (list, tuple)):
            for idx in pos:
                try:
                    xval = df_plot.index[int(idx)]
                    yval = float(df_plot['Close'].iloc[int(idx)])
                    fig.add_vline(x=xval, line={'color': 'purple', 'width': 1, 'dash': 'dot'})
                    fig.add_annotation(x=xval, y=yval, text='H&S', showarrow=True, arrowhead=2, ax=0, ay=-30)
                except Exception:
                    pass

        fig.update_layout(margin={'l': 20, 'r': 20, 't': 30, 'b': 20}, height=650)
        fig.update_layout(paper_bgcolor='white', plot_bgcolor='white')
        fig.update_xaxes(showgrid=True, gridcolor='rgba(0,0,0,0.06)', zerolinecolor='rgba(0,0,0,0.04)', tickfont=dict(color='rgba(0,0,0,0.88)'))
        fig.update_yaxes(showgrid=True, gridcolor='rgba(0,0,0,0.06)', zerolinecolor='rgba(0,0,0,0.04)', tickfont=dict(color='rgba(0,0,0,0.88)'))
        st.plotly_chart(fig, use_container_width=True)

    save_analysis(symbol, result['decision'], result['reason'], indicators, fundamentals)

    st.markdown('---')
    st.subheader('Historique des analyses')
    hist = get_history(200)
    if hist:
        df_hist = pd.DataFrame(hist)
        st.dataframe(df_hist)
    else:
        st.write('Aucune analyse enregistrée')
