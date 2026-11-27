"""Radiosää: oma tropoarvio (VHF/UHF) ja revontuliovaali.

Tropoarvio lasketaan Open-Meteon painepintaennusteesta (lämpötila, suhteellinen kosteus ja geopotentiaalikorkeus
1000/925/850 hPa sekä 2 m:n arvot ja pintapaine) harvassa ruudukossa kodin ympärillä. Kustakin pisteestä
lasketaan refraktiivisuus N = 77,6·P/T + 3,73·10⁵·e/T² ja sen pystygradientti alimmissa kerroksissa:
  dN/dh > −60 N/km  normaali (0)
  −60 … −90         kohonnut (1)
  −90 … −157        voimakas superrefraktio (2)
  < −157            kanavointi todennäköinen (3)   (dM/dh < 0)
Painepinnat ovat satojen metrien välein, joten ohuet inversiot tasoittuvat: arvio on suuntaa antava eikä vastaa
esimerkiksi Hepburnin indeksiä. Open-Meteo: ilmainen ei-kaupalliseen käyttöön, data CC BY 4.0.

Revontuliovaali: NOAA SWPC OVATION (ovation_aurora_latest.json, 1° ruudukko) karkeistettuna 2°:een.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from .i18n import tr

log = logging.getLogger("bmmonitor.tropo")

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OVATION = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
REFRESH = 3 * 3600          # tropoennusteen haku (malliajot päivittyvät muutaman tunnin välein)
OVATION_REFRESH = 600
LEVELS = (1000, 925, 850)
GRID_N = 7                  # 7 × 7 pistettä
STEP_LAT, STEP_LON = 1.5, 3.0
THRESH = (-60.0, -90.0, -157.0)


def sat_vp(t_c: float) -> float:
    """Kylläisen vesihöyryn paine (hPa), Magnus."""
    return 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))


def refractivity(t_c: float, rh: float, p_hpa: float) -> float:
    tk = t_c + 273.15
    e = sat_vp(t_c) * max(0.0, min(100.0, rh)) / 100.0
    return 77.6 * p_hpa / tk + 3.73e5 * e / (tk * tk)


def index_of(grad: float | None) -> int:
    if grad is None:
        return 0
    return 3 if grad < THRESH[2] else 2 if grad < THRESH[1] else 1 if grad < THRESH[0] else 0


def min_gradient(layers: list[tuple[float, float]]) -> float | None:
    """[(korkeus m, N), …] alhaalta ylös → jyrkin (pienin) dN/dh N/km."""
    best = None
    for (h1, n1), (h2, n2) in zip(layers, layers[1:]):
        if h2 - h1 < 20:
            continue
        g = (n2 - n1) / ((h2 - h1) / 1000.0)
        best = g if best is None else min(best, g)
    return best


def point_profile(h: dict, i: int, elev: float) -> list[tuple[float, float]]:
    """Yhden tunnin kerrokset: pinta (2 m) ja maanpinnan yläpuolella olevat painepinnat."""
    try:
        ps = h["surface_pressure"][i]
        out = [(elev + 2.0, refractivity(h["temperature_2m"][i], h["relative_humidity_2m"][i], ps))]
    except (KeyError, IndexError, TypeError):
        return []
    for p in LEVELS:
        try:
            t, rh, z = h[f"temperature_{p}hPa"][i], h[f"relative_humidity_{p}hPa"][i], h[f"geopotential_height_{p}hPa"][i]
        except (KeyError, IndexError):
            continue
        if None in (t, rh, z) or p >= ps - 3 or z <= out[-1][0]:
            continue                         # painepinta maan alla tai liian lähellä pintaa
        out.append((z, refractivity(t, rh, p)))
    return out


def grid_points(lat: float, lon: float) -> list[tuple[float, float]]:
    half = GRID_N // 2
    pts = []
    for i in range(GRID_N):
        for j in range(GRID_N):
            la = round(lat + (i - half) * STEP_LAT, 2)
            if -85 < la < 85:
                lo = round(((lon + (j - half) * STEP_LON + 180) % 360) - 180, 2)
                pts.append((la, lo))
    return pts


def parse_forecast(data, pts) -> dict:
    """Open-Meteon vastaus (lista tai yksi olio) → {times: [...], cells: [{lat, lon, idx: [...], grad: [...]}]}."""
    items = data if isinstance(data, list) else [data]
    times: list[int] = []
    cells = []
    for (la, lo), d in zip(pts, items):
        h = (d or {}).get("hourly") or {}
        if not times:
            times = [int(t) for t in h.get("time") or []]
        elev = float(d.get("elevation") or 0.0)
        grads, idx = [], []
        for i in range(len(h.get("time") or [])):
            g = min_gradient(point_profile(h, i, elev))
            grads.append(round(g, 1) if g is not None else None)
            idx.append(index_of(g))
        cells.append({"lat": la, "lon": lo, "idx": idx, "grad": grads})
    return {"times": times, "cells": cells}


def ovation_cells(data: dict, step: int = 2, min_prob: int = 3) -> list[list[int]]:
    """OVATION 1°-ruudukko → [[lat, lon, %], …] step°-ruuduissa (suurin arvo), vain ≥ min_prob."""
    acc: dict = {}
    for c in (data or {}).get("coordinates") or []:
        try:
            lon, lat, p = c[0], c[1], c[2]
        except (TypeError, IndexError):
            continue
        if p < min_prob:
            continue
        lon = lon - 360 if lon >= 180 else lon
        k = (int(lat // step) * step, int(lon // step) * step)
        acc[k] = max(acc.get(k, 0), int(p))
    return [[k[0], k[1], v] for k, v in sorted(acc.items())]


class TropoFeed:
    def __init__(self, mon, demo: bool = False):
        self.mon = mon
        self.demo = demo
        self.fc: dict | None = None
        self.fc_home = None
        self.state = {"ok": None, "error": None, "updated": None}
        self.oval: list = []
        self.oval_state = {"ok": None, "error": None, "updated": None, "forecast": None}
        self._wake = asyncio.Event()
        self.alerted: dict = {}

    def home(self):
        loc = self.mon.cfg["services"]["location"]
        return (loc["lat"], loc["lon"]) if loc.get("lat") is not None else None

    @property
    def cfg(self) -> dict:
        return self.mon.cfg["propagation"]

    def reconfigure(self):
        self._wake.set()

    async def _fetch_forecast(self, home):
        from .services import fetch_json
        pts = grid_points(round(home[0], 1), round(home[1], 1))
        vars_ = ["temperature_2m", "relative_humidity_2m", "surface_pressure"] + \
            [f"{v}_{p}hPa" for p in LEVELS for v in ("temperature", "relative_humidity", "geopotential_height")]
        url = (f"{OPEN_METEO}?latitude={','.join(str(p[0]) for p in pts)}&longitude={','.join(str(p[1]) for p in pts)}"
               f"&hourly={','.join(vars_)}&forecast_days=3&timeformat=unixtime&timezone=UTC")
        data = await fetch_json(url, 60)
        return parse_forecast(data, pts)

    def _demo_forecast(self, home):
        pts = grid_points(round(home[0], 1), round(home[1], 1))
        t0 = int(time.time() // 3600 * 3600)
        times = [t0 + 3600 * i for i in range(72)]
        cells = []
        for la, lo in pts:
            idx = []
            for i in range(72):
                v = math.sin((la - home[0]) / 3 + i / 9) + math.cos((lo - home[1]) / 6 - i / 14) + (0.8 if la < home[0] else 0)
                idx.append(max(0, min(3, int(v + 1.2))))
            cells.append({"lat": la, "lon": lo, "idx": idx, "grad": [None] * 72})
        return {"times": times, "cells": cells}

    async def run(self):
        last_fc = last_ov = 0.0
        while True:
            self._wake.clear()
            home = self.home()
            if self.cfg.get("enabled") and home:
                now = time.time()
                if self.fc_home != (round(home[0], 1), round(home[1], 1)):
                    last_fc = 0.0
                if now - last_fc > REFRESH:
                    try:
                        self.fc = self._demo_forecast(home) if self.demo else await self._fetch_forecast(home)
                        self.fc_home = (round(home[0], 1), round(home[1], 1))
                        self.state = {"ok": True, "error": None, "updated": now}
                        last_fc = now
                        self.check_alert()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        from .messaging import friendly_error
                        self.state = {"ok": False, "error": friendly_error(e), "updated": self.state.get("updated")}
                        log.info("Tropo: %s", e)
                        last_fc = now - REFRESH + 600          # uusi yritys 10 min päästä
                    self.mon.dirty = True
                if now - last_ov > OVATION_REFRESH and not self.demo:
                    try:
                        from .services import fetch_json
                        data = await fetch_json(OVATION, 60)
                        self.oval = ovation_cells(data)
                        self.oval_state = {"ok": True, "error": None, "updated": now, "forecast": data.get("Forecast Time")}
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        from .messaging import friendly_error
                        self.oval_state = {**self.oval_state, "ok": False, "error": friendly_error(e)}
                    last_ov = now
                elif self.demo and not self.oval:
                    self.oval = [[la, lo, int(max(0, 40 - abs(la - 67) * 8))] for la in range(60, 76, 2)
                                 for lo in range(-180, 180, 2) if abs(la - 67) < 5]
                    self.oval_state = {"ok": True, "error": None, "updated": time.time(), "forecast": None}
            try:
                await asyncio.wait_for(self._wake.wait(), 300)
            except asyncio.TimeoutError:
                pass

    # ---------------- tulokset
    def hour_index(self, offset_h: int = 0) -> int:
        if not self.fc or not self.fc["times"]:
            return 0
        target = time.time() + offset_h * 3600
        ts = self.fc["times"]
        return min(range(len(ts)), key=lambda i: abs(ts[i] - target))

    def home_cell(self):
        if not self.fc or not self.fc["cells"]:
            return None
        h = self.home() or self.fc_home
        return min(self.fc["cells"], key=lambda c: (c["lat"] - h[0]) ** 2 + ((c["lon"] - h[1]) * 0.5) ** 2)

    def outlook(self) -> list[dict]:
        """Kotipisteen suurin indeksi 6 tunnin jaksoissa seuraavat 72 h."""
        c = self.home_cell()
        if not c:
            return []
        ts, out = self.fc["times"], []
        start = self.hour_index(0)
        for b in range(start, len(ts), 6):
            seg = c["idx"][b:b + 6]
            if seg:
                out.append({"t": ts[b], "idx": max(seg)})
        return out

    def check_alert(self):
        """Hälytys, kun kotipisteen tropoarvio nousee seuraavan 12 h aikana voimakkaaksi tai kanavoinniksi."""
        if not self.cfg.get("alert_tropo"):
            return
        c = self.home_cell()
        if not c:
            return
        i0 = self.hour_index(0)
        seg = [(self.fc["times"][i], c["idx"][i]) for i in range(i0, min(i0 + 12, len(c["idx"])))]
        hits = [x for x in seg if x[1] >= 2]
        if not hits:
            return
        t, lvl = max(hits, key=lambda x: x[1])
        day = time.strftime("%Y%m%d", time.gmtime(t))
        if self.alerted.get(day, -1) >= lvl:
            return
        self.alerted[day] = lvl
        word = tr("kanavointi todennäköinen") if lvl == 3 else tr("voimakas")
        hhmm = time.strftime("%H:%M", time.localtime(t))
        push_alert(self.mon, tr("Tropoarvio: {w}", w=word),
                   tr("VHF/UHF-tropokeli voi parantua noin klo {t} alkaen (oma arvio Open-Meteon ennusteesta).", t=hhmm),
                   tr("Tropo {w} klo {t}", w=word, t=hhmm) if self.cfg.get("sms") else None)

    def query(self, offset_h: int = 0) -> dict:
        i = self.hour_index(offset_h)
        cells = [{"lat": c["lat"], "lon": c["lon"], "idx": c["idx"][i] if i < len(c["idx"]) else 0,
                  "grad": c["grad"][i] if i < len(c["grad"]) else None} for c in (self.fc or {}).get("cells", [])]
        hc = self.home_cell()
        return {"time": (self.fc or {}).get("times", [None])[i] if self.fc and self.fc["times"] else None,
                "cells": cells, "step": [STEP_LAT, STEP_LON], "state": self.state,
                "home_now": hc["idx"][self.hour_index(0)] if hc else None, "outlook": self.outlook(),
                "oval": self.oval, "oval_state": self.oval_state}


def push_alert(mon, title: str, body: str, sms: str | None, level: str = "info"):
    """Radiosään hälytys samaan hälytyslistaan kuin palveluiden hälytykset."""
    w = mon.watcher
    w.seq += 1
    w.alerts.appendleft({"seq": w.seq, "time": time.time(), "kind": "palvelu", "service": "radiosaa",
                         "service_title": tr("Radiosää"), "title": title, "body": body, "level": level,
                         "call": "", "tg": None, "via": ""})
    if sms:
        mon.hub.announce("", sms, source="radiosaa")
    mon.touch()
