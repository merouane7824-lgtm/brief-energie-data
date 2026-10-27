"""Sonde ponctuelle : enregistre le HTML brut de quelques pages pour écrire les parseurs."""
import os, urllib.request, http.cookiejar, time
os.makedirs("data/debug", exist_ok=True)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", "Mozilla/5.0 (brief-energie-data; collecte quotidienne)"), ("Accept-Language", "en")]
pages = {
    "chart_FFBYR-28": "https://www.omip.pt/en/javali/get_full_chart/FFBYR-28/0/0",
    "chart_FFBYR-27": "https://www.omip.pt/en/javali/get_full_chart/FFBYR-27/0/0",
    "chart_FFBWk45-26": "https://www.omip.pt/en/javali/get_full_chart/FFBWk45-26/0/0",
    "omip_fpb_wk_2026-10-26": "https://www.omip.pt/en/dados-mercado?date=2026-10-26&product=EL&zone=FR&instrument=FPB&maturity=Wk",
}
for name, url in pages.items():
    try:
        with op.open(url, timeout=60) as r:
            body = r.read()
        open(f"data/debug/{name}.txt", "wb").write(body)
        print("OK", name, len(body))
    except Exception as e:
        print("FAIL", name, e)
    time.sleep(2)
