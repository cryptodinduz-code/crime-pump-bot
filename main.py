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
    return "🚀 Crime Bot v9.5 - Better Logging"

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

def bot_loop():
    send_telegram("🚀 <b>Crime Bot Started</b>\nBetter logging active")

    while True:
        print(f"\n🔍 === NEW SCAN STARTED at {datetime.datetime.utcnow()} ===")
        
        total_checked = 0
        alerts_fired = 0

        exchanges = {
            "BloFin": ccxt.blofin({"enableRateLimit": True}),
            "MEXC": ccxt.mexc({"enableRateLimit": True}),
            "Binance": ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        }

        for name, ex in exchanges.items():
            try:
                tickers = ex.fetch_tickers()
                checked = 0
                for symbol, t in list(tickers.items())[:120]:
                    if not symbol.endswith("USDT"): 
                        continue
                    checked += 1
                    total_checked += 1

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
                    try:
                        candles = ex.fetch_ohlcv(symbol, '5m', limit=6)
                        vols = [c[5] for c in candles]
                        avg = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
                        vol_ratio = vols[-1] / avg
                        vol_spike = vol_ratio >= 4.5
                    except:
                        pass

                    score = 0
                    if abs(funding) > 0.0003: score += 32
                    if vol_spike: score += 28
                    if volume < 50_000_000: score += 12

                    if score >= ALERT_MIN_SCORE:
                        alerts_fired += 1
                        send_telegram(f"🚨 CRIME ALERT (Score: {score}) — {symbol} on {name}")

                print(f"  ✅ {name}: checked {checked} USDT pairs")

            except Exception as e:
                print(f"  ❌ Error on {name}: {e}")

        print(f"📊 Scan finished → Checked {total_checked} pairs | Fired {alerts_fired} alerts\n")

        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
