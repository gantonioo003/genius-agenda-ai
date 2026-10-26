"""Identidade exata de uma criação, sem depender do modelo ou do calendário."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import unicodedata

from app.services.time_rules import validate_interval


def creation_content_hash(title: str, description: str, start: datetime, end: datetime) -> str:
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str):
        raise ValueError("Informe título e descrição válidos para identificar o pedido.")
    validate_interval(start, end)
    content = {
        "version": 1,
        "title": unicodedata.normalize("NFC", title.strip()),
        "description": unicodedata.normalize("NFC", description),
        "start": start.astimezone(timezone.utc).isoformat(),
        "end": end.astimezone(timezone.utc).isoformat(),
    }
    return sha256(json.dumps(content, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
