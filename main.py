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

# Improved Scoring (tuned for real pumps like LAB/RAVE)
ALERT_MIN_SCORE = 65

# Weights
W_FUNDING = 30
W_VOLUME_SPIKE = 25
W_OI_SURGE = 25
W_FS_RATIO = 15
W_BINANCE_CONFIRM = 20
W_LOW_CAP = 10

# Thresholds
FUNDING_THRESH = 0.0003   # 0.03%
VOL_SPIKE_MULT = 4.5
OI_GROWTH_THRESH = 18.0
FS_RATIO_THRESH = 3.5

app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Crime Bot v6 (Binance Confirmation) is ALIVE!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# Memory
prev_oi = {}
vol_history = {}
alerted = {}

def load_memory():
    global prev_oi, vol_history, alerted
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f:
                data = json.load(f)
            prev_oi = data.get("prev_oi", {})
            vol_history = {k: deque(v, maxlen=168) for k, v in data.get("vol_history", {}).items()}
            alerted = data.get("alerted", {})
            print("✅ Memory loaded")
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

def send_telegram(text):
    if not TG_ENABLED:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except:
        pass

def calculate_score(data, binance_confirm=False):
    score = 0
    signals = []

    if abs(data.get('funding', 0)) > FUNDING_THRESH:
        score += W_FUNDING
        signals.append(f"Extreme Funding ({data['funding']*100:+.4f}%)")

    if data.get('vol_spike', False):
        score += W_VOLUME_SPIKE
        signals.append(f"Volume Spike {data.get('vol_ratio', 0):.1f}x")

    if data.get('oi_chg', 0) > OI_GROWTH_THRESH:
        score += W_OI_SURGE
        signals.append(f"OI Surge +{data['oi_chg']:.1f}%")

    if data.get('fs_ratio', 0) > FS_RATIO_THRESH:
        score += W_FS_RATIO
        signals.append(f"F/S Ratio {data['fs_ratio']:.1f}x")

    if binance_confirm:
        score += W_BINANCE_CONFIRM
        signals.append("✅ Binance Confirmation")

    if data.get('futures_vol', 0) < 30_000_000:
        score += W_LOW_CAP
        signals.append("Low Cap Play")

    return min(100, score), signals

# Main Scan Loop
def bot_loop():
    load_memory()
    send_telegram("🚀 <b>Crime Bot v6 Online</b>\nBinance Confirmation + Improved Scoring Active")

    while True:
        print(f"\n🔍 Scanning at {datetime.datetime.utcnow()}")
        alerts = []

        for name, ex in exchanges.items():
            try:
                tickers = ex.fetch_tickers()
                for symbol, t in list(tickers.items())[:100]:
                    if not symbol.endswith("USDT"):
                        continue

                    # Basic data
                    funding = 0
                    try:
                        fr = ex.fetch_funding_rate(symbol)
                        funding = fr.get('fundingRate', 0)
                    except:
                        pass

                    # 5m candles
                    try:
                        candles = ex.fetch_ohlcv(symbol, '5m', limit=6)
                        vols = [c[5] for c in candles]
                        avg_vol = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
                        vol_ratio = vols[-1] / avg_vol if avg_vol > 0 else 1
                        vol_spike = vol_ratio >= VOL_SPIKE_MULT
                    except:
                        vol_spike = False
                        vol_ratio = 0

                    # OI
                    oi_chg = 0
                    try:
                        oi = ex.fetch_open_interest(symbol)
                        oi_val = oi.get('openInterestAmount') or oi.get('openInterest')
                        key = f"{name}:{symbol}"
                        if key in prev_oi:
                            oi_chg = ((oi_val - prev_oi[key]) / prev_oi[key]) * 100 if prev_oi[key] > 0 else 0
                        prev_oi[key] = oi_val
                    except:
                        pass

                    fs_ratio = 2.0  # placeholder

                    data = {
                        "funding": funding,
                        "vol_spike": vol_spike,
                        "vol_ratio": vol_ratio,
                        "oi_chg": oi_chg,
                        "fs_ratio": fs_ratio,
                        "futures_vol": t.get('quoteVolume', 0),
                        "last_price": t.get('last', 0)
                    }

                    # Binance confirmation
                    binance_confirm = False
                    if name != "Binance":
                        try:
                            b_ticker = exchanges["Binance"].fetch_ticker(symbol)
                            if b_ticker.get('quoteVolume', 0) > 5_000_000:
                                binance_confirm = True
                        except:
                            pass

                    score, signals = calculate_score(data, binance_confirm)

                    if score >= ALERT_MIN_SCORE:
                        alert = {
                            "exchange": name,
                            "symbol": symbol,
                            "score": score,
                            "signals": signals,
                            "price": data["last_price"]
                        }
                        alerts.append(alert)
                        send_telegram(
                            f"🚨 <b>CRIME ALERT v6</b> (Score: {score})\n"
                            f"🔥 {symbol} on {name}\n"
                            f"Price: ${data['last_price']:.6g}\n"
                            + "\n".join(signals)
                        )

            except Exception as e:
                print(f"Error on {name}: {e}")

        save_memory()
        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot_loop()
