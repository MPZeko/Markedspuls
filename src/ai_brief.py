from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "latest.json"
GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
MODEL = os.environ.get("MARKEDSPULS_MODEL", "openai/gpt-4.1")


BRIEF_PROMPTS = {
    "morning": """
Udarbejd et kort, beslutningsorienteret morgenbrief på dansk om de globale
aktie- og kryptomarkeder. Dæk: 1) de vigtigste markedsnyheder siden forrige
brief, 2) seneste amerikanske lukning, 3) nattens udvikling i Asien,
4) forventninger til Europas åbning, 5) amerikanske indeksfutures, 6) renter,
centralbankforventninger, valuta, olie og andre tværgående signaler, når de er
relevante, 7) markedsstemning med fx VIX, markedsbredde og
investorpositionering, 8) dagens vigtigste makrodata, regnskaber og
begivenheder, 9) krypto: Bitcoin og Ethereum med verificerede prisbevægelser,
de vigtigste markedsdrivende kryptonyheder, spot-ETF-flows når pålidelige tal
er tilgængelige, regulering, institutionelle eller selskabsrelaterede
udviklinger, større sikkerhedshændelser og usædvanlige likvidationer. Forklar
kort, hvordan kryptosignalerne påvirker den bredere risikovillighed og
eventuelle børsnoterede kryptoaktier. Medtag kun andre kryptovalutaer, når
bevægelsen eller nyheden er væsentlig for markedet. Afslut med en klar
vurdering: risk-on, neutral eller risk-off, de tre vigtigste risici og hvad en
langsigtet investor bør holde øje med. Skeln mellem fakta og egen vurdering.
Undgå hype, rygter og unødvendig støj. På en markedshelligdag skal briefet være
kort og tydeligt angive lukkede markeder.
""",
    "premarket": """
Skriv en kort, beslutningsorienteret USA-premarket-opdatering på dansk 30
minutter før den normale amerikanske aktieåbning. Fokuser primært på det, der
har ændret sig siden morgenbriefet. Dæk: 1) S&P 500-, Nasdaq- og Dow-futures,
2) dagens udvikling i Europa, 3) offentliggjorte amerikanske nøgletal og
ændrede rente-/Fed-forventninger, 4) væsentlige premarket-regnskaber og
kursbevægelser med verificeret årsag, 5) VIX, renter og olie, hvis de påvirker
åbningen, 6) kryptoændringer siden morgenbriefet: Bitcoin og Ethereum, nye
markedsdrivende nyheder, regulering, spot-ETF-oplysninger eller større
likvidationer samt relevante premarket-bevægelser i børsnoterede kryptoaktier
som Coinbase, Strategy og større miners, når de er væsentlige, 7) samlet
forventning til åbningen og tre konkrete forhold at overvåge efter klokken
ringer. Medtag kun andre kryptovalutaer, når de er væsentlige for det samlede
marked. Skeln mellem fakta og vurdering. Undgå rygter og gentag ikke
morgenbriefets kryptostof, medmindre noget har ændret sig. Hvis der næsten ikke
er sket ændringer, så sig det kort i stedet for at fylde briefet med støj. På
amerikanske markedshelligdage skal opdateringen være kort og tydeligt angive,
at markedet er lukket.
""",
    "close": """
Skriv en kort, beslutningsorienteret dagsopsummering på dansk efter den
amerikanske aktielukning. Dæk: 1) S&P 500, Nasdaq og Dow med verificerede
slutniveauer og dagens vigtigste drivere, 2) markedsbredde, sektorrotation og
VIX, 3) renter, Fed-forventninger, dollar og olie, når de påvirkede handlen,
4) dagens vigtigste makrodata, regnskaber og selskabsbevægelser med verificeret
årsag, 5) Bitcoin og Ethereum samt væsentlige kryptonyheder, ETF-flow,
regulering, likvidationer eller sikkerhedshændelser, når pålidelige data findes,
6) relevante bevægelser i Coinbase, Strategy og større miners, 7) watchlisten
med fokus på RGTI, GOOGL, IREN og SPCX, når bevægelser eller nyheder er
væsentlige. Medtag kun andre kryptovalutaer, når de er markedsrelevante. Skeln
tydeligt mellem fakta og vurdering. Afslut med risk-on, neutral eller risk-off,
tre vigtigste læringer fra dagen og tre forhold at holde øje med næste
handelsdag. Undgå hype, rygter og unødvendig støj. På amerikanske
markedshelligdage skal opsummeringen være kort og tydeligt angive, at markedet
var lukket.
""",
}


