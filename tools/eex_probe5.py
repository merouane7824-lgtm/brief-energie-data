#!/usr/bin/env python3
"""5e test EEX : liste des contrats (filter-data-with-scope) puis cotations des produits FR visés."""
import base64, json, re, ssl, urllib.request, urllib.parse
from datetime import date, timedelta
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Referer": "https://www.eex.com/", "Origin": "https://www.eex.com", "Accept": "application/json, text/javascript, */*; q=0.01"}
def req(url, data=None, n=8_000_000):
    h = dict(H)
    if data is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        data = urllib.parse.urlencode(data).encode()
    r = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(r, timeout=40, context=ssl.create_default_context()) as x:
        return x.status, x.read(n).decode("utf-8", "replace")
out = {"ctx": [], "filter": {}, "fr": [], "ticker": {}, "table": {}, "intraday": {}}
_, tpl = req("https://eds.eex-group.com/widgets/pub/lib/v1/templates/customized-solution/marketDataHubTemplate.html")
for m in re.finditer(r"customizeUrl\s*\+\s*'/filter-data-with-scope'", tpl):
    out["ctx"].append(tpl[max(0, m.start() - 3000): m.start()])
i = tpl.find("eexDateFormatterLocal")
out["datefmt"] = tpl[i: i + 600] if i >= 0 else ""
CU = "https://api.eex-group.com/pub/customise-widget"
B = "https://api.eex-group.com/pub/market-data"
contracts = [{"commodity": "All", "pricing": "All", "area": "All", "product": "All", "productSpecific": "All", "maturityType": "All"}]
cands = [contracts]
rows = None
for k, p in enumerate(cands):
    enc = base64.b64encode(json.dumps(p, separators=(",", ":")).encode()).decode()
    try:
        st, b = req(CU + "/filter-data-with-scope?data=" + urllib.parse.quote(enc), {"data": enc})
        j = json.loads(b)
        lk = [x for x, v in j.items() if isinstance(v, list) and x != "header"]
        out["filter"][k] = {"status": st, "keys": list(j.keys()), "listkeys": lk, "raw": b[:1500]}
        if rows is None and j.get("header") and lk:
            hd = j["header"]; data = j[lk[0]]
            rows = [dict(zip(hd, r)) for r in data]
    except Exception as e:
        out["filter"][k] = {"error": str(e)[:300]}
if rows:
    fr = [r for r in rows if "power" in json.dumps(r).lower() and ("france" in json.dumps(r).lower() or '"fr"' in json.dumps(r).lower())]
    out["fr"] = fr[:200]
    out["sample_rows"] = rows[:5]
    # cotations : année, trimestre, mois en base et pointe
    d = date.today()
    fmt = lambda x: x.strftime("%Y-%m-%d")
    for r in fr:
        if r.get("pricing") not in (None, "Futures", "F") and "futur" not in str(r.get("pricing", "")).lower():
            continue
        sc = r.get("shortCode")
        mats = {"Year": [1, 2], "Quarter": [1], "Month": [1]}.get(r.get("maturityType"), [])
        for mt in mats:
            key = f"{sc}|{r.get('maturityType')}|{mt}"
            q = {"shortCode": sc, "area": r.get("area"), "product": r.get("product"), "commodity": r.get("commodity"), "pricing": r.get("pricing"), "maturity": mt}
            try:
                st, b = req(B + "/price-ticker?" + urllib.parse.urlencode(q), n=6000); out["ticker"][key] = [st, b[:1500]]
            except Exception as e:
                out["ticker"][key] = [str(e)[:200]]
            if len(out["table"]) < 6:
                q2 = {"shortCode": sc, "commodity": r.get("commodity"), "pricing": r.get("pricing"), "area": r.get("area"), "product": r.get("product"),
                      "maturity": mt, "startDate": fmt(d - timedelta(days=7)), "endDate": fmt(d), "maturityType": r.get("maturityType"), "isRolling": "true"}
                try:
                    st, b = req(B + "/table-data?" + urllib.parse.urlencode(q2), n=6000); out["table"][key] = [st, b[:2000]]
                except Exception as e:
                    out["table"][key] = [str(e)[:200]]
                q3 = {"commodity": r.get("commodity"), "pricing": r.get("pricing"), "area": r.get("area"), "product": r.get("product"), "maturity": mt, "startDate": fmt(d), "shortCode": sc}
                try:
                    st, b = req(B + "/chart/intraday?" + urllib.parse.urlencode(q3), n=4000); out["intraday"][key] = [st, b[:2000]]
                except Exception as e:
                    out["intraday"][key] = [str(e)[:200]]
json.dump(out, open("data/eex_probe5.json", "w"), ensure_ascii=False, indent=1)
print("ok", len(out["fr"]))
