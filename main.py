import ccxt
import os
import time
import datetime
import threading
import requests
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TG_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

LOOP_SECONDS = 60
ALERT_MIN_SCORE = 40

MAX_PAIRS_PER_EXCHANGE = 250

MIN_VOLUME = 2_000_000
LOW_VOLUME_BONUS_LIMIT = 30_000_000

# =========================================================
# FLASK KEEPALIVE
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🚀 Alpha Hunter Bot ONLINE"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):
    if not TG_ENABLED:
        print("Telegram disabled")
        return

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            },
            timeout=10
        )

        print(f"Telegram response: {r.status_code} | {r.text}")

    except Exception as e:
        print(f"Telegram error: {e}")

# =========================================================
# HELPERS
# =========================================================

def get_volume_spike(exchange, symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=8)

        if len(candles) < 6:
            return False, 0

        volumes = [c[5] for c in candles]

        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])

        if avg_volume == 0:
            return False, 0

        ratio = volumes[-1] / avg_volume

        return ratio >= 2.0, ratio

    except Exception as e:
        print(f"Volume spike error {symbol}: {e}")
        return False, 0

def get_liq_heat(exchange, symbol):
    """
    Approximation of liquidation pressure:
    volatility * volume
    """

    try:
        candles = exchange.fetch_ohlcv(symbol, "5m", limit=12)

        total = 0

        for c in candles:
            high = c[2]
            low = c[3]
            close = c[4]
            volume = c[5]

            if close == 0:
                continue

            range_pct = ((high - low) / close) * 100

            total += range_pct * volume

        return total

    except Exception as e:
        print(f"Liq heat error {symbol}: {e}")
        return 0

def analyze_order_book(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=20)

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)

        if ask_vol == 0:
            return False, 0

        ratio = bid_vol / ask_vol

        return ratio > 1.4, ratio

    except Exception as e:
        print(f"Orderbook error {symbol}: {e}")
        return False, 0

def get_open_interest_change(exchange, symbol, prev_oi):
    try:
        oi_data = exchange.fetch_open_interest(symbol)

        oi_value = (
            oi_data.get("openInterestAmount")
            or oi_data.get("openInterest")
            or oi_data.get("openInterestValue")
        )

        if oi_value is None:
            return 0

        oi_value = float(oi_value)

        key = f"{exchange.id}:{symbol}"

        oi_change = 0

        if key in prev_oi and prev_oi[key] > 0:
            oi_change = (
                (oi_value - prev_oi[key]) / prev_oi[key]
            ) * 100

        prev_oi[key] = oi_value

        return oi_change

    except Exception as e:
        print(f"OI error {symbol}: {e}")
        return 0

def get_funding(exchange, symbol):
    try:
        fr = exchange.fetch_funding_rate(symbol)

        return float(fr.get("fundingRate", 0))

    except Exception as e:
        print(f"Funding error {symbol}: {e}")
        return 0

# =========================================================
# SCORING ENGINE
# =========================================================

def calculate_score(
    funding,
    vol_ratio,
    oi_change,
    ob_ratio,
    liq_heat,
    volume
):
    score = 0
    reasons = []

    # FUNDING
    if abs(funding) > 0.00015:
        score += 20
        reasons.append("Funding Extreme")

    # VOLUME SPIKE
    if vol_ratio > 2:
        score += 25
        reasons.append(f"Volume Spike {vol_ratio:.1f}x")

    # OPEN INTEREST
    if oi_change > 5:
        score += 20
        reasons.append(f"OI +{oi_change:.1f}%")

    # ORDERBOOK
    if ob_ratio > 1.4:
        score += 15
        reasons.append(f"Buy Pressure {ob_ratio:.2f}")

    # LIQ HEAT
    if liq_heat > 500:
        score += 10
        reasons.append("Liquidation Pressure")

    # LOWER CAP / LOWER LIQUIDITY BONUS
    if volume < LOW_VOLUME_BONUS_LIMIT:
        score += 10
        reasons.append("Midcap Momentum")

    return score, reasons

# =========================================================
# MAIN LOOP
# =========================================================

