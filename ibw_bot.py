#!/usr/bin/env python3
"""Wohnungsbot für inberlinwohnen.de.

Die Seite bündelt die Angebote der landeseigenen Gesellschaften (degewo, Gesobau,
Gewobag, Howoge, Stadt und Land, WBM, dazu Berlinovo). Dieser Bot findet und meldet nur:
neue passende Angebote kommen als Push mit Sternen und Link zur Gesellschaft.
Angefragt wird hier nichts. WBM-Angebote übernimmt weiter wbm_bot.py (mit Anfrage).

Kriterien (Zimmer, Miete, WBS) kommen aus wbm_bot.py, die Bewertung aus rating.py.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import howoge
import offers
import rating
import wbm_bot
from wbm_bot import ALLOW_WBS, MAX_KALTMIETE, MAX_ROOMS, MAX_WARMMIETE, MIN_ROOMS

URL = "https://www.inberlinwohnen.de/wohnungsfinder/"
SEEN_FILE = Path(__file__).with_name("seen_ibw.json")
MAX_PAGES = 80          # 10 Angebote pro Seite, neueste zuerst
MIN_STARS = 3           # schlechter bewertete Angebote nicht melden
FIRST_RUN_TOP = 10      # erster Lauf: nur die besten als eine Sammelmeldung
SKIP_COMPANIES = {"wbm"}  # macht wbm_bot.py

# Die Seite nennt nur den Bezirk, nicht den Ortsteil. Erlaubt sind die Bezirke,
# in denen die gewünschten Ortsteile aus wbm_bot.ALLOWED_DISTRICTS liegen.
ALLOWED_BEZIRKE = [
    "Charlottenburg-Wilmersdorf", "Friedrichshain-Kreuzberg", "Lichtenberg", "Mitte",
    "Neukölln", "Pankow", "Treptow-Köpenick",
]
OTHER_BEZIRKE = [
    "Spandau", "Reinickendorf", "Marzahn-Hellersdorf", "Steglitz-Zehlendorf",
    "Tempelhof-Schöneberg",
]
# Für die Bewertung: Postleitzahlen, die ganz in Prenzlauer Berg liegen
PRENZLAUER_BERG_PLZ = {"10405", "10407", "10409", "10435", "10437", "10439"}


def plain(v):
    """Livewire verpackt Werte als [wert, {"s": ...}] - auspacken."""
    if isinstance(v, list) and len(v) == 2 and isinstance(v[1], dict) and "s" in v[1]:
        return plain(v[0])
    if isinstance(v, list):
        return [plain(x) for x in v]
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    return v


def num(v):
    """'1.074,60 €' / '2,0' / 680.32 -> Zahl; None wenn nicht lesbar."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d.]*(?:,\d+)?", str(v or ""))
    return float(m.group(0).replace(".", "").replace(",", ".")) if m else None


def district_ok(district):
    """True = gewünscht, False = nicht gewünscht, None = nicht erkennbar."""
    if district in ALLOWED_BEZIRKE:
        return True
    if district in OTHER_BEZIRKE:
        return False
    return None


def parse_page(html):
    """Alle Angebote einer Ergebnisseite. Die Daten stecken als JSON in jeder Karte."""
    listings = []
    for card in BeautifulSoup(html, "html.parser").select('[id^="apartment-"][wire\\:snapshot]'):
        try:
            item = plain(json.loads(card["wire:snapshot"])["data"]["item"])
            listings.append(parse_item(item, card.get_text(" ", strip=True)))
        except (KeyError, TypeError, ValueError) as e:
            print(f"Karte {card.get('id')} nicht lesbar: {e}", file=sys.stderr)
    return listings


def parse_item(item, text):
    addr = item.get("address") or {}
    # Sichtbare Zeilen der Karte ("Gesamtmiete", "WBS", ...) als label -> wert
    details = {}
    for group in item.get("details") or []:
        for d in group if isinstance(group, list) else [group]:
            if isinstance(d, dict) and d.get("label"):
                details[d["label"]] = d.get("value")
    wbs_text = str(details.get("WBS") or "").lower()
    rooms = num(item.get("rooms"))
    try:
        coord = (float(addr["lat"]), float(addr["lon"]))
    except (KeyError, TypeError, ValueError):
        coord = None
    # Manche Angebote haben falsche Koordinaten: außerhalb Berlins = unbekannt
    if coord and not (52.3 <= coord[0] <= 52.7 and 13.0 <= coord[1] <= 13.8):
        coord = None
    return {
        "id": int(item["id"]),
        "url": item.get("deeplink") or URL,
        "title": item.get("title") or "",
        "company": ((item.get("company") or {}).get("intern") or "").strip().lower(),
        "rooms": int(rooms) if rooms is not None and rooms.is_integer() else rooms,
        "size": num(item.get("area")),
        "cold": num(item.get("rentNet")),
        # Gesamtmiete (mit Heizung) steht nur in den sichtbaren Zeilen
        "price": num(details.get("Gesamtmiete")) or num(item.get("rentGross")),
        "wbs": None if not wbs_text else "nicht" not in wbs_text,
        "floor": item.get("level") if isinstance(item.get("level"), int) else None,
        "levels": item.get("levelsTotal") if isinstance(item.get("levelsTotal"), int) else None,
        "year": int(year) if (year := num(item.get("constructionYear"))) and 1800 < year < 2100 else None,
        "coord": coord,
        "bezirk": addr.get("district") or "",
        "plz": str(addr.get("zipCode") or ""),
        "address": f"{addr.get('street', '')} {addr.get('number', '')}".strip(),
        "district": district_ok(addr.get("district")),
        "text": text,
        "online_since": item.get("createdAt"),
        "image": f"https://www.inberlinwohnen.de/img/{item['imagePath']}?q=80&w=900&fm=jpg"
                 if item.get("imagePath") else None,
    }


