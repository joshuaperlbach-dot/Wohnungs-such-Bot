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
    card("e", "Schöne 2 Zimmer", "940,00", "60", 2),
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
    with mock.patch("wbm_bot.HTTP.get", fake_get), mock.patch("wbm_bot.HTTP.post") as post:
        status, _ = send_request(l, PERSON, send=False)
    assert status == "trockenlauf"
    post.assert_not_called()


def test_send_posts_once_and_detects_success():
    l = {"url": "https://www.wbm.de/wohnungen-berlin/angebote/details/a/"}
    ok = mock.Mock(status_code=200, text="<p>Vielen Dank für Ihre Anfrage</p>")
    with mock.patch("wbm_bot.HTTP.get", fake_get), mock.patch("wbm_bot.HTTP.post", return_value=ok) as post:
        status, _ = send_request(l, PERSON, send=True)
    assert status == "gesendet"
    assert post.call_count == 1
    assert post.call_args[0][0] == "https://www.wbm.de/wohnungen-berlin/angebote/details/"
    assert post.call_args[1]["headers"]["Referer"] == l["url"]


def test_rating_parsing_and_score():
    import rating
    assert rating.parse_size("Warmmiete 46,16 m² Größe") == 46.16
    assert rating.parse_size("98,77 m²") == 98.77
    f = rating.parse_features("Bad mit Dusche Aufzug Neubau Balkon")
    assert f == {"balkon": True, "garten": False, "aufzug": True}
    assert rating.parse_features("Wohnung mit Garten")["garten"]
    assert rating.parse_features("EG mit Mietergarten")["garten"]
    assert rating.parse_features("Wohnung mit Gartenanteil")["garten"]
    for kein_garten in ("Tiergarten 2 Zimmer", "Kindergarten nebenan", "Gartenstraße 5, 10115 Berlin"):
        assert not rating.parse_features(kein_garten)["garten"], kein_garten
    assert rating.parse_floor("Wohnung im 3. OG mit Blick") == 3
    assert rating.parse_floor("Lage: Erdgeschoss") == 0
    assert rating.parse_floor("nichts dazu") is None
    c = rating.address_candidates("Helle Wohnung Bernauer Strasse 29, 10115 Berlin")
    assert c[0].endswith("10115 Berlin") and "29" in c[0] and c[-1] == "10115 Berlin", c
    kein = {"balkon": False, "garten": False, "aufzug": False}
    top = {"balkon": True, "garten": False, "aufzug": True}
    gut = rating.score(650, 65, 2, top, 1.0, 8, 1.0, 2, "Prenzlauer Berg")
    schlecht = rating.score(990, 30, 1, kein, 9.0, 0, 0.0, 6, "Spandau")
    assert gut[0] == 5 and gut[1] == 100 and schlecht[0] == 1, (gut, schlecht)
    neutral = rating.score(None, None, None, kein, None, None, None, None)
    assert 1 <= neutral[0] <= 5
    # Preis: bis 850 okay, darüber höchstens 4 Sterne (außer fast perfekt), 1000 = Ende
    assert rating.price_score(700) == 1.0 and rating.price_score(1000) == 0.0
    assert rating.price_score(850) > rating.price_score(900) > rating.price_score(1000)
    teuer = rating.score(930, 65, 2, top, 1.0, 8, 1.0, 2, "Prenzlauer Berg")
    assert teuer[0] == 4, teuer
    fast_perfekt = rating.score(860, 65, 2, top, 1.0, 8, 1.0, 2, "Prenzlauer Berg")
    assert fast_perfekt[0] == 5, fast_perfekt
    # Aufzug: bis 2. Stock unwichtig, ab 3. Stock je höher desto wichtiger, Garten ersetzt ihn
    assert rating.lift_score(1, kein) >= 0.85
    assert rating.lift_score(3, kein) > rating.lift_score(4, kein) > rating.lift_score(5, kein) == 0.0
    assert rating.lift_score(5, top) == 1.0
    assert rating.lift_score(4, {"balkon": False, "garten": True, "aufzug": False}) == 1.0
    # Größe: 60-70 m² ideal, Zimmer: 2 ideal
    assert rating.size_score(65) == 1.0 > rating.size_score(45) > rating.size_score(35)
    assert rating.size_score(65) > rating.size_score(90)
    assert rating.rooms_score(2) > rating.rooms_score(1)
    assert rating.district_score("Prenzlauer Berg 2 Zimmer") > rating.district_score("Mitte") > rating.district_score("Treptow")


def test_rate_listing_survives_network_failure():
    import rating
    l = {"text": "Mitte Schöne Wohnung Bernauer Strasse 29, 10115 Berlin 900,00 € Warmmiete 40 m² 1 Zimmer",
         "price": 900.0}
    with mock.patch("rating.requests.get", side_effect=Exception("offline")), \
            mock.patch("rating.requests.post", side_effect=Exception("offline")), \
            mock.patch("rating.time.sleep"):
        stars, why = rating.rate_listing(l, "")
    assert "★" in stars and "/5" in stars


