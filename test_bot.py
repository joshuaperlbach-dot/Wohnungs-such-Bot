import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import offers
import wbm_bot

offers.FILE = Path(tempfile.mkdtemp()) / "offers.json"  # Tests schreiben nie in die echte Liste

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
    # multipart wie im Browser, mit allen Feldern
    files = post.call_args[1]["files"]
    assert files[f"{P}[e_mail]"] == (None, "max@example.com") and "data" not in post.call_args[1]


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
    # Feld im Exposé geht vor ("EG" kann sonst irgendwo auf der Seite stehen)
    assert rating.parse_floor("Größe: ca. 71.75 m² Etage: 4 Objektnummer EG") == 4
    assert rating.parse_floor("Etage: EG Objektnummer") == 0
    assert rating.parse_floor("Etage: DG Objektnummer") is None
    # Ringbahn ohne Netz: Ostkreuz nah, Alexanderplatz mittel/weit, Spandau weit
    assert rating.ring_score((52.5035, 13.4700)) == 1.0
    assert rating.ring_score((52.5380, 13.4250)) == 1.0  # Prenzlauer Berg, nahe Prenzlauer Allee
    assert rating.ring_score((52.5219, 13.4132)) == 0.0  # Alexanderplatz
    assert rating.ring_score((52.5350, 13.2000)) == 0.0  # Spandau
    assert len(rating.RINGBAHN) == 27
    assert wbm_bot.parse_rooms("Helle 2,5 Zimmer") == 2.5 and wbm_bot.parse_rooms("3 Zimmer mit Balkon 2 Zimmer") == 2
    assert wbm_bot.parse_cold_rent("<p>Mietkosten Nettokaltmiete 1.074,60 EUR Nebenkosten</p>") == 1074.60
    assert wbm_bot.parse_cold_rent("<p>nichts</p>") is None
    ok = {"price": 940.0, "rooms": 2, "wbs": False, "district": True}
    assert matches_strict(ok) and matches({**ok, "cold": 880.0}) and not matches({**ok, "cold": 910.0})
    c = rating.address_candidates("Helle Wohnung Bernauer Strasse 29, 10115 Berlin")
    assert c[0].endswith("10115 Berlin") and "29" in c[0] and c[-1] == "10115 Berlin", c
    kein = {"balkon": False, "garten": False, "aufzug": False}
    top = {"balkon": True, "garten": False, "aufzug": True}
    gut = rating.score(650, 70, 2, top, 1.0, 20, 1.0, 2, "Prenzlauer Berg", 1910, (400, 100), 5)
    schlecht = rating.score(990, 30, 1, kein, 9.0, 0, 0.0, 6, "Spandau")
    assert gut[0] == 5 and gut[1] == 100 and schlecht[0] == 1, (gut, schlecht)
    neutral = rating.score(None, None, None, kein, None, None, None, None)
    assert 1 <= neutral[0] <= 5
    # Preis: bis 850 okay, darüber höchstens 4 Sterne (außer fast perfekt), 1000 = Ende
    assert rating.price_score(700) == 1.0 and rating.price_score(1000) == 0.0
    assert rating.price_score(850) > rating.price_score(900) > rating.price_score(1000)
    teuer = rating.score(930, 70, 2, top, 1.0, 20, 1.0, 2, "Prenzlauer Berg", 1910, (400, 100), 5)
    assert teuer[0] == 4, teuer
    fast_perfekt = rating.score(860, 70, 2, top, 1.0, 20, 1.0, 2, "Prenzlauer Berg", 1910, (400, 100), 5)
    assert fast_perfekt[0] == 5, fast_perfekt
    # Aufzug: bis 2. Stock unwichtig, ab 3. Stock je höher desto wichtiger, Garten ersetzt ihn
    assert rating.lift_score(1, kein) >= 0.85
    assert rating.lift_score(3, kein) > rating.lift_score(4, kein) > rating.lift_score(5, kein) == 0.0
    assert rating.lift_score(5, top) == 1.0
    assert rating.lift_score(4, {"balkon": False, "garten": True, "aufzug": False}) == 1.0
    # Größe: 60-70 m² ideal, Zimmer: 2 ideal
    # Größe: je mehr Platz desto besser (ab 70 m² volle Punkte), Zimmer: 2 besser als 1
    assert rating.size_score(90) == rating.size_score(70) == 1.0 > rating.size_score(45) > rating.size_score(35) == 0.0
    assert rating.rooms_score(2) > rating.rooms_score(1.5) > rating.rooms_score(1)
    # Bauart über das Baujahr: Altbau vor Neubau vor Plattenbau-Jahren
    assert rating.year_score(1910) > rating.year_score(2020) > rating.year_score(None) > rating.year_score(1978)
    assert rating.parse_year("Energiekennzahlen Baujahr: 1930 Heizungsart") == 1930 and rating.parse_year("nix") is None
    # Altbau in Prenzlauer Berg schlägt sonst gleiche Platte in Lichtenberg deutlich
    # Altbau im lebendigen Kiez schlägt sonst gleiche Platte in der Hochhaussiedlung deutlich
    altbau = rating.score(700, 58, 2, top, 5.0, 12, 1.0, 2, "Prenzlauer Berg", 1930, (400, 100), 5)
    platte = rating.score(700, 58, 2, top, 5.0, 12, 1.0, 2, "Lichtenberg", 1978, (20, 100), 11)
    assert altbau[1] - platte[1] >= 20 and altbau[0] > platte[0], (altbau[:2], platte[:2])
    # Kiez: lebendig vor mittel vor Wohnsiedlung, Park in der Nähe hilft; Hochhaus gibt Abzug
    assert rating.kiez_score(430, 40) == 1.0 > rating.kiez_score(96, 140) > rating.kiez_score(14, 9999) == 0.0
    assert rating.kiez_score(96, 140) > rating.kiez_score(96, 900)
    assert rating.highrise_score(5) > rating.highrise_score(8) > rating.highrise_score(11) > rating.highrise_score(20)
    # kiez.json: Kollwitzplatz ist lebendig, Gropiusstadt nicht; funktioniert ohne Netz
    koll, gropius = rating.kiez((52.5363, 13.4177)), rating.kiez((52.4250, 13.4630))
    assert koll[0] > 200 and koll[1] <= 300 and gropius[0] < 60, (koll, gropius)
    assert sum(rating.W.values()) == 100
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
                mock.patch("wbm_bot.rating.rate_listing", return_value=("★★★★☆ (4/5)", "")):
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


