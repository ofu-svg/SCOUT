#!/usr/bin/env python3
"""
SCOUT v5 — Solana observed-ATL radar

Sources:
  - Raydium
  - Orca
  - Meteora DLMM

Rules:
  - Pool must be at least 48 hours old when the source provides a
    verifiable creation time.
  - For Orca, whose public pool API does not expose a pool creation
    timestamp, SCOUT requires 48 hours of its own observation history.
  - Alert only when current USD price is at or within 5% above the
    lowest price SCOUT has observed for that pool/token.
  - Maximum 20 alerts per run.
  - 24-hour alert cooldown.
  - No $0.0002 price ceiling.
  - No -30%, -45%, -50%, -70% or other dip-percentage rule.
  - No upward alerts.
  - No GeckoTerminal, DEX Screener, Solscan or Kamino.

"Observed ATL" means the lowest price SCOUT has actually observed
and persisted. It is deliberately NOT presented as a guaranteed
lifetime ATL.
"""

import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

MIN_AGE_HOURS = 48
ATL_TOLERANCE = 0.05
MAX_ALERTS = 20
ALERT_COOLDOWN_HOURS = 24

RAYDIUM_PAGES = 5
RAYDIUM_SIZE = 200
ORCA_PAGES = 5
ORCA_SIZE = 100
METEORA_PAGES = 5
METEORA_SIZE = 200

STATE_FILE = "scout_state.json"

RAYDIUM = "https://api-v3.raydium.io"
ORCA = "https://api.orca.so/v2/solana"
METEORA = "https://dlmm.datapi.meteora.ag"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

QUOTE_MINTS = {SOL_MINT, USDC_MINT}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "SCOUT-Solana-ATL-Radar/5.0",
}


def now():
    return datetime.now(timezone.utc)


def now_iso():
    return now().isoformat()


def number(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pick(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        value = obj.get(key)
        if value is not None:
            return value
    return None


def parse_time(value):
    if value is None:
        return None

    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts, timezone.utc)

        text = str(value).strip()
        if text.isdigit():
            ts = float(text)
            if ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts, timezone.utc)

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except Exception:
        return None


def age_hours(value):
    created = parse_time(value)
    if created is None:
        return None
    return (now() - created).total_seconds() / 3600


def hours_since(value):
    parsed = parse_time(value)
    if parsed is None:
        return None
    return (now() - parsed).total_seconds() / 3600


def http_json(url, retries=4):
    last_error = None

    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url,
                headers=HEADERS,
                method="GET",
            )

            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)

        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(
                f"HTTP {exc.code} {exc.reason}"
            )

            # 429/5xx can be temporary.
            if exc.code not in (429, 500, 502, 503, 504):
                break

        except Exception as exc:
            last_error = exc

        if attempt + 1 < retries:
            time.sleep(min(8, 1.5 * (2 ** attempt)))

    raise last_error or RuntimeError("HTTP request failed")


def token_mint(token):
    return pick(token, "address", "mint", "mintAddress")


def token_symbol(token):
    return pick(token, "symbol", "name") or "UNKNOWN"


def token_price(token):
    return number(
        pick(
            token,
            "price",
            "priceUsd",
            "price_usd",
        )
    )


def add_candidate(candidates, source, pool, mint, symbol, age,
                  price_usd=None, liquidity=None, volume=None):
    if not mint or mint in QUOTE_MINTS:
        return

    if not pool:
        return

    candidates.append({
        "source": source,
        "pool": str(pool),
        "mint": str(mint),
        "symbol": symbol or "UNKNOWN",
        "age": age,
        "price_usd": price_usd,
        "liquidity": liquidity,
        "volume": volume,
    })


