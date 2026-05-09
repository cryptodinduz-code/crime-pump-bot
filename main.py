import ccxt
import os
import time
import datetime
import threading
import requests
from flask import Flask
from collections import defaultdict

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP_SECONDS = 60
ALERT_MIN_SCORE = 60

MAX_PAIRS_PER_EXCHANGE = 400

MIN_VOLUME = 6_000_000
MAX_VOLUME = 150_000_000
BLOFIN_MIN_VOLUME = 2_000_000

LOW_VOLUME_BONUS_LIMIT = 30_000_000

# STOCK + COMMODITY FILTER (updated)
STOCK_KEYWORDS = ["AMD", "NVDA", "NVIDIA", "TSLA", "AAPL", "META", "AMZN", "GOOGL", "MSFT", "NFLX", 
                  "AMDSTOCK", "NVIDIASTOCK", "SNDKSTOCK", "IRENSTOCK", "MUSTOCK", "STOCK",
                  "USOIL", "UKOIL", "US30", "SPX500", "NAS100", "XAUT", "PAXG", "SILVER"]

# MAJOR PAIRS FILTER
MAJOR_PAIRS = ["BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "AVAX", "TRX", "SHIB"]


# =========================================================
# FLASK + TELEGRAM
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🚀 Alpha Hunter Bot ONLINE"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def send_telegram(text):
    if not TG_ENABLED: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except: pass


# =========================================================
# HELPERS (All previous features kept)
# =========================================================

def get_volume_spike(exchange, symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=8)
        if len(candles) < 6: return False, 0
        volumes = [c[5] for c in candles if c[5] is not None]
        avg = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 0
        ratio = volumes[-1] / avg if avg > 0 else 0
        return ratio >= 3.5, ratio
    except:
        return False, 0

def get_liq_heat(exchange, symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=12)
        total = 0
        for c in candles:
            if c[4] == 0: continue
            rng = (c[2] - c[3]) / c[4] * 100
            total += rng * (c[5] or 0)
        return total
    except:
        return 0

def analyze_order_book(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=20)
        bid_vol = sum(b[1] for b in ob.get("bids", []))
        ask_vol = sum(a[1] for a in ob.get("asks", []))
        ratio = bid_vol / ask_vol if ask_vol > 0 else 0
        return ratio > 1.45, ratio
    except:
        return False, 0

def get_open_interest_change(exchange, symbol, prev_oi):
    try:
        oi = exchange.fetch_open_interest(symbol)
        oi_val = oi.get("openInterestAmount") or oi.get("openInterest") or oi.get("openInterestValue")
        if not oi_val: return 0
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


def calculate_score(funding, vol_ratio, oi_change, ob_ratio, liq_heat, volume):
    score = 0
    reasons = []
    if abs(funding) > 0.00015: score += 20; reasons.append("Funding Extreme")
    if vol_ratio >= 3.5: score += 25; reasons.append(f"Volume Spike {vol_ratio:.1f}x")
    if oi_change > 5: score += 20; reasons.append(f"OI +{oi_change:.1f}%")
    if ob_ratio > 1.45: score += 15; reasons.append(f"Buy Pressure {ob_ratio:.2f}")
    if liq_heat > 500: score += 10; reasons.append("Liquidation Pressure")
    if volume < LOW_VOLUME_BONUS_LIMIT: score += 10; reasons.append("Midcap Momentum")
    return score, reasons


# =========================================================
# MAIN LOOP
# =========================================================

def bot_loop():
    send_telegram("🚀 <b>Alpha Hunter Bot v18</b>\nStricter + Stock Filter Updated")

    prev_oi = {}
    last_alert = defaultdict(lambda: 0)

    while True:
        print(f"\n=== SCAN STARTED {datetime.datetime.now(datetime.UTC)} ===")
        alerts_fired = 0
        now = time.time()

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

                sorted_tickers = sorted(tickers.items(), key=lambda x: float(x[1].get("quoteVolume") or 0), reverse=True)

                scanned = 0
                processed = 0

                for symbol, ticker in sorted_tickers:
                    if scanned >= MAX_PAIRS_PER_EXCHANGE: break
                    if "USDT" not in symbol: continue

                    upper = symbol.upper()
                    if any(k in upper for k in STOCK_KEYWORDS): continue
                    if any(m in upper for m in MAJOR_PAIRS) and name == "MEXC": continue

                    scanned += 1

                    volume = float(ticker.get("quoteVolume") or 0)
                    if name == "BloFin" and volume == 0:
                        volume = float(ticker.get("baseVolume") or 0) * 1000

                    if volume < MIN_VOLUME or volume > MAX_VOLUME:
                        continue

                    processed += 1

                    last_price = ticker.get("last") or 0
                    funding = get_funding(ex, symbol)
                    _, vol_ratio = get_volume_spike(ex, symbol)
                    oi_change = get_open_interest_change(ex, symbol, prev_oi)
                    _, ob_ratio = analyze_order_book(ex, symbol)
                    liq_heat = get_liq_heat(ex, symbol)

                    print(
                        f"✅ {name} | {symbol} | "
                        f"Vol=${volume/1e6:.2f}M | Funding={funding:.5f} | "
                        f"Spike={vol_ratio:.2f}x | OI={oi_change:.2f}% | "
                        f"OB={ob_ratio:.2f} | Liq={liq_heat:.1f}"
                    )

                    score, reasons = calculate_score(funding, vol_ratio, oi_change, ob_ratio, liq_heat, volume)

                    if score >= ALERT_MIN_SCORE:
                        if now - last_alert[symbol] < 2700:  # 45 minutes dedup
                            continue
                        last_alert[symbol] = now

                        alerts_fired += 1
                        reason_text = "\n".join([f"• {r}" for r in reasons])
                        msg = f"""
🚨 <b>ALPHA SIGNAL</b>

🔥 <b>{symbol}</b>
🏦 Exchange: {name}

💰 Price: ${last_price:.6g}
📊 Volume: ${volume / 1e6:.2f}M

📈 Funding: {funding*100:+.4f}%
📦 OI Change: {oi_change:+.2f}%
📚 Orderbook: {ob_ratio:.2f}
⚡ Volume Spike: {vol_ratio:.2f}x

🎯 Score: <b>{score}/100</b>

{reason_text}
"""
                        print(msg)
                        send_telegram(msg)
                        time.sleep(1)

                print(f"  → {name}: Scanned {scanned} | Processed {processed}")

            except Exception as e:
                print(f"❌ {name} ERROR: {e}")

        print(f"\n✅ Scan completed | Alerts fired: {alerts_fired}\n")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
