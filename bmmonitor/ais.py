"""Laivat (AIS) APRS-kartalle.

Lähteet:
- Fintraffic Digitraffic (meri.digitraffic.fi): Suomi ja Itämeri, ei avainta. Sijainnit pisteen ympäriltä
  (/api/ais/v1/locations?latitude=&longitude=&radius= km) minuutin välein ja alusten tiedot (/api/ais/v1/vessels)
  kuuden tunnin välein. Pyynnöissä Digitraffic-User-otsake (ilman sitä rajoitetaan, HTTP 429).
  Lisenssi CC 4.0 BY: "Lähde: Fintraffic / digitraffic.fi, lisenssi CC 4.0 BY".
- aisstream.io: koko maailma, käyttäjän oma ilmainen API-avain (salasanavarastossa). WebSocket-virta,
  tilausviesti 3 s sisällä yhteyden avaamisesta. Avain vain ohjelman taustaosassa, ei koskaan selaimessa.

Laivoja vain näytetään kartalla; mitään ei lähetetä eteenpäin. Kotisijainti pyöristetään pyynnöissä 0,1 asteeseen.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time

from . import __version__, secret_store
from .i18n import tr

log = logging.getLogger("bmmonitor.ais")

DIGITRAFFIC = "https://meri.digitraffic.fi/api/ais/v1"
AISSTREAM = "wss://stream.aisstream.io/v0/stream"
SOURCES = ("digitraffic", "aisstream")
RADII = (50, 100, 200, 450)
GROUPS = ("cargo", "tanker", "passenger", "special", "fishing", "pleasure", "other")
DEFAULT_TYPES = ["cargo", "tanker", "passenger", "special", "fishing"]
LOC_INTERVAL = 60          # s: Digitraffic-sijaintien haku
META_INTERVAL = 6 * 3600   # s: Digitraffic-alustietojen haku
MAX_AGE = 3600             # s: tätä vanhempi sijainti ei näy
DEFAULT_AIS = {
    "enabled": False,       # laivataso päällä (kartan tasovalinta)
    "source": "digitraffic",
    "km": 100,
    "types": list(DEFAULT_TYPES),
    "hide_moored": False,   # piilota laiturissa (navStat 5) ja ankkurissa (1) olevat
    # "key": aisstream.io-avain salasanavarastossa (aisstream-api-key)
}


def ship_group(t) -> str:
    """AIS-alustyyppi (0–99) → ryhmä."""
    try:
        t = int(t or 0)
    except (TypeError, ValueError):
        return "other"
    if 70 <= t <= 79:
        return "cargo"
    if 80 <= t <= 89:
        return "tanker"
    if 60 <= t <= 69 or 40 <= t <= 49:     # matkustaja-alukset ja nopeat alukset (pikalautat)
        return "passenger"
    if t == 30:
        return "fishing"
    if t in (36, 37):
        return "pleasure"
    if t in (31, 32, 33, 34, 35) or 50 <= t <= 59:   # hinaajat, luotsit, SAR, viranomaiset, jäänmurtajat ym.
        return "special"
    return "other"


def normalize_ais(new: dict, old: dict) -> dict:
    out = {**DEFAULT_AIS, **(old or {})}
    out.pop("key", None)
    if "enabled" in new:
        out["enabled"] = bool(new["enabled"])
    if "hide_moored" in new:
        out["hide_moored"] = bool(new["hide_moored"])
    if "source" in new:
        if new["source"] not in SOURCES:
            raise ValueError(tr("Virheellinen valinta"))
        out["source"] = new["source"]
    if "km" in new:
        try:
            km = int(new["km"])
        except (TypeError, ValueError):
            raise ValueError(tr("Virheellinen arvo: {x}", x=new["km"])) from None
        if km not in RADII:
            raise ValueError(tr("Sallitut arvot: {v}", v=", ".join(map(str, RADII))))
        out["km"] = km
    if "types" in new:
        t = new["types"] or []
        if not isinstance(t, list) or any(x not in GROUPS for x in t):
            raise ValueError(tr("Virheellinen valinta"))
        out["types"] = [g for g in GROUPS if g in t]
    return out


def _dist_km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _num(v, scale=1.0, bad=None):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if bad is not None and v >= bad:
        return None
    return round(v * scale, 2)


class AisFeed:
    """Laivojen sijainnit valitulta säteeltä kotisijainnista."""

    def __init__(self, mon, demo: bool = False):
        self.mon = mon
        self.demo = demo
        self.ships: dict[int, dict] = {}
        self.meta: dict[int, dict] = {}
        self.meta_time = 0.0
        self.state = {"ok": None, "error": None, "updated": None}
        self._wake = asyncio.Event()
        self.key, moved = secret_store.load_field(self.cfg, "key", "aisstream-api-key")
        if moved:
            mon.save_config()

    @property
    def cfg(self) -> dict:
        return self.mon.cfg["ais"]

    def home(self):
        loc = self.mon.cfg["services"]["location"]
        return (loc["lat"], loc["lon"]) if loc.get("lat") is not None else None

    def set_key(self, key: str):
        self.key = (key or "").strip()
        secret_store.save_field(self.cfg, "key", "aisstream-api-key", self.key)
        self.reconfigure()

    def reconfigure(self):
        self.ships.clear()
        self.state = {"ok": None, "error": None, "updated": None}
        self._wake.set()

    def _ua(self) -> str:
        return f"MonitorForAmateurRadio/{__version__}"

    # ---------------- Digitraffic ----------------
    @staticmethod
    def parse_locations(data: dict) -> list[dict]:
        out = []
        for f in (data or {}).get("features") or []:
            p = f.get("properties") or {}
            g = (f.get("geometry") or {}).get("coordinates") or []
            mmsi = p.get("mmsi") or f.get("mmsi")
            if not mmsi or len(g) < 2:
                continue
            ts = p.get("timestampExternal") or p.get("timestamp")
            out.append({"mmsi": int(mmsi), "lat": float(g[1]), "lon": float(g[0]),
                        "sog": _num(p.get("sog"), bad=102.3), "cog": _num(p.get("cog"), bad=360),
                        "heading": _num(p.get("heading"), bad=360), "nav": p.get("navStat"),
                        "ts": (ts / 1000.0) if ts and ts > 1e11 else ts})
        return out

    @staticmethod
    def parse_vessel(v: dict) -> dict:
        a, b = v.get("referencePointA") or 0, v.get("referencePointB") or 0
        c, d = v.get("referencePointC") or 0, v.get("referencePointD") or 0
        return {"name": (v.get("name") or "").strip(), "call": (v.get("callSign") or "").strip(),
                "type": v.get("shipType"), "dest": (v.get("destination") or "").strip(),
                "draught": round(v["draught"] / 10.0, 1) if v.get("draught") else None,
                "length": (a + b) or None, "width": (c + d) or None, "imo": v.get("imo") or None}

    async def _get(self, sess, path: str, params: dict | None = None):
        h = {"Digitraffic-User": self._ua(), "Accept-Encoding": "gzip", "Accept": "application/json"}
        async with sess.get(DIGITRAFFIC + path, params=params, headers=h) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return await r.json(content_type=None)

    async def _digitraffic_once(self, home):
        from .messaging import http_session
        lat, lon = round(home[0], 1), round(home[1], 1)
        async with http_session(60) as sess:
            if time.time() - self.meta_time > META_INTERVAL:
                vessels = await self._get(sess, "/vessels")
                self.meta = {int(v["mmsi"]): self.parse_vessel(v) for v in vessels or [] if v.get("mmsi")}
                self.meta_time = time.time()
            data = await self._get(sess, "/locations", {"latitude": lat, "longitude": lon, "radius": self.cfg["km"]})
        now = time.time()
        fresh = {}
        for s in self.parse_locations(data):
            if s["ts"] and now - s["ts"] > MAX_AGE:
                continue
            fresh[s["mmsi"]] = {**self.meta.get(s["mmsi"], {}), **s}
        self.ships = fresh
        self.state = {"ok": True, "error": None, "updated": now}
        self.mon.dirty = True

    # ---------------- aisstream.io ----------------
    def bbox(self, home) -> list:
        km = self.cfg["km"]
        dlat = km / 111.0
        dlon = km / (111.0 * max(0.1, math.cos(math.radians(home[0]))))
        la, lo = round(home[0], 1), round(home[1], 1)
        return [[[la - dlat, lo - dlon], [la + dlat, lo + dlon]]]

    def handle_stream(self, msg: dict, home):
        """aisstream.io-viesti → alustaulukko. Palauttaa True, jos jotain muuttui."""
        meta = msg.get("MetaData") or {}
        mmsi = meta.get("MMSI")
        if not mmsi:
            return False
        mmsi = int(mmsi)
        kind = msg.get("MessageType")
        body = (msg.get("Message") or {}).get(kind) or {}
        s = self.ships.get(mmsi) or {"mmsi": mmsi}
        if kind in ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"):
            lat, lon = body.get("Latitude", meta.get("latitude")), body.get("Longitude", meta.get("longitude"))
            if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
                return False
            if _dist_km(home, (lat, lon)) > self.cfg["km"]:
                return False
            s.update(lat=float(lat), lon=float(lon), sog=_num(body.get("Sog"), bad=102.3), cog=_num(body.get("Cog"), bad=360),
                     heading=_num(body.get("TrueHeading"), bad=360), ts=time.time())
            if "NavigationalStatus" in body:
                s["nav"] = body["NavigationalStatus"]
            if kind == "ExtendedClassBPositionReport" and body.get("Type"):
                s["type"] = body["Type"]
        elif kind in ("ShipStaticData", "StaticDataReport"):
            if kind == "StaticDataReport":       # luokka B: osat A (nimi) ja B (tyyppi, kutsu, mitat)
                body = {**(body.get("ReportA") or {}), **(body.get("ReportB") or {})}
                body.setdefault("Type", body.get("ShipType"))
            dim = body.get("Dimension") or {}
            s.update(name=(body.get("Name") or s.get("name") or "").strip(),
                     call=(body.get("CallSign") or s.get("call") or "").strip(),
                     type=body.get("Type") or s.get("type"), dest=(body.get("Destination") or s.get("dest") or "").strip(),
                     draught=body.get("MaximumStaticDraught") or s.get("draught"),
                     length=((dim.get("A") or 0) + (dim.get("B") or 0)) or s.get("length"),
                     imo=body.get("ImoNumber") or s.get("imo"))
        else:
            return False
        if not s.get("name") and meta.get("ShipName"):
            s["name"] = meta["ShipName"].strip()
        self.ships[mmsi] = s
        return True

    async def _aisstream(self, home):
        import aiohttp
        from .messaging import http_session
        async with http_session(None) as sess:
            async with sess.ws_connect(AISSTREAM, heartbeat=30, headers={"User-Agent": self._ua()}) as ws:
                await ws.send_str(json.dumps({"APIKey": self.key, "BoundingBoxes": self.bbox(home),
                                              "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport",
                                                                     "ExtendedClassBPositionReport", "ShipStaticData",
                                                                     "StaticDataReport"]}))
                self.state = {"ok": True, "error": None, "updated": time.time()}
                self.mon.dirty = True
                last_push = 0.0
                async for m in ws:
                    if self._wake.is_set():
                        return
                    if m.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        try:
                            d = json.loads(m.data)
                        except ValueError:
                            continue
                        if isinstance(d, dict) and d.get("error"):
                            raise RuntimeError(str(d["error"]))
                        if self.handle_stream(d, home) and time.time() - last_push > 5:
                            last_push = time.time()
                            self.state["updated"] = last_push
                            self.prune()
                            self.mon.dirty = True
                    elif m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        raise RuntimeError(tr("Yhteys suljettu"))

    # ---------------- demo ----------------
    def _demo_once(self, home):
        """Keksityt laivat kodin ympärillä (demotila ja testaus)."""
        rnd = random.Random(42)
        now = time.time()
        if not self.ships:
            names = [("FINNLADY", 70), ("VIKING GRACE", 60), ("BALTIC TANKER", 80), ("LUOTSI 7", 50), ("URHO", 52),
                     ("KALAPOIKA", 30), ("NEITI SININEN", 37), ("MEGASTAR", 60), ("POLARIS", 71), ("SVEA", 36)]
            for i, (n, t) in enumerate(names):
                ang, dist = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.15, 0.9) * min(self.cfg["km"], 120)
                lat = home[0] - abs(math.cos(ang)) * dist / 111.0 * 0.6
                lon = home[1] + math.sin(ang) * dist / (111.0 * math.cos(math.radians(home[0])))
                self.ships[230000000 + i] = {"mmsi": 230000000 + i, "lat": lat, "lon": lon, "name": n, "type": t,
                                             "call": f"OJ{i:03d}", "sog": rnd.choice([0, 0, 8.5, 12.1, 17.3, 21.0]),
                                             "cog": rnd.uniform(0, 359), "heading": None, "nav": 0,
                                             "dest": rnd.choice(["FIHEL", "SESTO", "EETLL", "DEHAM"]),
                                             "draught": round(rnd.uniform(2, 11), 1), "length": rnd.choice([12, 40, 120, 190, 212])}
                if self.ships[230000000 + i]["sog"] == 0:
                    self.ships[230000000 + i]["nav"] = 5
        for s in self.ships.values():
            if s.get("sog"):
                d = s["sog"] * 1.852 / 3600 * LOC_INTERVAL
                s["lat"] += math.cos(math.radians(s["cog"])) * d / 111.0
                s["lon"] += math.sin(math.radians(s["cog"])) * d / (111.0 * math.cos(math.radians(s["lat"])))
            s["ts"] = now
        self.state = {"ok": True, "error": None, "updated": now}
        self.mon.dirty = True

    def prune(self):
        now = time.time()
        for k in [k for k, s in self.ships.items() if now - (s.get("ts") or 0) > MAX_AGE]:
            del self.ships[k]

    async def run(self):
        while True:
            self._wake.clear()
            home = self.home()
            wait = LOC_INTERVAL
            if self.cfg.get("enabled") and home:
                try:
                    if self.demo:
                        self._demo_once(home)
                    elif self.cfg.get("source") == "aisstream":
                        if not self.key:
                            raise ValueError(tr("Aseta aisstream.io-avain"))
                        await self._aisstream(home)
                        wait = 1 if self._wake.is_set() else 30
                    else:
                        await self._digitraffic_once(home)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    from .messaging import friendly_error
                    self.state = {"ok": False, "error": friendly_error(e), "updated": self.state.get("updated")}
                    self.mon.dirty = True
                    log.info("AIS: %s", e)
                    wait = 120
            elif self.ships:
                self.ships.clear()
                self.mon.dirty = True
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except asyncio.TimeoutError:
                pass

    def snapshot(self) -> dict:
        return {**self.state, "enabled": bool(self.cfg.get("enabled")), "source": self.cfg.get("source"),
                "key": bool(self.key), "count": len(self.ships), "home": bool(self.home())}

    def ship_list(self) -> list[dict]:
        out = []
        for s in self.ships.values():
            if s.get("lat") is None:
                continue
            out.append({**s, "group": ship_group(s.get("type"))})
        return out
