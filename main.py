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
    return "🚀 Crime Bot v10.6 - Volume Spike 2x"

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

def analyze_order_book(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=20)
        bid_vol = sum(lvl[1] for lvl in ob.get("bids", []))
        ask_vol = sum(lvl[1] for lvl in ob.get("asks", []))
        if ask_vol == 0:
            return False
        ratio = bid_vol / ask_vol
        return ratio > 1.75
    except:
        return False

# Main Bot Loop
def bot_loop():
    send_telegram("🚀 <b>Crime Bot v10.6 Started</b>\nVolume Spike lowered to 2.0x")

    while True:
        print(f"\n🔍 SCAN STARTED at {datetime.datetime.utcnow()}")

        exchanges = {
            "BloFin": ccxt.blofin({"enableRateLimit": True}),
            "MEXC": ccxt.mexc({"enableRateLimit": True})
        }

        alerts_fired = 0

        for name, ex in exchanges.items():
            try:
                tickers = ex.fetch_tickers()
                print(f"  → {name}: Loaded {len(tickers)} tickers")

                for symbol, t in list(tickers.items())[:150]:
                    if not symbol.endswith("USDT"): 
                        continue

                    volume = t.get('quoteVolume') or 0
                    if volume < 4_000_000: 
                        continue

                    # Funding
                    funding = 0
                    try:
                        fr = ex.fetch_funding_rate(symbol)
                        funding = fr.get('fundingRate', 0) or 0
                    except:
                        pass

                    # Volume Spike - LOWERED TO 2.0x as requested
                    vol_spike = False
                    vol_ratio = 0
                    try:
                        candles = ex.fetch_ohlcv(symbol, '5m', limit=6)
                        vols = [c[5] for c in candles if c[5] is not None]
                        if len(vols) > 3:
                            avg = sum(vols[:-1]) / len(vols[:-1])
                            vol_ratio = vols[-1] / avg if avg > 0 else 0
                            vol_spike = vol_ratio >= 2.0      # ← CHANGED
                    except:
                        pass

                    # Order Book
                    buy_pressure = analyze_order_book(ex, symbol)

                    # Score
                    score = 0
                    if abs(funding) > 0.0003: score += 32
                    if vol_spike: score += 28
                    if buy_pressure: score += 20
                    if volume < 50_000_000: score += 12

                    if score >= ALERT_MIN_SCORE:
                        alerts_fired += 1
                        send_telegram(f"""🚨 <b>CRIME ALERT</b> (Score: {score}/100)
🔥 {symbol} on {name}

Volume: ${volume/1_000_000:.1f}M
Funding: {funding*100:+.4f}%
Volume Spike: {vol_ratio:.1f}x
Buy Pressure: {'✅ STRONG' if buy_pressure else 'Normal'}""")

            except Exception as e:
                print(f"  Error on {name}: {e}")

        print(f"📊 Scan finished → Fired {alerts_fired} alerts\n")
        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
