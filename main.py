import ccxt
import os
import time
import json
import datetime
import threading
import requests
from collections import deque
from flask import Flask

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP_SECONDS = 60
MEMORY_FILE = "memory.json"
ALERT_MIN_SCORE = 55   # Lowered for testing

app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Crime Bot v8.3 FULL is ALIVE! (BloFin + MEXC + Binance)"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# Global for /recap
current_top_signals = []

def send_telegram(text, chat_id=None):
    if not TG_ENABLED: return
    target = chat_id or TELEGRAM_CHAT_ID
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": target, "text": text, "parse_mode": "HTML"})
    except:
        pass

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

# /recap Command
def check_for_commands():
    offset = 0
    while True:
        try:
            resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=10")
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                if msg.get("text") == "/recap":
                    chat_id = msg["chat"]["id"]
                    if current_top_signals:
                        recap = "📊 <b>Current Top Crime Signals</b>\n\n"
                        for s in sorted(current_top_signals, key=lambda x: x.get('score',0), reverse=True)[:10]:
                            recap += f"• <b>{s['symbol']}</b> on {s['exchange']} — Score <b>{s.get('score',0)}</b>\n"
                        send_telegram(recap, chat_id)
                    else:
                        send_telegram("No strong signals right now.", chat_id)
        except:
            pass
        time.sleep(5)

# Main Bot Loop
def bot_loop():
    send_telegram("🚀 <b>Crime Bot v8.3 FULL Started</b>\nReal scanning from BloFin, MEXC, Binance")

    threading.Thread(target=check_for_commands, daemon=True).start()

    while True:
        print(f"🔍 Scanning at {datetime.datetime.utcnow()}")
        current_top_signals.clear()

        exchanges = {
            "BloFin": ccxt.blofin({"enableRateLimit": True}),
            "MEXC": ccxt.mexc({"enableRateLimit": True}),
            "Binance": ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        }

        for name, ex in exchanges.items():
            try:
                tickers = ex.fetch_tickers()
                for symbol, t in list(tickers.items())[:120]:
                    if not symbol.endswith("USDT"): 
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

                    # Order Book
                    ob_buy_pressure, _ = analyze_order_book(ex, symbol)

                    # Score
                    score = 0
                    if abs(funding) > 0.0003: score += 28
                    if vol_spike: score += 25
                    if oi_chg > 18: score += 22
                    if ob_buy_pressure: score += 18
                    if t.get('quoteVolume', 0) < 40000000: score += 10

                    if score >= ALERT_MIN_SCORE:
                        alert = {"symbol": symbol, "exchange": name, "score": score}
                        current_top_signals.append(alert)
                        send_telegram(f"🚨 CRIME ALERT (Score: {score}) - {symbol} on {name}")

            except:
                continue

        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
