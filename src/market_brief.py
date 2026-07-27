from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal
import requests
import yfinance as yf


COPENHAGEN = ZoneInfo("Europe/Copenhagen")
NEW_YORK = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

MARKETS = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Dow Jones": "^DJI",
    "VIX": "^VIX",
    "S&P futures": "ES=F",
    "Nasdaq futures": "NQ=F",
    "Dow futures": "YM=F",
    "Euro Stoxx 50": "^STOXX50E",
    "DAX": "^GDAXI",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
    "Shanghai Composite": "000001.SS",
    "USA 10-årig": "^TNX",
    "EUR/USD": "EURUSD=X",
    "Dollarindeks": "DX-Y.NYB",
    "WTI-olie": "CL=F",
    "Guld": "GC=F",
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
}

WATCHLIST = {
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "Nvidia": "NVDA",
    "Amazon": "AMZN",
    "Alphabet": "GOOGL",
    "Meta": "META",
    "Tesla": "TSLA",
    "Coinbase": "COIN",
    "Strategy": "MSTR",
    "IonQ": "IONQ",
    "Quantum Computing": "QUBT",
    "MP Materials": "MP",
    "Rigetti": "RGTI",
    "IREN": "IREN",
    "SpaceX": "SPCX",
}

NEWS_TICKERS = ["^GSPC", "BTC-USD", "ETH-USD", "COIN", "MSTR", "NVDA", "GOOGL", "SPCX"]


