#!/usr/bin/env python3
"""3e test EEX : widgets eds.eex-group.com du Market Data Hub (snippet 21 = Cal FR base)."""
import json, re, ssl, urllib.parse, urllib.request
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def get(url, n=4_000_000, ref="https://www.eex.com/"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
        return r.status, r.headers.get("content-type", ""), r.read(n).decode("utf-8", "replace")
out = {"snippets": {}, "eds_urls": [], "fetched": {}}
st, ct, html = get("https://www.eex.com/en/market-data/market-data-hub")
# options du sélecteur : id -> libellé
out["options"] = re.findall(r'<option[^>]*value="(\d+)"[^>]*>([^<]{1,120})<', html)
for m in re.finditer(r'id="snippet-(\d+)"', html):
    sid = m.group(1)
    out["snippets"][sid] = html[m.start(): m.start() + 1500]
eds = sorted(set(re.findall(r'https?://eds\.eex-group\.com[^"\'\s<>)]*', html)))
out["eds_urls"] = eds
seen = set()
queue = list(eds)
while queue and len(seen) < 25:
    u = queue.pop(0)
    if u in seen: continue
    seen.add(u)
    try:
        st, ct, b = get(u)
        more = set(re.findall(r'https?://eds\.eex-group\.com[^"\'\s<>)]*', b))
        rel = set(re.findall(r'(?:src|href)="([^"]+)"', b)) | set(re.findall(r'["\'](/widgets/[^"\']+|\.?/?[a-z0-9_./-]+\.(?:js|json))["\']', b))
        for r in rel:
            full = urllib.parse.urljoin(u, r)
            if "eds.eex-group.com" in full: more.add(full)
        ctx = [b[max(0, x.start()-200): x.end()+300] for x in re.finditer(r"fetch\(|ajax|XMLHttpRequest|/api/|websocket|wss://|token|apikey|getJSON", b, re.I)][:20]
        out["fetched"][u] = {"status": st, "ct": ct, "len": len(b), "head": b[:1500] if "html" in ct or "json" in ct else "", "ctx": ctx, "links": sorted(more)[:40]}
        for x in more:
            if x not in seen and "jquery" not in x: queue.append(x)
    except Exception as e:
        out["fetched"][u] = {"error": str(e)[:200]}
json.dump(out, open("data/eex_probe3.json", "w"), ensure_ascii=False, indent=1)
print("ok", len(out["snippets"]), len(eds))
