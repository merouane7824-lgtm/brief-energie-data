#!/usr/bin/env python3
"""Construit docs/index.html (GitHub Pages) à partir de site/page.html, la page du brief publiée sur Claude.

La page lit normalement sa base de données Claude (window.claude.use("db")). Ici, un petit adaptateur
fournit la même interface en lecture seule à partir des fichiers docs/data/*.json.
Usage : python3 tools/build_site.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
page = open(os.path.join(ROOT, "site", "page.html"), encoding="utf-8").read()
page = page[page.index("<title>"):]  # ignore l'en-tête ajouté par Claude à la publication

SHIM = r"""<script>
/* Adaptateur lecture seule : même interface que la base Claude, alimentée par docs/data/*.json */
window.claude = { use: async function (cap) {
  if (cap !== "db") return null;
  var cache = {};
  function load(f) { if (!f) return Promise.resolve(null); return cache[f] || (cache[f] = fetch("__DATA__" + f + "?v=" + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })); }
  var FILES = { reports: "reports.json", series: "series.json", quotes: "quotes.json" };
  var DOCS = { "history/storage": "history-storage.json", "nuclear/latest": "nuclear.json", "forwards/latest": "forwards.json" };
  function snapOf(a) { return { docs: (a || []).map(function (d) { return { exists: true, data: function () { return d; } }; }) }; }
  return {
    readOnly: true,
    collection: function (name) {
      var q = { _f: null, _dir: "asc", _lim: 1e9 };
      q.orderBy = function (f, dir) { q._f = f; q._dir = dir || "asc"; return q; };
      q.limit = function (n) { q._lim = n; return q; };
      q.doc = function () { return { set: function () { return Promise.reject({ code: "lecture seule" }); } }; };
      q.onSnapshot = function (cb) { load(FILES[name]).then(function (arr) { var a = (arr || []).slice(); if (q._f) a.sort(function (x, y) { return String(x[q._f]).localeCompare(String(y[q._f])); }); if (q._dir === "desc") a.reverse(); cb(snapOf(a.slice(0, q._lim))); }); return function () {}; };
      return q;
    },
    doc: function (path) { return { onSnapshot: function (cb) { load(DOCS[path]).then(function (d) { cb({ exists: !!d, data: function () { return d; } }); }); return function () {}; } }; }
  };
} };
</script>
"""
CDN = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js"></script>'
assert CDN in page, "balise Chart.js introuvable"
page = page.replace(CDN, SHIM + CDN)
page = page.replace('<div class="eyebrow">Gaz · Électricité · France &amp; Europe</div>',
                    '<div class="eyebrow">Gaz · Électricité · France &amp; Europe · version publique, mise à jour chaque jour ouvré</div>')
# formulations neutres pour la version publique
for a, b in ((" (méthode indiquée par toi, à valider avec la délibération CRE)", " (méthode à valider avec la délibération CRE)"),
             ("Pas de cotation publique gratuite pour ces produits : saisis-les depuis ton écran EEX.", "Pas de cotation publique gratuite pour ces produits."),
             ("Ouvre la page en étant connecté à ton compte pour lire les éditions.", "Les données de cette édition sont momentanément indisponibles.")):
    assert a in page, a
    page = page.replace(a, b)
head = ('<!doctype html>\n<html lang="fr">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n')
j = page.index('<div class="wrap">')
out = head + page[:j] + "<style>body{margin:0}</style>\n</head>\n<body>\n" + page[j:].rstrip() + "\n</body>\n</html>\n"
os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
# même page à deux endroits : racine du site (Pages sur « / (root) ») et docs/ (Pages sur « /docs »)
open(os.path.join(ROOT, "docs", "index.html"), "w", encoding="utf-8").write(out.replace("__DATA__", "data/"))
open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8").write(out.replace("__DATA__", "docs/data/"))
open(os.path.join(ROOT, "docs", ".nojekyll"), "w").write("")
print("index.html et docs/index.html", len(out))
