#!/usr/bin/env python3
"""Liest die Antworten der Wohnungsgesellschaften aus dem Postfach.

Nach einer Anfrage kommt die Antwort per Mail: Absage oder Einladung zur Besichtigung.
Dieses Skript schaut nur lesend ins Postfach (nichts wird als gelesen markiert, verschoben
oder gelöscht), ordnet die Mails den angefragten Angeboten zu und trägt das Ergebnis in die
Handy-Ansicht ein. Bei einer Einladung kommt sofort eine Push-Meldung.

Zugang über Umgebungsvariablen (GitHub-Secrets), nie im Code:
  MAIL_USER           z.B. name@gmail.com
  MAIL_APP_PASSWORD   App-Passwort von Google (nicht das normale Passwort)
Fehlen sie, macht das Skript nichts.

Das Repo ist öffentlich, die Logs auch: es wird nie Mail-Inhalt ausgegeben.
"""
import email
import email.policy
import hashlib
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

import howoge
import offers
import wbm_bot

HOST = "imap.gmail.com"
SENDERS = ["wbm.de", "howoge.de"]  # nur Mails von diesen Absendern werden überhaupt abgerufen
LOOK_BACK = timedelta(days=14)
SEEN_FILE = Path(__file__).with_name("mail_seen.json")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

# Absage zuerst prüfen: auch sie enthält das Wort "Besichtigung"
DECLINE = r"nicht möglich, alle Anfragen|leider|nicht berücksichtig|bereits vergeben|abgesagt|Absage"
INVITE = r"einlad|Besichtigungstermin|Termin (am|für|zur)|laden wir Sie|Bitte bestätigen Sie"


def classify(text):
    """'absage', 'einladung' oder 'antwort' (unklar, selbst nachsehen)."""
    if re.search(DECLINE, text, re.I):
        return "absage"
    if re.search(INVITE, text, re.I):
        return "einladung"
    return "antwort"


def norm(text):
    """Für den Adressvergleich: 'Kleine Andreasstraße 11' == 'Kleine Andreasstrasse 11' ==
    'Kleine Andreasstr. 11'. Umlaute und ß fallen ganz weg, damit auch Mails mit kaputt
    kodierten Sonderzeichen ("straÃŸe") noch passen."""
    t = "".join(c if ord(c) < 128 else " " if c.isspace() else "" for c in text.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"stra(?:ss)?e\b|str\b", "str", t).strip()


def street_of(address):
    """'Kleine Andreasstrasse 11, 10243 Berlin' -> 'kleine andreasstrasse 11'."""
    return norm(address.split(",")[0])


def find_offer(store, text):
    """Das angefragte Angebot, dessen Straße und Hausnummer in der Mail steht."""
    haystack = f" {norm(text)} "
    for key, o in store["offers"].items():
        street = street_of(o.get("address") or "")
        if o.get("requested") in offers.REQUESTED and street and f" {street} " in haystack:
            return key
    return None


def body_text(msg):
    """Der ganze Text der Mail. Manche Absender schicken den Inhalt nur als HTML und im
    Nur-Text-Teil bloß einen Hinweis, deshalb werden alle Textteile zusammengenommen."""
    texts = []
    for part in msg.walk():
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if part.get_content_subtype() == "html":
            content = BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
        texts.append(content)
    return "\n".join(texts)


def links(msg):
    """Alle Links der Mail (aus HTML-Teilen die Verweise, aus Text-Teilen ausgeschriebene Adressen)."""
    found = []
    for part in msg.walk():
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if part.get_content_subtype() == "html":
            found += [a["href"] for a in BeautifulSoup(content, "html.parser").find_all("a", href=True)]
        else:
            # Links im Text stehen manchmal in Klammern: "[Anfrage bestätigen](http://...)"
            found += [u.rstrip(").,]>") for u in re.findall(r"https?://[^\s<>\"']+", content)]
    return found


# Der Bestätigungslink der HOWOGE gilt nur 24 Stunden
CONFIRM_VALID = timedelta(hours=24)
# Bestätigungsmail der HOWOGE, auch wenn der Link darin nicht erkannt wird
DOI_MAIL = r"bestätigen Sie Ihre Anmeldung|Besichtigungsanfrage für eine unserer Wohnungen gestellt"


def expire_confirmations(store, now):
    """Offene Bestätigungen, deren Link schon abgelaufen ist, als 'abgelaufen' markieren,
    damit eine spätere Bestätigungsmail nicht dem falschen Angebot zugeordnet wird."""
    for o in store["offers"].values():
        since = offers.parse_time(o.get("requested_at"))
        if o.get("confirm") == "offen" and since and now - since > CONFIRM_VALID:
            o["confirm"] = "abgelaufen"


def pending_confirmation(store, text):
    """Angefragtes Angebot, das noch auf den Bestätigungslink wartet: das aus der Mail,
    sonst das älteste offene."""
    waiting = {k: o for k, o in store["offers"].items() if o.get("confirm") == "offen"}
    key = find_offer({"offers": waiting}, text)
    if key is None and waiting:
        key = min(waiting, key=lambda k: waiting[k].get("requested_at") or "")
    return key


