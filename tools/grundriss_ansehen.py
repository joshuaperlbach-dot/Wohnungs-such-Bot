#!/usr/bin/env python3
"""Einmaliges Werkzeug: Wo steckt der Grundriss? Für aktuelle Angebote je Gesellschaft alle Bilder und
Dateien ausgeben, die nach Grundriss aussehen (Name, alt-Text, Linktext), dazu alle GIF/PNG/PDF.
Nur öffentliche Seiten, keine persönlichen Daten."""
import re
import sys
from collections import defaultdict
from urllib.parse import urljoin

sys.path.insert(0, ".")
from bs4 import BeautifulSoup  # noqa: E402

import offers  # noqa: E402
import wbm_bot  # noqa: E402

HINT = re.compile(r"grundriss|floor.?plan|\bplan\b|\.gif|\.png|\.pdf|\.svg", re.I)
store = offers.load()
per = defaultdict(list)
for k, o in sorted(store["offers"].items(), key=lambda kv: kv[1].get("online_since") or "", reverse=True):
    if not o.get("gone") and o.get("url", "").startswith("https://"):
        per[o.get("company") or "?"].append(o["url"])
for company, urls in per.items():
    for url in urls[:2]:
        try:
            r = wbm_bot.HTTP.get(url, timeout=20)
        except Exception as e:
            print(f"\n== {company} {url}\n   {type(e).__name__}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        print(f"\n== {company} {url} -> {r.status_code}")
        seen = set()
        for tag in soup.find_all(["img", "a", "source", "object", "iframe"]):
            for attr in ("src", "data-src", "href", "srcset", "data-srcset", "data"):
                v = tag.get(attr)
                if not v:
                    continue
                v = v.split(",")[-1].strip().split(" ")[0]
                label = " ".join(filter(None, [tag.get("alt"), tag.get("title"), tag.get_text(" ", strip=True)[:60]]))
                if (HINT.search(v) or HINT.search(label)) and v not in seen and not re.search(r"logo|icon|favicon|sprite", v, re.I):
                    seen.add(v)
                    print(f"   <{tag.name} {attr}> {urljoin(url, v)[:170]}  | {label[:80]}")
        for m in re.finditer(r"grundriss", r.text, re.I):
            print("   Text:", re.sub(r"\s+", " ", r.text[max(0, m.start() - 120): m.end() + 120])[:240])
            break