def title_says_wbs(title):
    """WBS im Titel, ohne dass er verneint wird ("ohne WBS", "kein WBS", "WBS nicht nötig")."""
    return bool(re.search(r"WBS|Wohnberechtigung", title, re.I)
                and not re.search(r"\b(ohne|kein|keine|keinen|nicht)\b", title, re.I))


def page_says_wbs(html):
    """Die Seite der Gesellschaft kennzeichnet WBS-Wohnungen teils nur mit einem Bild-Etikett
    wie "220er" (degewo), auch wenn inberlinwohnen "nicht erforderlich" sagt."""
    return any(re.fullmatch(r"\s*(100|140|160|180|220)er\s*", cap.get_text())
               for cap in BeautifulSoup(html, "html.parser").find_all("figcaption"))


def matches(l):
    """Im Zweifel lieber melden als verpassen: unbekannte Werte schließen nicht aus."""
    if l["company"] in SKIP_COMPANIES or l["district"] is False:
        return False
    if (l["wbs"] or title_says_wbs(l["title"])) and not ALLOW_WBS:
        return False
    if re.search(r"Senioren|ab \d\d Jahre", l["title"], re.I):
        return False
    if l["rooms"] is not None and not (MIN_ROOMS <= l["rooms"] <= MAX_ROOMS):
        return False
    if (l["price"] or 0) > MAX_WARMMIETE or (l["cold"] or 0) > MAX_KALTMIETE:
        return False
    return True


def rated(l):
    """Bewertung als rating.Rated. Braucht kein Netz."""
    where = "Prenzlauer Berg" if l["plz"] in PRENZLAUER_BERG_PLZ else l["bezirk"]
    return rating.rate(l["price"], l["size"], l["rooms"], rating.parse_features(l["text"]),
                       l["coord"], l["floor"], where, year=l["year"], levels=l["levels"])


def rate(l):
    """(Anzahl Sterne, Sterne-Text, Kurzbegründung)."""
    r = rated(l)
    return r.stars, r[0], r[1]


def app_offer(l, r):
    """Ein Angebot so, wie es die Handy-Ansicht (docs/index.html) braucht."""
    return {
        "source": "ibw", "company": l["company"], "url": l["url"], "title": l["title"],
        "address": f"{l['address']}, {l['plz']} Berlin", "bezirk": l["bezirk"],
        "images": list(dict.fromkeys(([l["image"]] if l["image"] else []) + l.get("more_images", []))),
        "photos_checked": PHOTOS_VERSION,
        "rooms": l["rooms"], "price": l["price"], "cold": l["cold"], "size": l["size"],
        "floor": l["floor"], "levels": l["levels"], "year": l["year"],
        "stars": r.stars, "why": r[1], "points": r.points, "parts": r.parts,
        "online_since": l["online_since"],
    }


def describe(l, stars, why, request=None):
    price = f"{l['price']:.0f} € warm" if l["price"] else "Miete unbekannt"
    line = f"{stars}  {l['company'] or 'Angebot'}: {l['rooms']} Zi, {price}"
    if why:
        line += f"\n{why}"
    # Link springt, wo möglich, direkt zum Anfrage-Formular der Gesellschaft
    link = l["url"] + FORM_ANCHOR.get(l["company"], "") if "#" not in l["url"] else l["url"]
    line += f"\n{l['address']}, {l['plz']} {l['bezirk']}\n{link}"
    if request:
        line += f"\nAnfrage: {request[0]} ({request[1]})"
    return line


# degewo: Formular ist mit einem Captcha geschützt, deshalb fragt der Bot dort nicht selbst an.
# Der Link in der Meldung öffnet direkt das Formular, den Rest erledigt das Autofill des Handys.
FORM_ANCHOR = {"degewo": "#accordion-nl-rent-details"}

