from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "latest.json"
DELIVERY = ROOT / "data" / "delivery.json"


def split_message(text: str, limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(block) <= limit:
            current = block
            continue
        lines = block.splitlines()
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}".strip()
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = line[:limit]
    if current:
        chunks.append(current)
    return chunks


def webhook_with_receipt(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["wait"] = "true"
    return urlunparse(parsed._replace(query=urlencode(query)))


def safe_error(response: requests.Response) -> str:
    detail = ""
    try:
        body = response.json()
        detail = str(body.get("message") or "")[:160]
    except (ValueError, AttributeError):
        detail = response.text[:160].replace("\n", " ")
    return f"Discord HTTP {response.status_code}" + (f": {detail}" if detail else "")


def post_chunk(session: requests.Session, webhook: str, content: str) -> dict[str, Any]:
    last_error = "ukendt leveringsfejl"
    for attempt in range(1, 5):
        try:
            response = session.post(
                webhook,
                json={
                    "content": content,
                    "username": "Markedspuls",
                    "allowed_mentions": {"parse": []},
                },
                timeout=25,
            )
        except requests.RequestException as exc:
            last_error = f"Netværksfejl: {type(exc).__name__}"
            if attempt < 4:
                time.sleep(attempt * 2)
                continue
            break

        if response.status_code in (200, 204):
            if response.content:
                try:
                    return response.json()
                except ValueError:
                    return {}
            return {}

        last_error = safe_error(response)
        if response.status_code == 429 and attempt < 4:
            try:
                retry_after = float(response.json().get("retry_after", 2))
            except (ValueError, TypeError, AttributeError):
                retry_after = 2
            time.sleep(min(max(retry_after, 1), 12))
            continue
        if response.status_code >= 500 and attempt < 4:
            time.sleep(attempt * 2)
            continue
        break
    raise RuntimeError(last_error)


def save_receipt(payload: dict[str, Any], status: str, **details: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    receipt = {
        "brief_updated_at": payload.get("updated_at"),
        "discord": {
            "status": status,
            "attempted_at": now,
            **details,
        },
        "workflow": {
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_url": (
                f"{os.environ.get('GITHUB_SERVER_URL')}/"
                f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
                f"{os.environ.get('GITHUB_RUN_ID')}"
            ),
        },
    }
    DELIVERY.parent.mkdir(parents=True, exist_ok=True)
    DELIVERY.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    payload = json.loads(LATEST.read_text(encoding="utf-8"))
    text = str(payload.get("discord_text") or "").strip()
    if not text:
        save_receipt(payload, "failed", error="Briefingen er tom")
        raise RuntimeError("Briefingen er tom")

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if webhook.startswith("DISCORD_WEBHOOK_URL="):
        webhook = webhook.split("=", 1)[1].strip()
    parsed = urlparse(webhook)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"discord.com", "discordapp.com"}
        or "/api/webhooks/" not in parsed.path
    ):
        save_receipt(payload, "failed", error="Webhook-secret mangler eller er ugyldig")
        raise RuntimeError("DISCORD_WEBHOOK_URL mangler eller er ugyldig")

    chunks = split_message(text)
    try:
        session = requests.Session()
        receipt_url = webhook_with_receipt(webhook)
        for chunk in chunks:
            post_chunk(session, receipt_url, chunk)
        save_receipt(
            payload,
            "delivered",
            delivered_at=datetime.now(timezone.utc).isoformat(),
            messages=len(chunks),
        )
        print(f"Discord delivery confirmed ({len(chunks)} message(s))")
    except Exception as exc:
        save_receipt(payload, "failed", error=str(exc)[:220])
        raise


if __name__ == "__main__":
    main()
