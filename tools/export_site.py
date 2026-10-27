#!/usr/bin/env python3
"""Assemble docs/data/*.json (GitHub Pages) à partir des documents lus dans la base du brief.

Usage : python3 tools/export_site.py <dossier_db> [nb_editions]
<dossier_db> = dossier "out_dir" d'ArtifactData : reports/<date>.json, series/<date>.json,
history/storage.json, nuclear/latest.json, forwards/latest.json, quotes/<date>.json.
"""
import glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1]
keep = int(sys.argv[2]) if len(sys.argv) > 2 else 10
out = os.path.join(ROOT, "docs", "data")
os.makedirs(out, exist_ok=True)


def coll(name):
    rows = []
    for f in glob.glob(os.path.join(src, name, "*.json")):
        if os.path.basename(f) == "versions.json":
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("date"):
            rows.append(d)
    return sorted(rows, key=lambda d: d["date"])


def doc(path):
    f = os.path.join(src, path + ".json")
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else None


import re
PRIVATE = [(re.compile(r"cotation transmise par \w+"), "saisie manuelle"), (re.compile(r"transmise par \w+"), "saisie manuellement"),
           (re.compile(r"(?i)\bmerouane\b"), "l'utilisateur")]


def scrub(o):
    if isinstance(o, str):
        for rx, rep in PRIVATE:
            o = rx.sub(rep, o)
        return o
    if isinstance(o, list):
        return [scrub(x) for x in o]
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items()}
    return o


def dump(name, obj):
    if obj is None:
        return
    obj = scrub(obj)
    with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    print(name, len(json.dumps(obj)))


reports = coll("reports")[-keep:]
dump("reports.json", reports)
dump("series.json", coll("series"))
dump("quotes.json", coll("quotes")[-10:])
for path, name in (("history/storage", "history-storage.json"), ("nuclear/latest", "nuclear.json"), ("forwards/latest", "forwards.json")):
    dump(name, doc(path))
if reports:
    dump("latest.json", {"date": reports[-1]["date"], "headline": reports[-1].get("headline")})
