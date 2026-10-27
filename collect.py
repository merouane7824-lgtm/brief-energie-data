#!/usr/bin/env python3
"""Collecte quotidienne des données électricité France pour le Brief Marchés Énergie.

Sources : ENTSO-E Transparency Platform (API REST) et RTE Data (API OAuth2).
Sortie : data/brief.json (données) et data/status.json (journal de collecte).
Les identifiants sont lus dans les variables d'environnement ENTSOE_TOKEN,
RTE_CLIENT_ID et RTE_CLIENT_SECRET ; ils ne sont jamais écrits ni affichés.
Bibliothèque standard uniquement (Python 3.11+).
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.timezone.utc
FR = "10YFR-RTE------C"
ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"
RTE_BASE = "https://digital.iservices.rte-france.com"
PAST_DAYS = 35          # historique réalisé
NUC_AHEAD_DAYS = 364    # horizon des indisponibilités nucléaires (max. 1 an par requête ENTSO-E)
OUT_DIR = os.environ.get("OUT_DIR", "data")
STATUS: list[dict] = []
SECRET_NAMES = ("ENTSOE_TOKEN", "RTE_CLIENT_ID", "RTE_CLIENT_SECRET")


# ---------------------------------------------------------------- utilitaires
def secret(name: str) -> str:
    return os.environ.get(name, "").strip()


def redact(s: str) -> str:
    s = str(s)
    for n in SECRET_NAMES:
        v = secret(n)
        if v:
            s = s.replace(v, "***")
    return s


def log(step: str, ok: bool, msg: str = "") -> None:
    msg = redact(msg)[:500]
    STATUS.append({"step": step, "ok": ok, "msg": msg})
    print(("OK   " if ok else "FAIL ") + step + (" : " + msg if msg else ""), flush=True)


def http(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None,
         timeout: int = 120, tries: int = 3):
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(8 * (i + 1))
                last = e
                continue
            return e.code, e.headers.get("Content-Type", ""), body
        except Exception as e:  # réseau
            last = e
            time.sleep(8 * (i + 1))
    raise RuntimeError(redact(f"réseau : {last}"))


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(el, *names):
    cur = el
    for n in names:
        nxt = None
        for c in cur:
            if local(c.tag) == n:
                nxt = c
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def children(el, name):
    return [c for c in el if local(c.tag) == name]


def text(el, *names, default=None):
    c = child(el, *names)
    return c.text.strip() if c is not None and c.text and c.text.strip() else default


def parse_time(s: str) -> dt.datetime:
    s = s.strip().replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


STEPS = {"PT1M": dt.timedelta(minutes=1), "PT15M": dt.timedelta(minutes=15), "PT30M": dt.timedelta(minutes=30),
         "PT60M": dt.timedelta(hours=1), "PT1H": dt.timedelta(hours=1), "P1D": dt.timedelta(days=1),
         "P7D": dt.timedelta(days=7)}


def segments(ts, value_tag: str = "quantity", period_tag: str = "Period"):
    """(début, fin, valeur, pas) pour chaque point ; gère les courbes A03 (positions manquantes = valeur répétée)."""
    out = []
    for per in children(ts, period_tag):
        st = parse_time(text(per, "timeInterval", "start"))
        en = parse_time(text(per, "timeInterval", "end"))
        res = text(per, "resolution")
        step = STEPS.get(res)
        pts = []
        for p in children(per, "Point"):
            pos, v = text(p, "position"), text(p, value_tag)
            if pos is not None and v is not None:
                pts.append((int(pos), float(v)))
        if not pts:
            continue
        pts.sort()
        if step is None:  # P1Y, P1M… : une seule valeur sur tout l'intervalle
            out.append((st, en, pts[0][1], en - st))
            continue
        for i, (pos, v) in enumerate(pts):
            a = st + step * (pos - 1)
            b = st + step * (pts[i + 1][0] - 1) if i + 1 < len(pts) else en
            if b > a:
                out.append((a, min(b, en), v, step))
    return out


def slots(a: dt.datetime, b: dt.datetime, step: dt.timedelta):
    t = a
    while t < b:
        yield t, min(t + step, b)
        t += step


def paris_day(t: dt.datetime) -> str:
    return t.astimezone(PARIS).date().isoformat()


def day_start(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, tzinfo=PARIS).astimezone(UTC)


def daily_mean(segs, min_hours: float = 23.0, base_step=dt.timedelta(minutes=15)):
    """Moyenne journalière (jour de Paris), pondérée par la durée ; ne garde que les jours couverts ≥ min_hours."""
    acc = defaultdict(lambda: [0.0, 0.0])
    for a, b, v, step in segs:
        st = min(step, base_step) if step <= dt.timedelta(hours=1) else dt.timedelta(hours=1)
        for x, y in slots(a, b, st):
            h = (y - x).total_seconds() / 3600
            d = paris_day(x)
            acc[d][0] += v * h
            acc[d][1] += h
    return {d: s / h for d, (s, h) in acc.items() if h >= min_hours}


def hourly_by_day(segs):
    """Prix horaires par jour de Paris (0 h à 23 h) ; l'heure doublée du changement d'heure est moyennée."""
    by_step = defaultdict(list)
    for s in segs:
        by_step[s[3]].append(s)
    if not by_step:
        return {}
    fine = min(by_step)
    acc = defaultdict(lambda: defaultdict(list))
    for a, b, v, step in by_step[fine]:
        for x, _ in slots(a, b, min(step, dt.timedelta(hours=1))):
            loc = x.astimezone(PARIS)
            acc[loc.date().isoformat()][loc.hour].append(v)
    out = {}
    for d, hrs in acc.items():
        out[d] = [round(sum(hrs[h]) / len(hrs[h]), 2) if hrs.get(h) else None for h in range(24)]
    return out


def r1(x, n=1):
    return None if x is None else round(x, n)


# -------------------------------------------------------------------- ENTSO-E
def entsoe_period(a: dt.datetime) -> str:
    return a.astimezone(UTC).strftime("%Y%m%d%H%M")


def entsoe(params: dict):
    tok = secret("ENTSOE_TOKEN")
    if not tok:
        raise RuntimeError("jeton ENTSOE_TOKEN absent")
    q = dict(params)
    q["securityToken"] = tok
    code, _ctype, body = http(ENTSOE_URL + "?" + urllib.parse.urlencode(q))
    raw = []
    if body[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            raw = [z.read(n) for n in z.namelist() if n.lower().endswith(".xml")]
    else:
        raw = [body]
    docs = []
    for b in raw:
        try:
            root = ET.fromstring(b)
        except ET.ParseError:
            raise RuntimeError(f"HTTP {code}, réponse non XML : {b[:150]!r}")
        if local(root.tag) == "Acknowledgement_MarketDocument":
            reason = " ".join((t.text or "").strip() for t in root.iter() if local(t.tag) == "text")
            if "No matching data" in reason or "no matching data" in reason.lower():
                continue
            raise RuntimeError(f"HTTP {code} : {reason[:300]}")
        docs.append(root)
    if code != 200 and not docs:
        raise RuntimeError(f"HTTP {code}")
    return docs


def timeseries(docs):
    for d in docs:
        for ts in children(d, "TimeSeries"):
            yield ts


def window(days_back: int, days_fwd: int, today: dt.date):
    return day_start(today - dt.timedelta(days=days_back)), day_start(today + dt.timedelta(days=days_fwd))


def get_prices(today):
    a, b = window(PAST_DAYS, 2, today)
    docs = entsoe({"documentType": "A44", "in_Domain": FR, "out_Domain": FR,
                   "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b)})
    segs = [s for ts in timeseries(docs) for s in segments(ts, "price.amount")]
    return hourly_by_day(segs)


PSR = {"B14": "nuc", "B16": "solar", "B18": "wind_off", "B19": "wind_on"}
# toutes les filières pour le mix complet (codes ENTSO-E psrType)
MIX = {"B14": "nuc_gw", "B16": "solar_gw", "B18": "wind_gw", "B19": "wind_gw",
       "B10": "hydro_gw", "B11": "hydro_gw", "B12": "hydro_gw",
       "B04": "gas_gw", "B02": "coal_gw", "B05": "coal_gw", "B06": "coal_gw",
       "B01": "bio_gw", "B09": "bio_gw", "B15": "bio_gw", "B17": "bio_gw", "B20": "bio_gw", "B25": "bio_gw"}  # B25 = stockage (batteries)


def get_generation(today):
    """Production réalisée moyenne par jour et par filière (MW) ; le pompage (consommation des STEP) à part."""
    a, b = window(PAST_DAYS, 1, today)
    docs = entsoe({"documentType": "A75", "processType": "A16", "in_Domain": FR,
                   "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b)})
    per_key = defaultdict(list)
    seen = set()
    per_psr = defaultdict(list)
    for ts in timeseries(docs):
        psr = text(ts, "MktPSRType", "psrType")
        seen.add(psr)
        consumption = child(ts, "outBiddingZone_Domain.mRID") is not None and child(ts, "inBiddingZone_Domain.mRID") is None
        key = "pump_gw" if consumption and psr == "B10" else (None if consumption else MIX.get(psr))
        if key:
            per_psr[(key, psr, consumption)].extend(segments(ts))
    DIAG["A75_psr"] = sorted(x for x in seen if x)
    # moyenne journalière par type puis somme par filière (éolien terrestre + en mer, hydro fil de l'eau + lacs + STEP…)
    per_key = defaultdict(dict)
    for (key, _psr, _c), segs in per_psr.items():
        for d, mw in daily_mean(segs).items():
            per_key[key][d] = per_key[key].get(d, 0.0) + mw
    return per_key


NEIGHBOURS = {"BE": "10YBE----------2", "DE": "10Y1001A1001A82H", "CH": "10YCH-SWISSGRIDZ",
              "IT": "10Y1001A1001A73I", "ES": "10YES-REE------0", "GB": "10YGB----------A"}


def get_flows(today):
    """Flux physiques transfrontaliers (A11) : exports nets journaliers de la France, par frontière (MW, + = export)."""
    a, b = window(PAST_DAYS, 1, today)
    res = {}
    for name, eic in NEIGHBOURS.items():
        try:
            out_docs = entsoe({"documentType": "A11", "out_Domain": FR, "in_Domain": eic,
                               "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b)})
            in_docs = entsoe({"documentType": "A11", "out_Domain": eic, "in_Domain": FR,
                              "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b)})
            exp = daily_mean([sg for ts in timeseries(out_docs) for sg in segments(ts)])
            imp = daily_mean([sg for ts in timeseries(in_docs) for sg in segments(ts)])
            res[name] = {d: exp.get(d, 0.0) - imp.get(d, 0.0) for d in set(exp) | set(imp) if d in exp and d in imp}
            log(f"  flux {name}", True, f"{len(res[name])} jours")
        except Exception as e:
            log(f"  flux {name}", False, str(e))
        time.sleep(1)
    return res


DIAG: dict = {}


def ts_summary(ts):
    pers = children(ts, "Period")
    out = {"psr": text(ts, "MktPSRType", "psrType"), "bt": text(ts, "businessType"), "curve": text(ts, "curveType"),
           "in": text(ts, "inBiddingZone_Domain.mRID"), "out": text(ts, "outBiddingZone_Domain.mRID"), "periods": []}
    for per in pers[:3]:
        pts = children(per, "Point")
        out["periods"].append({"start": text(per, "timeInterval", "start"), "end": text(per, "timeInterval", "end"),
                               "res": text(per, "resolution"), "n": len(pts),
                               "first": [text(p, "quantity") for p in pts[:3]], "max": max((float(text(p, "quantity", default="0")) for p in pts), default=None)})
    return out


def get_ws_forecast(today):
    a, b = window(0, 3, today)
    res = {}
    for proc in ("A01", "A40", "A18"):  # day-ahead, intraday, current
        try:
            docs = entsoe({"documentType": "A69", "processType": proc, "in_Domain": FR,
                           "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b)})
        except Exception as e:
            DIAG[f"A69_{proc}"] = str(e)[:200]
            continue
        DIAG[f"A69_{proc}"] = [ts_summary(ts) for ts in timeseries(docs)][:8]
        per_type = defaultdict(list)
        for ts in timeseries(docs):
            k = PSR.get(text(ts, "MktPSRType", "psrType"))
            segs = segments(ts)
            if k and segs and max(v for _a, _b, v, _s in segs) > 0:  # série vide publiée à 0 : ignorée
                per_type[k].extend(segs)
        for k, v in per_type.items():
            for d, mw in daily_mean(v, min_hours=20.0).items():
                res.setdefault(k, {}).setdefault(d, (mw, proc))  # A01 prioritaire, puis A40, puis A18
    return {k: {d: mw for d, (mw, _p) in dm.items()} for k, dm in res.items()}


def get_load(today, process, days_back, days_fwd, min_hours=23.0):
    a, b = window(days_back, days_fwd, today)
    docs = entsoe({"documentType": "A65", "processType": process, "outBiddingZone_Domain": FR,
                   "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b)})
    by_bt = defaultdict(list)
    for ts in timeseries(docs):
        by_bt[text(ts, "businessType") or "x"].extend(segments(ts))
    return by_bt, docs


def load_minmax(by_bt):
    """Prévisions semaine / mois / année : une série min (A60) et une série max (A61) par jour ou semaine."""
    out = {}
    for bt, segs in by_bt.items():
        kind = "min" if bt == "A60" else "max" if bt == "A61" else "val"
        for a, b, v, step in segs:
            out.setdefault((a, b), {})[kind] = v
    rows = []
    for (a, b), vals in sorted(out.items()):
        rows.append({"from": paris_day(a), "to": paris_day(b - dt.timedelta(seconds=1)),
                     "min_gw": r1(vals.get("min", vals.get("val")) / 1000 if vals.get("min", vals.get("val")) is not None else None, 2),
                     "max_gw": r1(vals.get("max", vals.get("val")) / 1000 if vals.get("max", vals.get("val")) is not None else None, 2)})
    return rows


def get_installed_nuclear(today):
    a = day_start(dt.date(today.year, 1, 1))
    b = day_start(dt.date(today.year + 1, 1, 1))
    docs = entsoe({"documentType": "A68", "processType": "A33", "in_Domain": FR, "psrType": "B14",
                   "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b)})
    for ts in timeseries(docs):
        segs = segments(ts)
        if segs:
            return segs[-1][2]
    return None


UNAV_BT = {"A53": "planned", "A54": "forced"}


def get_unavailability(doc_type, today, days_back, days_fwd):
    """Deux fenêtres (passé, à venir) car l'API limite chaque requête à un an ; pagination par 200 messages."""
    docs = []
    for a, b in (window(days_back, 0, today), window(0, days_fwd, today)):
        offset = 0
        while True:
            part = entsoe({"documentType": doc_type, "biddingZone_Domain": FR,
                           "periodStart": entsoe_period(a), "periodEnd": entsoe_period(b), "offset": str(offset)})
            docs.extend(part)
            if len(part) < 200 or offset >= 4800:
                break
            offset += 200
    return docs


