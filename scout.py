#!/usr/bin/env python3

"""
SCOUT 🚨 — SOLANA LOW-PRICE DIP RADAR

SOURCES
-------
• Raydium
• Orca Whirlpools
• Meteora DLMM

RULES
-----
• Solana only
• Pool age: 48 hours or older
• Token price: BELOW $0.0002
• 24H dip: -30% or worse
• No upward alerts
• Maximum 20 Telegram alerts

NOT USED
--------
• GeckoTerminal
• DEX Screener
• Solscan
• Kamino

IMPORTANT
---------
SCOUT never treats $0.000200 as eligible.
The token must be strictly BELOW $0.0002.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

MIN_POOL_AGE_HOURS = 48

MAX_PRICE_USD = 0.0002

DIP_THRESHOLD = -30.0

MAX_ALERTS = 20

MAX_PAGES_PER_SOURCE = 10

PAGE_SIZE = 100


# ============================================================
# API URLS
# ============================================================

RAYDIUM_URL = (
    "https://api-v3.raydium.io"
)

ORCA_URL = (
    "https://api.orca.so/v2/solana"
)

METEORA_URL = (
    "https://dlmm.datapi.meteora.ag"
)


# ============================================================
# SOLANA MINTS
# ============================================================

SOL_MINT = (
    "So11111111111111111111111111111111111111112"
)

USDC_MINT = (
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
)

USDT_MINT = (
    "Es9vMFrzaCERmJfrF4H2FYD4V5FhV9pG2hQx5x5Z8"
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID"
)


def send_telegram(message):
    """Send a Telegram message."""

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing"
        )

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST"
    )

    with urllib.request.urlopen(
        request,
        timeout=20
    ) as response:

        result = json.loads(
            response.read().decode("utf-8")
        )

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram rejected the message"
        )


# ============================================================
# HTTP
# ============================================================

API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "SCOUT-Solana-Dip-Radar/3.0"
}


def get_json(url):
    """GET JSON safely."""

    request = urllib.request.Request(
        url,
        headers=API_HEADERS,
        method="GET"
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


# ============================================================
# HELPERS
# ============================================================

def to_float(value):
    try:

        if value is None:
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def get_age_hours(created_at):
    """Convert timestamp to pool age."""

    if created_at is None:
        return None

    try:

        if isinstance(
            created_at,
            (int, float)
        ):

            timestamp = float(
                created_at
            )

            # Handle milliseconds
            if timestamp > 10_000_000_000:
                timestamp /= 1000

            created = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc
            )

        else:

            created = datetime.fromisoformat(
                str(created_at).replace(
                    "Z",
                    "+00:00"
                )
            )

            if created.tzinfo is None:
                created = created.replace(
                    tzinfo=timezone.utc
                )

        now = datetime.now(
            timezone.utc
        )

        return (
            now - created
        ).total_seconds() / 3600

    except Exception:
        return None


def is_stable_or_sol(mint):
    return mint in {
        SOL_MINT,
        USDC_MINT,
        USDT_MINT
    }


def format_price(price):

    if price is None:
        return "N/A"

    if price >= 1:
        return f"${price:.4f}"

    if price >= 0.01:
        return f"${price:.6f}"

    return f"${price:.12f}"


def format_money(value):

    number = to_float(value)

    if number is None:
        return "N/A"

    if number >= 1_000_000:
        return (
            f"${number / 1_000_000:.2f}M"
        )

    if number >= 1_000:
        return (
            f"${number / 1_000:.1f}K"
        )

    return f"${number:,.0f}"


# ============================================================
# RAYDIUM
# ============================================================

def get_raydium_pools():

    print("")
    print("🚨 SCOUT: Scanning Raydium...")

    pools = []

    next_page = None

    for page in range(
        1,
        MAX_PAGES_PER_SOURCE + 1
    ):

        params = {
            "poolType": "all",
            "poolSortField": "volume24h",
            "sortType": "desc",
            "pageSize": PAGE_SIZE
        }

        if next_page:
            params[
                "nextPageId"
            ] = next_page

        url = (
            RAYDIUM_URL
            + "/pools/info/list-v2?"
            + urllib.parse.urlencode(params)
        )

        try:

            result = get_json(url)

        except Exception as error:

            print(
                "Raydium request failed:",
                error
            )

            break

        data = result.get(
            "data",
            {}
        )

        if isinstance(data, list):
            items = data
            next_page = None

        else:
            items = data.get(
                "data",
                []
            )

            next_page = data.get(
                "nextPageId"
            )

        if not items:
            break

        print(
            f"Raydium page {page}: "
            f"{len(items)} pools"
        )

        for pool in items:

            pool_id = pool.get(
                "id"
            )

            if not pool_id:
                continue

            mint_a = pool.get(
                "mintA",
                {}
            )

            mint_b = pool.get(
                "mintB",
                {}
            )

            mint_a_address = mint_a.get(
                "address"
            )

            mint_b_address = mint_b.get(
                "address"
            )

            price_a = to_float(
                mint_a.get("price")
            )

            price_b = to_float(
                mint_b.get("price")
            )

            # ------------------------------------------------
            # Find a cheap token in the pool.
            # ------------------------------------------------

            candidates = []

            if (
                mint_a_address
                and price_a is not None
            ):
                candidates.append(
                    (
                        mint_a_address,
                        mint_a.get(
                            "symbol",
                            "UNKNOWN"
                        ),
                        price_a
                    )
                )

            if (
                mint_b_address
                and price_b is not None
            ):
                candidates.append(
                    (
                        mint_b_address,
                        mint_b.get(
                            "symbol",
                            "UNKNOWN"
                        ),
                        price_b
                    )
                )

            cheap = [
                item
                for item in candidates
                if 0 < item[2] < MAX_PRICE_USD
            ]

            if not cheap:
                continue

            token_mint, symbol, price = cheap[0]

            age_hours = get_age_hours(
                pool.get("openTime")
            )

            if age_hours is None:
                continue

            if age_hours < MIN_POOL_AGE_HOURS:
                continue

            # ------------------------------------------------
            # Raydium's current pool object can expose
            # statistics differently by pool type/version.
            # Check all known locations defensively.
            # ------------------------------------------------

            change = None

            for location in (
                pool.get("stats24h"),
                pool.get("day"),
                pool.get("statistics")
            ):

                if not isinstance(
                    location,
                    dict
                ):
                    continue

                for key in (
                    "priceChange",
                    "price_change",
                    "change"
                ):

                    value = to_float(
                        location.get(key)
                    )

                    if value is not None:
                        change = value
                        break

                if change is not None:
                    break

            if change is None:
                continue

            if change > DIP_THRESHOLD:
                continue

            pools.append(
                {
                    "source": "Raydium",
                    "pool": pool_id,
                    "mint": token_mint,
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "age": age_hours,
                    "liquidity": pool.get(
                        "tvl"
                    ),
                    "volume": (
                        pool.get(
                            "day",
                            {}
                        ).get(
                            "volume"
                        )
                    )
                }
            )

        if not next_page:
            break

        time.sleep(0.3)

    return pools


# ============================================================
# ORCA
# ============================================================

def get_orca_pools():

    print("")
    print("🚨 SCOUT: Scanning Orca...")

    pools = []

    next_cursor = None

    for page in range(
        1,
        MAX_PAGES_PER_SOURCE + 1
    ):

        params = {
            "sortBy": "volume24h",
            "sortDirection": "desc",
            "size": PAGE_SIZE,
            "stats": "24h"
        }

        if next_cursor:
            params["next"] = next_cursor

        url = (
            ORCA_URL
            + "/pools?"
            + urllib.parse.urlencode(params)
        )

        try:

            result = get_json(url)

        except Exception as error:

            print(
                "Orca request failed:",
                error
            )

            break

        items = result.get(
            "data",
            []
        )

        if not items:
            break

        print(
            f"Orca page {page}: "
            f"{len(items)} pools"
        )

        for pool in items:

            pool_id = pool.get(
                "address"
            )

            token_a = pool.get(
                "tokenA",
                {}
            )

            token_b = pool.get(
                "tokenB",
                {}
            )

            mint_a = pool.get(
                "tokenMintA"
            )

            mint_b = pool.get(
                "tokenMintB"
            )

            # Orca's price is token A
            # denominated in token B.
            #
            # For a USD-priced pair we can
            # directly evaluate token A.
            #
            # For a SOL pair we use the pool
            # relationship to identify the
            # inexpensive side.

            price = to_float(
                pool.get("price")
            )

            if price is None or price <= 0:
                continue

            cheap_mint = None
            cheap_symbol = None
            cheap_price = None

            if mint_a and mint_b:

                symbol_a = token_a.get(
                    "symbol",
                    "UNKNOWN"
                )

                symbol_b = token_b.get(
                    "symbol",
                    "UNKNOWN"
                )

                # Token A / Token B
                if is_stable_or_sol(
                    mint_b
                ):
                    cheap_mint = mint_a
                    cheap_symbol = symbol_a
                    cheap_price = price

                elif is_stable_or_sol(
                    mint_a
                ):

                    # Price is A/B,
                    # therefore B/A is
                    # the price of B.
                    if price > 0:

                        cheap_mint = mint_b
                        cheap_symbol = symbol_b
                        cheap_price = (
                            1 / price
                        )

            if (
                cheap_mint is None
                or cheap_price is None
            ):
                continue

            # For SOL-denominated prices,
            # this is not yet USD.
            #
            # We only accept a value as USD
            # when the quote is a stablecoin.
            #
            # This avoids false cheap-token
            # alerts.

            if mint_b not in {
                USDC_MINT,
                USDT_MINT
            } and mint_a not in {
                USDC_MINT,
                USDT_MINT
            }:
                continue

            if not (
                0 < cheap_price < MAX_PRICE_USD
            ):
                continue

            # Orca's current API does not expose
            # pool creation time in the list
            # response. Do not guess it.
            #
            # Therefore this pool is skipped
            # unless age is explicitly available.

            age_hours = get_age_hours(
                pool.get("createdAt")
            )

            if age_hours is None:
                continue

            if age_hours < MIN_POOL_AGE_HOURS:
                continue

            stats = pool.get(
                "stats",
                {}
            )

            stats_24h = stats.get(
                "24h",
                {}
            )

            change = to_float(
                stats_24h.get(
                    "priceChange"
                )
            )

            if change is None:
                continue

            if change > DIP_THRESHOLD:
                continue

            pools.append(
                {
                    "source": "Orca",
                    "pool": pool_id,
                    "mint": cheap_mint,
                    "symbol": cheap_symbol,
                    "price": cheap_price,
                    "change": change,
                    "age": age_hours,
                    "liquidity": pool.get(
                        "tvlUsdc"
                    ),
                    "volume": stats_24h.get(
                        "volume"
                    )
                }
            )

        meta = result.get(
            "meta",
            {}
        )

        next_cursor = meta.get(
            "next"
        )

        if not next_cursor:
            break

        time.sleep(0.3)

    return pools


# ============================================================
# METEORA
# ============================================================

def get_meteora_pools():

    print("")
    print("🚨 SCOUT: Scanning Meteora...")

    pools = []

    for page in range(
        1,
        MAX_PAGES_PER_SOURCE + 1
    ):

        params = {
            "page": page,
            "page_size": PAGE_SIZE,
            "sort_by": "volume_24h:desc",
            "filter_by": (
                "is_blacklisted=false"
            )
        }

        url = (
            METEORA_URL
            + "/pools?"
            + urllib.parse.urlencode(params)
        )

        try:

            result = get_json(url)

        except Exception as error:

            print(
                "Meteora request failed:",
                error
            )

            break

        items = result.get(
            "data",
            []
        )

        if not items:
            break

        print(
            f"Meteora page {page}: "
            f"{len(items)} pools"
        )

        for pool in items:

            pool_id = pool.get(
                "address"
            )

            if not pool_id:
                continue

            token_x = pool.get(
                "token_x",
                {}
            )

            token_y = pool.get(
                "token_y",
                {}
            )

            mint_x = token_x.get(
                "address"
            )

            mint_y = token_y.get(
                "address"
            )

            price_x = to_float(
                token_x.get("price")
            )

            price_y = to_float(
                token_y.get("price")
            )

            cheap_mint = None
            cheap_symbol = None
            cheap_price = None

            if (
                mint_x
                and price_x is not None
                and 0 < price_x < MAX_PRICE_USD
            ):

                cheap_mint = mint_x
                cheap_symbol = token_x.get(
                    "symbol",
                    "UNKNOWN"
                )
                cheap_price = price_x

            elif (
                mint_y
                and price_y is not None
                and 0 < price_y < MAX_PRICE_USD
            ):

                cheap_mint = mint_y
                cheap_symbol = token_y.get(
                    "symbol",
                    "UNKNOWN"
                )
                cheap_price = price_y

            if (
                cheap_mint is None
                or cheap_price is None
            ):
                continue

            age_hours = get_age_hours(
                pool.get("created_at")
            )

            if age_hours is None:
                continue

            if age_hours < MIN_POOL_AGE_HOURS:
                continue

            # ------------------------------------------------
            # Meteora current API provides OHLCV separately.
            # We fetch only after the cheap-price and age
            # filters have passed.
            # ------------------------------------------------

            ohlcv_url = (
                METEORA_URL
                + "/pools/"
                + urllib.parse.quote(
                    pool_id,
                    safe=""
                )
                + "/ohlcv"
            )

            try:

                candles = get_json(
                    ohlcv_url
                    + "?"
                    + urllib.parse.urlencode({
                        "timeframe": "1h",
                        "limit": 25
                    })
                )

            except Exception:

                continue

            candle_data = candles.get(
                "data",
                []
            )

            if not candle_data:
                continue

            if isinstance(
                candle_data,
                dict
            ):
                candle_data = (
                    candle_data.get(
                        "data",
                        []
                    )
                )

            if not candle_data:
                continue

            try:

                latest = candle_data[-1]
                old = candle_data[0]

                current_close = to_float(
                    latest.get("close")
                )

                old_close = to_float(
                    old.get("close")
                )

            except (
                TypeError,
                IndexError
            ):

                continue

            if (
                current_close is None
                or old_close is None
                or old_close <= 0
            ):
                continue

            change = (
                (
                    current_close
                    - old_close
                )
                / old_close
            ) * 100

            if change > DIP_THRESHOLD:
                continue

            pools.append(
                {
                    "source": "Meteora",
                    "pool": pool_id,
                    "mint": cheap_mint,
                    "symbol": cheap_symbol,
                    "price": cheap_price,
                    "change": change,
                    "age": age_hours,
                    "liquidity": pool.get(
                        "tvl"
                    ),
                    "volume": pool.get(
                        "volume",
                        {}
                    ).get(
                        "24h"
                    )
                }
            )

        time.sleep(0.3)

    return pools


# ============================================================
# COMBINE + DEDUPLICATE
# ============================================================

def get_all_alerts():

    all_pools = []

    all_pools.extend(
        get_raydium_pools()
    )

    all_pools.extend(
        get_orca_pools()
    )

    all_pools.extend(
        get_meteora_pools()
    )

    print("")
    print(
        "Total qualifying candidates:",
        len(all_pools)
    )

    # --------------------------------------------------------
    # Remove duplicate token/DEX combinations.
    # --------------------------------------------------------

    unique = {}

    for item in all_pools:

        key = (
            item["source"],
            item["pool"],
            item["mint"]
        )

        if key not in unique:
            unique[key] = item

    alerts = list(
        unique.values()
    )

    # --------------------------------------------------------
    # Deepest dips first.
    # --------------------------------------------------------

    alerts.sort(
        key=lambda item: item["change"]
    )

    # --------------------------------------------------------
    # HARD 20-ALERT CEILING.
    # --------------------------------------------------------

    return alerts[:MAX_ALERTS]


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_message(alert):

    return (
        "🚨 SCOUT 🚨\n\n"
        "🔻 SOLANA LOW-PRICE DIP\n\n"
        f"🪙 {alert['symbol']}\n"
        f"💰 Price: "
        f"{format_price(alert['price'])}\n"
        f"📉 24H Change: "
        f"{alert['change']:.2f}%\n"
        f"⏳ Pool Age: "
        f"{alert['age']:.1f} hours\n"
        f"🏦 Pool Source: "
        f"{alert['source']}\n"
        f"💧 Liquidity: "
        f"{format_money(alert['liquidity'])}\n"
        f"📊 24H Volume: "
        f"{format_money(alert['volume'])}\n\n"
        f"🧬 Mint Address:\n"
        f"{alert['mint']}\n\n"
        f"🏊 Pool Address:\n"
        f"{alert['pool']}\n\n"
        "SCOUT RULES\n"
        "• Price < $0.0002\n"
        "• Pool age ≥ 48h\n"
        "• 24H dip ≤ -30%\n"
        "• Solana only"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print("========================================")
    print("       🚨 SCOUT v3 STARTING")
    print("========================================")
    print("")
    print("Sources:")
    print("• Raydium")
    print("• Orca")
    print("• Meteora")
    print("")
    print("Price ceiling: BELOW $0.0002")
    print("Pool age: 48+ hours")
    print("Dip threshold: -30%")
    print("Maximum alerts: 20")
    print("")

    alerts = get_all_alerts()

    print("")
    print(
        f"Final alerts: {len(alerts)}"
    )
    print("")

    if not alerts:

        print(
            "No qualifying SCOUT alerts."
        )

        print(
            "🚨 SCOUT COMPLETE"
        )

        return

    sent = 0

    for alert in alerts:

        message = build_message(
            alert
        )

        print(
            message
        )

        try:

            send_telegram(
                message
            )

            sent += 1

            print(
                f"Telegram alert sent: "
                f"{alert['symbol']}"
            )

        except Exception as error:

            print(
                "Telegram failed:",
                error
            )

        time.sleep(0.5)

    print("")
    print(
        f"🚨 SCOUT COMPLETE — "
        f"{sent}/{len(alerts)} alerts sent"
    )


if __name__ == "__main__":
    main()
