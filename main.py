import ccxt
import os
import time
import datetime
import threading
import requests
from flask import Flask

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP_SECONDS = 60
ALERT_MIN_SCORE = 60

app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Crime Bot v9.5 FULL is ALIVE!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

def send_telegram(text):
    if not TG_ENABLED: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except:
        pass

def get_liq_heat(exchange, symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, '5m', limit=12)
        high_low = sum(c[2] - c[3] for c in candles)
        return high_low * 0.4
    except:
        return 0

def analyze_order_book(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=20)
        bid_vol = sum(lvl[1] for lvl in ob.get("bids", []))
        ask_vol = sum(lvl[1] for lvl in ob.get("asks", []))
        if ask_vol == 0: return False, 0
        ratio = bid_vol / ask_vol
        return ratio > 1.8, round(ratio, 2)
    except:
        return False, 0

# Main Bot Loop
def bot_loop():
    send_telegram("🚀 <b>Crime Bot v9.5 FULL Started</b>\nScore minimum = 60")

    prev_oi = {}

    while True:
        print(f"\n🔍 === SCAN STARTED at {datetime.datetime.utcnow()} ===")
        alerts_fired = 0

        exchanges = {
            "BloFin": ccxt.blofin({"enableRateLimit": True}),
            "MEXC": ccxt.mexc({"enableRateLimit": True}),
            "Binance": ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        }

        for name, ex in exchanges.items():
            try:
                tickers = ex.fetch_tickers()
                print(f"  → {name}: Found {len(tickers)} tickers")

                for symbol, t in list(tickers.items())[:120]:
                    if not symbol.endswith("USDT"): 
                        continue

                    volume = t.get('quoteVolume', 0)
                    if volume < 6_000_000: 
                        continue

                    # Funding
                    funding = 0
                    try:
                        fr = ex.fetch_funding_rate(symbol)
                        funding = fr.get('fundingRate', 0)
                    except:
                        pass

                    # Volume Spike
                    vol_spike = False
                    vol_ratio = 0
                    try:
                        candles = ex.fetch_ohlcv(symbol, '5m', limit=6)
                        vols = [c[5] for c in candles]
                        avg = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
                        vol_ratio = vols[-1] / avg
                        vol_spike = vol_ratio >= 4.5
                    except:
                        pass

                    # OI
                    oi_chg = 0
                    try:
                        oi = ex.fetch_open_interest(symbol)
                        oi_val = oi.get('openInterestAmount') or oi.get('openInterest')
                        key = f"{name}:{symbol}"
                        if key in prev_oi and prev_oi[key] > 0:
                            oi_chg = ((oi_val - prev_oi[key]) / prev_oi[key]) * 100
                        prev_oi[key] = oi_val
                    except:
                        pass

                    # Order Book + Liq Heat
                    ob_buy_pressure, _ = analyze_order_book(ex, symbol)
                    liq_heat = get_liq_heat(ex, symbol)

                    # Score
                    score = 0
                    if abs(funding) > 0.0003: score += 30
                    if vol_spike: score += 28
                    if oi_chg > 18: score += 22
                    if ob_buy_pressure: score += 18
                    if liq_heat > 100000: score += 15
                    if volume < 50_000_000: score += 10

                    if score >= ALERT_MIN_SCORE:
                        alerts_fired += 1
                        send_telegram(f"""🚨 <b>CRIME ALERT</b> (Score: {score}/100)
🔥 {symbol} on {name}

Price: ${t.get('last', 0):.6g}
24h Futures Vol: ${volume/1000000:.2f}M
Funding Rate: {funding*100:+.4f}%
Liquidation Heat: ${liq_heat/1000000:.1f}M

Early Signals:
• Extreme Funding
• Volume Spike {vol_ratio:.1f}x
• OI Growth
• {'Strong Buy Wall' if ob_buy_pressure else 'Neutral'}
• {'Liquidation Pressure' if liq_heat > 100000 else ''}""")

            except Exception as e:
                print(f"  Error on {name}: {e}")