# Gesellschaften, bei denen der Bot selbst anfragen kann
SENDERS = {"howoge": howoge.send_request}


def can_request(l, r):
    """Automatisch anfragen nur, wenn alles sicher bekannt ist und passt (wie beim WBM-Bot)."""
    return (l["company"] in SENDERS and l["district"] is True and l["wbs"] is False
            and l["price"] is not None and l["rooms"] is not None
            and r.stars >= wbm_bot.MIN_STARS_SEND)


# Bilder, die nicht zur Wohnung gehören (Logos, Symbole, WBS-Etiketten ...)
IMG_SKIP = re.compile(r"logo|icon|sprite|favicon|avatar|placeholder|banner|badge|siegel|teaser|/404/"
                      r"|flyout|og_image|stadtkarte"
                      r"|\.svg|\.gif|\.png", re.I)
IMG_FILE = re.compile(r"\.(jpe?g|webp)(\?|$)", re.I)
MAX_IMAGES = 20
PHOTOS_VERSION = 3  # erhöhen, wenn sich das Foto-Sammeln ändert: dann werden alle Angebote neu geprüft


def page_images(html, base):
    """Fotos der Wohnung von der Seite der Gesellschaft (og:image, Bilder, Links auf Fotos).
    Die Seiten sind verschieden gebaut, deshalb grob: alle JPG/WebP außer Logos und Symbolen."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    og = soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        found.append(og["content"])
    for tag in soup.find_all(["img", "source"]):
        srcset = tag.get("data-srcset") or tag.get("srcset")
        if srcset:  # größte Variante: der letzte Eintrag
            found.append(srcset.split(",")[-1].strip().split(" ")[0])
        found += [tag.get(a) for a in ("data-src", "data-lazy-src", "src") if tag.get(a)]
    found += [a["href"] for a in soup.find_all("a", href=True)]
    out, keys = [], {}
    for u in found:
        u = urljoin(base, u.strip())
        if not (u.startswith("https://") and IMG_FILE.search(u) and not IMG_SKIP.search(u)):
            continue
        key = photo_key(u)
        if key not in keys:
            keys[key] = len(out)
            out.append(u)
        elif "_processed_" in out[keys[key]] and "_processed_" not in u:
            out[keys[key]] = u  # lieber das Original als die verkleinerte Fassung
    # Enthalten Bildadressen die Objektnummer aus der Adresse des Angebots (z.B. Gewobag:
    # .../gewo-immo-media/0100-01707-0101-0008/...), sind nur diese Fotos der Wohnung
    token = base.split("?")[0].rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    own = [u for u in out if len(token) >= 6 and token in u]
    return (own or out)[:MAX_IMAGES]


def photo_key(url):
    """Dasselbe Foto in verschiedenen Größen erkennen: TYPO3 macht aus "5723.jpg" etwa
    "_processed_/b/4/csm_5723_1a2b3c4d5e.jpg"."""
    name = url.split("?")[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return re.sub(r"_[0-9a-f]{10}$", "", re.sub(r"^csm_", "", name))


def add_missing_photos(store, limit=5):
    """Angebote, die schon vor dem Foto-Sammeln in der Liste waren: pro Lauf ein paar nachholen."""
    todo = [o for k, o in store["offers"].items() if k.startswith("ibw-") and not o.get("gone")
            and o.get("photos_checked") != PHOTOS_VERSION and safe_url(o.get("url"))]
    for o in todo[:limit]:
        o["photos_checked"] = PHOTOS_VERSION
        _, more = company_page(o)
        # Bild von inberlinwohnen behalten, Fotos der Gesellschaft neu (frühere Versuche hatten Ausschuss)
        first = [u for u in (o.get("images") or [])[:1] if "inberlinwohnen.de" in u]
        o["images"] = list(dict.fromkeys(first + more))


def safe_url(u):
    return u if isinstance(u, str) and u.startswith("https://") else None


def company_page(l):
    """Seite der Gesellschaft (nur für Treffer): (WBS-Etikett gefunden?, Fotos der Wohnung)."""
    try:
        resp = wbm_bot.HTTP.get(l["url"], timeout=20)
    except requests.RequestException:
        return False, []
    if resp.status_code != 200:  # z.B. Angebot schon weg: Fehlerseite hat keine Wohnungsfotos
        return False, []
    return page_says_wbs(resp.text), page_images(resp.text, l["url"])


def fetch_new(seen, first_run):
    """Seiten durchgehen, bis eine Seite nichts Neues mehr enthält (neueste stehen vorn).
    Gibt (neue Angebote, Problem) zurück."""
    new = {}
    for page in range(1, MAX_PAGES + 1):
        try:
            resp = wbm_bot.HTTP.get(URL, params={"page": page}, timeout=30)
        except requests.RequestException as e:
            return list(new.values()), type(e).__name__
        if resp.status_code != 200:
            return list(new.values()), f"HTTP {resp.status_code}"
        listings = parse_page(resp.text)
        if not listings:
            # Seite 1 ohne Karten: Aufbau geändert (oder wirklich leer, bei 6 Gesellschaften unwahrscheinlich)
            return list(new.values()), "keine Angebote lesbar" if page == 1 else None
        fresh = [l for l in listings if l["id"] not in seen and l["id"] not in new]
        new.update((l["id"], l) for l in fresh)
        if not fresh and not first_run:
            break
        time.sleep(0.5)
    return list(new.values()), None


def main():
    now = datetime.now(wbm_bot.TZ)
    stats = wbm_bot.load_stats(now.strftime("%Y-%m-%d"))
    first_run = not SEEN_FILE.exists()
    seen = set() if first_run else set(json.loads(SEEN_FILE.read_text()))
    store = offers.load()
    # Handy-Ansicht zum ersten Mal füllen: einmal alle Seiten ansehen
    seed = not store.get("ibw_seeded")

    new, problem = fetch_new(set() if seed else seen, first_run or seed)
    if problem:
        wbm_bot.alert(stats, f"inberlinwohnen-Bot: Seite nicht sauber lesbar ({problem}). Bitte prüfen.",
                      key="alert_day_ibw")
        wbm_bot.STATS_FILE.write_text(json.dumps(stats, indent=1, ensure_ascii=False))
        if not new or first_run:  # unvollständiger erster Lauf: beim nächsten Mal neu
            sys.exit(1)

    # Scharf mit demselben Schalter wie der WBM-Bot (WBM_SEND=1), sonst Trockenlauf.
    # Die HOWOGE verlangt ein Haushaltsnettoeinkommen von mindestens dem Dreifachen der
    # Gesamtmiete: MAX_WARM_SEND (falls gesetzt) ist die höchste Gesamtmiete, bis zu der angefragt wird.
    max_warm = wbm_bot.send_cap()
    send = os.environ.get("WBM_SEND") == "1"
    person, missing = wbm_bot.load_person()
    sends = 0
    hits = []
    for l in new:
        fresh = l["id"] not in seen
        since = offers.parse_time(l["online_since"])
        recent = since is None or now - since < offers.KEEP_UNREQUESTED
        if not matches(l) or not (fresh or recent):
            continue
        wbs, l["more_images"] = company_page(l)
        if wbs and not ALLOW_WBS:
            continue
        r = rated(l)
        request = None
        if (fresh and not first_run and can_request(l, r) and sends < wbm_bot.MAX_SENDS_PER_RUN
                and not (send and (missing or (max_warm is not None and l["price"] > max_warm)))):
            sends += 1
            # Schon VOR dem Senden merken und speichern: nie doppelt anfragen, auch nach Absturz
            seen.add(l["id"])
            SEEN_FILE.write_text(json.dumps(sorted(seen)))
            request = SENDERS[l["company"]](l, person, send)
        if recent or request:  # in die Handy-Ansicht kommt jeder Treffer, egal wie viele Sterne
            entry = app_offer(l, r)
            old = store["offers"].get(f"ibw-{l['id']}", {})
            if len(old.get("images") or []) > len(entry["images"]):  # Seite gerade nicht erreichbar
                entry["images"] = old["images"]
            if request and request[0] in offers.REQUESTED:
                entry.update(requested=request[0], confirm="offen")  # wartet auf den Link per Mail
            offers.add(store, f"ibw-{l['id']}", entry, now)
        if fresh and r.stars >= MIN_STARS:
            hits.append((r.stars, l, r[0], r[1], request))
    hits.sort(key=lambda h: -h[0])  # beste zuerst
    add_missing_photos(store)
    store["ibw_seeded"] = store.get("ibw_seeded") or not problem
    offers.save(store, now)

    if first_run and hits:
        # Alles ist "neu": eine Sammelmeldung statt vieler einzelner
        top = hits[:FIRST_RUN_TOP]
        wbm_bot.notify(f"inberlinwohnen-Bot gestartet: {len(hits)} passende Angebote online, "
                       f"die besten {len(top)}:\n\n" + "\n\n".join(describe(l, s, w) for _, l, s, w, _ in top))
    else:
        for _, l, stars, why, request in hits:
            wbm_bot.notify(describe(l, stars, why, request))

    seen |= {l["id"] for l in new}
    SEEN_FILE.write_text(json.dumps(sorted(seen)))
    print(f"[inberlinwohnen] {len(new)} Angebote geprüft, {len(hits)} Treffer gemeldet.")


if __name__ == "__main__":
    main()
