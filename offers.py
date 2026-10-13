"""Angebotsliste für die Handy-Ansicht.

Beide Bots tragen hier ihre passenden Angebote ein, docs/index.html zeigt sie an.
Nicht angefragte Angebote fliegen nach einem Tag raus, angefragte bleiben 30 Tage.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

FILE = Path(__file__).parent / "docs" / "offers.json"
KEEP_UNREQUESTED = timedelta(hours=24)
KEEP_REQUESTED = timedelta(days=30)
REQUESTED = ("gesendet", "unklar")  # Anfrage ist raus (bei "unklar" ohne klare Bestätigung)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value):
    """ISO-Zeit -> datetime (UTC); None, wenn nicht lesbar."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load():
    try:
        store = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        store = {}
    store.setdefault("offers", {})
    return store


def add(store, key, offer, now):
    """Angebot neu aufnehmen oder aktualisieren. Seit wann es online ist und eine schon
    gesendete Anfrage bleiben erhalten."""
    old = store["offers"].get(key, {})
    merged = {**old, **{k: v for k, v in offer.items() if v is not None}}
    since = parse_time(old.get("online_since") or offer.get("online_since"))
    merged["online_since"] = iso(since or now)
    if merged.get("requested") in REQUESTED:
        merged.setdefault("requested_at", iso(now))
    store["offers"][key] = merged


def age(offer, now):
    since = parse_time(offer.get("online_since"))
    return now - since if since else timedelta(0)


def save(store, now):
    store["offers"] = {
        key: o for key, o in store["offers"].items()
        if age(o, now) < (KEEP_REQUESTED if o.get("requested") in REQUESTED else KEEP_UNREQUESTED)
    }
    store["updated"] = iso(now)
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")