def test_wbs_from_expose_field():
    assert wbm_bot.parse_wbs_required("<p>Bezugsfertig ab: sofort WBS erforderlich: Ja Merkmale</p>") is True
    assert wbm_bot.parse_wbs_required("<dt>WBS erforderlich:</dt> <dd>Nein</dd>") is False
    assert wbm_bot.parse_wbs_required(FORM_HTML) is None
    cards = "<div class='list'>" + "".join([
        card("ohne", "2 Zimmer, kein WBS nötig", "800,00", "60", 2),      # Karte sagt WBS, Exposé nein
        card("mit", "Schöne 2 Zimmer", "800,00", "60", 2),                # Karte sagt nichts, Exposé ja
        card("offen", "2-Zimmer WBS 100", "480,00", "52", 2, "<li>WBS</li>"),  # Exposé ohne Feld
    ]) + "</div>"
    field = {"ohne": "<p>WBS erforderlich: Nein</p>", "mit": "<p>WBS erforderlich: Ja</p>", "offen": ""}
    env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
    sent = []

    def get(url, **kw):
        text = cards if url == wbm_bot.URL else field[url.split("/")[-2]] + FORM_HTML
        return mock.Mock(text=text, status_code=200)

    def post(url, **kw):
        sent.append(kw["headers"]["Referer"].split("/")[-2])
        return mock.Mock(status_code=200, text="Vielen Dank")

    with tempfile.TemporaryDirectory() as td:
        wbm_bot.SEEN_FILE = Path(td) / "seen.json"
        wbm_bot.STATS_FILE = Path(td) / "stats.json"
        with mock.patch.dict(os.environ, env), mock.patch("wbm_bot.HTTP.get", get), \
                mock.patch("wbm_bot.HTTP.post", post), mock.patch("wbm_bot.time.sleep"), \
                mock.patch("wbm_bot.rating.rate_listing", return_value=("★★★★☆ (4/5)", "")):
            wbm_bot.main()
        assert sent == ["ohne"], sent
        seen = [u.split("/")[-2] for u in json.loads(wbm_bot.SEEN_FILE.read_text())]
        assert seen == ["mit", "ohne"], seen  # "offen" bleibt offen, wird nicht angefragt


