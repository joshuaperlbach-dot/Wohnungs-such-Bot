"""Sternebewertung für Wohnungsangebote (1-5 Sterne).

Alles hier ist "best effort": Fehlt ein Wert, bekommt das Kriterium einen neutralen
Wert und der Bot läuft normal weiter. Netz braucht nur die Adresssuche (Nominatim);
Kiez, Haltestellen und Ringbahn kommen aus kiez.json bzw. festen Listen.

Alle Wünsche stehen in den Konstanten und der Funktion score() - dort anpassen.
"""
import json
import math
import re
import time
from pathlib import Path

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

# Die 27 Halte der Ringbahn (S41/S42), Koordinaten auf etwa 150 m genau
RINGBAHN = [
    ("Gesundbrunnen", 52.5487, 13.3885), ("Schönhauser Allee", 52.5493, 13.4152),
    ("Prenzlauer Allee", 52.5448, 13.4274), ("Greifswalder Straße", 52.5403, 13.4390),
    ("Landsberger Allee", 52.5296, 13.4553), ("Storkower Straße", 52.5239, 13.4650),
    ("Frankfurter Allee", 52.5136, 13.4752), ("Ostkreuz", 52.5031, 13.4693),
    ("Treptower Park", 52.4938, 13.4613), ("Sonnenallee", 52.4730, 13.4556),
    ("Neukölln", 52.4693, 13.4423), ("Hermannstraße", 52.4674, 13.4313),
    ("Tempelhof", 52.4706, 13.3855), ("Südkreuz", 52.4753, 13.3655),
    ("Schöneberg", 52.4795, 13.3521), ("Innsbrucker Platz", 52.4783, 13.3432),
    ("Bundesplatz", 52.4778, 13.3290), ("Heidelberger Platz", 52.4797, 13.3126),
    ("Hohenzollerndamm", 52.4886, 13.3003), ("Halensee", 52.4962, 13.2905),
    ("Westkreuz", 52.5011, 13.2838), ("Messe Nord/ICC", 52.5078, 13.2832),
    ("Westend", 52.5182, 13.2845), ("Jungfernheide", 52.5303, 13.2993),
    ("Beusselstraße", 52.5343, 13.3290), ("Westhafen", 52.5363, 13.3440),
    ("Wedding", 52.5427, 13.3661),
]

# Gewichte (Summe 100)
W = {
    "Kosten": 20, "Kiez": 16, "Lage": 10, "Ringbahn": 6, "ÖPNV": 3, "Bezirk": 3,
    "Baujahr": 9, "Hochhaus": 5, "Größe": 10, "Zimmer": 7, "Außenbereich": 6, "Aufzug": 5,
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
        # nur echter Garten: nicht Tiergarten, Kindergarten, Gartenstraße ...
        "garten": bool(re.search(
            r"\b(?:mieter|gemeinschafts|dach|privat)?garten(?!str|weg|platz|allee|stadt)", t)),
        "aufzug": "aufzug" in t or "fahrstuhl" in t,
    }


def parse_floor(text):
    """Etage aus dem Exposé; None = unbekannt, 0 = Erdgeschoss."""
    m = re.search(r"\bEtage:\s*(\w+)", text)  # eigenes Feld im Exposé: "Etage: 4", "EG", "DG"
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else 0 if v in ("EG", "Erdgeschoss") else None
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


KIEZ_FILE = Path(__file__).with_name("kiez.json")  # gebaut von build_kiez.py
_kiez_data = None


def kiez(coord):
    """Umgebung aus kiez.json (OpenStreetMap, kein Netz nötig): (Lokale und Läden im
    500-m-Umkreis, Meter bis zum nächsten Park/Wald, Haltestellen im 500-m-Umkreis).
    None, wenn die Datei fehlt."""
    global _kiez_data
    if _kiez_data is None:
        try:
            _kiez_data = json.loads(KIEZ_FILE.read_text())
            _kiez_data["parks"] = set(_kiez_data["parks"])
        except (OSError, ValueError):
            _kiez_data = {}
    k = _kiez_data
    if not k:
        return None
    (lat0, lon0), (dlat, dlon) = k["origin"], k["cell"]
    lat, lon = coord
    i0, j0 = int((lat - lat0) / dlat), int((lon - lon0) / dlon)
    places = stops = 0
    park_m = 9999
    for i in range(i0 - 7, i0 + 8):
        for j in range(j0 - 7, j0 + 8):
            m = 1000 * haversine_km(coord, (lat0 + (i + 0.5) * dlat, lon0 + (j + 0.5) * dlon))
            key = f"{i},{j}"
            if m <= 500:
                places += k["places"].get(key, 0)
                stops += k["stops"].get(key, 0)
            if key in k["parks"]:
                park_m = min(park_m, m)
    return places, round(park_m), stops