SYSTEM_PROMPT = """
Du er redaktør for Markedspuls, et nøgternt dansk markedsbrief. Du skriver
kompakt, beslutningsorienteret og uden hype.

Ufravigelige kvalitetsregler:
- Brug udelukkende fakta i EVIDENSPAKKEN. Du må ikke supplere fra hukommelsen.
- Opfind aldrig tal, årsager, kalenderbegivenheder, ETF-flows, Fed-sandsynligheder,
  markedsbredde, positionering, sikkerhedshændelser eller nyheder.
- Hvis en ønsket oplysning ikke er dokumenteret, udelad den eller skriv kort,
  at der ikke foreligger et verificeret tal. Fravær af evidens er ikke bevis for,
  at en begivenhed ikke er sket.
- Alle væsentlige kursniveauer skal svare nøjagtigt til evidenspakken.
- Links må kun kopieres fra evidenspakkens kilder. Brug Markdown-links.
- Skeln tydeligt mellem "Fakta" og "Vurdering".
- Skriv dato og tidspunkt for datakontrollen tydeligt.
- Brug dansk talnotation. Undgå gentagelser og fyld.
- Gør overskrifter konkrete. Discord understøtter Markdown, men ikke tabeller.

Returnér kun gyldig JSON uden kodehegn med denne struktur:
{
  "discord_text": "det komplette briefing-brief i Markdown",
  "dashboard": {
    "headline": "kort samlet markedssignal",
    "lede": "én kort forklaring på 1-2 sætninger",
    "facts": ["præcis faktalinje", "præcis faktalinje", "præcis faktalinje"],
    "assessment": ["vurderingslinje", "vurderingslinje"],
    "risks": ["konkret risiko", "konkret risiko", "konkret risiko"],
    "investor_focus": "kort råd om hvad en langsigtet investor bør holde øje med",
    "calendar": ["kun verificeret begivenhed fra evidenspakken"],
    "sources": [{"label": "kort kildenavn", "url": "https://..."}]
  }
}

Dashboardets facts skal være faktuelle og konkrete. Risks skal være de tre
vigtigste risici, ikke generiske ansvarsfraskrivelser. Calendar må være en tom
liste. Sources må kun indeholde URLs, der findes ordret i evidenspakken.
"""


def compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_quote_fields = {"symbol", "price", "change_pct", "previous_close", "timestamp"}
    instruments = {
        name: {key: value for key, value in quote.items() if key in allowed_quote_fields}
        for name, quote in (payload.get("instruments") or {}).items()
    }
    watchlist = {
        name: {key: value for key, value in quote.items() if key in allowed_quote_fields}
        for name, quote in (payload.get("watchlist") or {}).items()
    }
    headlines = []
    for item in payload.get("headlines") or []:
        if not item.get("title") or not item.get("url"):
            continue
        headlines.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "published_at": item.get("published_at"),
                "topic": item.get("topic"),
            }
        )
    return {
        "updated_at": payload.get("updated_at"),
        "updated_at_copenhagen": payload.get("updated_at_copenhagen"),
        "brief_type": payload.get("brief_type"),
        "market_calendar": payload.get("market_calendar"),
        "market_regime": payload.get("market_regime"),
        "risk_assessment": payload.get("risk_assessment"),
        "changes_since_morning": payload.get("changes_since_morning"),
        "instruments": instruments,
        "watchlist": watchlist,
        "crosschecks": payload.get("crosschecks") or {},
        "headlines": headlines[:18],
        "source_catalog": [
            {
                "label": "Yahoo Finance markedsdata",
                "url": "https://finance.yahoo.com/markets/",
                "scope": "Kurser medmindre et instrument har en særskilt kilde.",
            },
            {
                "label": "Cboe VIX",
                "url": "https://www.cboe.com/tradable_products/vix/vix_historical_data/",
                "scope": "Officiel seneste VIX-lukning.",
            },
            {
                "label": "CoinGecko",
                "url": "https://www.coingecko.com/",
                "scope": "Krydstjek af Bitcoin og Ethereum, når svaret er tilgængeligt.",
            },
        ],
        "fallback_brief": payload.get("discord_text"),
    }


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Modelsvar indeholder ikke JSON")
    return json.loads(cleaned[start : end + 1])