def mail_id(msg):
    raw = (msg.get("Message-ID") or f"{msg.get('Date')}{msg.get('Subject')}").encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]  # nur ein Fingerabdruck landet im Repo


def process(messages, store, seen, now):
    """Neue Mails auswerten. Gibt die Push-Meldungen zurück (ohne Mail-Inhalt)."""
    notes = []
    expire_confirmations(store, now)
    for msg in messages:
        mid = mail_id(msg)
        if mid in seen:
            continue
        seen.add(mid)
        text = f"{msg.get('Subject', '')}\n{body_text(msg)}"
        confirm_links = howoge.doi_links(links(msg))
        if confirm_links:
            # HOWOGE: die Anfrage zählt erst, wenn der Link aus der Mail geöffnet wurde
            kind, key = "bestaetigung", pending_confirmation(store, text)
            where = store["offers"][key].get("address", "") if key else "ein Angebot"
            if key is None:
                pass  # kein offener Vorgang des Bots (z.B. von Hand angefragt): nichts anklicken
            elif howoge.confirm(confirm_links[0]):
                store["offers"][key].update(requested="gesendet", confirm="bestaetigt")
            else:
                notes.append(f"HOWOGE-Anfrage für {where}: Bestätigungslink ließ sich nicht öffnen.\n"
                             "Bitte in der Mail selbst anklicken!")
        elif re.search(DOI_MAIL, text, re.I):
            # Bestätigungsmail, aber kein Link erkannt: nicht als Einladung werten, sondern Bescheid geben
            kind, key = "bestaetigung", pending_confirmation(store, text)
            if key:
                where = store["offers"][key].get("address", "")
                notes.append(f"HOWOGE-Anfrage für {where}: Bestätigungslink nicht erkannt.\n"
                             "Bitte in der Mail selbst anklicken (gilt 24 Stunden)!")
        else:
            kind = classify(text)
            key = find_offer(store, text)
            where = store["offers"][key].get("address", "") if key else "ein Angebot (nicht zugeordnet)"
            if key:
                store["offers"][key].update(reply=kind, reply_at=offers.iso(now))
        print(f"Mail ausgewertet: {kind}, {'zugeordnet' if key else 'nicht zugeordnet'}")
        # kurzes Protokoll ohne Inhalt (die letzten 10), zur Kontrolle in offers.json
        store["mail_log"] = (store.get("mail_log") or [])[-9:] + [
            {"at": offers.iso(now), "kind": kind, "matched": bool(key), "chars": len(text)}]
        if kind == "einladung":
            notes.append(f"EINLADUNG zur Besichtigung: {where}\nSchau in deine Mails und bestätige den Termin!")
        elif kind == "antwort" and key:  # sonstige Mails ohne Bezug zu einem Angebot: kein Push
            notes.append(f"Antwort einer Wohnungsgesellschaft zu: {where}\nBitte in den Mails nachsehen.")
    return notes


def fetch_messages(user, password, now):
    """Mails der bekannten Absender aus den letzten Tagen holen (nur lesend)."""
    since = now - LOOK_BACK
    since = f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"
    box = imaplib.IMAP4_SSL(HOST)
    try:
        box.login(user, password)
        box.select("INBOX", readonly=True)
        messages = []
        for sender in SENDERS:
            _, data = box.search(None, "FROM", f'"{sender}"', "SINCE", since)
            for num in data[0].split():
                _, parts = box.fetch(num, "(BODY.PEEK[])")
                messages.append(email.message_from_bytes(parts[0][1], policy=email.policy.default))
        return messages
    finally:
        try:
            box.logout()
        except Exception:
            pass


def main():
    user, password = os.environ.get("MAIL_USER"), os.environ.get("MAIL_APP_PASSWORD")
    if not user or not password:
        print("[Mail] nicht eingerichtet (MAIL_USER / MAIL_APP_PASSWORD fehlen).")
        return
    now = datetime.now(wbm_bot.TZ)
    stats = wbm_bot.load_stats(now.strftime("%Y-%m-%d"))
    try:
        messages = fetch_messages(user, password.replace(" ", ""), now)
    except Exception as e:  # falsches Passwort, Netz weg: melden, aber den Lauf nicht abbrechen
        wbm_bot.alert(stats, f"Mail-Abruf klappt nicht ({type(e).__name__}). App-Passwort prüfen.",
                      key="alert_day_mail")
        wbm_bot.STATS_FILE.write_text(json.dumps(stats, indent=1, ensure_ascii=False))
        return
    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    known = len(seen)
    store = offers.load()
    notes = process(messages, store, seen, now)
    if len(seen) > known:  # es gab neue Mails
        offers.save(store, now)
        SEEN_FILE.write_text(json.dumps(sorted(seen)))
    for note in notes:
        wbm_bot.notify(note)
    print(f"[Mail] {len(messages)} Mails geprüft, {len(notes)} Meldungen.")


if __name__ == "__main__":
    main()
