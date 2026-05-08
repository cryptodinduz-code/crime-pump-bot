# =========================================================
# INSTITUTIONAL CRIME SCANNER v3.0
# =========================================================
#
# FEATURES
# - Binance + MEXC + BloFin
# - Multi-timeframe alignment
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
# - Cooldown system
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

# =========================================================
# THRESHOLDS
# =========================================================

WATCHLIST_SCORE = 45
ALERT_SCORE = 60
ELITE_SCORE = 85

MIN_CONFLUENCE = 4

MIN_VOLUME = 5_000_000
MAX_VOLUME = 300_000_000

COOLDOWN_SECONDS = 3600

MAX_PAIRS = 300

# =========================================================
# MAJORS FILTER
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
    return "🚀 Crime Scanner v3 ONLINE"

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

        print(r.status_code)

    except Exception as e:
        print(e)

# =========================================================
# HELPERS
# =========================================================

def get_volume_spike(exchange, symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            "5m",
            limit=12
        )

        volumes = [c[5] for c in candles]

        current = volumes[-1]

        avg = (
            sum(volumes[:-1]) /
            len(volumes[:-1])
        )

        if avg == 0:
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

        first = candles[0][4]
        last = candles[-1][4]

        if first == 0:
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

        first = candles[0][4]
        last = candles[-1][4]

        if first == 0:
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

        ranges = []

        for c in candles:

            high = c[2]
            low = c[3]
            close = c[4]

            if close == 0:
                continue

            r = (
                (high - low) / close
            ) * 100

            ranges.append(r)

        if len(ranges) < 10:
            return 0

        recent = (
            sum(ranges[-5:]) / 5
        )

        old = (
            sum(ranges[:-5]) /
            len(ranges[:-5])
        )

        if old == 0:
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

        ranges = []

        for c in candles:

            high = c[2]
            low = c[3]
            close = c[4]

            if close == 0:
                continue

            ranges.append(
                ((high - low) / close) * 100
            )

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

        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)

        if ask_vol == 0:
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

        if len(asks) < 10:
            return False

        first_asks = sum(a[1] for a in asks[:10])

        total_asks = sum(a[1] for a in asks)

        if total_asks == 0:
            return False

        ratio = first_asks / total_asks

        return ratio < 0.15

    except:
        return False

# =========================================================

def get_open_interest_change(
    exchange,
    symbol
):

    try:

        oi = exchange.fetch_open_interest(
            symbol
        )

        value = (
            oi.get("openInterestAmount")
            or oi.get("openInterest")
            or oi.get("openInterestValue")
        )

        if value is None:
            return 0

        value = float(value)

        key = (
            f"{exchange.id}:{symbol}"
        )

        change = 0

        if key in prev_oi:

            old = prev_oi[key]

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

        funding = float(
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

def detect_funding_flip(
    exchange,
    symbol
):

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
    price_accel
):

    return (
        funding < 0 and
        oi_change > 10 and
        price_accel > 4
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

        greens = 0

        increasing_volume = True

        prev_volume = 0

        for c in candles:

            open_price = c[1]
            close_price = c[4]
            volume = c[5]

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

        closes = [c[4] for c in candles]

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

        closes = [c[4] for c in candles]
        highs = [c[2] for c in candles]
        volumes = [c[5] for c in candles]

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

    # =====================================================
    # VOLUME
    # =====================================================

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

    # =====================================================
    # OI
    # =====================================================

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

    # =====================================================
    # VOLATILITY
    # =====================================================

    if vol_expansion >= 2:

        score += 15
        reasons.append(
            "Volatility Expansion"
        )

    # =====================================================
    # ORDERBOOK
    # =====================================================

    if ob_ratio >= 2:

        score += 15
        reasons.append(
            f"Buy Pressure {ob_ratio:.2f}"
        )

    # =====================================================
    # ACCELERATION
    # =====================================================

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

    # =====================================================
    # FUNDING
    # =====================================================

    if funding < -0.0001:

        score += 10
        reasons.append(
            "Crowded Shorts"
        )

    # =====================================================
    # MTF
    # =====================================================

    score += mtf_score

    if mtf_score >= 60:

        reasons.append(
            "Elite Multi-TF"
        )

    # =====================================================
    # COMPRESSION BREAKOUT
    # =====================================================

    if compression_breakout:

        score += 20
        reasons.append(
            "Compression Breakout"
        )

    # =====================================================
    # THIN LIQUIDITY
    # =====================================================

    if thin_liquidity:

        score += 15
        reasons.append(
            "Thin Liquidity Above"
        )

    # =====================================================
    # FUNDING FLIP
    # =====================================================

    if funding_flip:

        score += 15
        reasons.append(
            "Funding Flip"
        )

    # =====================================================
    # SHORT SQUEEZE
    # =====================================================

    if short_squeeze:

        score += 20
        reasons.append(
            "Short Squeeze Setup"
        )

    # =====================================================
    # BUYING STACK
    # =====================================================

    if consecutive_buying:

        score += 15
        reasons.append(
            "Aggressive Buying"
        )

    # =====================================================
    # MIDCAP BONUS
    # =====================================================

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
        "🚀 Crime Scanner v3 Started"
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

                sorted_tickers = sorted(
                    tickers.items(),
                    key=lambda x: (
                        x[1].get(
                            "quoteVolume",
                            0
                        )
                    ),
                    reverse=True
                )

                scanned = 0

                for symbol, ticker in sorted_tickers:

                    try:

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

                        volume = ticker.get(
                            "quoteVolume",
                            0
                        )

                        if volume is None:
                            continue

                        volume = float(volume)

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

                        # =====================
                        # METRICS
                        # =====================

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

                        # =====================
                        # EARLY STAGE FILTER
                        # =====================

                        if move_1h > 18:
                            continue

                        # =====================
                        # CONFLUENCE
                        # =====================

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

                        # =====================
                        # SCORE
                        # =====================

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

                        # =====================
                        # WATCHLIST
                        # =====================

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

        print("Scan complete")

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