def raydium_candidates():
    print("SCOUT: Raydium")

    candidates = []
    cursor = None

    for page in range(1, RAYDIUM_PAGES + 1):
        params = {
            "poolType": "all",
            "poolSortField": "volume24h",
            "sortType": "desc",
            "size": RAYDIUM_SIZE,
        }

        if cursor:
            params["nextPageId"] = cursor

        url = (
            RAYDIUM
            + "/pools/info/list-v2?"
            + urllib.parse.urlencode(params)
        )

        try:
            result = http_json(url)
        except Exception as exc:
            print(f"  Raydium v2 page {page} failed: {exc}")
            break

        if not result.get("success", True):
            print(
                "  Raydium v2 returned an unsuccessful response:",
                result.get("msg", "unknown"),
            )
            break

        data = result.get("data") or {}
        items = data.get("data", []) if isinstance(data, dict) else []
        cursor = data.get("nextPageId") if isinstance(data, dict) else None

        print(f"  Raydium v2 page {page}: {len(items)} pools")

        for pool in items:
            pool_id = pool.get("id")
            age = age_hours(pool.get("openTime"))

            if not pool_id or age is None or age < MIN_AGE_HOURS:
                continue

            for key in ("mintA", "mintB"):
                token = pool.get(key) or {}
                mint = token_mint(token)

                # Raydium's pool price is pair-relative. We use the
                # official Raydium mint-price endpoint later for USD.
                add_candidate(
                    candidates,
                    "Raydium",
                    pool_id,
                    mint,
                    token_symbol(token),
                    age,
                    None,
                    pool.get("tvl"),
                    (pool.get("day") or {}).get("volume"),
                )

        if not cursor:
            break

        time.sleep(0.25)

    # If list-v2 was temporarily unavailable, use the documented
    # paginated list endpoint as a fallback.
    if not candidates:
        print("  Raydium v2 produced no candidates; trying list fallback.")

        for page in range(1, RAYDIUM_PAGES + 1):
            params = {
                "poolType": "all",
                "poolSortField": "volume24h",
                "sortType": "desc",
                "pageSize": RAYDIUM_SIZE,
                "page": page,
            }

            url = (
                RAYDIUM
                + "/pools/info/list?"
                + urllib.parse.urlencode(params)
            )

            try:
                result = http_json(url)
            except Exception as exc:
                print(f"  Raydium fallback page {page} failed: {exc}")
                break

            data = result.get("data") or []
            if isinstance(data, dict):
                items = data.get("data") or []
            else:
                items = data

            print(f"  Raydium fallback page {page}: {len(items)} pools")

            for pool in items:
                pool_id = pool.get("id")
                age = age_hours(pool.get("openTime"))

                if not pool_id or age is None or age < MIN_AGE_HOURS:
                    continue

                for key in ("mintA", "mintB"):
                    token = pool.get(key) or {}
                    add_candidate(
                        candidates,
                        "Raydium",
                        pool_id,
                        token_mint(token),
                        token_symbol(token),
                        age,
                        None,
                        pool.get("tvl"),
                        (pool.get("day") or {}).get("volume"),
                    )

            if len(items) < RAYDIUM_SIZE:
                break

            time.sleep(0.25)

    return candidates


def orca_candidates(state):
    print("SCOUT: Orca")

    candidates = []

    for page in range(1, ORCA_PAGES + 1):
        params = {
            "size": ORCA_SIZE,
        }

        # Orca's public list endpoint is not treated as if it had a
        # Raydium-style cursor unless the response actually supplies one.
        url = (
            ORCA
            + "/pools?"
            + urllib.parse.urlencode(params)
        )

        try:
            result = http_json(url)
        except Exception as exc:
            print(f"  Orca page {page} failed: {exc}")
            break

        items = result.get("data") or []
        if isinstance(items, dict):
            items = items.get("data") or []

        print(f"  Orca page {page}: {len(items)} pools")

        if not items:
            break

        for pool in items:
            pool_id = pick(
                pool,
                "address",
                "whirlpoolAddress",
                "poolAddress",
            )

            if not pool_id:
                continue

            token_a = pool.get("tokenA") or {}
            token_b = pool.get("tokenB") or {}

            # Orca's public pool API does not reliably expose pool
            # creation time. We therefore measure age from first_seen.
            state_key = f"ORCA_FIRST_SEEN:{pool_id}"
            first_seen = state.get(state_key)

            if not first_seen:
                state[state_key] = now_iso()
                first_seen = state[state_key]

            observed_age = hours_since(first_seen)

            if observed_age is None or observed_age < MIN_AGE_HOURS:
                continue

            stats = pool.get("stats") or {}

            volume = (
                pick(
                    stats,
                    "volume24hUsdc",
                    "volume24h",
                )
            )

            liquidity = pick(
                pool,
                "tvlUsdc",
                "tvl",
            )

            price_a = token_price(token_a)
            price_b = token_price(token_b)
            pair_price = number(pool.get("price"))

            # If the API supplies USD token prices, use them directly.
            add_candidate(
                candidates,
                "Orca",
                pool_id,
                token_mint(token_a),
                token_symbol(token_a),
                observed_age,
                price_a,
                liquidity,
                volume,
            )

            add_candidate(
                candidates,
                "Orca",
                pool_id,
                token_mint(token_b),
                token_symbol(token_b),
                observed_age,
                price_b,
                liquidity,
                volume,
            )

            # Keep pair price available as metadata if token USD
            # prices are absent. It is not treated as USD by itself.
            _ = pair_price

        # The public endpoint's exact pagination contract can change.
        # We deliberately stop after the returned page unless a next
        # cursor is explicitly present.
        meta = result.get("meta") or {}
        next_cursor = pick(meta, "next", "nextPage", "next_page")

        if not next_cursor:
            break

        # Only continue when the API explicitly provides a cursor.
        params["next"] = next_cursor
        time.sleep(0.25)

    return candidates


