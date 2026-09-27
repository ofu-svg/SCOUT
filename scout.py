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
• Telegram credentials come from GitHub Secrets
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

API_HEADERS = {
    "Accept": "application/json;version=20230302",
    "User-Agent": "SCOUT-Dip-Radar/1.0"
}


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram(message):
    """Send one message to Telegram."""

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

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

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

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

    with urllib.request.urlopen(request, timeout=30) as response:
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
        created = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
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
# GET ELIGIBLE POOLS
# ============================================================

def get_eligible_pools():
    """
    Scan pool pages until we have up to 20 pools
    that are at least 48 hours old.
    """

    eligible = []
    seen_addresses = set()

    page = 1

    # Safety limit so a bad API response cannot
    # make SCOUT scan forever.
    max_pages = 10

    while (
        len(eligible) < MAX_ELIGIBLE_POOLS
        and page <= max_pages
    ):

        query = urllib.parse.urlencode({
            "page": page,
            "sort": "h24_volume_usd_desc"
        })

        url = f"{GECKOTERMINAL_URL}?{query}"

        print(f"Scanning GeckoTerminal page {page}...")

        try:
            result = get_json(url)

        except Exception as error:
            print(
                f"Could not read page {page}: {error}"
            )
            break

        pools = result.get("data", [])

        if not pools:
            print("No more pools returned.")
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

            # If age cannot be verified,
            # do NOT treat it as an old pool.
            if age_hours is None:
                continue

            # MUST be 48 hours or older.
            if age_hours < MIN_POOL_AGE_HOURS:
                continue

            eligible.append(pool)

            print(
                f"Eligible pool {len(eligible)}/"
                f"{MAX_ELIGIBLE_POOLS}: "
                f"{attributes.get('name', 'Unknown')} "
                f"({age_hours:.1f}h)"
            )

            if len(eligible) >= MAX_ELIGIBLE_POOLS:
                break

        page += 1

        # Respect the public API.
        time.sleep(0.5)

    return eligible


# ============================================================
# CHECK FOR DIPS
# ============================================================

def find_dips(pools):
    """Return only -30% or worse pools."""

    alerts = []

    for pool in pools:

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

        # If price change is unavailable,
        # do not create an alert.
        if h24_change is None:
            continue

        # ====================================================
        # THE MAIN SCOUT RULE
        #
        # -30% or worse = ALERT
        # -29.99% or better = NO ALERT
        #
        # Positive numbers are automatically ignored.
        # ====================================================

        if h24_change > DIP_THRESHOLD:
            continue

        volume_data = attributes.get(
            "volume_usd",
            {}
        )

        liquidity = attributes.get(
            "reserve_in_usd"
        )

        volume_24h = volume_data.get(
            "h24"
        )

        dex_name = attributes.get(
            "dex_name",
            "Unknown DEX"
        )

        pool_url = (
            "https://www.geckoterminal.com/"
            f"solana/pools/{address}"
        )

        alert = {
            "name": name,
            "address": address,
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
    dex = alert["dex"]
    liquidity = alert["liquidity"]
    volume = alert["volume"]
    url = alert["url"]

    return (
        
        "🚨 SCOUT 🚨\n\n"
        "🔻 SOLANA DIP DETECTED\n\n"
        f"💎 Pool: {name}\n"
        f"📉 24H Change: {change:.2f}%\n"
        f"⏳ Pool Age: {age:.1f} hours\n"
        f"🏦 DEX: {dex}\n"
        f"💧 Liquidity: {format_money(liquidity)}\n"
        f"📊 24H Volume: {format_money(volume)}\n\n"
        f"🔗 {url}"
    )
