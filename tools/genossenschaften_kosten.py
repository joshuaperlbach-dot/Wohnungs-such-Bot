#!/usr/bin/env python3
"""Einmaliges Werkzeug: Kosten der Mitgliedschaft bei ausgewählten Genossenschaften auslesen.

Gibt von den Mitgliedschafts-Seiten (und dort verlinkten Unterseiten zu Mitgliedschaft,
Wohnungssuche, Satzung) alle Sätze mit Beträgen oder Stichwörtern wie Geschäftsanteil,
Eintrittsgeld, Kündigung, Warteliste aus. Nur öffentliche Inhalte.
"""
import re
import sys
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, __import__("os").path.dirname(__file__))
from genossenschaften import get, text_of  # noqa: E402

START = [
    "https://www.wgli.de/wgli-mitgliedschaft/",
    "https://www.bremer-hoehe.de/genossenschaft/mitgliedschaft/",
    "https://www.charlotte1907.de/genossenschaft/mitglied-werden",
    "https://www.wbg-wilhelmsruh.de/ueber-uns/mitglied-werden/",
    "https://www.evmberlin.de/wohnungssuche/",
]
LINK = re.compile(r"mitglied|wohnungssuch|wohnung.?suchen|interess|bewerb|warte|anteil|faq|satzung", re.I)
KEY = re.compile(r"€|euro|geschäftsanteil|pflichtanteil|eintrittsgeld|aufnahmegebühr|kündig|auszahl|rate"
                 r"|warteliste|wartezeit|interessent|bewerb|registrier|aufnahme|derzeit", re.I)


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if KEY.search(s) and 15 < len(s) < 400]


def main():
    for start in START:
        html = get(start)
        print(f"\n######## {start}")
        if not html:
            print("   (nicht erreichbar)")
            continue
        host = urlparse(start).netloc
        links = [start] + list(dict.fromkeys(
            urljoin(start, a["href"]).split("#")[0] for a in BeautifulSoup(html, "html.parser").find_all("a", href=True)
            if LINK.search(a.get_text(" ", strip=True) + " " + a["href"]) and urlparse(urljoin(start, a["href"])).netloc == host
        ))
        seen = set()
        for url in list(dict.fromkeys(links))[:6]:
            h = html if url == start else get(url)
            if not h:
                continue
            new = [s for s in sentences(text_of(h)) if s not in seen]
            seen.update(new)
            if new:
                print(f"--- {url}")
                for s in new[:25]:
                    print(f"   • {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