def meteora_candidates():
    print("SCOUT: Meteora")

    candidates = []

    for page in range(1, METEORA_PAGES + 1):
        params = {
            "page": page,
            "page_size": METEORA_SIZE,
            "sort_by": "volume_24h:desc",
            "filter_by": "is_blacklisted=false",
        }

        url = (
            METEORA
            + "/pools?"
            + urllib.parse.urlencode(params)
        )

        try:
            result = http_json(url)
        except Exception as exc:
            print(f"  Meteora page {page} failed: {exc}")
            break

        items = result.get("data") or []
        if isinstance(items, dict):
            items = items.get("data") or []

        print(f"  Meteora page {page}: {len(items)} pools")

        if not items:
            break

        for pool in items:
            pool_id = pick(
                pool,
                "address",
                "pool_address",
                "public_key",
                "lb_pair",
            )

            if not pool_id:
                continue

            # Current Meteora Data API exposes pool_created_at.
            created = pick(
                pool,
                "pool_created_at",
                "poolCreatedAt",
                "created_at",
                "createdAt",
            )

            age = age_hours(created)

            if age is None or age < MIN_AGE_HOURS:
                continue

            token_x = pool.get("token_x") or {}
            token_y = pool.get("token_y") or {}

            current_pair_price = number(
                pick(
                    pool,
                    "current_price",
                    "currentPrice",
                )
            )

            px = token_price(token_x)
            py = token_price(token_y)

            # Meteora supplies USD token prices in the token objects.
            # Prefer those. If only the quote token has a USD price,
            # derive token X USD from the pair price.
            if px is None and current_pair_price is not None and py:
                px = current_pair_price * py

            add_candidate(
                candidates,
                "Meteora",
                pool_id,
                token_mint(token_x),
                token_symbol(token_x),
                age,
                px,
                pool.get("tvl"),
                (pool.get("volume") or {}).get("24h"),
            )

            add_candidate(
                candidates,
                "Meteora",
                pool_id,
                token_mint(token_y),
                token_symbol(token_y),
                age,
                py,
                pool.get("tvl"),
                (pool.get("volume") or {}).get("24h"),
            )

        time.sleep(0.25)

    return candidates


def raydium_usd_prices(mints):
    prices = {}
    unique = list(dict.fromkeys(mints))

    # Raydium documents /mint/price and accepts a comma-separated
    # mint list. Keep batches modest.
    for start in range(0, len(unique), 100):
        batch = unique[start:start + 100]

        url = (
            RAYDIUM
            + "/mint/price?"
            + urllib.parse.urlencode({
                "mints": ",".join(batch),
            })
        )

        try:
            result = http_json(url)
        except Exception as exc:
            print(f"  Raydium USD price batch failed: {exc}")
            continue

        data = result.get("data") or {}

        if isinstance(data, dict):
            for mint, raw in data.items():
                if isinstance(raw, dict):
                    raw = pick(raw, "price", "priceUsd", "price_usd")

                value = number(raw)

                if value is not None and value > 0:
                    prices[mint] = value

        time.sleep(0.15)

    return prices


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        return data if isinstance(data, dict) else {}

    except Exception as exc:
        print(f"State read failed; starting clean: {exc}")
        return {}


def save_state(state):
    temp = STATE_FILE + ".tmp"

    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")

    os.replace(temp, STATE_FILE)


def evaluate(candidates, fallback_prices, state):
    alerts = []
    current_time = now_iso()

    # Same source/pool/mint only once.
    unique = {}

    for item in candidates:
        key = (
            item["source"],
            item["pool"],
            item["mint"],
        )

        # Prefer a candidate that already has a source-provided USD price.
        old = unique.get(key)
        if old is None or (
            old.get("price_usd") is None
            and item.get("price_usd") is not None
        ):
            unique[key] = item

    for item in unique.values():
        price = number(item.get("price_usd"))

        if price is None:
            price = fallback_prices.get(item["mint"])

        if price is None or price <= 0:
            continue

        key = (
            f"{item['source']}:"
            f"{item['pool']}:"
            f"{item['mint']}"
        )

        record = state.get(key)

        if not isinstance(record, dict):
            # First observation is only a baseline.
            state[key] = {
                "observed_atl": price,
                "last_price": price,
                "last_seen": current_time,
                "last_alert_at": None,
                "symbol": item["symbol"],
                "source": item["source"],
                "pool": item["pool"],
                "mint": item["mint"],
                "age": item["age"],
            }
            continue

        atl = number(record.get("observed_atl"))

        if atl is None or atl <= 0:
            atl = price

        new_low = price < atl

        if new_low:
            atl = price
            record["observed_atl"] = atl

        record["last_price"] = price
        record["last_seen"] = current_time
        record["symbol"] = item["symbol"]
        record["age"] = item["age"]
        record["liquidity"] = item["liquidity"]
        record["volume"] = item["volume"]

        distance_pct = ((price / atl) - 1) * 100

        within_atl_band = price <= atl * (1 + ATL_TOLERANCE)

        since_alert = hours_since(
            record.get("last_alert_at")
        )

        cooldown_ok = (
            since_alert is None
            or since_alert >= ALERT_COOLDOWN_HOURS
        )

        if within_atl_band and cooldown_ok:
            alert = dict(item)
            alert["price_usd"] = price
            alert["atl"] = atl
            alert["above_atl_pct"] = distance_pct
            alert["new_low"] = new_low

            alerts.append((key, alert))

    alerts.sort(
        key=lambda pair: pair[1]["above_atl_pct"]
    )

    return alerts[:MAX_ALERTS]


