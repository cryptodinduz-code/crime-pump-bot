import ccxt
import os
import time
import datetime
import threading
import requests
from flask import Flask


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP_SECONDS = 60
ALERT_MIN_SCORE = 60

MAX_PAIRS_PER_EXCHANGE = 400

MIN_VOLUME = 3_000_000
LOW_VOLUME_BONUS_LIMIT = 30_000_000

# STOCK FILTER
STOCK_KEYWORDS = ["AMD", "NVDA", "NVIDIA", "TSLA", "AAPL", "META", "AMZN", "GOOGL", "MSFT", "NFLX", 
                  "AMDSTOCK", "NVIDIASTOCK", "SNDKSTOCK", "IRENSTOCK", "MUSTOCK", "STOCK"]

# MAJOR PAIRS FILTER
MAJOR_PAIRS = ["BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "AVAX", "TRX", "SHIB"]


# =========================================================
# FLASK KEEPALIVE
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🚀 Alpha Hunter Bot ONLINE"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):
    if not TG_ENABLED:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except:
        pass


# =========================================================
# HELPERS (unchanged)
# =========================================================

def get_volume_spike(exchange, symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=8)
        if len(candles) < 6:
            return False, 0
        volumes = [c[5] for c in candles if c[5] is not None]
        avg = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 0
        ratio = volumes[-1] / avg if avg > 0 else 0
        return ratio >= 3.0, ratio
    except:
        return False, 0

def get_liq_heat(exchange, symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=12)
        total = 0
        for c in candles:
            if c[4] == 0: continue
            rng = (c[2] - c[3]) / c[4] * 100
            total += rng * c[5]
        return total
    except:
        return 0

def analyze_order_book(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=20)
        bids_vol = sum(b[1] for b in ob.get("bids", []))
        asks_vol = sum(a[1] for a in ob.get("asks", []))
        ratio = bids_vol / asks_vol if asks_vol > 0 else 0
        return ratio > 1.4, ratio
    except:
        return False, 0

def get_open_interest_change(exchange, symbol, prev_oi):
    try:
        oi = exchange.fetch_open_interest(symbol)
        oi_val = oi.get("openInterestAmount") or oi.get("openInterest") or oi.get("openInterestValue")
        if oi_val is None: return 0
        oi_val = float(oi_val)
        key = f"{exchange.id}:{symbol}"
        change = ((oi_val - prev_oi.get(key, 0)) / prev_oi.get(key, 1)) * 100 if key in prev_oi else 0
        prev_oi[key] = oi_val
        return change
    except:
        return 0

def get_funding(exchange, symbol):
    try:
        fr = exchange.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate", 0))
    except:
        return 0


# =========================================================
# SCORING
# =========================================================

def calculate_score(funding, vol_ratio, oi_change, ob_ratio, liq_heat, volume):
    score = 0
    reasons = []

    if abs(funding) > 0.00015:
        score += 20
        reasons.append("Funding Extreme")
    if vol_ratio >= 3.0:
        score += 25
        reasons.append(f"Volume Spike {vol_ratio:.1f}x")
    if oi_change > 5:
        score += 20
        reasons.append(f"OI +{oi_change:.1f}%")
    if ob_ratio > 1.4:
        score += 15
        reasons.append(f"Buy Pressure {ob_ratio:.2f}")
    if liq_heat > 500:
        score += 10
        reasons.append("Liquidation Pressure")
    if volume < LOW_VOLUME_BONUS_LIMIT:
        score += 10
        reasons.append("Midcap Momentum")

    return score, reasons


# =========================================================
# MAIN BOT LOOP
# =========================================================

def bot_loop():
    send_telegram("🚀 <b>Alpha Hunter Bot v11</b>\nBloFin Aggressive Fix")

    prev_oi = {}

    while True:
        print(f"\n=== SCAN STARTED {datetime.datetime.utcnow()} ===")
        alerts_fired = 0

        exchanges = {
            "MEXC": ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": "swap"}}),
            "BloFin": ccxt.blofin({"enableRateLimit": True})
        }

        for name, ex in exchanges.items():
            print(f"\n🔍 Scanning {name}")

            try:
                ex.load_markets()
                tickers = ex.fetch_tickers()
                print(f"  → Loaded {len(tickers)} tickers")

                sorted_tickers = sorted(
                    tickers.items(),
                    key=lambda x: float(x[1].get("quoteVolume") or 0),
                    reverse=True
                )

                scanned = 0
                processed = 0
                passed_filters = 0

                for symbol, ticker in sorted_tickers:
                    if scanned >= MAX_PAIRS_PER_EXCHANGE:
                        break

                    if "USDT" not in symbol:
                        continue

                    upper = symbol.upper()
                    if any(k in upper for k in STOCK_KEYWORDS):
                        continue
                    if any(m in upper for m in MAJOR_PAIRS):
                        continue

                    scanned += 1

                    volume = float(ticker.get("quoteVolume") or 0)
                    if volume < MIN_VOLUME:
                        continue

                    processed += 1

                    # For BloFin we skip strict market check
                    last_price = ticker.get("last") or 0

                    funding = get_funding(ex, symbol)
                    _, vol_ratio = get_volume_spike(ex, symbol)
                    oi_change = get_open_interest_change(ex, symbol, prev_oi)
                    _, ob_ratio = analyze_order_book(ex, symbol)
                    liq_heat = get_liq_heat(ex, symbol)

                    print(f"   {name} | {symbol} | Vol=${volume/1e6:.1f}M | Spike={vol_ratio:.1f}x | Fund={funding:.5f}")

                    score, reasons = calculate_score(funding, vol_ratio, oi_change, ob_ratio, liq_heat, volume)

                    if score >= ALERT_MIN_SCORE:
                        alerts_fired += 1
                        reason_text = "\n".join([f"• {r}" for r in reasons])
                        msg = f"""
🚨 <b>ALPHA SIGNAL</b>

🔥 <b>{symbol}</b> on {name}
Score: <b>{score}/100</b>

{reason_text}
"""
                        print(msg)
                        send_telegram(msg)

                print(f"  → {name} Summary: Scanned={scanned} | Passed Volume={processed} | Alerts={alerts_fired}")

            except Exception as e:
                print(f"❌ {name} CRITICAL ERROR: {e}")

        print(f"\n✅ Full scan done | Total alerts: {alerts_fired}")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
