"""Tilauspalvelut: salamahälytys, säävaroitukset, avaruussää ja tieliikennetiedotteet.

Jokainen palvelu on erikseen päälle/pois kytkettävä. Uudet tapahtumat näkyvät
Hälytykset-listassa ja ilmoituksina, ja ne voi lähettää tekstiviestinä radioon.

Lähteet:
- Salamat:      FMI avoin data, WFS fmi::observations::lightning::simple
- Varoitukset:  FMI CAP-syöte https://alerts.fmi.fi/cap/feed/atom_fi-FI.xml
- Avaruussää:   NOAA SWPC planetary_k_index_1m.json ja alerts.json
- Tieliikenne:  Digitraffic tie.digitraffic.fi/api/traffic-message/v1/messages
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .i18n import bearing, num, tr
from .messaging import friendly_error, http_session

log = logging.getLogger("bmmonitor.services")

PROVINCES = ["Ahvenanmaa", "Etelä-Karjala", "Etelä-Pohjanmaa", "Etelä-Savo", "Kainuu", "Kanta-Häme",
             "Keski-Pohjanmaa", "Keski-Suomi", "Kymenlaakso", "Lappi", "Pirkanmaa", "Pohjanmaa",
             "Pohjois-Karjala", "Pohjois-Pohjanmaa", "Pohjois-Savo", "Päijät-Häme", "Satakunta",
             "Uusimaa", "Varsinais-Suomi"]
TRAFFIC_TYPES = {"TRAFFIC_ANNOUNCEMENT": "Liikennetiedotteet (onnettomuudet, häiriöt)",
                 "ROAD_WORK": "Tietyöt",
                 "WEIGHT_RESTRICTION": "Painorajoitukset",
                 "EXEMPTED_TRANSPORT": "Erikoiskuljetukset"}
COLORS = {"yellow": 1, "orange": 2, "red": 3}

DEFAULT_SERVICES = {
    "location": {"lat": None, "lon": None, "label": ""},
    "lightning": {"enabled": False, "radius_km": 30, "cooldown_min": 30, "ground_only": False, "sms": False},
    "warnings": {"enabled": False, "min_color": "yellow", "traffic_weather": True, "sms": False},
    "space": {"enabled": False, "kp_threshold": 5, "forecasts": True, "flares": True, "sms": False},
    "aurora": {"enabled": False, "threshold_nt": 100, "dark_only": True, "skip_overcast": False, "sms": False},
    "radiation": {"enabled": False, "threshold": 0.4, "radius_km": 100, "sms": False},
    "traffic": {"enabled": False, "use_location": True, "radius_km": 30, "provinces": [], "roads": [],
                "types": ["TRAFFIC_ANNOUNCEMENT"], "updates": False, "details": False, "sms": False},
}


# --------------------------------------------------------------------------
# Paikkatieto
# --------------------------------------------------------------------------
def locator_to_latlon(loc: str) -> tuple[float, float]:
    loc = loc.strip().upper()
    if not re.fullmatch(r"[A-R]{2}\d{2}([A-X]{2}(\d{2})?)?", loc):
        raise ValueError(tr("Virheellinen lokaattori (esim. KP20LE)"))
    lon = (ord(loc[0]) - 65) * 20 - 180
    lat = (ord(loc[1]) - 65) * 10 - 90
    lon += int(loc[2]) * 2
    lat += int(loc[3])
    w, h = 2.0, 1.0
    if len(loc) >= 6:
        w, h = 2 / 24, 1 / 24
        lon += (ord(loc[4]) - 65) * w
        lat += (ord(loc[5]) - 65) * h
    if len(loc) == 8:
        w, h = w / 10, h / 10
        lon += int(loc[6]) * w
        lat += int(loc[7]) * h
    return round(lat + h / 2, 5), round(lon + w / 2, 5)


def latlon_to_locator(lat: float, lon: float) -> str:
    lon += 180
    lat += 90
    s = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    s += str(int((lon % 20) // 2)) + str(int(lat % 10))
    s += chr(97 + int((lon % 2) * 12)) + chr(97 + int((lat % 1) * 24))
    return s[:4] + s[4:].upper()


def parse_location(text: str) -> tuple[float, float]:
    t = (text or "").strip()
    m = re.fullmatch(r"(-?\d+(?:[.,]\d+)?)\s*[,; ]\s*(-?\d+(?:[.,]\d+)?)", t)
    if m:
        lat, lon = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(tr("Koordinaatit eivät ole sallitulla alueella"))
        return lat, lon
    return locator_to_latlon(t)


def distance_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_name(lat1, lon1, lat2, lon2) -> str:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    b = (math.degrees(math.atan2(y, x)) + 360) % 360
    return bearing(int((b + 22.5) // 45))


def point_in_polygon(lat, lon, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _iso_to_ts(s) -> float | None:
    if not s:
        return None
    try:
        s = str(s).replace("Z", "+00:00")
        if re.fullmatch(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(\.\d+)?", s):
            s += "+00:00"
        return datetime.fromisoformat(s.replace(" ", "T")).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------
class Service:
    name = ""
    title = ""
    interval = 300

    def __init__(self, hub: "ServiceHub"):
        self.hub = hub
        self.status: dict = {"checked": None, "error": None, "last_event": None}
        self.wake = asyncio.Event()
        self.primed = False

    @property
    def cfg(self) -> dict:
        return self.hub.cfg[self.name]

    @property
    def loc(self):
        loc = self.hub.cfg["location"]
        return (loc["lat"], loc["lon"]) if loc.get("lat") is not None else None

    def reset(self):
        """Asetukset muuttuivat – seuraava haku vain merkitsee nykytilan nähdyksi."""
        self.primed = False
        self.wake.set()

    async def run(self):
        while True:
            if self.cfg.get("enabled"):
                try:
                    await self.check()
                    self.status["error"] = None
                except Exception as e:  # noqa: BLE001
                    self.status["error"] = friendly_error(e)
                    log.warning("%s: %s", self.title, self.status["error"])
                self.status["checked"] = time.time()
                self.hub.touch()
                wait = self.interval
            else:
                wait = 5
            try:
                await asyncio.wait_for(self.wake.wait(), wait)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def check(self):
        raise NotImplementedError

    def alert(self, title: str, body: str, sms_text: str, level: str = "info"):
        self.status["last_event"] = time.time()
        self.hub.alert(self.name, title, body, sms_text if self.cfg.get("sms") else None, level)


async def fetch_text(url: str, timeout=40, headers=None) -> str:
    async with http_session(timeout) as s:
        async with s.get(url, headers=headers or {}) as r:
            r.raise_for_status()
            return await r.text()


async def fetch_json(url: str, timeout=30, headers=None):
    return json.loads(await fetch_text(url, timeout, headers))


# --------------------------------------------------------------------------
# Salamahälytys
# --------------------------------------------------------------------------
class Lightning(Service):
    name = "lightning"
    title = "Salamahälytys"
    interval = 120
    WFS = "https://opendata.fmi.fi/wfs"

    def __init__(self, hub):
        super().__init__(hub)
        self.seen: dict[tuple, float] = {}
        self.strikes: list[dict] = []
        self.last_alert = 0.0
        self.last_alert_dist = None

    async def check(self):
        if not self.loc:
            raise ValueError(tr("Aseta oma sijainti"))
        lat, lon = self.loc
        r = float(self.cfg.get("radius_km") or 30)
        dlat = r / 111.0 + 0.05
        dlon = r / (111.0 * max(0.2, math.cos(math.radians(lat)))) + 0.05
        start = time.gmtime(time.time() - 3600)
        url = (f"{self.WFS}?service=WFS&version=2.0.0&request=getFeature"
               f"&storedquery_id=fmi::observations::lightning::simple&parameters=cloud_indicator"
               f"&starttime={time.strftime('%Y-%m-%dT%H:%M:%SZ', start)}"
               f"&bbox={lon - dlon:.4f},{lat - dlat:.4f},{lon + dlon:.4f},{lat + dlat:.4f}")
        xml = await fetch_text(url, 60)
        pts = re.findall(r"<gml:pos>([\d.\-]+) ([\d.\-]+)</gml:pos>\s*</gml:Point>\s*</BsWfs:Location>\s*"
                         r"<BsWfs:Time>([^<]+)</BsWfs:Time>\s*<BsWfs:ParameterName>cloud_indicator</BsWfs:ParameterName>"
                         r"\s*<BsWfs:ParameterValue>([^<]*)</BsWfs:ParameterValue>", xml)
        now = time.time()
        new, recent = [], []
        for la, lo, ts, cloud in pts:
            la, lo = float(la), float(lo)
            t = _iso_to_ts(ts) or now
            ground = cloud.strip() in ("0", "0.0")
            if self.cfg.get("ground_only") and not ground:
                continue
            d = distance_km(lat, lon, la, lo)
            if d > r:
                continue
            s = {"time": t, "lat": la, "lon": lo, "km": round(d, 1), "dir": bearing_name(lat, lon, la, lo),
                 "ground": ground}
            recent.append(s)
            key = (ts, la, lo)
            if key not in self.seen:
                self.seen[key] = now
                if self.primed and now - t < 1800:
                    new.append(s)
        self.seen = {k: v for k, v in self.seen.items() if now - v < 7200}
        self.strikes = sorted(recent, key=lambda s: -s["time"])[:50]
        self.status.update(count_60min=len(recent),
                           nearest=min(recent, key=lambda s: s["km"]) if recent else None,
                           latest=self.strikes[0] if self.strikes else None)
        if not self.primed:
            self.primed = True
            return
        if not new:
            return
        nearest = min(new, key=lambda s: s["km"])
        cooldown = float(self.cfg.get("cooldown_min") or 30) * 60
        closer = self.last_alert_dist is not None and nearest["km"] < self.last_alert_dist - 5
        if now - self.last_alert < cooldown and not closer:
            return
        self.last_alert = now
        self.last_alert_dist = nearest["km"]
        n15 = sum(1 for s in recent if now - s["time"] < 900)
        kind = tr("maasalama") if nearest["ground"] else tr("salama")
        km = f"{nearest['km']:.0f}"
        txt = tr("Salama {km} km {dir}, {n} iskua/15 min", km=km, dir=nearest["dir"], n=n15)
        self.alert(tr("⚡ {kind} {km} km {dir}", kind=kind.capitalize(), km=km, dir=nearest["dir"]),
                   tr("{n} iskua 15 min aikana {r} km säteellä", n=n15, r=self.cfg.get("radius_km")), txt, "bad")


# --------------------------------------------------------------------------
# Säävaroitukset (FMI CAP)
# --------------------------------------------------------------------------
CAP_NS = {"a": "http://www.w3.org/2005/Atom", "c": "urn:oasis:names:tc:emergency:cap:1.2"}


def parse_cap_feed(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for entry in root.findall("a:entry", CAP_NS):
        al = entry.find(".//c:alert", CAP_NS)
        if al is None:
            continue
        info = al.find("c:info", CAP_NS)
        if info is None:
            continue
        g = lambda el, tag: (el.findtext(f"c:{tag}", default="", namespaces=CAP_NS) or "").strip()  # noqa: E731
        params = {p.findtext("c:valueName", "", CAP_NS): p.findtext("c:value", "", CAP_NS)
                  for p in info.findall("c:parameter", CAP_NS)}
        codes = [e.findtext("c:value", "", CAP_NS) for e in info.findall("c:eventCode", CAP_NS)]
        areas = []
        for a in info.findall("c:area", CAP_NS):
            polys = []
            for ptxt in [p.text or "" for p in a.findall("c:polygon", CAP_NS)]:
                pts = []
                for pair in ptxt.split():
                    try:
                        la, lo = pair.split(",")
                        pts.append((float(la), float(lo)))
                    except ValueError:
                        continue
                if len(pts) >= 3:
                    polys.append(pts)
            areas.append({"name": g(a, "areaDesc"), "polygons": polys})
        out.append({
            "id": g(al, "identifier"), "msgType": g(al, "msgType"), "references": g(al, "references"),
            "sent": _iso_to_ts(g(al, "sent")), "event": g(info, "event"), "code": codes[0] if codes else "",
            "severity": g(info, "severity"), "headline": g(info, "headline"), "description": g(info, "description"),
            "onset": _iso_to_ts(g(info, "onset")) or _iso_to_ts(g(info, "effective")),
            "expires": _iso_to_ts(g(info, "expires")), "color": params.get("color", ""), "areas": areas,
            "params": params,
        })
    return out


CAUSES = {"snow": "lumi", "sleet": "räntä", "snowOrSleet": "lumi/räntä", "rain": "sade", "heavyRain": "rankkasade",
          "freezingDrizzle": "jäätävä tihku", "freezingRain": "jäätävä sade", "waterOnIce": "vesi jään päällä",
          "snowDrift": "pyry", "blowingSnow": "pyry", "wind": "tuuli", "ice": "jää", "frost": "kuura",
          "hail": "rakeet", "thunderstorm": "ukkonen", "fog": "sumu", "slush": "sohjo", "blackIce": "musta jää",
          "frozenSlush": "jäätynyt sohjo", "snowAndIce": "lumi ja jää"}
EVENTS = {"Tuulivaroitus merelle": "tuuli merellä", "Tuulivaroitus maa-alueille": "tuuli", "Aallokkovaroitus": "aallokko",
          "Metsäpalovaroitus": "metsäpalo", "Ruohikkopalovaroitus": "ruohikkopalo", "Pakkasvaroitus": "pakkanen",
          "Hellevaroitus": "helle", "Tulvavaroitus": "tulva", "Ukkosvaroitus": "ukkonen", "Merivedenkorkeus": "merivesi",
          "Vedenkorkeusvaroitus merelle": "merivesi", "Sadevaroitus": "sade", "Lumivaroitus": "lumi",
          "UV-varoitus": "UV", "Jäätämisvaroitus merelle": "jäätäminen merellä"}
COLORFI = {"yellow": "Keltainen", "orange": "Oranssi", "red": "Punainen"}
WD = ["P", "KO", "I", "KA", "E", "LO", "L", "LU"]
WEEKDAY = ["ma", "ti", "ke", "to", "pe", "la", "su"]


def _hm_fi(ts) -> str:
    t = time.localtime(ts)
    return f"{t.tm_hour}" if t.tm_min == 0 else f"{t.tm_hour}.{t.tm_min:02d}"


def _span_fi(a, b) -> str:
    """'nyt–24', 'to 14–pe 12', 'la 0–24', 'pe 8–17'. Keskiyöllä päättyvä = edellisen päivän 24."""
    if not a:
        return ""
    now = time.time()
    ta = time.localtime(a)
    start = "nyt" if a <= now + 60 else f"{WEEKDAY[ta.tm_wday]} {_hm_fi(a)}"
    start_day = time.localtime(max(a, now)).tm_yday
    if not b:
        return start
    tb = time.localtime(b)
    if tb.tm_hour == 0 and tb.tm_min == 0:
        prev = time.localtime(b - 60)
        end_day, end_txt, end_wd = prev.tm_yday, "24", prev.tm_wday
    else:
        end_day, end_txt, end_wd = tb.tm_yday, _hm_fi(b), tb.tm_wday
    if end_day == start_day:
        return f"{start}–{end_txt}"
    return f"{start}–{WEEKDAY[end_wd]} {end_txt}"


def short_warning(w: dict) -> str:
    """Esim. 'Keltainen liikennesää: lumi/räntä, to 14–pe 12 (30 %)'."""
    p = w.get("params") or {}
    ev = EVENTS.get(w.get("event", ""), re.sub(r"varoitus", "", (w.get("event") or "varoitus")).strip().lower() or "varoitus")
    head = f"{COLORFI.get(w.get('color'), '')} {ev}".strip()
    bits = []
    causes = [CAUSES.get(c) for c in (p.get("eventCause") or "").split()]
    causes = [c for c in causes if c]
    if causes:
        bits.append(" + ".join(dict.fromkeys(causes)))
    if p.get("windIntensity"):
        d = ""
        try:
            d = WD[int((float(p.get("windDirection", 0)) + 22.5) // 45) % 8] + " "
        except ValueError:
            pass
        bits.append(f"{d}{_qty(p['windIntensity'])} m/s")
    if p.get("waveHeight"):
        bits.append(f"aallot {_qty(p['waveHeight'])} m")
    for k, unit in (("temperature", "°C"), ("rainAmount", "mm"), ("snowAmount", "cm")):
        if p.get(k):
            bits.append(f"{_qty(p[k])} {unit}")
    span = _span_fi(w.get("onset"), w.get("expires"))
    if span:
        bits.append(span)
    prob = p.get("probability")
    text = f"{head}: {', '.join(bits)}" if bits else head
    if prob and prob not in ("90", "100"):
        text += f" ({prob} %)"
    if w.get("msgType") == "Cancel":
        text = "POISTETTU: " + head
    return text


class Warnings(Service):
    name = "warnings"
    title = "Säävaroitukset"
    interval = 300
    FEED = "https://alerts.fmi.fi/cap/feed/atom_fi-FI.xml"

    def __init__(self, hub):
        super().__init__(hub)
        self.seen: set[str] = set()
        self.alerted_headlines: dict[str, str] = {}   # id -> headline
        self.active: list[dict] = []

    async def check(self):
        if not self.loc:
            raise ValueError(tr("Aseta oma sijainti"))
        lat, lon = self.loc
        items = parse_cap_feed(await fetch_text(self.FEED, 60))
        min_level = COLORS.get(self.cfg.get("min_color", "yellow"), 1)
        now = time.time()
        mine = []
        for w in items:
            hit = [a["name"] for a in w["areas"] if any(point_in_polygon(lat, lon, p) for p in a["polygons"])]
            if not hit:
                continue
            if not self.cfg.get("traffic_weather", True) and w["code"] == "trafficWeather":
                continue
            if COLORS.get(w["color"], 1) < min_level and w["msgType"] != "Cancel":
                continue
            w = {k: v for k, v in w.items() if k != "areas"}
            w["here"] = hit[0]
            w["short"] = short_warning(w)
            mine.append(w)
        cancelled_refs = {r for w in mine if w["msgType"] == "Cancel" for r in re.findall(r"urn:oid:[\d.]+", w["references"])}
        self.active = sorted([w for w in mine if w["msgType"] != "Cancel" and w["id"] not in cancelled_refs
                              and (not w["expires"] or w["expires"] > now)],
                             key=lambda w: (-COLORS.get(w["color"], 1), w["onset"] or 0))
        self.status.update(active=len(self.active))
        fresh = [w for w in mine if w["id"] not in self.seen]
        self.seen |= {w["id"] for w in mine}
        if not self.primed:
            self.primed = True
            return
        for w in sorted(fresh, key=lambda w: w["sent"] or 0):
            refs = set(re.findall(r"urn:oid:[\d.]+", w["references"]))
            if w["msgType"] == "Cancel":
                if not refs & set(self.alerted_headlines):
                    continue
            elif w["msgType"] == "Update":
                prev = {self.alerted_headlines.get(r) for r in refs}
                if w["headline"] in prev:
                    self.alerted_headlines[w["id"]] = w["headline"]
                    continue
            self.alerted_headlines[w["id"]] = w["headline"]
            color = {"yellow": "Keltainen", "orange": "Oranssi", "red": "Punainen"}.get(w["color"], "")
            level = "bad" if w["color"] in ("orange", "red") else "warn"
            self.alert(f"⚠ {w['short']}", f"{w['description']} ({w['here']})", w["short"], level if w["msgType"] != "Cancel" else "info")
            log.info("Varoitus: %s %s", color, w["headline"])


# --------------------------------------------------------------------------
# Avaruussää (NOAA SWPC)
# --------------------------------------------------------------------------
class SpaceWeather(Service):
    name = "space"
    title = "Avaruussää"
    interval = 300
    KP = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
    ALERTS = "https://services.swpc.noaa.gov/products/alerts.json"

    def __init__(self, hub):
        super().__init__(hub)
        self.seen: set[str] = set()
        self.storm = False
        self.recent: list[dict] = []

    async def check(self):
        kp_data = await fetch_json(self.KP)
        last = kp_data[-1] if kp_data else {}
        kp = float(last.get("estimated_kp") or last.get("kp_index") or 0)
        hist = [float(x.get("estimated_kp") or 0) for x in kp_data[-360:]]
        self.status.update(kp=kp, kp_time=_iso_to_ts(last.get("time_tag")), kp_max6h=max(hist) if hist else None)
        thr = float(self.cfg.get("kp_threshold") or 5)
        alerts = await fetch_json(self.ALERTS)
        msgs = []
        for a in alerts:
            m = a.get("message", "").replace("\r", "")
            key = f"{a.get('product_id')}|{a.get('issue_datetime')}"
            title = self.describe(m, thr)
            if title:
                msgs.append({"key": key, "time": _iso_to_ts(a.get("issue_datetime")), "title": title[0], "kind": title[1]})
        msgs.sort(key=lambda x: -(x["time"] or 0))
        self.recent = msgs[:10]
        new = [x for x in msgs if x["key"] not in self.seen]
        self.seen |= {x["key"] for x in msgs}
        if not self.primed:
            self.primed = True
            self.storm = kp >= thr
            return
        if kp >= thr and not self.storm:
            self.storm = True
            self.alert(tr("Geomagneettinen myrsky: Kp {kp}", kp=num(kp)),
                       tr("Revontulikelit VHF:llä mahdollisia, HF-kelit voivat heiketä."),
                       tr("Avaruussaa: Kp {kp} - myrsky, revontulikelit mahdollisia", kp=f"{kp:.1f}"), "warn")
        elif kp < thr - 1:
            self.storm = False
        for x in reversed(new):
            if x["kind"] == "flare" and self.cfg.get("flares", True):
                self.alert(x["title"], tr("HF-yhteydet voivat katketa auringon puoleisella maapallolla."), x["title"], "warn")
            elif x["kind"] == "forecast" and self.cfg.get("forecasts", True):
                self.alert(x["title"], tr("NOAA:n ennuste"), x["title"], "info")

    @staticmethod
    def describe(m: str, thr: float):
        f = re.search(r"X-ray (?:Flux exceeded|Event exceeded) (M5|X\d+)", m, re.I)
        if f:
            return (tr("Auringonpurkaus: röntgenvuo ylitti {c}", c=f.group(1).upper()), "flare")
        k = re.search(r"WARNING: Geomagnetic K-index of (\d) expected", m)
        if k and int(k.group(1)) >= thr:
            valid = re.search(r"Valid To: \d{4} (\w{3} \d+ \d{4}) UTC", m)
            return (tr("Ennuste: K-indeksi {k} odotettavissa", k=k.group(1)) + (tr(" (voimassa {t} UTC asti)", t=valid.group(1)) if valid else ""),
                    "forecast")
        return None


# --------------------------------------------------------------------------
# Paikallinen revontulihälytys (FMI magnetometrit)
# --------------------------------------------------------------------------
MAG_STATIONS = {(69.76, 27.01): "Kevo", (69.06, 20.77): "Kilpisjärvi", (68.56, 27.29): "Ivalo",
                (68.02, 23.52): "Muonio", (66.9, 24.08): "Pello", (65.9, 26.41): "Ranua",
                (64.51, 27.23): "Oulujärvi", (62.77, 30.97): "Mekrijärvi", (62.25, 26.6): "Hankasalmi",
                (60.51, 24.66): "Nurmijärvi", (58.26, 26.46): "Tartto"}


def sun_elevation(lat: float, lon: float, ts: float | None = None) -> float:
    """Auringon korkeuskulma asteina (NOAA:n likikaava, tarkkuus ~1°)."""
    t = datetime.fromtimestamp(ts or time.time(), timezone.utc)
    doy = t.timetuple().tm_yday
    hour = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    tst = hour * 60 + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cosz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return 90 - math.degrees(math.acos(max(-1, min(1, cosz))))


def mag_name(lat, lon) -> str:
    best = min(MAG_STATIONS, key=lambda k: abs(k[0] - lat) + abs(k[1] - lon))
    return MAG_STATIONS[best] if abs(best[0] - lat) + abs(best[1] - lon) < 0.2 else f"{lat:.1f}N {lon:.1f}E"


async def fetch_clouds(lat: float, lon: float) -> tuple[float | None, str]:
    """Lähimmän sääaseman pilvisyys (oktaa 0–8)."""
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    url = (f"https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature"
           f"&storedquery_id=fmi::observations::weather::multipointcoverage&parameters=n_man"
           f"&bbox={lon - 1.2:.3f},{lat - 0.6:.3f},{lon + 1.2:.3f},{lat + 0.6:.3f}&starttime={start}")
    xml = await fetch_text(url, 40)
    names = re.findall(r'locationcode/name">([^<]+)<', xml)
    pos = re.search(r"<gmlcov:positions>(.*?)</gmlcov:positions>", xml, re.S)
    vals = re.search(r"<gml:doubleOrNilReasonTupleList>(.*?)</gml:doubleOrNilReasonTupleList>", xml, re.S)
    if not (pos and vals):
        return None, ""
    rows = [r.split() for r in pos.group(1).strip().splitlines()]
    vs = vals.group(1).split()
    # sijainnit -> asema: asemat tulevat järjestyksessä, kukin omalla koordinaatillaan
    coords = []
    for r in rows:
        c = (float(r[0]), float(r[1]))
        if not coords or coords[-1] != c:
            coords.append(c)
    latest: dict[tuple, float] = {}
    for r, v in zip(rows, vs):
        try:
            fv = float(v)
        except ValueError:
            continue
        if fv == fv:  # ei NaN
            latest[(float(r[0]), float(r[1]))] = fv
    if not latest:
        return None, ""
    c = min(latest, key=lambda k: distance_km(lat, lon, k[0], k[1]))
    idx = coords.index(c) if c in coords else -1
    return latest[c], names[idx] if 0 <= idx < len(names) else ""


class Aurora(Service):
    name = "aurora"
    title = "Revontulihälytys"
    interval = 300
    WFS = "https://opendata.fmi.fi/wfs"

    def __init__(self, hub):
        super().__init__(hub)
        self.level = 0          # 0 = ei, 1 = mahdollisia, 2 = todennäköisiä, 3 = voimakkaita
        self.stations: list[dict] = []

    async def check(self):
        if not self.loc:
            raise ValueError(tr("Aseta oma sijainti"))
        lat, lon = self.loc
        start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3900))
        xml = await fetch_text(f"{self.WFS}?service=WFS&version=2.0.0&request=getFeature"
                               f"&storedquery_id=fmi::observations::magnetometer::simple"
                               f"&parameters=MAGNX_PT1M_AVG&starttime={start}", 60)
        series: dict[tuple, list[tuple[float, float]]] = {}
        for la, lo, ts, v in re.findall(r"<gml:pos>([\d.\-]+) ([\d.\-]+)\s*</gml:pos>.*?<BsWfs:Time>([^<]+)</BsWfs:Time>"
                                        r".*?<BsWfs:ParameterValue>([^<]*)</BsWfs:ParameterValue>", xml, re.S):
            try:
                fv = float(v)
            except ValueError:
                continue
            if fv != fv:
                continue
            series.setdefault((float(la), float(lo)), []).append((_iso_to_ts(ts) or 0, fv))
        now = time.time()
        sts = []
        for (la, lo), vals in series.items():
            recent = [v for t, v in vals if now - t <= 3600]
            if len(recent) < 10:
                continue
            sts.append({"name": mag_name(la, lo), "lat": la, "lon": lo, "range": round(max(recent) - min(recent)),
                        "km": round(distance_km(lat, lon, la, lo))})
        if not sts:
            raise ValueError(tr("Magnetometridataa ei saatu"))
        self.stations = sorted(sts, key=lambda s: -s["lat"])
        near = min(sts, key=lambda s: s["km"])
        thr = float(self.cfg.get("threshold_nt") or 100)
        rng = near["range"]
        lvl = 3 if rng >= 4 * thr else 2 if rng >= 2 * thr else 1 if rng >= thr else 0
        elev = sun_elevation(lat, lon)
        dark = elev < -6
        clouds, cloud_st = (None, "")
        try:
            clouds, cloud_st = await fetch_clouds(lat, lon)
        except Exception:  # noqa: BLE001
            pass
        self.status.update(nearest=near, sun=round(elev, 1), dark=dark, clouds=clouds, cloud_station=cloud_st,
                           level=lvl)
        if not self.primed:
            self.primed = True
            self.level = lvl if (dark or not self.cfg.get("dark_only", True)) else 0
            return
        visible_ok = dark or not self.cfg.get("dark_only", True)
        if self.cfg.get("skip_overcast") and clouds is not None and clouds >= 7:
            visible_ok = False
        if lvl == 0 and rng < thr * 0.6:
            self.level = 0
            return
        if lvl > self.level and visible_ok:
            self.level = lvl
            word = tr({1: "mahdollisia", 2: "todennäköisiä", 3: "voimakkaita"}[lvl])
            cl = tr(", pilvisyys {c}/8", c=int(clouds)) if clouds is not None else ""
            txt = tr("Revontulet {word}: {name} {rng} nT/h{cl}", word=word, name=near["name"], rng=rng, cl=cl)
            self.alert(f"🌌 {txt}", tr("Magneettinen aktiivisuus lähimmällä magnetometrillä ({name}, {km} km). "
                                      "VHF-revontulikelit mahdollisia.", name=near["name"], km=near["km"]),
                       txt, "warn" if lvl < 3 else "bad")


# --------------------------------------------------------------------------
# Säteilyvalvonta (STUK)
# --------------------------------------------------------------------------
class Radiation(Service):
    name = "radiation"
    title = "Säteilyvalvonta"
    interval = 600
    URL = ("https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature"
           "&storedquery_id=stuk::observations::external-radiation::latest::multipointcoverage")

    def __init__(self, hub):
        super().__init__(hub)
        self.alerted: dict[str, float] = {}
        self.stations: list[dict] = []

    async def check(self):
        radius = float(self.cfg.get("radius_km") or 0)
        if radius > 0 and not self.loc:
            raise ValueError(tr("Aseta oma sijainti tai valitse koko Suomi (säde 0)"))
        xml = await fetch_text(self.URL, 60)
        names = re.findall(r'locationcode/name">([^<]+)<', xml)
        pos = re.search(r"<gmlcov:positions>(.*?)</gmlcov:positions>", xml, re.S)
        vals = re.search(r"<gml:doubleOrNilReasonTupleList>(.*?)</gml:doubleOrNilReasonTupleList>", xml, re.S)
        if not (pos and vals):
            raise ValueError(tr("Säteilydataa ei saatu"))
        rows = [r.split() for r in pos.group(1).strip().splitlines()]
        vrows = [r.split() for r in vals.group(1).strip().splitlines()]
        sts = []
        for i, (r, v) in enumerate(zip(rows, vrows)):
            try:
                dr = float(v[0])
            except (ValueError, IndexError):
                continue
            if dr != dr:
                continue
            la, lo = float(r[0]), float(r[1])
            st = {"name": names[i] if i < len(names) else f"{la:.2f},{lo:.2f}", "lat": la, "lon": lo, "dr": dr,
                  "time": float(r[2]) if len(r) > 2 else None}
            if self.loc:
                st["km"] = round(distance_km(self.loc[0], self.loc[1], la, lo))
            sts.append(st)
        if not sts:
            raise ValueError(tr("Säteilydataa ei saatu"))
        area = [s for s in sts if radius <= 0 or s.get("km", 1e9) <= radius]
        self.stations = sorted(area, key=lambda s: s.get("km", 0))[:8]
        fin_max = max(sts, key=lambda s: s["dr"])
        area_max = max(area, key=lambda s: s["dr"]) if area else None
        nearest = min(sts, key=lambda s: s.get("km", 0)) if self.loc else None
        self.status.update(count=len(area), nearest=nearest, area_max=area_max, fin_max=fin_max)
        thr = float(self.cfg.get("threshold") or 0.4)
        high = [s for s in area if s["dr"] >= thr]
        for s in area:
            if s["name"] in self.alerted and s["dr"] < thr * 0.8:
                self.alerted.pop(s["name"], None)
        if not self.primed:
            self.primed = True
            self.alerted.update({s["name"]: time.time() for s in high})
            return
        new = [s for s in high if s["name"] not in self.alerted]
        if not new:
            return
        for s in new:
            self.alerted[s["name"]] = time.time()
        top = max(new, key=lambda s: s["dr"])
        more = tr(" (+{n} asemaa)", n=len(new) - 1) if len(new) > 1 else ""
        dist = f", {top['km']} km" if "km" in top else ""
        txt = tr("Säteily koholla: {name} {dr} µSv/h{more}", name=top["name"], dr=num(top["dr"], 2), more=more)
        self.alert(f"☢ {txt}", tr("Raja {thr} µSv/h{dist}. Normaali taso Suomessa n. 0,05–0,3 µSv/h. "
                                 "Tarkista tilanne STUK:n sivuilta.", thr=num(thr, 2), dist=dist),
                   txt.replace("µSv/h", "uSv/h"), "bad")


# --------------------------------------------------------------------------
# Tieliikennetiedotteet (Digitraffic)
# --------------------------------------------------------------------------
class Traffic(Service):
    name = "traffic"
    title = "Tieliikennetiedotteet"
    interval = 180
    API = "https://tie.digitraffic.fi/api/traffic-message/v1/messages"
    HEADERS = {"Digitraffic-User": "BMMonitor", "Accept-Encoding": "gzip"}

    def __init__(self, hub):
        super().__init__(hub)
        self.seen: dict[str, int] = {}
        self.active: list[dict] = []

    def center(self):
        if self.cfg.get("use_location", True):
            return self.loc
        c = self.cfg.get("center")
        return (c["lat"], c["lon"]) if c else None

    async def check(self):
        types = [t for t in self.cfg.get("types", []) if t in TRAFFIC_TYPES] or ["TRAFFIC_ANNOUNCEMENT"]
        radius = float(self.cfg.get("radius_km") or 0)
        center = self.center() if radius > 0 else None
        provinces = set(self.cfg.get("provinces") or [])
        roads = {int(r) for r in self.cfg.get("roads") or []}
        if not (center or provinces or roads):
            raise ValueError(tr("Valitse sijainti, maakunta tai tienumero"))
        feats = []
        for t in types:
            d = await fetch_json(f"{self.API}?inactiveHours=0&includeAreaGeometry=false&situationType={t}",
                                 headers=self.HEADERS)
            feats += d.get("features", [])
        items = []
        for f in feats:
            p = f.get("properties", {})
            ann = (p.get("announcements") or [{}])[0]
            ld = (ann.get("locationDetails") or {}).get("roadAddressLocation") or {}
            pts = [x for x in (ld.get("primaryPoint"), ld.get("secondaryPoint")) if x]
            prov = {x.get("province") for x in pts if x.get("province")}
            muni = [x.get("municipality") for x in pts if x.get("municipality")]
            rds = {(x.get("roadAddress") or {}).get("road") for x in pts} - {None}
            dist = None
            if center:
                dist = _min_dist(center, f.get("geometry"))
            why = []
            if center and dist is not None and dist <= radius:
                why.append(f"{dist:.0f} km")
            if prov & provinces:
                why.append(", ".join(sorted(prov & provinces)))
            if rds & roads:
                why.append(tr("tie {roads}", roads=", ".join(str(r) for r in sorted(rds & roads))))
            if not why:
                continue
            feats_txt = ", ".join(x.get("name", "") for x in ann.get("features", []) if x.get("name"))
            items.append({"id": p.get("situationId"), "version": p.get("version") or 0, "short": short_traffic(p, ann),
                          "type": p.get("situationType"), "title": (ann.get("title") or "").strip(),
                          "what": feats_txt, "where": ((ann.get("location") or {}).get("description") or "").strip(),
                          "municipality": ", ".join(dict.fromkeys(muni)), "km": dist, "why": "; ".join(why),
                          "time": _iso_to_ts(p.get("releaseTime")) or _iso_to_ts(p.get("versionTime")),
                          "comment": (ann.get("comment") or "")[:300]})
        self.active = sorted(items, key=lambda i: (i["km"] if i["km"] is not None else 9999))
        self.status.update(active=len(items))
        for i in sorted(items, key=lambda i: i["time"] or 0):
            prev = self.seen.get(i["id"])
            self.seen[i["id"]] = i["version"]
            if not self.primed:
                continue
            if prev is None or (self.cfg.get("updates") and i["version"] > prev):
                lvl = "bad" if re.search(r"onnettomuus|suljettu|katkaistu", i["short"].lower()) else "warn"
                self.alert(f"🚧 {i['short']}", f"{i['why']}" + (f" · {i['where']}" if self.cfg.get("details") else ""),
                           i["short"], lvl)
        self.primed = True


# Tiedotteiden lyhennys: pitkät vakioilmaukset -> lyhyet, tärkeysjärjestyksessä (pienempi = tärkeämpi)
SHORT_FEATURES = [
    (r"^onnettomuus, jossa on mukana raskas ajoneuvo", "onnettomuus, raskas ajoneuvo", 0),
    (r"^onnettomuus", "onnettomuus", 0),
    (r"^tie on suljettu liikenteeltä", "tie suljettu", 1),
    (r"^tie on ajoittain suljettu", "tie ajoittain suljettu", 1),
    (r"kiertotie", "kiertotie", 2),
    (r"^ajokaistoja suljettu", "kaistoja suljettu", 3),
    (r"^ajokaista suljettu", "kaista suljettu", 3),
    (r"^ajokaistoja on kavennettu", "kaistat kavennettu", 4),
    (r"^liikenne on jonoutunut", "jonoa", 4),
    (r"^liikenne pysäytetään ajoittain", "pysäytyksiä", 5),
    (r"^liikenne ohjataan vuorotellen", "vuorottainen liikenne", 5),
    (r"liikennevalo", "liikennevalot", 6),
    (r"liikenteenohjaus", "liikenteenohjaus", 6),
    (r"^kelirikko", "kelirikko", 3),
    (r"^lauttaliikenne keskeytetään", "lauttaliikenne keskeytetty", 1),
    (r"^lautan kantavuus", "lautan kantavuus muuttunut", 6),
    (r"^ajoneuvon suurin sallittu massa", "max {q} t", 7),
    (r"^ajoneuvon suurin sallittu leveys", "leveys max {q} m", 7),
    (r"^ajoneuvon suurin sallittu korkeus", "korkeus max {q} m", 7),
    (r"^nopeusrajoitus", "nopeus {q}", 8),
    (r"^liikennejärjestelyt ovat muuttuneet", "järjestelyt muuttuneet", 8),
]


def _qty(v) -> str:
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f).replace(".", ",")
    except (TypeError, ValueError):
        return ""


def short_traffic(props: dict, ann: dict, max_effects: int = 2) -> str:
    """Esim. 'Tie 4 Oulu–Ii: onnettomuus, raskas ajoneuvo, liikenteenohjaus'."""
    ld = (ann.get("locationDetails") or {}).get("roadAddressLocation") or {}
    pts = [x for x in (ld.get("primaryPoint"), ld.get("secondaryPoint")) if x]
    road = next(((x.get("roadAddress") or {}).get("road") for x in pts if (x.get("roadAddress") or {}).get("road")), None)
    munis = list(dict.fromkeys(re.sub(r"\s+(kunta|stad|kommun|kaupunki)$", "", x.get("municipality"))
                               for x in pts if x.get("municipality")))
    munis = [{"Pedersören": "Pedersöre"}.get(m, m) for m in munis]
    title = re.sub(r"\s+", " ", ann.get("title") or "").strip()
    # Tarkempi paikka (AlertC-sijaintinimet): "Välillä A - B" -> "A–B", "Paikasta A ..., suuntaan B" -> "A"
    p1, p2 = ld.get("primaryPoint") or {}, ld.get("secondaryPoint") or {}
    n1 = ((p1.get("alertCLocation") or {}).get("name") or "").strip()
    n2 = ((p2.get("alertCLocation") or {}).get("name") or "").strip()
    desc = ((ann.get("location") or {}).get("description") or "")
    segment = bool(n1 and n2 and n1 != n2 and re.search(r"Tarkempi paikka:\s*Välillä", desc))
    detail_full = f"{n1}–{n2}" if segment else n1
    street = (p1.get("roadName") or "").strip() if not road else ""
    if road:
        base = f"Tie {road} "
    elif street:
        base = f"{street} "
    else:
        base = ""
    if not road and not street:
        # aluetiedote tai tuntematon muoto: otsikon alku, esim. "Rautatienkatu, Tampere"
        place_plain = ", ".join(x.strip() for x in title.split(".")[0].split(",")[:2]).strip()
    else:
        place_plain = (base + "–".join(munis[:2])).strip()

    def make_place(detail: str) -> str:
        if not detail or not (road or street):
            return place_plain
        if detail.lower() in (street.lower(), *(m.lower() for m in munis)):
            return place_plain
        return f"{base}{munis[0] if munis else ''}, {detail}".replace(" ,", ",").strip()

    place = make_place(detail_full)
    effects: list[tuple[int, str]] = []
    for f in ann.get("features") or []:
        name = (f.get("name") or "").strip()
        low = name.lower()
        for pat, short, prio in SHORT_FEATURES:
            if re.search(pat, low):
                txt = short.format(q=_qty(f.get("quantity")))
                if "{q}" in short and not _qty(f.get("quantity")):
                    txt = short.split(" {q}")[0]
                effects.append((prio, txt))
                break
        else:
            if name:
                effects.append((9, name[0].lower() + name[1:]))
    # tietyön laji ja arvioitu viive
    event = ""
    delay = None
    for ph in ann.get("roadWorkPhases") or []:
        for w in ph.get("workTypes") or []:
            d = (w.get("description") or "").strip()
            if d and w.get("type") != "OTHER" and not event:
                event = d
        for r in ph.get("restrictions") or []:
            rn = (r.get("restriction") or {}).get("name", "")
            if "viivytys" in rn.lower() and "ruuhka" not in rn.lower():
                delay = _qty((r.get("restriction") or {}).get("quantity"))
    if not event and props.get("situationType") == "ROAD_WORK":
        event = "tietyö"
    if not event and props.get("situationType") == "WEIGHT_RESTRICTION":
        event = "painorajoitus"
    if not event and props.get("situationType") == "EXEMPTED_TRANSPORT":
        event = "erikoiskuljetus"
    seen, parts = {event.lower()} if event else set(), []
    for _, e in sorted(effects, key=lambda x: x[0]):
        if e.lower() not in seen:
            seen.add(e.lower())
            parts.append(e)
    parts = parts[:max_effects + (1 if parts and parts[0].startswith("onnettomuus") else 0)]
    if delay:
        parts.append(f"viive ~{delay} min")
    if place and event and place.lower() in event.lower() or (place and place.lower() in [p.lower() for p in parts]):
        place = ""
    prefix = "OHI: " if props.get("trafficAnnouncementType") == "ENDED" or "tilanne ohi" in title.lower() else ""

    def build(ps, pl):
        allp = ([event[0].upper() + event[1:]] if event else []) + ps
        text = ", ".join(allp) or "liikennetiedote"
        text = text[0].upper() + text[1:]
        return f"{prefix}{pl}: {text}" if pl else prefix + text

    out = build(parts, place)
    # Mahdu yhteen 67 merkin radioviestiin. Järjestys:
    # 1) pudota vähiten tärkeitä vaikutuksia, 2) välistä vain alkupiste, 3) lyhennä paikan nimeä,
    # 4) jätä tarkka paikka pois.
    while len(out) > 67 and len(parts) > 1:
        parts.pop()
        out = build(parts, place)
    if len(out) > 67 and segment:
        place = make_place(n1)
        out = build(parts, place)
    if len(out) > 67 and n1 and place != place_plain:
        over = len(out) - 67
        name = n1 if not segment else n1
        if len(name) - over - 1 >= 6:
            place = make_place(name[: len(name) - over - 1].rstrip(" -,") + ".")
            out = build(parts, place)
    if len(out) > 67 and place != place_plain:
        out = build(parts, place_plain)
    return out


def _min_dist(center, geom) -> float | None:
    if not geom:
        return None
    lat, lon = center
    best = None

    def walk(c):
        nonlocal best
        if not c:
            return
        if isinstance(c[0], (int, float)):
            d = distance_km(lat, lon, c[1], c[0])
            best = d if best is None or d < best else best
        else:
            step = max(1, len(c) // 50) if isinstance(c[0], list) and c and isinstance(c[0][0], (int, float)) else 1
            for x in c[::step]:
                walk(x)
            if step > 1:
                walk(c[-1])
    walk(geom.get("coordinates"))
    if geom.get("type") == "GeometryCollection":
        for g in geom.get("geometries", []):
            d = _min_dist(center, g)
            if d is not None and (best is None or d < best):
                best = d
    return best


# --------------------------------------------------------------------------
class ServiceHub:
    def __init__(self, mon):
        self.mon = mon
        self.services = {s.name: s for s in (Lightning(self), Warnings(self), SpaceWeather(self), Aurora(self),
                                             Radiation(self), Traffic(self))}

    @property
    def cfg(self):
        return self.mon.cfg["services"]

    def touch(self):
        self.mon.touch()

    def alert(self, service: str, title: str, body: str, sms: str | None, level: str):
        w = self.mon.watcher
        w.seq += 1
        w.alerts.appendleft({"seq": w.seq, "time": time.time(), "kind": "palvelu", "service": service,
                             "service_title": tr(self.services[service].title), "title": title, "body": body,
                             "level": level, "call": "", "tg": None, "via": ""})
        if sms:
            self.mon.hub.announce("", sms, source=service)
        self.touch()

    def reconfigure(self, changed: set[str]):
        for name in changed:
            if name in self.services:
                self.services[name].reset()
        if "location" in changed:
            for n in ("lightning", "warnings", "traffic", "aurora", "radiation"):
                self.services[n].reset()

    def snapshot(self) -> dict:
        loc = self.cfg["location"]
        out = {"location": {**loc, "locator": latlon_to_locator(loc["lat"], loc["lon"]) if loc.get("lat") is not None else ""}}
        L, W, S, T = (self.services[n] for n in ("lightning", "warnings", "space", "traffic"))
        out["lightning"] = {**L.status, "strikes": L.strikes[:10]}
        out["warnings"] = {**W.status, "items": [{k: w[k] for k in ("headline", "short", "description", "color", "onset", "expires", "here", "event", "id")}
                                                for w in W.active[:10]]}
        out["space"] = {**S.status, "recent": S.recent}
        out["traffic"] = {**T.status, "items": T.active[:15]}
        A, R = self.services["aurora"], self.services["radiation"]
        out["aurora"] = {**A.status, "stations": A.stations}
        out["radiation"] = {**R.status, "stations": R.stations}
        out["provinces"] = PROVINCES
        out["traffic_types"] = {k: tr(v) for k, v in TRAFFIC_TYPES.items()}
        return out

    async def run(self):
        await asyncio.gather(*(s.run() for s in self.services.values()))


def normalize_services(new: dict, old: dict) -> tuple[dict, set[str]]:
    out = json.loads(json.dumps(old))
    changed = set()

    def setb(sec, key):
        if key in new.get(sec, {}):
            out[sec][key] = bool(new[sec][key])

    def setn(sec, key, lo, hi):
        if key in new.get(sec, {}):
            out[sec][key] = min(hi, max(lo, float(new[sec][key] or lo)))

    if "location" in new:
        loc = new["location"]
        if loc.get("text") is not None:
            text = str(loc["text"]).strip()
            if text:
                lat, lon = parse_location(text)
                out["location"] = {"lat": lat, "lon": lon, "label": text}
            else:
                out["location"] = {"lat": None, "lon": None, "label": ""}
        elif loc.get("lat") is not None:
            lat, lon = float(loc["lat"]), float(loc["lon"])
            out["location"] = {"lat": round(lat, 5), "lon": round(lon, 5), "label": loc.get("label") or "selaimen sijainti"}
    for sec in ("lightning", "warnings", "space", "traffic", "aurora", "radiation"):
        if sec in new:
            setb(sec, "enabled")
            setb(sec, "sms")
    setn("lightning", "radius_km", 1, 300)
    setn("lightning", "cooldown_min", 0, 1440)
    setb("lightning", "ground_only")
    if new.get("warnings", {}).get("min_color") in COLORS:
        out["warnings"]["min_color"] = new["warnings"]["min_color"]
    setb("warnings", "traffic_weather")
    setn("space", "kp_threshold", 1, 9)
    setb("space", "forecasts")
    setb("space", "flares")
    setn("aurora", "threshold_nt", 20, 2000)
    setb("aurora", "dark_only")
    setb("aurora", "skip_overcast")
    setn("radiation", "threshold", 0.1, 100)
    setn("radiation", "radius_km", 0, 1500)
    setb("traffic", "use_location")
    setb("traffic", "updates")
    setb("traffic", "details")
    setn("traffic", "radius_km", 0, 500)
    tr = new.get("traffic", {})
    if "provinces" in tr:
        out["traffic"]["provinces"] = [p for p in tr["provinces"] if p in PROVINCES]
    if "roads" in tr:
        r = tr["roads"]
        r = r.replace(",", " ").split() if isinstance(r, str) else r
        roads = []
        for x in r:
            x = str(x).strip()
            if not x:
                continue
            if not x.isdigit():
                raise ValueError(tr("Tienumeron pitää olla numero: {x}", x=x))
            roads.append(int(x))
        out["traffic"]["roads"] = roads
    if "types" in tr:
        out["traffic"]["types"] = [t for t in tr["types"] if t in TRAFFIC_TYPES] or ["TRAFFIC_ANNOUNCEMENT"]
    if "center" in tr:
        c = str(tr["center"] or "").strip()
        if c:
            lat, lon = parse_location(c)
            out["traffic"]["center"] = {"lat": lat, "lon": lon, "label": c}
        else:
            out["traffic"].pop("center", None)
    for k in out:
        if out[k] != old.get(k):
            changed.add(k)
    for sec in ("lightning", "warnings", "traffic", "aurora", "radiation"):
        if sec == "radiation" and not out[sec].get("radius_km"):
            continue
        if out[sec].get("enabled") and out["location"].get("lat") is None and not (
                sec == "traffic" and (not out["traffic"].get("use_location") and out["traffic"].get("center")
                                      or out["traffic"]["provinces"] or out["traffic"]["roads"])):
            raise ValueError(tr("Aseta ensin oma sijainti (koordinaatit tai lokaattori)"))
    return out, changed
