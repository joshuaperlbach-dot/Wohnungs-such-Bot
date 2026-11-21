#!/usr/bin/env python3
"""Einmaliges Werkzeug: Fotos aus einer (per Link freigegebenen) Google-Drive-Datei einer Wohnung hinzufügen.

ARG = "<Drive-Datei-ID> <Angebots-Schlüssel>". PDF (z.B. WBM-Fotodokumentation): alle eingebetteten Bilder,
ab ca. 300 px Breite. Bilddatei: so wie sie ist. Die Bilder landen in docs/img/<ordner des Angebots>/ als
fotodoku-NN.jpg; offers.save verknüpft sie beim nächsten Lauf (relink_kept_photos). Danach wird committet.
Es werden nur Bilder übernommen, kein Text (Exposés enthalten Namen von Ansprechpartnern)."""
import os
import re
import subprocess
import sys

import requests

sys.path.insert(0, ".")
import offers  # noqa: E402

file_id, key = os.environ["ARG"].split(maxsplit=1)
assert re.fullmatch(r"[\w-]{20,}", file_id), "Drive-ID sieht falsch aus"
store = offers.load()
assert key in store["offers"], f"Angebot nicht in der Liste: {key}"

s = requests.Session()
r = s.get("https://drive.google.com/uc", params={"export": "download", "id": file_id}, timeout=60)
if "text/html" in r.headers.get("Content-Type", ""):  # große Dateien: Bestätigungsseite
    m = re.search(r'name="confirm" value="([^"]+)"', r.text) or re.search(r"confirm=([\w-]+)", r.text)
    uuid = re.search(r'name="uuid" value="([^"]+)"', r.text)
    if m:
        params = {"export": "download", "id": file_id, "confirm": m.group(1)}
        if uuid:
            params["uuid"] = uuid.group(1)
        r = s.get("https://drive.usercontent.google.com/download", params=params, timeout=120)
kind = r.headers.get("Content-Type", "")
print("Download:", r.status_code, kind, len(r.content), "Bytes")
if "text/html" in kind:
    sys.exit("Keine Datei bekommen (vermutlich nicht für 'Jeder mit dem Link' freigegeben).")

folder = offers.PHOTO_DIR / offers.photo_folder(key)
folder.mkdir(parents=True, exist_ok=True)
saved = 0
if r.content[:4] == b"%PDF":
    import pymupdf as fitz
    doc = fitz.open(stream=r.content, filetype="pdf")
    seen = set()
    for page in doc:
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            pix = fitz.Pixmap(doc, xref)
            if pix.width < 300 or pix.height < 200:
                continue
            if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            saved += 1
            (folder / f"fotodoku-{saved:02d}.jpg").write_bytes(pix.tobytes("jpg", jpg_quality=85))
    print("PDF-Seiten:", len(doc))
elif kind.startswith("image/"):
    saved = 1
    (folder / f"fotodoku-01.{kind.split('/')[1].split(';')[0].replace('jpeg', 'jpg')}").write_bytes(r.content)
print("Bilder gespeichert:", saved)
if not saved:
    sys.exit("Keine Bilder gefunden.")
# die Liste zeigt ab jetzt auf die Kopien (Fotos der Gesellschaft sind offline)
o = store["offers"][key]
o["images"] = sorted(f"img/{folder.name}/{p.name}" for p in folder.iterdir() if not p.name.startswith("grundriss-"))
o["photos_kept"] = True
offers.save(store, offers.parse_time(store.get("updated")) or __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
subprocess.run(["git", "config", "user.name", "wbm-bot"], check=True)
subprocess.run(["git", "config", "user.email", "wbm-bot@users.noreply.github.com"], check=True)
subprocess.run(["git", "add", "-A", "docs/img", "docs/offers.json"], check=True)
subprocess.run(["git", "commit", "-m", f"Fotos aus der Fotodokumentation hinzugefügt ({saved} Bilder)"], check=True)
for _ in range(3):
    if subprocess.run(["git", "pull", "--rebase", "-X", "theirs"]).returncode == 0 and subprocess.run(["git", "push"]).returncode == 0:
        print("gespeichert")
        break
    subprocess.run(["git", "rebase", "--abort"])
else:
    sys.exit("Speichern fehlgeschlagen")
