#!/usr/bin/env python3
"""Grundkarte für die Statistik-Karte: holt aus OpenStreetMap (Overpass) nur das, was zur Orientierung
zählt – S-Bahn-Ring, S- und U-Bahn, große Straßen, Spree und Kanäle, große Parks, Bahnhöfe als Knoten
und Ortsteil-Namen – vereinfacht die Linien und legt alles als docs/basemap.json ab.
Einmalig laufen lassen (Werkzeug "tools/basemap.py" im Workflow formular.yml), bei Bedarf wiederholen."""
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import requests

OUT = Path("docs/basemap.json")
BBOX = "52.36,13.10,52.66,13.76"
SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
UA = {"User-Agent": "wohnungs-such-bot basemap (github.com/joshuaperlbach-dot/Wohnungs-such-Bot)"}

# Knotenpunkte: 1 = immer beschriftet, 2 = ab mittlerem Zoom
HUBS = {
    "Hauptbahnhof": 1, "Alexanderplatz": 1, "Friedrichstraße": 1, "Ostkreuz": 1, "Südkreuz": 1,
    "Gesundbrunnen": 1, "Westkreuz": 1, "Zoologischer Garten": 1, "Kottbusser Tor": 1,
    "Warschauer Straße": 1, "Potsdamer Platz": 1, "Hermannplatz": 1, "Ostbahnhof": 1,
    "Hackescher Markt": 2, "Jannowitzbrücke": 2, "Schönhauser Allee": 2, "Frankfurter Allee": 2,
    "Neukölln": 2, "Hermannstraße": 2, "Bornholmer Straße": 2, "Yorckstraße": 2, "Tempelhof": 2,
    "Innsbrucker Platz": 2, "Westend": 2, "Jungfernheide": 2, "Beusselstraße": 2, "Wedding": 2,
    "Prenzlauer Allee": 2, "Greifswalder Straße": 2, "Storkower Straße": 2, "Treptower Park": 2,
    "Sonnenallee": 2, "Mehringdamm": 2, "Görlitzer Bahnhof": 2, "Schlesisches Tor": 2,
    "Rosa-Luxemburg-Platz": 2, "Eberswalder Straße": 2, "Nollendorfplatz": 2, "Wittenbergplatz": 2,
    "Moritzplatz": 2, "Hallesches Tor": 2, "Leopoldplatz": 2, "Rathaus Steglitz": 2, "Bundesplatz": 2,
    "Lichtenberg": 2, "Schöneweide": 2, "Pankow": 2, "Spandau": 2, "Charlottenburg": 2,
    "Rathaus Neukölln": 2, "Boddinstraße": 2, "Rosenthaler Platz": 2, "Senefelderplatz": 2,
    "Samariterstraße": 2, "Frankfurter Tor": 2, "Weberwiese": 2, "Strausberger Platz": 2,
}

QUERY = f"""[out:json][timeout:240];
(
  relation["type"="route"]["ref"="S41"]({BBOX});
  way["railway"="light_rail"][!"service"]({BBOX});
  way["railway"="subway"][!"service"]({BBOX});
  way["highway"~"^(motorway|trunk|primary|secondary)$"]({BBOX});
  way["waterway"~"^(river|canal)$"]({BBOX});
  way["natural"="water"]({BBOX});
  relation["natural"="water"]({BBOX});
  way["leisure"="park"]({BBOX});
  relation["leisure"="park"]({BBOX});
  way["landuse"="forest"]({BBOX});
  relation["landuse"="forest"]({BBOX});
  node["railway"="station"]({BBOX});
  node["place"~"^(suburb|quarter)$"]({BBOX});
);
out geom;"""


def fetch():
    for _ in range(3):
        for url in SERVERS:
            try:
                r = requests.post(url, data={"data": QUERY}, headers=UA, timeout=300)
                if r.ok:
                    return r.json()["elements"]
                print(url, r.status_code)
            except Exception as e:
                print(url, type(e).__name__)
        time.sleep(30)
    sys.exit("Overpass nicht erreichbar")


def simplify(pts, tol):
    """Douglas-Peucker in Grad (Längengrade auf Berliner Breite gestaucht)."""
    if len(pts) < 3:
        return pts
    k = math.cos(math.radians(52.5))

    def dist(p, a, b):
        ax, ay, bx, by, px, py = a[1] * k, a[0], b[1] * k, b[0], p[1] * k, p[0]
        dx, dy = bx - ax, by - ay
        if dx == dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0, None
        for m in range(i + 1, j):
            d = dist(pts[m], pts[i], pts[j])
            if d > best:
                best, idx = d, m
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, kk in zip(pts, keep) if kk]


