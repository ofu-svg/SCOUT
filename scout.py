#!/usr/bin/env python3

"""
SCOUT 🚨 — SOLANA DIP RADAR

RULES
-----
• Solana pools only
• Pool age: 48 hours or older
• Maximum: 20 eligible pools per scan
• Alert: -30% or worse over 24 hours
• No upward alerts
• No alert for anything better than -30%
• Uses GeckoTerminal + DEX Screener
• Shows token mint address
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
MAX_ELIGIBLE_POOLS = 20
DIP_THRESHOLD = -30.0

GECKOTERMINAL_URL = (
    "https://api.geckoterminal.com/api/v2/"
    "networks/solana/pools"
)

DEXSCREENER_PROFILES_URL = (
    "https://api.dexscreener.com/"
    "token-profiles/latest/v1"
)

DEXSCREENER_TOKEN_PAIRS_URL = (
    "https://api.dexscreener.com/"
    "token-pairs/v1/solana/"
)

API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "SCOUT-Dip-Radar/2.0"
}


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
    """Send one message to Telegram."""

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

def get_json(url):
    """Download JSON safely."""

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
    """Convert a value to float safely."""

    try:
        if value is None:
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def get_pool_age_hours(created_at):
    """Return pool age in hours."""

    if not created_at:
        return None

    try:

        if isinstance(created_at, (int, float)):
            created = datetime.fromtimestamp(
                created_at / 1000,
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

        now = datetime.now(timezone.utc)

        age_seconds = (
            now - created
        ).total_seconds()

        return age_seconds / 3600

    except Exception:
        return None


def format_money(value):
    """Format USD values."""

    number = to_float(value)

    if number is None:
        return "N/A"

    if number >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"

    if number >= 1_000:
        return f"${number / 1_000:.1f}K"

    return f"${number:,.0f}"


# ============================================================
# GECKOTERMINAL
# ============================================================

def get_gecko_pools():
    """
    Get older Solana pools from GeckoTerminal.
    """

    eligible = []
    seen_addresses = set()

    page = 1
    max_pages = 10

    while (
        len(eligible) < MAX_ELIGIBLE_POOLS
        and page <= max_pages
    ):

        query = urllib.parse.urlencode({
            "page": page,
            "sort": "h24_volume_usd_desc"
        })

        url = (
            f"{GECKOTERMINAL_URL}?{query}"
        )

        print(
            f"Scanning GeckoTerminal page {page}..."
        )

        try:

            result = get_json(url)

        except Exception as error:

            print(
                f"GeckoTerminal page {page} "
                f"failed: {error}"
            )

            break

        pools = result.get(
            "data",
            []
        )

        if not pools:
            break

        for pool in pools:

            attributes = pool.get(
                "attributes",
                {}
            )

            address = attributes.get(
                "address"
            )

            if not address:
                continue

            if address in seen_addresses:
                continue

            seen_addresses.add(address)

            created_at = attributes.get(
                "pool_created_at"
            )

            age_hours = get_pool_age_hours(
                created_at
            )

            if age_hours is None:
                continue

            if age_hours < MIN_POOL_AGE_HOURS:
                continue

            pool["_scout_source"] = "GeckoTerminal"

            eligible.append(pool)

            print(
                f"Gecko eligible "
                f"{len(eligible)}: "
                f"{attributes.get('name', 'Unknown')} "
                f"({age_hours:.1f}h)"
            )

            if len(eligible) >= MAX_ELIGIBLE_POOLS:
                break

        page += 1

        time.sleep(0.5)

    return eligible


# ============================================================
# DEX SCREENER
# ============================================================

def get_dexscreener_pools():
    """
    Get Solana token profiles from DEX Screener,
    then inspect their Solana pools.
    """

    eligible = []
    seen_pairs = set()
    seen_tokens = set()

    print(
        "Scanning DEX Screener token profiles..."
    )

    try:

        profiles = get_json(
            DEXSCREENER_PROFILES_URL
        )

    except Exception as error:

        print(
            f"DEX Screener profiles failed: "
            f"{error}"
        )

        return eligible

    if not isinstance(profiles, list):
        return eligible

    for profile in profiles:

        if len(eligible) >= MAX_ELIGIBLE_POOLS:
            break

        if profile.get("chainId") != "solana":
            continue

        token_address = profile.get(
            "tokenAddress"
        )

        if not token_address:
            continue

        if token_address in seen_tokens:
            continue

        seen_tokens.add(token_address)

        url = (
            DEXSCREENER_TOKEN_PAIRS_URL
            + urllib.parse.quote(
                token_address,
                safe=""
            )
        )

        try:

            pairs = get_json(url)

        except Exception as error:

            print(
                f"DEX Screener token failed: "
                f"{error}"
            )

            continue

        if not isinstance(pairs, list):
            continue

        for pair in pairs:

            if len(eligible) >= MAX_ELIGIBLE_POOLS:
                break

            if pair.get("chainId") != "solana":
                continue

            pair_address = pair.get(
                "pairAddress"
            )

            if not pair_address:
                continue

            if pair_address in seen_pairs:
                continue

            seen_pairs.add(pair_address)

            created_at = pair.get(
                "pairCreatedAt"
            )

            age_hours = get_pool_age_hours(
                created_at
            )

            if age_hours is None:
                continue

            if age_hours < MIN_POOL_AGE_HOURS:
                continue

            pair["_scout_source"] = (
                "DEX Screener"
            )

            eligible.append(pair)

            base = pair.get(
                "baseToken",
                {}
            )

            print(
                f"DEX Screener eligible "
                f"{len(eligible)}: "
                f"{base.get('symbol', 'Unknown')} / "
                f"{pair.get('quoteToken', {}).get('symbol', 'Unknown')} "
                f"({age_hours:.1f}h)"
            )

            time.sleep(0.1)

    return eligible


# ============================================================
# COMBINE SOURCES
# ============================================================

def get_eligible_pools():
    """
    Combine GeckoTerminal and DEX Screener.
    10 pools from each source = 20 maximum.
    """

    print("🚨 Getting GeckoTerminal pools...")
    gecko = get_gecko_pools()

    print(
        f"GeckoTerminal pools found: {len(gecko)}"
    )

    print("🚨 Getting DEX Screener pools...")
    dex = get_dexscreener_pools()

    print(
        f"DEX Screener pools found: {len(dex)}"
    )

    combined = []
    seen = set()

    # Add maximum 10 from GeckoTerminal
    for pool in gecko[:10]:

        attributes = pool.get(
            "attributes",
            {}
        )

        address = attributes.get(
            "address"
        )

        if address and address not in seen:
            seen.add(address)
            combined.append(pool)

    # Add maximum 10 from DEX Screener
    for pool in dex[:10]:

        address = pool.get(
            "pairAddress"
        )

        if address and address not in seen:
            seen.add(address)
            combined.append(pool)

    print(
        f"Combined eligible pools: "
        f"{len(combined)}"
    )

    return combined

# ============================================================
# CHECK FOR DIPS
# ============================================================

def find_dips(pools):
    """Return only -30% or worse pools."""

    alerts = []

    for pool in pools:

        source = pool.get(
            "_scout_source"
        )

        # ====================================================
        # GECKOTERMINAL FORMAT
        # ====================================================

        if source == "GeckoTerminal":

            attributes = pool.get(
                "attributes",
                {}
            )

            name = attributes.get(
                "name",
                "Unknown Pool"
            )

            address = attributes.get(
                "address",
                ""
            )

            created_at = attributes.get(
                "pool_created_at"
            )

            age_hours = get_pool_age_hours(
                created_at
            )

            price_changes = attributes.get(
                "price_change_percentage",
                {}
            )

            h24_change = to_float(
                price_changes.get("h24")
            )

            liquidity = attributes.get(
                "reserve_in_usd"
            )

            volume_data = attributes.get(
                "volume_usd",
                {}
            )

            volume_24h = volume_data.get(
                "h24"
            )

            dex_name = attributes.get(
                "dex_name",
                "Unknown DEX"
            )

            base_token = attributes.get(
                "base_token_price_usd"
            )

            # GeckoTerminal pool API may not expose
            # mint directly in every response.
            # Keep pool address available.
            relationships = pool.get(
    "relationships",
    {}
)

base_token_data = relationships.get(
    "base_token",
    {}
).get(
    "data",
    {}
)

mint = base_token_data.get(
    "id",
    "Not available"
)

if mint.startswith("solana_"):
    mint = mint.replace(
        "solana_",
        "",
        1
    )
          )







pool_url = (
            "https://www.geckoterminal.com/"
            f"solana/pools/{address}"
        )

        # ====================================================
        # DEX SCREENER FORMAT
        # ====================================================

        else:

            base = pool.get(
                "baseToken",
                {}
            )

            quote = pool.get(
                "quoteToken",
                {}
            )

            name = (
                f"{base.get('symbol', 'Unknown')} / "
                f"{quote.get('symbol', 'Unknown')}"
            )

            address = pool.get(
                "pairAddress",
                ""
            )

            age_hours = get_pool_age_hours(
                pool.get("pairCreatedAt")
            )

            price_change = pool.get(
                "priceChange",
                {}
            )

            h24_change = to_float(
                price_change.get("h24")
            )

            liquidity_data = pool.get(
                "liquidity",
                {}
            )

            liquidity = liquidity_data.get(
                "usd"
            )

            volume_data = pool.get(
                "volume",
                {}
            )

            volume_24h = volume_data.get(
                "h24"
            )

            dex_name = pool.get(
                "dexId",
                "Unknown DEX"
            )

            mint = base.get(
                "address",
                "Not available"
            )

            pool_url = pool.get(
                "url",
                ""
            )

        # ====================================================
        # MAIN SCOUT RULE
        #
        # -30% or worse = ALERT
        # ====================================================

        if h24_change is None:
            continue

        if h24_change > DIP_THRESHOLD:
            continue

        alert = {
            "name": name,
            "address": address,
            "mint": mint,
            "change": h24_change,
            "age": age_hours,
            "dex": dex_name,
            "liquidity": liquidity,
            "volume": volume_24h,
            "url": pool_url
        }

        alerts.append(alert)

    return alerts


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_message(alert):
    """Create SCOUT Telegram alert."""

    change = alert["change"]
    age = alert["age"]
    name = alert["name"]
    mint = alert["mint"]
    dex = alert["dex"]
    liquidity = alert["liquidity"]
    volume = alert["volume"]
    url = alert["url"]

    return (
        "🚨 SCOUT 🚨\n\n"
        "🔻 SOLANA DIP DETECTED\n\n"
        f"💎 Pool: {name}\n"
        f"📍 Mint Address:\n{mint}\n"
        f"📉 24H Change: {change:.2f}%\n"
        f"⏳ Pool Age: {age:.1f} hours\n"
        f"🏦 DEX: {dex}\n"
        f"💧 Liquidity: {format_money(liquidity)}\n"
        f"📊 24H Volume: {format_money(volume)}\n\n"
        f"🔗 {url}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("🚨 SCOUT STARTING...")

    pools = get_eligible_pools()

    print(
        f"Eligible pools found: "
        f"{len(pools)}"
    )

    alerts = find_dips(pools)

    print(
        f"Alerts found: "
        f"{len(alerts)}"
    )

    for alert in alerts:

        message = build_message(
            alert
        )

        send_telegram(message)

        print(
            "Telegram alert sent: "
            f"{alert['name']}"
        )

    print("🚨 SCOUT COMPLETE")


if __name__ == "__main__":
    main()
