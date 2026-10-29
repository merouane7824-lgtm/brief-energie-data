#!/usr/bin/env python3
"""6e test EEX : cotations France base/pointe (Cal-27, Cal-28, Q1-27, M+1), au rythme lent (limite 429)."""
import base64, json, ssl, time, urllib.request, urllib.parse
from datetime import date, timedelta
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Referer": "https://www.eex.com/", "Origin": "https://www.eex.com", "Accept": "application/json, text/javascript, */*; q=0.01"}
def req(url, data=None, n=8_000_000):
    h = dict(H)
    if data is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"; data = urllib.parse.urlencode(data).encode()
    for a in range(4):
        try:
            r = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(r, timeout=40, context=ssl.create_default_context()) as x:
                return x.status, x.read(n).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 and a < 3:
                time.sleep(10 * (a + 1)); continue
            return e.code, e.read(2000).decode("utf-8", "replace")
CU = "https://api.eex-group.com/pub/customise-widget"; B = "https://api.eex-group.com/pub/market-data"
contracts = [{"commodity": "POWER", "pricing": "F", "area": "FR", "product": "All", "productSpecific": "All", "maturityType": "All"}]
enc = base64.b64encode(json.dumps(contracts, separators=(",", ":")).encode()).decode()
st, b = req(CU + "/filter-data-with-scope?data=" + urllib.parse.quote(enc), {"data": enc})
out = {"filter_status": st}
try:
    j = json.loads(b); rows = [dict(zip(j["header"], r)) for r in j["data"]]
except Exception:
    rows = []; out["filter_body"] = b[:1000]
out["products"] = sorted({(r["product"], r["shortCode"][:3], r["maturityType"]) for r in rows if r["area"] == "FR"})
d = date.today()
m1 = (d.replace(day=1) + timedelta(days=32)).strftime("%Y%m")
want = {("Year", f"{d.year+1}01"), ("Year", f"{d.year+2}01"), ("Quarter", f"{d.year+1}01"), ("Month", m1)}
targets = [r for r in rows if r["area"] == "FR" and r["product"] in ("Base", "Peak") and (r["maturityType"], r["maturity"]) in want]
out["targets"] = targets
out["ticker"], out["table"], out["intraday"] = {}, {}, {}
for r in targets:
    key = f'{r["product"]} {r["maturityType"]} {r["maturity"]} ({r["shortCode"]})'
    q = {"shortCode": r["shortCode"], "area": "FR", "product": r["product"], "commodity": "POWER", "pricing": "F", "maturity": r["maturity"]}
    out["ticker"][key] = req(B + "/price-ticker?" + urllib.parse.urlencode(q), n=8000); time.sleep(3)
    q3 = {"commodity": "POWER", "pricing": "F", "area": "FR", "product": r["product"], "maturity": r["maturity"], "startDate": d.isoformat(), "shortCode": r["shortCode"]}
    out["intraday"][key] = req(B + "/chart/intraday?" + urllib.parse.urlencode(q3), n=6000); time.sleep(3)
    if r["product"] == "Base" and r["maturityType"] == "Year" and not out["table"]:
        q2 = {"shortCode": r["shortCode"], "commodity": "POWER", "pricing": "F", "area": "FR", "product": "Base", "maturity": r["maturity"],
              "startDate": (d - timedelta(days=7)).isoformat(), "endDate": d.isoformat(), "maturityType": "Year", "isRolling": "true"}
        out["table"][key] = req(B + "/table-data?" + urllib.parse.urlencode(q2), n=6000); time.sleep(3)
json.dump(out, open("data/eex_probe6.json", "w"), ensure_ascii=False, indent=1)
print("ok", len(targets))