def ring_score(coord):
    """1.0 = Ringbahn-Halt in ~800 m, 0.5 = in ~1,5 km, sonst 0. Braucht kein Netz."""
    km = min(haversine_km(coord, (lat, lon)) for _, lat, lon in RINGBAHN)
    return 1.0 if km <= 0.8 else 0.5 if km <= 1.5 else 0.0


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
    """Je mehr Platz, desto besser: 35 m² = 0, ab 70 m² volle Punkte."""
    return lin(size, 70, 35) if size is not None else 0.5


def rooms_score(rooms):
    """Zwei Zimmer sind besser als eins."""
    if rooms is None:
        return 0.5
    return 1.0 if rooms >= 2 else 0.75 if rooms > 1 else 0.4


def year_score(year):
    """Altbau (bis 1949) ist der Traum, Neubau gut, die Plattenbau-Jahre schwach.
    Das Baujahr ist nur ein Anhaltspunkt für die Bauart."""
    if year is None:
        return 0.5
    if year < 1950:
        return 1.0
    if year >= 2000:
        return 0.7
    if year >= 1991:
        return 0.5
    return 0.1 if year >= 1960 else 0.4


def parse_year(text):
    m = re.search(r"Baujahr:?\s*(\d{4})", text)
    return int(m.group(1)) if m else None


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


def kiez_score(places, park_m):
    """Lebendiger Kiez statt Wohnsiedlung: ab ca. 300 Lokalen und Läden im 500-m-Umkreis
    volle Punkte, unter ca. 15 keine (zum Vergleich: Kollwitzplatz ~430, Gropiusstadt ~15).
    Ein Park in der Nähe zählt ein Fünftel."""
    life = lin(math.log10(places + 1), math.log10(301), math.log10(16))
    park = 1.0 if park_m <= 300 else 0.5 if park_m <= 600 else 0.0
    return 0.8 * life + 0.2 * park


def highrise_score(levels):
    """Hochhaus gibt Abzug: bis 6 Etagen gut, ab 12 keine Punkte."""
    if levels is None:
        return 0.5
    return 1.0 if levels <= 6 else 0.6 if levels <= 8 else 0.3 if levels <= 11 else 0.0


def district_score(district_text):
    t = district_text or ""
    if t.startswith("Prenzlauer Berg"):
        return 1.0
    if any(t.startswith(d) for d in ("Pankow", "Friedrichshain", "Kreuzberg", "Mitte")):
        return 0.7
    return 0.3  # geht auch, ist aber nicht der Wunsch


def score(price, size, rooms, feats, dist_km, stops, ring, floor, district_text="", year=None,
          around=None, levels=None):
    """Gibt (Sterne 1-5, Punkte 0-100, Teilwerte) zurück. None = unbekannt -> neutral 0,5.
    around = (Lokale und Läden im Umkreis, Meter zum Park)."""
    s = {
        "Kosten": price_score(price),
        "Kiez": kiez_score(*around) if around is not None else 0.5,
        "Lage": lin(dist_km, 2, 8) if dist_km is not None else 0.5,
        "Ringbahn": ring if ring is not None else 0.5,
        "ÖPNV": clamp01(stops / 15) if stops is not None else 0.5,
        "Bezirk": district_score(district_text),
        "Baujahr": year_score(year),
        "Hochhaus": highrise_score(levels),
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
    return rate(listing["price"], size, listing.get("rooms"), feats, coord, floor, text,
                parse_year(expose_text))


def rate(price, size, rooms, feats, coord, floor, district_text="", year=None, levels=None):
    """Bewertung aus fertigen Werten (ohne Netz). Gibt (Sterne-Text, Kurzbegründung) zurück."""
    dist = haversine_km(coord, WORK) if coord else None
    ring = ring_score(coord) if coord else None
    area = kiez(coord) if coord else None
    around, stops = (area[:2], area[2]) if area else (None, None)
    if levels is None and floor is not None and floor >= 6:
        levels = floor + 1  # Haus hat mindestens so viele Etagen
    n, points, _ = score(price, size, rooms, feats, dist, stops, ring, floor, district_text, year,
                         around, levels)
    bits = []
    if size:
        bits.append(f"{size:g} m²")
    if dist is not None:
        bits.append(f"~{dist:.1f} km zur Arbeit (Luftlinie)")
    if around:
        places, park_m = around
        mood = "lebendig" if places >= 200 else "mittel" if places >= 60 else "wenig los"
        bits.append(f"Kiez {mood} ({places} Lokale/Läden)" + (", Park nah" if park_m <= 300 else ""))
    elif coord:
        bits.append("Kiez unbekannt")
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
    if year is not None:
        bits.append(f"Baujahr {year}")
    if levels is not None and levels >= 9:
        bits.append(f"Hochhaus ({levels} Etagen)")
    return f"{stars_text(n)} ({n}/5)", ", ".join(bits)
