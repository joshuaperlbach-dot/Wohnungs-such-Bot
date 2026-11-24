"""Einmalig: Gibt es die Berlinovo-Fotos auch in voller Größe? Druckt nur Bildgrößen, keine persönlichen Daten."""
import json
import re

import requests

offers = json.load(open("docs/offers.json"))["offers"]
urls = [u for o in offers.values() for u in o.get("images", []) if "berlinovo.de" in u][:4]
for u in urls:
    full = re.sub(r"/styles/[^/]+/public/", "/", u.split("?")[0])
    for name, x in (("klein", u), ("voll", full)):
        try:
            r = requests.get(x, timeout=30, headers={"User-Agent": "wohnungsbot (privates Projekt)"})
            print(name, r.status_code, r.headers.get("content-type"), len(r.content) // 1024, "KB", x.rsplit("/", 1)[-1][:30])
        except Exception as e:
            print(name, "Fehler", type(e).__name__)
