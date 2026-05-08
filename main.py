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
MIN_SCORE = 70
MAX_PAIRS = 250

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ALPHA ENGINE V7 LIVE"

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
        "BloFin": ccxt.blofin({"enableRateLimit": True, "timeout": 30000}),
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
# 1. REGIME CLASSIFICATION (FIXED)
# =========================================================

def regime(ex, s):
    c = candles(ex, s, "15m", 40)
    if not c:
        return "none"

    closes = [f(x[4]) for x in c]
    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    trend_up = closes[-1] > closes[-5] > closes[-10]
    trend_down = closes[-1] < closes[-5] < closes[-10]

    range_now = max(highs[-10:]) - min(lows[-10:])
    range_prev = max(highs[-20:-10]) - min(lows[-20:-10])

    compression = range_prev > 0 and (range_prev / max(range_now, 1)) > 1.8

    if compression:
        return "compression"
    if trend_up:
        return "trend_up"
    if trend_down:
        return "trend_down"
    return "chop"

# =========================================================
# 2. STRUCTURAL COMPRESSION (IMPROVED)
# =========================================================

def compression_strength(ex, s):
    c = candles(ex, s, "5m", 40)
    if not c:
        return 0

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]

    # tighter range + directional bias
    recent_highs = highs[-15:]
    recent_lows = lows[-15:]

    older_highs = highs[-30:-15]
    older_lows = lows[-30:-15]

    if not older_highs or not recent_highs:
        return 0

    old_range = max(older_highs) - min(older_lows)
    new_range = max(recent_highs) - min(recent_lows)

    if new_range == 0:
        return 0

    ratio = old_range / new_range

    return min(ratio if ratio > 1.5 else 0, 5)

# =========================================================
# 3. REAL LIQUIDITY SWEEP (FIXED)
# =========================================================

def liquidity_sweep(ex, s):
    c = candles(ex, s, "5m", 10)
    if not c:
        return 0

    prev_highs = [f(x[2]) for x in c[:-1]]
    prev_lows = [f(x[3]) for x in c[:-1]]

    last = c[-1]
    o, h, l, cl = f(last[1]), f(last[2]), f(last[3]), f(last[4])

    # sweep above previous high + rejection
    if h > max(prev_highs) and cl < h:
        return 1

    # sweep below previous low + rejection
    if l < min(prev_lows) and cl > l:
        return 1

    return 0

# =========================================================
# 4. BREAKOUT CONFIRMATION (FIXED)
# =========================================================

def breakout(ex, s):
    c = candles(ex, s, "5m", 20)
    if not c:
        return 0

    closes = [f(x[4]) for x in c]
    highs = [f(x[2]) for x in c]
    vols = [f(x[5]) for x in c]

    resistance = max(highs[-15:-1])

    vol_ok = vols[-1] > sum(vols[-6:-1]) / 5

    if closes[-1] > resistance and vol_ok:
        return 2

    return 0

# =========================================================
# 5. VOLUME EXPANSION
# =========================================================

def volume_expansion(ex, s):
    c = candles(ex, s, "5m", 15)
    if not c:
        return 0

    vols = [f(x[5]) for x in c]

    avg = sum(vols[:-1]) / max(len(vols[:-1]), 1)
    if avg == 0:
        return 0

    spike = vols[-1] / avg

    return min(max(spike, 0), 8)

# =========================================================
# 6. OI (SMOOTHED)
# =========================================================

def oi_change(ex, s, state):
    if ex.id != "binance":
        return 0

    try:
        data = ex.fetch_open_interest(s)
        val = f(data.get("openInterest") or data.get("openInterestAmount"))

        key = f"{ex.id}:{s}"
        old = state.get(key, val)

        state[key] = val

        if old == 0:
            return 0

        return ((val - old) / old) * 100

    except:
        return 0

# =========================================================
# SCORE + EXPLANATION ENGINE
# =========================================================

def score_and_explain(reg, comp, brk, vol, sweep, oi):

    score = 0
    reasons = []

    if reg in ["compression", "trend_up"]:
        score += 10
        reasons.append("Favorable market regime")

    if comp > 0:
        score += comp * 15
        reasons.append("Structure compression detected")

    if brk > 0:
        score += 30
        reasons.append("Breakout confirmed")

    if vol > 2:
        score += vol * 8
        reasons.append("Volume expansion")

    if sweep:
        score += 20
        reasons.append("Liquidity sweep (stop hunt)")

    if oi > 5:
        score += 10
        reasons.append("OI expansion (speculative flow)")

    score = min(score, 100)

    return score, reasons

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    state = {}

    tg("🚀 ALPHA ENGINE V7 STARTED")

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

                    comp = compression_strength(ex, s)
                    brk = breakout(ex, s)
                    volx = volume_expansion(ex, s)
                    sweep = liquidity_sweep(ex, s)
                    oi = oi_change(ex, s, state)

                    sc, reasons = score_and_explain(reg, comp, brk, volx, sweep, oi)

                    print(f"{s} | score={sc:.1f} | {reg}")

                    if sc < MIN_SCORE:
                        continue

                    # =================================================
                    # SIGNAL MESSAGE (WITH STRENGTH SUMMARY)
                    # =================================================

                    strengths = "\n".join([f"• {r}" for r in reasons])

                    msg = f"""
🚨 {name} ALPHA SIGNAL

{s}
Score: {sc:.1f}
Regime: {reg}

STRENGTHS:
{strengths}

Metrics:
Compression: {comp:.2f}
Breakout: {brk}
Volume: {volx:.2f}x
Sweep: {sweep}
OI: {oi:.1f}%
"""

                    tg(msg)
                    time.sleep(0.3)

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
