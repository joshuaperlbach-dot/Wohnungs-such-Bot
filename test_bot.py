import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import wbm_bot
from wbm_bot import parse_listings, matches, matches_strict, build_payload, send_request

P = "tx_powermail_pi1[field]"


def card(slug, title, price, size, rooms, feats="", d="Mitte"):
    return f"""<div class="card"><span class="ort">{d}</span><a href="https://www.wbm.de/wohnungen-berlin/angebote/details/{slug}/" title="Details">{title}</a>
    <ul><li>{price} € Warmmiete</li><li>{size} m² Größe</li><li>{rooms} Zimmer</li>{feats}</ul></div>"""


LIST_HTML = "<div class='list'>" + "".join([
    card("a", "Kompakte 1-Zimmer-Wohnung in Spandau, nahe Mitte", "855,38", "48,19", 1, d="Spandau"),
    card("b", "Zentrale 1-Zimmer in Mitte", "1.254,62", "46,16", 1),
    card("c", "WBS 160 - Lichtdurchflutete 3 Zimmer mit Balkon", "1.112,13", "71,75", 3, "<li>WBS</li>"),
    card("d", "2-Zimmer WBS 100", "480,00", "52", 2, "<li>WBS</li>"),
    card("e", "Schöne 2 Zimmer", "990,00", "60", 2),
    card("f", "Helle 1 Zimmer", "700,00", "40", 1, d="Neukölln"),
    card("h", "1 Zimmer unbekannter Ort", "600,00", "35", 1, d="Irgendwo"),
]) + "</div>"

# Nachbau des Powermail-Formulars (Feldnamen aus der echten Seite ausgelesen)
FORM_HTML = f"""<form method="post" action="/wohnungen-berlin/angebote/details/" enctype="multipart/form-data">
<input type="hidden" name="tx_powermail_pi1[__trustedProperties]" value="TOKEN123">
<input type="hidden" name="tx_powermail_pi1[field][objekt]" value="OBJ-42">
<input type="hidden" name="tx_powermail_pi1[mail][form]" value="7">
<input type="radio" name="{P}[wbsvorhanden]" value="1"><input type="radio" name="{P}[wbsvorhanden]" value="0">
<select name="{P}[wbszimmeranzahl]"><option value="">-</option><option value="1">1</option></select>
<input type="checkbox" name="{P}[wbsmitbesonderemwohnbedarf][]" value="x">
<select name="{P}[anrede]"><option>Frau</option><option>Herr</option><option>Offen</option></select>
<input type="text" name="{P}[name]"><input type="text" name="{P}[vorname]">
<input type="text" name="{P}[strasse]"><input type="text" name="{P}[plz]"><input type="text" name="{P}[ort]">
<input type="text" name="{P}[e_mail]"><input type="text" name="{P}[telefon]">
<input type="hidden" name="{P}[datenschutzhinweis]" value="">
<input type="checkbox" name="{P}[datenschutzhinweis][]" value="1">
<input type="text" name="{P}[__hp]">
<input type="submit" value="Anfrage absenden"></form>"""

PERSON = dict(anrede="Herr", name="Mustermann", vorname="Max", strasse="Teststr. 1",
              plz="10115", ort="Berlin", email="max@example.com", telefon="0123")


def test_listings_and_filter():
    ls = parse_listings(LIST_HTML)
    assert len(ls) == 7, len(ls)
    hits = [l["url"].split("/")[-2] for l in ls if matches(l)]
    assert hits == ["e", "f", "h"], hits  # a (Spandau) raus, h (Bezirk unklar) nur melden
    assert [l["url"].split("/")[-2] for l in ls if matches_strict(l)] == ["e", "f"]
    # unklare Daten: melden ja, automatisch senden nein
    unklar = {"price": None, "rooms": 1, "wbs": False, "district": True}
    assert matches(unklar) and not matches_strict(unklar)


def test_payload():
    from bs4 import BeautifulSoup
    form = BeautifulSoup(FORM_HTML, "html.parser").form
    d = build_payload(form, PERSON)
    assert d["tx_powermail_pi1[__trustedProperties]"] == "TOKEN123"
    assert d["tx_powermail_pi1[field][objekt]"] == "OBJ-42"
    assert d[f"{P}[wbsvorhanden]"] == "0"
    assert d[f"{P}[anrede]"] == "Herr"
    assert d[f"{P}[datenschutzhinweis][]"] == "1"
    assert d[f"{P}[__hp]"] == ""
    assert d[f"{P}[e_mail]"] == "max@example.com"
    assert f"{P}[wbsmitbesonderemwohnbedarf][]" not in d  # nicht angehakt


def fake_get(url, **kw):
    return mock.Mock(text=FORM_HTML, status_code=200)


def test_dry_run_sends_nothing():
    l = {"url": "https://www.wbm.de/wohnungen-berlin/angebote/details/a/"}
    with mock.patch("wbm_bot.requests.get", fake_get), mock.patch("wbm_bot.requests.post") as post:
        status, _ = send_request(l, PERSON, send=False)
    assert status == "trockenlauf"
    post.assert_not_called()


def test_send_posts_once_and_detects_success():
    l = {"url": "https://www.wbm.de/wohnungen-berlin/angebote/details/a/"}
    ok = mock.Mock(status_code=200, text="<p>Vielen Dank für Ihre Anfrage</p>")
    with mock.patch("wbm_bot.requests.get", fake_get), mock.patch("wbm_bot.requests.post", return_value=ok) as post:
        status, _ = send_request(l, PERSON, send=True)
    assert status == "gesendet"
    assert post.call_count == 1
    assert post.call_args[0][0] == "https://www.wbm.de/wohnungen-berlin/angebote/details/"


def test_main_never_sends_twice():
    with tempfile.TemporaryDirectory() as td:
        wbm_bot.SEEN_FILE = Path(td) / "seen.json"
        env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
        ok = mock.Mock(status_code=200, text="Vielen Dank")

        def get(url, **kw):
            return mock.Mock(text=LIST_HTML if url == wbm_bot.URL else FORM_HTML, status_code=200)

        with mock.patch.dict(os.environ, env), mock.patch("wbm_bot.requests.get", get), \
                mock.patch("wbm_bot.requests.post", return_value=ok) as post, mock.patch("wbm_bot.time.sleep"):
            wbm_bot.main()
            first = post.call_count
            wbm_bot.main()  # zweiter Lauf: nichts Neues
            assert post.call_count == first == 2, (first, post.call_count)
        assert len(json.loads(wbm_bot.SEEN_FILE.read_text())) == 3  # e, f gesendet; h nur gemeldet


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
