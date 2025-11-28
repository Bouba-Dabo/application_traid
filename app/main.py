import os
import sys

# Ensure repository root is on sys.path so `import app.*` works in hosted environments
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import streamlit as st
import urllib.parse
import pandas as pd
# ruff: noqa: E501,E402
import math
import numpy as np
import hashlib
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit.components.v1 as components

from app.finance import (
    fetch_data,
    compute_indicators,
    fetch_fundamentals,
    resolve_name_to_ticker,
)
from app.dsl_engine import DSLEngine
from app.db import init_db, save_analysis, get_history

# Thresholds for RSI interpretation (can be tuned)
RSI_OVERSOLD = 30.0
RSI_CAUTION = 60.0
RSI_OVERBOUGHT = 70.0

st.set_page_config(page_title="Traid - Analyseur", layout="wide")

# Landing control: show a cover page before accessing the analysis
if "show_landing" not in st.session_state:
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
    return DSLEngine("app/rules.dsl")


# cache helpers
fetch_data = st.cache_data(fetch_data)
fetch_fundamentals = st.cache_data(fetch_fundamentals)
resolve_name_to_ticker = st.cache_data(resolve_name_to_ticker)
get_history = st.cache_data(get_history)
# Cache compute_indicators (it's relatively expensive and deterministic for a given DataFrame)
compute_indicators = st.cache_data(compute_indicators)

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

    # MACD-based scoring
    try:
        macd = indicators.get("MACD")
        macd_s = indicators.get("MACD_SIGNAL")
        if macd is None or macd_s is None:
            out["MACD"] = 3
        else:
            diff = float(macd) - float(macd_s)
            if diff > 0 and float(macd) > 0:
                out["MACD"] = 5
            elif diff > 0:
                out["MACD"] = 4
            elif abs(diff) < 1e-8:
                out["MACD"] = 3
            elif diff < 0 and float(macd) < 0:
                out["MACD"] = 1
            else:
                out["MACD"] = 2
    except Exception:
        out["MACD"] = 3

    # RSI-based scoring (higher score when in oversold zone -> potential buy)
    try:
        rsi = indicators.get("RSI")
        if rsi is None:
            out["RSI"] = 3
        else:
            r = float(rsi)
            if r <= RSI_OVERSOLD:
                out["RSI"] = 5
            elif r <= 40:
                out["RSI"] = 4
            elif r <= RSI_CAUTION:
                out["RSI"] = 3
            elif r <= RSI_OVERBOUGHT:
                out["RSI"] = 2
            else:
                out["RSI"] = 1
    except Exception:
        out["RSI"] = 3

    # ADX-based scoring (higher score for stronger trends)
    try:
        adx = indicators.get("ADX")
        if adx is None:
            out["ADX"] = 3
        else:
            a = float(adx)
            if a >= 25:
                out["ADX"] = 5
            elif a >= 20:
                out["ADX"] = 4
            elif a >= 15:
                out["ADX"] = 3
            elif a >= 10:
                out["ADX"] = 2
            else:
                out["ADX"] = 1
    except Exception:
        out["ADX"] = 3

    # Trend classification
    try:
        trend = str(indicators.get("trend", "Sideways"))
        if "Up (strong)" in trend:
            out["TREND"] = 5
        elif "Up" in trend:
            out["TREND"] = 4
        elif "Sideways" in trend or "Unknown" in trend:
            out["TREND"] = 3
        elif "Down" in trend and "strong" in trend:
            out["TREND"] = 1
        elif "Down" in trend:
            out["TREND"] = 2
        else:
            out["TREND"] = 3
    except Exception:
        out["TREND"] = 3

    # Head & Shoulders pattern
    try:
        if indicators.get("hs_found"):
            conf = float(indicators.get("hs_confidence", 0.0))
            if conf >= 0.7:
                out["HNS"] = 1
            elif conf >= 0.4:
                out["HNS"] = 2
            else:
                out["HNS"] = 3
        else:
            out["HNS"] = 5
    except Exception:
        out["HNS"] = 5

    # Stochastic (K/D) scoring: prefer low K (survente) or K > D crossover
    try:
        k = indicators.get("STOCH_K")
        d = indicators.get("STOCH_D")
        if k is None:
            out["STOCH"] = 3
        else:
            ks = float(k)
            if ks <= 20:
                base = 5
            elif ks <= 40:
                base = 4
            elif ks <= 60:
                base = 3
            elif ks <= 80:
                base = 2
            else:
                base = 1
            # small boost if momentum (K > D)
            try:
                if d is not None and float(k) > float(d):
                    base = min(5, base + 1)
            except Exception:
                pass
            out["STOCH"] = base
    except Exception:
        out["STOCH"] = 3

    # Bollinger width scoring: narrow bands -> higher score (consolidation), wide -> low
    try:
        bw = indicators.get("BB_WIDTH_PCT")
        if bw is None:
            out["BB"] = 3
        else:
            bwf = float(bw)
            if bwf < 0.03:
                out["BB"] = 5
            elif bwf < 0.06:
                out["BB"] = 4
            elif bwf < 0.09:
                out["BB"] = 3
            elif bwf < 0.12:
                out["BB"] = 2
            else:
                out["BB"] = 1
    except Exception:
        out["BB"] = 3

    # SMA crossover scoring: SMA20 relative to SMA50
    try:
        s20 = indicators.get("SMA20")
        s50 = indicators.get("SMA50")
        if s20 is None or s50 is None:
            out["SMA"] = 3
        else:
            if float(s20) > float(s50):
                out["SMA"] = 5
            else:
                out["SMA"] = 2
    except Exception:
        out["SMA"] = 3

    # Candlestick pattern scoring
    try:
        if indicators.get("candlestick_bull_engulf") or indicators.get(
            "candlestick_hammer"
        ):
            out["CANDLE"] = 5
        elif indicators.get("candlestick_doji"):
            out["CANDLE"] = 3
        elif indicators.get("candlestick_bear_engulf"):
            out["CANDLE"] = 1
        else:
            out["CANDLE"] = 3
    except Exception:
        out["CANDLE"] = 3

    return out


def _score_color(score: int) -> str:
    s = _clamp_score(score)
    cmap = {
        0: "#7f1d1d",
        1: "#ef4444",
        2: "#f59e0b",
        3: "#fbbf24",
        4: "#06b6d4",
        5: "#10b981",
    }
    return cmap.get(s, "#64748b")


def _score_label(score: int) -> str:
    """Return a short human-readable label for a score 0..5.

    Keeps labels concise and in French to match the rest of the UI.
    """
    try:
        s = _clamp_score(score)
    except Exception:
        s = 0
    labels = {
        0: "N/A",
        1: "Très faible",
        2: "Faible",
        3: "Moyen",
        4: "Bon",
        5: "Excellent",
    }
    return labels.get(s, "N/A")


def compute_overall_score(scores: dict) -> int:
    """Return an overall score 0..5 computed from numeric entries in `scores`.

    We take the simple average of available numeric scores and round to nearest int,
    then clamp to 0..5.
    """
    vals = []
    for v in scores.values():
        try:
            vals.append(float(v))
        except Exception:
            continue
    if not vals:
        return 0
    avg = sum(vals) / len(vals)
    # Round to nearest integer and clamp
    try:
        iv = int(round(avg))
    except Exception:
        iv = int(avg)
    return _clamp_score(iv)


