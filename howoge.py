"""Besichtigungsanfrage bei der HOWOGE.

Ablauf auf der Seite: Exposé -> "Besichtigung anfragen" -> drei Hinweise bestätigen (WBS,
Haushaltsnettoeinkommen mindestens das Dreifache der Gesamtmiete, Bonitätsauskunft) ->
Vorname, Nachname, E-Mail absenden. Danach schickt die HOWOGE eine Mail mit einem
Bestätigungslink; erst wenn der geöffnet wurde, zählt die Anfrage. Das Öffnen übernimmt
mail_check.py.

Kein Captcha beobachtet. Taucht eines auf, wird nichts gesendet.
"""
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import wbm_bot

P = "tx_howrealestate_visitform[visitRequest]"
DOI_PATH = "/bewerbungsprozess/doi.html"  # so sieht der Bestätigungslink in der Mail aus


def send_request(listing, person, send):
    """Gibt (status, info) zurück. Nach dem Absenden ist der Status "unklar", bis der
    Bestätigungslink aus der Mail geöffnet wurde."""
    try:
        page = wbm_bot.HTTP.get(listing["url"], timeout=20)
        link = BeautifulSoup(page.text, "html.parser").find("a", href=re.compile("showVisitForm"))
        if link is None:
            return "fehler", "kein Anfrage-Knopf (evtl. schon vergeben)"
        form_url = urljoin(listing["url"], link["href"])
        form_page = wbm_bot.HTTP.get(form_url, headers={"Referer": listing["url"]}, timeout=20)
    except requests.RequestException as e:
        return "fehler", type(e).__name__
    soup = BeautifulSoup(form_page.text, "html.parser")
    form = next((f for f in soup.find_all("form") if f.find(attrs={"name": f"{P}[email]"})), None)
    if form is None:
        return "fehler", "Formular nicht gefunden"
    if re.search(r"captcha", str(form), re.I):
        return "fehler", "Formular hat ein Captcha, bitte von Hand anfragen"
    data = wbm_bot.form_defaults(form)
    data[f"{P}[firstName]"] = person["vorname"]
    data[f"{P}[lastName]"] = person["name"]
    data[f"{P}[email]"] = person["email"]
    if not send:
        return "trockenlauf", f"{len(data)} Felder vorbereitet, nichts gesendet"
    try:
        resp = wbm_bot.HTTP.post(urljoin(form_url, form.get("action", "")), data=data,
                                 headers={"Referer": form_url}, timeout=20)
    except requests.RequestException as e:
        return "unklar", type(e).__name__  # kann trotzdem angekommen sein
    if resp.status_code != 200:
        return "fehler", f"HTTP {resp.status_code}"
    return "unklar", "abgeschickt, wartet auf den Bestätigungslink per Mail"


def doi_links(urls):
    """Aus allen Links einer Mail nur echte Bestätigungslinks der HOWOGE. In der echten Mail
    (15.10.2026) steht der Link nur im Nur-Text-Teil, und zwar mit http:// statt https://;
    im HTML-Teil verbirgt er sich hinter einem Klickzähler. Geöffnet wird immer per https."""
    out = []
    for u in urls:
        parts = urlparse(u)
        if parts.scheme in ("http", "https") and parts.hostname == "www.howoge.de" and DOI_PATH in parts.path:
            out.append(parts._replace(scheme="https").geturl())
    return out


def confirm(url):
    """Bestätigungslink öffnen. True, wenn die Seite geantwortet hat."""
    try:
        return wbm_bot.HTTP.get(url, timeout=20).status_code == 200
    except requests.RequestException:
        return False
