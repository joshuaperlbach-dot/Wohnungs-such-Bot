// ==UserScript==
// @name         Wohnungsbot: Anfrage ausfüllen
// @namespace    https://joshuaperlbach-dot.github.io/Wohnungs-such-Bot/
// @version      1.1
// @description  Füllt die Anfrageformulare von degewo, Gewobag, Stadt und Land & Co. mit deinen Daten aus. Captcha lösen und Absenden machst du selbst.
// @match        https://*.degewo.de/*
// @match        https://*.gewobag.de/*
// @match        https://*.stadtundland.de/*
// @match        https://stadtundland.de/*
// @match        https://*.wohnungshelden.de/*
// @match        https://*.gesobau.de/*
// @match        https://*.berlinovo.de/*
// @match        https://*.howoge.de/*
// @match        https://*.wbm.de/*
// @grant        GM_getValue
// @grant        GM_setValue
// @run-at       document-idle
// @updateURL    https://joshuaperlbach-dot.github.io/Wohnungs-such-Bot/ausfuellen.user.js
// @downloadURL  https://joshuaperlbach-dot.github.io/Wohnungs-such-Bot/ausfuellen.user.js
// ==/UserScript==

// Deine Daten stehen NICHT in dieser Datei (sie ist öffentlich), sondern nur im
// Speicher von Tampermonkey auf deinem Handy. Beim ersten Mal fragt das Skript danach.
// Es schickt nie selbst etwas ab und überschreibt keine Felder, die schon ausgefüllt sind.
(function () {
  "use strict";

  const FIELDS = [
    // [Schlüssel, Beschriftung im Eingabefenster]
    ["anrede", "Anrede (Herr/Frau/Divers)"],
    ["vorname", "Vorname"],
    ["nachname", "Nachname"],
    ["email", "E-Mail"],
    ["telefon", "Telefon"],
    ["strasse", "Straße"],
    ["hausnummer", "Hausnummer"],
    ["zusatz", "Adresszusatz (z.B. c/o Name, sonst leer)"],
    ["plz", "PLZ"],
    ["ort", "Ort"],
    ["personen", "Personen im Haushalt"],
    ["kinder", "Davon Kinder"],
  ];

  // Erkennung über Name, ID, Platzhalter, autocomplete und die sichtbare Beschriftung.
  // Reihenfolge zählt: "Vorname" vor "Name", "Hausnummer" vor "Straße".
  // "skip": Felder, die das Skript nie anfasst (Freitext, Fragen zum Einkommen usw.).
  const RULES = [
    ["skip", /anmerkung|nachricht|kommentar|message|bemerkung|einkommen|wbs|haustier|ab wann|einzug/],
    ["anrede", /anrede|salutation|gender/],
    ["zusatz", /zusatz|c\/o|address.?line.?2/],
    ["email", /e-?mail/, (el) => el.type === "email"],
    ["telefon", /telefon|phone|mobil|handy|\btel\b/, (el) => el.type === "tel"],
    ["vorname", /vorname|first.?name|given.?name|fname/],
    ["nachname", /nachname|last.?name|family.?name|surname|lname|^name$|\bname\b/],
    ["hausnummer", /hausnummer|haus-?nr|house.?(number|no)|\bnr\.?$/],
    ["strasse", /stra(ss|ß)e|street|adresse|address/],
    ["plz", /\bplz\b|postleitzahl|zip|postal/],
    ["ort", /\bort\b|wohnort|city|\btown\b|stadt\b/],
    ["kinder", /kinder|children/],
    ["personen", /personen|einziehend|haushaltsgr|persons|household.?size|anzahl.*(bewohner|erwachsene)/],
  ];
  const CONSENT = /datenschutz|privacy|einwillig/;

  const load = () => {
    try { return JSON.parse(GM_getValue("daten", "null")); } catch (e) { return null; }
  };

  function ask(old) {
    const d = {};
    for (const [key, label] of FIELDS) {
      const v = prompt(`${label}:`, (old && old[key]) || "");
      if (v === null) return old || d;  // abgebrochen: bisherige Daten behalten
      d[key] = v.trim();
    }
    GM_setValue("daten", JSON.stringify(d));
    return d;
  }

  function describe(el) {
    const parts = [el.name, el.id, el.placeholder, el.getAttribute("aria-label"),
                   el.getAttribute("autocomplete"), el.getAttribute("formcontrolname")];
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) parts.push(l.textContent);
    }
    const wrap = el.closest("label");
    if (wrap) parts.push(wrap.textContent);
    if (el.labels) for (const l of el.labels) parts.push(l.textContent);
    // Beschriftung direkt davor (häufig bei Formular-Baukästen)
    // (nur, wenn in dem Kasten kein anderes Feld steckt, sonst mischen sich die Beschriftungen)
    const box = el.closest("div, p, li, td");
    if (box && box.textContent.length < 120 && box.querySelectorAll("input, select, textarea").length === 1) {
      parts.push(box.textContent);
    }
    return parts.filter(Boolean).join(" ").toLowerCase();
  }

  // Wert so setzen, dass auch React/Angular-Formulare ihn mitbekommen
  function setValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    for (const t of ["input", "change"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  }

  // Nur Felder, die man auch sieht und antippen kann. Ausklapp-Listen mancher Seiten haben ein
  // verstecktes Eingabefeld; da etwas hineinzuschreiben hat bei Stadt und Land das Formular blockiert.
  function usable(el) {
    if (el.disabled || el.readOnly || el.tabIndex < 0 || el.closest("[aria-hidden=true]")) return false;
    if (["hidden", "submit", "button", "password", "file", "radio", "date"].includes(el.type)) return false;
    const r = el.getBoundingClientRect();
    return r.width > 4 && r.height > 4 && getComputedStyle(el).visibility !== "hidden";
  }
  const done = new WeakSet();  // jedes Feld höchstens einmal anfassen, danach gehört es dir

  function pickOption(select, value) {
    const want = value.toLowerCase();
    const opt = [...select.options].find((o) => o.value && o.text.toLowerCase().includes(want));
    if (!opt || select.value === opt.value) return false;
    select.value = opt.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function fill(d) {
    let count = 0;
    const els = [...document.querySelectorAll("input, select, textarea")].filter((el) => !done.has(el) && usable(el));
    const texts = new Map(els.map((el) => [el, describe(el)]));
    // Kein eigenes Feld für die Hausnummer: dann kommt sie mit ins Straßenfeld
    const hausRe = RULES.find(([key]) => key === "hausnummer")[1];
    const strasseOnly = ![...texts.values()].some((t) => hausRe.test(t));
    for (const el of els) {
      const text = texts.get(el);
      if (el.type === "checkbox") {
        done.add(el);
        if (!el.checked && CONSENT.test(text)) { el.click(); count++; }
        continue;
      }
      const rule = RULES.find(([, re, test]) => re.test(text) || (test && test(el)));
      if (!rule) continue;
      done.add(el);
      if (rule[0] === "skip") continue;
      let value = d[rule[0]];
      if (rule[0] === "strasse" && strasseOnly && d.hausnummer) value = `${d.strasse} ${d.hausnummer}`;
      if (!value) continue;
      if (el.tagName === "SELECT") { if (pickOption(el, value)) count++; continue; }
      if (rule[0] === "anrede") continue;  // Anrede nur in echte Auswahllisten, nie in Textfelder
      if (el.value) continue;  // nichts überschreiben
      setValue(el, value);
      count++;
    }
    return count;
  }

  // Kleiner Knopf unten rechts: zeigt, was passiert ist; Tippen füllt nochmal aus,
  // langes Drücken ändert die Daten.
  function badge() {
    if (window.top !== window.self) return null;  // im eingebetteten Formular kein zweiter Knopf
    const b = document.createElement("button");
    b.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:2147483647;padding:10px 14px;" +
      "border:0;border-radius:20px;background:#1a7f37;color:#fff;font:600 14px sans-serif;box-shadow:0 2px 8px #0005";
    b.textContent = "Ausfüllen";
    let timer;
    b.addEventListener("touchstart", () => { timer = setTimeout(() => { timer = null; daten = ask(daten); run(); }, 700); });
    b.addEventListener("touchend", () => clearTimeout(timer));
    b.addEventListener("click", () => { if (timer !== null) run(); });
    document.body.appendChild(b);
    return b;
  }

  let daten = load();
  let total = 0;
  const btn = badge();
  function run() {
    if (!daten) daten = ask(null);
    if (!daten) return;
    const n = fill(daten);
    if (btn) show(n);
    else if (n) window.top.postMessage({ wohnungsbot: n }, "*");  // eingebettetes Formular: dem Knopf oben Bescheid geben
  }
  function show(n) {
    total += n;
    btn.textContent = total ? `✓ ${total} Felder ausgefüllt` : "Ausfüllen";
  }
  if (btn) window.addEventListener("message", (e) => { if (Number.isInteger(e.data && e.data.wohnungsbot)) show(e.data.wohnungsbot); });

  run();
  // Formulare tauchen oft erst nach einem Klick auf (Akkordeon, eingebettetes Formular):
  // eine Minute lang bei Änderungen der Seite erneut nach NEUEN Feldern sehen.
  // Bereits angefasste Felder bleiben in Ruhe, damit nichts zurückgesetzt wird.
  let pending = null;
  const obs = new MutationObserver(() => {
    if (pending) return;
    pending = setTimeout(() => { pending = null; if (daten) run(); }, 300);
  });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  // Aufklappen (z.B. degewo) ändert oft nur die Sichtbarkeit: nach jedem Tippen kurz nachsehen
  document.addEventListener("click", () => setTimeout(() => { if (daten) run(); }, 400), true);
  setTimeout(() => obs.disconnect(), 60000);
})();
