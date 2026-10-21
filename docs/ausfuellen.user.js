// ==UserScript==
// @name         Wohnungsbot: Anfrage ausfüllen
// @namespace    https://joshuaperlbach-dot.github.io/Wohnungs-such-Bot/
// @version      1.0
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
    ["plz", "PLZ"],
    ["ort", "Ort"],
    ["personen", "Personen im Haushalt"],
  ];

  // Erkennung über Name, ID, Platzhalter, autocomplete und die sichtbare Beschriftung.
  // Reihenfolge zählt: "Vorname" vor "Name", "Hausnummer" vor "Straße".
  const RULES = [
    ["email", /e-?mail/, (el) => el.type === "email"],
    ["telefon", /telefon|phone|mobil|handy|\btel\b/, (el) => el.type === "tel"],
    ["vorname", /vorname|first.?name|given.?name|fname/],
    ["nachname", /nachname|last.?name|family.?name|surname|lname|^name$|\bname\b/],
    ["hausnummer", /hausnummer|haus-?nr|house.?(number|no)|\bnr\.?$/],
    ["strasse", /stra(ss|ß)e|street|adresse|address/],
    ["plz", /\bplz\b|postleitzahl|zip|postal/],
    ["ort", /\bort\b|wohnort|city|\btown\b|stadt\b/],
    ["personen", /personen|haushaltsgr|persons|household.?size|anzahl.*(bewohner|erwachsene)/],
    ["anrede", /anrede|salutation|gender|title/],
  ];
  const CONSENT = /datenschutz|privacy|einwillig/;

  const load = () => {
    try { return JSON.parse(GM_getValue("daten", "null")); } catch (e) { return null; }
  };

  function ask(old) {
    const d = {};
    for (const [key, label] of FIELDS) {
      const v = prompt(`${label}:`, (old && old[key]) || "");
      if (v === null) return old;  // abgebrochen: alles beim Alten
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
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  }

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
    const els = [...document.querySelectorAll("input, select, textarea")].filter((el) =>
      !el.disabled && !el.readOnly && !["hidden", "submit", "button", "password", "file"].includes(el.type));
    const texts = new Map(els.map((el) => [el, describe(el)]));
    // Kein eigenes Feld für die Hausnummer: dann kommt sie mit ins Straßenfeld
    const hausRe = RULES.find(([key]) => key === "hausnummer")[1];
    const strasseOnly = ![...texts.values()].some((t) => hausRe.test(t));
    for (const el of els) {
      const text = texts.get(el);
      if (el.type === "checkbox") {
        if (!el.checked && CONSENT.test(text)) { el.click(); count++; }
        continue;
      }
      if (el.type === "radio") continue;
      const rule = RULES.find(([, re, test]) => re.test(text) || (test && test(el)));
      if (!rule) continue;
      let value = d[rule[0]];
      if (rule[0] === "strasse" && strasseOnly && d.hausnummer) value = `${d.strasse} ${d.hausnummer}`;
      if (!value) continue;
      if (el.tagName === "SELECT") { if (pickOption(el, value)) count++; continue; }
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
    total += fill(daten);
    if (btn) btn.textContent = total ? `✓ ${total} Felder ausgefüllt` : "Ausfüllen";
  }

  run();
  // Formulare tauchen oft erst nach einem Klick auf (Akkordeon, eingebettetes Formular):
  // eine Minute lang bei Änderungen der Seite erneut versuchen.
  let pending = null;
  const obs = new MutationObserver(() => {
    if (pending) return;
    pending = setTimeout(() => { pending = null; if (daten) run(); }, 300);
  });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(() => obs.disconnect(), 60000);
})();