def parse_unavailability(docs):
    """Dernière révision de chaque message, hors messages annulés ; ne garde que le nucléaire (B14)."""
    latest = {}
    for d in docs:
        mrid = text(d, "mRID") or str(id(d))
        rev = int(text(d, "revisionNumber", default="0") or 0)
        if mrid not in latest or rev >= latest[mrid][0]:
            latest[mrid] = (rev, d)
    out = []
    for rev, d in latest.values():
        status = text(d, "docStatus", "value")
        if status in ("A09", "A13"):
            continue
        reasons = [text(r, "text") for r in children(d, "Reason") if text(r, "text")]
        for ts in children(d, "TimeSeries"):
            psr = text(ts, "production_RegisteredResource.pSRType.psrType")
            if psr != "B14":
                continue
            name = (text(ts, "production_RegisteredResource.pSRType.powerSystemResources.name")
                    or text(ts, "production_RegisteredResource.name") or "?")
            nom = text(ts, "production_RegisteredResource.pSRType.powerSystemResources.nominalP")
            if nom is None:
                continue
            nom = float(nom)
            sd, st = text(ts, "start_DateAndOrTime.date"), text(ts, "start_DateAndOrTime.time", default="00:00:00Z")
            ed, et = text(ts, "end_DateAndOrTime.date"), text(ts, "end_DateAndOrTime.time", default="00:00:00Z")
            segs = segments(ts, "quantity", "Available_Period")
            if not segs:
                continue
            ts_reasons = [text(r, "text") for r in children(ts, "Reason") if text(r, "text")]
            out.append({
                "mrid": text(d, "mRID"), "rev": rev, "unit": name.strip(), "nominal_mw": nom,
                "type": UNAV_BT.get(text(ts, "businessType"), "other"),
                "start": parse_time(f"{sd}T{st}") if sd else segs[0][0],
                "end": parse_time(f"{ed}T{et}") if ed else segs[-1][1],
                "segs": [(a, b, v) for a, b, v, _ in segs],
                "reason": "; ".join(ts_reasons or reasons)[:160] or None,
            })
    return out


