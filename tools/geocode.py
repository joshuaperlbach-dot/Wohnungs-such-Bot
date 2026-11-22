#!/usr/bin/env python3
"""Koordinaten für die Statistik-Karte: Adressen der Angebote aus docs/history.json, der aktuellen Liste und
tools/history_addresses.json (ältere Angebote, aus dem Verlauf) bei Nominatim nachschlagen und in docs/coords.json
ablegen (Angebots-Schlüssel -> [lat, lon]). Höchstens 1 Anfrage pro Sekunde, nur
Adressen ohne Koordinaten. docs/coords.json fasst der Bot sonst nicht an, deshalb geht beim Speichern nichts verloren."""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
import offers  # noqa: E402
import rating  # noqa: E402

COORDS = Path("docs/coords.json")
coords = json.loads(COORDS.read_text()) if COORDS.exists() else {}
hist = json.loads(offers.HISTORY_FILE.read_text()) if offers.HISTORY_FILE.exists() else {}
store = offers.load()
extra = json.loads(Path("tools/history_addresses.json").read_text()) if Path("tools/history_addresses.json").exists() else {}
todo = []   # (Schlüssel, Adresse)
for key in set(hist) | set(store["offers"]) | set(extra):
    o = store["offers"].get(key) or hist.get(key) or {}
    if key in coords:
        continue
    if isinstance(o.get("coord"), list) and len(o["coord"]) == 2:
        coords[key] = o["coord"]
        continue
    a = (o.get("address") or extra.get(key) or "").strip()
    if a:
        todo.append((key, a))
print("nachschlagen:", len(todo))
found = 0
cache = {}
start = time.time()
for key, a in todo[:400]:
    if time.time() - start > 12 * 60:   # Zeitlimit des Workflows: lieber speichern und nochmal laufen lassen
        print("Zeit um, Rest beim nächsten Lauf")
        break
    if a in cache:
        coords[key] = cache[a]
        continue
    try:
        c = rating.geocode(a)
    except Exception as e:
        c = None
        print("Fehler", type(e).__name__)
    time.sleep(1.1)
    if c and 52.3 <= c[0] <= 52.7 and 13.0 <= c[1] <= 13.8:
        coords[key] = cache[a] = [round(c[0], 5), round(c[1], 5)]
        found += 1
print("gefunden:", found, "gesamt:", len(coords))
COORDS.write_text(json.dumps(coords, ensure_ascii=False, separators=(",", ":")))
subprocess.run(["git", "config", "user.name", "wbm-bot"], check=True)
subprocess.run(["git", "config", "user.email", "wbm-bot@users.noreply.github.com"], check=True)
subprocess.run(["git", "add", str(COORDS)], check=True)
if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
    sys.exit(0)
subprocess.run(["git", "commit", "-m", f"Koordinaten für die Statistik-Karte ({found} neu)"], check=True)
for _ in range(4):
    if subprocess.run(["git", "pull", "--rebase", "-X", "theirs"]).returncode == 0 and subprocess.run(["git", "push"]).returncode == 0:
        print("gespeichert")
        break
    subprocess.run(["git", "rebase", "--abort"])
    time.sleep(3)
else:
    sys.exit("Speichern fehlgeschlagen")
