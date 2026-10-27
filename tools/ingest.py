#!/usr/bin/env python3
"""Prépare les écritures ArtifactData à partir de data/brief.json (collecteur ENTSO-E / RTE).

Usage : python3 ingest.py brief.json existing_series_dir out_dir
- existing_series_dir : documents "series" déjà lus (un JSON par jour, avec leur version dans versions.json)
- out_dir : nuclear_latest.json, forecast.json, series_patch/<date>.json et writes.json (lot ArtifactData)
Règle : les champs déjà présents dans "series" ne sont jamais écrasés, sauf spot_h incomplet.
"""
import json, os, sys

src, ser_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(src, encoding="utf-8"))
os.makedirs(os.path.join(out, "series_patch"), exist_ok=True)
versions = {}
vp = os.path.join(ser_dir, "versions.json")
if os.path.exists(vp):
    versions = json.load(open(vp))

FIELDS = ("spot_h", "spot_fr", "nuc_gw", "wind_gw", "solar_gw", "load_gw")
writes = []
for date, row in sorted(d.get("series", {}).items()):
    p = os.path.join(ser_dir, date + ".json")
    cur = json.load(open(p)) if os.path.exists(p) else None
    patch = {}
    for k in FIELDS:
        if row.get(k) is None:
            continue
        if cur is None or cur.get(k) is None:
            patch[k] = row[k]
        elif k == "spot_h" and (len(cur[k]) != 24 or any(v is None for v in cur[k])) and None not in row[k]:
            patch[k] = row[k]
    if not patch:
        continue
    fp = os.path.join(out, "series_patch", date + ".json")
    if cur is None:
        patch["date"] = date
        json.dump(patch, open(fp, "w"), ensure_ascii=False)
        writes.append({"op": "set", "collection": "series", "doc_id": date, "file_path": os.path.abspath(fp)})
    else:
        json.dump(patch, open(fp, "w"), ensure_ascii=False)
        w = {"op": "update", "collection": "series", "doc_id": date, "file_path": os.path.abspath(fp)}
        if date in versions:
            w["if_version"] = versions[date]
        writes.append(w)

nuc = d.get("nuclear") or {}
if nuc.get("daily"):
    nuc = dict(nuc)
    nuc["date"] = d["today"]
    json.dump(nuc, open(os.path.join(out, "nuclear_latest.json"), "w"), ensure_ascii=False)

fc = [{k: v for k, v in r.items() if k != "src"} for r in d.get("forecast", [])]
srcs = sorted({s for r in d.get("forecast", []) for s in r.get("src", {}).values()})
json.dump({"forecast": fc, "sources": srcs, "load_outlook": d.get("load_outlook", {})},
          open(os.path.join(out, "forecast.json"), "w"), ensure_ascii=False)
json.dump(writes, open(os.path.join(out, "writes.json"), "w"), ensure_ascii=False)
print(f"{len(writes)} écritures series, nucléaire : {len(nuc.get('daily', []))} jours, prévisions : {len(fc)} jours ({', '.join(srcs)})")
