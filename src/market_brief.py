from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
}

NEWS_TICKERS = ["^GSPC", "BTC-USD", "ETH-USD", "COIN", "MSTR", "NVDA", "GOOGL"]


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
    """Track material SpaceX headlines without pretending there is a stock quote."""
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

    regime = "risk-on" if score >= 2 else "risk-off" if score <= -2 else "neutral"
    return {"regime": regime, "score": score, "drivers": drivers[:4]}


def fmt(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def move(item: Quote | None) -> str:
    if item is None:
        return "n/a"
    sign = "+" if item.change_pct is not None and item.change_pct >= 0 else ""
    return f"{fmt(item.price)} ({sign}{fmt(item.change_pct)}%)"


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
) -> str:
    stamp = now.astimezone(COPENHAGEN).strftime("%d.%m.%Y kl. %H:%M %Z")
    title = brief_title(kind)
    if not status["open"]:
        return f"## {title}\n**{stamp}**\n\n🇺🇸 {status['reason']}\nKort opdatering: Ingen normal amerikansk aktiehandel i dag."

    sections = [
        f"## {title}",
        f"**Data hentet {stamp}**",
        "",
        f"**Samlet signal: {assessment['regime'].upper()}** (regelbaseret score {assessment['score']:+d})",
    ]
    if assessment["drivers"]:
        sections.append(" • " + " · ".join(assessment["drivers"]))

    if kind == "morning":
        sections.extend([
            "",
            "**USA – seneste bevægelse**",
            f"S&P 500 {move(markets.get('S&P 500'))} · Nasdaq {move(markets.get('Nasdaq'))} · Dow {move(markets.get('Dow Jones'))}",
            "",
            "**Asien**",
            f"Nikkei {move(markets.get('Nikkei 225'))} · Hang Seng {move(markets.get('Hang Seng'))} · Shanghai {move(markets.get('Shanghai Composite'))}",
        ])
    elif kind == "premarket":
        sections.extend([
            "",
            "**USA-futures**",
            f"S&P {move(markets.get('S&P futures'))} · Nasdaq {move(markets.get('Nasdaq futures'))} · Dow {move(markets.get('Dow futures'))}",
            "",
            "**Europa**",
            f"Euro Stoxx 50 {move(markets.get('Euro Stoxx 50'))} · DAX {move(markets.get('DAX'))}",
        ])
    else:
        sections.extend([
            "",
            "**USA**",
            f"S&P 500 {move(markets.get('S&P 500'))} · Nasdaq {move(markets.get('Nasdaq'))} · Dow {move(markets.get('Dow Jones'))}",
        ])

    sections.extend([
        "",
        "**Tværgående signaler**",
        f"VIX {move(markets.get('VIX'))} · USA 10-årig {move(markets.get('USA 10-årig'))} · WTI {move(markets.get('WTI-olie'))} · EUR/USD {move(markets.get('EUR/USD'))}",
        "",
        "**Krypto**",
        f"Bitcoin {move(markets.get('Bitcoin'))} · Ethereum {move(markets.get('Ethereum'))}",
    ])

    movers = top_movers(watchlist)
    if movers:
        sections.extend(["", "**Watchlist – største bevægelser**"])
        sections.append(" · ".join(f"{item.symbol} {item.change_pct:+.2f}%" for _, item in movers))

    if headlines:
        sections.extend(["", "**Udvalgte overskrifter – automatisk kildefeed**"])
        for item in headlines[:4]:
            sections.append(f"• [{item['title'][:140]}]({item['url']}) — {item['source']}")

    sections.extend([
        "",
        "**Fakta:** Kurser og ændringer er maskinelt hentede. **Vurdering:** Risk-signalet følger faste regler og er ikke investeringsrådgivning.",
        "SpaceX følges som unoteret selskabsnyhed; der findes ingen offentlig SpaceX-aktiekurs.",
    ])
    return "\n".join(sections)[:1950]


def post_discord(text: str) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL is missing")
    response = requests.post(
        webhook,
        json={"content": text, "allowed_mentions": {"parse": []}},
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
    watchlist = get_quotes(WATCHLIST)
    headlines = get_headlines()
    assessment = risk_assessment(markets)
    text = make_discord_text(args.brief, now, status, markets, watchlist, assessment, headlines)

    payload = {
        "status": "ok",
        "updated_at": now.isoformat(),
        "updated_at_copenhagen": now.astimezone(COPENHAGEN).isoformat(),
        "brief_type": args.brief,
        "market_calendar": status,
        "market_regime": assessment["regime"],
        "risk_assessment": assessment,
        "instruments": serialise_quotes(markets),
        "watchlist": serialise_quotes(watchlist),
        "private_company_watch": ["SpaceX"],
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
