"""Satelliittien ylilennot oman sijainnin yli.

- Ratatiedot: CelesTrak, radioamatöörisatelliitit (TLE), haetaan kerran vuorokaudessa.
- Taajuudet: SatNOGS DB:n lähetintietokanta (CC BY-SA), haetaan kerran vuorokaudessa.
- Ratalaskenta: SGP4 (sgp4-kirjasto). Paikka TEME-koordinaateissa -> maahan kiinnitetty (GMST-kierto)
  -> paikallinen horisonttikoordinaatisto (atsimuutti, korkeuskulma).
- "Aktiiviset" satelliitit: amatööriryhmän satelliitit, joilla on SatNOGSissa toimiva lähetin-vastaanotin
  tai transponderi (ylös- ja alaslinkki radioamatöörialueilla). ISS on aina mukana.
- ISS:n näkyvä ylilento: ISS auringon valaisema, havaitsija hämärässä (aurinko alle -6°) ja korkeus >= 10°.

Tarkkuus on harrastuskäyttöön riittävä (ajat noin ±10 s): napaliikettä ja nutaatiota ei huomioida.
"""
from __future__ import annotations

import calendar
import json
import logging
import math
import time
from pathlib import Path

log = logging.getLogger("bmmonitor.satellites")

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle"
TX_URL = "https://db.satnogs.org/api/transmitters/?format=json"
ISS = 25544
REFRESH = 86400          # ratatiedot ja taajuudet kerran vuorokaudessa
HAM_BANDS = ((28e6, 29.7e6), (144e6, 146e6), (430e6, 440e6), (1240e6, 1300e6), (2400e6, 2450e6),
             (10450e6, 10500e6))
RE = 6378.137            # WGS84
F = 1 / 298.257223563


def _ham(f) -> bool:
    return bool(f) and any(a <= f <= b for a, b in HAM_BANDS)


def parse_tle(text: str) -> dict[int, dict]:
    """3-rivinen TLE -> {norad: {name, l1, l2, epoch_age_days, mean_motion}}."""
    lines = [x.rstrip() for x in text.splitlines() if x.strip()]
    out = {}
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue
        try:
            norad = int(l1[2:7])
            yy, doy = int(l1[18:20]), float(l1[20:32])
            year = 2000 + yy if yy < 57 else 1900 + yy
            epoch = calendar.timegm((year, 1, 1, 0, 0, 0)) + (doy - 1) * 86400
            out[norad] = {"name": name, "l1": l1, "l2": l2, "epoch": epoch, "mean_motion": float(l2[52:63])}
        except ValueError:
            pass
        i += 3
    return out


def short_name(name: str) -> str:
    """'SAUDISAT 1C (SO-50)' -> 'SO-50', 'ISS (ZARYA)' -> 'ISS'."""
    if name.startswith("ISS"):
        return "ISS"
    if "(" in name and name.endswith(")"):
        inner = name[name.rfind("(") + 1:-1]
        if inner and len(inner) <= 12:
            return inner
    return name


def pick_transmitters(tx: list[dict], norads: set[int]) -> dict[int, list[dict]]:
    """SatNOGS-lähettimistä radioamatöörin käytettävät: toimiva ja aktiivinen lähetin-vastaanotin tai
    transponderi, jonka ylös- ja alaslinkki ovat amatöörialueilla."""
    out: dict[int, list[dict]] = {}
    for t in tx:
        n = t.get("norad_cat_id")
        if n not in norads or not t.get("alive") or t.get("status") != "active":
            continue
        if t.get("type") not in ("Transceiver", "Transponder"):
            continue
        if not (_ham(t.get("uplink_low")) and _ham(t.get("downlink_low"))):
            continue
        out.setdefault(n, []).append({
            "desc": str(t.get("description") or "")[:60], "mode": str(t.get("mode") or ""),
            "up": t.get("uplink_low"), "down": t.get("downlink_low"), "type": t.get("type")})
    for lst in out.values():   # FM ensin, sitten muut
        lst.sort(key=lambda x: (x["mode"] != "FM", x["mode"] not in ("USB", "LSB", "CW"), x["desc"]))
    return out


