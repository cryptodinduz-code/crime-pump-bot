# =========================================================
# INSTITUTIONAL CRIME SCANNER v3.1
# =========================================================
#
# FIXES:
# - FIXED BloFin NoneType sorting crash
# - FIXED missing volume values
# - FIXED unstable ticker sorting
# - FIXED bad quoteVolume handling
# - BETTER error handling
# - BETTER exchange compatibility
#
# FEATURES:
# - Binance + MEXC + BloFin
# - Multi-timeframe breakout alignment
# - Compression breakout detection
# - Funding flip detection
# - Short squeeze detection
# - Thin liquidity detection
# - Volatility expansion
# - Open interest expansion
# - Consecutive buying detection
# - Market regime filter
# - Overextension protection
# - Watchlist system
# - Elite alert filtering
#
# =========================================================

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

TG_ENABLED = bool(
    TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
)

LOOP_SECONDS = 60

WATCHLIST_SCORE = 45
ALERT_SCORE = 60
ELITE_SCORE = 85

MIN_CONFLUENCE = 4

MIN_VOLUME = 5_000_000
MAX_VOLUME = 300_000_000

MAX_PAIRS = 300

COOLDOWN_SECONDS = 3600

# =========================================================
# FILTERS
# =========================================================

MAJOR_COINS = [
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "BNB",
    "DOGE",
    "ADA",
    "TRX",
    "LTC"
]

# =========================================================
# STATE
# =========================================================

prev_oi = {}
funding_history = {}
last_alerts = {}

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🚀 Crime Scanner v3.1 ONLINE"

def run_web():

    port = int(
        os.environ.get("PORT", 8080)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )

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

        print(f"Telegram: {r.status_code}")

    except Exception as e:
        print(f"Telegram error: {e}")

# =========================================================
# SAFE HELPERS
# =========================================================

def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        return float(value)

    except:
        return default

# =========================================================
# METRICS
# =========================================================

def get_volume_spike(exchange, symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            "5m",
            limit=12
        )

        if not candles or len(candles) < 6:
            return 0

        volumes = [safe_float(c[5]) for c in candles]

        current = volumes[-1]

        avg = (
            sum(volumes[:-1]) /
            len(volumes[:-1])
        )

        if avg <= 0:
            return 0

        return current / avg

    except:
        return 0

# =========================================================

def get_price_acceleration(exchange, symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            "5m",
            limit=6
        )

        if not candles or len(candles) < 6:
            return 0

        first = safe_float(candles[0][4])
        last = safe_float(candles[-1][4])

        if first <= 0:
            return 0

        return (
            (last - first) / first
        ) * 100

    except:
        return 0

# =========================================================

def get_1h_move(exchange, symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            "1h",
            limit=6
        )

        if not candles or len(candles) < 6:
            return 0

        first = safe_float(candles[0][4])
        last = safe_float(candles[-1][4])

        if first <= 0:
            return 0

        return (
            (last - first) / first
        ) * 100

    except:
        return 0

# =========================================================

def get_volatility_expansion(exchange, symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            "5m",
            limit=20
        )

        if not candles or len(candles) < 10:
            return 0

        ranges = []

        for c in candles:

            high = safe_float(c[2])
            low = safe_float(c[3])
            close = safe_float(c[4])

            if close <= 0:
                continue

            ranges.append(
                ((high - low) / close) * 100
            )

        if len(ranges) < 10:
            return 0

        recent = (
            sum(ranges[-5:]) / 5
        )

        old = (
            sum(ranges[:-5]) /
            len(ranges[:-5])
        )

        if old <= 0:
            return 0

        return recent / old

    except:
        return 0

# =========================================================

def detect_compression_breakout(exchange, symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            "1h",
            limit=24
        )

        if not candles or len(candles) < 10:
            return False

        ranges = []

        for c in candles:

            high = safe_float(c[2])
            low = safe_float(c[3])
            close = safe_float(c[4])

            if close <= 0:
                continue

            ranges.append(
                ((high - low) / close) * 100
            )

        if len(ranges) < 10:
            return False

        recent_vol = (
            sum(ranges[-3:]) / 3
        )

        old_vol = (
            sum(ranges[:-3]) /
            len(ranges[:-3])
        )

        compression = old_vol < 2
        breakout = recent_vol > old_vol * 1.8

        return compression and breakout

    except:
        return False

# =========================================================

