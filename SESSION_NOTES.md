# Session Notes: WBM-Wohnungsbot

Stand: 13.10.2026. Zum Weiterarbeiten in Claude Code. Enthält bewusst keine persönlichen Daten.

## Ziel
Bot für die Wohnungssuche bei der WBM (Wohnungsbaugesellschaft Berlin-Mitte): neue Angebote erkennen,
nach Kriterien filtern, bei Treffern automatisch eine Besichtigungsanfrage senden und eine
Push-Meldung mit Sternebewertung und Link schicken. Einmal täglich ein Tagesbericht.
Später evtl. weitere Gesellschaften als eigene Module (Gewobag, Gesobau, degewo, Howoge, Stadt und Land).

## Aufbau
- `wbm_bot.py`: Hauptskript. Angebote von `https://www.wbm.de/wohnungen-berlin/angebote/` parsen,
  filtern, Anfrage senden, melden, Statistik.
- `rating.py`: Sternebewertung (1-5) mit Kurzbegründung. Alle Gewichte und Wünsche stehen als Konstanten oben.
- `ibw_bot.py`: zweiter Bot für `https://www.inberlinwohnen.de/wohnungsfinder/` (alle landeseigenen
  Gesellschaften plus Berlinovo, ca. 450 Angebote). Nur finden und melden, keine Anfrage. Siehe unten.
- `offers.py` + `docs/`: Handy-Ansicht. Beide Bots tragen ihre Treffer in `docs/offers.json` ein,
  `docs/index.html` zeigt sie an (GitHub Pages, Quelle: Branch main, Ordner /docs). Siehe unten.
- `mail_check.py`: liest die Antworten der Gesellschaften aus dem Postfach (nur lesend, IMAP) und trägt
  Absage/Einladung in die Handy-Ansicht ein; bei Einladung Push. Merkt sich Mails als Fingerabdruck in `mail_seen.json`.
- `howoge.py`: automatische Besichtigungsanfrage bei der HOWOGE (aufgerufen von `ibw_bot.py`). Siehe unten.
- `watch.py`: Seiten-Wächter. Prüft alle 30 Minuten den Text der WGLi-Wohnungssuche (Wohnungsgenossenschaft
  Lichtenberg, hat keine Angebote online) und meldet per Push, wenn er sich ändert. Zustand in `watch.json`.
- `test_bot.py`: Tests mit Mocks (`python3 test_bot.py`), kein Netzwerk nötig.
- `.github/workflows/wbm.yml`: GitHub Actions, Cron alle 10 Min (`*/10 * * * *`), manuell startbar.
  Führt erst `wbm_bot.py`, dann `ibw_bot.py` aus. Committet `seen.json`, `seen_ibw.json` und `stats.json` zurück ins Repo.
- `seen.json`: bereits gemeldete/angefragte Angebote (verhindert Doppelanfragen).
- Seit 28.10.2026: Checkout immer von `main` (wartende Läufe holten sonst einen alten Stand und scheiterten beim
  Speichern an Konflikten in `offers.json`); Speichern mit `pull --rebase -X theirs` und bis zu 3 Versuchen.
  `mail_check.py` öffnet das Postfach nur alle 10 Minuten (`mail_checked_at` in `stats.json`), außer eine
  HOWOGE-Bestätigung ist offen; IMAP-Timeout 30 s. Bestätigungslinks werden auch bei Handanfragen geöffnet.
- `stats.json`: Tageszähler (gescannte Angebote, gesendete Anfragen, Tag des letzten Berichts).

## Konfiguration (GitHub, nicht im Code)
- Secret `WBM_PERSON_JSON`: JSON mit anrede, name, vorname, strasse, plz, ort, email, telefon.
- Secrets `MAIL_USER` (Gmail-Adresse) und `MAIL_APP_PASSWORD` (Google-App-Passwort): für `mail_check.py`.
  Fehlen sie, wird der Mail-Abruf übersprungen. Der echte Abruf ist nur mit Mocks getestet.
- Externer Anstoß: cron-job.org ruft alle 2 Minuten `workflow_dispatch` auf (Token des Nutzers, läuft am 11.01.2027 ab).
- Secret `NTFY_TOPIC`: geheimer Topic für Push über ntfy.sh. Alle Meldungen mit normaler Priorität
  (bis 16.10.2026 kamen 4-Sterne-Treffer als "high", das vibrierte dem Nutzer zu stark).
- Variable `WBM_SEND`: `1` = echt senden (WBM und HOWOGE), sonst Trockenlauf.
- Repo ist öffentlich (damit Actions-Minuten reichen), deshalb keine personenbezogenen Daten im Code.