def fmt_price(value):
    value = number(value)

    if value is None:
        return "N/A"

    if value >= 1:
        return f"${value:.4f}"

    if value >= 0.01:
        return f"${value:.6f}"

    if value >= 0.000001:
        return f"${value:.9f}"

    return f"${value:.12f}"


def fmt_money(value):
    value = number(value)

    if value is None:
        return "N/A"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:,.0f}"


def telegram(message):
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

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(
            response.read().decode("utf-8")
        )

    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message")


def alert_text(item):
    return (
        "🚨 SCOUT — ATL ALERT 🚨\n\n"
        f"🪙 {item['symbol']}\n"
        f"💰 Current: {fmt_price(item['price_usd'])}\n"
        f"🔻 Observed ATL: {fmt_price(item['atl'])}\n"
        f"📏 Above ATL: {item['above_atl_pct']:.2f}%\n"
        f"⏳ Pool age: {item['age']:.1f}h\n"
        f"🏦 Source: {item['source']}\n"
        f"💧 Liquidity: {fmt_money(item['liquidity'])}\n"
        f"📊 24h volume: {fmt_money(item['volume'])}\n\n"
        f"🧬 Mint:\n{item['mint']}\n\n"
        f"🏊 Pool:\n{item['pool']}\n\n"
        "RULE: current price is at observed ATL or within 5% above it.\n"
        "ATL is SCOUT's observed history, not a guaranteed lifetime ATL."
    )


def main():
    print("=" * 64)
    print("SCOUT v5 STARTING")
    print("=" * 64)
    print("Rule: current price <= observed ATL x 1.05")
    print("Age: >= 48h where verifiable")
    print("Orca: requires 48h of SCOUT observation history")
    print("Maximum alerts: 20")
    print("Cooldown: 24h")
    print("")

    state = load_state()

    candidates = []

    # One failing source must never stop the other sources.
    try:
        candidates.extend(raydium_candidates())
    except Exception as exc:
        print(f"Raydium scanner stopped safely: {exc}")

    try:
        candidates.extend(orca_candidates(state))
    except Exception as exc:
        print(f"Orca scanner stopped safely: {exc}")

    try:
        candidates.extend(meteora_candidates())
    except Exception as exc:
        print(f"Meteora scanner stopped safely: {exc}")

    # Persist first-seen timestamps even when no alerts exist.
    save_state(state)

    print("")
    print(f"Candidate pool/token pairs: {len(candidates)}")

    if not candidates:
        print("No candidates found.")
        return

    # Only call Raydium price service for tokens still missing a
    # source-provided USD price.
    missing_mints = [
        item["mint"]
        for item in candidates
        if number(item.get("price_usd")) is None
    ]

    fallback_prices = raydium_usd_prices(missing_mints)

    print(f"Raydium USD fallback prices: {len(fallback_prices)}")

    alerts = evaluate(
        candidates,
        fallback_prices,
        state,
    )

    # Save ATL baselines and new lows before Telegram.
    save_state(state)

    print(f"ATL alerts ready: {len(alerts)}")

    sent = 0

    for key, item in alerts:
        message = alert_text(item)

        print("")
        print(message)

        try:
            telegram(message)

            state[key]["last_alert_at"] = now_iso()
            sent += 1

            print(f"Telegram sent: {item['symbol']}")

        except Exception as exc:
            print(
                f"Telegram failed for "
                f"{item['symbol']}: {exc}"
            )

        time.sleep(0.5)

    save_state(state)

    print("")
    print(
        f"SCOUT COMPLETE — "
        f"{sent}/{len(alerts)} alerts sent"
    )


if __name__ == "__main__":
    main()
