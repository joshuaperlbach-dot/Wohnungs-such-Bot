#!/usr/bin/env python3
"""Seiten-Wächter: meldet, wenn sich der Text einer Seite ändert.

Für Vermieter, die (noch) keine Angebote online haben, z.B. die Wohnungsgenossenschaft
Lichtenberg (WGLi): dort steht nur ein Hinweistext. Ändert er sich (Angebote, Warteliste,
neue Regeln), kommt eine Push-Meldung mit Link zur Seite.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import offers
import wbm_bot

PAGES = {
    "WGLi Wohnungssuche": "https://www.wgli.de/wohnungssuche/",
}
STATE_FILE = Path(__file__).with_name("watch.json")
EVERY = timedelta(minutes=30)  # kleine Seiten nicht bei jedem Lauf abfragen


def page_text(html):
    """Nur der sichtbare Inhalt, ohne Menü, Fußzeile und Skripte."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.find(id="content") or soup.body or soup
    for tag in main.find_all(["script", "style", "nav", "footer", "form"]):
        tag.decompose()
    return re.sub(r"\s+", " ", main.get_text(" ", strip=True))


def check(name, url, state, now):
    """Eine Seite prüfen. Gibt die Push-Meldung zurück, wenn sich der Text geändert hat."""
    old = state.get(name, {})
    last = offers.parse_time(old.get("checked"))
    if last and now - last < EVERY:
        return None
    try:
        resp = wbm_bot.HTTP.get(url, timeout=20)
    except requests.RequestException:
        return None
    text = page_text(resp.text) if resp.status_code == 200 else ""
    if len(text) < 40:  # Fehlerseite oder leer: nicht als Änderung werten
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    state[name] = {"hash": digest, "checked": offers.iso(now), "changed": old.get("changed")}
    if old.get("hash") and old["hash"] != digest:
        state[name]["changed"] = offers.iso(now)
        return f"{name}: Die Seite hat sich geändert, schau mal rein.\n{url}"
    return None


def main():
    now = datetime.now(wbm_bot.TZ)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    before = json.dumps(state, sort_keys=True)
    notes = [note for name, url in PAGES.items() if (note := check(name, url, state, now))]
    if json.dumps(state, sort_keys=True) != before:
        STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    for note in notes:
        wbm_bot.notify(note)
    print(f"[Wächter] {len(PAGES)} Seiten, {len(notes)} Änderungen.")


if __name__ == "__main__":
    main()
