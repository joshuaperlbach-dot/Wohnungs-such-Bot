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
- `test_bot.py`: Tests mit Mocks (`python3 test_bot.py`), kein Netzwerk nötig.
- `.github/workflows/wbm.yml`: GitHub Actions, Cron alle 10 Min (`*/10 * * * *`), manuell startbar.
  Führt erst `wbm_bot.py`, dann `ibw_bot.py` aus. Committet `seen.json`, `seen_ibw.json` und `stats.json` zurück ins Repo.
- `seen.json`: bereits gemeldete/angefragte Angebote (verhindert Doppelanfragen).
- `stats.json`: Tageszähler (gescannte Angebote, gesendete Anfragen, Tag des letzten Berichts).

## Konfiguration (GitHub, nicht im Code)
- Secret `WBM_PERSON_JSON`: JSON mit anrede, name, vorname, strasse, plz, ort, email, telefon.
- Secret `NTFY_TOPIC`: geheimer Topic für Push über ntfy.sh.
- Variable `WBM_SEND`: `1` = echt senden, sonst Trockenlauf.
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
- Nächster Ausbau: automatische Anfrage je Gesellschaft (jede hat ein eigenes Formular/Portal).

## Kriterien und Bewertung
- Filter: 1-2 Zimmer, max. 950 € Warmmiete, ohne WBS.
  Bezirke: Charlottenburg, Friedrichshain, Kreuzberg, Lichtenberg, Mitte, Neukölln, Pankow, Prenzlauer Berg, Tiergarten, Treptow, Wedding.
- Preis: bis 750 € volle Punkte, 850 € Wunschgrenze, 950 € Maximum (nur für ansonsten perfekte Wohnungen).
  Über 850 € höchstens 4 Sterne, außer Punkte >= 88.
- Aufzug nur ab 3. Stock wichtig (je höher, desto mehr), Garten ersetzt Aufzug.
- Traumwohnung: 60-70 m², zwei Zimmer (ein kleines, ein großes), Küche, Bad, Balkon, nahe Ringbahn, am liebsten Prenzlauer Berg.
- Arbeitsweg: Luftlinie zum Moritzplatz (keine echte Fahrzeit).
- Gewichte (Summe 100): Kosten 25, Lage 15, Ringbahn 10, ÖPNV 5, Bezirk 5, Größe 12, Zimmer 8, Außenbereich 10, Aufzug 10.
- Sterne ab Punkten: 5 >= 85, 4 >= 70, 3 >= 55, 2 >= 40.
- Bewusst NICHT bewertet: Lärm (unwichtig, "laut sein können" lässt sich vorher nicht prüfen).
- Kaltmiete: Obergrenze 900 € kalt (`MAX_KALTMIETE`), aus dem Exposé gelesen; unbekannt = kein Ausschluss.
- Ringbahn: feste Liste der 27 Halte in `rating.py`, braucht kein Netz.

## Bekannte Schwächen / ungetestet
- Das echte Senden wurde noch nicht gegen die Live-Seite getestet (bisher keine passenden Angebote). Beim ersten echten Treffer Status und Meldung prüfen.
- Overpass (Haltestellen im Umkreis, 5 von 100 Punkten) antwortet oft nicht (504/Timeout); dann zählt der Wert neutral.
- Adresse wird aus dem Kartentext geraten (Straße + Hausnummer, Fallback nur PLZ).
- Etage "DG" (Dachgeschoss) hat keine Zahl und zählt als unbekannt.
- Bezirk wird per Textanfang erkannt, unbekannte Labels werden nur gemeldet, nicht angefragt.
- GitHub kann Cron-Läufe verzögern; geplante Workflows werden nach 60 Tagen ohne Repo-Aktivität deaktiviert (die täglichen Commits von `stats.json` halten es aktiv).
- Seitenaufbau von wbm.de kann sich ändern: Bot bricht mit Fehler ab, wenn keine Angebote gefunden werden.

## Ideen / nächste Schritte
- Gewichte nach Feedback des Nutzers nachjustieren (welche Wohnungen gut/schlecht waren).
- Energieklasse und Heizkosten aus dem Exposé in die Kosten einrechnen.
- Automatische Anfrage für weitere Gesellschaften (gefunden werden sie schon über `ibw_bot.py`):
  Howoge und degewo zuerst (die meisten Angebote), dann Gewobag, Stadt und Land, Gesobau (über Immomio).
- Optional: Anfrage-Button in der Push-Meldung (ntfy-Aktion + GitHub-Token), statt vollautomatisch.
- Echte Fahrzeit zur Arbeit statt Luftlinie.
