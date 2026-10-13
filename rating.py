"""Sternebewertung für Wohnungsangebote (1-5 Sterne).

Alles hier ist "best effort": Schlägt ein Netzwerkaufruf fehl, bekommt das
Kriterium einen neutralen Wert und der Bot läuft normal weiter.

Alle Wünsche stehen in den Konstanten und der Funktion score() - dort anpassen.
"""
import math
import re
import time

import requests

WORK = (52.5037, 13.4107)  # Moritzplatz (Arbeitsweg-Ziel)
UA = {"User-Agent": "wbm-wohnungsbot (privates Projekt)"}

# Preis: bis SOFT_PRICE alles gut, bis HARD_PRICE absolute Obergrenze
GOOD_PRICE = 750.0
SOFT_PRICE = 850.0
HARD_PRICE = 950.0
# Ab wie vielen Punkten (von 100) es welche Sterne gibt
STAR_STEPS = [(5, 85), (4, 70), (3, 55), (2, 40)]
# Über SOFT_PRICE gibt es höchstens 4 Sterne, außer die Wohnung ist sonst extrem gut
FIVE_STAR_OVER_SOFT_MIN_POINTS = 88

# Gewichte (Summe 100)
W = {
    "Kosten": 25, "Lage": 15, "Ringbahn": 10, "ÖPNV": 5, "Bezirk": 5,
    "Größe": 12, "Zimmer": 8, "Außenbereich": 10, "Aufzug": 10,
}


def clamp01(x):
    return max(0.0, min(1.0, x))


def lin(x, best, worst):
    """1.0 bei 'best', 0.0 bei 'worst', dazwischen linear."""
    return clamp01((x - worst) / (best - worst))


def haversine_km(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = (math.sin((la2 - la1) * p / 2) ** 2
         + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


# --- Daten aus dem Angebotstext ---
def parse_size(text):
    m = re.search(r"([\d.]+,\d{1,2}|\d+)\s*m²", text)
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def parse_features(text):
    t = text.lower()
    return {
        "balkon": any(w in t for w in ("balkon", "terrasse", "loggia")),
        "garten": "garten" in t,
        "aufzug": "aufzug" in t or "fahrstuhl" in t,
    }


def parse_floor(text):
    """Etage aus dem Exposé; None = unbekannt, 0 = Erdgeschoss."""
    if re.search(r"\b(EG|Erdgeschoss|Erdgeschoß)\b", text):
        return 0
    m = re.search(r"(\d+)\.?\s*(?:OG|Obergeschoss|Obergeschoß|Etage|Stock)\b", text) \
        or re.search(r"(?:Etage|Geschoss|Geschoß|Stockwerk)\s*:?\s*(\d+)", text)
    return int(m.group(1)) if m else None


def address_candidates(text):
    """Mögliche Suchanfragen für die Adresse, genaueste zuerst."""
    m = re.search(r"(\d{5})\s+Berlin", text)
    if not m:
        return []
    plz = m.group(1)
    before = text[: m.start()].rstrip(" ,").split()
    cands = []
    if before and re.match(r"\d+[a-zA-Z]?$", before[-1]):
        # Hausnummer + bis zu 2 Wörter davor (Straßenname)
        street = before[-3:] if len(before) >= 3 else before
        cands.append(" ".join(street[-3:]) + f", {plz} Berlin")
        cands.append(" ".join(before[-2:]) + f", {plz} Berlin")
    cands.append(f"{plz} Berlin")
    return cands


# --- Netzwerk (Nominatim / Overpass, beides OpenStreetMap) ---
def geocode(query):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "de"},
        headers=UA, timeout=15,
    )
    data = r.json()
    return (float(data[0]["lat"]), float(data[0]["lon"])) if data else None


def locate(text):
    for q in address_candidates(text):
        try:
            coord = geocode(q)
        except Exception:
            coord = None
        time.sleep(1.1)  # Nominatim: max. 1 Anfrage pro Sekunde
        if coord:
            return coord
    return None


def _overpass(query):
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data": query},
                          headers=UA, timeout=25)
        return int(r.json()["elements"][0]["tags"]["total"])
    except Exception:
        return None


def count_transit(coord, radius=500):
    lat, lon = coord
    return _overpass(
        f'[out:json][timeout:20];('
        f'node["railway"~"station|subway_entrance|tram_stop"](around:{radius},{lat},{lon});'
        f'node["highway"="bus_stop"](around:{radius},{lat},{lon}););out count;')


def ringbahn_stations(coord, radius):
    """Anzahl Ringbahn-Halte (S41/S42) im Umkreis; None bei Fehler."""
    lat, lon = coord
    return _overpass(
        f'[out:json][timeout:25];rel["route"~"train|light_rail"]["ref"~"^S4[12]$"]->.r;'
        f'node(r.r)["railway"~"station|stop"](around:{radius},{lat},{lon});out count;')