def analyze_orderbook(exchange, symbol):

    try:

        ob = exchange.fetch_order_book(
            symbol,
            limit=20
        )

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        if not bids or not asks:
            return 0

        bid_vol = sum(
            safe_float(b[1]) for b in bids
        )

        ask_vol = sum(
            safe_float(a[1]) for a in asks
        )

        if ask_vol <= 0:
            return 0

        return bid_vol / ask_vol

    except:
        return 0

# =========================================================

def detect_thin_liquidity(exchange, symbol):

    try:

        ob = exchange.fetch_order_book(
            symbol,
            limit=50
        )

        asks = ob.get("asks", [])

        if not asks or len(asks) < 10:
            return False

        first_asks = sum(
            safe_float(a[1])
            for a in asks[:10]
        )

        total_asks = sum(
            safe_float(a[1])
            for a in asks
        )

        if total_asks <= 0:
            return False

        ratio = first_asks / total_asks

        return ratio < 0.15

    except:
        return False

# =========================================================

def get_open_interest_change(exchange, symbol):

    try:

        oi = exchange.fetch_open_interest(
            symbol
        )

        value = (
            oi.get("openInterestAmount")
            or oi.get("openInterest")
            or oi.get("openInterestValue")
        )

        value = safe_float(value)

        if value <= 0:
            return 0

        key = (
            f"{exchange.id}:{symbol}"
        )

        change = 0

        if key in prev_oi:

            old = safe_float(prev_oi[key])

            if old > 0:

                change = (
                    (value - old) / old
                ) * 100

        prev_oi[key] = value

        return change

    except:
        return 0

# =========================================================

def get_funding(exchange, symbol):

    try:

        fr = exchange.fetch_funding_rate(
            symbol
        )

        funding = safe_float(
            fr.get("fundingRate", 0)
        )

        key = (
            f"{exchange.id}:{symbol}"
        )

        if key not in funding_history:
            funding_history[key] = []

        funding_history[key].append(
            funding
        )

        funding_history[key] = (
            funding_history[key][-5:]
        )

        return funding

    except:
        return 0

# =========================================================

def detect_funding_flip(exchange, symbol):

    key = f"{exchange.id}:{symbol}"

    if key not in funding_history:
        return False

    history = funding_history[key]

    if len(history) < 3:
        return False

    return (
        history[0] < 0 and
        history[-1] > history[0]
    )

# =========================================================

def detect_short_squeeze(
    funding,
    oi_change,
    accel
):

    return (
        funding < 0 and
        oi_change > 10 and
        accel > 4
    )

# =========================================================

def detect_consecutive_buying(
    exchange,
    symbol
):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            "5m",
            limit=5
        )

        if not candles or len(candles) < 5:
            return False

        greens = 0

        increasing_volume = True

        prev_volume = 0

        for c in candles:

            open_price = safe_float(c[1])
            close_price = safe_float(c[4])
            volume = safe_float(c[5])

            if close_price > open_price:
                greens += 1

            if volume < prev_volume:
                increasing_volume = False

            prev_volume = volume

        return (
            greens >= 4 and
            increasing_volume
        )

    except:
        return False

# =========================================================

def market_regime_filter(exchange):

    try:

        candles = exchange.fetch_ohlcv(
            "BTC/USDT:USDT",
            "1h",
            limit=50
        )

        if not candles or len(candles) < 20:
            return True

        closes = [
            safe_float(c[4])
            for c in candles
        ]

        current = closes[-1]

        ema = (
            sum(closes[-20:]) / 20
        )

        return current > ema

    except:
        return True

# =========================================================

def analyze_timeframe(
    exchange,
    symbol,
    tf
):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            tf,
            limit=40
        )

        if not candles or len(candles) < 15:
            return 0

        closes = [
            safe_float(c[4])
            for c in candles
        ]

        highs = [
            safe_float(c[2])
            for c in candles
        ]

        volumes = [
            safe_float(c[5])
            for c in candles
        ]

        current_close = closes[-1]

        recent_high = max(
            highs[-10:-1]
        )

        avg_volume = (
            sum(volumes[:-1]) /
            len(volumes[:-1])
        )

        current_volume = volumes[-1]

        ema = (
            sum(closes[-9:]) / 9
        )

        score = 0

        if current_close > recent_high:
            score += 1

        if current_close > ema:
            score += 1

        if (
            avg_volume > 0 and
            current_volume >
            avg_volume * 1.5
        ):
            score += 1

        if (
            closes[-1] >
            closes[-2] >
            closes[-3]
        ):
            score += 1

        return score

    except:
        return 0

# =========================================================

