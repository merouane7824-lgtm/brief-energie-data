#!/usr/bin/env python3
"""Test du flux public (différé) du Market Data Hub EEX pour quelques produits électricité France.
Écrit data/eex_probe.json : réponses brutes résumées, pour décider si un suivi intrajournalier est faisable.
Bibliothèque standard uniquement."""
import json, re, ssl, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HDR = {"User-Agent": UA, "Referer": "https://www.eex.com/", "Origin": "https://www.eex.com",
       "Accept": "application/json, text/plain, */*"}
BASE = "https://webservice-eex.gvsi.com/query/json/"
out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hub": {}, "chains": {}, "dailies": {}, "errors": []}


def get(url, hdr=HDR, n=400000):
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
        return r.status, r.read(n).decode("utf-8", "replace")


# 1) Page du hub et scripts : repérer les requêtes gvsi réellement utilisées
try:
    st, html = get("https://www.eex.com/en/market-data/market-data-hub", {"User-Agent": UA}, 3_000_000)
    out["hub"]["status"] = st
    srcs = sorted(set(re.findall(r'src="([^"]+\.js[^"]*)"', html)))
    out["hub"]["scripts"] = srcs[:60]
    hits = []
    for s in [html] + srcs[:60]:
        try:
            body = s if s is html else get(urllib.parse.urljoin("https://www.eex.com/", s), {"User-Agent": UA}, 6_000_000)[1]
        except Exception as e:
            out["errors"].append(f"script {s[:80]}: {e}")
            continue
        for m in re.finditer(r"gvsi|query/json|optionroot|pricesymbol", body):
            hits.append(((s if s is not html else "html")[:80], body[max(0, m.start() - 160): m.end() + 220]))
            if len(hits) > 60:
                break
    out["hub"]["hits"] = hits
except Exception as e:
    out["errors"].append(f"hub: {e}")

# 2) Chaînes par racine produit (base / pointe, mois / trimestre / année), noms candidats
today = datetime.now(timezone.utc).date()
exp = today.strftime("%Y/%m/%d")
FIELDS = "gv.pricesymbol/gv.displaydate/gv.expirationdate/tradedatetimegmt/gv.eexdeliverystart/ontradeprice/close/onexchsingletradevolume/onexchtradevolumeeex/offexchtradevolumeeex/openinterest/"
roots = []
for p in ("FEB", "FEP", "F7B", "F7P", "FRB", "FRP"):
    for t in ("M", "Q", "Y"):
        roots.append(p + t)
for r in roots:
    url = BASE + "getChain/" + FIELDS + "?" + urllib.parse.urlencode({"optionroot": f'"/E.{r}"', "expirationdate": exp})
    try:
        st, body = get(url)
        try:
            j = json.loads(body)
            items = (j.get("results") or {}).get("items") or []
        except Exception:
            j, items = None, []
        out["chains"][r] = {"status": st, "n": len(items), "sample": items[:6] if items else body[:300]}
    except Exception as e:
        out["chains"][r] = {"error": str(e)[:200]}
    time.sleep(0.4)

# 3) Historique quotidien d'un symbole trouvé (pour voir le format du « close » et du dernier échange)
found = [it.get("gv.pricesymbol") for c in out["chains"].values() for it in (c.get("sample") or []) if isinstance(it, dict) and it.get("gv.pricesymbol")]
for sym in found[:4]:
    q = {"priceSymbol": f'"{sym}"', "chartstartdate": (today - timedelta(days=10)).strftime("%Y/%m/%d"),
         "chartstopdate": today.strftime("%Y/%m/%d"), "dailybarinterval": "Days", "aggregatepriceselection": "First"}
    url = BASE + "getDaily/close/offexchtradevolumeeex/onexchtradevolumeeex/tradedatetimegmt/?" + urllib.parse.urlencode(q)
    try:
        st, body = get(url)
        out["dailies"][sym] = {"status": st, "body": body[:1500]}
    except Exception as e:
        out["dailies"][sym] = {"error": str(e)[:200]}

json.dump(out, open("data/eex_probe.json", "w"), ensure_ascii=False, indent=1)
print("chaînes non vides :", [k for k, v in out["chains"].items() if v.get("n")], "| erreurs :", len(out["errors"]))
