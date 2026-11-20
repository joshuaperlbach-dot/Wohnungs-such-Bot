"""Angebotsliste für die Handy-Ansicht.

Beide Bots tragen hier ihre passenden Angebote ein, docs/index.html zeigt sie an.
Nicht angefragte Angebote fliegen nach einem Tag raus, angefragte bleiben 30 Tage.
"""
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

FILE = Path(__file__).parent / "docs" / "offers.json"
# Fotos angefragter Wohnungen werden kopiert: Die Gesellschaften löschen sie, sobald das Angebot offline ist
PHOTO_DIR = FILE.parent / "img"
MAX_PHOTOS = 15
MAX_PHOTO_BYTES = 4_000_000
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
    for field in ("images", "plans"):
        if any(is_local(u) for u in old.get(field) or []):
            merged[field] = old[field]  # schon gesicherte Fotos und Grundrisse behalten
    since = parse_time(old.get("online_since") or offer.get("online_since"))
    merged["online_since"] = iso(since or now)
    if merged.get("requested") in REQUESTED:
        merged.setdefault("requested_at", iso(now))
    store["offers"][key] = merged


def age(offer, now):
    since = parse_time(offer.get("online_since"))
    return now - since if since else timedelta(0)


def is_local(url):
    return isinstance(url, str) and url.startswith("img/")


def photo_folder(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def keep_photos(store, key, http, limit=MAX_PHOTOS):
    """Fotos und Grundrisse eines angefragten Angebots nach docs/img/<ordner>/ kopieren und in der Liste
    auf die Kopien umstellen. Gibt die Zahl der gesicherten Bilder zurück; Fehler lassen den Bot weiterlaufen."""
    o = store["offers"].get(key)
    if not o or o.get("photos_kept"):
        return 0
    o["photos_kept"] = True  # nur ein Versuch pro Angebot
    folder = photo_folder(key)
    total = 0
    for field, prefix in (("images", ""), ("plans", "grundriss-")):
        out = []
        for i, url in enumerate(u for u in (o.get(field) or []) if not is_local(u)):
            if len(out) >= limit or not str(url).startswith("https://"):
                continue
            try:
                r = http.get(url, timeout=15)
            except Exception:
                continue
            kind = r.headers.get("Content-Type", "")
            if r.status_code != 200 or not kind.startswith("image/") or len(r.content) > MAX_PHOTO_BYTES:
                continue
            ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(kind.split(";")[0], "jpg")
            (PHOTO_DIR / folder).mkdir(parents=True, exist_ok=True)
            name = f"{prefix}{i + 1:02d}.{ext}"
            (PHOTO_DIR / folder / name).write_bytes(r.content)
            out.append(f"img/{folder}/{name}")
        if out:
            o[field] = out
        total += len(out)
    return total


def drop_unused_photos(store):
    """Ordner von Angeboten, die nicht mehr in der Liste stehen, wieder löschen."""
    used = {u.split("/")[1] for o in store["offers"].values()
            for u in (o.get("images") or []) + (o.get("plans") or []) if is_local(u)}
    if PHOTO_DIR.is_dir():
        for d in PHOTO_DIR.iterdir():
            if d.is_dir() and d.name not in used:
                shutil.rmtree(d, ignore_errors=True)


def save(store, now):
    store["offers"] = {
        key: o for key, o in store["offers"].items()
        if age(o, now) < (KEEP_REQUESTED if o.get("requested") in REQUESTED else KEEP_UNREQUESTED)
    }
    drop_unused_photos(store)
    store["updated"] = iso(now)
    # Für die Anzeige in der App: lief der Bot von selbst oder von Hand? Von selbst heißt:
    # GitHub-Zeitplan oder der Wecker (cron-job.org). Nur der Knopf in der App meldet sich als "app".
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event == "schedule" or (event and os.environ.get("RUN_SOURCE") != "app"):
        store["last_auto"] = iso(now)
    elif event:
        store["last_manual"] = iso(now)
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")