def freq_text(t: dict) -> str:
    """'FM 145,990↑ 437,800↓'."""
    f = lambda v: f"{v / 1e6:.3f}".replace(".", ",")  # noqa: E731
    mode = "APRS" if "APRS" in t["desc"].upper() else ("Lineaari" if t["type"] == "Transponder" and t["mode"] != "FM"
                                                       else t["mode"])
    up, down = t["up"], t["down"]
    return f"{mode} {f(up)}" if up == down else f"{mode} {f(up)}↑ {f(down)}↓"


# --------------------------------------------------------------------------
# Ratalaskenta
# --------------------------------------------------------------------------
def _jd(ts: float) -> tuple[float, float]:
    jd = ts / 86400.0 + 2440587.5
    whole = math.floor(jd - 0.5) + 0.5
    return whole, jd - whole


def gmst(ts: float) -> float:
    """Greenwichin tähtiaika radiaaneina (IAU 1982)."""
    jd = ts / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    g = 67310.54841 + (876600.0 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t ** 3
    return math.radians((g % 86400.0) / 240.0)


def observer_ecef(lat: float, lon: float, h_km: float = 0.0):
    la, lo = math.radians(lat), math.radians(lon)
    e2 = F * (2 - F)
    n = RE / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return ((n + h_km) * math.cos(la) * math.cos(lo), (n + h_km) * math.cos(la) * math.sin(lo),
            (n * (1 - e2) + h_km) * math.sin(la))


def look_angles(r_teme, ts: float, lat: float, lon: float, obs=None) -> tuple[float, float, float]:
    """(korkeuskulma°, atsimuutti°, etäisyys km)."""
    g = gmst(ts)
    cg, sg = math.cos(g), math.sin(g)
    x = cg * r_teme[0] + sg * r_teme[1]
    y = -sg * r_teme[0] + cg * r_teme[1]
    z = r_teme[2]
    ox, oy, oz = obs or observer_ecef(lat, lon)
    dx, dy, dz = x - ox, y - oy, z - oz
    la, lo = math.radians(lat), math.radians(lon)
    sla, cla, slo, clo = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    e = -slo * dx + clo * dy
    n = -sla * clo * dx - sla * slo * dy + cla * dz
    u = cla * clo * dx + cla * slo * dy + sla * dz
    rng = math.sqrt(e * e + n * n + u * u)
    return math.degrees(math.atan2(u, math.hypot(e, n))), (math.degrees(math.atan2(e, n)) + 360) % 360, rng


def sun_vector(ts: float) -> tuple[float, float, float]:
    """Auringon suuntavektori (päiväntasaajakoordinaatit, yksikkövektori)."""
    n = ts / 86400.0 + 2440587.5 - 2451545.0
    lm = math.radians((280.460 + 0.9856474 * n) % 360)
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = lm + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    return math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam)


def sunlit(r_teme, ts: float) -> bool:
    """Onko satelliitti auringon valaisema (lieriömäinen maan varjo)."""
    s = sun_vector(ts)
    p = r_teme[0] * s[0] + r_teme[1] * s[1] + r_teme[2] * s[2]
    if p > 0:
        return True
    perp = [r_teme[i] - p * s[i] for i in range(3)]
    return math.sqrt(sum(c * c for c in perp)) > RE


def sun_elev(ts: float, lat: float, lon: float) -> float:
    s = sun_vector(ts)
    return look_angles((s[0] * 1.496e8, s[1] * 1.496e8, s[2] * 1.496e8), ts, lat, lon)[0]


class Orbit:
    def __init__(self, norad: int, tle: dict):
        from sgp4.api import Satrec
        self.norad, self.name = norad, tle["name"]
        self.sat = Satrec.twoline2rv(tle["l1"], tle["l2"])

    def pos(self, ts: float):
        jd, fr = _jd(ts)
        err, r, _v = self.sat.sgp4(jd, fr)
        return None if err else r


