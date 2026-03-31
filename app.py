import streamlit as st
import pandas as pd
import requests
import time
import plotly.graph_objects as go
import plotly.express as px

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PolyTracker · Bot Hunter",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap');

html, body, [class*="css"] { font-family: 'Syne', sans-serif; background-color: #0a0a0f; color: #e2e8f0; }
.stApp { background-color: #0a0a0f; }

.hero-title {
    font-family: 'Syne', sans-serif; font-size: 2.8rem; font-weight: 800;
    background: linear-gradient(90deg, #00ff9d, #00b4ff);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    line-height: 1.1; margin-bottom: 0.2rem;
}
.hero-sub {
    font-family: 'Space Mono', monospace; font-size: 0.72rem; color: #4a5568;
    letter-spacing: 0.15em; text-transform: uppercase; margin-bottom: 2rem;
}
.metric-card {
    background: #111827; border: 1px solid #1f2937; border-radius: 12px;
    padding: 1.2rem 1.5rem; text-align: center;
}
.metric-value { font-family: 'Space Mono', monospace; font-size: 2rem; font-weight: 700; color: #00ff9d; }
.metric-label { font-size: 0.68rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.1em; margin-top: 0.3rem; }
.section-title {
    font-family: 'Space Mono', monospace; font-size: 0.62rem; color: #4b5563;
    text-transform: uppercase; letter-spacing: 0.2em; margin-bottom: 0.8rem;
    padding-bottom: 0.4rem; border-bottom: 1px solid #1f2937;
}
.info-box {
    background: rgba(0,180,255,0.07); border: 1px solid rgba(0,180,255,0.25);
    border-radius: 8px; padding: 0.75rem 1rem;
    font-family: 'Space Mono', monospace; font-size: 0.72rem; color: #7dd3fc;
    margin-bottom: 1rem;
}
[data-testid="stSidebar"] { background-color: #0d1117; border-right: 1px solid #1f2937; }
</style>
""", unsafe_allow_html=True)

# ─── API constants ────────────────────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PolyTracker/1.0)",
    "Accept": "application/json",
}

# ─── API helpers ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_crypto_markets(limit: int = 30) -> list:
    """Fetch active BTC/Crypto markets via Gamma API."""
    markets = []
    slugs_to_try = ["bitcoin", "btc", "crypto", "ethereum"]
    for tag in slugs_to_try:
        try:
            r = requests.get(
                f"{GAMMA_API}/markets",
                params={"limit": limit, "active": "true", "closed": "false", "tag_slug": tag},
                headers=HEADERS, timeout=15,
            )
            if r.ok:
                data = r.json()
                items = data if isinstance(data, list) else data.get("markets", [])
                markets.extend(items)
        except Exception:
            pass

    # Also try general search and keyword filter
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"limit": 50, "active": "true", "closed": "false"},
            headers=HEADERS, timeout=15,
        )
        if r.ok:
            data = r.json()
            items = data if isinstance(data, list) else data.get("markets", [])
            for m in items:
                q = (m.get("question") or m.get("title") or "").lower()
                if any(k in q for k in ["bitcoin", "btc", "crypto", "ethereum", "eth"]):
                    markets.append(m)
    except Exception:
        pass

    seen, unique = set(), []
    for m in markets:
        cid = m.get("conditionId") or m.get("condition_id") or m.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(m)
    return unique


@st.cache_data(ttl=60)
def get_trades_for_market(condition_id: str, limit: int = 500) -> list:
    """
    Fetch trades using Data API — correct endpoint for per-market trades.
    GET data-api.polymarket.com/trades?market=<conditionId>&limit=<n>
    """
    try:
        r = requests.get(
            f"{DATA_API}/trades",
            params={"market": condition_id, "limit": limit},
            headers=HEADERS, timeout=15,
        )
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", []) or data.get("trades", [])
    except Exception:
        pass
    return []


@st.cache_data(ttl=180)
def get_leaderboard(window: str = "7d", limit: int = 100) -> list:
    """
    Fetch top traders from Data API leaderboard.
    GET data-api.polymarket.com/leaderboard?window=7d&limit=100
    """
    try:
        r = requests.get(
            f"{DATA_API}/leaderboard",
            params={"window": window, "limit": limit},
            headers=HEADERS, timeout=15,
        )
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", []) or data.get("leaderboard", [])
    except Exception:
        pass
    return []


def classify_wallet(row) -> str:
    wr  = row.get("win_rate", 0)
    cnt = row.get("trade_count", 0)
    vol = row.get("volume", row.get("total_volume", 0))
    pnl = row.get("profit", row.get("pnl_est", 0))
    if cnt >= 20 and wr >= 0.65:
        return "🤖 BOT"
    if vol >= 5000 or pnl >= 1000:
        return "🐳 WHALE"
    return "👤 HUMAN"


def analyze_trades(trades: list) -> pd.DataFrame:
    """Aggregate trades by proxyWallet → wallet stats."""
    if not trades:
        return pd.DataFrame()

    rows = []
    for t in trades:
        wallet  = t.get("proxyWallet") or t.get("maker") or t.get("user") or ""
        side    = str(t.get("side", "")).upper()
        size    = float(t.get("size", 0) or 0)
        price   = float(t.get("price", 0) or 0)
        outcome = str(t.get("outcome", "")).upper()
        rows.append({"wallet": wallet, "side": side, "size": size,
                     "price": price, "value": size * price, "outcome": outcome})

    df = pd.DataFrame(rows)
    if df.empty or "wallet" not in df.columns:
        return pd.DataFrame()

    df = df[df["wallet"].notna() & (df["wallet"] != "")]

    agg = (
        df.groupby("wallet")
        .agg(
            trade_count  = ("size", "count"),
            total_volume = ("value", "sum"),
            avg_size     = ("size", "mean"),
            avg_price    = ("price", "mean"),
            yes_count    = ("outcome", lambda x: (x == "YES").sum()),
            no_count     = ("outcome", lambda x: (x == "NO").sum()),
        )
        .reset_index()
    )
    agg["win_rate"] = agg["yes_count"] / (agg["yes_count"] + agg["no_count"] + 1e-9)
    agg["pnl_est"]  = agg["total_volume"] * (agg["win_rate"] - 0.5) * 2
    agg["type"]     = agg.apply(classify_wallet, axis=1)

    return agg.sort_values("win_rate", ascending=False).reset_index(drop=True)


def parse_leaderboard(raw: list) -> pd.DataFrame:
    """Parse leaderboard response into a clean DataFrame."""
    if not raw:
        return pd.DataFrame()

    rows = []
    for entry in raw:
        wallet  = entry.get("proxyWallet") or entry.get("user") or entry.get("address", "")
        name    = entry.get("name") or entry.get("pseudonym") or (wallet[:10] + "…" if wallet else "—")
        profit  = float(entry.get("profit") or entry.get("pnl") or 0)
        volume  = float(entry.get("volume") or entry.get("totalVolume") or 0)
        markets = int(entry.get("marketsTraded") or entry.get("markets") or 0)
        rows.append({"wallet": wallet, "name": name, "profit": profit,
                     "volume": volume, "markets": markets})

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["win_rate"]    = (df["profit"] / (df["volume"] + 1e-9)).clip(0, 1)
    df["trade_count"] = df["markets"]
    df["type"]        = df.apply(classify_wallet, axis=1)

    return df.sort_values("profit", ascending=False).reset_index(drop=True)


# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuración")

    mode = st.radio(
        "Fuente de datos",
        ["📊 Leaderboard global", "🔍 Trades por mercado"],
        index=0,
        help="Leaderboard: ranking oficial de Polymarket. Trades: analiza wallets en mercados BTC específicos.",
    )

    if mode == "📊 Leaderboard global":
        lb_window = st.selectbox("Ventana de tiempo", ["1d", "7d", "30d", "all"], index=1)
        lb_limit  = st.slider("Top traders a cargar", 20, 100, 50)
    else:
        max_markets  = st.slider("Mercados BTC a escanear", 1, 10, 3)
        trades_limit = st.slider("Trades por mercado", 100, 500, 300)

    st.divider()
    min_trades   = st.slider("Filtro: mínimo trades/mercados", 1, 50, 1)
    min_win_rate = st.slider("Filtro: win rate mínimo %", 0, 100, 0) / 100
    show_types   = st.multiselect(
        "Mostrar tipo",
        ["🤖 BOT", "🐳 WHALE", "👤 HUMAN"],
        default=["🤖 BOT", "🐳 WHALE", "👤 HUMAN"],
    )
    st.divider()
    st.markdown("""
    <div style='font-family:Space Mono,monospace;font-size:0.62rem;color:#4b5563;line-height:2'>
    🤖 BOT → win rate ≥65% + ≥20 trades<br>
    🐳 WHALE → profit ≥$1K o vol ≥$5K<br>
    👤 HUMAN → resto
    </div>
    """, unsafe_allow_html=True)


# ─── Header ───────────────────────────────────────────────────────────────────
st.markdown('<div class="hero-title">PolyTracker</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-sub">// bot & whale detector · polymarket btc/crypto markets</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MODO 1 — LEADERBOARD GLOBAL
# ═══════════════════════════════════════════════════════════════════════════════
if mode == "📊 Leaderboard global":

    with st.spinner(f"Cargando top {lb_limit} traders ({lb_window}) desde Polymarket Data API..."):
        raw_lb = get_leaderboard(window=lb_window, limit=lb_limit)

    if not raw_lb:
        st.error("⚠️ No se pudo obtener el leaderboard. La API puede estar temporalmente no disponible. Intenta en unos minutos.")
        st.stop()

    df_lb = parse_leaderboard(raw_lb)

    df_f = df_lb[
        (df_lb["trade_count"] >= min_trades) &
        (df_lb["win_rate"]    >= min_win_rate) &
        (df_lb["type"].isin(show_types))
    ].copy()

    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="metric-card"><div class="metric-value">{len(df_lb)}</div><div class="metric-label">Traders en leaderboard</div></div>', unsafe_allow_html=True)
    with col2:
        bots = (df_f["type"] == "🤖 BOT").sum()
        st.markdown(f'<div class="metric-card"><div class="metric-value">{bots}</div><div class="metric-label">Bots detectados</div></div>', unsafe_allow_html=True)
    with col3:
        whales = (df_f["type"] == "🐳 WHALE").sum()
        st.markdown(f'<div class="metric-card"><div class="metric-value">{whales}</div><div class="metric-label">Whales detectadas</div></div>', unsafe_allow_html=True)
    with col4:
        total_profit = df_f["profit"].sum() if not df_f.empty else 0
        st.markdown(f'<div class="metric-card"><div class="metric-value">${total_profit:,.0f}</div><div class="metric-label">Profit total filtrado</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["🏆 Ranking de traders", "📈 Visualizaciones"])

    with tab1:
        st.markdown('<div class="section-title">Top traders por profit — leaderboard global</div>', unsafe_allow_html=True)
        if df_f.empty:
            st.info("Sin datos con los filtros actuales. Baja los umbrales en el sidebar.")
        else:
            display = df_f[["name", "type", "profit", "volume", "markets", "win_rate"]].copy()
            display.columns = ["Trader", "Tipo", "Profit ($)", "Volumen ($)", "Mercados", "Win Rate Est."]
            display["Win Rate Est."] = (display["Win Rate Est."] * 100).round(1).astype(str) + "%"
            display["Profit ($)"]    = display["Profit ($)"].round(2)
            display["Volumen ($)"]   = display["Volumen ($)"].round(2)

            st.dataframe(
                display, use_container_width=True, hide_index=True,
                column_config={
                    "Profit ($)":  st.column_config.NumberColumn(format="$%.2f"),
                    "Volumen ($)": st.column_config.NumberColumn(format="$%.2f"),
                },
            )
            st.download_button("⬇️ Descargar CSV", display.to_csv(index=False).encode(),
                               "polytracker_leaderboard.csv", "text/csv")

    with tab2:
        if not df_f.empty:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown('<div class="section-title">Distribución de profit por tipo</div>', unsafe_allow_html=True)
                fig = px.box(
                    df_f, x="type", y="profit", color="type",
                    color_discrete_map={"🤖 BOT": "#00ff9d", "🐳 WHALE": "#00b4ff", "👤 HUMAN": "#6b7280"},
                    template="plotly_dark", labels={"profit": "Profit $", "type": "Tipo"},
                )
                fig.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    showlegend=False, margin=dict(t=20, b=20), font_family="Space Mono")
                st.plotly_chart(fig, use_container_width=True)

            with col_b:
                st.markdown('<div class="section-title">Volumen vs Profit</div>', unsafe_allow_html=True)
                fig2 = px.scatter(
                    df_f, x="volume", y="profit", color="type",
                    color_discrete_map={"🤖 BOT": "#00ff9d", "🐳 WHALE": "#00b4ff", "👤 HUMAN": "#6b7280"},
                    template="plotly_dark", hover_data=["name", "markets"],
                    labels={"volume": "Volumen $", "profit": "Profit $"},
                )
                fig2.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    margin=dict(t=20, b=20), font_family="Space Mono")
                st.plotly_chart(fig2, use_container_width=True)

            st.markdown('<div class="section-title">Top 20 traders por profit</div>', unsafe_allow_html=True)
            top20  = df_f.nlargest(20, "profit").copy()
            colors = ["#00ff9d" if v >= 0 else "#ef4444" for v in top20["profit"]]
            fig3 = go.Figure(go.Bar(
                x=top20["name"], y=top20["profit"],
                marker_color=colors,
                text=["$" + f"{v:,.0f}" for v in top20["profit"]],
                textposition="outside",
            ))
            fig3.update_layout(
                paper_bgcolor="#111827", plot_bgcolor="#111827",
                font_family="Space Mono", font_color="#9ca3af",
                margin=dict(t=10, b=10), xaxis_tickangle=-35, yaxis_title="Profit ($)",
            )
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info("Sin datos para visualizar con los filtros actuales.")


# ═══════════════════════════════════════════════════════════════════════════════
# MODO 2 — TRADES POR MERCADO
# ═══════════════════════════════════════════════════════════════════════════════
else:
    with st.spinner("Obteniendo mercados BTC/Crypto..."):
        markets = get_crypto_markets(limit=50)

    if not markets:
        st.error("No se encontraron mercados BTC/Crypto activos.")
        st.stop()

    selected    = markets[:max_markets]
    all_wallets = []
    market_log  = []

    prog = st.progress(0, text="Escaneando mercados...")
    for i, mkt in enumerate(selected):
        cid   = mkt.get("conditionId") or mkt.get("condition_id") or mkt.get("id") or ""
        title = mkt.get("question") or mkt.get("title") or cid[:20]
        prog.progress((i + 1) / len(selected), text=f"Escaneando: {title[:55]}...")

        trades = get_trades_for_market(cid, limit=trades_limit)
        df_w   = analyze_trades(trades)
        if not df_w.empty:
            df_w["market"] = title
            all_wallets.append(df_w)
        market_log.append({"Mercado": title[:60], "Condition ID": cid[:20] + "…", "Trades": len(trades)})
        time.sleep(0.3)

    prog.empty()

    if not all_wallets:
        st.warning("No se obtuvieron trades en los mercados. Prueba el modo **📊 Leaderboard global** que usa un endpoint diferente.")
        st.dataframe(pd.DataFrame(market_log), use_container_width=True, hide_index=True)
        st.stop()

    df_all = pd.concat(all_wallets, ignore_index=True)
    df_f   = df_all[
        (df_all["trade_count"] >= min_trades) &
        (df_all["win_rate"]    >= min_win_rate) &
        (df_all["type"].isin(show_types))
    ].copy()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="metric-card"><div class="metric-value">{len(markets)}</div><div class="metric-label">Mercados encontrados</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="metric-card"><div class="metric-value">{(df_f["type"]=="🤖 BOT").sum()}</div><div class="metric-label">Bots detectados</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-card"><div class="metric-value">{(df_f["type"]=="🐳 WHALE").sum()}</div><div class="metric-label">Whales</div></div>', unsafe_allow_html=True)
    with col4:
        vol = df_f["total_volume"].sum() if not df_f.empty else 0
        st.markdown(f'<div class="metric-card"><div class="metric-value">${vol:,.0f}</div><div class="metric-label">Volumen total</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    tab1, tab2, tab3 = st.tabs(["🔍 Wallets Ranking", "📈 PnL & Volumen", "🗂️ Mercados"])

    with tab1:
        if df_f.empty:
            st.info("Sin datos con los filtros actuales.")
        else:
            d = df_f[["wallet","type","trade_count","win_rate","total_volume","avg_size","pnl_est","market"]].copy()
            d.columns = ["Wallet","Tipo","# Trades","Win Rate","Volumen $","Avg Size","PnL Est. $","Mercado"]
            d["Win Rate"]    = (d["Win Rate"]*100).round(1).astype(str) + "%"
            d["Wallet"]      = d["Wallet"].astype(str).str[:14] + "…"
            d["Volumen $"]   = d["Volumen $"].round(2)
            d["PnL Est. $"]  = d["PnL Est. $"].round(2)
            st.dataframe(d, use_container_width=True, hide_index=True)
            st.download_button("⬇️ CSV", d.to_csv(index=False).encode(), "wallets.csv", "text/csv")

    with tab2:
        if not df_f.empty:
            c1, c2 = st.columns(2)
            with c1:
                fig = px.box(df_f, x="type", y="win_rate", color="type",
                    color_discrete_map={"🤖 BOT":"#00ff9d","🐳 WHALE":"#00b4ff","👤 HUMAN":"#6b7280"},
                    template="plotly_dark")
                fig.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    showlegend=False, font_family="Space Mono", margin=dict(t=10,b=10))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig2 = px.scatter(df_f, x="total_volume", y="win_rate", color="type",
                    color_discrete_map={"🤖 BOT":"#00ff9d","🐳 WHALE":"#00b4ff","👤 HUMAN":"#6b7280"},
                    template="plotly_dark", hover_data=["wallet","trade_count"])
                fig2.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    font_family="Space Mono", margin=dict(t=10,b=10))
                st.plotly_chart(fig2, use_container_width=True)

    with tab3:
        st.dataframe(pd.DataFrame(market_log), use_container_width=True, hide_index=True)


# ─── Footer ───────────────────────────────────────────────────────────────────
st.markdown("""
<div style='text-align:center;margin-top:3rem;font-family:Space Mono,monospace;
font-size:0.58rem;color:#1f2937;letter-spacing:0.1em'>
POLYTRACKER · DATA API + GAMMA API · NO ES ASESORAMIENTO FINANCIERO
</div>
""", unsafe_allow_html=True)
