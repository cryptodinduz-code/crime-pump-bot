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

# Weights (updated)
W_FUNDING = 28
W_VOL_SPIKE = 25
W_OI_SURGE = 22
W_LIQ_HEAT = 20
W_LS_RATIO = 18
W_FS_RATIO = 12
W_LOW_CAP = 10
W_ORDER_BOOK = 18   # New deeper order book weight

app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Crime Bot v8 (Deep Order Book Analysis) is ALIVE!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# Memory
prev_oi = {}
vol_history = {}
alerted = {}
daily_signals = []

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
        data = {"prev_oi": prev_oi, "vol_history": {k: list(v) for k, v in vol_history.items()}, "alerted": alerted}
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

def send_telegram(text):
    if not TG_ENABLED: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except:
        pass

def analyze_order_book(exchange, symbol):
    """Deeper order book analysis - returns True if strong buy pressure"""
    try:
        ob = exchange.fetch_order_book(symbol, limit=20)
        bid_vol = sum(lvl[1] for lvl in ob.get("bids", []))
        ask_vol = sum(lvl[1] for lvl in ob.get("asks", []))
        if ask_vol == 0:
            return False, 0
        ratio = bid_vol / ask_vol
        return ratio > 1.8, round(ratio, 2)  # strong buy pressure if bids > 1.8x asks
    except:
        return False, 0

def calculate_score(data, ob_buy_pressure=False):
    score = 0
    signals = []

    if abs(data.get('funding', 0)) > 0.0003:
        score += W_FUNDING
        signals.append(f"Extreme Funding ({data['funding']*100:+.4f}%)")

    if data.get('vol_spike', False):
        score += W_VOL_SPIKE
        signals.append(f"Vol Spike {data.get('vol_ratio',0):.1f}x")

    if data.get('oi_chg', 0) > 18:
        score += W_OI_SURGE
        signals.append(f"OI Surge +{data['oi_chg']:.1f}%")

    if data.get('liq_heat', 0) > 0:
        score += W_LIQ_HEAT
        signals.append(f"Liquidation Heat ${data['liq_heat']/1000000:.1f}M")

    if data.get('ls_ratio', 0) > 1.8:
        score += W_LS_RATIO
        signals.append(f"Long/Short Ratio {data['ls_ratio']:.1f}")

    if data.get('fs_ratio', 0) > 3.5:
        score += W_FS_RATIO
        signals.append(f"F/S Ratio {data['fs_ratio']:.1f}x")

    if data.get('futures_vol', 0) < 40_000_000:
        score += W_LOW_CAP
        signals.append("Low Cap")

    if ob_buy_pressure:
        score += W_ORDER_BOOK
        signals.append("Deep Order Book: Strong Buy Pressure")

    return min(100, score), signals

# Daily Summary
def daily_summary():
    while True:
        now = datetime.datetime.utcnow()
        if now.hour == 0 and now.minute < 5 and daily_signals:
            top = sorted(daily_signals, key=lambda x: x['score'], reverse=True)[:5]
            msg = "📊 <b>Daily Crime Summary (v8)</b>\n\n"
            for s in top:
                msg += f"• {s['symbol']} on {s['exchange']} — Score {s['score']}\n"
            send_telegram(msg)
            daily_signals.clear()
        time.sleep(60)

# Main Loop
def bot_loop():
    load_memory()
    send_telegram("🚀 <b>Crime Bot v8 Online</b>\nDeep Order Book Analysis Added")

    threading.Thread(target=daily_summary, daemon=True).start()

    while True:
        print(f"\n🔍 Scanning at {datetime.datetime.utcnow()}")
        for name, ex in list(exchanges.items()):
            try:
                tickers = ex.fetch_tickers()
                for symbol, t in list(tickers.items())[:80]:
                    if not symbol.endswith("USDT"): continue

                    # Basic data (funding, volume, OI, etc.)
                    funding = 0
                    try:
                        fr = ex.fetch_funding_rate(symbol)
                        funding = fr.get('fundingRate', 0)
                    except:
                        pass

                    vol_spike = False
                    vol_ratio = 0
                    try:
                        candles = ex.fetch_ohlcv(symbol, '5m', limit=6)
                        vols = [c[5] for c in candles]
                        avg = sum(vols[:-1]) / len(vols[:-1]) if len(vols)>1 else 1
                        vol_ratio = vols[-1] / avg
                        vol_spike = vol_ratio >= 4.5
                    except:
                        pass

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

                    # Deeper Order Book Analysis (only on promising symbols)
                    ob_buy_pressure = False
                    if abs(funding) > 0.0002 or vol_spike:  # only check when already strong
                        ob_buy_pressure, _ = analyze_order_book(ex, symbol)

                    data = {
                        "funding": funding,
                        "vol_spike": vol_spike,
                        "vol_ratio": vol_ratio,
                        "oi_chg": oi_chg,
                        "fs_ratio": 3.0,
                        "futures_vol": t.get('quoteVolume', 0),
                        "liq_heat": 0,
                        "ls_ratio": 1.0
                    }

                    score, signals = calculate_score(data, ob_buy_pressure)

                    if score >= ALERT_MIN_SCORE:
                        alert = {"symbol": symbol, "exchange": name, "score": score}
                        daily_signals.append(alert)
                        send_telegram(
                            f"🚨 <b>CRIME ALERT v8</b> (Score: {score})\n"
                            f"🔥 {symbol} on {name}\n"
                            + "\n".join(signals)
                        )

            except:
                continue

        save_memory()
        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
