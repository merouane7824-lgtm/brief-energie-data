"""Sonde ponctuelle : enregistre le HTML brut de quelques pages pour écrire les parseurs."""
import os, urllib.request, http.cookiejar, time
os.makedirs("data/debug", exist_ok=True)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", "Mozilla/5.0 (brief-energie-data; collecte quotidienne)"), ("Accept-Language", "en")]
pages = {
    "omip_wk_2026-10-26": "https://www.omip.pt/en/dados-mercado?date=2026-10-26&product=EL&zone=FR&instrument=FFB&maturity=Wk",
    "omip_we_2026-10-26": "https://www.omip.pt/en/dados-mercado?date=2026-10-26&product=EL&zone=FR&instrument=FFB&maturity=WE",
    "omip_yr_2026-10-26": "https://www.omip.pt/en/dados-mercado?date=2026-10-26&product=EL&zone=FR&instrument=FFB&maturity=YR",
    "omip_yr_2025-03-14": "https://www.omip.pt/en/dados-mercado?date=2025-03-14&product=EL&zone=FR&instrument=FFB&maturity=YR",
    "omip_all_2026-10-26": "https://www.omip.pt/en/dados-mercado?date=2026-10-26&product=EL&zone=FR&instrument=FFB",
    "omip_home": "https://www.omip.pt/en",
    "selectra_cal": "https://entreprises.selectra.info/energie/electricite/cal",
}
for name, url in pages.items():
    try:
        with op.open(url, timeout=60) as r:
            body = r.read()
        open(f"data/debug/{name}.html", "wb").write(body)
        print("OK", name, len(body))
    except Exception as e:
        print("FAIL", name, e)
    time.sleep(2)