def test_empty_page_vs_broken_page():
    import requests
    frame = "<h1>Wohnungsangebote</h1><h2>{n} Mietwohnungen in Berlin</h2><div class='tx-openimmo search'>{body}</div>"
    one = card("e", "Schöne 2 Zimmer", "1.940,00", "60", 2)
    assert wbm_bot.check_page(frame.format(n=0, body=""), []) is None  # wirklich leer
    assert wbm_bot.check_page(frame.format(n=1, body=one), parse_listings(one)) is None
    assert "2 Angebote" in wbm_bot.check_page(frame.format(n=2, body=one), parse_listings(one))
    assert wbm_bot.check_page("<html>Wartungsarbeiten</html>", [])
    assert wbm_bot.check_page("<div class='tx-openimmo'>850,00 € Warmmiete</div>", [])  # Karten nicht erkannt

    def run(get, runs=1):
        msgs, codes = [], []
        with tempfile.TemporaryDirectory() as td:
            wbm_bot.SEEN_FILE = Path(td) / "seen.json"
            wbm_bot.STATS_FILE = Path(td) / "stats.json"
            with mock.patch("wbm_bot.HTTP.get", get), mock.patch("wbm_bot.notify", side_effect=msgs.append), \
                    mock.patch("wbm_bot.SUMMARY_HOUR", 25):
                for _ in range(runs):
                    try:
                        wbm_bot.main()
                        codes.append(0)
                    except SystemExit as e:
                        codes.append(e.code)
        return msgs, codes

    # leer: normaler Lauf, keine Meldung
    assert run(lambda url, **kw: mock.Mock(text=frame.format(n=0, body=""), status_code=200)) == ([], [0])
    # kaputt, Serverfehler, Netz weg: Fehler, aber nur eine Push-Meldung pro Tag
    for get in (lambda url, **kw: mock.Mock(text="<html>Wartung</html>", status_code=200),
                lambda url, **kw: mock.Mock(text="", status_code=503),
                mock.Mock(side_effect=requests.ConnectionError("weg"))):
        msgs, codes = run(get, runs=2)
        assert codes == [1, 1] and len(msgs) == 1 and "nicht sauber lesbar" in msgs[0], (msgs, codes)


def test_send_only_from_four_stars_but_report_all():
    cards = "<div class='list'>" + "".join(
        card(s, "Schöne 2 Zimmer", "800,00", "60", 2) for s in ("top", "mittel", "mau")) + "</div>"
    stars = {"top": 5, "mittel": 3, "mau": 1}
    env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
    sent, msgs = [], []

    def get(url, **kw):
        return mock.Mock(text=cards if url == wbm_bot.URL else FORM_HTML, status_code=200)

    def post(url, **kw):
        sent.append(kw["headers"]["Referer"].split("/")[-2])
        return mock.Mock(status_code=200, text="Vielen Dank")

    with tempfile.TemporaryDirectory() as td:
        wbm_bot.SEEN_FILE = Path(td) / "seen.json"
        wbm_bot.STATS_FILE = Path(td) / "stats.json"
        with mock.patch.dict(os.environ, env), mock.patch("wbm_bot.HTTP.get", get),                 mock.patch("wbm_bot.HTTP.post", post), mock.patch("wbm_bot.time.sleep"),                 mock.patch("wbm_bot.notify", side_effect=msgs.append), mock.patch("wbm_bot.SUMMARY_HOUR", 25),                 mock.patch("wbm_bot.rating.rate_listing",
                           lambda l, text="": ("★" * stars[l["url"].split("/")[-2]], "")):
            wbm_bot.main()
            wbm_bot.main()
    assert sent == ["top"], sent  # nur ab 4 Sternen anfragen
    assert len(msgs) == 3, msgs   # aber alle drei melden, je einmal
    assert "Anfrage: gesendet" in msgs[0] and all("Keine Anfrage (unter 4 Sternen)" in m for m in msgs[1:]), msgs