def get_mtf_alignment(
    exchange,
    symbol
):

    tf = {

        "5m": analyze_timeframe(
            exchange,
            symbol,
            "5m"
        ),

        "15m": analyze_timeframe(
            exchange,
            symbol,
            "15m"
        ),

        "1h": analyze_timeframe(
            exchange,
            symbol,
            "1h"
        ),

        "4h": analyze_timeframe(
            exchange,
            symbol,
            "4h"
        )
    }

    score = 0

    if tf["5m"] >= 3:
        score += 10

    if tf["15m"] >= 3:
        score += 20

    if tf["1h"] >= 3:
        score += 30

    if tf["4h"] >= 3:
        score += 40

    return score, tf

# =========================================================
# SCORING
# =========================================================

def calculate_score(
    volume,
    vol_ratio,
    oi_change,
    vol_expansion,
    ob_ratio,
    accel,
    funding,
    mtf_score,
    compression_breakout,
    thin_liquidity,
    funding_flip,
    short_squeeze,
    consecutive_buying
):

    score = 0
    reasons = []

    if vol_ratio >= 5:

        score += 30
        reasons.append(
            f"Extreme Volume {vol_ratio:.1f}x"
        )

    elif vol_ratio >= 3:

        score += 20
        reasons.append(
            f"Strong Volume {vol_ratio:.1f}x"
        )

    if oi_change >= 20:

        score += 30
        reasons.append(
            f"OI Explosion +{oi_change:.1f}%"
        )

    elif oi_change >= 10:

        score += 20
        reasons.append(
            f"Strong OI +{oi_change:.1f}%"
        )

    if vol_expansion >= 2:

        score += 15
        reasons.append(
            "Volatility Expansion"
        )

    if ob_ratio >= 2:

        score += 15
        reasons.append(
            f"Buy Pressure {ob_ratio:.2f}"
        )

    if accel >= 8:

        score += 25
        reasons.append(
            f"Explosive Move +{accel:.1f}%"
        )

    elif accel >= 4:

        score += 15
        reasons.append(
            f"Momentum +{accel:.1f}%"
        )

    if funding < -0.0001:

        score += 10
        reasons.append(
            "Crowded Shorts"
        )

    score += mtf_score

    if compression_breakout:

        score += 20
        reasons.append(
            "Compression Breakout"
        )

    if thin_liquidity:

        score += 15
        reasons.append(
            "Thin Liquidity Above"
        )

    if funding_flip:

        score += 15
        reasons.append(
            "Funding Flip"
        )

    if short_squeeze:

        score += 20
        reasons.append(
            "Short Squeeze Setup"
        )

    if consecutive_buying:

        score += 15
        reasons.append(
            "Aggressive Buying"
        )

    if (
        5_000_000 <= volume <=
        80_000_000
    ):

        score += 10
        reasons.append(
            "Midcap Profile"
        )

    return score, reasons

# =========================================================
# MAIN LOOP
# =========================================================