## Funktionsweise
1. Angebotsseite laden, Karten parsen (Container um jeden Link `/angebote/details/`): Preis (Warmmiete),
   Zimmer, WBS-Flag, Bezirk (steht am Textanfang der Karte).
2. Benachrichtigen (`matches`): Bezirk nicht ausgeschlossen, Zimmer/Preis passen, kein WBS. Bei unklaren Daten lieber melden.
   WBS und Kaltmiete kommen aus den Feldern im Exposé ("WBS erforderlich: Ja/Nein", "Nettokaltmiete"),
   das Wort "WBS" auf der Karte ist nur ein Hinweis.
3. Automatisch anfragen (`matches_strict`): nur wenn Bezirk sicher passt und alle Daten bekannt sind.
   Max. 3 Anfragen pro Lauf: bei mehr Treffern die mit den meisten Sternen zuerst, der Rest beim nächsten Lauf.
   Nie zweimal dasselbe Angebot: `seen.json` wird schon VOR dem Senden gespeichert, und der Workflow
   committet sie auch nach Absturz/Timeout.
4. Formular: Powermail (TYPO3) im Exposé, POST multipart an `/wohnungen-berlin/angebote/details/`.
   Versteckte Felder (`__trustedProperties`, `objekt`, `mail[form]`) werden frisch von der Seite gelesen.
   WBS=nein (`wbsvorhanden`=0), Datenschutz-Häkchen `datenschutzhinweis[]`=1, Honeypot `__hp` leer lassen.
   Kein Login, kein Upload, kein Captcha beobachtet.
   Gesendet wird wie im Browser: eine Sitzung (Cookie `fe_typo_user`), Referer, multipart.
5. Erfolgserkennung per Text ("Vielen Dank", "erfolgreich", ...). Sonst Status "unklar" (zählt trotzdem als gesendet, wird im Tagesbericht markiert).
6. Bewertung läuft NACH dem Senden (Geschwindigkeit zuerst) und darf den Bot nie stoppen (try/except).
7. Tagesbericht ab 20:00 Berliner Zeit, einmal pro Tag.
8. Störung (`check_page`): Die Seite nennt die Anzahl selbst ("8 Mietwohnungen in Berlin"). Weicht die
   gelesene Anzahl ab oder ist die Seite nicht lesbar, gibt es höchstens eine Push-Meldung pro Tag.
   Eine wirklich leere Angebotsliste ist kein Fehler.

## inberlinwohnen-Bot (`ibw_bot.py`)
- Jede Karte enthält ihre Daten als JSON (`wire:snapshot`, Livewire): Zimmer, Fläche, Kaltmiete, Etage,
  Gesellschaft, Link zur Gesellschaft, Adresse mit Bezirk und Koordinaten. Gesamtmiete und WBS stehen in `details`.
- 10 Angebote pro Seite, neueste zuerst, weitere Seiten per `?page=N`. Der Bot blättert, bis eine Seite nichts
  Neues enthält. Erster Lauf (ohne `seen_ibw.json`): alle Seiten (ca. 1 Minute), eine Sammelmeldung mit den besten 10.
- Filter wie beim WBM-Bot (Konstanten aus `wbm_bot.py`), aber nur Bezirk statt Ortsteil bekannt (`ALLOWED_BEZIRKE`).
  Deshalb zusätzlich: nur melden ab `MIN_STARS` = 3 Sternen. WBM-Angebote werden übersprungen (macht `wbm_bot.py`).
- Bewertung ohne Netz (Koordinaten kommen von der Seite). Prenzlauer Berg wird über die PLZ erkannt.
- WBS-Angabe der Seite ist unzuverlässig: zusätzlich Titel prüfen und bei Treffern die Seite der Gesellschaft
  (degewo kennzeichnet WBS-Wohnungen nur mit einem Bild-Etikett wie "220er"). Der Nutzer liegt knapp über WBS 220.
