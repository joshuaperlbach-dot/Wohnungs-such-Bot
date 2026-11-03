#!/usr/bin/env python3
"""Zweites degewo-Formular automatisch ausfüllen und abschicken.

Nach einer Anfrage bei der degewo kommt eine Mail "Bitte füllen Sie das beigefügte Formular aus"
(vermietung.degewo.de, Wohnungshelden). Erst damit wird die Anfrage aufgenommen. Das Formular hat
kein Captcha und fünf Felder (Stand 03.11.2026): Telefonnummer, Anzahl einziehende Personen,
davon Anzahl Kinder, monatliches Haushaltsnettoeinkommen, "für sich selbst oder für Dritte?".

mail_check.py legt gefundene Formular-Links in PENDING ab (nur für diesen Lauf, nie im Repo:
der Link ist persönlich). Der Workflow startet dieses Skript nur, wenn die Datei existiert.

Daten: Telefon aus WBM_PERSON_JSON, Einkommen aus dem Secret HAUSHALT_EINKOMMEN.
Echt abgeschickt wird nur mit WBM_SEND=1. Im Protokoll (öffentlich) stehen nie Link oder Werte.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import offers
import wbm_bot

PENDING = Path(__file__).with_name(".degewo_forms.json")
PERSONEN = "1"
KINDER = "0"
SUCCESS = re.compile(r"vielen dank|erfolgreich|übermittelt|eingegangen|gesendet", re.I)


def fill(page, phone, income, send):
    """Formular auf der geöffneten Seite ausfüllen. Gibt (Status, Info) zurück."""
    def field(label, value):
        box = page.get_by_label(re.compile(label, re.I)).first
        box.fill(str(value))

    field(r"Telefonnummer", phone)
    field(r"Anzahl einziehende Personen", PERSONEN)
    field(r"Anzahl Kinder", KINDER)
    field(r"Haushaltsnettoeinkommen", income)
    page.get_by_role("combobox", name=re.compile(r"für sich selbst", re.I)).first.click()
    page.get_by_role("option", name=re.compile(r"selbst|mich", re.I)).first.click()
    if not send:
        return "trockenlauf", "ausgefüllt, nicht abgeschickt"
    page.get_by_role("button", name=re.compile(r"^\s*Absenden\s*$", re.I)).first.click()
    page.wait_for_timeout(5000)
    text = page.inner_text("body")
    if SUCCESS.search(text):
        return "gesendet", "Bestätigung auf der Seite"
    if page.get_by_role("button", name=re.compile(r"Absenden", re.I)).count() == 0:
        return "gesendet", "Formular verschwunden"
    return "unklar", "keine Bestätigung erkannt"


def run(pending, phone, income, send, browser):
    results = []
    for item in pending:
        page = browser.new_page(locale="de-DE")
        try:
            page.goto(item["url"], wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            results.append((item, *fill(page, phone, income, send)))
        except Exception as e:  # Seite anders als erwartet: Bescheid geben, Link per Push
            results.append((item, "fehler", type(e).__name__))
        finally:
            page.close()
    return results


def main():
    if not PENDING.exists():
        return
    pending = json.loads(PENDING.read_text())
    person, _ = wbm_bot.load_person()
    income = os.environ.get("HAUSHALT_EINKOMMEN", "").strip()
    send = os.environ.get("WBM_SEND") == "1"
    if not income or not person.get("telefon"):
        for item in pending:
            wbm_bot.notify(f"degewo will für {item['where']} noch ein zweites Formular. Der Bot kann es nicht "
                           f"selbst ausfüllen (Einkommen oder Telefon fehlt in den Secrets).\n{item['url']}")
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        results = run(pending, person["telefon"], income, send, browser)
        browser.close()
    now = datetime.now(wbm_bot.TZ)
    store = offers.load()
    for item, status, info in results:
        print(f"[degewo-Formular] {status} ({info})")
        o = store["offers"].get(item.get("key") or "")
        if status == "gesendet":
            if o:
                o.update(confirm="formular_gesendet")
        elif status != "trockenlauf":
            wbm_bot.notify(f"degewo will für {item['where']} noch ein zweites Formular. Der Bot hat es versucht, "
                           f"aber es hat nicht geklappt ({status}). Bitte selbst ausfüllen:\n{item['url']}")
    offers.save(store, now)
    PENDING.unlink()


if __name__ == "__main__":
    main()