def ring_score(coord):
    """1.0 = Ringbahn-Halt in ~800 m, 0.5 = in ~1,5 km, sonst 0; None-Fehler -> 0,5."""
    near = ringbahn_stations(coord, 800)
    if near is None:
        return None
    if near > 0:
        return 1.0
    far = ringbahn_stations(coord, 1500)
    if far is None:
        return None
    return 0.5 if far > 0 else 0.0


# --- Bewertung ---
def price_score(price):
    """<=750 sehr gut, 850 noch okay (0,6), 1000 = Obergrenze (0)."""
    if price is None:
        return 0.5
    if price <= GOOD_PRICE:
        return 1.0
    if price <= SOFT_PRICE:
        return 1.0 - 0.4 * (price - GOOD_PRICE) / (SOFT_PRICE - GOOD_PRICE)
    return 0.6 * lin(price, SOFT_PRICE, HARD_PRICE)


def size_score(size):
    """Traum: 60-70 m². Kleiner/größer gibt langsam Abzug."""
    if size is None:
        return 0.5
    if size < 60:
        return clamp01((size - 35) / 25)
    if size <= 70:
        return 1.0
    return clamp01(1 - (size - 70) / 30)


def rooms_score(rooms):
    """Traum: zwei Zimmer (ein kleines, ein großes)."""
    return {2: 1.0, 1: 0.5}.get(rooms, 0.3) if rooms is not None else 0.5


def lift_score(floor, feats):
    """Aufzug ist erst ab dem 3. Stock wichtig, je höher desto mehr. Garten ersetzt ihn."""
    has_substitute = feats["aufzug"] or feats["garten"]
    if floor is None:
        return 1.0 if has_substitute else 0.5
    if floor <= 2:
        return 1.0 if has_substitute else 0.85  # nur "nice to have"
    if has_substitute:
        return 1.0
    return {3: 0.5, 4: 0.25}.get(floor, 0.0)  # ab 5. Stock ohne Aufzug: 0


def district_score(district_text):
    t = district_text or ""
    if t.startswith("Prenzlauer Berg"):
        return 1.0
    if any(t.startswith(d) for d in ("Pankow", "Friedrichshain", "Kreuzberg", "Mitte")):
        return 0.7
    return 0.5  # "kann auch mal woanders wohnen"


def score(price, size, rooms, feats, dist_km, stops, ring, floor, district_text=""):
    """Gibt (Sterne 1-5, Punkte 0-100, Teilwerte) zurück. None = unbekannt -> neutral 0,5."""
    s = {
        "Kosten": price_score(price),
        "Lage": lin(dist_km, 2, 8) if dist_km is not None else 0.5,
        "Ringbahn": ring if ring is not None else 0.5,
        "ÖPNV": clamp01(stops / 6) if stops is not None else 0.5,
        "Bezirk": district_score(district_text),
        "Größe": size_score(size),
        "Zimmer": rooms_score(rooms),
        "Außenbereich": 1.0 if (feats["balkon"] or feats["garten"]) else 0.0,
        "Aufzug": lift_score(floor, feats),
    }
    points = sum(W[k] * v for k, v in s.items())
    stars = next((n for n, lo in STAR_STEPS if points >= lo), 1)
    # Über dem Wunschpreis: höchstens 4 Sterne, außer sonst wirklich Spitze
    if price is not None and price > SOFT_PRICE and points < FIVE_STAR_OVER_SOFT_MIN_POINTS:
        stars = min(stars, 4)
    return stars, round(points), s


def stars_text(n):
    return "★" * n + "☆" * (5 - n)


def rate_listing(listing, expose_text=""):
    """Komplettbewertung. Gibt (Sterne-Text, Kurzbegründung) zurück; fällt bei Fehlern
    auf neutrale Werte zurück."""
    text = listing["text"]
    size = parse_size(text)
    feats = parse_features(text + " " + expose_text)
    floor = parse_floor(expose_text) if expose_text else None
    coord = locate(text)
    dist = haversine_km(coord, WORK) if coord else None
    stops = count_transit(coord) if coord else None
    ring = ring_score(coord) if coord else None
    n, points, _ = score(listing["price"], size, listing.get("rooms"), feats, dist, stops,
                         ring, floor, text)
    bits = []
    if size:
        bits.append(f"{size:g} m²")
    if dist is not None:
        bits.append(f"~{dist:.1f} km zur Arbeit (Luftlinie)")
    if ring is not None:
        bits.append("Ringbahn nah" if ring == 1.0 else "Ringbahn mittel" if ring == 0.5 else "Ringbahn weit")
    if feats["balkon"]:
        bits.append("Balkon/Terrasse")
    if feats["garten"]:
        bits.append("Garten")
    if feats["aufzug"]:
        bits.append("Aufzug")
    if floor is not None:
        bits.append("EG" if floor == 0 else f"{floor}. Etage")
    return f"{stars_text(n)} ({n}/5)", ", ".join(bits)
