import ccxt
import time
import threading
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

LOOP_SECONDS = 60
MAX_PAIRS = 120
MIN_SCORE = 60

MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}

# =========================================================
# WEB SERVER (KEEP ALIVE)
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ALPHA ENGINE V20 ONLINE"

def run_web():
    app.run("0.0.0.0", 8080, use_reloader=False)

# =========================================================
# SAFE NUMERIC CORE (CRITICAL)
# =========================================================

def f(x):
    try:
        if x is None:
            return 0.0
        if isinstance(x, complex):
            return float(x.real)
        x = float(x)
        if x != x:  # NaN check
            return 0.0
        return x
    except:
        return 0.0


def clamp(x, mn=0.0, mx=10.0):
    x = f(x)
    return max(mn, min(mx, x))


def safe_div(a, b):
    a, b = f(a), f(b)
    return a / b if b != 0 else 0.0

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
# PAIRS
# =========================================================

def get_pairs(ex):

    pairs = []

    try:
        tickers = ex.fetch_tickers()

        for s, t in tickers.items():

            if not s:
                continue

            s = str(s).upper().replace(":USDT", "")

            if "/USDT" not in s:
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
# FUNDING / OI (SAFE OPTIONAL)
# =========================================================

def funding(ex, s):
    try:
        if hasattr(ex, "fetch_funding_rate"):
            fr = ex.fetch_funding_rate(s)
            return f(fr.get("fundingRate"))
    except:
        pass
    return 0.0


def open_interest(ex, s):
    try:
        if hasattr(ex, "fetch_open_interest"):
            oi = ex.fetch_open_interest(s)
            return f(oi.get("openInterest") or oi.get("openInterestAmount"))
    except:
        pass
    return 0.0

# =========================================================
# FEATURES (ALPHA CORE)
# =========================================================

def features(c):

    highs = [f(x[2]) for x in c]
    lows = [f(x[3]) for x in c]
    closes = [f(x[4]) for x in c]
    vols = [f(x[5]) for x in c]

    # compression (pre-move squeeze)
    old_range = max(highs[:20]) - min(lows[:20])
    new_range = max(highs[-20:]) - min(lows[-20:])
    comp = clamp(safe_div(old_range - new_range, old_range))

    # breakout pressure
    resistance = max(highs[-15:-3])
    brk = clamp(safe_div(closes[-1] - resistance, resistance))
    brk = max(0.0, brk)

    # volume acceleration
    short = sum(vols[-5:]) / 5
    long = sum(vols[-20:-5]) / 15 if len(vols) > 20 else short
    vol = clamp(safe_div(short - long, long))
    vol = max(0.0, vol)

    # sweep detection
    swp = 0.0
    if highs[-1] > max(highs[:-1]):
        swp += 0.5
    if lows[-1] < min(lows[:-1]):
        swp += 0.5

    return comp, brk, vol, swp

# =========================================================
# SCORE ENGINE (STABLE 1–100)
# =========================================================

def score(comp, brk, vol, swp, fund, oi):

    raw = (
        comp * 0.30 +
        brk * 0.25 +
        vol * 0.20 +
        swp * 0.15 +
        abs(f(fund)) * 0.05 +
        clamp(oi / 1_000_000) * 0.05
    )

    raw = max(0.0, min(raw, 2.0))

    return max(1, min(raw * 50, 100))

# =========================================================
# MAIN LOOP
# =========================================================

def run():

    exs = exchanges()

    while True:

        for name, ex in exs.items():

            print(f"\nScanning {name}")

            load(ex)

            pairs = get_pairs(ex)

            print(f"{name} pairs: {len(pairs)}")

            for s, vol in pairs[:20]:

                c = candles(ex, s)
                if not c:
                    continue

                comp, brk, volx, swp = features(c)

                fund = funding(ex, s)
                oi = open_interest(ex, s)

                sc = score(comp, brk, volx, swp, fund, oi)

                print(
                    f"{s} | score={sc:.1f} | "
                    f"c={comp:.2f} b={brk:.2f} v={volx:.2f} s={swp:.2f} "
                    f"f={fund:.4f} oi={oi:.0f}"
                )

                if sc >= MIN_SCORE:
                    print("🚨 SIGNAL:", s, sc)

        print("SCAN COMPLETE\n")
        time.sleep(LOOP_SECONDS)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run()