def test_over_limit_best_stars_first_rest_next_run():
    many = "<div class='list'>" + "".join(
        card(f"w{i}", "Schöne 2 Zimmer", "800,00", "60", 2) for i in range(5)) + "</div>"
    env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
    ok = mock.Mock(status_code=200, text="Vielen Dank")
    stars = {"w0": 4, "w1": 5, "w2": 4, "w3": 5, "w4": 4}
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
            assert sent == ["w1", "w3", "w0"], sent  # die besten drei zuerst
            wbm_bot.main()
            assert sent == ["w1", "w3", "w0", "w2", "w4"], sent  # Rest beim nächsten Lauf
            wbm_bot.main()
            assert len(sent) == 5, sent  # danach nichts mehr, nie doppelt


def ibw_card(id, company="degewo", rooms="2,0", net="600,00", total="750,00 €", wbs="nicht erforderlich",
             bezirk="Pankow", plz="10439", lat="52.5480", lon="13.4190"):
    """Nachbau einer Karte von inberlinwohnen.de (Livewire-Format wie auf der echten Seite)."""
    arr = {"s": "arr"}
    item = {
        "id": id, "title": "Schöne Wohnung", "deeplink": f"https://example.org/{id}", "rooms": rooms,
        "area": "58,25", "level": 2, "rentNet": net, "rentGross": 700.0,
        "company": [{"intern": company, "name": company}, arr],
        "address": [{"street": "Teststraße", "number": "9", "zipCode": plz, "district": bezirk,
                     "lat": lat, "lon": lon}, arr],
        "details": [[[[{"label": "Gesamtmiete", "value": total}, arr], [{"label": "WBS", "value": wbs}, arr]], arr], arr],
    }
    snap = json.dumps({"data": {"item": [item, arr]}}).replace("'", "&#39;")
    return f"<div id='apartment-{id}' wire:snapshot='{snap}'>Gesamtmiete: {total} Balkon / Loggia / Terrasse</div>"