def render_score_card(col, label: str, score: int):
    color = _score_color(score)
    text = _score_label(score)
    html = f"""
    <div class='score-card' style='padding:8px;border-radius:10px;margin-bottom:8px'>
      <div style='display:flex;justify-content:space-between;align-items:center'>
                <div style='font-size:13px;color:var(--accent);font-weight:700'>{label}</div>
        <div style='background:{color};color:#fff;padding:6px 10px;border-radius:14px;font-weight:700'>{score}/5 — {text}</div>
      </div>
    </div>
    """
    col.markdown(html, unsafe_allow_html=True)


def generate_advice(
    decision: str, triggered: list, indicators: dict, fundamentals: dict | None = None
) -> str:
    advice_parts = []
    if decision == "BUY":
        advice_parts.append("📈 **Notre analyse suggère une opportunité d'achat.**")
    elif decision == "SELL":
        advice_parts.append("📉 **Notre analyse suggère une opportunité de vente.**")
    else:
        # Highlight the neutral recommendation using the company accent color
        advice_parts.append(
            "⚖️ <span style='color:var(--accent);font-weight:700'><strong>Il est conseillé de conserver la position pour le moment.</strong></span>"
        )
    advice_parts.append("\n**Arguments clés :**")
    # Evaluate triggered rules and relate them to current RSI using thresholds
    try:
        rsi_val = None
        try:
            rsi_val = float(indicators.get("RSI", 0.0))
        except Exception:
            rsi_val = None

        if not triggered:
            advice_parts.append(
                "\n- Aucun signal technique majeur n'a été déclenché par vos règles."
            )
        else:
            for rule in triggered:
                comment = (rule.get("comment", "") or "").lower()
                expr = rule.get("expr", "") or ""
                expr_l = expr.lower()
                # Handle RSI-related rules more precisely using thresholds
                if "rsi <" in expr_l or "oversold" in comment:
                    if rsi_val is not None and rsi_val <= RSI_OVERSOLD:
                        advice_parts.append(
                            f"\n- Le RSI ({rsi_val:.1f}) est en zone de survente (≤{RSI_OVERSOLD:.0f}), ce qui peut indiquer un rebond."
                        )
                    else:
                        cur = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"
                        advice_parts.append(
                            f"\n- Règle déclenchée : `{expr}` — RSI actuel = {cur} (pas strictement en survente)."
                        )
                elif "rsi >" in expr_l or "overbought" in comment:
                    if rsi_val is not None and rsi_val >= RSI_OVERBOUGHT:
                        advice_parts.append(
                            f"\n- Le RSI ({rsi_val:.1f}) est en zone de surachat (≥{RSI_OVERBOUGHT:.0f}), signalant un risque de correction."
                        )
                    elif rsi_val is not None and rsi_val >= RSI_CAUTION:
                        advice_parts.append(
                            f"\n- Le RSI ({rsi_val:.1f}) est modérément élevé ({RSI_CAUTION:.0f}–{RSI_OVERBOUGHT:.0f}) — prudence requise."
                        )
                    else:
                        cur = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"
                        advice_parts.append(
                            f"\n- Règle déclenchée : `{expr}` — RSI actuel = {cur}."
                        )
                elif "sma20 > sma50" in expr_l or (
                    "sma20" in expr_l and "sma50" in expr_l
                ):
                    advice_parts.append(
                        "\n- La moyenne mobile à 20 jours est au-dessus de celle à 50 jours, confirmant une tendance haussière."
                    )
                else:
                    advice_parts.append(
                        f"\n- Signal déclenché par la règle : `{expr}` ({comment})."
                    )
    except Exception:
        advice_parts.append("\n- Erreur lors de l'analyse des règles déclenchées.")
    # Contre-arguments / points de vigilance (indicateurs contraires)
    contra = []
    try:
        rsi_val = float(indicators.get("RSI", 0.0))
        if rsi_val >= 65:
            contra.append(
                f"Le RSI est élevé ({rsi_val:.1f}), signe d'une zone potentielle de sur-achat à court terme."
            )
    except Exception:
        pass
    try:
        macd = indicators.get("MACD")
        macd_s = indicators.get("MACD_SIGNAL")
        if macd is not None and macd_s is not None and float(macd) < float(macd_s):
            contra.append("Le momentum (MACD) est orienté à la baisse.")
    except Exception:
        pass
    try:
        sma20 = indicators.get("SMA20")
        sma50 = indicators.get("SMA50")
        if sma20 is not None and sma50 is not None and float(sma20) < float(sma50):
            contra.append(
                "La SMA20 est en dessous de la SMA50, ce qui est un signal technique baissier."
            )
    except Exception:
        pass
    if contra:
        advice_parts.append("\n**Points de vigilance :**")
        for c in contra:
            advice_parts.append(f"\n- {c}")

    # Volatilité (Bandes de Bollinger width)
    try:
        bw = indicators.get("BB_WIDTH_PCT")
        if bw is not None:
            if bw > 0.06:
                advice_parts.append(
                    "\n- La volatilité est élevée (Bandes de Bollinger larges). Attendez-vous à des mouvements de prix amples."
                )
            elif bw < 0.03:
                advice_parts.append(
                    "\n- La volatilité est faible (Bandes de Bollinger étroites) — phase de consolidation probable."
                )
    except Exception:
        pass

    # Contexte fondamental
    if fundamentals:
        try:
            pe = (
                fundamentals.get("trailingPE")
                or fundamentals.get("forwardPE")
                or fundamentals.get("pe")
            )
            if pe is not None:
                try:
                    pef = float(pe)
                    if decision == "BUY" and pef > 0:
                        if pef <= 15:
                            advice_parts.append(
                                f"\n**Contexte fondamental :** Le PER est de {pef:.1f}, ce qui peut indiquer une valorisation raisonnable et renforce le signal technique."
                            )
                        elif pef >= 30:
                            advice_parts.append(
                                f"\n**Contexte fondamental :** Le PER est élevé ({pef:.1f}), ce qui invite à la prudence malgré le signal technique."
                            )
                        else:
                            advice_parts.append(
                                f"\n**Contexte fondamental :** PER = {pef:.1f}. Aucune anomalie manifeste dans la valorisation."
                            )
                    elif decision == "SELL" and pef > 0:
                        advice_parts.append(
                            f"\n**Contexte fondamental :** PER = {pef:.1f}. Considérez le contexte de valorisation dans votre décision."
                        )
                except Exception:
                    pass
        except Exception:
            pass

    advice_parts.append(
        "\n\n---\n*Ces informations sont générées automatiquement à titre indicatif et ne constituent pas un conseil en investissement.*"
    )
    # Add a short ranked list of most influential triggered rules (by absolute score)
    try:
        if triggered:
            sorted_tr = sorted(
                triggered, key=lambda x: abs(int(x.get("score", 0))), reverse=True
            )
            advice_parts.append("\n**Paramètres les plus influents :**")
            for t in sorted_tr[:5]:
                sc = int(t.get("score", 0))
                expr = t.get("expr", "")
                comment = t.get("comment", "")
                sign = "+" if sc >= 0 else ""
                advice_parts.append(
                    f"\n- {sign}{sc}: `{expr}` {f'— {comment}' if comment else ''}"
                )
        else:
            # Even if no rules triggered, show key raw indicators that may still matter
            key_params = []
            try:
                rsi_val = float(indicators.get("RSI", 0.0))
                key_params.append(f"RSI = {rsi_val:.1f}")
            except Exception:
                pass
            try:
                macd = indicators.get("MACD")
                macd_s = indicators.get("MACD_SIGNAL")
                if macd is not None and macd_s is not None:
                    diff = float(macd) - float(macd_s)
                    key_params.append(f"MACD diff = {diff:.3f}")
            except Exception:
                pass
            try:
                sma20 = indicators.get("SMA20")
                sma50 = indicators.get("SMA50")
                if sma20 is not None and sma50 is not None:
                    key_params.append(
                        f"SMA20/SMA50 = {float(sma20):.2f}/{float(sma50):.2f}"
                    )
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
        .header-sub{color:var(--accent);font-weight:700}
        select, input, textarea, button {background: #fff !important; color: #0f1724 !important; border:1px solid rgba(0,0,0,0.06) !important}

        /* Make headings and prominent labels adopt the company accent color */
        .block-container h1, .block-container h2, .block-container h3, .block-container h4,
        .sidebar h1, .sidebar h2, .sidebar h3,
        .header-sub, .app-tag, .metric-label, .name, .stMarkdown h1, .stMarkdown h2 {
            color: var(--accent) !important;
        }

        /* Color widget labels in the sidebar to follow the company accent */
        .stSidebar label, .stSidebar .stMarkdown p { color: var(--accent) !important; font-weight:600 }

        /* Keep secondary/muted text readable */
        .muted, .mini-slogan, .hero-desc, .card div[style*='color:var(--muted)'] { color: var(--muted) !important }

        /* Hero styles (polished) */
        .hero{background-position:center; background-size:cover; background-repeat:no-repeat; min-height:480px; display:flex; align-items:center; border-radius:14px; margin-bottom:32px; position:relative; overflow:hidden}
        .hero::before{ content: ''; position:absolute; inset:0; background: linear-gradient(90deg, rgba(4,9,30,0.55) 0%, rgba(10,20,40,0.28) 40%, rgba(255,255,255,0.02) 100%); mix-blend-mode: multiply }
        .hero-inner{position:relative; z-index:2; max-width:1100px; margin:0 auto; display:flex; flex-direction:column; gap:18px; padding:30px; border-radius:12px; backdrop-filter: blur(6px); background: linear-gradient(180deg, rgba(255,255,255,0.88), rgba(255,255,255,0.80));}
        .hero-title{display:flex;align-items:center;gap:20px}
        .logo{background:linear-gradient(135deg,var(--accent),var(--accent-2)); color:white; width:88px;height:88px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:36px;box-shadow:0 10px 30px rgba(6,30,60,0.08)}
        .app-name{font-size:36px;font-weight:800;color:transparent;background:linear-gradient(90deg,var(--accent),var(--accent-2));-webkit-background-clip:text;background-clip:text;letter-spacing:-0.5px}
        .mini-slogan{font-size:12px;color:#64748b;margin-top:4px;font-weight:600}
        .app-tag{font-size:15px;color:#334155;margin-top:6px;font-weight:600}
        .hero-desc{color:#334155;margin:0;max-width:980px;font-size:15px;line-height:1.6}
        .schools{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:14px}
        .badge{background:linear-gradient(90deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));padding:6px 10px;border-radius:999px;color:var(--accent);margin-left:6px;font-weight:700;border:1px solid rgba(0,0,0,0.04)}

        .team{margin-top:6px}
        .members{display:flex;gap:16px;flex-wrap:wrap}
        .member{display:flex;flex-direction:column;align-items:center;width:90px}
        .avatar{background:linear-gradient(135deg,var(--accent),var(--accent-2));width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:18px;box-shadow:0 8px 18px rgba(3,12,30,0.12);transition:transform .18s ease}
        .avatar:hover{transform:translateY(-4px)}
        .name{font-size:13px;color:#0f1724;margin-top:8px;text-align:center}

        .cta-wrap{display:flex;justify-content:center;margin-top:14px}
        .cta-button{background:linear-gradient(90deg,var(--accent),var(--accent-2)); color:white;padding:14px 26px;border-radius:14px;font-weight:800;border:none;box-shadow:0 12px 34px rgba(6,30,60,0.14); cursor:pointer; font-size:16px}
        .cta-button:hover{transform:translateY(-3px);transition:all .18s ease}

        /* Small visual tweaks for Streamlit default button below hero */
        /* Use several selectors and !important to override Streamlit's built-in styles */
        .stButton>button, .stButton button, .stButton>div>button, .stButton>button>div {
            border-radius:12px !important;
            padding:14px 22px !important;
            background: linear-gradient(90deg,var(--accent) 0%,var(--accent-2) 100%) !important;
            color:#fff !important;
            font-weight:700 !important;
            border:none !important;
            box-shadow:0 12px 34px rgba(14,90,255,0.20) !important;
            transition:transform .12s ease, box-shadow .12s ease !important;
        }
        .stButton>button:hover, .stButton button:hover, .stButton>div>button:hover {
            transform:translateY(-3px) !important;
            box-shadow:0 18px 40px rgba(14,90,255,0.24) !important;
        }
        .stButton>button::after, .stButton button::after { content:' →'; margin-left:8px }

        /* Features */
        .features{display:flex;gap:12px;margin-top:14px}
        .feature{display:flex;align-items:center;gap:10px;background:linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));padding:8px 12px;border-radius:10px;box-shadow:0 6px 18px rgba(3,12,30,0.04);font-weight:600;color:#0f1724;border:1px solid rgba(0,0,0,0.03)}
        .f-icon{background:linear-gradient(90deg,var(--accent),var(--accent-2));width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800}

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

        /* Expander header styling: make the clickable bar adopt the company accent
           Try multiple selectors to target different Streamlit versions / DOM structures */
        .streamlit-expanderHeader, .st-expander > button, .stExpanderHeader, .stExpander > div > button, .st-expanderHeader,
        /* details/summary used by many Streamlit releases */
        details[role="group"] > summary, details[role="group"] > summary > div, details[role="group"] > summary > div > button,
        /* fallback selectors */
        div[data-testid="stExpander"] > button, button[data-testid="stExpander"] {
            background: linear-gradient(90deg, var(--accent), var(--accent-2)) !important;
            color: #fff !important;
            border-radius: 10px !important;
            padding: 8px 12px !important;
            font-weight: 700 !important;
            box-shadow: 0 8px 28px rgba(6,30,60,0.08) !important;
            border: none !important;
            display: flex !important;
            align-items: center !important;
            gap: 8px !important;
        }
        details[role="group"] > summary::marker { display:none }
        details[role="group"] > summary { list-style: none; }
        details[role="group"] > summary > div { width:100%; }
        details[role="group"] > summary > div > button { background: transparent !important; color: inherit !important }
        .streamlit-expanderHeader:hover, .st-expander > button:hover, details[role="group"] > summary:hover {
            transform: translateY(-2px);
            transition: all .12s ease;
        }

        </style>
        """,
    unsafe_allow_html=True,
)

if st.session_state.get("show_landing", True):
    # Try to load a local image from the app folder and inline it as base64 for the hero background
    # To keep startup fast, avoid reading/encoding large local images into memory.
    # Prefer a remote background image (fallback) rather than embedding a base64 data URL.
    bg_url = "https://images.unsplash.com/photo-1559526324-593bc073d938?auto=format&fit=crop&w=1650&q=80"

    import textwrap

    hero_html = textwrap.dedent(
        f"""
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
    """
    )
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
        if st.button("Accéder à l'analyse", key="enter_app"):
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
    # Header styled with the company accent
    st.markdown(
        "<div class='sidebar-header' style='font-size:20px;font-weight:700;color:var(--accent)'>Paramètres</div>",
        unsafe_allow_html=True,
    )

    # Use native widget labels (non-empty) so Streamlit doesn't warn; CSS will color the labels.
    refresh = st.slider(
        "Fréquence de rafraîchissement (sec)",
        min_value=5,
        max_value=3600,
        value=60,
        key="refresh",
    )
    period = st.selectbox(
        "Période historique",
        ["7d", "30d", "60d", "180d", "1y", "2y"],
        index=2,
        key="period",
    )
    interval = st.selectbox(
        "Interval", ["1m", "2m", "5m", "15m", "1d"], index=4, key="interval"
    )

    st.markdown("---")
    st.markdown(
        "<div style='color:var(--accent);font-weight:600'>Entrez un nom d'entreprise (ex: TotalEnergies) ou un ticker (ex: TTE.PA)</div>",
        unsafe_allow_html=True,
    )

    with st.expander("Affichages graphiques", expanded=True):
        show_sma = st.checkbox("Afficher SMA20/SMA50", value=True, key="show_sma")
        show_bb = st.checkbox("Afficher Bollinger Bands", value=True, key="show_bb")
        show_volume = st.checkbox("Afficher Volume", value=True, key="show_volume")
        show_returns = st.checkbox(
            "Afficher Rendements cumulés", value=True, key="show_returns"
        )

    # Refresh data button: clears caches for data/indicators/fundamentals/history then reruns
    try:
        if st.button("Rafraîchir les données", key="refresh_data"):
            cleared = []
            try:
                fetch_data.clear()
                cleared.append("fetch_data")
            except Exception:
                pass
            try:
                compute_indicators.clear()
                cleared.append("compute_indicators")
            except Exception:
                pass
            try:
                fetch_fundamentals.clear()
                cleared.append("fetch_fundamentals")
            except Exception:
                pass
            try:
                get_history.clear()
                cleared.append("get_history")
            except Exception:
                pass
            st.success(f"Caches vidés: {', '.join(cleared) if cleared else 'aucun' }")
            try:
                # Force a rerun so the UI re-fetches fresh data
                st.experimental_rerun()
            except Exception:
                pass
    except Exception:
        # If button rendering fails for any Streamlit variant, ignore silently
        pass

companies = [
    ("Hermès", "RMS.PA"),
    ("TotalEnergies", "TTE.PA"),
    ("Airbus", "AIR.PA"),
    ("Sopra Steria", "SOP.PA"),
    ("Dassault Systèmes", "DSY.PA"),
]

# Per-company metadata: preferred color (hex) and optional logo URL.
# We use a fallback avatar generator if no real logo URL is provided.
COMPANY_META = {
    "Hermès": {"color": "#D4AF37", "logo_url": "https://logo.clearbit.com/hermes.com"},
    "TotalEnergies": {
        "color": "#ff5a00",
        "logo_url": "https://logo.clearbit.com/totalenergies.com",
    },
    "Airbus": {"color": "#003366", "logo_url": "https://logo.clearbit.com/airbus.com"},
    "Sopra Steria": {
        "color": "#e4002b",
        "logo_url": "https://logo.clearbit.com/soprasteria.com",
    },
    "Dassault Systèmes": {
        "color": "#1f77b4",
        "logo_url": "https://logo.clearbit.com/3ds.com",
    },
}


def _hex_lighter(hex_color: str, percent: float = 0.45) -> str:
    """Return a lighter version of a hex color by blending with white.

    percent: 0..1 where 0 returns original color, 1 returns white.
    """
    try:
        h = hex_color.lstrip("#")
        lv = len(h)
        if lv == 3:
            h = "".join([c * 2 for c in h])
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        r = int(r + (255 - r) * percent)
        g = int(g + (255 - g) * percent)
        b = int(b + (255 - b) * percent)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


# Determine selected company from query params or session state (we'll use a small HTML dropdown).
try:
    # New API: prefer stable `st.query_params` (returns a dict-like of query params)
    params = st.query_params
except Exception:
    # Fallback for older Streamlit versions that still have the experimental API
    try:
        params = st.experimental_get_query_params()
    except Exception:
        params = {}
available_names = [c[0] for c in companies]
default_choice = available_names[0]
candidate = None
if "company" in params:
    try:
        candidate = params.get("company", [default_choice])[0]
    except Exception:
        candidate = default_choice
if candidate and candidate in available_names:
    choice = candidate
else:
    # fall back to session state or default
    choice = st.session_state.get("company_choice", default_choice)
        # Detect a change in selected company and mark that we need to fetch fresh data.
        # We intentionally avoid forcing an immediate rerun here to prevent race
        # conditions; instead we set `needs_fetch` and clear caches just before
        # the next actual fetch call.
prev_choice = st.session_state.get("company_choice")
st.session_state["company_choice"] = choice
if prev_choice is None:
    # first load: mark as needing fetch
    st.session_state.setdefault("needs_fetch", True)
elif prev_choice != choice:
    # user switched company: request fresh fetch on next analysis run
    st.session_state["needs_fetch"] = True
symbol = dict(companies)[choice]

# Use a native Streamlit selectbox for reliable, synchronous selection handling.
try:
    sel_index = available_names.index(choice) if choice in available_names else 0
    new_choice = st.selectbox("Choisir une entreprise française", available_names, index=sel_index, key="company_select")
    # If the user changed the selection, update query params and mark for refresh.
    if new_choice != choice:
        choice = new_choice
        try:
            st.experimental_set_query_params(company=choice)
        except Exception:
            pass
        st.session_state["company_choice"] = choice
        st.session_state["needs_fetch"] = True
        # If auto-analyze is enabled, trigger an immediate rerun to start analysis.
        try:
            if st.session_state.get("auto_analyze", False):
                try:
                    st.experimental_rerun()
                except Exception:
                    pass
        except Exception:
            pass
except Exception:
    # Fallback: use the native selectbox without query param handling
    choice = st.selectbox("Choisir une entreprise française", available_names)
    st.session_state["company_choice"] = choice

# Per-selection: compute company theme (color + accent) and inject CSS vars so the whole page adapts
meta_sel = COMPANY_META.get(choice, {})
company_color = meta_sel.get("color", "#3A8BFF")
company_accent2 = _hex_lighter(company_color, 0.45)
logo_url_default = meta_sel.get("logo_url") or ""
try:
    # set CSS variables to override the default theme defined earlier
    st.markdown(
        f"<style>:root {{ --accent: {company_color}; --accent-2: {company_accent2}; }}</style>",
        unsafe_allow_html=True,
    )
except Exception:
    pass

st.markdown(
    f"<div class='sidebar-line' style='color:var(--accent);font-weight:600'>Symbole sélectionné: <b style='color:var(--accent)'>{symbol}</b></div>",
    unsafe_allow_html=True,
)

# Visible HTML label (styled) and an accessible hidden native label for the checkbox.
st.markdown(
    "<div style='margin-top:8px;color:var(--accent);font-weight:600'>Analyse automatique</div>",
    unsafe_allow_html=True,
)
auto_analyze = st.checkbox(
    "Analyse automatique",
    value=True,
    help="Lancer automatiquement l'analyse lors du chargement ou du changement de symbole",
    key="auto_analyze",
    label_visibility="hidden",
)
# Run analysis if auto_analyze enabled, user clicked the button,
# or a company change requested a fresh fetch (needs_fetch).
run_analysis = auto_analyze or st.button("Analyser") or st.session_state.get("needs_fetch", False)

if not run_analysis:
    st.info(
        "Appuyez sur 'Analyser' ou activez 'Analyse automatique' pour lancer l'analyse."
    )

if run_analysis:
    with st.spinner(
        "Analyse en cours — récupération des données et calcul des indicateurs..."
    ):
        try:
            # Small runtime instrumentation to help diagnose stale results.
            try:
                last_loaded = st.session_state.get("last_loaded_company")
                debug_needs = st.session_state.get("needs_fetch", False)
                st.info(
                    f"DEBUG FETCH: choice={choice} symbol={symbol} needs_fetch={debug_needs} last_loaded={last_loaded}"
                )
                # If the currently selected choice differs from last loaded company,
                # ensure we request a fresh fetch so cached data isn't reused.
                if last_loaded is not None and last_loaded != choice:
                    st.session_state["needs_fetch"] = True
            except Exception:
                pass
            if not symbol:
                st.error("Aucun symbole résolu à analyser")
                st.stop()

            # Attempt to fetch data. For intraday minute intervals (e.g. '1m'),
            # Yahoo/YFinance often only provides recent data (typically ~7 days).
            # If the initial fetch returns no data, retry with a shorter period
            # and inform the user.
            try:
                # If a company switch occurred previously, clear cached data now
                # so the upcoming fetch is guaranteed to return fresh values.
                if st.session_state.get("needs_fetch", False):
                    try:
                        fetch_data.clear()
                    except Exception:
                        pass
                    try:
                        compute_indicators.clear()
                    except Exception:
                        pass
                    try:
                        fetch_fundamentals.clear()
                    except Exception:
                        pass
                    try:
                        get_history.clear()
                    except Exception:
                        pass
                    # reset the flag
                    st.session_state["needs_fetch"] = False

                df = fetch_data(symbol, period=period, interval=interval)
                try:
                    # Record fetch time and brief summary so we can see if data changed.
                    import datetime

                    st.session_state["last_fetch_time"] = datetime.datetime.utcnow().isoformat()
                    try:
                        st.info(
                            f"FETCHED: rows={len(df)} last_index={getattr(df.index, 'max', lambda: None)()}"
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception as e_raw:
                # If the backend raised a descriptive error, keep it for later
                df = None
                fetch_err = e_raw

            # If we received an empty DataFrame (or fetch raised), and the
            # user requested a minute-based interval, retry with a shorter
            # period that is compatible with intraday data.
            if (df is None or (hasattr(df, 'empty') and df.empty)) and (
                isinstance(interval, str) and interval.endswith("m")
            ):
                fallback_period = "7d"
                try:
                    st.info(
                        f"Les données intrajournalières ('{interval}') peuvent être limitées dans le temps. Réessai avec période='{fallback_period}'..."
                    )
                    df = fetch_data(symbol, period=fallback_period, interval=interval)
                except Exception as e2:
                    # Nothing worked — present the best error message available
                    err_msg = (
                        str(e2) if e2 is not None else str(fetch_err)
                    )
                    st.error(f"Erreur récupération: {err_msg}")
                    st.stop()

            # Final check: if still empty, report and stop
            if df is None or (hasattr(df, "empty") and df.empty):
                st.error("Erreur récupération: Aucune donnée renvoyée pour ce symbole/intervalle.")
                st.stop()
            # Record that we've loaded data for this company so subsequent
            # UI interactions don't show stale data.
            try:
                st.session_state["last_loaded_company"] = choice
            except Exception:
                pass
        except Exception as e:
            st.error(f"Erreur récupération: {e}")
            st.stop()

        indicators = compute_indicators(df)
        indicators["Close"] = float(df["Close"].iloc[-1])

    df_plot = df.copy()
    df_plot["SMA20"] = df_plot["Close"].rolling(20).mean()
    df_plot["SMA50"] = df_plot["Close"].rolling(50).mean()
    ma = df_plot["Close"].rolling(20).mean()
    sd = df_plot["Close"].rolling(20).std()
    df_plot["BBU"] = ma + 2 * sd
    df_plot["BBL"] = ma - 2 * sd

    # Expose a few derived values into indicators for advice generation
    try:
        indicators["SMA20"] = float(df_plot["SMA20"].iloc[-1])
    except Exception:
        indicators["SMA20"] = None
    try:
        indicators["SMA50"] = float(df_plot["SMA50"].iloc[-1])
    except Exception:
        indicators["SMA50"] = None
    try:
        latest_bbu = float(df_plot["BBU"].iloc[-1])
        latest_bbl = float(df_plot["BBL"].iloc[-1])
        indicators["BB_WIDTH_PCT"] = (latest_bbu - latest_bbl) / indicators.get(
            "Close", 1.0
        )
    except Exception:
        indicators["BB_WIDTH_PCT"] = None

    fundamentals = fetch_fundamentals(symbol)
    result = engine.evaluate(indicators, fundamentals)

    col1, col_div, col2 = st.columns([1, 0.02, 2])
    try:
        col_div.markdown(
            "<div style='height:100%;border-left:1px solid #e6e6e6;margin:0 8px;'></div>",
            unsafe_allow_html=True,
        )
    except Exception:
        col_div.write("")

    with col1:
        price = indicators["Close"]
        prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else price
        change = price - prev
        pct = (change / prev * 100.0) if prev != 0 else 0.0

        # Per-company theming (logo + accent colors)
        meta = COMPANY_META.get(choice, {})
        company_color = meta.get("color", "#3A8BFF")
        company_accent2 = _hex_lighter(company_color, 0.45)
        # logo: prefer provided URL, else generate an avatar image with the company name
        logo_url = meta.get("logo_url")
        if not logo_url:
            try:
                logo_name = urllib.parse.quote(choice)
                logo_url = f"https://ui-avatars.com/api/?name={logo_name}&background={company_color.lstrip('#')}&color=ffffff&size=128"
            except Exception:
                logo_url = ""

        # Inject CSS variables so components use the company color where we referenced --accent
        try:
            st.markdown(
                f"<style>:root {{ --accent: {company_color}; --accent-2: {company_accent2}; }}</style>",
                unsafe_allow_html=True,
            )
        except Exception:
            pass

        price_html = f"""
        <div class='card'>
          <div style='display:flex;justify-content:space-between;align-items:center'>
            <div style='display:flex;align-items:center;gap:12px'>
              <img src='{logo_url}' alt='{choice} logo' style='width:56px;height:56px;border-radius:10px;object-fit:cover;box-shadow:0 6px 18px rgba(2,6,23,0.12)' />
              <div>
                            <div style='display:flex;flex-direction:column'>
                                <div class='header-sub' style='display:flex;gap:8px;align-items:center'><span>{symbol}</span></div>
                                <div style='font-size:14px;font-weight:800;color:{company_color};margin-top:4px'>{choice}</div>
                                <div style='font-size:32px;font-weight:800'>{price:.2f} €</div>
                            </div>
                <div style='color:#6b7280'>{change:+.2f} EUR ({pct:+.2f}%)</div>
              </div>
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
            # compute combined overall score
            overall = compute_overall_score(scores)
            overall_color = _score_color(overall)
            overall_label = _score_label(overall)

            st.markdown(
                "<div class='card'><div class='header-sub'>Scores (0–5)</div>",
                unsafe_allow_html=True,
            )
            # Show a prominent overall score at the top
            try:
                st.markdown(
                    f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>"
                    f"<div style='font-weight:700;color:var(--accent)'>Score global</div>"
                    f"<div style='background:{overall_color};color:#fff;padding:8px 14px;border-radius:16px;font-weight:800;font-size:16px'>{overall}/5 — {overall_label}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except Exception:
                pass

            sc_l, sc_r = st.columns([1, 1])
            render_score_card(sc_l, "RSI", scores.get("RSI", 0))
            render_score_card(sc_l, "MACD", scores.get("MACD", 0))
            render_score_card(sc_l, "ADX", scores.get("ADX", 0))
            render_score_card(sc_l, "STOCH", scores.get("STOCH", 0))
            render_score_card(sc_l, "SMA", scores.get("SMA", 0))
            render_score_card(sc_r, "Tendance", scores.get("TREND", 0))
            render_score_card(sc_r, "H&S", scores.get("HNS", 0))
            render_score_card(sc_r, "BB", scores.get("BB", 0))
            render_score_card(sc_r, "Candles", scores.get("CANDLE", 0))
            st.markdown("</div>", unsafe_allow_html=True)
        except Exception:
            pass

        advice = generate_advice(
            result["decision"], result["triggered"], indicators, fundamentals
        )
        # Render the expander normally; widget labels are now native and colored via CSS.
        with st.expander("Conseils et détails", expanded=False):
            # Styled header inside the expander to adopt company color
            st.markdown(
                "<div style='font-weight:700;color:var(--accent);font-size:15px;margin-bottom:8px'>Conseils et détails</div>",
                unsafe_allow_html=True,
            )
            # advice contains small HTML snippets (coloured highlights). Allow HTML rendering.
            st.markdown(advice, unsafe_allow_html=True)

        st.markdown(
            "<div class='card'><div class='header-sub'>Signaux techniques</div>",
            unsafe_allow_html=True,
        )
        adx = indicators.get("ADX")
        di_plus = indicators.get("DI_PLUS")
        di_minus = indicators.get("DI_MINUS")
        sigs = []
        if adx is not None:
            sigs.append(f"ADX: {adx:.1f}")
        if di_plus is not None and di_minus is not None:
            sigs.append(f"+DI: {di_plus:.1f} | -DI: {di_minus:.1f}")
        cs = []
        if indicators.get("candlestick_hammer"):
            cs.append("🔔 Hammer")
        if indicators.get("candlestick_bull_engulf"):
            cs.append("📈 Bull Engulfing")
        if indicators.get("candlestick_bear_engulf"):
            cs.append("📉 Bear Engulfing")
        if indicators.get("candlestick_doji"):
            cs.append("⚪ Doji")
        if cs:
            sigs.append(" / ".join(cs))
        st.markdown(
            '<div style="color:var(--muted);font-size:13px">'
            + " · ".join(sigs)
            + "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        # --- Données techniques: rendements, volatilité, indicateurs bruts ---
        with st.expander(
            "Données techniques (rendements, volatilité, indicateurs bruts)",
            expanded=False,
        ):
            st.markdown(
                "<div style='font-weight:700;color:var(--accent);font-size:15px;margin-bottom:8px'>Données techniques</div>",
                unsafe_allow_html=True,
            )
            try:
                # Cumulative returns over the loaded period
                returns_series = (
                    df["Close"].pct_change().fillna(0) + 1.0
                ).cumprod() - 1.0
                cum_pct = (
                    float(returns_series.iloc[-1]) * 100.0
                    if len(returns_series) > 0
                    else 0.0
                )
                start_price = float(df["Close"].iloc[0]) if len(df) > 0 else price
                period_return_pct = (
                    (price / start_price - 1.0) * 100.0
                    if start_price and len(df) > 1
                    else 0.0
                )

                # Approximate period length in days (if index is datetime)
                try:
                    days = (df.index[-1] - df.index[0]).days
                except Exception:
                    days = None

                ann_return = None
                if days and days > 0:
                    try:
                        ann_return = (
                            (1.0 + cum_pct / 100.0) ** (365.0 / days) - 1.0
                        ) * 100.0
                    except Exception:
                        ann_return = None

                # Annualized volatility (estimate)
                try:
                    daily_ret = df["Close"].pct_change().dropna()
                    vol_annual = float(daily_ret.std()) * (252**0.5) * 100.0
                except Exception:
                    vol_annual = None

                avg_vol = float(df["Volume"].mean()) if "Volume" in df.columns else None

                rows = []
                rows.append(("Prix actuel", f"{price:.2f} €"))
                rows.append(("Variation", f"{change:+.2f} € ({pct:+.2f} % )"))
                rows.append(("Rendement sur période", f"{period_return_pct:+.2f}%"))
                rows.append(("Rendement cumulé", f"{cum_pct:+.2f}%"))
                if ann_return is not None:
                    rows.append(("Rendement annualisé (est.)", f"{ann_return:+.2f}%"))
                if vol_annual is not None:
                    rows.append(("Volatilité annualisée", f"{vol_annual:.2f}%"))
                if avg_vol is not None:
                    rows.append(("Volume moyen", f"{avg_vol:,.0f}"))

                # Raw indicator values
                for k in ("RSI", "MACD", "ADX", "SMA20", "SMA50", "BB_WIDTH_PCT"):
                    v = indicators.get(k)
                    if v is None:
                        continue
                    if k == "BB_WIDTH_PCT":
                        try:
                            rows.append(("Bollinger width", f"{float(v) * 100:.2f}%"))
                        except Exception:
                            rows.append(("Bollinger width", str(v)))
                    elif k in ("SMA20", "SMA50"):
                        try:
                            rows.append((k, f"{float(v):.2f}"))
                        except Exception:
                            rows.append((k, str(v)))
                    elif k == "MACD":
                        try:
                            rows.append(("MACD", f"{float(v):.3f}"))
                        except Exception:
                            rows.append(("MACD", str(v)))
                    else:
                        try:
                            rows.append((k, f"{float(v):.2f}"))
                        except Exception:
                            rows.append((k, str(v)))

                # Dividend yield if present in fundamentals
                try:
                    dy = fundamentals.get("dividendYield")
                    if dy is not None:
                        rows.append(("Rendement (div)", f"{float(dy) * 100:.2f}%"))
                except Exception:
                    pass

                # Render a compact two-column table inside a card
                html = "<div class='card'><table style='width:100%;border-collapse:collapse'>"
                for label, val in rows:
                    html += f"<tr><td style='padding:6px 8px;border-bottom:1px solid rgba(0,0,0,0.04);width:60%;font-weight:700;color:var(--accent)'>{label}</td><td style='padding:6px 8px;border-bottom:1px solid rgba(0,0,0,0.04);text-align:right'>{val}</td></tr>"
                html += "</table></div>"
                st.markdown(html, unsafe_allow_html=True)
            except Exception as e:
                st.write("Erreur calcul des données techniques :", e)

        with st.expander("Données fondamentales", expanded=False):
            st.markdown(
                "<div style='font-weight:700;color:var(--accent);font-size:15px;margin-bottom:8px'>Données fondamentales</div>",
                unsafe_allow_html=True,
            )
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
                        return "N/A"

                mcap = humanize_number(fundamentals.get("marketCap"))
                fpe = fmt_float(fundamentals.get("forwardPE"))
                tpe = fmt_float(fundamentals.get("trailingPE"))
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Market Cap", mcap)
                col_b.metric("Forward P/E", fpe)
                col_c.metric("Trailing P/E", tpe)

                label_map = [
                    ("Dividende / action", "dividendRate"),
                    ("Rendement (div)", "dividendYield"),
                    ("EPS", "earningsPerShare"),
                    ("PER (trailing)", "trailingPE"),
                    ("Price / Book", "priceToBook"),
                ]
                rows = []
                for label, key in label_map:
                    val = fundamentals.get(key)
                    if val is None:
                        display = "N/A"
                    else:
                        if isinstance(val, (int, float)):
                            if key in ("dividendYield",):
                                try:
                                    display = f"{float(val)*100:.2f}%"
                                except Exception:
                                    display = f"{val}"
                            elif key in ("dividendRate", "earningsPerShare"):
                                display = f"{float(val):.2f}"
                            else:
                                display = str(val)
                        else:
                            display = str(val)
                    rows.append({"Champ": label, "Valeur": display})

    with col2:
        st.subheader(f"Graphique {symbol}")
        df_plot = df_plot.copy()
        try:
            df_plot["returns_cum"] = (
                df_plot["Close"].pct_change().fillna(0) + 1.0
            ).cumprod() - 1.0
        except Exception:
            df_plot["returns_cum"] = 0.0

        show_sma = st.session_state.get("show_sma", True)
        show_bb = st.session_state.get("show_bb", True)
        show_volume = st.session_state.get("show_volume", True)
        show_returns = st.session_state.get("show_returns", True)

        # Use three rows: price (+indicators) / volume / cumulative returns.
        # This keeps volume and returns on separate y-scales so the returns
        # line remains visible instead of being dwarfed by volume bars.
        rows_heights = [0.6, 0.2, 0.2]
        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.04,
            row_heights=rows_heights,
            specs=[[{"secondary_y": False}], [{"secondary_y": False}], [{"secondary_y": False}]],
        )

        fig.add_trace(
            go.Candlestick(
                x=df_plot.index,
                open=df_plot["Open"],
                high=df_plot["High"],
                low=df_plot["Low"],
                close=df_plot["Close"],
                name="OHLC",
                increasing_line_color="#0f9d58",
                decreasing_line_color="#d9230f",
            ),
            row=1,
            col=1,
        )

        if show_sma:
            # Use the selected company color for SMA20 and a lighter accent for SMA50
            try:
                sma20_color = company_color
                sma50_color = company_accent2
            except Exception:
                sma20_color = "#1f77b4"
                sma50_color = "#ff7f0e"
            fig.add_trace(
                go.Scatter(
                    x=df_plot.index,
                    y=df_plot["SMA20"],
                    mode="lines",
                    name="SMA20",
                    line={"color": sma20_color},
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=df_plot.index,
                    y=df_plot["SMA50"],
                    mode="lines",
                    name="SMA50",
                    line={"color": sma50_color},
                ),
                row=1,
                col=1,
            )
        if show_bb:
            fig.add_trace(
                go.Scatter(
                    x=df_plot.index,
                    y=df_plot["BBU"],
                    mode="lines",
                    name="BBU",
                    line={"color": "rgba(31,119,180,0.2)"},
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=df_plot.index,
                    y=df_plot["BBL"],
                    mode="lines",
                    name="BBL",
                    line={"color": "rgba(31,119,180,0.2)"},
                ),
                row=1,
                col=1,
            )

        if show_volume and "Volume" in df_plot.columns:
            fig.add_trace(
                go.Bar(
                    x=df_plot.index,
                    y=df_plot["Volume"],
                    name="Volume",
                    marker_color="rgba(100,100,120,0.6)",
                ),
                row=2,
                col=1,
            )

        # Plot cumulative returns on their own subplot so the scale is
        # independent of volume and easier to read.
        if show_returns:
            fig.add_trace(
                go.Scatter(
                    x=df_plot.index,
                    y=df_plot["returns_cum"] * 100.0,
                    mode="lines",
                    name="Cumulative Return %",
                    line={"color": "#444444"},
                ),
                row=3,
                col=1,
            )

        # Downsample long series for plotting performance. We aggregate OHLCV
        # and keep the last-known indicator values per block. This is a simple
        # decimation strategy that preserves key price extrema within each
        # bucket while drastically reducing point count for the renderer.
        @st.cache_data
        def _downsample_ohlcv(df_in: pd.DataFrame, max_points: int = 4000) -> pd.DataFrame:
            n = len(df_in)
            if n <= max_points:
                return df_in
            ratio = math.ceil(n / max_points)
            # group index per block
            grp = np.arange(n) // ratio
            agg = {}
            # OHLCV
            if "Open" in df_in.columns:
                agg["Open"] = "first"
            if "High" in df_in.columns:
                agg["High"] = "max"
            if "Low" in df_in.columns:
                agg["Low"] = "min"
            if "Close" in df_in.columns:
                agg["Close"] = "last"
            if "Volume" in df_in.columns:
                agg["Volume"] = "sum"
            # indicators / moving averages: keep last value in the block
            for c in ("SMA20", "SMA50", "BBU", "BBL", "returns_cum"):
                if c in df_in.columns:
                    agg[c] = "last"

            try:
                df_grp = df_in.groupby(grp).agg(agg)
                # set timestamp to the first timestamp of each block for clarity
                timestamps = [df_in.index[i * ratio] for i in range(len(df_grp))]
                df_grp.index = pd.to_datetime(timestamps)
                return df_grp
            except Exception:
                # Fallback: if grouping fails for any reason, return original
                return df_in

        # Apply downsampling if the series is long
        try:
            MAX_PLOT_POINTS = 4000
            df_plot_ds = _downsample_ohlcv(df_plot, max_points=MAX_PLOT_POINTS)
        except Exception:
            df_plot_ds = df_plot

        # When we annotate H&S positions, map original indices to downsampled ones
        pos = indicators.get("hs_positions")
        if pos and isinstance(pos, (list, tuple)):
            try:
                n_orig = len(df_plot)
                n_ds = len(df_plot_ds)
                if n_orig > 0 and n_ds > 0:
                    ratio_map = math.ceil(n_orig / max(1, n_ds))
                    pos_mapped = [int(int(idx) / ratio_map) for idx in pos]
                else:
                    pos_mapped = pos
            except Exception:
                pos_mapped = pos
        else:
            pos_mapped = pos

        # Use downsampled DataFrame for plotting if available
        try:
            dp = df_plot_ds
        except Exception:
            dp = df_plot

        pos = pos_mapped if "pos_mapped" in locals() else indicators.get("hs_positions")
        if pos and isinstance(pos, (list, tuple)):
            for idx in pos:
                try:
                    xval = dp.index[int(idx)]
                    yval = float(dp["Close"].iloc[int(idx)])
                    fig.add_vline(
                        x=xval, line={"color": "purple", "width": 1, "dash": "dot"}
                    )
                    fig.add_annotation(
                        x=xval,
                        y=yval,
                        text="H&S",
                        showarrow=True,
                        arrowhead=2,
                        ax=0,
                        ay=-30,
                    )
                except Exception:
                    pass

        # Remove duplicated traces (e.g., accidental double plotting of the same series)
        try:
            seen = set()
            new_traces = []
            for tr in fig.data:
                key = (getattr(tr, "name", None), getattr(tr, "type", None))
                if key in seen:
                    continue
                seen.add(key)
                new_traces.append(tr)
            # reassign cleaned traces
            fig.data = tuple(new_traces)
        except Exception:
            pass

        # Specific safeguard: if multiple 'Cumulative' traces remain (sometimes
        # generated as slightly different scatter traces), remove any later
        # occurrences and keep only the first one so the plot shows a single
        # cumulative-return line.
        try:
            filtered = []
            seen_cum = False
            for tr in fig.data:
                name = (getattr(tr, "name", "") or "").lower()
                if "cumul" in name or "cumulative" in name or "return" in name and "cum" in name:
                    if seen_cum:
                        # skip this duplicate cumulative trace
                        continue
                    seen_cum = True
                filtered.append(tr)
            fig.data = tuple(filtered)
        except Exception:
            pass

        # Final robust deduplication: compute a lightweight signature for each
        # trace (type, name, length, SHA256 of y-values) and drop later traces
        # with identical signatures. This handles the case where two traces are
        # numerically identical but were created separately.
        try:
            sigs = set()
            unique_traces = []
            for tr in fig.data:
                try:
                    ttype = getattr(tr, "type", "")
                    tname = (getattr(tr, "name", "") or "")
                    y = getattr(tr, "y", None)
                    if y is None:
                        y_bytes = b""
                        length = 0
                    else:
                        y_arr = np.asarray(y)
                        length = y_arr.size
                        # tobytes on a float64 representation for stable hashing
                        try:
                            y_bytes = y_arr.astype(np.float64).tobytes()
                        except Exception:
                            # Fallback to repr if conversion fails
                            y_bytes = repr(y_arr).encode("utf-8")
                    h = hashlib.sha256(y_bytes).hexdigest()
                    sig = (ttype, tname, length, h)
                except Exception:
                    sig = (getattr(tr, "type", ""), getattr(tr, "name", ""), 0, "")

                if sig in sigs:
                    # duplicate data trace: skip
                    continue
                sigs.add(sig)
                unique_traces.append(tr)

            fig.data = tuple(unique_traces)
        except Exception:
            pass

        # Layout & interactivity improvements:
        fig.update_layout(
            margin={"l": 20, "r": 20, "t": 30, "b": 20},
            height=850,
            paper_bgcolor="white",
            plot_bgcolor="white",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

        # Range slider + selector buttons (attach to bottom x-axis only)
        # First, ensure no rangeslider on all xaxes
        fig.update_xaxes(rangeslider_visible=False)
        # Then enable rangeslider and rangeselector only on the bottom subplot (row=3)
        fig.update_xaxes(
            row=3,
            col=1,
            rangeslider_visible=True,
            rangeselector=dict(
                buttons=[
                    dict(count=1, label="1d", step="day", stepmode="backward"),
                    dict(count=7, label="7d", step="day", stepmode="backward"),
                    dict(count=1, label="1m", step="month", stepmode="backward"),
                    dict(count=6, label="6m", step="month", stepmode="backward"),
                    dict(step="all", label="All"),
                ]
            ),
            showgrid=True,
            gridcolor="rgba(0,0,0,0.06)",
            zerolinecolor="rgba(0,0,0,0.04)",
            tickfont=dict(color="rgba(0,0,0,0.88)"),
        )

        fig.update_yaxes(
            showgrid=True,
            gridcolor="rgba(0,0,0,0.06)",
            zerolinecolor="rgba(0,0,0,0.04)",
            tickfont=dict(color="rgba(0,0,0,0.88)"),
        )

        # Axis titles and formatting per subplot — explicitly target each y-axis
        try:
            fig.update_yaxes(title_text="Price (EUR)", row=1, col=1)
            # Format volume axis with SI suffixes (k, M) for readability
            fig.update_yaxes(title_text="Volume", row=2, col=1, tickformat=",.0s")
            fig.update_yaxes(title_text="Cumulative Return (%)", row=3, col=1)
        except Exception:
            pass

        # Improve hover templates for clarity
        for tr in fig.data:
            try:
                tname = (tr.name or "").lower()
                if getattr(tr, "type", "") == "candlestick":
                    tr.hovertemplate = "Date: %{x}<br>open: %{open:.2f}<br>high: %{high:.2f}<br>low: %{low:.2f}<br>close: %{close:.2f}<extra></extra>"
                elif getattr(tr, "type", "") == "bar":
                    tr.hovertemplate = "Date: %{x}<br>Volume: %{y:,}<extra></extra>"
                elif "returns" in tname or "cumulative" in tname:
                    tr.hovertemplate = "Date: %{x}<br>%{y:.2f}%<extra></extra>"
                elif "sma" in tname or "bbu" in tname or "bbl" in tname:
                    tr.hovertemplate = "Date: %{x}<br>" + (tr.name or "%{y}") + ": %{y:.2f}<extra></extra>"
                else:
                    # generic fallback for other scatter traces
                    if getattr(tr, "type", "") == "scatter":
                        tr.hovertemplate = "Date: %{x}<br>" + (tr.name or "%{y}") + ": %{y:.2f}<extra></extra>"
            except Exception:
                # Non-critical: skip if any trace doesn't accept hovertemplate
                pass

        # Plotly modebar config: ensure image export button is present
        plotly_config = {
            "toImageButtonOptions": {"format": "png", "filename": f"{symbol}_chart"},
            "modeBarButtonsToAdd": ["toImage"],
        }

        # Use new API: width='stretch' replaces use_container_width=True (deprecated)
        try:
            st.plotly_chart(fig, width="stretch", config=plotly_config)
        except Exception:
            # Fallback for older Streamlit versions
            st.plotly_chart(fig, use_container_width=True, config=plotly_config)

    save_analysis(
        symbol, result["decision"], result["reason"], indicators, fundamentals
    )

    st.markdown("---")
    st.subheader("Historique des analyses")
    hist = get_history(200)
    if hist:
        df_hist = pd.DataFrame(hist)
        st.dataframe(df_hist)
    else:
        st.write("Aucune analyse enregistrée")
