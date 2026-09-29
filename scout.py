#!/usr/bin/env python3
"""
SCOUT v4 — Solana observed-ATL radar.

RULES
- Raydium + Orca + Meteora candidate discovery.
- Pool age must be >= 48 hours when the source exposes a verifiable age.
- No $0.0002 ceiling.
- No -30% rule.
- No -70% rule.
- Alert when current USD price <= observed ATL * 1.05.
- Maximum 20 Telegram alerts per run.
- 24-hour cooldown per pool/token.
- First observation creates a baseline and does not alert.
- State is stored in scout_state.json.
- No GeckoTerminal, DEX Screener, Solscan or Kamino.

IMPORTANT
"Observed ATL" is the lowest price SCOUT has actually observed and saved.
It is not claimed to be a guaranteed lifetime historical ATL.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MIN_POOL_AGE_HOURS = 48
ATL_TOLERANCE = 0.05
MAX_ALERTS = 20
ALERT_COOLDOWN_HOURS = 24

RAYDIUM_PAGES = 5
RAYDIUM_PAGE_SIZE = 200
ORCA_PAGES = 5
ORCA_PAGE_SIZE = 100
METEORA_PAGES = 5
METEORA_PAGE_SIZE = 200

STATE_FILE = "scout_state.json"

RAYDIUM_URL = "https://api-v3.raydium.io"
ORCA_URL = "https://api.orca.so/v2/solana"
METEORA_URL = "https://dlmm.datapi.meteora.ag"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4V5FhV9pG2hQx5x5Z8"
QUOTE_MINTS = {SOL_MINT, USDC_MINT, USDT_MINT}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "SCOUT-Solana-ATL-Radar/4.0",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def to_float(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def first_value(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


def age_hours(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000
            created = datetime.fromtimestamp(ts, timezone.utc)
        else:
            created = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

        return (
            datetime.now(timezone.utc) - created
        ).total_seconds() / 3600
    except Exception:
        return None


def get_json(url, retries=3):
    last_error = None

    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url,
                headers=HEADERS,
                method="GET",
            )

            with urllib.request.urlopen(request, timeout=25) as response:
                return json.loads(
                    response.read().decode("utf-8")
                )

        except Exception as exc:
            last_error = exc

            if attempt + 1 < retries:
                time.sleep(1.5 * (2 ** attempt))

    raise RuntimeError(str(last_error))


def get_raydium_candidates():
    print("SCOUT: scanning Raydium...")
    candidates = []
    cursor = None

    for page in range(1, RAYDIUM_PAGES + 1):
        params = {
            "poolType": "all",
            "poolSortField": "volume24h",
            "sortType": "desc",
            "size": RAYDIUM_PAGE_SIZE,
        }

        if cursor:
            params["nextPageId"] = cursor

        url = (
            RAYDIUM_URL
            + "/pools/info/list-v2?"
            + urllib.parse.urlencode(params)
        )

        try:
            result = get_json(url)
        except Exception as exc:
            print(f"Raydium page {page} failed: {exc}")
            break

        if result.get("success") is False:
            print(
                "Raydium API error:",
                result.get("msg", "unknown"),
            )
            break

        data = result.get("data") or {}

        if isinstance(data, dict):
            items = data.get("data") or []
            cursor = data.get("nextPageId")
        else:
            items = data
            cursor = None

        if not items:
            break

        print(f"  Raydium page {page}: {len(items)} pools")

        for pool in items:
            pool_id = pool.get("id")
            if not pool_id:
                continue

            created = pool.get("openTime")
            age = age_hours(created)

            if age is None or age < MIN_POOL_AGE_HOURS:
                continue

            for token_key in ("mintA", "mintB"):
                token = pool.get(token_key) or {}
                mint = token.get("address")

                if not mint or mint in QUOTE_MINTS:
                    continue

                candidates.append({
                    "source": "Raydium",
                    "pool": pool_id,
                    "mint": mint,
                    "symbol": token.get("symbol") or "UNKNOWN",
                    "age": age,
                    "liquidity": pool.get("tvl"),
                    "volume": (pool.get("day") or {}).get("volume"),
                })

        if not cursor:
            break

        time.sleep(0.25)

    return candidates


def get_orca_candidates():
    print("SCOUT: scanning Orca...")
    candidates = []
    cursor = None

    for page in range(1, ORCA_PAGES + 1):
        params = {"size": ORCA_PAGE_SIZE}

        if cursor:
            params["next"] = cursor

        url = (
            ORCA_URL
            + "/pools?"
            + urllib.parse.urlencode(params)
        )

        try:
            result = get_json(url)
        except Exception as exc:
            print(f"Orca page {page} failed: {exc}")
            break

        items = result.get("data") or []
        meta = result.get("meta") or {}
        cursor = meta.get("next")

        if not items:
            break

        print(f"  Orca page {page}: {len(items)} pools")

        for pool in items:
            pool_id = first_value(
                pool,
                "address",
                "whirlpoolAddress",
            )

            if not pool_id:
                continue

            created = first_value(
                pool,
                "createdAt",
                "created_at",
                "creationTime",
                "creationTimestamp",
            )

            age = age_hours(created)

            # Never invent Orca pool age.
            if age is None or age < MIN_POOL_AGE_HOURS:
                continue

            for token_key in ("tokenA", "tokenB"):
                token = pool.get(token_key) or {}

                mint = first_value(
                    token,
                    "mint",
                    "address",
                )

                if not mint or mint in QUOTE_MINTS:
                    continue

                candidates.append({
                    "source": "Orca",
                    "pool": pool_id,
                    "mint": mint,
                    "symbol": first_value(
                        token,
                        "symbol",
                        "name",
                    ) or "UNKNOWN",
                    "age": age,
                    "liquidity": first_value(
                        pool,
                        "tvlUsdc",
                        "tvl",
                    ),
                    "volume": first_value(
                        pool,
                        "volume24hUsdc",
                    ),
                })

        if not cursor:
            break

        time.sleep(0.25)

    if not candidates:
        print(
            "  Orca: no pools with a verifiable >=48h age; "
            "skipping safely."
        )

    return candidates


def get_meteora_candidates():
    print("SCOUT: scanning Meteora...")
    candidates = []

    for page in range(1, METEORA_PAGES + 1):
        params = {
            "page": page,
            "page_size": METEORA_PAGE_SIZE,
            "sort_by": "volume_24h:desc",
        }

        url = (
            METEORA_URL
            + "/pools?"
            + urllib.parse.urlencode(params)
        )

        try:
            result = get_json(url)
        except Exception as exc:
            print(f"Meteora page {page} failed: {exc}")
            break

        items = result.get("data") if isinstance(result, dict) else result

        if isinstance(items, dict):
            items = items.get("data") or []

        if not items:
            break

        print(f"  Meteora page {page}: {len(items)} pools")

        for pool in items:
            pool_id = first_value(
                pool,
                "address",
                "public_key",
                "lb_pair",
            )

            if not pool_id:
                continue

            created = first_value(
                pool,
                "created_at",
                "createdAt",
                "creation_time",
                "creation_timestamp",
            )

            age = age_hours(created)

            # Never invent Meteora pool age.
            if age is None or age < MIN_POOL_AGE_HOURS:
                continue

            for token_key in (
                "token_x",
                "tokenX",
                "token_y",
                "tokenY",
            ):
                token = pool.get(token_key) or {}

                mint = first_value(
                    token,
                    "address",
                    "mint",
                    "mint_address",
                )

                if not mint or mint in QUOTE_MINTS:
                    continue

                candidates.append({
                    "source": "Meteora",
                    "pool": pool_id,
                    "mint": mint,
                    "symbol": first_value(
                        token,
                        "symbol",
                        "name",
                    ) or "UNKNOWN",
                    "age": age,
                    "liquidity": first_value(
                        pool,
                        "tvl",
                        "liquidity",
                        "tvl_usd",
                    ),
                    "volume": first_value(
                        pool,
                        "volume24h",
                        "volume_24h",
                        "volume24h_usd",
                    ),
                })

        time.sleep(0.25)

    return candidates


def get_usd_prices(mints):
    """
    Use Raydium's official mint-price endpoint for a consistent USD
    price for candidates discovered from all three DEX sources.
    """
    prices = {}
    unique = list(dict.fromkeys(mints))

    for start in range(0, len(unique), 100):
        batch = unique[start:start + 100]

        url = (
            RAYDIUM_URL
            + "/mint/price?"
            + urllib.parse.urlencode({
                "mints": ",".join(batch),
            })
        )

        try:
            result = get_json(url)
        except Exception as exc:
            print(f"USD price batch failed: {exc}")
            continue

        data = result.get("data") or {}

        if isinstance(data, dict):
            for mint, raw in data.items():
                if isinstance(raw, dict):
                    raw = raw.get("price")

                price = to_float(raw)

                if price is not None and price > 0:
                    prices[mint] = price

        time.sleep(0.15)

    return prices


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as handle:
            data = json.load(handle)

        return data if isinstance(data, dict) else {}

    except Exception as exc:
        print(f"State read failed; starting clean: {exc}")
        return {}


def save_state(state):
    temp_file = STATE_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    os.replace(temp_file, STATE_FILE)


def hours_since(value):
    if not value:
        return None

    try:
        then = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        return (
            datetime.now(timezone.utc) - then
        ).total_seconds() / 3600

    except Exception:
        return None


def evaluate(candidates, prices, state):
    alerts = []
    current_time = now_iso()

    for item in candidates:
        price = prices.get(item["mint"])

        if price is None or price <= 0:
            continue

        key = (
            f"{item['source']}:"
            f"{item['pool']}:"
            f"{item['mint']}"
        )

        record = state.get(key)

        if not isinstance(record, dict):
            # First observation establishes the baseline.
            state[key] = {
                "observed_atl": price,
                "last_price": price,
                "last_seen": current_time,
                "last_alert_at": None,
                "symbol": item["symbol"],
                "source": item["source"],
                "pool": item["pool"],
                "mint": item["mint"],
            }
            continue

        atl = to_float(record.get("observed_atl"))

        if atl is None or atl <= 0:
            atl = price
            record["observed_atl"] = atl

        # New lower low becomes the new observed ATL.
        if price < atl:
            atl = price
            record["observed_atl"] = atl

        record["last_price"] = price
        record["last_seen"] = current_time
        record["symbol"] = item["symbol"]
        record["age"] = item["age"]
        record["liquidity"] = item["liquidity"]
        record["volume"] = item["volume"]

        # Main SCOUT rule:
        # current price must be at ATL or <=5% above ATL.
        within_band = price <= atl * (1 + ATL_TOLERANCE)

        cooldown = hours_since(
            record.get("last_alert_at")
        )

        cooldown_ok = (
            cooldown is None
            or cooldown >= ALERT_COOLDOWN_HOURS
        )

        if within_band and cooldown_ok:
            alert = dict(item)
            alert["price"] = price
            alert["atl"] = atl
            alert["above_atl_pct"] = (
                ((price / atl) - 1) * 100
            )

            alerts.append((key, alert))

    # Closest to ATL first.
    alerts.sort(
        key=lambda pair: pair[1]["above_atl_pct"]
    )

    return alerts[:MAX_ALERTS]


def format_price(value):
    if value >= 1:
        return f"${value:.4f}"

    if value >= 0.01:
        return f"${value:.6f}"

    return f"${value:.12f}"


def format_money(value):
    value = to_float(value)

    if value is None:
        return "N/A"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:,.0f}"


def build_message(item):
    return (
        "🚨 SCOUT — ATL ALERT 🚨\n\n"
        f"🪙 {item['symbol']}\n"
        f"💰 Current: {format_price(item['price'])}\n"
        f"🔻 Observed ATL: {format_price(item['atl'])}\n"
        f"📏 Above ATL: {item['above_atl_pct']:.2f}%\n"
        f"⏳ Pool age: {item['age']:.1f}h\n"
        f"🏦 Source: {item['source']}\n"
        f"💧 Liquidity: {format_money(item['liquidity'])}\n"
        f"📊 Volume: {format_money(item['volume'])}\n\n"
        f"🧬 Mint:\n{item['mint']}\n\n"
        f"🏊 Pool:\n{item['pool']}\n\n"
        "RULE: current price is at observed ATL or within 5% above it.\n"
        "Observed ATL is learned from SCOUT's saved history."
    )


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing"
        )

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=20,
    ) as response:
        result = json.loads(
            response.read().decode("utf-8")
        )

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram rejected the message"
        )


def main():
    print("=" * 60)
    print("SCOUT v4 STARTING")
    print("=" * 60)
    print("Rule: current price <= observed ATL x 1.05")
    print("Pool age: >= 48 hours")
    print("Maximum alerts: 20")
    print("Cooldown: 24 hours")
    print("")

    state = load_state()

    candidates = []
    candidates.extend(get_raydium_candidates())
    candidates.extend(get_orca_candidates())
    candidates.extend(get_meteora_candidates())

    # Deduplicate source/pool/token combinations.
    unique = {}

    for item in candidates:
        key = (
            item["source"],
            item["pool"],
            item["mint"],
        )
        unique[key] = item

    candidates = list(unique.values())

    print(
        f"Candidate pool/token pairs: "
        f"{len(candidates)}"
    )

    if not candidates:
        save_state(state)
        print("No candidates.")
        return

    prices = get_usd_prices(
        [item["mint"] for item in candidates]
    )

    print(
        f"USD prices received: "
        f"{len(prices)}"
    )

    alerts = evaluate(
        candidates,
        prices,
        state,
    )

    # Save baselines/new ATLs before Telegram.
    save_state(state)

    print(
        f"ATL alerts ready: "
        f"{len(alerts)}"
    )

    sent = 0

    for key, item in alerts:
        message = build_message(item)

        print("")
        print(message)

        try:
            send_telegram(message)

            state[key]["last_alert_at"] = now_iso()
            sent += 1

            print(
                f"Telegram sent: "
                f"{item['symbol']}"
            )

        except Exception as exc:
            print(
                f"Telegram failed for "
                f"{item['symbol']}: {exc}"
            )

        time.sleep(0.5)

    # Save cooldown timestamps.
    save_state(state)

    print("")
    print(
        f"SCOUT COMPLETE — "
        f"{sent}/{len(alerts)} alerts sent"
    )


if __name__ == "__main__":
    main()
