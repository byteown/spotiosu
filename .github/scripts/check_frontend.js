#!/usr/bin/env node
/* Static checks for the front-end that `node --check` cannot catch.
 *
 * 1. Every element id app.js reaches for exists in index.html.
 * 2. No local in app.js shadows a global exported by i18n.js.
 *    (This one is not theoretical: `const t = d.totals` once shadowed the
 *    translation function t() and broke the whole profile page at runtime.)
 * 3. Every translation key used is defined in *both* languages, and the two
 *    dictionaries agree on genre ids and on {placeholders} inside templates.
 * 4. Every asset the page references exists under webapp/ (only that tree is
 *    deployed, so a file elsewhere would 404 in production).
 */
"use strict";

const fs = require("fs");
const path = require("path");

const STATIC = "webapp/static";
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
const app = fs.readFileSync(path.join(STATIC, "app.js"), "utf8");
const i18nSrc = fs.readFileSync(path.join(STATIC, "i18n.js"), "utf8");

const problems = [];
const ok = (msg) => console.log(`  ok    ${msg}`);
const bad = (msg) => { console.log(`  FAIL  ${msg}`); problems.push(msg); };

// --- 1. element ids ---------------------------------------------------------
const htmlIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
const jsIds = new Set([...app.matchAll(/\$\("([^"]+)"\)/g)].map((m) => m[1]));
const missingIds = [...jsIds].filter((id) => !htmlIds.has(id));
missingIds.length
  ? bad(`app.js references missing element ids: ${missingIds.join(", ")}`)
  : ok(`${jsIds.size} element ids all exist in index.html`);

// --- 2. shadowing of i18n globals -------------------------------------------
const globals = [...i18nSrc.matchAll(/^(?:function|const|let|var)\s+(\w+)/gm)].map((m) => m[1]);
const shadows = [];
for (const g of globals) {
  const re = new RegExp(
    `(?:const|let|var)\\s+${g}\\s*[=;]` +          // const t = ...
    `|for\\s*\\(\\s*(?:const|let)\\s+${g}\\b` +    // for (const t of ...)
    `|function\\s+\\w+\\s*\\([^)]*\\b${g}\\b`,     // function f(t) {...}
    "g");
  for (const m of app.matchAll(re)) {
    shadows.push(`${g} at app.js:${app.slice(0, m.index).split("\n").length}`);
  }
}
shadows.length
  ? bad(`app.js shadows i18n globals: ${shadows.join("; ")}`)
  : ok(`no local shadows any of: ${globals.join(", ")}`);

// --- 3. translation keys ----------------------------------------------------
const DICT = eval(i18nSrc.replace(/^"use strict";/, "") + "\n; I18N;");
const used = new Set();
for (const m of html.matchAll(/data-i18n(?:-html|-title|-ph)?="([^"]+)"/g)) used.add(m[1]);
for (const m of app.matchAll(/\bt\("([a-z_0-9]+)"/g)) used.add(m[1]);
// keys built by concatenation, e.g. t("verdict_" + verdict)
["easier", "harder", "onpar"].forEach((k) => used.add("verdict_" + k));
["chill", "mid", "fast", "breakneck"].forEach((k) => used.add("tempo_" + k));
used.delete("verdict_"); used.delete("tempo_");

const langs = Object.keys(DICT);
const missingKeys = [];
for (const key of used) {
  for (const lang of langs) {
    if (DICT[lang][key] === undefined) missingKeys.push(`${lang}:${key}`);
  }
}
missingKeys.length
  ? bad(`undefined translation keys: ${missingKeys.join(", ")}`)
  : ok(`${used.size} translation keys defined in ${langs.join(" + ")}`);

const genreIds = langs.map((l) => Object.keys(DICT[l].genres).sort().join(","));
new Set(genreIds).size === 1
  ? ok("genre ids match across languages")
  : bad("genre id sets differ between languages");

for (const key of used) {
  const sets = langs.map((l) =>
    [...String(DICT[l][key] ?? "").matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(","));
  if (new Set(sets).size !== 1) {
    bad(`placeholders differ for "${key}": ${langs.map((l, i) => `${l}=[${sets[i]}]`).join(" ")}`);
  }
}
if (!problems.some((p) => p.startsWith("placeholders"))) ok("template placeholders match across languages");

// --- 4. assets --------------------------------------------------------------
const missingAssets = [];
for (const ref of new Set([...html.matchAll(/(?:href|src)="(\/[^"]+)"/g)].map((m) => m[1]))) {
  let file = null;
  if (ref === "/favicon.ico") file = path.join(STATIC, "favicon/favicon.ico");
  else if (ref.startsWith("/static/")) file = path.join("webapp", ref.slice(1));
  if (file && !fs.existsSync(file)) missingAssets.push(ref);
}
const manifest = JSON.parse(fs.readFileSync(path.join(STATIC, "favicon/site.webmanifest"), "utf8"));
for (const icon of manifest.icons) {
  if (!fs.existsSync(path.join("webapp", icon.src.slice(1)))) missingAssets.push(icon.src);
}
missingAssets.length
  ? bad(`referenced but not present under webapp/: ${missingAssets.join(", ")}`)
  : ok("all referenced assets resolve");

console.log();
if (problems.length) {
  console.error(`${problems.length} front-end check(s) failed`);
  process.exit(1);
}
console.log("all front-end checks passed");
