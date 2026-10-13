#!/usr/bin/env python3
"""WBM-Wohnungsbot.

Stufe 1: neue passende Angebote melden.
Stufe 2: bei passenden Angeboten automatisch eine Besichtigungsanfrage senden.

Standardmäßig TROCKENLAUF: es wird nichts abgeschickt. Echtes Senden nur mit
WBM_SEND=1. Persönliche Daten kommen aus Umgebungsvariablen, nie in den Code:

  WBM_ANREDE (Herr/Frau/Offen)  WBM_NAME  WBM_VORNAME  WBM_STRASSE
  WBM_PLZ  WBM_ORT  WBM_EMAIL  WBM_TELEFON
Optional Push aufs Handy: NTFY_TOPIC=dein-geheimer-topic
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import rating

URL = "https://www.wbm.de/wohnungen-berlin/angebote/"
SEEN_FILE = Path(__file__).with_name("seen.json")
STATS_FILE = Path(__file__).with_name("stats.json")
TZ = ZoneInfo("Europe/Berlin")
SUMMARY_HOUR = 20  # Tagesbericht ab 20 Uhr (Berliner Zeit)
HEADERS = {"User-Agent": "Mozilla/5.0"}
# Eine Sitzung wie im Browser: das Cookie der Seite geht beim Absenden mit
# (der Spamschutz des Formulars kann sonst einen Bot vermuten).
HTTP = requests.Session()
HTTP.headers.update(HEADERS)
P = "tx_powermail_pi1[field]"

# --- Kriterien ---
MIN_ROOMS = 1
MAX_ROOMS = 2
MAX_WARMMIETE = 950.0
ALLOW_WBS = False
MAX_SENDS_PER_RUN = 3  # Sicherung gegen Fehlalarm

# Erlaubte Bezirke/Ortsteile (steht am Anfang jeder Angebotskarte)
ALLOWED_DISTRICTS = [
    "Charlottenburg", "Friedrichshain", "Kreuzberg", "Lichtenberg", "Mitte",
    "Neukölln", "Pankow", "Prenzlauer Berg", "Tiergarten", "Treptow", "Wedding",
]
# Bekannte, nicht gewünschte Bezirke (damit "unbekannt" von "nein" unterscheidbar ist)
OTHER_DISTRICTS = [
    "Spandau", "Reinickendorf", "Marzahn", "Hellersdorf", "Köpenick", "Tempelhof",
    "Schöneberg", "Steglitz", "Zehlendorf", "Wilmersdorf", "Hohenschönhausen",
    "Weißensee", "Britz", "Buckow", "Rudow", "Gropiusstadt", "Adlershof",
]


def district_ok(text):
    """True = gewünscht, False = nicht gewünscht, None = nicht erkennbar."""
    if any(text.startswith(d) for d in ALLOWED_DISTRICTS):
        return True
    if any(text.startswith(d) for d in OTHER_DISTRICTS):
        return False
    return None


def parse_price(text):
    m = re.search(r"([\d.]+,\d{2})\s*€\s*Warmmiete", text)
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def parse_rooms(text):
    hits = re.findall(r"(\d+)\s+Zimmer\b", text)
    return int(hits[-1]) if hits else None


def parse_listings(html):
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select('a[href*="/angebote/details/"]')
    listings = {}
    for a in anchors:
        href = a["href"]
        if href in listings:
            continue
        node = a
        # Hochgehen bis zum Container, der genau dieses eine Angebot samt Preis enthält
        while node.parent is not None:
            node = node.parent
            inner = {x["href"] for x in node.select('a[href*="/angebote/details/"]')}
            if len(inner) > 1:
                break
            if "Warmmiete" in node.get_text(" ", strip=True):
                text = node.get_text(" ", strip=True)
                listings[href] = {
                    "url": urljoin(URL, href),
                    "text": text,
                    "price": parse_price(text),
                    "rooms": parse_rooms(text),
                    "wbs": bool(re.search(r"\bWBS\b", text)),
                    "district": district_ok(text),
                }
                break
    return list(listings.values())


def matches(l):
    """Für Benachrichtigung: im Zweifel lieber melden als verpassen."""
    if l.get("district") is False:
        return False
    if l["price"] is None or l["rooms"] is None:
        return True
    if l.get("district") is None:  # Bezirk unklar: melden, aber Zimmer/Preis müssen passen
        return (MIN_ROOMS <= l["rooms"] <= MAX_ROOMS and l["price"] <= MAX_WARMMIETE
                and (ALLOW_WBS or not l["wbs"]))
    return matches_strict(l)


def matches_strict(l):
    """Für automatisches Senden: nur wenn alles sicher bekannt ist und passt."""
    if l.get("district") is not True:  # nur senden, wenn der Bezirk sicher passt
        return False
    if l["price"] is None or l["rooms"] is None:
        return False
    if not (MIN_ROOMS <= l["rooms"] <= MAX_ROOMS):
        return False
    if l["price"] > MAX_WARMMIETE:
        return False
    if l["wbs"] and not ALLOW_WBS:
        return False
    return True


def build_payload(form, person):
    """Wie ein Browser: alle Felder mit Standardwerten, dann deine Daten drüber.
    Versteckte Felder (Token, Objekt) kommen frisch von der Seite."""
    data = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.get("type") in ("submit", "button"):
            continue
        t = el.get("type")
        if t in ("radio", "checkbox"):
            if el.has_attr("checked"):
                data[name] = el.get("value", "on")
        elif el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            data[name] = opt.get("value", opt.get_text(strip=True)) if opt else ""
        else:
            data[name] = el.get("value", "")
    data[f"{P}[wbsvorhanden]"] = "0"  # WBS: nein
    data[f"{P}[anrede]"] = person["anrede"]
    data[f"{P}[name]"] = person["name"]
    data[f"{P}[vorname]"] = person["vorname"]
    data[f"{P}[strasse]"] = person["strasse"]
    data[f"{P}[plz]"] = person["plz"]
    data[f"{P}[ort]"] = person["ort"]
    data[f"{P}[e_mail]"] = person["email"]
    data[f"{P}[telefon]"] = person["telefon"]
    data[f"{P}[datenschutzhinweis][]"] = "1"
    data[f"{P}[__hp]"] = ""  # Honeypot muss leer bleiben
    return data


def load_person():
    keys = {
        "anrede": "WBM_ANREDE", "name": "WBM_NAME", "vorname": "WBM_VORNAME",
        "strasse": "WBM_STRASSE", "plz": "WBM_PLZ", "ort": "WBM_ORT",
        "email": "WBM_EMAIL", "telefon": "WBM_TELEFON",
    }
    person = {k: os.environ.get(v, "") for k, v in keys.items()}
    blob = os.environ.get("WBM_PERSON_JSON")  # alle Daten in einem Secret
    if blob:
        person.update({k: str(v) for k, v in json.loads(blob).items() if k in keys})
    missing = [v for k, v in keys.items() if k in ("name", "vorname", "email") and not person[k]]
    return person, missing


def fetch_expose(url):
    try:
        return HTTP.get(url, timeout=20).text
    except requests.RequestException:
        return ""


def send_request(listing, person, send, html=None):
    """Besichtigungsanfrage für ein Angebot. Gibt (status, info) zurück."""
    html = html or fetch_expose(listing["url"])
    soup = BeautifulSoup(html, "html.parser")
    form = next((f for f in soup.find_all("form") if f.find(attrs={"name": f"{P}[e_mail]"})), None)
    if form is None:
        return "fehler", "Formular nicht gefunden"
    data = build_payload(form, person)
    if not send:
        return "trockenlauf", f"{len(data)} Felder vorbereitet, nichts gesendet"
    action = urljoin(listing["url"], form.get("action", ""))
    try:
        resp = HTTP.post(action, data=data, headers={"Referer": listing["url"]}, timeout=20)
    except requests.RequestException as e:
        # Kann trotzdem angekommen sein: als unklar werten, nicht erneut senden
        return "unklar", type(e).__name__
    ok =resp.status_code == 200 and re.search(
        r"vielen dank|erfolgreich|wurde.*(gesendet|übermittelt)", resp.text, re.I
    )
    return ("gesendet" if ok else "unklar"), f"HTTP {resp.status_code}"


def notify(msg):
    print(msg + "\n")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"), timeout=10)
        except requests.RequestException:
            pass


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=1))


def load_stats(today):
    s = json.loads(STATS_FILE.read_text()) if STATS_FILE.exists() else {}
    if s.get("day") != today:  # neuer Tag: Zähler zurücksetzen
        s = {"day": today, "scanned": [], "sent": 0, "unclear": 0,
             "summary_day": s.get("summary_day", "")}
    s.setdefault("unclear", 0)
    return s


def daily_summary(stats, now):
    """Einmal pro Tag ab SUMMARY_HOUR eine Zusammenfassung melden."""
    if now.hour >= SUMMARY_HOUR and stats["summary_day"] != stats["day"]:
        msg = (f"WBM-Bot Tagesbericht: {len(stats['scanned'])} Angebote gescannt, "
               f"{stats['sent']} Anfragen abgeschickt.")
        if stats["unclear"]:
            msg += f" ({stats['unclear']} davon ohne klare Bestätigung, bitte prüfen)"
        notify(msg)
        stats["summary_day"] = stats["day"]


def main():
    send = os.environ.get("WBM_SEND") == "1"
    person, missing = load_person()
    if send and missing:
        print(f"Fehlende Variablen: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    html = HTTP.get(URL, timeout=20).text
    listings = parse_listings(html)
    if not listings:
        print("Keine Angebote gefunden - Seitenaufbau evtl. geändert.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(TZ)
    stats = load_stats(now.strftime("%Y-%m-%d"))
    stats["scanned"] = sorted(set(stats["scanned"]) | {l["url"] for l in listings})

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    new_hits = [l for l in listings if matches(l) and l["url"] not in seen]
    sends = 0
    results = []
    # Erst alle Anfragen senden, danach bewerten und melden: Geschwindigkeit geht vor
    for l in new_hits:
        expose_html = fetch_expose(l["url"])
        status = info = None
        # Schon VOR dem Senden merken und speichern: bricht der Lauf danach ab
        # (Absturz, Timeout), geht die Anfrage beim nächsten Lauf nicht doppelt raus.
        seen.add(l["url"])
        save_seen(seen)
        if matches_strict(l) and sends < MAX_SENDS_PER_RUN:
            sends += 1
            status, info = send_request(l, person, send, expose_html)
            if status in ("gesendet", "unklar"):
                stats["sent"] += 1
            if status == "unklar":
                stats["unclear"] += 1
            time.sleep(2)
        results.append((l, expose_html, status, info))
    for l, expose_html, status, info in results:
        line = f"WBM: {l['rooms']} Zi, {l['price']} € warm"
        try:
            expose_text = BeautifulSoup(expose_html, "html.parser").get_text(" ", strip=True)
            stars, why = rating.rate_listing(l, expose_text)
            line = f"{stars}  {line}" + (f"\n{why}" if why else "")
        except Exception as e:  # Bewertung darf nie den Bot stoppen
            print(f"Bewertung fehlgeschlagen: {e}", file=sys.stderr)
        line += f"\n{l['url']}"
        if status:
            line += f"\nAnfrage: {status} ({info})"
        notify(line)
    save_seen(seen)
    daily_summary(stats, now)
    STATS_FILE.write_text(json.dumps(stats, indent=1, ensure_ascii=False))
    mode = "SENDEN" if send else "TROCKENLAUF"
    print(f"[{mode}] {len(listings)} Angebote geprüft, {len(new_hits)} neue Treffer.")


if __name__ == "__main__":
    main()
