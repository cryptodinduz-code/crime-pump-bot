import ccxt
import os
import time
import threading
import requests
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TG = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP = 60
MIN_SCORE = 0.65   # normalized score (0–1 system)

MAX_PAIRS = 250

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "BALANCED ALPHA ENGINE V8"

def run_web():
    app.run("0.0.0.0", 8080, use_reloader=False)

# =========================================================
# TELEGRAM
# =========================================================

def tg(msg):
    if not TG:
        print(msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4000]},
            timeout=10
        )
    except:
        pass

# =========================================================
# SAFE FLOAT
# =========================================================

def f(x):
    try:
        return float(x) if x is not None else 0.0
    except:
        return 0.0

# =========================================================
# EXCHANGES
# =========================================================

def exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True}),
        "Binance": ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"}
        })
    }

# =========================================================
# CANDLES
# =========================================================

def candles(ex, s, tf="5m", n=40):
    try:
        c = ex.fetch_ohlcv(s, tf, limit=n)
        return c if len(c) > 20 else None
    except:
        return None

# =========================================================
# 1. REGIME (SOFT CLASSIFICATION)
# =========================================================

def regime(ex, s):
    c = candles(ex, s, "15m", 40)
    if not c:
        return "none"

    closes = [f(x[4]) for x in c]
    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    trend_up = closes[-1] > closes[-5] > closes[-10]

    range_now = max(highs[-10:]) - min(lows[-10:])
    range_prev = max(highs[-20:-10]) - min(lows[-20:-10])

    compression = range_prev > 0 and range_prev / max(range_now, 1) > 1.8

    if trend_up:
        return "trend"
    if compression:
        return "compression"
    return "neutral"

# =========================================================
# 2. NORMALIZED METRICS (0–1)
# =========================================================

def compression_score(ex, s):
    c = candles(ex, s, "5m", 40)
    if not c:
        return 0

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    old_range = max(highs[:20]) - min(lows[:20])
    new_range = max(highs[-20:]) - min(lows[-20:])

    if new_range == 0:
        return 0

    ratio = old_range / new_range

    return min(ratio / 5, 1)  # normalized 0–1

# =========================================================
# 3. BREAKOUT (0–1)
# =========================================================

def breakout_score(ex, s):
    c = candles(ex, s, "5m", 20)
    if not c:
        return 0

    closes = [f(x[4]) for x in c]
    highs = [f(x[2]) for x in c]

    resistance = max(highs[-15:-1])

    if closes[-1] > resistance:
        return 1

    return 0

# =========================================================
# 4. VOLUME EXPANSION (0–1)
# =========================================================

def volume_score(ex, s):
    c = candles(ex, s, "5m", 15)
    if not c:
        return 0

    vols = [f(x[5]) for x in c]

    avg = sum(vols[:-1]) / max(len(vols[:-1]), 1)
    if avg == 0:
        return 0

    spike = vols[-1] / avg

    return min(spike / 5, 1)

# =========================================================
# 5. LIQUIDITY SWEEP (0–1)
# =========================================================

def sweep_score(ex, s):
    c = candles(ex, s, "5m", 10)
    if not c:
        return 0

    highs = [f(x[2]) for x in c[:-1]]
    lows = [f(x[3]) for x in c[:-1]]

    last = c[-1]
    h, l, cl = f(last[2]), f(last[3]), f(last[4])

    if h > max(highs) and cl < h:
        return 1

    if l < min(lows) and cl > l:
        return 1

    return 0

# =========================================================
# 6. FINAL SCORE (BALANCED WEIGHTS)
# =========================================================

def final_score(reg, comp, brk, vol, sweep):

    # HARD FILTER (must have event)
    if brk == 0 and sweep == 0 and vol < 0.4:
        return 0, []

    reasons = []

    score = 0

    # weights balanced (no domination)
    score += comp * 0.25
    score += brk * 0.30
    score += vol * 0.25
    score += sweep * 0.20

    if reg == "trend":
        score *= 1.1
        reasons.append("Trend context")

    if reg == "compression":
        reasons.append("Compression buildup")

    if brk:
        reasons.append("Breakout confirmed")

    if sweep:
        reasons.append("Liquidity sweep detected")

    if vol > 0.6:
        reasons.append("Volume expansion")

    return min(score, 1), reasons

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    tg("🚀 BALANCED ALPHA ENGINE V8 STARTED")

    while True:

        exs = exchanges()

        for name, ex in exs.items():

            try:

                print(f"\nScanning {name}")

                try:
                    ex.load_markets()
                except:
                    pass

                tickers = ex.fetch_tickers()
                if not tickers:
                    continue

                universe = []

                for s, t in tickers.items():

                    if not any(x in s for x in ["/USDT", "-USDT"]):
                        continue

                    base = s.split("/")[0]
                    if base in MAJORS:
                        continue

                    vol = f(t.get("quoteVolume") or t.get("baseVolume"))
                    if vol <= 0:
                        continue

                    universe.append((s, vol))

                universe.sort(key=lambda x: x[1], reverse=True)
                universe = universe[:MAX_PAIRS]

                for s, vol in universe:

                    reg = regime(ex, s)

                    comp = compression_score(ex, s)
                    brk = breakout_score(ex, s)
                    volx = volume_score(ex, s)
                    sweep = sweep_score(ex, s)

                    sc, reasons = final_score(reg, comp, brk, volx, sweep)

                    print(f"{s} | score={sc:.2f} | {reg}")

                    if sc < MIN_SCORE:
                        continue

                    msg = f"""
🚨 {name} BALANCED SIGNAL

{s}
Score: {sc:.2f}
Regime: {reg}

STRENGTHS:
• """ + "\n• ".join(reasons) + f"""

Metrics (normalized):
Compression: {comp:.2f}
Breakout: {brk}
Volume: {volx:.2f}
Sweep: {sweep}
"""

                    tg(msg)
                    time.sleep(0.2)

            except Exception as e:
                print(name, "error:", e)

        print("SCAN COMPLETE")
        time.sleep(LOOP)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