def test_ibw_parse_and_filter():
    import ibw_bot
    html = "".join([
        ibw_card(1),
        ibw_card(2, wbs="erforderlich"),
        ibw_card(3, company="wbm"),                  # macht der WBM-Bot
        ibw_card(4, bezirk="Spandau"),
        ibw_card(5, rooms="3,0"),
        ibw_card(6, total="1.074,60 €"),
        ibw_card(7, net="910,00", total="940,00 €"),  # zu teuer kalt
        ibw_card(8, bezirk="Lichtenberg", plz="10319"),
    ])
    ls = ibw_bot.parse_page(html)
    assert [l["id"] for l in ls] == [1, 2, 3, 4, 5, 6, 7, 8]
    a = ls[0]
    assert (a["rooms"], a["price"], a["cold"], a["size"], a["wbs"], a["floor"]) == (2, 750.0, 600.0, 58.25, False, 2), a
    assert a["coord"] == (52.548, 13.419) and a["company"] == "degewo" and a["url"] == "https://example.org/1"
    assert ls[5]["price"] == 1074.60 and ls[1]["wbs"] is True
    # falsche Koordinaten von der Seite (weit außerhalb Berlins) zählen als unbekannt
    wrong = ibw_bot.parse_page(ibw_card(9, lat="41.0", lon="-12.5"))[0]
    assert wrong["coord"] is None and "km zur Arbeit" not in ibw_bot.rate(wrong)[2]
    assert [l["id"] for l in ls if ibw_bot.matches(l)] == [1, 8]
    # WBS, das inberlinwohnen nicht meldet: im Titel oder als Bild-Etikett bei der Gesellschaft
    assert ibw_bot.title_says_wbs("Ein WBS bis zu 220 % ist erforderlich!") and ibw_bot.title_says_wbs("mit WBS-Berechtigung 160-220")
    assert not ibw_bot.title_says_wbs("2-Zimmer-Wohnung (ohne WBS)") and not ibw_bot.title_says_wbs("Schöne Wohnung")
    assert not ibw_bot.matches({**a, "title": "Familienwohnung, WBS 160 erforderlich"})
    assert not ibw_bot.matches({**a, "title": "Seniorenresidenz, erst ab 60 Jahren"})
    assert ibw_bot.page_says_wbs("<figure><img><figcaption class='c'>\n 220er\n</figcaption></figure>")
    assert not ibw_bot.page_says_wbs("<figcaption>Wohnzimmer</figcaption><p>Nalepastraße 220er Block</p>")
    assert a["year"] is None and ibw_bot.parse_item({"id": 1, "constructionYear": "1930"}, "")["year"] == 1930
    n, stars, why = ibw_bot.rate(a)  # Prenzlauer Berg, nah an der Ringbahn, Balkon
    assert n >= 4 and "Ringbahn nah" in why and "Balkon" in why, (stars, why)
    assert "Teststraße 9, 10439 Pankow" in ibw_bot.describe(a, stars, why)


def test_ibw_main_first_run_then_only_new():
    import ibw_bot
    pages = {1: ibw_card(12) + ibw_card(11), 2: ibw_card(10, bezirk="Spandau"), 3: ""}
    calls = []

    def get(url, params=None, **kw):
        if params is None:  # Seite der Gesellschaft: Angebot 11 hat dort ein WBS-Etikett
            return mock.Mock(text="<figcaption>220er</figcaption>" if url.endswith("/11") else "", status_code=200)
        calls.append(params["page"])
        return mock.Mock(text=pages.get(params["page"], ""), status_code=200)

    msgs = []
    with tempfile.TemporaryDirectory() as td:
        ibw_bot.SEEN_FILE = Path(td) / "seen_ibw.json"
        wbm_bot.STATS_FILE = Path(td) / "stats.json"
        with mock.patch("wbm_bot.HTTP.get", get), mock.patch("wbm_bot.notify", side_effect=msgs.append), \
                mock.patch("ibw_bot.time.sleep"):
            ibw_bot.main()  # erster Lauf: alle Seiten, eine Sammelmeldung
            assert calls == [1, 2, 3] and len(msgs) == 1 and "1 passende Angebote" in msgs[0], (calls, msgs)
            assert json.loads(ibw_bot.SEEN_FILE.read_text()) == [10, 11, 12]
            ibw_bot.main()  # nichts Neues: nur Seite 1, keine Meldung
            assert calls == [1, 2, 3, 1] and len(msgs) == 1
            pages[1] = ibw_card(13) + ibw_card(12)
            ibw_bot.main()  # ein neues Angebot: einzeln melden, bis zur ersten Seite ohne Neues
            assert len(msgs) == 2 and "example.org/13" in msgs[1], msgs
            ibw_bot.main()
            assert len(msgs) == 2  # nie doppelt
            # Seite kaputt: Fehler und genau eine Störungsmeldung
            pages[1] = "<html>Wartung</html>"
            for _ in range(2):
                try:
                    ibw_bot.main()
                    assert False, "sollte abbrechen"
                except SystemExit as e:
                    assert e.code == 1
            assert len(msgs) == 3 and "nicht sauber lesbar" in msgs[2], msgs