def validate_result(result: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    discord_text = str(result.get("discord_text") or "").strip()
    dashboard = result.get("dashboard")
    if len(discord_text) < 600 or not isinstance(dashboard, dict):
        raise ValueError("Modelsvar mangler et komplet brief eller dashboard")
    if "Fakta" not in discord_text or "Vurdering" not in discord_text:
        raise ValueError("Briefet skelner ikke tydeligt mellem fakta og vurdering")

    for key, minimum in (("facts", 3), ("assessment", 2), ("risks", 3)):
        value = dashboard.get(key)
        if not isinstance(value, list) or len(value) < minimum:
            raise ValueError(f"Dashboardfeltet {key} er ufuldstændigt")

    evidence_urls = {
        item["url"]
        for item in (evidence.get("headlines") or []) + (evidence.get("source_catalog") or [])
        if item.get("url")
    }
    sources = dashboard.get("sources") or []
    dashboard["sources"] = [
        source
        for source in sources
        if isinstance(source, dict) and source.get("url") in evidence_urls
    ][:8]
    dashboard["facts"] = [str(item).strip() for item in dashboard["facts"][:5] if str(item).strip()]
    dashboard["assessment"] = [
        str(item).strip() for item in dashboard["assessment"][:4] if str(item).strip()
    ]
    dashboard["risks"] = [str(item).strip() for item in dashboard["risks"][:3] if str(item).strip()]
    dashboard["calendar"] = [
        str(item).strip()
        for item in (dashboard.get("calendar") or [])[:6]
        if str(item).strip()
    ]
    dashboard["headline"] = str(dashboard.get("headline") or "").strip()[:100]
    dashboard["lede"] = str(dashboard.get("lede") or "").strip()[:360]
    dashboard["investor_focus"] = str(dashboard.get("investor_focus") or "").strip()[:500]
    return {"discord_text": discord_text, "dashboard": dashboard}


def generate(payload: dict[str, Any], token: str) -> dict[str, Any]:
    kind = str(payload.get("brief_type") or "morning")
    evidence = compact_payload(payload)
    prompt = (
        BRIEF_PROMPTS[kind].strip()
        + "\n\nEVIDENSPAKKE (JSON):\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )
    response = requests.post(
        GITHUB_MODELS_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2026-03-10",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.strip()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
            "max_tokens": 7000,
        },
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    return validate_result(extract_json(content), evidence)


def main() -> None:
    payload = json.loads(LATEST.read_text(encoding="utf-8"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN mangler; den regelbaserede fallback bevares")

    result = generate(payload, token)
    payload["discord_text"] = result["discord_text"]
    payload["dashboard"] = result["dashboard"]
    payload["brief_engine"] = {
        "mode": "github-models",
        "model": MODEL,
        "grounding": "Kun Markedspuls-evidenspakken",
    }
    LATEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    archive = ROOT / "data" / "archive"
    matches = sorted(archive.glob(f"*-{payload.get('brief_type')}.json"))
    if matches:
        matches[-1].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"AI-redigering gennemført med {MODEL}")


if __name__ == "__main__":
    main()