def test_daily_summary_once_per_day():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Berlin")
    stats = {"day": "2026-10-13", "scanned": ["a", "b", "c"], "sent": 2, "unclear": 0, "summary_day": ""}
    msgs = []
    with mock.patch("wbm_bot.notify", side_effect=msgs.append):
        wbm_bot.daily_summary(stats, datetime(2026, 10, 13, 19, 50, tzinfo=tz))
        assert msgs == []  # noch zu früh
        wbm_bot.daily_summary(stats, datetime(2026, 10, 13, 20, 5, tzinfo=tz))
        wbm_bot.daily_summary(stats, datetime(2026, 10, 13, 20, 15, tzinfo=tz))
    assert len(msgs) == 1 and "3 Angebote gescannt" in msgs[0] and "2 Anfragen" in msgs[0], msgs
    # neuer Tag setzt Zähler zurück, summary_day bleibt erhalten
    with tempfile.TemporaryDirectory() as td:
        wbm_bot.STATS_FILE = Path(td) / "stats.json"
        wbm_bot.STATS_FILE.write_text(json.dumps(stats))
        s2 = wbm_bot.load_stats("2026-10-14")
    assert s2["scanned"] == [] and s2["sent"] == 0 and s2["summary_day"] == "2026-10-13"


def test_main_never_sends_twice():
    with tempfile.TemporaryDirectory() as td:
        wbm_bot.SEEN_FILE = Path(td) / "seen.json"
        wbm_bot.STATS_FILE = Path(td) / "stats.json"
        env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
        ok = mock.Mock(status_code=200, text="Vielen Dank")

        def get(url, **kw):
            return mock.Mock(text=LIST_HTML if url == wbm_bot.URL else FORM_HTML, status_code=200)

        with mock.patch.dict(os.environ, env), mock.patch("wbm_bot.HTTP.get", get), \
                mock.patch("wbm_bot.HTTP.post", return_value=ok) as post, mock.patch("wbm_bot.time.sleep"), \
                mock.patch("wbm_bot.rating.rate_listing", return_value=("★★★★☆ (4/5)", "42 m²")):
            wbm_bot.main()
            first = post.call_count
            wbm_bot.main()  # zweiter Lauf: nichts Neues
            assert post.call_count == first == 2, (first, post.call_count)
        assert len(json.loads(wbm_bot.SEEN_FILE.read_text())) == 3  # e, f gesendet; h nur gemeldet
        st = json.loads(wbm_bot.STATS_FILE.read_text())
        assert st["sent"] == 2 and len(st["scanned"]) == 7, st


def test_no_double_send_after_crash():
    import requests
    with tempfile.TemporaryDirectory() as td:
        wbm_bot.SEEN_FILE = Path(td) / "seen.json"
        wbm_bot.STATS_FILE = Path(td) / "stats.json"
        env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
        saved_before_post = []

        def get(url, **kw):
            return mock.Mock(text=LIST_HTML if url == wbm_bot.URL else FORM_HTML, status_code=200)

        def post_timeout(url, **kw):  # Anfrage hängt: evtl. trotzdem angekommen
            saved_before_post.append(len(json.loads(wbm_bot.SEEN_FILE.read_text())))
            raise requests.Timeout("hängt")

        def crash(*a, **kw):  # Lauf bricht nach dem Senden ab
            raise KeyboardInterrupt

        with mock.patch.dict(os.environ, env), mock.patch("wbm_bot.HTTP.get", get), \
                mock.patch("wbm_bot.time.sleep"), mock.patch("wbm_bot.notify", crash), \
                mock.patch("wbm_bot.rating.rate_listing", return_value=("★", "")):
            with mock.patch("wbm_bot.HTTP.post", post_timeout):
                try:
                    wbm_bot.main()
                except KeyboardInterrupt:
                    pass
            assert saved_before_post == [1, 2], saved_before_post  # vor jedem POST gespeichert
            with mock.patch("wbm_bot.HTTP.post") as post:
                try:
                    wbm_bot.main()  # nächster Lauf: nichts erneut senden
                except KeyboardInterrupt:
                    pass
                post.assert_not_called()


def test_over_limit_best_stars_first_rest_next_run():
    many = "<div class='list'>" + "".join(
        card(f"w{i}", "Schöne 2 Zimmer", "800,00", "60", 2) for i in range(5)) + "</div>"
    env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
    ok = mock.Mock(status_code=200, text="Vielen Dank")
    stars = {"w0": 1, "w1": 5, "w2": 2, "w3": 4, "w4": 3}
    sent = []

    def get(url, **kw):
        return mock.Mock(text=many if url == wbm_bot.URL else FORM_HTML, status_code=200)

    def post(url, **kw):
        sent.append(kw["headers"]["Referer"].split("/")[-2])
        return ok

    def rate(l, text=""):
        return "★" * stars[l["url"].split("/")[-2]], ""

    with tempfile.TemporaryDirectory() as td:
        wbm_bot.SEEN_FILE = Path(td) / "seen.json"
        wbm_bot.STATS_FILE = Path(td) / "stats.json"
        with mock.patch.dict(os.environ, env), mock.patch("wbm_bot.HTTP.get", get), \
                mock.patch("wbm_bot.HTTP.post", post), mock.patch("wbm_bot.time.sleep"), \
                mock.patch("wbm_bot.rating.rate_listing", rate):
            wbm_bot.main()
            assert sent == ["w1", "w3", "w4"], sent  # die besten drei zuerst
            wbm_bot.main()
            assert sent == ["w1", "w3", "w4", "w0", "w2"], sent  # Rest beim nächsten Lauf
            wbm_bot.main()
            assert len(sent) == 5, sent  # danach nichts mehr, nie doppelt


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
