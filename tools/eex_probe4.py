#!/usr/bin/env python3
"""4e test EEX : appels de l'API publique du Market Data Hub (api.eex-group.com/pub/market-data)."""
import json, re, ssl, urllib.request, urllib.parse
from datetime import date, timedelta
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Referer": "https://www.eex.com/", "Origin": "https://www.eex.com", "Accept": "application/json, text/javascript, */*; q=0.01"}
def get(url, n=3_000_000):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
        return r.status, r.read(n).decode("utf-8", "replace")
out = {}
_, meta = get("https://eds.eex-group.com/widgets/pub/ee7aff9c-287d-4d4d-a509-ec628bd35ea5/metadata.html")
out["meta"] = meta
_, tpl = get("https://eds.eex-group.com/widgets/pub/lib/v1/templates/customized-solution/marketDataHubTemplate.html")
out["endpoints"] = []
for m in re.finditer(r"(baseUrl|customizeUrl)\s*\+\s*'([^']+)'", tpl):
    out["endpoints"].append(tpl[m.start(): m.start() + 900])
B = "https://api.eex-group.com/pub/market-data"
d = date.today()
tries = {}
for path in ["/table-data", "/futures-table", "/settlement-prices", "/product-list", "/products", "/instruments", "/snippets", "/snippet/21", "/hub-config"]:
    for q in ["", "?shortCode=F7BY", "?shortCode=/E.F7BY"]:
        u = B + path + q
        try:
            st, b = get(u, 3000); tries[u] = [st, b[:800]]
        except Exception as e:
            tries[u] = [str(e)[:150]]
out["tries"] = tries
json.dump(out, open("data/eex_probe4.json", "w"), ensure_ascii=False, indent=1)
print("ok", len(out["endpoints"]))