def bot_loop():

    send_telegram(
        "🚀 Crime Scanner v3.1 Started"
    )

    while True:

        print(
            f"\nSCAN STARTED "
            f"{datetime.datetime.utcnow()}"
        )

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

            try:

                print(f"\n🔍 Scanning {name}")

                bullish_market = (
                    market_regime_filter(
                        exchange
                    )
                )

                if not bullish_market:

                    print(
                        "Bad market regime"
                    )

                    continue

                markets = exchange.load_markets()

                tickers = exchange.fetch_tickers()

                # =====================================================
                # FIXED NONE SORTING ISSUE HERE
                # =====================================================

                valid_tickers = []

                for symbol, ticker in tickers.items():

                    try:

                        volume = safe_float(
                            ticker.get(
                                "quoteVolume",
                                0
                            )
                        )

                        valid_tickers.append(
                            (
                                symbol,
                                ticker,
                                volume
                            )
                        )

                    except:
                        continue

                sorted_tickers = sorted(
                    valid_tickers,
                    key=lambda x: x[2],
                    reverse=True
                )

                scanned = 0

                for item in sorted_tickers:

                    try:

                        symbol = item[0]
                        ticker = item[1]
                        volume = item[2]

                        if scanned >= MAX_PAIRS:
                            break

                        if "USDT" not in symbol:
                            continue

                        if symbol not in markets:
                            continue

                        market = markets[symbol]

                        if not (
                            market.get("swap")
                            or market.get("future")
                        ):
                            continue

                        base = (
                            symbol.split("/")[0]
                        )

                        if base in MAJOR_COINS:
                            continue

                        if (
                            volume < MIN_VOLUME
                            or
                            volume > MAX_VOLUME
                        ):
                            continue

                        scanned += 1

                        key = (
                            f"{name}:{symbol}"
                        )

                        now = time.time()

                        if key in last_alerts:

                            if (
                                now -
                                last_alerts[key]
                                <
                                COOLDOWN_SECONDS
                            ):
                                continue

                        # =================================================
                        # METRICS
                        # =================================================

                        vol_ratio = (
                            get_volume_spike(
                                exchange,
                                symbol
                            )
                        )

                        oi_change = (
                            get_open_interest_change(
                                exchange,
                                symbol
                            )
                        )

                        vol_expansion = (
                            get_volatility_expansion(
                                exchange,
                                symbol
                            )
                        )

                        ob_ratio = (
                            analyze_orderbook(
                                exchange,
                                symbol
                            )
                        )

                        accel = (
                            get_price_acceleration(
                                exchange,
                                symbol
                            )
                        )

                        funding = (
                            get_funding(
                                exchange,
                                symbol
                            )
                        )

                        mtf_score, tf = (
                            get_mtf_alignment(
                                exchange,
                                symbol
                            )
                        )

                        compression_breakout = (
                            detect_compression_breakout(
                                exchange,
                                symbol
                            )
                        )

                        thin_liquidity = (
                            detect_thin_liquidity(
                                exchange,
                                symbol
                            )
                        )

                        funding_flip = (
                            detect_funding_flip(
                                exchange,
                                symbol
                            )
                        )

                        short_squeeze = (
                            detect_short_squeeze(
                                funding,
                                oi_change,
                                accel
                            )
                        )

                        consecutive_buying = (
                            detect_consecutive_buying(
                                exchange,
                                symbol
                            )
                        )

                        move_1h = (
                            get_1h_move(
                                exchange,
                                symbol
                            )
                        )

                        # =================================================
                        # OVEREXTENSION FILTER
                        # =================================================

                        if move_1h > 18:
                            continue

                        # =================================================
                        # CONFLUENCE
                        # =================================================

                        confluence = 0

                        if vol_ratio > 3:
                            confluence += 1

                        if oi_change > 10:
                            confluence += 1

                        if vol_expansion > 1.5:
                            confluence += 1

                        if ob_ratio > 1.5:
                            confluence += 1

                        if accel > 4:
                            confluence += 1

                        if mtf_score > 30:
                            confluence += 1

                        if compression_breakout:
                            confluence += 1

                        if short_squeeze:
                            confluence += 1

                        if confluence < MIN_CONFLUENCE:
                            continue

                        # =================================================
                        # SCORE
                        # =================================================

                        score, reasons = (
                            calculate_score(
                                volume,
                                vol_ratio,
                                oi_change,
                                vol_expansion,
                                ob_ratio,
                                accel,
                                funding,
                                mtf_score,
                                compression_breakout,
                                thin_liquidity,
                                funding_flip,
                                short_squeeze,
                                consecutive_buying
                            )
                        )

                        # =================================================
                        # TIERS
                        # =================================================

                        tier = None

                        if score >= ELITE_SCORE:
                            tier = "ELITE"

                        elif score >= ALERT_SCORE:
                            tier = "ALERT"

                        elif score >= WATCHLIST_SCORE:
                            tier = "WATCHLIST"

                        if tier is None:
                            continue

                        last_alerts[key] = now

                        reasons_text = "\n".join(
                            [
                                f"• {r}"
                                for r in reasons
                            ]
                        )

                        msg = f"""
🚨 <b>{tier} CRIME SIGNAL</b>

🔥 <b>{symbol}</b>
🏦 {name}

🎯 Score: <b>{score}</b>

📊 Volume: ${volume/1e6:.1f}M
⚡ Vol Spike: {vol_ratio:.2f}x
📦 OI: {oi_change:.2f}%
📈 Vol Expansion: {vol_expansion:.2f}
📚 OB Ratio: {ob_ratio:.2f}
🚀 Acceleration: {accel:.2f}%
💸 Funding: {funding*100:.4f}%

🧠 MTF:
5m={tf['5m']}
15m={tf['15m']}
1h={tf['1h']}
4h={tf['4h']}

{reasons_text}
"""

                        print(msg)

                        send_telegram(msg)

                        time.sleep(1)

                    except Exception as e:

                        print(
                            f"Pair error "
                            f"{symbol}: {e}"
                        )

            except Exception as e:

                print(
                    f"{name} error: {e}"
                )

        print("\n✅ Scan complete")

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