def nuclear_availability(msgs, installed_mw, today, actual_daily, days_back=PAST_DAYS, days_fwd=NUC_AHEAD_DAYS):
    """Disponibilité journalière = capacité installée − Σ unités max(indisponibilité) ; séparée programmé / fortuit."""
    g0 = day_start(today - dt.timedelta(days=days_back))
    g1 = day_start(today + dt.timedelta(days=days_fwd))
    n = int((g1 - g0).total_seconds() // 3600)
    hours = [g0 + dt.timedelta(hours=i) for i in range(n)]
    units = defaultdict(lambda: {"planned": [0.0] * n, "forced": [0.0] * n})
    for m in msgs:
        arr = units[m["unit"]][m["type"] if m["type"] in ("planned", "forced") else "planned"]
        for a, b, avail in m["segs"]:
            un = max(0.0, m["nominal_mw"] - avail)
            if un <= 0:
                continue
            i0 = max(0, int((a - g0).total_seconds() // 3600))
            i1 = min(n, int(-(-(b - g0).total_seconds() // 3600)))
            for i in range(i0, i1):
                if un > arr[i]:
                    arr[i] = un
    plan_h = [0.0] * n
    forc_h = [0.0] * n
    for u in units.values():
        for i in range(n):
            p, f = u["planned"][i], u["forced"][i]
            tot = max(p, f)
            if tot <= 0:
                continue
            fs = max(0.0, tot - p)
            forc_h[i] += fs
            plan_h[i] += tot - fs
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    for i, t in enumerate(hours):
        d = paris_day(t)
        acc[d][0] += plan_h[i]
        acc[d][1] += forc_h[i]
        acc[d][2] += 1
    daily = []
    for d in sorted(acc):
        p, f, c = acc[d]
        if c < 20:
            continue
        up, uf = p / c / 1000, f / c / 1000
        row = {"date": d, "unav_planned_gw": r1(up, 2), "unav_forced_gw": r1(uf, 2), "unav_gw": r1(up + uf, 2)}
        if installed_mw:
            row["avail_gw"] = r1(installed_mw / 1000 - up - uf, 2)
        if d in actual_daily:
            row["actual_gw"] = r1(actual_daily[d] / 1000, 2)
        daily.append(row)
    return daily


import re
BOILER = re.compile(r"(recouplage|restart|remont|ramp-up|palier|load threshold|sous r[ée]serve|subject to|tso agreement|accord rte|reprogramm|resched|may be|likely to be|susceptible|^-+$)", re.I)


def clean_reason(t):
    if not t:
        return None
    t = t.split("\n-")[0]  # partie française avant la traduction
    parts = [p.strip(" .-") for p in re.split(r"[\n.;]+", t) if p.strip(" .-")]
    low = t.lower()
    if "modulation" in low:
        return "Modulation (conditions économiques)"
    if "environnement" in low or "environmental" in low:
        return "Contraintes environnementales"
    keep = [p for p in parts if not BOILER.search(p)]
    out = keep[0][:80] if keep else ""
    return out or None


def outage_table(msgs, now, days_fwd=NUC_AHEAD_DAYS, min_mw=100):
    rows = []
    horizon = now + dt.timedelta(days=days_fwd)
    for m in msgs:
        mx = max((m["nominal_mw"] - v for _, _, v in m["segs"]), default=0)
        if mx < min_mw or m["end"] < now or m["start"] > horizon:
            continue
        dur_h = (m["end"] - m["start"]).total_seconds() / 3600
        if dur_h < (4 if m["type"] == "forced" else 24):
            continue  # essais et modulations de quelques heures : comptés dans les moyennes, pas listés
        rows.append({"unit": m["unit"], "type": m["type"], "unav_mw": round(mx), "nominal_mw": round(m["nominal_mw"]),
                     "start": m["start"].astimezone(PARIS).strftime("%Y-%m-%dT%H:%M"),
                     "end": m["end"].astimezone(PARIS).strftime("%Y-%m-%dT%H:%M"),
                     "ongoing": m["start"] <= now, "reason": clean_reason(m["reason"])})
    # doublons (même unité, mêmes dates) : garder la plus forte indisponibilité
    best = {}
    for r in rows:
        k = (r["unit"], r["start"][:10], r["end"][:10])
        if k not in best or r["unav_mw"] > best[k]["unav_mw"]:
            best[k] = r
    return sorted(best.values(), key=lambda r: (not r["ongoing"], r["start"], r["unit"]))


# ------------------------------------------------------------------------ RTE
def rte_token():
    cid, cs = secret("RTE_CLIENT_ID"), secret("RTE_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("identifiants RTE absents")
    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    code, _ct, body = http(RTE_BASE + "/token/oauth/", data=b"", method="POST",
                           headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
    if code != 200:
        raise RuntimeError(f"jeton RTE refusé (HTTP {code}) : {body[:200]!r}")
    return json.loads(body)["access_token"]


def rte_get(tok, path, params):
    url = RTE_BASE + path + "?" + urllib.parse.urlencode(params)
    code, _ct, body = http(url, headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
    if code != 200:
        raise RuntimeError(f"HTTP {code} sur {path} : {body[:240]!r}")
    return json.loads(body)


def rte_date(d: dt.date) -> str:
    return dt.datetime(d.year, d.month, d.day, tzinfo=PARIS).isoformat()


def rte_values_daily(values):
    segs = []
    for v in values or []:
        if v.get("value") is None:
            continue
        a, b = parse_time(v["start_date"]), parse_time(v["end_date"])
        segs.append((a, b, float(v["value"]), b - a))
    return daily_mean(segs, min_hours=22.0)


def rte_consumption(tok, today):
    out = {}  # date -> (gw, label)
    rank = {"D-1": 1, "D-2": 2, "WEEKLY": 3}
    try:
        j = rte_get(tok, "/open_api/consumption/v1/short_term",
                    {"type": "D-1,D-2", "start_date": rte_date(today), "end_date": rte_date(today + dt.timedelta(days=3))})
        for blk in j.get("short_term", []):
            typ = blk.get("type")
            for d, mw in rte_values_daily(blk.get("values")).items():
                if typ in rank and (d not in out or rank[typ] < rank[out[d][1]]):
                    out[d] = (mw / 1000, typ)
        log("RTE consommation J/J+1", True, f"{len(out)} jours")
    except Exception as e:
        log("RTE consommation J/J+1", False, str(e))
    try:
        j = rte_get(tok, "/open_api/consumption/v1/weekly_forecasts",
                    {"start_date": rte_date(today), "end_date": rte_date(today + dt.timedelta(days=10))})
        n = 0
        blocks = j.get("weekly_forecasts", [])
        blocks = sorted(blocks, key=lambda x: x.get("updated_date", ""))  # la plus récente écrase
        for blk in blocks:
            for d, mw in rte_values_daily(blk.get("values")).items():
                if d not in out or rank[out[d][1]] >= rank["WEEKLY"]:
                    out[d] = (mw / 1000, "WEEKLY"); n += 1
        log("RTE consommation hebdomadaire", True, f"{n} jours")
    except Exception as e:
        log("RTE consommation hebdomadaire", False, str(e))
    return out


def rte_generation_forecast(tok, today):
    res = defaultdict(dict)  # kind -> date -> (gw, type)
    rank = {"D-1": 1, "D-2": 2, "D-3": 3}
    kinds = {"SOLAR": "solar", "WIND_ONSHORE": "wind_on", "WIND_OFFSHORE": "wind_off"}
    for pt, k in kinds.items():
        for typ in ("D-1", "D-2", "D-3"):
            try:
                j = rte_get(tok, "/open_api/generation_forecast/v2/forecasts",
                            {"production_type": pt, "type": typ, "start_date": rte_date(today),
                             "end_date": rte_date(today + dt.timedelta(days=4))})
                for blk in j.get("forecasts", []):
                    for d, mw in rte_values_daily(blk.get("values")).items():
                        cur = res[k].get(d)
                        if cur is None or rank[typ] < rank[cur[1]]:
                            res[k][d] = (mw / 1000, typ)
            except Exception as e:
                log(f"RTE prévision {pt} {typ}", False, str(e))
    log("RTE prévisions éolien/solaire", bool(res), ", ".join(f"{k}: {len(v)} j" for k, v in res.items()))
    return res


# ----------------------------------------------------------------------- OMIP
# OMIP publie chaque jour les prix de référence des futures France base (FFB), égaux aux règlements EEX.
OMIP_URL = "https://www.omip.pt/en/dados-mercado?date={d}&product=EL&zone=FR&instrument=FFB&maturity={m}"
UA = {"User-Agent": "Mozilla/5.0 (brief-energie-data; collecte quotidienne)", "Accept-Language": "en"}


def parse_omip(t: str):
    import re as _re
    dates = _re.findall(r"transparency\?date=(\d{4}-\d{2}-\d{2})", t)
    page_date = max(set(dates), key=dates.count) if dates else None
    rows = []
    for tr in _re.split(r"<tr class=\"(?:odd|even)\"", t)[1:]:
        m = _re.search(r"</span>\s*(FFB [^<]+?)\s*</strong>", tr)
        if not m:
            continue
        last = _re.search(r"Trading last day:</label>\s*(\d{4}-\d{2}-\d{2})", tr)
        vals = _re.findall(r'<td width="">\s*([^<]*?)\s*</td>', tr)

        def num(x):
            x = (x or "").replace(",", "")
            return float(x) if _re.fullmatch(r"-?\d+(\.\d+)?", x) else None
        rows.append({"contract": m.group(1).strip(), "last_day": last.group(1) if last else None,
                     "d": num(vals[9]) if len(vals) > 10 else None, "d1": num(vals[10]) if len(vals) > 10 else None})
    return page_date, rows


def omip_get(d: dt.date, maturity: str):
    code, _ct, body = http(OMIP_URL.format(d=d.isoformat(), m=maturity), headers=UA, timeout=60)
    if code != 200:
        raise RuntimeError(f"OMIP HTTP {code}")
    return parse_omip(body.decode("utf-8", "replace"))


def omip_latest(today: dt.date):
    """Dernière séance publiée : on remonte jour ouvré par jour ouvré jusqu'à trouver une table datée de ce jour."""
    for k in range(0, 8):
        d = today - dt.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        pd_, rows = omip_get(d, "Wk")
        if pd_ == d.isoformat() and any(r["d"] is not None for r in rows):
            return d
        time.sleep(1)
    return None


MONTHS_EN = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def omip_short_term(today: dt.date):
    import re as _re
    sess = omip_latest(today)
    if not sess:
        raise RuntimeError("aucune séance OMIP trouvée sur 8 jours")
    out = {"session": sess.isoformat(), "weeks": [], "weekends": [], "months": [], "years": []}
    for mat in ("Wk", "WE", "M", "YR"):
        pd_, rows = omip_get(sess, mat)
        time.sleep(1)
        for r in rows:
            c = r["contract"]
            if mat == "Wk":
                m = _re.match(r"FFB Wk(\d+)-(\d+)", c)
                if not m:
                    continue
                wk, yy = int(m.group(1)), 2000 + int(m.group(2))
                mon = dt.date.fromisocalendar(yy, wk, 1)
                out["weeks"].append({"label": f"S{wk}", "from": mon.isoformat(), "to": (mon + dt.timedelta(days=6)).isoformat(),
                                     "base": r["d"], "base_d1": r["d1"], "peak": None, "asof": pd_})
            elif mat == "WE":
                m = _re.match(r"FFB WE (\d+)(\w{3})-(\d+)", c)
                if not m:
                    continue
                sat = dt.date(2000 + int(m.group(3)), MONTHS_EN[m.group(2)], int(m.group(1)))
                out["weekends"].append({"label": c.replace("FFB ", ""), "from": sat.isoformat(), "to": (sat + dt.timedelta(days=1)).isoformat(),
                                        "base": r["d"], "base_d1": r["d1"], "peak": None, "asof": pd_})
            elif mat == "M":
                out["months"].append({"contract": c.replace("FFB ", ""), "base": r["d"], "base_d1": r["d1"], "asof": pd_})
            else:
                out["years"].append({"contract": c.replace("FFB ", ""), "base": r["d"], "base_d1": r["d1"], "asof": pd_})
    return out


def omip_year_history(today: dt.date, path: str):
    """Historique quotidien des Cal (YR) depuis le 1er janvier de l'année N-1 ; complété jour par jour."""
    hist = {}
    if os.path.exists(path):
        try:
            hist = json.load(open(path, encoding="utf-8")).get("prices", {})
        except Exception:
            hist = {}
    start = dt.date(today.year - 1, 1, 1)
    d, fetched, budget = start, 0, int(os.environ.get("OMIP_BUDGET", "600"))
    while d <= today and fetched < budget:
        if d.weekday() < 5 and d.isoformat() not in hist:
            try:
                pd_, rows = omip_get(d, "YR")
                fetched += 1
                if pd_ == d.isoformat():
                    vals = {}
                    for r in rows:
                        yy = r["contract"].replace("FFB YR-", "")
                        if yy.isdigit() and r["d"] is not None:
                            vals["cal" + yy] = r["d"]
                    if vals:
                        hist[d.isoformat()] = vals
                time.sleep(0.6)
            except Exception as e:
                log("OMIP historique " + d.isoformat(), False, str(e))
                time.sleep(3)
        d += dt.timedelta(days=1)
    return {"source": "OMIP, prix de référence des futures France base (identiques aux règlements EEX)",
            "asof": max(hist) if hist else None, "prices": {k: hist[k] for k in sorted(hist)}}, fetched


# ----------------------------------------------------------------------- main
def run(today: dt.date | None = None):
    now = dt.datetime.now(UTC)
    today = today or now.astimezone(PARIS).date()
    data = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "today": today.isoformat(), "series": {},
            "forecast": [], "load_outlook": {}, "nuclear": {}, "sources": {}}
    series = defaultdict(dict)

    def step(name, fn, *a):
        try:
            v = fn(*a)
            log(name, True)
            return v
        except Exception as e:
            log(name, False, str(e))
            return None

    prices = step("ENTSO-E prix day-ahead (A44)", get_prices, today) or {}
    for d, arr in prices.items():
        if sum(v is not None for v in arr) >= 23:
            series[d]["spot_h"] = arr
            vals = [v for v in arr if v is not None]
            series[d]["spot_fr"] = round(sum(vals) / len(vals), 2)
    gen = step("ENTSO-E production réalisée par filière (A75)", get_generation, today) or {}
    for k, dm in gen.items():
        for d, mw in dm.items():
            if d < today.isoformat():
                series[d][k] = r1(mw / 1000, 1 if k in ("nuc_gw", "wind_gw", "solar_gw") else 2)
    flows = step("ENTSO-E échanges physiques aux frontières (A11)", get_flows, today) or {}
    days = set(d for v in flows.values() for d in v)
    for d in days:
        if d >= today.isoformat():
            continue
        per = {n: r1(v[d] / 1000, 2) for n, v in flows.items() if d in v}
        if len(per) == len(flows) and per:  # toutes les frontières renseignées ce jour-là
            series[d]["exp_borders"] = per
            series[d]["exp_net_gw"] = r1(sum(per.values()), 2)
    actual_load = step("ENTSO-E consommation réalisée (A65 A16)", get_load, today, "A16", PAST_DAYS, 1)
    if actual_load:
        segs = [s for v in actual_load[0].values() for s in v]
        for d, mw in daily_mean(segs).items():
            if d < today.isoformat():
                series[d]["load_gw"] = r1(mw / 1000, 2)
    data["series"] = {d: series[d] for d in sorted(series)}

    # --- prévisions journalières (J → plus loin possible)
    fc = defaultdict(dict)
    da = step("ENTSO-E prévision conso J/J+1 (A65 A01)", get_load, today, "A01", 0, 3)
    if da:
        for d, mw in daily_mean([s for v in da[0].values() for s in v]).items():
            fc[d]["cons_gw"] = (mw / 1000, "ENTSO-E day-ahead")
    wa = step("ENTSO-E prévision conso semaine (A65 A31)", get_load, today, "A31", 0, 9)
    if wa:
        for row in load_minmax(wa[0]):
            fc[row["from"]].setdefault("cons_min_gw", (row["min_gw"], "ENTSO-E week-ahead"))
            fc[row["from"]].setdefault("cons_max_gw", (row["max_gw"], "ENTSO-E week-ahead"))
        data["load_outlook"]["week_ahead"] = load_minmax(wa[0])
    ma = step("ENTSO-E prévision conso mois (A65 A32)", get_load, today, "A32", 0, 45)
    if ma:
        data["load_outlook"]["month_ahead"] = load_minmax(ma[0])
    ya = step("ENTSO-E prévision conso année (A65 A33)", get_load, today, "A33", 0, 365, 0)
    if ya:
        data["load_outlook"]["year_ahead"] = load_minmax(ya[0])
    ws = step("ENTSO-E prévision éolien/solaire (A69)", get_ws_forecast, today) or {}
    for k, dm in ws.items():
        for d, mw in dm.items():
            if d >= today.isoformat():
                key = {"nuc": "_nuc", "solar": "solar_gw", "wind_on": "_won", "wind_off": "_woff"}.get(k)
                if key:
                    fc[d][key] = (mw / 1000, "ENTSO-E day-ahead")

    rte_ok = bool(secret("RTE_CLIENT_ID") and secret("RTE_CLIENT_SECRET"))
    if rte_ok:
        tok = step("RTE jeton OAuth", rte_token)
        if tok:
            for d, (gw, typ) in rte_consumption(tok, today).items():
                if d >= today.isoformat():
                    fc[d]["cons_gw"] = (gw, f"RTE {typ}")
            gf = rte_generation_forecast(tok, today)
            for k, dm in gf.items():
                for d, (gw, typ) in dm.items():
                    if d >= today.isoformat():
                        key = {"solar": "solar_gw", "wind_on": "_won", "wind_off": "_woff"}[k]
                        fc[d][key] = (gw, f"RTE {typ}")
    else:
        log("RTE", False, "identifiants absents, étape ignorée")

    # --- nucléaire
    installed = step("ENTSO-E capacité nucléaire installée (A68)", get_installed_nuclear, today)
    msgs = []
    for doc_type, label in (("A80", "unités de production (A80)"), ("A77", "groupes de production (A77)")):
        docs = step(f"ENTSO-E indisponibilités {label}", get_unavailability, doc_type, today, PAST_DAYS, NUC_AHEAD_DAYS)
        if docs:
            parsed = parse_unavailability(docs)
            log(f"  nucléaire {doc_type}", True, f"{len(docs)} messages, {len(parsed)} séries nucléaires")
            if parsed:
                msgs = parsed
                break
    actual_nuc = {d: v["nuc_gw"] * 1000 for d, v in data["series"].items() if v.get("nuc_gw") is not None}
    if msgs:
        daily = nuclear_availability(msgs, installed, today, actual_nuc)
        outages = outage_table(msgs, now)
        far = max((m["end"] for m in msgs), default=None)
        data["nuclear"] = {
            "asof": data["generated_at"], "installed_gw": r1(installed / 1000, 2) if installed else None,
            "daily": daily, "outages": outages, "n_units": len({m["unit"] for m in msgs}),
            "horizon_end": far.astimezone(PARIS).date().isoformat() if far else None,
            "window_end": (today + dt.timedelta(days=NUC_AHEAD_DAYS)).isoformat(),
            "source": "ENTSO-E Transparency Platform, indisponibilités des unités de production (données RTE/EDF)",
        }
        for row in daily:
            if row["date"] >= today.isoformat() and row.get("avail_gw") is not None:
                fc[row["date"]]["nuc_avail_gw"] = (row["avail_gw"], "ENTSO-E indisponibilités")

    # --- assemblage des prévisions
    rows = []
    for d in sorted(fc):
        if d < today.isoformat():
            continue
        f = fc[d]
        if "_won" in f or "_woff" in f:
            won, woff = f.pop("_won", (0.0, "")), f.pop("_woff", (0.0, ""))
            f["wind_gw"] = (won[0] + woff[0], won[1] or woff[1])
        row = {"date": d, "src": {}}
        for k, (v, s) in f.items():
            if v is None:
                continue
            row[k] = r1(v, 2)
            row["src"][k] = s
        if len(row) > 2:
            rows.append(row)
    data["forecast"] = rows
    # --- OMIP : semaines, week-ends, mois, années (dernière séance) + historique des Cal
    data["omip"] = step("OMIP séance la plus récente (semaines, week-ends, mois, années)", omip_short_term, today) or {}
    fw = step("OMIP historique des Cal", omip_year_history, today, os.path.join(OUT_DIR, "forwards.json"))
    if fw:
        data["_forwards"], n_new = fw
        log("  OMIP historique", True, f"{n_new} séances lues, {len(fw[0]['prices'])} au total")
    data["status"] = STATUS
    data["diag"] = DIAG
    return data


def main():
    data = run()
    os.makedirs(OUT_DIR, exist_ok=True)
    fw = data.pop("_forwards", None)
    if fw:
        with open(os.path.join(OUT_DIR, "forwards.json"), "w", encoding="utf-8") as f:
            json.dump(fw, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT_DIR, "brief.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": data["generated_at"], "steps": STATUS}, f, ensure_ascii=False, indent=1)
    fails = [s for s in STATUS if not s["ok"]]
    print(f"\n{len(STATUS) - len(fails)} étapes OK, {len(fails)} en échec")
    # code 0 tant qu'au moins les prix ou le nucléaire sont sortis
    if not data["series"] and not data["nuclear"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
