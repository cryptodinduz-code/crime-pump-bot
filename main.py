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
    return "🚀 Crime Bot v9.6 is ALIVE!"

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

# Main Bot Loop
def bot_loop():
    send_telegram("🚀 <b>Crime Bot v9.6 Started</b>\nScore minimum = 60")

    while True:
        print(f"🔍 Scanning at {datetime.datetime.utcnow()}")

        exchanges = {
            "BloFin": ccxt.blofin({"enableRateLimit": True}),
            "MEXC": ccxt.mexc({"enableRateLimit": True}),
            "Binance": ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        }

        for name, ex in exchanges.items():
            try:
                tickers = ex.fetch_tickers()
                print(f"  → {name}: Loaded {len(tickers)} tickers")

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

                    # Score
                    score = 0
                    if abs(funding) > 0.0003: score += 32
                    if vol_spike: score += 28
                    if volume < 50_000_000: score += 12

                    if score >= ALERT_MIN_SCORE:
                        send_telegram(f"🚨 CRIME ALERT (Score: {score}) — {symbol} on {name}")

            except Exception as e:
                print(f"  Error on {name}: {e}")

        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
