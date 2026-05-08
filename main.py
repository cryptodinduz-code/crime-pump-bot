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
ALERT_MIN_SCORE = 68

# Weights
W_FUNDING = 28
W_VOL_SPIKE = 25
W_OI_SURGE = 22
W_LIQ_HEAT = 20
W_LS_RATIO = 18
W_FS_RATIO = 12
W_LOW_CAP = 10
W_ORDER_BOOK = 18

app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Crime Bot v8.1 FULL (BloFin + MEXC + Binance + /recap) is ALIVE!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# Memory
prev_oi = {}
vol_history = {}
alerted = {}
daily_signals = []
current_top_signals = []

def load_memory():
    global prev_oi, vol_history, alerted
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f:
                data = json.load(f)
            prev_oi = data.get("prev_oi", {})
            vol_history = {k: deque(v, maxlen=168) for k, v in data.get("vol_history", {}).items()}
            alerted = data.get("alerted", {})
        except:
            pass

def save_memory():
    try:
        data = {
            "prev_oi": prev_oi,
            "vol_history": {k: list(v) for k, v in vol_history.items()},
            "alerted": alerted
        }
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f)
    except:
        pass

# Exchanges
exchanges = {
    "BloFin": ccxt.blofin({"enableRateLimit": True}),
    "MEXC": ccxt.mexc({"enableRateLimit": True}),
    "Binance": ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
}

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

def calculate_score(data, ob_buy_pressure=False):
    score = 0
    signals = []
    if abs(data.get('funding', 0)) > 0.0003:
        score += W_FUNDING
        signals.append(f"Extreme Funding")
    if data.get('vol_spike', False):
        score += W_VOL_SPIKE
        signals.append(f"Volume Spike {data.get('vol_ratio',0):.1f}x")
    if data.get('oi_chg', 0) > 18:
        score += W_OI_SURGE
        signals.append(f"OI Surge")
    if data.get('ls_ratio', 0) > 1.8:
        score += W_LS_RATIO
        signals.append(f"L/S Ratio")
    if ob_buy_pressure:
        score += W_ORDER_BOOK
        signals.append("Strong Buy Wall")
    if data.get('futures_vol', 0) < 40_000_000:
        score += W_LOW_CAP
        signals.append("Low Cap")
    return min(100, score), signals

# /recap Command
def check_for_commands():
    offset = 0
    while True:
        try:
            resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=10")
            data = resp.json()
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = msg.get("chat", {}).get("id")
                    if text == "/recap" and chat_id:
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

# Daily Summary
def daily_summary():
    while True:
        now = datetime.datetime.utcnow()
        if now.hour == 0 and now.minute < 5 and daily_signals:
            top = sorted(daily_signals, key=lambda x: x.get('score',0), reverse=True)[:5]
            msg = "📊 <b>Daily Crime Summary (v8.1)</b>\n\n"
            for s in top:
                msg += f"• {s['symbol']} on {s['exchange']} — Score {s.get('score',0)}\n"
            send_telegram(msg)
            daily_signals.clear()
        time.sleep(60)

# Main Bot Loop
def bot_loop():
    load_memory()
    send_telegram("🚀 <b>Crime Bot v8.1 FULL Online</b>\nBloFin + MEXC + Binance + Deep Order Book + /recap")

    threading.Thread(target=check_for_commands, daemon=True).start()
    threading.Thread(target=daily_summary, daemon=True).start()

    while True:
        print(f"\n🔍 Scanning at {datetime.datetime.utcnow()}")
        current_top_signals.clear()

        for name, ex in exchanges.items():
            try:
                tickers = ex.fetch_tickers()
                for symbol, t in list(tickers.items())[:100]:
                    if not symbol.endswith("USDT"): continue

                    # Funding
                    funding = 0
                    try:
                        fr = ex.fetch_funding_rate(symbol)
                        funding = fr.get('fundingRate', 0)
                    except:
                        pass

                    # Volume Spike (5m candles)
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

                    # Deep Order Book
                    ob_buy_pressure, _ = analyze_order_book(ex, symbol)

                    # Long/Short Ratio (Binance)
                    ls_ratio = 1.0
                    if name == "Binance":
                        try:
                            ls = ex.fetch_long_short_ratio(symbol, limit=1)
                            ls_ratio = float(ls[0].get('longShortRatio', 1.0))
                        except:
                            pass

                    data = {
                        "funding": funding,
                        "vol_spike": vol_spike,
                        "vol_ratio": vol_ratio,
                        "oi_chg": oi_chg,
                        "fs_ratio": 3.0,
                        "futures_vol": t.get('quoteVolume', 0),
                        "ls_ratio": ls_ratio
                    }

                    score, signals = calculate_score(data, ob_buy_pressure)

                    if score >= ALERT_MIN_SCORE:
                        alert = {"symbol": symbol, "exchange": name, "score": score}
                        daily_signals.append(alert)
                        current_top_signals.append(alert)
                        send_telegram(
                            f"🚨 <b>CRIME ALERT v8.1</b> (Score: {score})\n"
                            f"🔥 {symbol} on {name}\n" +
                            "\n".join(signals)
                        )

            except:
                continue

        save_memory()
        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