def test_offers_list_for_app():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)
    hours = lambda h: offers.iso(now - timedelta(hours=h))
    store = {"offers": {}}
    offers.add(store, "neu", {"title": "Neu", "online_since": hours(1)}, now)
    offers.add(store, "alt", {"title": "Alt", "online_since": hours(25)}, now)
    offers.add(store, "alt-angefragt", {"title": "Alt, angefragt", "online_since": hours(25), "requested": "gesendet"}, now)
    offers.add(store, "ohne-zeit", {"title": "Ohne Zeit", "online_since": None}, now)
    # spätere Aktualisierung ohne Status löscht die Anfrage nicht und verschiebt "online seit" nicht
    offers.add(store, "alt-angefragt", {"title": "Alt, angefragt", "stars": 4, "requested": None,
                                        "online_since": hours(0)}, now)
    old_file = offers.FILE
    with tempfile.TemporaryDirectory() as td:
        offers.FILE = Path(td) / "docs" / "offers.json"
        try:
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}):
                offers.save(store, now)
            later = now + timedelta(minutes=10)
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}):
                offers.save(offers.load(), later)
            loaded = offers.load()
            # automatischer und händischer Lauf werden getrennt gemerkt
            assert loaded["last_auto"] == offers.iso(now) and loaded["last_manual"] == offers.iso(later), loaded
            offers.save(store, now)
            loaded = offers.load()
        finally:
            offers.FILE = old_file
    assert sorted(loaded["offers"]) == ["alt-angefragt", "neu", "ohne-zeit"], sorted(loaded["offers"])
    a = loaded["offers"]["alt-angefragt"]
    assert a["requested"] == "gesendet" and a["stars"] == 4 and a["online_since"] == hours(25) and a["requested_at"]
    assert loaded["offers"]["ohne-zeit"]["online_since"] == offers.iso(now) and loaded["updated"] == offers.iso(now)


