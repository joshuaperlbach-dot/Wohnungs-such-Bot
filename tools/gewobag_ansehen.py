#!/usr/bin/env python3
"""Einmaliges Werkzeug: aktuelle Gewobag-Angebote ansehen (nur Aufbau, keine persönlichen Daten).

1. Abruf wie der Bot (requests): Status, Weiterleitung, wie viele Bilder im Objektordner.
2. Echter Browser: Bilder nach dem Laden, Anfrageformular (Felder, Knöpfe, Captcha-Spuren).
"""
import json
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import ibw_bot  # noqa: E402
import offers  # noqa: E402
import wbm_bot  # noqa: E402

store = offers.load()
urls = [o["url"] for k, o in sorted(store["offers"].items(), reverse=True)
        if "gewobag.de/fuer-mietinteressentinnen/mietangebote/" in o.get("url", "") and not o.get("gone")][:4]

for u in urls:
    r = wbm_bot.HTTP.get(u, timeout=20)
    token = u.rstrip("/").rsplit("/", 1)[-1]
    print(f"\n== {u}\n   requests: {r.status_code} -> {r.url} | {len(r.text)} Zeichen | Objektordner-Treffer: {r.text.count(token)}"
          f" | page_images: {len(ibw_bot.page_images(r.text, u))}")
    # Wie kennzeichnet die Seite geförderte/WBS-Wohnungen? (Textstellen, Navigation inklusive)
    from genossenschaften import text_of
    t = text_of(r.text)
    for m in re.finditer(r"geförder|förderung|WBS|Wohnberechtigung", t, re.I):
        print("   WBS?", "…" + t[max(0, m.start() - 80): m.end() + 80] + "…")
    for m in sorted(set(re.findall(r"[\w./:-]*" + re.escape(token) + r"[\w./-]*", r.text)))[:6]:
        print("     ", m[:160])

DUMP = """() => {
  const label = el => {
    const t = [];
    if (el.id) document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`).forEach(l => t.push(l.innerText));
    t.push(el.getAttribute('aria-label') || '', el.placeholder || '', el.name || '');
    return t.filter(Boolean).join(' | ').replace(/\\s+/g, ' ').slice(0, 160);
  };
  return {
    title: document.title,
    imgs: [...document.querySelectorAll('img')].map(i => i.currentSrc || i.src).filter(s => s.includes('gewo-immo-media')).length,
    iframes: [...document.querySelectorAll('iframe')].map(f => f.src.split('?')[0]),
    scripts: [...document.querySelectorAll('script[src]')].map(s => s.src.split('?')[0]).filter(s => /captcha|recaptcha|hcaptcha|friendly|turnstile|wohnungshelden/i.test(s)),
    buttons: [...document.querySelectorAll('button, a.button, a.btn, [type=submit]')].map(b => b.innerText.trim()).filter(t => t && t.length < 60).slice(0, 30),
    fields: [...document.querySelectorAll('input, select, textarea')].filter(e => e.type !== 'hidden')
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.type, required: e.required, visible: !!(e.offsetWidth || e.offsetHeight), label: label(e)})),
    captcha: [...document.querySelectorAll('[class*=captcha i], [id*=captcha i], .frc-captcha, .g-recaptcha, .h-captcha, .cf-turnstile')].map(e => e.className || e.id).slice(0, 5),
  };
}"""

if urls:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(locale="de-DE")
        page.goto(urls[0], wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        print("\n== Browser, Seite:", json.dumps(page.evaluate(DUMP), indent=1, ensure_ascii=False))
        for name in ("Anfrage", "Kontakt", "Interesse", "Bewerb", "Jetzt"):
            btn = page.get_by_role("button", name=re.compile(name, re.I))
            if btn.count() == 0:
                btn = page.get_by_role("link", name=re.compile(name, re.I))
            if btn.count():
                print(f"\n-- klicke auf '{btn.first.inner_text().strip()}'")
                try:
                    btn.first.click(timeout=5000)
                    page.wait_for_timeout(5000)
                except Exception as e:
                    print("   Klick ging nicht:", type(e).__name__)
                break
        print("\n== Browser, nach Klick:", json.dumps(page.evaluate(DUMP), indent=1, ensure_ascii=False))
        for f in page.frames[1:]:
            try:
                print(f"\n== Rahmen {f.url.split('?')[0]}:", json.dumps(f.evaluate(DUMP), indent=1, ensure_ascii=False))
            except Exception as e:
                print("Rahmen nicht lesbar:", type(e).__name__)
        b.close()
