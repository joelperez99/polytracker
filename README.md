# PolyTracker 🔍

**Bot & Whale Detector para mercados BTC/Crypto de Polymarket**

Analiza wallets activas en mercados de predicción de Bitcoin/Crypto en Polymarket, calcula win rate y PnL estimado, y clasifica automáticamente cada wallet como Bot, Whale o Humano.

---

## 🚀 Instalación rápida

```bash
# 1. Clona el repo
git clone https://github.com/joelperez99/polytracker
cd polytracker

# 2. Instala dependencias
pip install -r requirements.txt

# 3. Corre la app
streamlit run app.py
```

La app abre en `http://localhost:8501`

---

## 🧠 Cómo funciona

1. **Obtiene mercados** BTC/Crypto activos desde la Gamma API de Polymarket
2. **Descarga trades** recientes de cada mercado vía CLOB API
3. **Agrupa por wallet** y calcula:
   - Win rate (% de trades en dirección ganadora)
   - Volumen total operado
   - PnL estimado
   - Número de trades
4. **Clasifica** cada wallet:
   - 🤖 **BOT** → win rate ≥ 65% con al menos 20 trades
   - 🐳 **WHALE** → volumen total ≥ $5,000
   - 👤 **HUMAN** → resto

---

## ⚙️ Configuración (sidebar)

| Parámetro | Descripción |
|-----------|-------------|
| Mínimo de trades | Filtra wallets con poca actividad |
| Win rate mínimo | Umbral de calidad |
| Tipo de wallet | BOT / WHALE / HUMAN |
| Mercados a escanear | Cuántos mercados analizar (más = más lento) |
| Auto-refresh | Refresca cada ~2 min |

---

## 📊 Pestañas

- **Wallets Ranking** — tabla filtrable con descarga CSV
- **PnL & Volumen** — box plot, scatter y bar chart de PnL
- **Mercados escaneados** — listado de mercados y trades obtenidos

---

## ⚠️ Disclaimer

Los datos provienen directamente de la API pública de Polymarket.  
El PnL es una **estimación simplificada**, no un cálculo exacto de contabilidad.  
Esto no es asesoramiento financiero.

---

## 🛠️ Stack

- [Streamlit](https://streamlit.io) — interfaz web
- [Plotly](https://plotly.com) — gráficas interactivas
- [Polymarket Gamma API](https://gamma-api.polymarket.com) — mercados
- [Polymarket CLOB API](https://clob.polymarket.com) — trades