def find_passes(orb: Orbit, lat: float, lon: float, start: float, hours: float = 24, step: float = 30,
                visible: bool = False) -> list[dict]:
    """Ylilennot aikavälillä. Nousu ja lasku haetaan puolitushaulla, korkein kohta kultaisella leikkauksella."""
    obs = observer_ecef(lat, lon)

    def el(ts):
        r = orb.pos(ts)
        return -90.0 if r is None else look_angles(r, ts, lat, lon, obs)[0]

    def cross(a, b, rising):   # el(a) ja el(b) eri puolilla nollaa
        for _ in range(16):
            m = (a + b) / 2
            if (el(m) > 0) == rising:
                b = m
            else:
                a = m
        return (a + b) / 2

    out = []
    end = start + hours * 3600
    t = start
    prev = el(t)
    aos = start if prev > 0 else None
    while t < end:
        t2 = t + step
        e2 = el(t2)
        if prev <= 0 < e2:
            aos = cross(t, t2, True)
        elif prev > 0 >= e2 and aos is not None:
            los = cross(t, t2, False)
            out.append(_describe(orb, aos, los, lat, lon, obs, visible))
            aos = None
        prev, t = e2, t2
    return out


def _describe(orb, aos, los, lat, lon, obs, visible) -> dict:
    def la(ts):
        r = orb.pos(ts)
        return look_angles(r, ts, lat, lon, obs), r
    a, b = aos, los
    gr = (math.sqrt(5) - 1) / 2
    for _ in range(30):     # korkein kohta
        c, d = b - gr * (b - a), a + gr * (b - a)
        if la(c)[0][0] > la(d)[0][0]:
            b = d
        else:
            a = c
    tca = (a + b) / 2
    (el_max, az_max, _), _ = la(tca)
    p = {"norad": orb.norad, "name": short_name(orb.name), "aos": round(aos), "los": round(los),
         "tca": round(tca), "max_el": round(el_max), "aos_az": round(la(aos)[0][1]), "tca_az": round(az_max),
         "los_az": round(la(los)[0][1]), "visible": None}
    if visible:
        vis = []
        ts = aos
        while ts <= los:
            (e, _az, _), r = la(ts)
            if e >= 10 and sunlit(r, ts) and sun_elev(ts, lat, lon) < -6:
                vis.append((ts, e))
            ts += 10
        if vis:
            p["visible"] = {"start": round(vis[0][0]), "end": round(vis[-1][0]), "max_el": round(max(e for _, e in vis))}
    return p


# --------------------------------------------------------------------------
# Tietojen haku ja välimuisti
# --------------------------------------------------------------------------
class SatData:
    """Ratatiedot ja taajuudet välimuistissa (asetuskansiossa), päivitys kerran vuorokaudessa."""

    def __init__(self, cache: Path | None):
        self.cache = cache
        self.tle: dict[int, dict] = {}
        self.tx: dict[int, list[dict]] = {}
        self.fetched = 0.0
        if cache and cache.exists():
            try:
                d = json.loads(cache.read_text(encoding="utf-8"))
                self.tle = {int(k): v for k, v in d.get("tle", {}).items()}
                self.tx = {int(k): v for k, v in d.get("tx", {}).items()}
                self.fetched = float(d.get("fetched", 0))
            except (OSError, ValueError) as e:
                log.warning("Satelliittivälimuisti: %s", e)

    def stale(self) -> bool:
        return not self.tle or time.time() - self.fetched > REFRESH

    def update(self, tle_text: str, tx_json: list[dict]):
        tle = parse_tle(tle_text)
        if not tle:
            raise ValueError("TLE")
        self.tle = tle
        self.tx = pick_transmitters(tx_json, set(tle))
        self.fetched = time.time()
        if self.cache:
            try:
                self.cache.write_text(json.dumps({"fetched": self.fetched, "tle": self.tle, "tx": self.tx}),
                                      encoding="utf-8")
            except OSError as e:
                log.warning("Satelliittivälimuisti: %s", e)

    def active(self) -> list[int]:
        """ISS ja satelliitit, joilla on käytettävä lähetin-vastaanotin tai transponderi.
        Geostationaariset (esim. QO-100) ja yli 30 vrk vanhat ratatiedot jätetään pois."""
        now = time.time()
        ids = {ISS} | set(self.tx)
        return sorted(n for n in ids if n in self.tle and self.tle[n]["mean_motion"] > 2
                      and now - self.tle[n]["epoch"] < 30 * 86400)