def bot_loop():

    send_telegram(
        "🚀 <b>Alpha Hunter Bot Started</b>\n"
        f"Minimum score: {ALERT_MIN_SCORE}"
    )

    prev_oi = {}

    while True:

        print(
            f"\n==============================\n"
            f"SCAN STARTED {datetime.datetime.utcnow()}\n"
            f"=============================="
        )

        alerts_fired = 0

        exchanges = {
            "Binance": ccxt.binance({
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future"
                }
            }),

            "MEXC": ccxt.mexc({
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap"
                }
            }),

            "BloFin": ccxt.blofin({
                "enableRateLimit": True
            })
        }

        for name, exchange in exchanges.items():

            print(f"\n🔍 Scanning {name}")

            try:
                markets = exchange.load_markets()

                tickers = exchange.fetch_tickers()

                # SORT BY VOLUME
                sorted_tickers = sorted(
                    tickers.items(),
                    key=lambda x: x[1].get("quoteVolume", 0),
                    reverse=True
                )

                scanned = 0

                for symbol, ticker in sorted_tickers:

                    try:

                        if scanned >= MAX_PAIRS_PER_EXCHANGE:
                            break

                        if "USDT" not in symbol:
                            continue

                        if symbol not in markets:
                            continue

                        market = markets[symbol]

                        # ONLY FUTURES / SWAPS
                        if not (
                            market.get("swap")
                            or market.get("future")
                        ):
                            continue

                        scanned += 1

                        volume = ticker.get("quoteVolume", 0)

                        if volume is None:
                            continue

                        volume = float(volume)

                        if volume < MIN_VOLUME:
                            continue

                        last_price = ticker.get("last", 0)

                        # ====================================
                        # METRICS
                        # ====================================

                        funding = get_funding(exchange, symbol)

                        vol_spike, vol_ratio = get_volume_spike(
                            exchange,
                            symbol
                        )

                        oi_change = get_open_interest_change(
                            exchange,
                            symbol,
                            prev_oi
                        )

                        ob_pressure, ob_ratio = analyze_order_book(
                            exchange,
                            symbol
                        )

                        liq_heat = get_liq_heat(
                            exchange,
                            symbol
                        )

                        # ====================================
                        # DEBUG LOG
                        # ====================================

                        print(
                            f"{name} | {symbol} | "
                            f"Vol=${volume/1e6:.1f}M | "
                            f"Funding={funding:.5f} | "
                            f"VolRatio={vol_ratio:.2f} | "
                            f"OI={oi_change:.2f}% | "
                            f"OB={ob_ratio:.2f} | "
                            f"Liq={liq_heat:.1f}"
                        )

                        # ====================================
                        # SCORE
                        # ====================================

                        score, reasons = calculate_score(
                            funding,
                            vol_ratio,
                            oi_change,
                            ob_ratio,
                            liq_heat,
                            volume
                        )

                        # ====================================
                        # ALERT
                        # ====================================

                        if score >= ALERT_MIN_SCORE:

                            alerts_fired += 1

                            reason_text = "\n".join(
                                [f"• {r}" for r in reasons]
                            )

                            msg = f"""
🚨 <b>ALPHA SIGNAL</b>

🔥 <b>{symbol}</b>
🏦 Exchange: {name}

💰 Price: ${last_price:.6g}
📊 24h Volume: ${volume/1e6:.2f}M

📈 Funding: {funding*100:+.4f}%
📦 OI Change: {oi_change:+.2f}%
📚 Orderbook Ratio: {ob_ratio:.2f}
⚡ Volume Spike: {vol_ratio:.2f}x

🎯 Score: <b>{score}/100</b>

{reason_text}
"""

                            print(msg)

                            send_telegram(msg)

                            time.sleep(1)

                    except Exception as e:
                        print(f"Pair error {symbol}: {e}")

            except Exception as e:
                print(f"{name} exchange error: {e}")

        print(
            f"\n✅ Scan completed | Alerts fired: {alerts_fired}\n"
        )

        time.sleep(LOOP_SECONDS)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    bot_loop()