- Automatische Anfrage je Gesellschaft (`SENDERS` in `ibw_bot.py`), Stand 13.10.2026:
  - HOWOGE: gebaut (`howoge.py`). Formular fragt nur Vorname, Nachname, E-Mail; davor drei Hinweise zum Abhaken
    (WBS, Haushaltsnettoeinkommen mind. 3x Gesamtmiete, Bonitätsauskunft). Nach dem Absenden kommt eine Mail mit
    Bestätigungslink (`.../bewerbungsprozess/doi.html`), den `mail_check.py` öffnet; erst dann zählt die Anfrage.
    Scharf mit demselben Schalter wie WBM (`WBM_SEND=1`, seit 16.10.2026), sonst Trockenlauf. Optional begrenzt
    `MAX_WARM_SEND` die Gesamtmiete (= Netto / 3, weil die HOWOGE das Dreifache der Miete als Einkommen verlangt);
    leer = keine Grenze. Dieselbe Variable begrenzt auch die WBM-Anfragen; gemeldet und angezeigt wird weiter alles bis `MAX_WARMMIETE`. Gleiche Regeln wie WBM: ab 4 Sternen,
    Bezirk sicher passend, kein WBS, max. 3 pro Lauf, nie doppelt. Echtes Senden und die Bestätigungsmail sind
    noch nicht live geprüft (Trockenlauf gegen die echte Seite klappt).
    Erste echte Anfrage am 15.10.2026 (nach 12 Sekunden raus). Die Bestätigungsmail kam von
    `noreply@mailings.howoge.de`; der Link steht nur im Nur-Text-Teil und mit `http://`, im HTML nur ein
    Klickzähler (`r.sib.howoge.de`). Der Bot hatte ihn deshalb nicht erkannt (behoben 21.10.2026).
    Der Link gilt nur 24 Stunden; ältere offene Bestätigungen werden als `abgelaufen` markiert.
  - degewo: Formular hat ein Captcha (Friendly Captcha). Wird bewusst nicht umgangen, dort nur melden.
  - Gewobag, Stadt und Land: Formular von Wohnungshelden mit reCAPTCHA, ebenfalls nur melden.
  - Gesobau, Berlinovo: noch nicht angesehen.

## Handy-Ansicht (`docs/index.html`)
- Adresse: https://joshuaperlbach-dot.github.io/Wohnungs-such-Bot/ (öffentlich, aber `noindex`; enthält nur
  öffentliche Angebote und ob angefragt wurde, keine persönlichen Daten).
- Liste aller Treffer mit Sternen, Klick öffnet Bilder, Eckdaten, Sterne-Aufschlüsselung und Beschreibung.
- Button: grün = Anfrage raus (vom Bot oder von Hand markiert), grau = nicht angefragt,
  rötlich = seit über 2 Stunden online und nicht angefragt.
- Von Hand markieren speichert nur im Browser des Geräts (`localStorage`); die Seite kann nichts ins Repo schreiben.
- Nicht angefragte Angebote fliegen nach 24 Stunden raus, angefragte nach 30 Tagen (`offers.py`).
- `offers.json` wird bei jedem Lauf neu geschrieben (Feld `updated`); die Seite warnt, wenn der Bot über 45 Minuten
  nicht gelaufen ist.

## Ausfüll-Hilfe für Formulare mit Captcha (`docs/ausfuellen.user.js`)
- Userscript für Tampermonkey (Firefox auf Android). Läuft auf den Seiten von degewo, Gewobag, Stadt und Land,
  Wohnungshelden (eingebettete Formulare), Gesobau, Berlinovo, HOWOGE, WBM.
- Füllt leere Felder anhand ihrer Beschriftung aus (Anrede, Name, E-Mail, Telefon, Adresse, Personen) und hakt
  nur Datenschutz-Häkchen an. Schickt nie selbst ab; Captcha und Absenden macht der Nutzer.
- Persönliche Daten stehen nur im Tampermonkey-Speicher auf dem Handy, nicht im Repo. Langes Drücken auf den
  grünen Knopf ändert sie.
- Feldnamen der echten Formulare waren beim Bau nicht einsehbar (kein Netz), daher Erkennung per Text.
  Getestet nur gegen Testformulare. Captchas werden bewusst nicht automatisch gelöst.
- v1.1 (23.10.2026) nach erstem Einsatz bei Stadt und Land (Formular in eingebettetem Frame): versteckte Felder
  (Ausklapp-Listen) nie anfassen, Anrede nur in echte Auswahllisten, jedes Feld nur einmal, Adresszusatz und
  Kinderzahl als eigene Angaben, Freitext/Einkommen/Einzugsdatum werden nie ausgefüllt.

## Kriterien und Bewertung
- Filter: 1-2 Zimmer, max. 950 € Warmmiete, ohne WBS.
  Bezirke: Charlottenburg, Friedrichshain, Kreuzberg, Lichtenberg, Mitte, Neukölln, Pankow, Prenzlauer Berg, Tiergarten, Treptow, Wedding.
- Preis: bis 750 € volle Punkte, 850 € Wunschgrenze, 950 € Maximum (nur für ansonsten perfekte Wohnungen).
  Über 850 € höchstens 4 Sterne, außer Punkte >= 88.
