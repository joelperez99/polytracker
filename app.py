import streamlit as st
import pandas as pd
import requests
import time
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(page_title="PolyTracker · Bot Hunter", page_icon="🔍",
                   layout="wide", initial_sidebar_state="expanded")

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
.hero-sub { font-family:'Space Mono',monospace; font-size:0.72rem; color:#4a5568;
    letter-spacing:.15em; text-transform:uppercase; margin-bottom:1.5rem; }
.metric-card { background:#111827; border:1px solid #1f2937; border-radius:12px;
    padding:1.1rem 1.2rem; text-align:center; margin-bottom:.5rem; }
.metric-value { font-family:'Space Mono',monospace; font-size:1.8rem; font-weight:700; color:#00ff9d; }
.metric-label { font-size:.65rem; color:#6b7280; text-transform:uppercase; letter-spacing:.1em; margin-top:.3rem; }
.section-title { font-family:'Space Mono',monospace; font-size:.6rem; color:#4b5563;
    text-transform:uppercase; letter-spacing:.2em; margin-bottom:.6rem;
    padding-bottom:.3rem; border-bottom:1px solid #1f2937; }
.tip-box { background:rgba(0,255,157,.05); border:1px solid rgba(0,255,157,.2);
    border-radius:8px; padding:.7rem 1rem; font-family:'Space Mono',monospace;
    font-size:.68rem; color:#6ee7b7; margin-bottom:1rem; line-height:1.8; }
[data-testid="stSidebar"] { background-color:#0d1117; border-right:1px solid #1f2937; }
</style>
""", unsafe_allow_html=True)

# ─── Constants ────────────────────────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
HEADERS   = {"User-Agent": "PolyTracker/1.0", "Accept": "application/json"}

# ─── API ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_markets(tag_slug: str = "", limit: int = 50, min_volume: float = 0) -> list:
    """Fetch active markets. Optionally filter by tag slug."""
    params = {"limit": limit, "active": "true", "closed": "false",
              "order": "volume", "ascending": "false"}
    if tag_slug:
        params["tag_slug"] = tag_slug
    try:
        r = requests.get(f"{GAMMA_API}/markets", params=params, headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            items = data if isinstance(data, list) else data.get("markets", [])
            if min_volume > 0:
                items = [m for m in items
                         if float(m.get("volume", 0) or m.get("volumeClob", 0) or 0) >= min_volume]
            return items
    except Exception:
        pass
    return []


@st.cache_data(ttl=300)
def fetch_all_crypto_markets(limit_per_tag: int = 30) -> list:
    """
    Pull from multiple crypto tags and deduplicate.
    Returns markets sorted by volume descending.
    """
    tags = ["bitcoin", "btc", "ethereum", "crypto", "cryptocurrency",
            "solana", "ripple", "dogecoin"]
    seen, markets = set(), []
    for tag in tags:
        items = fetch_markets(tag_slug=tag, limit=limit_per_tag)
        for m in items:
            cid = m.get("conditionId") or m.get("id")
            if cid and cid not in seen:
                seen.add(cid)
                markets.append(m)
    # Also pull top volume markets (no tag filter) and check title
    general = fetch_markets(limit=100, min_volume=0)
    crypto_kw = ["bitcoin","btc","ethereum","eth","crypto","solana","sol","xrp",
                 "doge","dogecoin","bnb","coinbase","binance","altcoin","blockchain",
                 "defi","nft","token","stablecoin","usdc","usdt","halving","etf crypto"]
    for m in general:
        cid   = m.get("conditionId") or m.get("id")
        title = (m.get("question") or m.get("title") or "").lower()
        if cid and cid not in seen and any(kw in title for kw in crypto_kw):
            seen.add(cid)
            markets.append(m)

    # Sort by volume
    def vol(m):
        return float(m.get("volume", 0) or m.get("volumeClob", 0) or 0)
    return sorted(markets, key=vol, reverse=True)


@st.cache_data(ttl=90)
def fetch_trades(condition_id: str, limit: int = 500) -> list:
    """GET data-api.polymarket.com/trades?market=<cid>&limit=<n>"""
    for attempt in range(2):
        try:
            r = requests.get(f"{DATA_API}/trades",
                             params={"market": condition_id, "limit": limit},
                             headers=HEADERS, timeout=20)
            if r.ok:
                data = r.json()
                return data if isinstance(data, list) else data.get("data", [])
            if r.status_code == 429:
                time.sleep(2)
        except Exception:
            time.sleep(1)
    return []


@st.cache_data(ttl=180)
def fetch_leaderboard(window: str = "7d", limit: int = 100) -> list:
    """GET data-api.polymarket.com/leaderboard"""
    try:
        r = requests.get(f"{DATA_API}/leaderboard",
                         params={"window": window, "limit": limit},
                         headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data if isinstance(data, list) else data.get("data", [])
    except Exception:
        pass
    return []


# ─── Bot detection ────────────────────────────────────────────────────────────
def analyze_wallets(trades: list, market_title: str = "") -> pd.DataFrame:
    """
    Aggregate trades → wallet stats → classify BOT / WHALE / HUMAN.

    Bot signals (behavioral fingerprints):
      1. size_cv < 0.5  → consistent trade sizes (bots use fixed amounts)
      2. trades_per_hour > 1  → high frequency
      3. trade_count >= 10   → enough data to judge
      4. buy_sell_ratio near 1.0 → balanced market-maker behavior
    """
    if not trades:
        return pd.DataFrame()

    rows = []
    for t in trades:
        wallet = t.get("proxyWallet") or t.get("maker") or t.get("user") or ""
        if not wallet:
            continue
        side    = str(t.get("side", "")).upper()
        size    = float(t.get("size", 0) or 0)
        price   = float(t.get("price", 0) or 0)
        usd     = float(t.get("usdcSize", 0) or (size * price))
        ts      = int(t.get("timestamp", 0) or 0)
        rows.append({"wallet": wallet, "side": side, "size": size,
                     "price": price, "usd": usd, "ts": ts})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df[(df["wallet"] != "") & (df["size"] > 0)]

    results = []
    for wallet, g in df.groupby("wallet"):
        n         = len(g)
        total_usd = g["usd"].sum()
        avg_size  = g["size"].mean()
        std_size  = g["size"].std(ddof=0) if n > 1 else 0
        size_cv   = std_size / (avg_size + 1e-9)

        buys  = g[g["side"] == "BUY"]
        sells = g[g["side"] == "SELL"]
        nb, ns = len(buys), len(sells)
        # Buy/sell balance: 1.0 = perfect market maker
        bs_ratio = min(nb, ns) / (max(nb, ns) + 1e-9) if (nb + ns) > 0 else 0

        # Time frequency
        ts_vals = g["ts"][g["ts"] > 0]
        if len(ts_vals) > 1:
            span_h = (ts_vals.max() - ts_vals.min()) / 3600
            tph    = n / (span_h + 1e-9)
        else:
            tph = 0

        # Avg prices
        avg_buy  = float(buys["price"].mean())  if nb > 0 else None
        avg_sell = float(sells["price"].mean()) if ns > 0 else None

        # ── Classification ──
        # Strong bot: fixed sizes + high frequency
        bot_score = 0
        if n >= 10:           bot_score += 1
        if size_cv < 0.4:     bot_score += 2  # very consistent sizes
        elif size_cv < 0.6:   bot_score += 1
        if tph > 2:           bot_score += 2  # very high frequency
        elif tph > 0.5:       bot_score += 1
        if bs_ratio > 0.6:    bot_score += 1  # market maker pattern
        if n >= 30:           bot_score += 1  # very active

        is_bot   = bot_score >= 4
        is_whale = total_usd >= 5000

        wtype = "🤖 BOT" if is_bot else ("🐳 WHALE" if is_whale else "👤 HUMAN")

        results.append({
            "wallet":     wallet,
            "type":       wtype,
            "bot_score":  bot_score,
            "trades":     n,
            "volume_$":   round(total_usd, 2),
            "avg_size":   round(avg_size, 4),
            "size_cv":    round(size_cv, 3),
            "trades/h":   round(tph, 2),
            "bs_ratio":   round(bs_ratio, 3),
            "buys":       nb,
            "sells":      ns,
            "avg_buy_p":  round(avg_buy, 4)  if avg_buy  else None,
            "avg_sell_p": round(avg_sell, 4) if avg_sell else None,
            "market":     market_title[:60],
        })

    return (pd.DataFrame(results)
            .sort_values(["bot_score", "trades"], ascending=False)
            .reset_index(drop=True))


def parse_leaderboard(raw: list) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame()
    rows = []
    for e in raw:
        wallet  = e.get("proxyWallet") or e.get("user") or ""
        name    = e.get("name") or e.get("pseudonym") or (wallet[:10]+"…" if wallet else "—")
        profit  = float(e.get("profit") or 0)
        volume  = float(e.get("volume") or 0)
        markets = int(e.get("marketsTraded") or 0)
        rows.append({"wallet": wallet, "name": name, "profit": profit,
                     "volume": volume, "markets": markets})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["p_rate"] = (df["profit"] / (df["volume"] + 1e-9)).clip(-1, 1)
    df["type"]   = df.apply(lambda r: (
        "🤖 BOT"   if r["markets"] >= 10 and r["p_rate"] > 0.10 else
        "🐳 WHALE" if r["volume"]  >= 10000 or r["profit"] >= 2000 else
        "👤 HUMAN"
    ), axis=1)
    return df.sort_values("profit", ascending=False).reset_index(drop=True)


# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    mode = st.radio("Modo", ["📊 Leaderboard global", "🔍 Trades por mercado"], index=0)

    if mode == "📊 Leaderboard global":
        lb_window = st.selectbox("Ventana", ["1d", "7d", "30d", "all"], index=1)
        lb_limit  = st.slider("Top traders", 20, 100, 100)
    else:
        n_markets    = st.slider("Mercados a escanear", 3, 20, 8,
                                 help="Más mercados = más wallets detectadas, más lento")
        trades_limit = st.slider("Trades por mercado", 200, 500, 400)

    st.divider()
    min_trades = st.slider("Min. trades para mostrar", 1, 30, 2)
    show_types = st.multiselect("Tipos a mostrar",
        ["🤖 BOT", "🐳 WHALE", "👤 HUMAN"],
        default=["🤖 BOT", "🐳 WHALE", "👤 HUMAN"])

    st.divider()
    st.markdown("""
    <div style='font-family:Space Mono,monospace;font-size:.6rem;color:#4b5563;line-height:2.2'>
    <b style='color:#00ff9d'>🤖 BOT</b> (score ≥ 4)<br>
    · Tamaños consistentes (CV&lt;0.6)<br>
    · Alta frecuencia (trades/hora)<br>
    · Balance buys/sells<br>
    · ≥10-30 trades en mismo mercado<br><br>
    <b style='color:#00b4ff'>🐳 WHALE</b><br>
    · Volumen ≥ $5,000<br><br>
    <b style='color:#6b7280'>👤 HUMAN</b> → resto
    </div>
    """, unsafe_allow_html=True)

# ─── Header ───────────────────────────────────────────────────────────────────
st.markdown('<div class="hero-title">PolyTracker</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-sub">// bot & whale detector · polymarket prediction markets</div>',
            unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MODO 1 — LEADERBOARD
# ═══════════════════════════════════════════════════════════════════════════════
if mode == "📊 Leaderboard global":
    with st.spinner(f"Cargando leaderboard {lb_window} — top {lb_limit}…"):
        raw = fetch_leaderboard(lb_window, lb_limit)

    if not raw:
        st.error("No se pudo obtener el leaderboard. Intenta en unos minutos.")
        st.stop()

    df = parse_leaderboard(raw)
    df_f = df[(df["markets"] >= min_trades) & df["type"].isin(show_types)].copy()

    c1, c2, c3, c4 = st.columns(4)
    for col, val, label in [
        (c1, len(df),                              "Traders totales"),
        (c2, (df_f["type"]=="🤖 BOT").sum(),       "Bots detectados"),
        (c3, (df_f["type"]=="🐳 WHALE").sum(),     "Whales"),
        (c4, f"${df_f['profit'].sum():,.0f}" if not df_f.empty else "$0", "Profit total"),
    ]:
        col.markdown(f'<div class="metric-card"><div class="metric-value">{val}</div>'
                     f'<div class="metric-label">{label}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="tip-box">'
        '📌 <b>Criterio BOT en leaderboard:</b> opera en ≥10 mercados distintos Y tiene '
        'profit_rate &gt; 10% — señal de estrategia sistemática no humana<br>'
        '📌 <b>WHALE:</b> volumen ≥$10K o profit ≥$2K'
        '</div>', unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🏆 Ranking", "📈 Gráficas"])
    with tab1:
        if df_f.empty:
            st.info("Sin datos con filtros actuales. Baja el mínimo de trades.")
        else:
            d = df_f[["name","type","profit","volume","markets","p_rate"]].copy()
            d.columns = ["Trader","Tipo","Profit ($)","Volumen ($)","Mercados","Profit Rate"]
            d["Profit Rate"] = (d["Profit Rate"]*100).round(1).astype(str)+"%"
            st.dataframe(d, use_container_width=True, hide_index=True,
                column_config={"Profit ($)": st.column_config.NumberColumn(format="$%.2f"),
                               "Volumen ($)": st.column_config.NumberColumn(format="$%.2f")})
            st.download_button("⬇️ CSV", d.to_csv(index=False).encode(), "leaderboard.csv")

    with tab2:
        if not df_f.empty:
            c1, c2 = st.columns(2)
            with c1:
                fig = px.box(df_f, x="type", y="profit", color="type",
                    color_discrete_map={"🤖 BOT":"#00ff9d","🐳 WHALE":"#00b4ff","👤 HUMAN":"#6b7280"},
                    template="plotly_dark")
                fig.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    showlegend=False, margin=dict(t=10,b=10), font_family="Space Mono")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig2 = px.scatter(df_f, x="volume", y="profit", color="type", size="markets",
                    color_discrete_map={"🤖 BOT":"#00ff9d","🐳 WHALE":"#00b4ff","👤 HUMAN":"#6b7280"},
                    template="plotly_dark", hover_data=["name","markets"])
                fig2.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    margin=dict(t=10,b=10), font_family="Space Mono")
                st.plotly_chart(fig2, use_container_width=True)

            top20 = df_f.nlargest(20,"profit")
            fig3  = go.Figure(go.Bar(x=top20["name"], y=top20["profit"],
                marker_color=["#00ff9d" if v>=0 else "#ef4444" for v in top20["profit"]],
                text=["$"+f"{v:,.0f}" for v in top20["profit"]], textposition="outside"))
            fig3.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                font_family="Space Mono", font_color="#9ca3af",
                margin=dict(t=10,b=10), xaxis_tickangle=-35, yaxis_title="Profit ($)")
            st.plotly_chart(fig3, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MODO 2 — TRADES POR MERCADO
# ═══════════════════════════════════════════════════════════════════════════════
else:
    # ── Fetch markets ──
    with st.spinner("Buscando todos los mercados crypto activos…"):
        all_markets = fetch_all_crypto_markets(limit_per_tag=30)

    total_found = len(all_markets)

    if total_found == 0:
        st.error("No se encontraron mercados. Verifica tu conexión.")
        st.stop()

    # Show market selector
    market_options = {
        (m.get("question") or m.get("title") or "?"): m
        for m in all_markets[:50]
    }

    st.markdown(f'<div class="tip-box">✅ Se encontraron <b>{total_found}</b> mercados crypto activos. '
                f'Escaneando los primeros <b>{min(n_markets, total_found)}</b> por volumen.</div>',
                unsafe_allow_html=True)

    with st.expander(f"📋 Ver los {min(50, total_found)} mercados disponibles"):
        for i, (title, m) in enumerate(market_options.items()):
            vol = float(m.get("volume",0) or m.get("volumeClob",0) or 0)
            st.markdown(f"`{i+1:02d}` **{title[:80]}** — Vol: ${vol:,.0f}")

    # ── Scan trades ──
    selected   = all_markets[:n_markets]
    all_dfs    = []
    market_log = []

    prog = st.progress(0, "Iniciando…")
    for i, mkt in enumerate(selected):
        cid   = mkt.get("conditionId") or mkt.get("condition_id") or mkt.get("id") or ""
        title = mkt.get("question") or mkt.get("title") or "?"
        vol   = float(mkt.get("volume",0) or 0)
        prog.progress((i+1)/len(selected), text=f"[{i+1}/{len(selected)}] {title[:55]}…")

        trades = fetch_trades(cid, limit=trades_limit)
        df_w   = analyze_wallets(trades, market_title=title)

        n_wallets = len(df_w) if not df_w.empty else 0
        n_bots    = int((df_w["type"]=="🤖 BOT").sum()) if not df_w.empty else 0
        n_whales  = int((df_w["type"]=="🐳 WHALE").sum()) if not df_w.empty else 0

        market_log.append({
            "Mercado": title[:65], "Vol $": f"${vol:,.0f}",
            "Trades": len(trades), "Wallets": n_wallets,
            "Bots": n_bots, "Whales": n_whales,
        })

        if not df_w.empty:
            all_dfs.append(df_w)
        time.sleep(0.3)

    prog.empty()

    if not all_dfs:
        st.warning("No se obtuvieron trades. La Data API puede estar limitando. "
                   "Prueba reducir el número de mercados o usa el modo Leaderboard.")
        st.dataframe(pd.DataFrame(market_log), use_container_width=True, hide_index=True)
        st.stop()

    df_all = pd.concat(all_dfs, ignore_index=True)

    # Apply filters
    df_f = df_all[
        (df_all["trades"] >= min_trades) &
        (df_all["type"].isin(show_types))
    ].copy()

    # ── Metrics ──
    c1, c2, c3, c4 = st.columns(4)
    total_wallets = df_all["wallet"].nunique()
    n_bots_total  = (df_all[df_all["trades"] >= min_trades]["type"] == "🤖 BOT").sum()
    n_whal_total  = (df_all[df_all["trades"] >= min_trades]["type"] == "🐳 WHALE").sum()
    total_vol     = df_all["volume_$"].sum()

    for col, val, label in [
        (c1, total_wallets,        "Wallets únicas"),
        (c2, n_bots_total,         "Bots detectados"),
        (c3, n_whal_total,         "Whales"),
        (c4, f"${total_vol:,.0f}", "Volumen total"),
    ]:
        col.markdown(f'<div class="metric-card"><div class="metric-value">{val}</div>'
                     f'<div class="metric-label">{label}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="tip-box">'
        '📌 <b>Bot score</b> (máx ~8 pts): '
        '+2 si CV&lt;0.4 (tamaños muy fijos) · +1 si CV&lt;0.6 · '
        '+2 si trades/h&gt;2 · +1 si trades/h&gt;0.5 · '
        '+1 si buys≈sells (market maker) · +1 si ≥30 trades · '
        '+1 si ≥10 trades → <b>BOT si score ≥ 4</b>'
        '</div>', unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["🔍 Wallets detectadas", "📈 Análisis", "🗂️ Resumen por mercado"])

    with tab1:
        st.markdown('<div class="section-title">todas las wallets detectadas — ordenadas por bot score</div>',
                    unsafe_allow_html=True)
        if df_f.empty:
            st.info("Sin wallets con los filtros actuales. Baja el mínimo de trades a 1.")
        else:
            disp = df_f[["wallet","type","bot_score","trades","volume_$",
                          "size_cv","trades/h","bs_ratio","market"]].copy()
            disp.columns = ["Wallet","Tipo","Bot Score","# Trades","Volumen $",
                             "CV Tamaño","Trades/Hora","Buy/Sell Ratio","Mercado"]
            disp["Wallet"] = disp["Wallet"].str[:14] + "…"

            # Color coding via column config
            st.dataframe(disp, use_container_width=True, hide_index=True,
                column_config={
                    "Bot Score":      st.column_config.ProgressColumn(
                        "Bot Score", min_value=0, max_value=8, format="%d"),
                    "Volumen $":      st.column_config.NumberColumn(format="$%.2f"),
                    "Trades/Hora":    st.column_config.NumberColumn(format="%.2f"),
                    "CV Tamaño":      st.column_config.NumberColumn(
                        format="%.3f", help="<0.4 = muy consistente → probable bot"),
                    "Buy/Sell Ratio": st.column_config.NumberColumn(
                        format="%.2f", help="Cerca de 1.0 = market maker"),
                })
            st.download_button("⬇️ CSV completo",
                               df_f.to_csv(index=False).encode(), "wallets_bots.csv")

    with tab2:
        if not df_f.empty:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown('<div class="section-title">trades vs volumen</div>', unsafe_allow_html=True)
                fig = px.scatter(df_f, x="trades", y="volume_$", color="type",
                    size="bot_score",
                    color_discrete_map={"🤖 BOT":"#00ff9d","🐳 WHALE":"#00b4ff","👤 HUMAN":"#6b7280"},
                    template="plotly_dark", hover_data=["wallet","size_cv","trades/h"],
                    labels={"trades":"# Trades","volume_$":"Volumen $"})
                fig.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    margin=dict(t=10,b=10), font_family="Space Mono")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                st.markdown('<div class="section-title">CV Tamaño (consistencia) por tipo</div>',
                            unsafe_allow_html=True)
                fig2 = px.violin(df_f, x="type", y="size_cv", color="type", box=True,
                    color_discrete_map={"🤖 BOT":"#00ff9d","🐳 WHALE":"#00b4ff","👤 HUMAN":"#6b7280"},
                    template="plotly_dark",
                    labels={"size_cv":"CV Tamaño","type":"Tipo"})
                fig2.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                    showlegend=False, margin=dict(t=10,b=10), font_family="Space Mono")
                st.plotly_chart(fig2, use_container_width=True)

            # Bot score distribution
            st.markdown('<div class="section-title">distribución de bot score</div>',
                        unsafe_allow_html=True)
            fig3 = px.histogram(df_f, x="bot_score", color="type", nbins=9,
                color_discrete_map={"🤖 BOT":"#00ff9d","🐳 WHALE":"#00b4ff","👤 HUMAN":"#6b7280"},
                template="plotly_dark", labels={"bot_score":"Bot Score","count":"Wallets"})
            fig3.add_vline(x=4, line_dash="dash", line_color="#ef4444",
                          annotation_text="Umbral BOT (4)", annotation_position="top right")
            fig3.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827",
                margin=dict(t=10,b=10), font_family="Space Mono")
            st.plotly_chart(fig3, use_container_width=True)

    with tab3:
        st.markdown('<div class="section-title">resumen por mercado escaneado</div>',
                    unsafe_allow_html=True)
        df_log = pd.DataFrame(market_log)
        st.dataframe(df_log, use_container_width=True, hide_index=True)


# ─── Footer ───────────────────────────────────────────────────────────────────
st.markdown("""
<div style='text-align:center;margin-top:3rem;font-family:Space Mono,monospace;
font-size:.55rem;color:#1f2937;letter-spacing:.1em'>
POLYTRACKER · DATA API + GAMMA API · NO ES ASESORAMIENTO FINANCIERO
</div>
""", unsafe_allow_html=True)