def test_bots_fill_app_list():
    import ibw_bot
    expose = """<h1>Schöne 2 Zimmer in Mitte</h1>
      <div class="openimmo-detail__intro-wrapper-col-1"><h2>Wohnungsdetails</h2>
      <p>Teststrasse 9, 10115 Berlin</p><p>Helle Wohnung mit Balkon.</p><p>Zweiter Absatz.</p></div>
      <img src="/_assets/logo.svg"><img src="/fileadmin/user_upload/tx_openimmo/c/images/1-a.jpg">
      <img src="/fileadmin/user_upload/tx_openimmo/c/images/1-a.jpg"><img data-src="/fileadmin/user_upload/tx_openimmo/c/images/1-b.jpg">
      <p>Etage: 3 Baujahr: 1910</p>""" + FORM_HTML
    d = wbm_bot.expose_details(expose, "https://www.wbm.de/x/")
    assert d["title"] == "Schöne 2 Zimmer in Mitte" and d["address"] == "Teststrasse 9, 10115 Berlin", d
    assert d["text"] == "Helle Wohnung mit Balkon.\nZweiter Absatz." and len(d["images"]) == 2, d
    assert d["images"][0] == "https://www.wbm.de/fileadmin/user_upload/tx_openimmo/c/images/1-a.jpg"

    cards = "<div class='list'>" + card("top", "Schöne 2 Zimmer", "800,00", "60", 2) \
        + card("mau", "Schöne 2 Zimmer", "800,00", "60", 2) + "</div>"
    stars = {"top": 5, "mau": 2}
    env = {"WBM_SEND": "1", "WBM_NAME": "M", "WBM_VORNAME": "M", "WBM_EMAIL": "m@x.de"}
    pages = {1: ibw_card(21) + ibw_card(22, wbs="erforderlich"), 2: ""}

    def get(url, params=None, **kw):
        if url == ibw_bot.URL:
            return mock.Mock(text=pages.get(params["page"], ""), status_code=200)
        return mock.Mock(text=cards if url == wbm_bot.URL else expose, status_code=200)

    old_file = offers.FILE
    with tempfile.TemporaryDirectory() as td:
        offers.FILE = Path(td) / "docs" / "offers.json"
        wbm_bot.SEEN_FILE, wbm_bot.STATS_FILE = Path(td) / "seen.json", Path(td) / "stats.json"
        ibw_bot.SEEN_FILE = Path(td) / "seen_ibw.json"
        try:
            with mock.patch.dict(os.environ, env), mock.patch("wbm_bot.HTTP.get", get), \
                    mock.patch("wbm_bot.HTTP.post", return_value=mock.Mock(status_code=200, text="Vielen Dank")), \
                    mock.patch("wbm_bot.time.sleep"), mock.patch("ibw_bot.time.sleep"), mock.patch("wbm_bot.notify"), \
                    mock.patch("wbm_bot.rating.rate_listing",
                               lambda l, text="": ("★" * stars[l["url"].split("/")[-2]], "gut")):
                wbm_bot.main()
                ibw_bot.main()
                ibw_bot.main()
                o = offers.load()
                # "mau" verschwindet von der WBM-Seite: bleibt in der App, aber als nicht mehr online
                cards = "<div class='list'>" + card("top", "Schöne 2 Zimmer", "800,00", "60", 2) + "</div>"
                wbm_bot.main()
                later = offers.load()["offers"]
                assert [k.split("/")[-2] for k, x in later.items() if x.get("gone")] == ["mau"], later
        finally:
            offers.FILE = old_file
    top, mau = (o["offers"][f"https://www.wbm.de/wohnungen-berlin/angebote/details/{k}/"] for k in ("top", "mau"))
    assert top["requested"] == "gesendet" and top["stars"] == 5 and top["floor"] == 3 and top["year"] == 1910, top
    assert mau.get("requested") is None and mau["stars"] == 2 and mau["bezirk"] == "Mitte" and mau["price"] == 800.0, mau
    assert top["title"] == "Schöne 2 Zimmer in Mitte" and len(top["images"]) == 2 and top["online_since"]
    assert not top.get("gone")
    i = o["offers"]["ibw-21"]
    assert "ibw-22" not in o["offers"] and o["ibw_seeded"] is True, sorted(o["offers"])  # 22 braucht WBS
    assert i["company"] == "degewo" and i["stars"] >= 1 and i["parts"]["Kiez"][1] == 16 and i["url"] == "https://example.org/21", i
    assert i["address"] == "Teststraße 9, 10439 Berlin" and i["images"] == []


