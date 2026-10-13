#!/usr/bin/env python3
"""Baut kiez.json aus der Berlin-Karte von OpenStreetMap (einmalig, bei Bedarf wiederholen).

Der Bot selbst braucht das nicht: er liest nur die fertige kiez.json. Neu bauen lohnt
sich höchstens alle paar Monate.

  pip install osmium
  Karte laden: https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf
  python build_kiez.py berlin-latest.osm.pbf

Die Stadt wird in Kästchen von etwa 100 m zerlegt. Pro Kästchen wird gezählt:
  places = Restaurants, Cafés, Bars, Imbisse und Läden
  stops  = Haltestellen (Bahn, Tram, Bus)
  parks  = Kästchen, die ein Park oder Wald (ab ca. 0,5 ha) berührt
"""
import json
import sys
from collections import Counter
from pathlib import Path

import osmium

ORIGIN = (52.33, 13.08)           # Südwest-Ecke von Berlin
CELL = (0.0009, 0.00147)          # ca. 100 m x 100 m
FOOD = {"restaurant", "cafe", "bar", "pub", "fast_food", "biergarten", "ice_cream"}
NO_SHOP = {"vacant", "car", "car_repair", "car_parts", "motorcycle", "storage_rental"}
MIN_PARK_M2 = 5000


def cell(lat, lon):
    i, j = int((lat - ORIGIN[0]) / CELL[0]), int((lon - ORIGIN[1]) / CELL[1])
    return f"{i},{j}" if 0 <= i < 500 and 0 <= j < 500 else None


class Handler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.places, self.stops, self.parks = Counter(), Counter(), set()

    def is_place(self, tags):
        return tags.get("amenity") in FOOD or ("shop" in tags and tags["shop"] not in NO_SHOP)

    def node(self, n):
        if not n.location.valid():
            return
        c = cell(n.location.lat, n.location.lon)
        if c is None:
            return
        if self.is_place(n.tags):
            self.places[c] += 1
        if (n.tags.get("railway") in ("station", "halt", "tram_stop", "subway_entrance")
                or n.tags.get("highway") == "bus_stop"):
            self.stops[c] += 1

    def way(self, w):
        # Läden und Lokale, die als Gebäude-Umriss eingetragen sind
        if self.is_place(w.tags):
            for n in w.nodes:
                if n.location.valid():
                    c = cell(n.location.lat, n.location.lon)
                    if c:
                        self.places[c] += 1
                    break

    def area(self, a):
        t = a.tags
        if not (t.get("leisure") in ("park", "nature_reserve") or t.get("landuse") == "forest"
                or t.get("natural") == "wood"):
            return
        for ring in a.outer_rings():
            pts = [(n.lat, n.lon) for n in ring if n.location.valid()]
            if len(pts) < 3:
                continue
            # Fläche grob über die Gauß-Formel, in Quadratmetern
            m = [((lat - ORIGIN[0]) * 111320, (lon - ORIGIN[1]) * 67800) for lat, lon in pts]
            size = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(m, m[1:] + m[:1]))) / 2
            if size < MIN_PARK_M2:
                continue
            # Rand in 50-m-Schritten abgehen, damit lange gerade Kanten nicht fehlen
            for (la1, lo1), (la2, lo2) in zip(pts, pts[1:] + pts[:1]):
                steps = max(1, int((abs(la2 - la1) * 111320 + abs(lo2 - lo1) * 67800) / 50))
                for s in range(steps):
                    c = cell(la1 + (la2 - la1) * s / steps, lo1 + (lo2 - lo1) * s / steps)
                    if c:
                        self.parks.add(c)


def main():
    h = Handler()
    h.apply_file(sys.argv[1], locations=True)
    out = {"origin": ORIGIN, "cell": CELL, "places": dict(h.places), "stops": dict(h.stops),
           "parks": sorted(h.parks)}
    target = Path(__file__).with_name("kiez.json")
    target.write_text(json.dumps(out, separators=(",", ":")))
    print(f"{sum(h.places.values())} Lokale/Läden in {len(h.places)} Kästchen, "
          f"{sum(h.stops.values())} Haltestellen, {len(h.parks)} Park-Kästchen, "
          f"{target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