@dataclass
class Quote:
    symbol: str
    price: float | None
    change_pct: float | None
    previous_close: float | None
    currency: str | None
    timestamp: str


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def quote(symbol: str) -> Quote:
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="5d", interval="1d", auto_adjust=False)
    price = previous = None
    if not history.empty:
        closes = history["Close"].dropna()
        if len(closes):
            price = finite(closes.iloc[-1])
        if len(closes) > 1:
            previous = finite(closes.iloc[-2])

    # Futures and crypto can move after the latest daily bar. Prefer live-ish
    # fast_info when available, but preserve the prior daily close as baseline.
    try:
        live = finite(ticker.fast_info.get("last_price"))
        fast_previous = finite(ticker.fast_info.get("previous_close"))
        price = live if live is not None else price
        previous = fast_previous if fast_previous is not None else previous
        currency = ticker.fast_info.get("currency")
    except Exception:
        currency = None

    change = None
    if price is not None and previous not in (None, 0):
        change = (price / previous - 1) * 100
    return Quote(
        symbol=symbol,
        price=price,
        change_pct=change,
        previous_close=previous,
        currency=currency,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def get_quotes(items: dict[str, str]) -> dict[str, Quote]:
    symbols = list(dict.fromkeys(items.values()))
    downloaded = yf.download(
        tickers=symbols,
        period="5d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
        timeout=15,
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    result: dict[str, Quote] = {}
    for name, symbol in items.items():
        price = previous = None
        try:
            if len(symbols) == 1:
                closes = downloaded["Close"].dropna()
            else:
                closes = downloaded["Close"][symbol].dropna()
            if len(closes):
                price = finite(closes.iloc[-1])
            if len(closes) > 1:
                previous = finite(closes.iloc[-2])
        except Exception as exc:
            print(f"warning: {symbol}: {exc}")
        change = None
        if price is not None and previous not in (None, 0):
            change = (price / previous - 1) * 100
        result[name] = Quote(
            symbol=symbol,
            price=price,
            change_pct=change,
            previous_close=previous,
            currency=None,
            timestamp=timestamp,
        )
    return result


def get_intraday_quote(symbol: str, fallback: Quote) -> Quote:
    """Use an intraday bar for instruments that trade before the US equity open."""
    try:
        downloaded = yf.download(
            tickers=symbol,
            period="1d",
            interval="5m",
            prepost=True,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=15,
        )
        closes = downloaded["Close"].dropna()
        if hasattr(closes, "columns"):
            closes = closes.iloc[:, 0]
        price = finite(closes.iloc[-1]) if len(closes) else None
        if price is None:
            return fallback
        previous = fallback.price
        change = None
        if previous not in (None, 0):
            change = (price / previous - 1) * 100
        return Quote(
            symbol=symbol,
            price=price,
            change_pct=change,
            previous_close=previous,
            currency=fallback.currency,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        print(f"warning: intraday {symbol}: {exc}")
        return fallback


def get_official_vix(fallback: Quote) -> Quote:
    """Use Cboe's official VIX history instead of treating a stale Yahoo row as live."""
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Markedspuls/1.0"})
        response.raise_for_status()
        rows = list(csv.DictReader(StringIO(response.text)))
        if not rows:
            return fallback
        latest = rows[-1]
        previous_row = rows[-2] if len(rows) > 1 else None
        price = finite(latest.get("CLOSE"))
        previous = finite(previous_row.get("CLOSE")) if previous_row else None
        change = None
        if price is not None and previous not in (None, 0):
            change = (price / previous - 1) * 100
        return Quote(
            symbol="^VIX",
            price=price,
            change_pct=change,
            previous_close=previous,
            currency=None,
            timestamp=f"{latest.get('DATE')}T20:00:00+00:00",
        )
    except Exception as exc:
        print(f"warning: official VIX: {exc}")
        return fallback


def extract_news_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    content = raw.get("content") or raw
    title = content.get("title")
    canonical = content.get("canonicalUrl") or {}
    link = canonical.get("url") if isinstance(canonical, dict) else canonical
    if not link:
        click = content.get("clickThroughUrl") or {}
        link = click.get("url") if isinstance(click, dict) else click
    provider = content.get("provider") or {}
    provider_name = provider.get("displayName") if isinstance(provider, dict) else provider
    published = content.get("pubDate") or content.get("providerPublishTime")
    if not title or not link:
        return None
    return {
        "title": re.sub(r"\s+", " ", str(title)).strip(),
        "url": str(link),
        "source": provider_name or "Yahoo Finance",
        "published_at": published,
    }


def get_headlines(limit: int = 8) -> list[dict[str, Any]]:
    headlines: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_by_symbol: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(lambda s: yf.Ticker(s).news or [], symbol): symbol for symbol in NEWS_TICKERS}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                raw_by_symbol[symbol] = future.result(timeout=12)
            except Exception as exc:
                print(f"warning: news {symbol}: {exc}")
    for symbol in NEWS_TICKERS:
        try:
            for raw in raw_by_symbol.get(symbol, []):
                item = extract_news_item(raw)
                if item and item["url"] not in seen:
                    seen.add(item["url"])
                    headlines.append(item)
        except Exception as exc:
            print(f"warning: news {symbol}: {exc}")
    headlines.extend(get_spacex_headlines())
    return headlines[:limit]


def get_spacex_headlines() -> list[dict[str, Any]]:
    """Track material headlines for the now-public SpaceX ticker SPCX."""
    url = (
        "https://news.google.com/rss/search"
        "?q=SpaceX%20(site%3Aspacex.com%20OR%20site%3Areuters.com)"
        "&hl=en-US&gl=US&ceid=US%3Aen"
    )
    try:
        response = requests.get(url, timeout=12, headers={"User-Agent": "Markedspuls/1.0"})
        response.raise_for_status()
        root = ET.fromstring(response.content)
        results = []
        for item in root.findall("./channel/item")[:3]:
            title = item.findtext("title")
            link = item.findtext("link")
            source = item.find("source")
            if title and link:
                results.append(
                    {
                        "title": re.sub(r"\s+", " ", title).strip(),
                        "url": link,
                        "source": source.text if source is not None else "SpaceX-nyhedsfeed",
                        "published_at": item.findtext("pubDate"),
                        "topic": "SpaceX",
                    }
                )
        return results
    except Exception as exc:
        print(f"warning: SpaceX news: {exc}")
        return []


def nyse_status(now: datetime) -> dict[str, Any]:
    calendar = mcal.get_calendar("NYSE")
    today_ny = now.astimezone(NEW_YORK).date()
    schedule = calendar.schedule(start_date=today_ny, end_date=today_ny)
    if schedule.empty:
        return {"open": False, "reason": "Amerikanske aktiemarkeder er lukket i dag."}
    market_open = schedule.iloc[0]["market_open"].to_pydatetime()
    market_close = schedule.iloc[0]["market_close"].to_pydatetime()
    return {
        "open": True,
        "market_open": market_open.isoformat(),
        "market_close": market_close.isoformat(),
    }


def pct(quotes: dict[str, Quote], name: str) -> float | None:
    item = quotes.get(name)
    return item.change_pct if item else None


def risk_assessment(quotes: dict[str, Quote]) -> dict[str, Any]:
    score = 0
    drivers: list[str] = []

    sp = pct(quotes, "S&P futures")
    nq = pct(quotes, "Nasdaq futures")
    vix = quotes.get("VIX")
    btc = pct(quotes, "Bitcoin")
    oil = pct(quotes, "WTI-olie")
    rate = quotes.get("USA 10-årig")
    europe = pct(quotes, "Euro Stoxx 50")

    for label, value in [("S&P-futures", sp), ("Nasdaq-futures", nq)]:
        if value is not None:
            if value >= 0.35:
                score += 1
                drivers.append(f"{label} er tydeligt positive")
            elif value <= -0.35:
                score -= 1
                drivers.append(f"{label} er tydeligt negative")

    if vix and vix.price is not None:
        if vix.price >= 25:
            score -= 2
            drivers.append("VIX signalerer høj uro")
        elif vix.price >= 20:
            score -= 1
            drivers.append("VIX er forhøjet")
        elif vix.price < 16:
            score += 1
            drivers.append("VIX er lav")

    if btc is not None:
        if btc >= 2:
            score += 1
            drivers.append("Bitcoin understøtter risikovilligheden")
        elif btc <= -2:
            score -= 1
            drivers.append("Bitcoin svækker risikovilligheden")

    if oil is not None and oil >= 4:
        score -= 1
        drivers.append("Kraftig olieprisbevægelse øger inflationsrisikoen")
    elif oil is not None and oil <= -4:
        score += 1
        drivers.append("Lavere olie dæmper det kortsigtede inflationspres")

    if rate and rate.price is not None and rate.previous_close is not None:
        rate_bp = (rate.price - rate.previous_close) * 100
        if rate_bp >= 5:
            score -= 1
            drivers.append("Den 10-årige rente stiger mindst 5 bp")
        elif rate_bp <= -5:
            score += 1
            drivers.append("Den 10-årige rente falder mindst 5 bp")

    if europe is not None:
        if europe >= 1:
            score += 1
            drivers.append("Europæiske aktier deltager i fremgangen")
        elif europe <= -1:
            score -= 1
            drivers.append("Europæiske aktier er under bredt pres")

    regime = "risk-on" if score >= 3 else "risk-off" if score <= -3 else "neutral"
    return {"regime": regime, "score": score, "drivers": drivers[:4]}


def fmt(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def move(item: Quote | None) -> str:
    if item is None:
        return "n/a"
    sign = "+" if item.change_pct is not None and item.change_pct >= 0 else ""
    return f"{fmt(item.price)} ({sign}{fmt(item.change_pct)}%)"


def yield_move(item: Quote | None) -> str:
    if item is None or item.price is None:
        return "n/a"
    bp = None
    if item.previous_close is not None:
        bp = (item.price - item.previous_close) * 100
    bp_text = "n/a" if bp is None else f"{bp:+.1f} bp".replace(".", ",")
    return f"{fmt(item.price)}% ({bp_text})"


def weekday_date(now: datetime) -> str:
    weekdays = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
    local = now.astimezone(COPENHAGEN)
    return f"{weekdays[local.weekday()]} {local.strftime('%d.%m.%Y')}"


def market_reading(markets: dict[str, Quote]) -> str:
    sp = pct(markets, "S&P futures")
    nq = pct(markets, "Nasdaq futures")
    oil = pct(markets, "WTI-olie")
    rate = markets.get("USA 10-årig")
    rate_bp = None
    if rate and rate.price is not None and rate.previous_close is not None:
        rate_bp = (rate.price - rate.previous_close) * 100

    if sp is not None and nq is not None and sp > 0.5 and nq > 0.5:
        lead = "Amerikanske futures peger på en tydeligt positiv åbning."
    elif sp is not None and sp > 0:
        lead = "Amerikanske futures peger svagt op, men signalet er foreløbig begrænset."
    elif sp is not None and sp < -0.5:
        lead = "Amerikanske futures peger på en svag åbning og lavere risikovillighed."
    else:
        lead = "Amerikanske futures ligger tæt på uændret."

    context = []
    if oil is not None and oil <= -4:
        context.append("Det markante oliefald reducerer det kortsigtede inflationspres")
    elif oil is not None and oil >= 4:
        context.append("Den kraftige oliestigning øger inflations- og renterisikoen")
    if rate_bp is not None and rate_bp >= 3:
        context.append("stigende lange renter begrænser dog kvaliteten af risk-on-signalet")
    elif rate_bp is not None and rate_bp <= -3:
        context.append("faldende lange renter understøtter især vækstaktier")
    return lead + (" " + ", mens ".join(context) + "." if context else "")


def changes_since_morning(
    kind: str, now: datetime, markets: dict[str, Quote]
) -> list[dict[str, Any]]:
    if kind != "premarket":
        return []
    date = now.astimezone(COPENHAGEN).strftime("%Y-%m-%d")
    morning_path = DATA_DIR / "archive" / f"{date}-morning.json"
    if not morning_path.exists():
        return []
    try:
        morning = json.loads(morning_path.read_text(encoding="utf-8"))
        old_instruments = morning.get("instruments", {})
    except (OSError, json.JSONDecodeError):
        return []

    result: list[dict[str, Any]] = []
    for name in [
        "S&P futures",
        "Nasdaq futures",
        "Dow futures",
        "VIX",
        "USA 10-årig",
        "Bitcoin",
        "Ethereum",
    ]:
        current = markets.get(name)
        old_price = finite((old_instruments.get(name) or {}).get("price"))
        if not current or current.price is None or old_price in (None, 0):
            continue
        if name == "USA 10-årig":
            delta = (current.price - old_price) * 100
            unit = "bp"
        elif name == "VIX":
            delta = current.price - old_price
            unit = "point"
        else:
            delta = (current.price / old_price - 1) * 100
            unit = "%"
        result.append(
            {
                "name": name,
                "morning": old_price,
                "current": current.price,
                "delta": delta,
                "unit": unit,
            }
        )
    return result


def change_line(item: dict[str, Any]) -> str:
    delta = finite(item.get("delta"))
    if delta is None:
        return f"**{item.get('name')}:** n/a"
    unit = item.get("unit")
    if unit == "bp":
        rendered = f"{delta:+.1f} bp".replace(".", ",")
    elif unit == "point":
        rendered = f"{delta:+.2f} point".replace(".", ",")
    else:
        rendered = f"{delta:+.2f}%".replace(".", ",")
    return f"**{item.get('name')}:** {rendered} siden morgenbriefet"


def top_movers(watchlist: dict[str, Quote], count: int = 5) -> list[tuple[str, Quote]]:
    valid = [(name, item) for name, item in watchlist.items() if item.change_pct is not None]
    return sorted(valid, key=lambda pair: abs(pair[1].change_pct or 0), reverse=True)[:count]


def brief_title(kind: str) -> str:
    return {
        "morning": "Morgenbrief",
        "premarket": "USA premarket",
        "close": "Dagens markedsopsummering",
    }[kind]


def make_discord_text(
    kind: str,
    now: datetime,
    status: dict[str, Any],
    markets: dict[str, Quote],
    watchlist: dict[str, Quote],
    assessment: dict[str, Any],
    headlines: list[dict[str, Any]],
    morning_changes: list[dict[str, Any]],
) -> str:
    stamp = now.astimezone(COPENHAGEN).strftime("%d.%m.%Y kl. %H:%M %Z")
    title = brief_title(kind)
    if not status["open"]:
        return f"## {title}\n**{stamp}**\n\n🇺🇸 {status['reason']}\nKort opdatering: Ingen normal amerikansk aktiehandel i dag."

    call = {
        "risk-on": "Forsigtigt risk-on",
        "risk-off": "Forsigtigt risk-off",
        "neutral": "Neutral",
    }[assessment["regime"]]
    sections = [f"# {title} — {weekday_date(now)}", f"**Data kontrolleret {stamp}.**", ""]

    if kind == "premarket":
        sections.extend([
            "## Det vigtigste siden morgenbriefet",
            market_reading(markets),
        ])
        if morning_changes:
            sections.extend([f"- {change_line(item)}" for item in morning_changes])
        else:
            sections.append(
                "Der findes endnu ikke et kvalitetssikret morgen-snapshot fra samme handelsdag."
            )
        sections.extend([
            "",
            "## Futures",
            f"- **S&P 500-futures:** {move(markets.get('S&P futures'))}",
            f"- **Nasdaq 100-futures:** {move(markets.get('Nasdaq futures'))}",
            f"- **Dow-futures:** {move(markets.get('Dow futures'))}",
            "",
            "## Europa",
            f"- **Euro Stoxx 50:** {move(markets.get('Euro Stoxx 50'))}",
            f"- **DAX:** {move(markets.get('DAX'))}",
        ])
    elif kind == "morning":
        sections.extend([
            f"## Samlet signal: {call}",
            market_reading(markets),
            "",
            "## Seneste amerikanske lukning",
            f"- **S&P 500:** {move(markets.get('S&P 500'))}",
            f"- **Nasdaq Composite:** {move(markets.get('Nasdaq'))}",
            f"- **Dow Jones:** {move(markets.get('Dow Jones'))}",
            "",
            "## Asien og amerikanske futures",
            f"- **Nikkei 225:** {move(markets.get('Nikkei 225'))}",
            f"- **Hang Seng:** {move(markets.get('Hang Seng'))}",
            f"- **Shanghai Composite:** {move(markets.get('Shanghai Composite'))}",
            f"- **S&P 500-futures:** {move(markets.get('S&P futures'))}",
            f"- **Nasdaq 100-futures:** {move(markets.get('Nasdaq futures'))}",
        ])
    else:
        sections.extend([
            f"## Samlet signal: {call}",
            market_reading(markets),
            "",
            "## USA ved lukning",
            f"- **S&P 500:** {move(markets.get('S&P 500'))}",
            f"- **Nasdaq Composite:** {move(markets.get('Nasdaq'))}",
            f"- **Dow Jones:** {move(markets.get('Dow Jones'))}",
        ])

    sections.extend([
        "",
        "## Renter, VIX, olie og valuta",
        f"- **10-årig Treasury:** {yield_move(markets.get('USA 10-årig'))}",
        f"- **VIX:** {move(markets.get('VIX'))}",
        f"- **WTI:** {move(markets.get('WTI-olie'))}",
        f"- **EUR/USD:** {move(markets.get('EUR/USD'))}",
        "",
        "## Krypto",
        f"- **Bitcoin:** {move(markets.get('Bitcoin'))}",
        f"- **Ethereum:** {move(markets.get('Ethereum'))}",
        "Der er ikke indlæst verificerede ETF-flow-, regulerings- eller likvidationsdata i denne nøglefri version.",
    ])

    movers = top_movers(watchlist)
    if movers:
        sections.extend(["", "## Watchlist — seneste officielle lukning"])
        sections.append(
            "Disse er **ikke verificerede premarket-kurser**: "
            + " · ".join(f"{item.symbol} {item.change_pct:+.2f}%" for _, item in movers)
        )

    if headlines:
        sections.extend(["", "## Udvalgte markedsoverskrifter"])
        approved = [
            item for item in headlines
            if item.get("source") in {"Reuters", "Yahoo Finance", "MT Newswires", "CoinDesk"}
        ]
        for item in approved[:3]:
            sections.append(f"• [{item['title'][:140]}]({item['url']}) — {item['source']}")

    sections.extend([
        "",
        f"## Forventning: **{call}**",
        f"**Fakta:** De viste markedsdata er maskinelt hentede. Den regelbaserede score er {assessment['score']:+d}.",
        f"**Vurdering:** {market_reading(markets)}",
        "",
        "### Tre forhold at overvåge",
        "1. Om futuresbevægelsen holder efter den amerikanske åbning.",
        "2. Om olie og den 10-årige rente fortsætter i samme retning.",
        "3. Om markedsbredden bekræfter indeksbevægelsen.",
        "",
        "_SpaceX indgår i watchlisten som SPCX._",
    ])
    return "\n".join(sections)


def split_discord(text: str, limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}".strip()
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block
    if current:
        chunks.append(current[:limit])
    return chunks


def post_discord(text: str) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL is missing")
    for chunk in split_discord(text):
        response = requests.post(
            webhook,
            json={"content": chunk, "allowed_mentions": {"parse": []}},
            timeout=20,
        )
        response.raise_for_status()


def serialise_quotes(quotes: dict[str, Quote]) -> dict[str, dict[str, Any]]:
    return {name: asdict(item) for name, item in quotes.items()}


def save_payload(payload: dict[str, Any], kind: str, now: datetime) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive = DATA_DIR / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    (DATA_DIR / "latest.json").write_text(content + "\n", encoding="utf-8")
    date = now.astimezone(COPENHAGEN).strftime("%Y-%m-%d")
    (archive / f"{date}-{kind}.json").write_text(content + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", choices=["morning", "premarket", "close"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    status = nyse_status(now)
    markets = get_quotes(MARKETS)
    markets["USA 10-årig"] = get_intraday_quote("^TNX", markets["USA 10-årig"])
    markets["VIX"] = get_official_vix(markets["VIX"])
    watchlist = get_quotes(WATCHLIST)
    headlines = get_headlines()
    assessment = risk_assessment(markets)
    morning_changes = changes_since_morning(args.brief, now, markets)
    text = make_discord_text(
        args.brief,
        now,
        status,
        markets,
        watchlist,
        assessment,
        headlines,
        morning_changes,
    )

    payload = {
        "feed_version": 2,
        "status": "ok",
        "updated_at": now.isoformat(),
        "updated_at_copenhagen": now.astimezone(COPENHAGEN).isoformat(),
        "brief_type": args.brief,
        "market_calendar": status,
        "market_regime": assessment["regime"],
        "risk_assessment": assessment,
        "changes_since_morning": morning_changes,
        "instruments": serialise_quotes(markets),
        "watchlist": serialise_quotes(watchlist),
        "headlines": headlines,
        "discord_text": text,
        "methodology": {
            "facts": "Kurser og procentændringer hentes maskinelt via Yahoo Finance.",
            "assessment": "Risk-regimet beregnes ud fra faste tærskler for futures, VIX, Bitcoin og olie.",
        },
    }
    save_payload(payload, args.brief, now)

    print(text)
    if not args.dry_run:
        post_discord(text)


if __name__ == "__main__":
    main()