def test_mail_replies():
    import mail_check
    from datetime import datetime, timezone
    from email.message import EmailMessage
    now = datetime(2026, 10, 13, 18, 30, tzinfo=timezone.utc)

    def mail(mid, subject, body, html=False):
        m = EmailMessage()
        m["Message-ID"], m["From"], m["Subject"] = mid, "Vermietung <info@wbm.de>", subject
        m.set_content(body, subtype="html" if html else "plain")
        return m

    absage = mail("<1@wbm.de>", "Ihre Anfrage", """Guten Tag Max Mustermann,
wir danken Ihnen für Ihr Interesse an unserer Wohnung
2.0 Zimmer, 56.78 m², Etage: 4, 731.42 € Gesamtmiete Kleine Andreasstraße 11, 10243 Berlin.
Aufgrund des hohen Anfrageaufkommens ist es derzeit nicht möglich, alle Anfragen für eine Besichtigung
zu berücksichtigen. Die Auswahl der möglichen Teilnehmer erfolgte auf Basis des zeitlichen Anfrageneingangs.""")
    einladung = mail("<2@wbm.de>", "Besichtigungstermin", "<p>Guten Tag,</p><p>wir laden Sie zur Besichtigung der "
                     "Wohnung Teststr. 5, 10115 Berlin ein. Termin am 20.10.2026 um 15 Uhr.</p>", html=True)
    sonstiges = mail("<3@wbm.de>", "Newsletter", "Neues aus dem Quartier.")
    assert [mail_check.classify(mail_check.body_text(m)) for m in (absage, einladung, sonstiges)] == \
        ["absage", "einladung", "antwort"]
    assert mail_check.street_of("Kleine Andreasstrasse 11, 10243 Berlin") == mail_check.norm("Kleine Andreasstraße 11") \
        == mail_check.norm("Kleine Andreasstr. 11") == mail_check.norm("Kleine AndreasstraÃ\x9fe 11") \
        == mail_check.norm("Kleine\nAndreasstraÃŸe\xa011")
    assert mail_check.norm("Köpenicker Straße 3a") == mail_check.norm("KÃ¶penicker Strasse 3A")
    # Inhalt nur im HTML-Teil, Nur-Text-Teil ist bloß ein Hinweis
    both = EmailMessage()
    both["Message-ID"], both["From"], both["Subject"] = "<9@wbm.de>", "info@wbm.de", "Ihre Anfrage"
    both.set_content("Bitte HTML-Ansicht verwenden.")
    both.add_alternative("<p>Wohnung <b>Kleine Andreasstra&szlig;e&nbsp;11</b>, leider vergeben.</p>", subtype="html")
    probe = {"offers": {"a": {"address": "Kleine Andreasstrasse 11, 10243 Berlin", "requested": "gesendet"}}}
    assert mail_check.process([both], probe, set(), now) == [] and probe["offers"]["a"]["reply"] == "absage"
    assert probe["mail_log"][-1]["matched"] is True and probe["mail_log"][-1]["kind"] == "absage"

    store = {"offers": {
        "a": {"address": "Kleine Andreasstrasse 11, 10243 Berlin", "requested": "gesendet"},
        "b": {"address": "Teststraße 5, 10115 Berlin", "requested": "unklar"},
        "c": {"address": "Kleine Andreasstrasse 1, 10243 Berlin", "requested": "gesendet"},  # andere Hausnummer
        "d": {"address": "Teststraße 5, 10115 Berlin"},                                      # nie angefragt
    }}
    seen = set()
    notes = mail_check.process([absage, einladung, sonstiges], store, seen, now)
    o = store["offers"]
    assert o["a"]["reply"] == "absage" and o["b"]["reply"] == "einladung" and o["a"]["reply_at"]
    assert "reply" not in o["c"] and "reply" not in o["d"]
    assert len(notes) == 2 and notes[0].startswith("EINLADUNG") and "Teststraße 5" in notes[0], notes
    assert "nicht zugeordnet" in notes[1]
    assert not any("Mustermann" in n or "Quartier" in n for n in notes)  # nie Mail-Inhalt weitergeben
    assert len(seen) == 3 and all(len(x) == 16 for x in seen)
    assert mail_check.process([absage, einladung, sonstiges], store, seen, now) == []  # jede Mail nur einmal

    # ohne Zugangsdaten passiert nichts, bei Login-Fehler läuft der Bot weiter und meldet einmal
    msgs = []
    with tempfile.TemporaryDirectory() as td:
        wbm_bot.STATS_FILE, mail_check.SEEN_FILE = Path(td) / "stats.json", Path(td) / "mail_seen.json"
        with mock.patch.dict(os.environ, {"MAIL_USER": "", "MAIL_APP_PASSWORD": ""}), \
                mock.patch("mail_check.fetch_messages") as fetch:
            mail_check.main()
            fetch.assert_not_called()
        with mock.patch.dict(os.environ, {"MAIL_USER": "x@gmail.com", "MAIL_APP_PASSWORD": "abcd efgh"}), \
                mock.patch("wbm_bot.notify", side_effect=msgs.append):
            with mock.patch("mail_check.fetch_messages", side_effect=OSError("login")) as fetch:
                mail_check.main()
                mail_check.main()
                assert fetch.call_args[0][1] == "abcdefgh"  # Leerzeichen aus dem App-Passwort entfernt
            assert len(msgs) == 1 and "Mail-Abruf" in msgs[0], msgs
            with mock.patch("mail_check.fetch_messages", return_value=[sonstiges]):
                mail_check.main()
                mail_check.main()
            assert len(msgs) == 2 and len(json.loads(mail_check.SEEN_FILE.read_text())) == 1, msgs


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
