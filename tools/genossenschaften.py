#!/usr/bin/env python3
"""Einmaliges Werkzeug: Mitgliedschaft und Wartelisten Berliner Wohnungsgenossenschaften prüfen.

Startet bei der gemeinsamen Seite der Berliner Genossenschaften (Liste aller Mitglieder) und
einer Handvoll bekannter Adressen, sucht auf jeder Startseite Links zu Mitgliedschaft,
Warteliste oder Wohnungssuche und gibt kurze Textstellen mit Signalwörtern aus
("Aufnahmestopp", "Warteliste", "Mitglied werden", ...). Nur öffentliche Inhalte.
"""
import re
import sys
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HTTP = requests.Session()
HTTP.headers["User-Agent"] = "Mozilla/5.0 (Wohnungssuche, einmalige Pruefung)"

PORTALS = ["https://www.wohnungsbaugenossenschaften.de/", "https://www.wohnungsbaugenossenschaften.de/genossenschaften"]
KNOWN = [  # Startpunkte, falls die gemeinsame Liste nicht lesbar ist (Adressen aus dem Gedächtnis, ungeprüft)
    "https://www.wgli.de/", "https://www.bbwo1892.de/", "https://www.charlotte1907.de/", "https://www.bremer-hoehe.de/",
    "https://www.evmberlin.de/", "https://www.vaterlaendischer-bauverein.de/", "https://www.maerkische-scholle.de/",
    "https://www.berolina-berlin.de/", "https://www.wbg-friedrichshain.de/", "https://www.fortuna-eg.de/",
    "https://www.wbg-humboldt.de/", "https://www.wbg-wilhelmsruh.de/", "https://www.neue-lebensraeume.de/",
    "https://www.wg-vorwaerts.de/", "https://www.solidaritaet-eg.de/", "https://www.altglienicke-eg.de/",
]
LINK = re.compile(r"mitglied|warte|interess|wohnungssuch|wohnung.?suchen|aufnahme|vermietung", re.I)
SIGNAL = re.compile(r"aufnahmestopp|aufnahme.{0,30}(ausgesetzt|geschlossen|gestoppt|nicht möglich)"
                    r"|keine (neuen )?mitglieder|warteliste.{0,40}(geschlossen|voll|ausgesetzt|offen|geöffnet)"
                    r"|interessentenliste|mitglied werden|beitrittserklärung|online.?bewerb|geschäftsanteil"
                    r"|keine wohnungsangebote|derzeit keine", re.I)
SKIP = ("facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "youtube.", "xing.", "google.", "wohnungsbaugenossenschaften.de")


def get(url):
    try:
        r = HTTP.get(url, timeout=15)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def text_of(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def coop_sites():
    sites = {}
    for portal in PORTALS:
        html = get(portal)
        if not html:
            continue
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            u = urljoin(portal, a["href"])
            host = urlparse(u).netloc.lower()
            if u.startswith("http") and host and not any(s in host for s in SKIP):
                sites.setdefault(host, f"{urlparse(u).scheme}://{host}/")
    print(f"Aus der gemeinsamen Liste: {len(sites)} Seiten")
    for u in KNOWN:
        sites.setdefault(urlparse(u).netloc.lower(), u)
    return list(sites.values())


def check(home):
    html = get(home)
    if not html:
        return f"## {home}\n   (nicht erreichbar)"
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:80]
    pages = [home] + list(dict.fromkeys(
        urljoin(home, a["href"]) for a in soup.find_all("a", href=True)
        if LINK.search(a.get_text(" ", strip=True) + " " + a["href"]) and urlparse(urljoin(home, a["href"])).netloc == urlparse(home).netloc
    ))[:4]
    out = [f"## {title} | {home}"]
    for p in pages:
        h = html if p == home else get(p)
        if not h:
            continue
        t = text_of(h)
        hits = []
        for m in SIGNAL.finditer(t):
            snippet = t[max(0, m.start() - 70): m.end() + 90]
            if not any(snippet[20:60] in x for x in hits):
                hits.append(snippet)
        if hits:
            out.append(f"   {p}")
            out += [f"     … {s} …" for s in hits[:4]]
    return "\n".join(out)


def main():
    sites = coop_sites()
    print(f"Prüfe {len(sites)} Seiten\n")
    for home in sites[:60]:
        print(check(home), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
