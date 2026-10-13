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
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://www.wbm.de/wohnungen-berlin/angebote/"
SEEN_FILE = Path(__file__).with_name("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0"}
P = "tx_powermail_pi1[field]"

# --- Kriterien ---
MIN_ROOMS = 1
MAX_ROOMS = 2
MAX_WARMMIETE = 1000.0
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


def send_request(listing, person, send):
    """Besichtigungsanfrage für ein Angebot. Gibt (status, info) zurück."""
    r = requests.get(listing["url"], headers=HEADERS, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    form = next((f for f in soup.find_all("form") if f.find(attrs={"name": f"{P}[e_mail]"})), None)
    if form is None:
        return "fehler", "Formular nicht gefunden"
    data = build_payload(form, person)
    if not send:
        return "trockenlauf", f"{len(data)} Felder vorbereitet, nichts gesendet"
    action = urljoin(listing["url"], form.get("action", ""))
    resp = requests.post(action, data=data, headers=HEADERS, timeout=20)
    ok = resp.status_code == 200 and re.search(
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


def main():
    send = os.environ.get("WBM_SEND") == "1"
    person, missing = load_person()
    if send and missing:
        print(f"Fehlende Variablen: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    html = requests.get(URL, timeout=20, headers=HEADERS).text
    listings = parse_listings(html)
    if not listings:
        print("Keine Angebote gefunden - Seitenaufbau evtl. geändert.", file=sys.stderr)
        sys.exit(1)

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    new_hits = [l for l in listings if matches(l) and l["url"] not in seen]
    sends = 0
    for l in new_hits:
        line = f"WBM: {l['rooms']} Zi, {l['price']} € warm\n{l['url']}"
        if matches_strict(l) and sends < MAX_SENDS_PER_RUN:
            sends += 1
            status, info = send_request(l, person, send)
            line += f"\nAnfrage: {status} ({info})"
            time.sleep(2)
        notify(line)
        seen.add(l["url"])  # nie doppelt senden, auch bei Fehler/unklar
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=1))
    mode = "SENDEN" if send else "TROCKENLAUF"
    print(f"[{mode}] {len(listings)} Angebote geprüft, {len(new_hits)} neue Treffer.")


if __name__ == "__main__":
    main()
