import ccxt
import time

# =========================================================
# SAFE CORE
# =========================================================

def safe_float(x):
    try:
        if x is None:
            return 0.0
        if isinstance(x, complex):
            return float(x.real)
        return float(x)
    except:
        return 0.0


def normalize_symbol(s):
    if not s:
        return None

    s = str(s).upper()

    s = s.replace(":USDT", "")
    s = s.replace("USDT", "/USDT") if "USDT" in s and "/" not in s else s

    return s


# =========================================================
# EXCHANGE INIT
# =========================================================

def get_exchanges():
    return {
        "MEXC": ccxt.mexc({"enableRateLimit": True}),
        "BloFin": ccxt.blofin({"enableRateLimit": True})
    }


def load_exchange(ex):
    try:
        ex.load_markets()
    except Exception as e:
        print("load_markets error:", e)


# =========================================================
# UNIVERSAL TICKER EXTRACTION
# =========================================================

def get_pairs(ex, name):

    pairs = []

    try:
        tickers = ex.fetch_tickers()

        if not tickers:
            return []

        for raw_symbol, t in tickers.items():

            symbol = normalize_symbol(raw_symbol)

            if not symbol:
                continue

            if "/USDT" not in symbol:
                continue

            vol = safe_float(t.get("quoteVolume") or t.get("baseVolume"))

            if vol <= 0:
                continue

            pairs.append((symbol, vol))

    except Exception as e:
        print(f"{name} fetch error:", e)

    return pairs


# =========================================================
# MAIN SCANNER
# =========================================================

def scan_exchange(name, ex):

    print(f"\nScanning {name}")

    load_exchange(ex)

    pairs = get_pairs(ex, name)

    print(f"{name} raw pairs found: {len(pairs)}")

    if len(pairs) == 0:
        print(f"{name} WARNING: no pairs returned")
        return []

    pairs.sort(key=lambda x: x[1], reverse=True)

    return pairs[:100]


# =========================================================
# MAIN LOOP
# =========================================================

def run():

    exs = get_exchanges()

    while True:

        total = 0

        for name, ex in exs.items():

            try:
                pairs = scan_exchange(name, ex)

                print(f"{name} usable pairs: {len(pairs)}")

                total += len(pairs)

                for s, vol in pairs[:15]:
                    print(f"{s} | vol={vol:.0f}")

            except Exception as e:
                print(f"{name} CRASH:", e)

        print(f"\nTOTAL PAIRS SCANNED: {total}")
        print("SCAN COMPLETE\n")

        time.sleep(60)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    run()