- Aufzug nur ab 3. Stock wichtig (je höher, desto mehr), Garten ersetzt Aufzug.
- Traumwohnung: 60-70 m², zwei Zimmer (ein kleines, ein großes), Küche, Bad, Balkon, nahe Ringbahn, am liebsten Prenzlauer Berg.
- Arbeitsweg: Luftlinie zum Moritzplatz (keine echte Fahrzeit).
- Gewichte (Summe 100): Kosten 20, Kiez 16, Lage 10, Ringbahn 6, ÖPNV 3, Bezirk 3, Baujahr 9, Hochhaus 5,
  Größe 10, Zimmer 7, Außenbereich 6, Aufzug 5.
- Kiez-Wert (Nutzerwunsch 13.10.2026: "Lage zählt mehr als Bezirk, lebendige Ecke statt Wohnsiedlung,
  Hochhaussiedlung am schlimmsten"): Lokale und Läden im 500-m-Umkreis plus Park in der Nähe, aus `kiez.json`.
  Die Datei baut `build_kiez.py` einmalig aus der Berlin-Karte von OpenStreetMap (Geofabrik), der Bot braucht
  dafür kein Netz. Overpass war am 13.10.2026 durchgehend nicht erreichbar (504) und wird nicht mehr benutzt.
  Richtwerte: Kollwitzplatz ~430, Boxhagener Platz ~580, Wedding ~100, Gropiusstadt ~15.
- Hochhaus: Etagenzahl des Hauses (nur bei inberlinwohnen bekannt), ab 9 Etagen deutlicher Abzug.
- Nutzerwunsch (13.10.2026): Altbau in Prenzlauer Berg schlägt Plattenbau in Lichtenberg, 2 Zimmer besser als 1,
  je mehr Platz desto besser. Bauart wird über das Baujahr geschätzt (bis 1949 top, 1960-1990 schwach).
- Automatische Anfrage (WBM) erst ab 4 Sternen (`MIN_STARS_SEND`), gemeldet wird jeder Treffer.
- Sterne ab Punkten: 5 >= 85, 4 >= 70, 3 >= 55, 2 >= 40.
- Bewusst NICHT bewertet: Lärm (unwichtig, "laut sein können" lässt sich vorher nicht prüfen).
- Kaltmiete: Obergrenze 900 € kalt (`MAX_KALTMIETE`), aus dem Exposé gelesen; unbekannt = kein Ausschluss.
- Ringbahn: feste Liste der 27 Halte in `rating.py`, braucht kein Netz.

## Bekannte Schwächen / ungetestet
- Echtes Senden funktioniert: Am 13.10.2026 ging die erste Anfrage raus (Kleine Andreasstraße 11, Status "gesendet"),
  WBM hat per Mail geantwortet. Es war eine Absage mit dem Satz "Die Auswahl der möglichen Teilnehmer erfolgte auf
  Basis des zeitlichen Anfrageneingangs": WBM vergibt Besichtigungen also nach Reihenfolge, nicht per Los.
  Das Angebot war rund 20 Minuten nach dem Fund schon wieder offline. Geschwindigkeit entscheidet, der Bot muss
  im Minutentakt laufen (externer Anstoß, siehe Zeitplan unten).
- Adresse wird aus dem Kartentext geraten (Straße + Hausnummer, Fallback nur PLZ).
- Etage "DG" (Dachgeschoss) hat keine Zahl und zählt als unbekannt.
- Bezirk wird per Textanfang erkannt, unbekannte Labels werden nur gemeldet, nicht angefragt.
- Der Zeitplan (Cron) ist am 13.10.2026 in den ersten 3 Stunden kein einziges Mal von selbst gelaufen, nur manuelle
  Starts. Cron steht jetzt auf `3-59/10` (weg von der vollen Stunde). Falls das nicht reicht: externer Anstoß über
  `workflow_dispatch` (z.B. cron-job.org mit Token).
- GitHub kann Cron-Läufe verzögern; geplante Workflows werden nach 60 Tagen ohne Repo-Aktivität deaktiviert (die täglichen Commits von `stats.json` halten es aktiv).
- Seitenaufbau von wbm.de kann sich ändern: Bot bricht mit Fehler ab, wenn keine Angebote gefunden werden.

## Ideen / nächste Schritte
- Gewichte nach Feedback des Nutzers nachjustieren (welche Wohnungen gut/schlecht waren).
- Energieklasse und Heizkosten aus dem Exposé in die Kosten einrechnen.
- Automatische Anfrage für weitere Gesellschaften (gefunden werden sie schon über `ibw_bot.py`):
  Howoge und degewo zuerst (die meisten Angebote), dann Gewobag, Stadt und Land, Gesobau (über Immomio).
- Optional: Anfrage-Button in der Push-Meldung (ntfy-Aktion + GitHub-Token), statt vollautomatisch.
- Echte Fahrzeit zur Arbeit statt Luftlinie.
