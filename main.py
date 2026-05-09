import ccxt
import time
import threading
import requests
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""

TG = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP = 60
MAX_PAIRS = 120
MIN_SCORE = 60

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# WEB SERVER
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ALPHA ENGINE V17 ONLINE"

def run_web():
    app.run("0.0.0.0", 8080, use_reloader=False)

# =========================================================
# SAFE UTIL
# =========================================================

def f(x):
    try:
        if x is None:
            return 0.0
        if isinstance(x, complex):
            return float(x.real)
        return float(x)
    except:
        return 0.0


def norm_symbol(s):
    if not s:
        return None
    s = str(s).upper()
    s = s.replace(":USDT", "")
    return s

# =========================================================
# EXCHANGES
# =========================================================

def exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True})
    }


def load(ex):
    try:
        ex.load_markets()
    except:
        pass

# =========================================================
# PAIR COLLECTION
# =========================================================

def get_pairs(ex, name):

    pairs = []

    try:
        tickers = ex.fetch_tickers()

        for s, t in tickers.items():

            s = norm_symbol(s)

            if not s or "/USDT" not in s:
                continue

            base = s.split("/")[0]
            if base in MAJORS:
                continue

            vol = f(t.get("quoteVolume") or t.get("baseVolume"))
            if vol <= 0:
                continue

            pairs.append((s, vol))

    except:
        return []

    pairs.sort(key=lambda x: x[1], reverse=True)

    return pairs[:MAX_PAIRS]

# =========================================================
# CANDLES
# =========================================================

def candles(ex, s):

    try:
        c = ex.fetch_ohlcv(s, "5m", limit=40)
        if not c or len(c) < 25:
            return None
        return c
    except:
        return None

# =========================================================
# FEATURES (FULL ALPHA SET)
# =========================================================

def features(c):

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]
    closes = [f(x[4]) for x in c]
    vols = [f(x[5]) for x in c]

    # 1. COMPRESSION (pre-move squeeze)
    old_range = max(highs[:20]) - min(lows[:20])
    new_range = max(highs[-20:]) - min(lows[-20:])
    comp = (old_range - new_range) / old_range if old_range > 0 else 0

    # 2. BREAKOUT PRESSURE (not confirmed breakout)
    resistance = max(highs[-15:-3])
    brk = (closes[-1] - resistance) / resistance if resistance > 0 else 0
    brk = max(0, brk)

    # 3. VOLUME ACCELERATION
    short = sum(vols[-5:]) / 5
    long = sum(vols[-20:-5]) / 15 if len(vols) > 20 else short
    vol = (short / long - 1) if long > 0 else 0
    vol = max(0, vol)

    # 4. LIQUIDITY SWEEP PRESSURE
    swp = 0
    if highs[-1] > max(highs[:-1]):
        swp += 0.5
    if lows[-1] < min(lows[:-1]):
        swp += 0.5

    return comp, brk, vol, swp

# =========================================================
# SCORE ENGINE (STABLE DISTRIBUTION)
# =========================================================

def score(comp, brk, vol, swp):

    raw = (
        comp * 0.32 +
        brk * 0.28 +
        vol * 0.25 +
        swp * 0.15
    )

    # smooth curve to avoid clustering
    return max(1, min((raw ** 0.78) * 100, 100))

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    exs = exchanges()

    while True:

        for name, ex in exs.items():

            print(f"\nScanning {name}")

            load(ex)

            pairs = get_pairs(ex, name)

            print(f"{name} pairs: {len(pairs)}")

            for s, vol in pairs[:20]:

                c = candles(ex, s)
                if not c:
                    continue

                comp, brk, volx, swp = features(c)

                sc = score(comp, brk, volx, swp)

                print(f"{s} | score={sc:.1f} | c={comp:.2f} b={brk:.2f} v={volx:.2f} s={swp:.2f}")

                if sc >= MIN_SCORE:
                    print("🚨 SIGNAL:", s, sc)

        print("SCAN COMPLETE\n")
        time.sleep(LOOP)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