def area_m2(ring):
    k = math.cos(math.radians(52.5)) * 111320
    s = 0
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        s += (x1 * k) * (y2 * 110540) - (x2 * k) * (y1 * 110540)
    return abs(s) / 2


def stitch(segments):
    """Teilstücke eines Multipolygons zu geschlossenen Ringen verbinden."""
    segs = [list(s) for s in segments if len(s) > 1]
    rings = []
    while segs:
        cur = segs.pop()
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]
                elif s[-1] == cur[-1]:
                    cur += s[::-1][1:]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[::-1][:-1] + cur
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        rings.append(cur)
    return rings


def geom(e):
    return [(round(p["lat"], 5), round(p["lon"], 5)) for p in e.get("geometry") or []]


def flat(pts, tol):
    s = simplify(pts, tol)
    return [v for p in s for v in (round(p[0], 4), round(p[1], 4))]


def clean(name):
    for pre in ("Berlin-", "Berlin "):
        if name.startswith(pre):
            name = name[len(pre):]
    return name.replace(" (Berlin)", "").replace(" Hbf", "").replace("Hbf", "Hauptbahnhof").strip()


els = fetch()
print("Elemente:", len(els))
out = {k: [] for k in ("ring", "sbahn", "ubahn", "road1", "road2", "road3", "river", "water", "park", "hubs", "areas")}
hubs = {}
for e in els:
    t = e.get("tags") or {}
    if e["type"] == "node":
        if t.get("railway") == "station":
            n = clean(t.get("name", ""))
            if n in HUBS:
                hubs.setdefault(n, []).append((e["lat"], e["lon"]))
        elif t.get("place") and t.get("name"):
            out["areas"].append([t["name"], round(e["lat"], 4), round(e["lon"], 4), 1 if t["place"] == "suburb" else 2])
        continue
    if e["type"] == "relation":
        if t.get("type") == "route":
            for m in e.get("members", []):
                if m["type"] == "way" and m.get("geometry"):
                    out["ring"].append(flat(geom(m), 0.00008))
            continue
        outer = [geom(m) for m in e.get("members", []) if m["type"] == "way" and m.get("role") == "outer" and m.get("geometry")]
        kind = "water" if t.get("natural") == "water" else "park"
        for r in stitch(outer):
            if r[0] == r[-1] and area_m2(r) > (20000 if kind == "water" else 80000):
                out[kind].append(flat(r, 0.00015))
        continue
    g = geom(e)
    if len(g) < 2:
        continue
    rw, hw = t.get("railway"), t.get("highway")
    if rw == "light_rail":
        out["sbahn"].append(flat(g, 0.0001))
    elif rw == "subway":
        out["ubahn"].append(flat(g, 0.0001))
    elif hw in ("motorway", "trunk"):
        out["road1"].append(flat(g, 0.00012))
    elif hw == "primary":
        out["road2"].append(flat(g, 0.00012))
    elif hw == "secondary":
        out["road3"].append(flat(g, 0.00012))
    elif t.get("waterway"):
        out["river"].append(flat(g, 0.00012))
    elif t.get("natural") == "water" and g[0] == g[-1] and area_m2(g) > 20000:
        out["water"].append(flat(g, 0.00015))
    elif (t.get("leisure") == "park" or t.get("landuse") == "forest") and g[0] == g[-1] and area_m2(g) > 80000:
        out["park"].append(flat(g, 0.00015))
for n, ps in hubs.items():
    out["hubs"].append([n, round(sum(p[0] for p in ps) / len(ps), 4), round(sum(p[1] for p in ps) / len(ps), 4), HUBS[n]])
out["attribution"] = "© OpenStreetMap-Mitwirkende (ODbL)"
for k, v in out.items():
    print(k, len(v))
OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
print("Größe:", OUT.stat().st_size // 1024, "KB")

subprocess.run(["git", "config", "user.name", "wbm-bot"], check=True)
subprocess.run(["git", "config", "user.email", "wbm-bot@users.noreply.github.com"], check=True)
subprocess.run(["git", "add", str(OUT)], check=True)
if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
    sys.exit(0)
subprocess.run(["git", "commit", "-m", "Grundkarte für die Statistik"], check=True)
for _ in range(4):
    if subprocess.run(["git", "pull", "--rebase", "-X", "theirs"]).returncode == 0 and subprocess.run(["git", "push"]).returncode == 0:
        print("gespeichert")
        break
    subprocess.run(["git", "rebase", "--abort"])
    time.sleep(3)
else:
    sys.exit("Speichern fehlgeschlagen")
