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
    return "🚀 Crime Bot v9.1 - Clean & Debugging"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# Global for recap
current_top_signals = []

def send_telegram(text):
    if not TG_ENABLED: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except:
        pass

# Main Loop
def bot_loop():
    send_telegram("🚀 <b>Crime Bot v9.1 Started</b>\nClean version with debug")

    prev_oi = {}  # moved inside function

    while True:
        print(f"🔍 Starting scan at {datetime.datetime.utcnow()}")
        current_top_signals.clear()

        exchanges = {
            "BloFin": ccxt.blofin({"enableRateLimit": True}),
            "MEXC": ccxt.mexc({"enableRateLimit": True}),
            "Binance": ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        }

        for name, ex in exchanges.items():
            print(f"  → Scanning {name}...")
            try:
                tickers = ex.fetch_tickers()
                print(f"    Found {len(tickers)} tickers on {name}")

                for symbol, t in list(tickers.items())[:80]:
                    if not symbol.endswith("USDT"):
                        continue

                    score = 45  # base
                    signals = []

                    # Funding
                    try:
                        fr = ex.fetch_funding_rate(symbol)
                        funding = fr.get('fundingRate', 0)
                        if abs(funding) > 0.0003:
                            score += 28
                            signals.append("Extreme Funding")
                    except:
                        pass

                    # Volume Spike
                    try:
                        candles = ex.fetch_ohlcv(symbol, '5m', limit=6)
                        vols = [c[5] for c in candles]
                        avg = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
                        vol_ratio = vols[-1] / avg
                        if vol_ratio >= 4.5:
                            score += 25
                            signals.append(f"Vol Spike {vol_ratio:.1f}x")
                    except:
                        pass

                    if score >= ALERT_MIN_SCORE:
                        alert = {"symbol": symbol, "exchange": name, "score": score}
                        current_top_signals.append(alert)
                        send_telegram(f"🚨 CRIME ALERT (Score: {score}) — {symbol} on {name}")

            except Exception as e:
                print(f"    Error on {name}: {e}")

        print(f"✅ Scan finished - {len(current_top_signals)} signals found")
        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
