#!/usr/bin/env python3
"""Einmaliges Werkzeug: Sieht der inberlinwohnen-Bot alle Angebote? Gibt für die ersten Seiten
die Angebots-Nummern, "online seit" und ob sie schon gesehen wurden aus (nur öffentliche Daten)."""
import json
import re
import sys

sys.path.insert(0, ".")
import ibw_bot  # noqa: E402
import wbm_bot  # noqa: E402

seen = set(json.loads(ibw_bot.SEEN_FILE.read_text()))
print("gesehen:", len(seen), "höchste Nummer:", max(seen))
for page in range(1, 9):
    r = wbm_bot.HTTP.get(ibw_bot.URL, params={"page": page}, timeout=30)
    ls = ibw_bot.parse_page(r.text)
    if page == 1:
        m = re.search(r"(\d[\d.]*)\s*(Wohnungen|Angebote|Ergebnisse|Treffer)", ibw_bot.BeautifulSoup(r.text, "html.parser").get_text(" "))
        print("Seite sagt:", m.group(0) if m else "?", "| Karten-Elemente:", r.text.count('id="apartment-'))
    print(f"Seite {page}: {r.status_code}, {len(ls)} Angebote")
    for l in ls:
        print(f"   {l['id']} {'gesehen' if l['id'] in seen else 'NEU    '} {l.get('online_since')} {l.get('company')}")
