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
MAX_PAIRS = 200
MIN_ALERT = 60

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "CRIME ENGINE V13 EARLY ALPHA"

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
        "Binance": ccxt.binance({"enableRateLimit": True})
    }

# =========================================================
# CANDLES
# =========================================================

def candles(ex, s, tf="5m", n=40):
    try:
        c = ex.fetch_ohlcv(s, tf, limit=n)
        return c if c and len(c) > 25 else None
    except:
        return None

# =========================================================
# 1. ENERGY BUILDUP (IMPROVED COMPRESSION)
# =========================================================

def energy_build(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    early_range = max(highs[:20]) - min(lows[:20])
    late_range = max(highs[-20:]) - min(lows[-20:])

    if early_range == 0:
        return 0

    squeeze = (early_range - late_range) / early_range

    return min(max(squeeze, 0), 1)

# =========================================================
# 2. PRESSURE BREAKOUT (NOT CONFIRMED BREAKOUT)
# =========================================================

def breakout_pressure(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    closes = [f(x[4]) for x in c]
    highs = [f(x[2]) for x in c]

    resistance = max(highs[-20:-3])

    distance = (closes[-1] - resistance) / resistance

    if distance > 0:
        return min(distance * 8, 1)   # early pressure scaling

    return 0

# =========================================================
# 3. VOLUME PRESSURE (EARLY ACCELERATION)
# =========================================================

def volume_pressure(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    vols = [f(x[5]) for x in c]

    short = sum(vols[-5:]) / 5
    long = sum(vols[-20:-5]) / 15

    if long == 0:
        return 0

    ratio = short / long

    return min((ratio - 1) * 2, 1)

# =========================================================
# 4. SWEEP PRESSURE (GRADIENT VERSION)
# =========================================================

def sweep_pressure(ex, s):
    c = candles(ex, s)
    if not c:
        return 0

    highs = [f(x[2]) for x in c[:-1]]
    lows = [f(x[3]) for x in c[:-1]]

    last = c[-1]
    h, l, cl = f(last[2]), f(last[3]), f(last[4])

    pressure = 0

    if h > max(highs):
        pressure += (h - max(highs)) / max(highs)

    if l < min(lows):
        pressure += (min(lows) - l) / min(lows)

    return min(pressure * 5, 1)

# =========================================================
# 5. REGIME (WEIGHTED CONTEXT)
# =========================================================

def regime(ex, s):
    c = candles(ex, s, "15m", 30)
    if not c:
        return 0.4

    closes = [f(x[4]) for x in c]

    trend = closes[-1] > closes[-5] > closes[-10]

    return 0.65 if trend else 0.35

# =========================================================
# FINAL SCORE (CALIBRATED EARLY ALPHA MODEL)
# =========================================================

def score(reg, energy, brk, vol, sweep):

    # EARLY ALPHA WEIGHTS (very important change)
    raw = (
        energy * 0.30 +
        brk * 0.25 +
        vol * 0.20 +
        sweep * 0.15 +
        reg * 0.10
    )

    # soft curve (prevents clustering at 0–10)
    adjusted = raw ** 0.7

    return max(1, min(adjusted * 100, 100))

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    tg("🚀 V13 EARLY ALPHA ENGINE LIVE")

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

                    energy = energy_build(ex, s)
                    brk = breakout_pressure(ex, s)
                    volx = volume_pressure(ex, s)
                    swp = sweep_pressure(ex, s)
                    reg = regime(ex, s)

                    sc = score(reg, energy, brk, volx, swp)

                    print(f"{s} | crime={sc:.1f}")

                    if sc < MIN_ALERT:
                        continue

                    msg = f"""
🚨 CRIME SIGNAL V13

{s}
Crime Score: {sc:.1f}/100

Early Alpha Signals:
• Energy Build: {energy:.2f}
• Break Pressure: {brk:.2f}
• Volume Accel: {volx:.2f}
• Sweep Pressure: {swp:.2f}
• Regime: {reg:.2f}
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
