import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime, timezone
import plotly.graph_objects as go
import plotly.express as px

# ─── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PolyTracker · Bot Hunter",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Syne', sans-serif;
    background-color: #0a0a0f;
    color: #e2e8f0;
}

.stApp { background-color: #0a0a0f; }

/* Header */
.hero-title {
    font-family: 'Syne', sans-serif;
    font-size: 2.8rem;
    font-weight: 800;
    background: linear-gradient(90deg, #00ff9d, #00b4ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.1;
    margin-bottom: 0.2rem;
}
.hero-sub {
    font-family: 'Space Mono', monospace;
    font-size: 0.75rem;
    color: #4a5568;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    margin-bottom: 2rem;
}

/* Metric cards */
.metric-card {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 12px;
    padding: 1.2rem 1.5rem;
    text-align: center;
}
.metric-value {
    font-family: 'Space Mono', monospace;
    font-size: 2rem;
    font-weight: 700;
    color: #00ff9d;
}
.metric-label {
    font-size: 0.7rem;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 0.3rem;
}

/* Status badge */
.badge-bot {
    background: rgba(0,255,157,0.1);
    border: 1px solid #00ff9d;
    color: #00ff9d;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.7rem;
    font-family: 'Space Mono', monospace;
}
.badge-human {
    background: rgba(255,255,255,0.05);
    border: 1px solid #374151;
    color: #9ca3af;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.7rem;
    font-family: 'Space Mono', monospace;
}
.badge-whale {
    background: rgba(0,180,255,0.1);
    border: 1px solid #00b4ff;
    color: #00b4ff;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.7rem;
    font-family: 'Space Mono', monospace;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #0d1117;
    border-right: 1px solid #1f2937;
}

/* Dataframe tweaks */
[data-testid="stDataFrame"] {
    border: 1px solid #1f2937;
    border-radius: 10px;
}

/* Section headers */
.section-title {
    font-family: 'Space Mono', monospace;
    font-size: 0.65rem;
    color: #4b5563;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    margin-bottom: 0.8rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid #1f2937;
}

/* Alert box */
.alert-box {
    background: rgba(239,68,68,0.08);
    border: 1px solid rgba(239,68,68,0.3);
    border-radius: 8px;
    padding: 0.8rem 1rem;
    font-family: 'Space Mono', monospace;
    font-size: 0.75rem;
    color: #fca5a5;
    margin-bottom: 1rem;
}
</style>
""", unsafe_allow_html=True)

# ─── API helpers ─────────────────────────────────────────────────────────────
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

@st.cache_data(ttl=120)
def get_crypto_markets(limit: int = 30) -> list[dict]:
    """Fetch active BTC/crypto markets from Gamma API."""
    keywords = ["bitcoin", "btc", "crypto", "ethereum", "eth"]
    markets = []
    for kw in keywords:
        try:
            r = requests.get(
                f"{GAMMA_BASE}/markets",
                params={"limit": limit, "active": "true", "closed": "false",
                        "tag_slug": kw},
                timeout=10,
            )
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    markets.extend(data)
                elif isinstance(data, dict) and "markets" in data:
                    markets.extend(data["markets"])
        except Exception:
            pass
    # dedup by condition_id
    seen = set()
    unique = []
    for m in markets:
        cid = m.get("conditionId") or m.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(m)
    return unique

@st.cache_data(ttl=60)
def get_trades_for_market(condition_id: str, limit: int = 500) -> list[dict]:
    """Fetch recent trades for a market via CLOB API."""
    try:
        r = requests.get(
            f"{CLOB_BASE}/trades",
            params={"market": condition_id, "limit": limit},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", [])
    except Exception:
        pass
    return []

def classify_wallet(stats: dict) -> str:
    wr  = stats.get("win_rate", 0)
    cnt = stats.get("trade_count", 0)
    vol = stats.get("total_volume", 0)
    if cnt >= 20 and wr >= 0.65:
        return "🤖 BOT"
    if vol >= 5000:
        return "🐳 WHALE"
    return "👤 HUMAN"

def analyze_trades(trades: list[dict]) -> pd.DataFrame:
    """Aggregate trades by maker address → wallet stats."""
    if not trades:
        return pd.DataFrame()

    rows = []
    for t in trades:
        maker = t.get("maker") or t.get("maker_address") or t.get("transactedAt")
        side  = t.get("side", "").upper()
        size  = float(t.get("size", 0) or 0)
        price = float(t.get("price", 0) or 0)
        outcome = t.get("outcome", "") or ""
        ts_raw = t.get("timestamp") or t.get("created_at") or ""
        rows.append({
            "maker": maker,
            "side": side,
            "size": size,
            "price": price,
            "value": size * price,
            "outcome": str(outcome).upper(),
            "ts": ts_raw,
        })

    df = pd.DataFrame(rows)
    if df.empty or "maker" not in df.columns:
        return pd.DataFrame()

    df = df[df["maker"].notna() & (df["maker"] != "")]

    # group by maker
    agg = (
        df.groupby("maker")
        .agg(
            trade_count=("size", "count"),
            total_volume=("value", "sum"),
            avg_size=("size", "mean"),
            avg_price=("price", "mean"),
            yes_trades=("outcome", lambda x: (x == "YES").sum()),
            no_trades=("outcome",  lambda x: (x == "NO").sum()),
        )
        .reset_index()
    )
    agg["win_rate"] = agg["yes_trades"] / (agg["yes_trades"] + agg["no_trades"] + 1e-9)
    agg["pnl_est"]  = agg["total_volume"] * (agg["win_rate"] - 0.5) * 2   # rough estimate
    agg["type"]     = agg.apply(classify_wallet, axis=1)

    return agg.sort_values("win_rate", ascending=False).reset_index(drop=True)

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    min_trades   = st.slider("Mínimo de trades", 1, 50, 5)
    min_win_rate = st.slider("Win rate mínimo %", 0, 100, 50) / 100
    show_types   = st.multiselect(
        "Mostrar tipo",
        ["🤖 BOT", "🐳 WHALE", "👤 HUMAN"],
        default=["🤖 BOT", "🐳 WHALE"],
    )
    max_markets  = st.slider("Mercados a escanear", 1, 10, 3)
    st.divider()
    st.markdown("""
    <div style='font-family:Space Mono,monospace;font-size:0.65rem;color:#4b5563;line-height:1.8'>
    🤖 BOT → win rate ≥65% + ≥20 trades<br>
    🐳 WHALE → volumen ≥ $5,000<br>
    👤 HUMAN → resto
    </div>
    """, unsafe_allow_html=True)
    auto_refresh = st.checkbox("Auto-refresh (2 min)", value=False)

# ─── Main ────────────────────────────────────────────────────────────────────
st.markdown('<div class="hero-title">PolyTracker</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-sub">// bot & whale detector · polymarket btc/crypto markets</div>', unsafe_allow_html=True)

if auto_refresh:
    st.rerun()

# Load markets
with st.spinner("Obteniendo mercados BTC/Crypto..."):
    markets = get_crypto_markets(limit=50)

if not markets:
    st.markdown('<div class="alert-box">⚠️ No se pudieron obtener mercados. Verifica tu conexión o intenta más tarde.</div>', unsafe_allow_html=True)
    st.stop()

# Top metrics row
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{len(markets)}</div><div class="metric-label">Mercados encontrados</div></div>', unsafe_allow_html=True)

# Pick markets to scan
selected_markets = markets[:max_markets]

# Collect all trades
all_wallets: list[pd.DataFrame] = []
market_labels = []

progress = st.progress(0, text="Escaneando mercados...")
for i, mkt in enumerate(selected_markets):
    cid   = mkt.get("conditionId") or mkt.get("id") or ""
    title = mkt.get("question") or mkt.get("title") or cid[:20]
    progress.progress((i + 1) / len(selected_markets), text=f"Escaneando: {title[:60]}...")
    trades = get_trades_for_market(cid, limit=300)
    df_w   = analyze_trades(trades)
    if not df_w.empty:
        df_w["market"] = title
        all_wallets.append(df_w)
    market_labels.append({"title": title, "cid": cid, "trades": len(trades)})
    time.sleep(0.2)

progress.empty()

# Merge all wallets
if not all_wallets:
    st.warning("No se encontraron trades en los mercados seleccionados. Intenta aumentar el número de mercados.")
    st.stop()

df_all = pd.concat(all_wallets, ignore_index=True)

# Apply filters
df_filtered = df_all[
    (df_all["trade_count"] >= min_trades) &
    (df_all["win_rate"]    >= min_win_rate) &
    (df_all["type"].isin(show_types))
].copy()

# Update metrics
bots   = (df_filtered["type"] == "🤖 BOT").sum()
whales = (df_filtered["type"] == "🐳 WHALE").sum()
total_vol = df_filtered["total_volume"].sum()

with col2:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{bots}</div><div class="metric-label">Bots detectados</div></div>', unsafe_allow_html=True)
with col3:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{whales}</div><div class="metric-label">Whales detectadas</div></div>', unsafe_allow_html=True)
with col4:
    st.markdown(f'<div class="metric-card"><div class="metric-value">${total_vol:,.0f}</div><div class="metric-label">Volumen total</div></div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🔍 Wallets Ranking", "📈 PnL & Volumen", "🗂️ Mercados escaneados"])

with tab1:
    st.markdown('<div class="section-title">Wallets por win rate — filtradas</div>', unsafe_allow_html=True)

    if df_filtered.empty:
        st.info("No hay wallets que cumplan los filtros actuales. Baja los umbrales en la barra lateral.")
    else:
        display_df = df_filtered[[
            "maker", "type", "trade_count", "win_rate",
            "total_volume", "avg_size", "pnl_est", "market"
        ]].copy()
        display_df.columns = [
            "Wallet", "Tipo", "# Trades", "Win Rate",
            "Volumen $", "Avg Size", "PnL Estimado $", "Mercado"
        ]
        display_df["Win Rate"] = (display_df["Win Rate"] * 100).round(1).astype(str) + "%"
        display_df["Volumen $"]      = display_df["Volumen $"].round(2)
        display_df["PnL Estimado $"] = display_df["PnL Estimado $"].round(2)
        display_df["Avg Size"]       = display_df["Avg Size"].round(2)
        display_df["Wallet"] = display_df["Wallet"].astype(str).str[:12] + "..."

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Win Rate": st.column_config.TextColumn("Win Rate"),
                "Volumen $": st.column_config.NumberColumn("Volumen $", format="$%.2f"),
                "PnL Estimado $": st.column_config.NumberColumn("PnL Est.", format="$%.2f"),
            }
        )

        csv = display_df.to_csv(index=False).encode()
        st.download_button("⬇️ Descargar CSV", csv, "polytracker_wallets.csv", "text/csv")

with tab2:
    if df_filtered.empty:
        st.info("Sin datos para graficar.")
    else:
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown('<div class="section-title">Win Rate por tipo</div>', unsafe_allow_html=True)
            fig_wr = px.box(
                df_filtered, x="type", y="win_rate", color="type",
                color_discrete_map={"🤖 BOT": "#00ff9d", "🐳 WHALE": "#00b4ff", "👤 HUMAN": "#6b7280"},
                template="plotly_dark",
                labels={"win_rate": "Win Rate", "type": "Tipo"},
            )
            fig_wr.update_layout(
                paper_bgcolor="#111827", plot_bgcolor="#111827",
                showlegend=False, margin=dict(t=20, b=20),
                font_family="Space Mono",
            )
            st.plotly_chart(fig_wr, use_container_width=True)

        with col_b:
            st.markdown('<div class="section-title">Volumen vs Win Rate</div>', unsafe_allow_html=True)
            fig_sc = px.scatter(
                df_filtered, x="total_volume", y="win_rate",
                color="type", size="trade_count",
                color_discrete_map={"🤖 BOT": "#00ff9d", "🐳 WHALE": "#00b4ff", "👤 HUMAN": "#6b7280"},
                template="plotly_dark",
                labels={"total_volume": "Volumen $", "win_rate": "Win Rate"},
                hover_data=["maker", "trade_count"],
            )
            fig_sc.update_layout(
                paper_bgcolor="#111827", plot_bgcolor="#111827",
                margin=dict(t=20, b=20), font_family="Space Mono",
            )
            st.plotly_chart(fig_sc, use_container_width=True)

        st.markdown('<div class="section-title">PnL estimado — Top 20 wallets</div>', unsafe_allow_html=True)
        top20 = df_filtered.nlargest(20, "pnl_est").copy()
        top20["wallet_short"] = top20["maker"].astype(str).str[:10] + "…"
        colors = ["#00ff9d" if v >= 0 else "#ef4444" for v in top20["pnl_est"]]
        fig_pnl = go.Figure(go.Bar(
            x=top20["wallet_short"], y=top20["pnl_est"],
            marker_color=colors,
            text=top20["pnl_est"].round(0).astype(str),
            textposition="outside",
        ))
        fig_pnl.update_layout(
            paper_bgcolor="#111827", plot_bgcolor="#111827",
            font_family="Space Mono", font_color="#9ca3af",
            margin=dict(t=10, b=10), xaxis_tickangle=-35,
            yaxis_title="PnL Estimado ($)",
        )
        st.plotly_chart(fig_pnl, use_container_width=True)

with tab3:
    st.markdown('<div class="section-title">Mercados escaneados</div>', unsafe_allow_html=True)
    df_mkt = pd.DataFrame(market_labels)
    df_mkt.columns = ["Mercado", "Condition ID", "Trades obtenidos"]
    st.dataframe(df_mkt, use_container_width=True, hide_index=True)

# Footer
st.markdown("""
<div style='text-align:center;margin-top:3rem;font-family:Space Mono,monospace;
font-size:0.6rem;color:#1f2937;letter-spacing:0.1em'>
POLYTRACKER · DATOS EN TIEMPO REAL VIA POLYMARKET API · NO ES ASESORAMIENTO FINANCIERO
</div>
""", unsafe_allow_html=True)
