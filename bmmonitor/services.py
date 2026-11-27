"""Tilauspalvelut: salamahälytys, säävaroitukset, avaruussää ja tieliikennetiedotteet.

Jokainen palvelu on erikseen päälle/pois kytkettävä. Uudet tapahtumat näkyvät
Hälytykset-listassa ja ilmoituksina, ja ne voi lähettää tekstiviestinä radioon.

Lähteet:
- Salamat:      FMI avoin data, WFS fmi::observations::lightning::simple
- Varoitukset:  FMI CAP-syöte https://alerts.fmi.fi/cap/feed/atom_fi-FI.xml
                tai MeteoAlarm https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-<maa>
- Revontulet:   FMI magnetometrit tai NOAA SWPC ovation_aurora_latest.json
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

from .i18n import bearing, dist, get_lang, num, tr
from .messaging import friendly_error, http_session
from . import satellites as sat

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
# MeteoAlarmin maat (syötteen nimi -> näyttönimi). Syöte: meteoalarm-legacy-atom-<nimi>
MA_COUNTRIES = {"austria": "Itävalta", "belgium": "Belgia", "bosnia-herzegovina": "Bosnia ja Hertsegovina",
                "bulgaria": "Bulgaria", "croatia": "Kroatia", "cyprus": "Kypros", "czechia": "Tšekki",
                "denmark": "Tanska", "estonia": "Viro", "finland": "Suomi", "france": "Ranska", "germany": "Saksa",
                "greece": "Kreikka", "hungary": "Unkari", "iceland": "Islanti", "ireland": "Irlanti",
                "israel": "Israel", "italy": "Italia", "latvia": "Latvia", "lithuania": "Liettua",
                "luxembourg": "Luxemburg", "malta": "Malta", "moldova": "Moldova", "montenegro": "Montenegro",
                "netherlands": "Alankomaat", "republic-of-north-macedonia": "Pohjois-Makedonia", "norway": "Norja",
                "poland": "Puola", "portugal": "Portugali", "romania": "Romania", "serbia": "Serbia",
                "slovakia": "Slovakia", "slovenia": "Slovenia", "spain": "Espanja", "sweden": "Ruotsi",
                "switzerland": "Sveitsi", "ukraine": "Ukraina", "united-kingdom": "Iso-Britannia"}
MA_FEED = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{}"

DEFAULT_SERVICES = {
    "location": {"lat": None, "lon": None, "label": ""},
    "lightning": {"enabled": False, "radius_km": 30, "cooldown_min": 30, "ground_only": False, "sms": False},
    "warnings": {"enabled": False, "source": "fmi", "country": "", "areas": [], "min_color": "yellow",
                 "traffic_weather": True, "sms": False},
    "space": {"enabled": False, "kp_threshold": 5, "forecasts": True, "flares": True, "sms": False},
    "aurora": {"enabled": False, "source": "fmi", "threshold_nt": 100, "min_prob": 10, "dark_only": True,
               "skip_overcast": False, "sms": False},
    "radiation": {"enabled": False, "threshold": 0.4, "radius_km": 100, "sms": False},
    "traffic": {"enabled": False, "use_location": True, "radius_km": 30, "provinces": [], "roads": [],
                "types": ["TRAFFIC_ANNOUNCEMENT"], "updates": False, "details": False, "sms": False},
    "satellites": {"enabled": False, "lead_min": 30, "min_el": 20, "alert_sats": [25544], "iss_visible": True,
                   "only_alerted": False, "sms": False},
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
        km = dist(nearest["km"])
        txt = tr("Salama {km} {dir}, {n} iskua/15 min", km=km, dir=nearest["dir"], n=n15)
        self.alert(tr("⚡ {kind} {km} {dir}", kind=kind.capitalize(), km=km, dir=nearest["dir"]),
                   tr("{n} iskua 15 min aikana {r} säteellä", n=n15, r=dist(self.cfg.get("radius_km"))), txt, "bad")


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


def parse_meteoalarm(xml: str) -> list[dict]:
    """MeteoAlarmin Atom-syöte: yksi entry per varoitus ja alue. Palauttaa varoitukset ryhmiteltyinä
    tunnisteen mukaan, alueina nimi, EMMA_ID ja mahdolliset monikulmiot."""
    ns = {"a": "http://www.w3.org/2005/Atom", "c": "urn:oasis:names:tc:emergency:cap:1.2"}
    root = ET.fromstring(xml)
    out: dict[str, dict] = {}
    for e in root.findall("a:entry", ns):
        g = lambda tag: (e.findtext(f"c:{tag}", default="", namespaces=ns) or "").strip()  # noqa: E731
        ident = g("identifier") or (e.findtext("a:id", "", ns) or "")
        title = (e.findtext("a:title", "", ns) or "").strip()
        m = re.match(r"(yellow|orange|red)\b", title, re.I)
        color = m.group(1).lower() if m else {"Severe": "orange", "Extreme": "red"}.get(g("severity"), "yellow")
        polys = []
        for p in e.findall("c:polygon", ns):
            pts = []
            for pair in (p.text or "").split():
                try:
                    la, lo = pair.split(",")
                    pts.append((float(la), float(lo)))
                except ValueError:
                    continue
            if len(pts) >= 3:
                polys.append(pts)
        emma = ""
        for gc in e.findall("c:geocode", ns):
            vn = next((x.text for x in gc if x.tag.endswith("valueName")), "")
            if vn == "EMMA_ID":
                emma = (next((x.text for x in gc if x.tag.split("}")[-1] == "value"), "") or "").strip()
        w = out.setdefault(ident, {
            "id": ident, "msgType": g("message_type") or g("msgType") or "Alert", "references": "",
            "sent": _iso_to_ts(g("sent")), "event": g("event"), "code": "", "severity": g("severity"),
            "headline": re.sub(r"\s+-\s+[^-]*$", "", title) or g("event"), "description": "",
            "onset": _iso_to_ts(g("onset")) or _iso_to_ts(g("effective")), "expires": _iso_to_ts(g("expires")),
            "color": color, "areas": [], "params": {}})
        w["areas"].append({"name": g("areaDesc"), "emma": emma, "polygons": polys})
    return list(out.values())


def ma_areas(items: list[dict]) -> list[dict]:
    """Syötteen alueet valintalistaksi (nimi ja EMMA_ID)."""
    seen = {}
    for w in items:
        for a in w["areas"]:
            if a["name"]:
                seen[(a["emma"], a["name"])] = bool(a["polygons"])
    return [{"id": k[0], "name": k[1], "poly": v} for k, v in sorted(seen.items(), key=lambda x: x[0][1].lower())]


def short_ma(w: dict) -> str:
    """Esim. 'Keltainen tuulivaroitus: Wind Level 1, nyt–24'."""
    color = tr(COLORFI.get(w.get("color"), ""))
    span = _span_fi(w.get("onset"), w.get("expires"))
    return f"{color}: {w.get('event') or w.get('headline')}" + (f", {span}" if span else "")


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

    def _match_areas(self, w: dict, lat, lon) -> list[str]:
        """Osuvat alueet: oma sijainti alueen rajojen sisällä tai alue valittu nimellä / EMMA_ID:llä."""
        wanted = {x.strip().lower() for x in self.cfg.get("areas") or [] if x.strip()}
        hit = []
        for a in w["areas"]:
            if lat is not None and any(point_in_polygon(lat, lon, p) for p in a["polygons"]):
                hit.append(a["name"])
            elif wanted and (a["name"].lower() in wanted or (a.get("emma") or "").lower() in wanted):
                hit.append(a["name"])
        return hit

    async def check(self):
        ma = self.cfg.get("source") == "meteoalarm"
        lat, lon = self.loc or (None, None)
        if ma:
            country = self.cfg.get("country") or ""
            if country not in MA_COUNTRIES:
                raise ValueError(tr("Valitse maa"))
            items = parse_meteoalarm(await fetch_text(MA_FEED.format(country), 60))
            has_poly = any(a["polygons"] for w in items for a in w["areas"])
            if not self.cfg.get("areas") and (lat is None or (items and not has_poly)):
                raise ValueError(tr("Valitse alue: tämän maan varoituksissa ei ole aluerajoja, joten sijaintia ei voi käyttää"
                                    if items and not has_poly else "Aseta oma sijainti tai valitse alue"))
        else:
            if lat is None:
                raise ValueError(tr("Aseta oma sijainti"))
            items = parse_cap_feed(await fetch_text(self.FEED, 60))
        min_level = COLORS.get(self.cfg.get("min_color", "yellow"), 1)
        now = time.time()
        mine = []
        for w in items:
            hit = self._match_areas(w, lat, lon) if ma else \
                [a["name"] for a in w["areas"] if any(point_in_polygon(lat, lon, p) for p in a["polygons"])]
            if not hit:
                continue
            if ma and w["msgType"] == "Cancel":
                continue
            if not self.cfg.get("traffic_weather", True) and w["code"] == "trafficWeather":
                continue
            if COLORS.get(w["color"], 1) < min_level and w["msgType"] != "Cancel":
                continue
            w = {k: v for k, v in w.items() if k != "areas"}
            w["here"] = hit[0]
            w["short"] = short_ma(w) if ma else short_warning(w)
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
                prev = {self.alerted_headlines.get(r) for r in refs} if refs else set(self.alerted_headlines.values())
                if (w["short"] if not refs else w["headline"]) in prev:
                    self.alerted_headlines[w["id"]] = w["headline"]
                    continue
            self.alerted_headlines[w["id"]] = w["short"] if ma else w["headline"]
            color = {"yellow": "Keltainen", "orange": "Oranssi", "red": "Punainen"}.get(w["color"], "")
            level = "bad" if w["color"] in ("orange", "red") else "warn"
            body = f"{w['description'] or w['headline']} ({w['here']})"
            self.alert(f"⚠ {w['short']}", body, w["short"] + (f" ({w['here']})" if ma else ""),
                       level if w["msgType"] != "Cancel" else "info")
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


OPEN_METEO = "https://api.open-meteo.com/v1/forecast?latitude={lat:.3f}&longitude={lon:.3f}&current=cloud_cover"


def in_finland(lat: float, lon: float) -> bool:
    """Karkea rajaus: Ilmatieteen laitoksen havaintoasemien alue."""
    return 59.3 <= lat <= 70.2 and 19.0 <= lon <= 31.7


async def fetch_clouds_any(lat: float, lon: float, cache: dict) -> tuple[float | None, str]:
    """Pilvisyys oktina: Suomessa lähimmän IL:n havaintoaseman havainto, muualla (tai jos havaintoa ei ole)
    Open-Meteon malliarvo (CC BY 4.0). Open-Meteo haetaan enintään 30 min välein."""
    if in_finland(lat, lon):
        try:
            c, st = await fetch_clouds(lat, lon)
            if c is not None:
                return c, st
        except Exception:  # noqa: BLE001
            pass
    key = (round(lat, 2), round(lon, 2))
    hit = cache.get("om")
    if hit and hit[0] == key and time.time() - hit[1] < 1800:
        return hit[2], "Open-Meteo"
    try:
        d = await fetch_json(OPEN_METEO.format(lat=lat, lon=lon), 30)
        pct = (d.get("current") or {}).get("cloud_cover")
        okta = None if pct is None else round(float(pct) / 100 * 8, 1)
    except Exception:  # noqa: BLE001
        return (hit[2], "Open-Meteo") if hit and hit[0] == key else (None, "")
    cache["om"] = (key, time.time(), okta)
    return okta, "Open-Meteo"


def ovation_prob(data: dict, lat: float, lon: float, radius_km: float = 300) -> tuple[int, int]:
    """NOAA OVATION -ruudukosta (1° x 1°, pituuspiiri 0–359) todennäköisyys omalla kohdalla ja suurin
    arvo radius_km säteellä. Revontulet näkyvät taivaanrannassa kauempaakin kuin suoraan yläpuolella."""
    lon360 = lon % 360
    here, best = 0, 0
    for c in data.get("coordinates") or []:
        try:
            clon, clat, v = float(c[0]), float(c[1]), float(c[2])
        except (TypeError, ValueError, IndexError):
            continue
        if abs(clat - lat) > radius_km / 111 + 1:
            continue
        dlon = min(abs(clon - lon360), 360 - abs(clon - lon360))
        if round(clat) == round(lat) and dlon < 0.5:
            here = max(here, int(v))
        if distance_km(lat, lon, clat, clon if clon <= 180 else clon - 360) <= radius_km:
            best = max(best, int(v))
    return here, max(best, here)


class Aurora(Service):
    name = "aurora"
    title = "Revontulihälytys"
    interval = 300
    WFS = "https://opendata.fmi.fi/wfs"
    OVATION = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
    RADIUS = 300

    def __init__(self, hub):
        super().__init__(hub)
        self.level = 0          # 0 = ei, 1 = mahdollisia, 2 = todennäköisiä, 3 = voimakkaita
        self.stations: list[dict] = []

    async def _check_noaa(self, lat, lon):
        data = await fetch_json(self.OVATION, 60)
        here, prob = ovation_prob(data, lat, lon, self.RADIUS)
        thr = float(self.cfg.get("min_prob") or 10)
        lvl = 3 if prob >= min(100, 4 * thr) else 2 if prob >= min(100, 2 * thr) else 1 if prob >= thr else 0
        self.stations = []
        return {"source": "noaa", "prob": prob, "prob_here": here, "radius": self.RADIUS,
                "forecast_time": _iso_to_ts(data.get("Forecast Time")), "nearest": None}, lvl, prob, thr

    async def check(self):
        if not self.loc:
            raise ValueError(tr("Aseta oma sijainti"))
        lat, lon = self.loc
        if self.cfg.get("source") == "noaa":
            info, lvl, val, thr = await self._check_noaa(lat, lon)
            await self._decide(lat, lon, info, lvl, val, thr,
                               lambda word, cl: tr("Revontulet {word}: NOAA-ennuste {p} %{cl}", word=word, p=val, cl=cl),
                               tr("NOAA:n OVATION-ennuste: näkyvien revontulien todennäköisyys {r} säteellä. "
                                  "VHF-revontulikelit mahdollisia.", r=dist(self.RADIUS)))
            return
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
        await self._decide(lat, lon, {"source": "fmi", "nearest": near}, lvl, rng, thr,
                           lambda word, cl: tr("Revontulet {word}: {name} {rng} nT/h{cl}", word=word, name=near["name"],
                                               rng=rng, cl=cl),
                           tr("Magneettinen aktiivisuus lähimmällä magnetometrillä ({name}, {km}). "
                              "VHF-revontulikelit mahdollisia.", name=near["name"], km=dist(near["km"])))

    async def _decide(self, lat, lon, info: dict, lvl: int, val: float, thr: float, text, body: str):
        """Yhteinen hälytyslogiikka: uusi hälytys, kun taso nousee ja revontulet voivat näkyä."""
        elev = sun_elevation(lat, lon)
        dark = elev < -6
        if not hasattr(self, "_cloud_cache"):
            self._cloud_cache = {}
        clouds, cloud_st = await fetch_clouds_any(lat, lon, self._cloud_cache)
        for k in ("prob", "prob_here", "radius", "forecast_time"):
            self.status.pop(k, None)
        self.status.update(**info, sun=round(elev, 1), dark=dark, clouds=clouds, cloud_station=cloud_st, level=lvl)
        if not self.primed:
            self.primed = True
            self.level = lvl if (dark or not self.cfg.get("dark_only", True)) else 0
            return
        visible_ok = dark or not self.cfg.get("dark_only", True)
        if self.cfg.get("skip_overcast") and clouds is not None and clouds >= 7:
            visible_ok = False
        if lvl == 0 and val < thr * 0.6:
            self.level = 0
            return
        if lvl > self.level and visible_ok:
            self.level = lvl
            word = tr({1: "mahdollisia", 2: "todennäköisiä", 3: "voimakkaita"}[lvl])
            cl = tr(", pilvisyys {c}/8", c=int(clouds)) if clouds is not None else ""
            txt = text(word, cl)
            self.alert(f"🌌 {txt}", body, txt, "warn" if lvl < 3 else "bad")


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
        dist_ = f", {dist(top['km'])}" if "km" in top else ""
        txt = tr("Säteily koholla: {name} {dr} µSv/h{more}", name=top["name"], dr=num(top["dr"], 2), more=more)
        self.alert(f"☢ {txt}", tr("Raja {thr} µSv/h{dist}. Normaali taso Suomessa n. 0,05–0,3 µSv/h. "
                                 "Tarkista tilanne STUK:n sivuilta.", thr=num(thr, 2), dist=dist_),
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
            d_km = None
            if center:
                d_km = _min_dist(center, f.get("geometry"))
            why = []
            if center and d_km is not None and d_km <= radius:
                why.append(dist(d_km))
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
                          "municipality": ", ".join(dict.fromkeys(muni)), "km": d_km, "why": "; ".join(why),
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
# --------------------------------------------------------------------------
# Satelliittien ylilennot
# --------------------------------------------------------------------------
def _hm(ts: float) -> str:
    return time.strftime("%H.%M" if get_lang() == "fi" else "%H:%M", time.localtime(ts))


def _compass(az: float) -> str:
    return bearing(int((az + 22.5) // 45))


class Satellites(Service):
    name = "satellites"
    title = "Satelliittien ylilennot"
    interval = 60
    RECOMPUTE = 1800

    def __init__(self, hub):
        super().__init__(hub)
        cache = None
        try:
            cache = hub.mon.cfg_path.parent / "satellites.json"
        except AttributeError:
            pass
        self.data = sat.SatData(cache)
        self.passes: list[dict] = []
        self.computed = (0.0, None)
        self.alerted: dict[tuple, float] = {}

    def _compute(self, lat, lon) -> list[dict]:
        out = []
        start = time.time() - 1200          # myös käynnissä oleva ylilento
        for n in self.data.active():
            try:
                orb = sat.Orbit(n, self.data.tle[n])
                out += sat.find_passes(orb, lat, lon, start, 25, 30, visible=(n == sat.ISS))
            except Exception as e:  # noqa: BLE001 – yksi rikkinäinen ratatieto ei kaada muita
                log.warning("Satelliitti %s: %s", n, e)
        return sorted(out, key=lambda p: p["aos"])

    async def _refresh(self):
        tle = await fetch_text(sat.TLE_URL, 60)
        tx = await fetch_json(sat.TX_URL, 120)
        self.data.update(tle, tx)

    def freqs(self, norad: int) -> list[str]:
        return [sat.freq_text(t) for t in self.data.tx.get(norad, [])[:3]]

    def _alert_text(self, p) -> tuple[str, str, str]:
        vis = p.get("visible") if self.cfg.get("iss_visible", True) else None
        mins = max(0, round((p["aos"] - time.time()) / 60))
        title = tr("🛰 {name} ylittää klo {t} ({m} min)", name=p["name"], t=_hm(p["aos"]), m=mins)
        body = tr("Nousu {a} {az1}, korkein {el}° {az2} klo {tca}, lasku {l} {az3}.", a=_hm(p["aos"]),
                  az1=_compass(p["aos_az"]), el=p["max_el"], az2=_compass(p["tca_az"]), tca=_hm(p["tca"]),
                  l=_hm(p["los"]), az3=_compass(p["los_az"]))
        if vis:
            body += " " + tr("Näkyy taivaalla klo {a}–{b} (korkeimmillaan {el}°).", a=_hm(vis["start"]),
                             b=_hm(vis["end"]), el=vis["max_el"])
        fr = self.freqs(p["norad"])
        if fr:
            body += " " + tr("Taajuudet: {f}.", f=", ".join(fr))
        sms = tr("{name} klo {t} max {el}° {az}", name=p["name"], t=_hm(p["aos"]), el=p["max_el"], az=_compass(p["tca_az"]))
        if vis:
            sms += " " + tr("näkyy {a}–{b}", a=_hm(vis["start"]), b=_hm(vis["end"]))
        elif fr:
            sms += " " + fr[0]
        return title, body, sms

    def wanted(self, p) -> bool:
        """Täyttääkö ylilento rajat: korkeuskulma tai (ISS) näkyvä ylilento."""
        if p["max_el"] >= float(self.cfg.get("min_el") or 0):
            return True
        return bool(p.get("visible") and self.cfg.get("iss_visible", True))

    async def check(self):
        if not self.loc:
            raise ValueError(tr("Aseta oma sijainti"))
        lat, lon = self.loc
        if self.data.stale():
            try:
                await self._refresh()
            except Exception as e:  # noqa: BLE001
                if not self.data.tle:
                    raise
                log.warning("Satelliittitietojen päivitys: %s (käytetään vanhoja)", friendly_error(e))
        now = time.time()
        key = (lat, lon, self.data.fetched)
        if now - self.computed[0] > self.RECOMPUTE or self.computed[1] != key:
            self.passes = await asyncio.get_running_loop().run_in_executor(None, self._compute, lat, lon)
            self.computed = (now, key)
        lead = float(self.cfg.get("lead_min") or 30) * 60
        alert_sats = set(self.cfg.get("alert_sats") or [])
        for p in self.passes:
            k = (p["norad"], p["aos"] // 60)
            if p["norad"] in alert_sats and self.wanted(p) and 0 < p["aos"] - now <= lead and k not in self.alerted:
                self.alerted[k] = now
                title, body, sms = self._alert_text(p)
                self.alert(title, body, sms, "info")
        self.alerted = {k: v for k, v in self.alerted.items() if now - v < 86400}
        self.status.update(sats=len(self.data.active()), data_time=self.data.fetched or None)

    def upcoming(self) -> list[dict]:
        now = time.time()
        alert_sats = set(self.cfg.get("alert_sats") or [])
        out = []
        for p in self.passes:
            if p["los"] < now or not self.wanted(p):
                continue
            if self.cfg.get("only_alerted") and p["norad"] not in alert_sats:
                continue
            out.append({**p, "freqs": self.freqs(p["norad"]), "alert": p["norad"] in alert_sats,
                        "visible": p.get("visible") if self.cfg.get("iss_visible", True) else None})
            if len(out) >= 25:
                break
        return out

    def map_data(self) -> dict:
        """Satelliitit-välilehti: hälytettävien satelliittien paikat, maajäljet, seuraava ylilento
        ja sen taivaskartan reitti."""
        now = time.time()
        lat, lon = self.loc or (None, None)
        out = {"time": now, "sun": sat.sun_subpoint(now), "location": None if lat is None else [lat, lon],
               "enabled": bool(self.cfg.get("enabled")), "sats": []}
        cache = getattr(self, "_orbits", {})
        for n in sorted(set(self.cfg.get("alert_sats") or [])):
            if n not in self.data.tle:
                continue
            orb = cache.get(n)
            if orb is None or orb.name != self.data.tle[n]["name"] or getattr(orb, "_l1", None) != self.data.tle[n]["l1"]:
                orb = sat.Orbit(n, self.data.tle[n])
                orb._l1 = self.data.tle[n]["l1"]
                cache[n] = orb
            tr_ = sat.track(orb, now, lat, lon)
            if not tr_:
                continue
            nxt = next((p for p in self.passes if p["norad"] == n and p["los"] > now), None)
            item = {"norad": n, "name": sat.short_name(self.data.tle[n]["name"]), "freqs": self.freqs(n), **tr_,
                    "pass": None}
            if nxt and lat is not None:
                item["pass"] = {**{k: nxt[k] for k in ("aos", "los", "tca", "max_el", "aos_az", "tca_az", "los_az")},
                                "visible": nxt.get("visible") if self.cfg.get("iss_visible", True) else None,
                                "path": sat.sky_path(orb, nxt["aos"], nxt["los"], lat, lon)}
            out["sats"].append(item)
        self._orbits = cache
        return out

    def sat_list(self) -> list[dict]:
        return [{"norad": n, "name": sat.short_name(self.data.tle[n]["name"]), "freqs": self.freqs(n)}
                for n in self.data.active()]


class ServiceHub:
    def __init__(self, mon):
        self.mon = mon
        self.services = {s.name: s for s in (Lightning(self), Warnings(self), SpaceWeather(self), Aurora(self),
                                             Radiation(self), Traffic(self), Satellites(self))}

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
            for n in ("lightning", "warnings", "traffic", "aurora", "radiation", "satellites"):
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
        SA = self.services["satellites"]
        out["satellites"] = {**SA.status, "passes": SA.upcoming(), "sat_list": SA.sat_list()}
        out["provinces"] = PROVINCES
        out["ma_countries"] = {k: tr(v) for k, v in sorted(MA_COUNTRIES.items(), key=lambda x: tr(x[1]))}
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
            out["location"] = {"lat": round(lat, 5), "lon": round(lon, 5), "label": loc.get("label") or f"{lat:.5f}, {lon:.5f}"}
    for sec in ("lightning", "warnings", "space", "traffic", "aurora", "radiation", "satellites"):
        if sec in new:
            setb(sec, "enabled")
            setb(sec, "sms")
    setn("lightning", "radius_km", 1, 300)
    setn("lightning", "cooldown_min", 0, 1440)
    setb("lightning", "ground_only")
    nw = new.get("warnings", {})
    if "source" in nw:
        out["warnings"]["source"] = "meteoalarm" if nw["source"] == "meteoalarm" else "fmi"
    if "country" in nw:
        c = str(nw["country"] or "")
        if c and c not in MA_COUNTRIES:
            raise ValueError(tr("Tuntematon maa: {c}", c=c))
        out["warnings"]["country"] = c
    if "areas" in nw:
        a = nw["areas"]
        a = re.split(r"[;\n]" if ";" in a or "\n" in a else ",", a) if isinstance(a, str) else a
        out["warnings"]["areas"] = [str(x).strip()[:80] for x in a if str(x).strip()][:30]
    if out["warnings"].get("source") == "meteoalarm" and out["warnings"].get("enabled") and not out["warnings"].get("country"):
        raise ValueError(tr("Valitse maa"))
    if new.get("aurora", {}).get("source") in ("fmi", "noaa"):
        out["aurora"]["source"] = new["aurora"]["source"]
    setn("aurora", "min_prob", 1, 100)
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
    setn("satellites", "lead_min", 1, 240)
    setn("satellites", "min_el", 0, 90)
    setb("satellites", "iss_visible")
    setb("satellites", "only_alerted")
    if "alert_sats" in new.get("satellites", {}):
        ids = []
        for x in new["satellites"]["alert_sats"] or []:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        out["satellites"]["alert_sats"] = sorted(set(ids))[:100]
    setb("traffic", "use_location")
    setb("traffic", "updates")
    setb("traffic", "details")
    setn("traffic", "radius_km", 0, 500)
    trf = new.get("traffic", {})
    if "provinces" in trf:
        out["traffic"]["provinces"] = [p for p in trf["provinces"] if p in PROVINCES]
    if "roads" in trf:
        r = trf["roads"]
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
    if "types" in trf:
        out["traffic"]["types"] = [t for t in trf["types"] if t in TRAFFIC_TYPES] or ["TRAFFIC_ANNOUNCEMENT"]
    if "center" in trf:
        c = str(trf["center"] or "").strip()
        if c:
            lat, lon = parse_location(c)
            out["traffic"]["center"] = {"lat": lat, "lon": lon, "label": c}
        else:
            out["traffic"].pop("center", None)
    for k in out:
        if out[k] != old.get(k):
            changed.add(k)
    for sec in ("lightning", "warnings", "traffic", "aurora", "radiation", "satellites"):
        if sec == "radiation" and not out[sec].get("radius_km"):
            continue
        if sec == "warnings" and out[sec].get("source") == "meteoalarm" and out[sec].get("areas"):
            continue
        if out[sec].get("enabled") and out["location"].get("lat") is None and not (
                sec == "traffic" and (not out["traffic"].get("use_location") and out["traffic"].get("center")
                                      or out["traffic"]["provinces"] or out["traffic"]["roads"])):
            raise ValueError(tr("Aseta ensin oma sijainti (koordinaatit tai lokaattori)"))
    return out, changed
