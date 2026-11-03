#!/usr/bin/env python3
"""Einmaliges Werkzeug: öffnet das zweite degewo-Formular in einem echten Browser und gibt nur
den AUFBAU aus (Beschriftungen, Feldtypen, Pflichtfelder, Auswahlmöglichkeiten). Keine Werte,
keine Adresse: das Protokoll des öffentlichen Repos ist für alle sichtbar."""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

url = os.environ.get("FORM_URL") or next(
    (o.get("form_url") for o in json.loads(Path("docs/offers.json").read_text())["offers"].values()
     if o.get("form_url")), None)
if not url:
    sys.exit("Kein Formular-Link gefunden.")

DUMP = """() => {
  const label = el => {
    const t = [];
    if (el.id) document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`).forEach(l => t.push(l.innerText));
    const w = el.closest('label, mat-form-field, .form-group, .field, fieldset'); if (w) t.push(w.innerText.slice(0, 160));
    t.push(el.getAttribute('aria-label') || '', el.placeholder || '', el.getAttribute('formcontrolname') || '');
    return t.filter(Boolean).join(' | ').replace(/\\s+/g, ' ').slice(0, 240);
  };
  const fields = [...document.querySelectorAll('input, select, textarea, mat-select, [role=combobox], [role=radiogroup], mat-checkbox')]
    .map(el => ({tag: el.tagName.toLowerCase(), type: el.type || el.getAttribute('role') || '', name: el.name || '',
                 required: !!(el.required || el.getAttribute('aria-required') === 'true'),
                 visible: !!(el.offsetWidth || el.offsetHeight), label: label(el),
                 options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()).slice(0, 25) : undefined}));
  const buttons = [...document.querySelectorAll('button, [type=submit]')].map(b => b.innerText.trim()).filter(Boolean);
  const headings = [...document.querySelectorAll('h1, h2, h3, h4, legend')].map(h => h.innerText.trim()).filter(Boolean);
  return {title: document.title, host: location.host, headings, buttons, fields,
          captcha: !!document.querySelector('[class*=captcha], iframe[src*=captcha], .frc-captcha, .g-recaptcha')};
}"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(locale="de-DE")
    resp = page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(4000)
    info = page.evaluate(DUMP)
    info["status"] = resp.status if resp else None
    for f in info["fields"]:  # Sicherheitshalber: nie Werte ausgeben
        f.pop("value", None)
    print(json.dumps(info, indent=1, ensure_ascii=False))
    browser.close()
