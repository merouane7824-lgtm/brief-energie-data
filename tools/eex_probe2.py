#!/usr/bin/env python3
"""2e test EEX : repérer le point d'accès de données du Market Data Hub (hôtes, iframes, snippets)."""
import json, re, ssl, urllib.parse, urllib.request
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def get(url, n=6_000_000, hdr=None):
    req = urllib.request.Request(url, headers=hdr or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
        return r.status, r.headers.get("content-type", ""), r.read(n).decode("utf-8", "replace")
out = {"pages": {}, "js": {}, "tries": {}}
urls = ["https://www.eex.com/en/market-data/market-data-hub",
        "https://www.eex.com/en/market-data/power/futures"]
pat = re.compile(r"(https?:)?//[a-z0-9.-]+\.(?:eex|eex-group|gvsi|deutsche-boerse)[a-z0-9./_-]*|/api/[A-Za-z0-9/_.-]+|snippet[A-Za-z_]*[\"']?\s*[:=]\s*[\"'][^\"']{1,80}|data-[a-z-]+=\"[^\"]{1,200}\"|<iframe[^>]+>", re.I)
for u in urls:
    try:
        st, ct, html = get(u)
        found = sorted(set(m.group(0) for m in pat.finditer(html)))
        out["pages"][u] = {"status": st, "len": len(html), "found": found[:200]}
        for s in sorted(set(re.findall(r'src="([^"]+\.js[^"]*)"', html))):
            if "etracker" in s or s in out["js"]:
                continue
            try:
                st2, ct2, js = get(urllib.parse.urljoin(u, s))
                f = sorted(set(m.group(0) for m in pat.finditer(js)))
                ctx = [js[max(0, m.start()-150):m.end()+250] for m in re.finditer(r"market-?data|snippet|fetch\(|\$\.ajax|XMLHttpRequest", js, re.I)][:25]
                out["js"][s] = {"len": len(js), "found": f[:150], "ctx": ctx}
            except Exception as e:
                out["js"][s] = {"error": str(e)[:200]}
    except Exception as e:
        out["pages"][u] = {"error": str(e)[:200]}
for t in ["https://webservice-eex.gvsi.com/", "https://api.eex-group.com/", "https://www.eex.com/api/"]:
    try:
        st, ct, b = get(t, 2000, {"User-Agent": UA, "Referer": "https://www.eex.com/"})
        out["tries"][t] = {"status": st, "ct": ct, "body": b[:300]}
    except Exception as e:
        out["tries"][t] = {"error": str(e)[:200]}
json.dump(out, open("data/eex_probe2.json", "w"), ensure_ascii=False, indent=1)
print("ok")
