"""Radiosää: PSKReporterin vastaanottoraportit (myöhemmin myös RBN ja tropoarvio).

Lähde: mqtt.pskreporter.info (Tom Fanning M0LTE), reaaliaikainen MQTT-jakelu PSK Reporterin
(Philip Gladstone N1DQ) raporteista. Ohjeiden mukaisesti:
- yksi yhteys käyttäjää kohden, tilaukset rajattu omaan kutsuun ja omaan 4-merkkiseen lokaattoriruutuun
- asiakastunnus alkaa ohjelman nimellä
- uudelleenyhdistys kasvavalla viiveellä (5 s → 2 min)
- lähdemerkintä käyttöliittymässä
Raportteja vain näytetään; mitään ei lähetetä eteenpäin. Yhteys vain, kun Radiosää on päällä.

Aiheen rakenne: pskr/filter/v2/{band}/{mode}/{tx_call}/{rx_call}/{tx_grid}/{rx_grid}/{tx_dxcc}/{rx_dxcc}
Raportin kentät: sq, f (Hz), b, md, rp (dB), t, t_tx, sc, sl, sa, rc, rl, ra.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from collections import deque

from . import __version__
from .i18n import tr

log = logging.getLogger("bmmonitor.propagation")

MQTT_HOST = "mqtt.pskreporter.info"
MQTT_PORT = 1884            # MQTT TLS:n yli
KEEPALIVE = 60
KEEP_S = 6 * 3600           # raporttien säilytys muistissa
MAX_SPOTS = 30000
WINDOWS = (15, 30, 60, 180, 360)
BANDS = ("160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m", "4m", "2m", "70cm", "23cm")
HF_BANDS = BANDS[:10]
DEFAULT_PROP = {
    "enabled": False,       # Radiosää päällä (yhteys PSKReporteriin)
    "mine": True,           # missä minut kuultiin (oma kutsu lähettäjänä)
    "area": True,           # oma lokaattoriruutu: täällä kuullut ja täältä kuullut
}


def normalize_prop(new: dict, old: dict) -> dict:
    out = {**DEFAULT_PROP, **(old or {})}
    for k in ("enabled", "mine", "area"):
        if k in new:
            out[k] = bool(new[k])
    return out


# ---------------------------------------------------------------- paikka
_LOC_RE = re.compile(r"^[A-R]{2}\d{2}(?:[A-X]{2}(?:\d{2})?)?", re.I)


def loc_latlon(loc: str | None):
    """Lokaattori (4–10 merkkiä, isot tai pienet) → ruudun keskipiste; virheellinen → None."""
    m = _LOC_RE.match((loc or "").strip())
    if not m:
        return None
    s = m.group(0).upper()
    lon = (ord(s[0]) - 65) * 20 - 180 + int(s[2]) * 2
    lat = (ord(s[1]) - 65) * 10 - 90 + int(s[3])
    w, h = 2.0, 1.0
    if len(s) >= 6:
        w, h = 2 / 24, 1 / 24
        lon += (ord(s[4]) - 65) * w
        lat += (ord(s[5]) - 65) * h
    if len(s) >= 8:
        w, h = w / 10, h / 10
        lon += int(s[6]) * w
        lat += int(s[7]) * h
    return round(lat + h / 2, 4), round(lon + w / 2, 4)


def grid4(lat: float, lon: float) -> str:
    return chr(65 + int((lon + 180) // 20)) + chr(65 + int((lat + 90) // 10)) + \
        str(int(((lon + 180) % 20) // 2)) + str(int((lat + 90) % 10))


def dist_bearing(a, b):
    """Isoympyräetäisyys (km) ja suuntakulma (°) pisteestä a pisteeseen b."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = 2 * 6371.0 * math.asin(min(1.0, math.sqrt(math.sin((la2 - la1) / 2) ** 2 +
                                                   math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return d, (math.degrees(math.atan2(y, x)) + 360) % 360


def band_of(b: str | None, f: float | None = None) -> str:
    b = (b or "").strip().lower()
    if b in BANDS:
        return b
    if f:
        mhz = f / 1e6
        for lo, hi, n in ((1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.0, 5.5, "60m"), (7.0, 7.3, "40m"), (10.1, 10.15, "30m"),
                          (14.0, 14.35, "20m"), (18.068, 18.168, "17m"), (21.0, 21.45, "15m"), (24.89, 24.99, "12m"),
                          (28.0, 29.7, "10m"), (50.0, 54.0, "6m"), (70.0, 71.0, "4m"), (144.0, 148.0, "2m"),
                          (420.0, 450.0, "70cm"), (1240.0, 1300.0, "23cm")):
            if lo <= mhz <= hi:
                return n
    return b or "?"


def base_call(c: str) -> str:
    """Kutsu ilman etu-/jälkiliitteitä (OH/DL1ABC/P → DL1ABC)."""
    parts = [p for p in (c or "").upper().split("/") if p]
    if not parts:
        return ""
    return max(parts, key=lambda p: (bool(re.search(r"\d", p)) and bool(re.search(r"[A-Z]", p)), len(p)))


# ---------------------------------------------------------------- MQTT 3.1.1 (vain tilaus, QoS 0)
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str) -> bytes:
    b = s.encode()
    return len(b).to_bytes(2, "big") + b


def mqtt_connect_packet(client_id: str, keepalive: int = KEEPALIVE) -> bytes:
    body = _str("MQTT") + bytes([4, 0x02]) + keepalive.to_bytes(2, "big") + _str(client_id)   # clean session
    return bytes([0x10]) + _varint(len(body)) + body


def mqtt_subscribe_packet(pid: int, topics: list[str]) -> bytes:
    body = pid.to_bytes(2, "big") + b"".join(_str(t) + b"\x00" for t in topics)
    return bytes([0x82]) + _varint(len(body)) + body


PINGREQ = b"\xc0\x00"
DISCONNECT = b"\xe0\x00"


async def mqtt_read_packet(reader: asyncio.StreamReader):
    """(tyyppi, liput, sisältö)."""
    h = (await reader.readexactly(1))[0]
    mult, n = 1, 0
    for _ in range(4):
        b = (await reader.readexactly(1))[0]
        n += (b & 0x7F) * mult
        if not b & 0x80:
            break
        mult *= 128
    else:
        raise ValueError("MQTT: virheellinen pituus")
    if n > 1 << 20:
        raise ValueError("MQTT: liian suuri viesti")
    return h >> 4, h & 0x0F, (await reader.readexactly(n)) if n else b""


def mqtt_parse_publish(flags: int, body: bytes):
    tl = int.from_bytes(body[:2], "big")
    topic = body[2:2 + tl].decode("utf-8", "replace")
    i = 2 + tl + (2 if (flags >> 1) & 3 else 0)
    return topic, body[i:]


def topics_for(call: str, grid: str, mine: bool, area: bool) -> list[str]:
    t = []
    if mine and call:
        t.append(f"pskr/filter/v2/+/+/{call.replace('/', '.')}/#")
    if area and grid:
        t.append(f"pskr/filter/v2/+/+/+/+/{grid}/#")       # lähettäjä omassa ruudussa
        t.append(f"pskr/filter/v2/+/+/+/+/+/{grid}/#")     # vastaanottaja omassa ruudussa
    return t


# ---------------------------------------------------------------- syöte
class PropFeed:
    def __init__(self, mon, demo: bool = False):
        self.mon = mon
        self.demo = demo
        self.spots: deque = deque(maxlen=MAX_SPOTS)
        self._seen: dict = {}
        self.state = {"ok": None, "error": None, "connected": False, "updated": None, "since": None}
        self._wake = asyncio.Event()
        self.host, self.port, self.tls = MQTT_HOST, MQTT_PORT, True     # testeissä paikallinen palvelin

    @property
    def cfg(self) -> dict:
        return self.mon.cfg["propagation"]

    def home(self):
        loc = self.mon.cfg["services"]["location"]
        return (loc["lat"], loc["lon"]) if loc.get("lat") is not None else None

    def my_call(self) -> str:
        try:
            return self.mon.qso.my_call()
        except Exception:  # noqa: BLE001
            return ""

    def grid(self) -> str:
        h = self.home()
        return grid4(*h) if h else ""

    def reconfigure(self):
        self._wake.set()

    # ---------------- raporttien käsittely
    def add_spot(self, d: dict, src: str = "pskr") -> bool:
        """Yksi raportti (PSKReporterin JSON-kentät) talteen; True jos uusi ja kelvollinen."""
        try:
            sc, rc = str(d.get("sc") or "").upper(), str(d.get("rc") or "").upper()
            t = float(d.get("t_tx") or d.get("t") or 0)
        except (TypeError, ValueError):
            return False
        if not sc or not rc or not t:
            return False
        key = (src, d.get("sq")) if d.get("sq") is not None else (src, sc, rc, d.get("b"), int(t))
        if key in self._seen:
            return False
        sp, rp = loc_latlon(d.get("sl")), loc_latlon(d.get("rl"))
        if not sp or not rp:
            return False
        try:
            f = float(d.get("f") or 0) or None
        except (TypeError, ValueError):
            f = None
        try:
            snr = int(round(float(d["rp"]))) if d.get("rp") is not None else None
        except (TypeError, ValueError):
            snr = None
        km, _ = dist_bearing(sp, rp)
        self._seen[key] = t
        self.spots.append({"t": t, "src": src, "b": band_of(d.get("b"), f), "md": str(d.get("md") or "").upper()[:10],
                           "f": f, "rp": snr, "sc": sc, "sl": str(d.get("sl") or "")[:10], "rc": rc,
                           "rl": str(d.get("rl") or "")[:10], "s": sp, "r": rp, "km": round(km)})
        return True

    def prune(self):
        cut = time.time() - KEEP_S
        while self.spots and self.spots[0]["t"] < cut:
            self.spots.popleft()
        if len(self._seen) > MAX_SPOTS * 2:
            self._seen = {k: v for k, v in self._seen.items() if v >= cut}

    def classify(self, s: dict, call: str, grid: str) -> str | None:
        """mine = oma kutsu lähettäjänä; heard = oma ruutu kuuli; sent = omasta ruudusta kuultiin."""
        if call and base_call(s["sc"]) == call:
            return "mine"
        if grid and s["rl"][:4].upper() == grid:
            return "heard"
        if grid and s["sl"][:4].upper() == grid:
            return "sent"
        return None

    def query(self, minutes: int = 60, bands=None, modes=None) -> dict:
        """Kartalle ja yhteenvetoon: kaukaiset päät kaistoittain (uusin/paras), yhteenveto kaistoittain."""
        now = time.time()
        cut = now - minutes * 60
        call, grid, home = self.my_call(), self.grid(), self.home()
        bands = set(bands or [])
        modes = {m.upper() for m in (modes or [])}
        best: dict = {}
        summ: dict = {}
        for s in self.spots:
            if s["t"] < cut:
                continue
            kind = self.classify(s, call, grid)
            if not kind or (bands and s["b"] not in bands):
                continue
            if modes and not (s["md"] in modes or ("OTHER" in modes and s["md"] not in ("FT8", "FT4", "JS8", "CW", "RTTY", "WSPR"))):
                continue
            near, far = (s["s"], s["r"]) if kind in ("mine", "sent") else (s["r"], s["s"])
            fcall = s["rc"] if kind in ("mine", "sent") else s["sc"]
            origin = home if (kind == "mine" and home) else near
            km, az = dist_bearing(origin, far)
            k = (kind, s["b"], fcall)
            cur = best.get(k)
            if not cur or s["t"] > cur["t"]:
                best[k] = {"kind": kind, "b": s["b"], "md": s["md"], "call": fcall, "near": s["sc"] if kind != "heard" else s["rc"],
                           "lat": far[0], "lon": far[1], "olat": origin[0], "olon": origin[1], "km": round(km),
                           "az": round(az), "rp": s["rp"], "t": s["t"], "f": s["f"], "src": s["src"]}
            b = summ.setdefault(s["b"], {"b": s["b"], "n": 0, "mine": 0, "far": 0, "farcall": "", "sect": [0] * 8,
                                          "last": 0, "modes": {}})
            b["n"] += 1
            if kind == "mine":
                b["mine"] += 1
            if km > b["far"]:
                b["far"], b["farcall"] = round(km), fcall
            b["sect"][int(((az + 22.5) % 360) // 45)] += 1
            b["last"] = max(b["last"], s["t"])
            b["modes"][s["md"]] = b["modes"].get(s["md"], 0) + 1
        order = {b: i for i, b in enumerate(BANDS)}
        summary = sorted(summ.values(), key=lambda x: order.get(x["b"], 99))
        for b in summary:
            b["modes"] = sorted(b["modes"], key=lambda m: -b["modes"][m])[:3]
        return {"points": sorted(best.values(), key=lambda p: p["t"])[-4000:], "summary": summary,
                "call": call, "grid": grid, "home": list(home) if home else None, "minutes": minutes}

    # ---------------- yhteys
    async def _mqtt_once(self, topics: list[str]):
        from .messaging import ssl_context
        cid = f"MonitorForAmateurRadio-{__version__}-{self.my_call() or 'anon'}-{random.randint(1000, 9999)}"[:64]
        tls = {"ssl": ssl_context(), "server_hostname": self.host} if self.tls else {}
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port, **tls), 20)
        try:
            writer.write(mqtt_connect_packet(cid))
            await writer.drain()
            typ, _, body = await asyncio.wait_for(mqtt_read_packet(reader), 20)
            if typ != 2 or len(body) < 2 or body[1] != 0:
                raise ConnectionError(tr("Palvelin hylkäsi yhteyden"))
            writer.write(mqtt_subscribe_packet(1, topics))
            await writer.drain()
            self.state.update(ok=True, error=None, connected=True, since=self.state.get("since") or time.time())
            self.mon.dirty = True
            last = {"rx": time.time(), "push": 0.0}

            async def read_loop():
                while True:
                    typ, flags, body = await mqtt_read_packet(reader)
                    last["rx"] = time.time()
                    if typ == 3:
                        _topic, payload = mqtt_parse_publish(flags, body)
                        try:
                            d = json.loads(payload)
                        except ValueError:
                            continue
                        if isinstance(d, dict) and self.add_spot(d) and time.time() - last["push"] > 5:
                            last["push"] = time.time()
                            self.state["updated"] = last["push"]
                            self.prune()
                            self.mon.dirty = True
                    elif typ == 9 and b"\x80" in body[2:]:
                        raise ConnectionError(tr("Palvelin hylkäsi tilauksen"))

            rt = asyncio.create_task(read_loop())
            wt = asyncio.create_task(self._wake.wait())
            try:
                while not self._wake.is_set():
                    done, _ = await asyncio.wait({rt, wt}, timeout=KEEPALIVE / 2,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if rt in done:
                        exc = rt.exception()
                        if exc and not isinstance(exc, asyncio.IncompleteReadError):
                            raise exc
                        raise ConnectionError(tr("Yhteys suljettu"))     # palvelin sulki yhteyden
                    if time.time() - last["rx"] > KEEPALIVE * 2:
                        raise asyncio.TimeoutError()
                    writer.write(PINGREQ)
                    await writer.drain()
            finally:
                rt.cancel()
                wt.cancel()
            try:
                writer.write(DISCONNECT)
                await writer.drain()
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.state["connected"] = False
            writer.close()

    # ---------------- demo
    def _demo_tick(self):
        h = self.home() or (60.17, 24.94)
        call = self.my_call() or "OH2ABC"
        g = grid4(*h)
        rnd = random.Random()
        now = time.time()
        far = [("K1ABC", "FN42", "20m"), ("JA1XYZ", "PM95", "20m"), ("VK2DEF", "QF56", "15m"), ("PY2GHI", "GG66", "10m"),
               ("ZS6JKL", "KG33", "15m"), ("W6MNO", "CM87", "40m"), ("EA8PQR", "IL18", "10m"), ("G4STU", "IO91", "40m"),
               ("DL1VWX", "JO62", "80m"), ("UA9YZA", "MO05", "20m"), ("LU1BCD", "GF05", "10m"), ("SM5EFG", "JO89", "6m"),
               ("ES2HIJ", "KO29", "2m"), ("YL2KLM", "KO26", "2m"), ("OH8NOP", "KP24", "2m"), ("SP2QRS", "JO94", "70cm"),
               ("VE3TUV", "FN03", "17m"), ("4X1WXY", "KM72", "12m"), ("HL1ZAB", "PM37", "30m"), ("EI2CDE", "IO63", "60m")]
        modes = {"2m": "FT8", "70cm": "FT8", "6m": "FT8", "80m": "CW", "60m": "FT8", "30m": "FT8"}
        first = not self.spots
        for _ in range(rnd.randint(120, 160) if first else rnd.randint(3, 8)):
            rc, rl, b = rnd.choice(far)
            md = modes.get(b) or rnd.choice(["FT8", "FT8", "FT4", "CW", "JS8"])
            if rnd.random() < 0.45:      # minut kuultiin
                d = {"sc": call, "sl": latlon_loc6(*h), "rc": rc, "rl": rl + "aa"}
            elif rnd.random() < 0.5:     # oma ruutu kuuli
                d = {"sc": rc, "sl": rl, "rc": "OH2" + rnd.choice(["XA", "XB", "XC"]), "rl": g + "le"}
            else:                        # omasta ruudusta kuultiin
                d = {"sc": "OH2" + rnd.choice(["XD", "XE"]), "sl": g + "kf", "rc": rc, "rl": rl}
            d.update(b=b, md=md, rp=rnd.randint(-22, 12), t=now - rnd.uniform(0, 3500 if first else 50), f=None, sq=rnd.getrandbits(40))
            self.add_spot(d, "pskr")
        self.state.update(ok=True, error=None, connected=True, updated=now, since=self.state.get("since") or now)
        self.prune()
        self.mon.dirty = True

    async def run(self):
        delay = 5
        while True:
            self._wake.clear()
            wait = 60
            if self.cfg.get("enabled"):
                topics = topics_for(self.my_call(), self.grid(), self.cfg.get("mine", True), self.cfg.get("area", True))
                if not topics:
                    self.state.update(ok=False, error=tr("Aseta oma kutsu (Lokit) tai sijainti (Palvelut)"), connected=False)
                    self.mon.dirty = True
                elif self.demo:
                    self._demo_tick()
                    wait = 30
                else:
                    try:
                        await self._mqtt_once(topics)
                        delay = 5
                        wait = 0.5
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        from .messaging import friendly_error
                        self.state.update(ok=False, error=friendly_error(e), connected=False)
                        self.mon.dirty = True
                        log.info("PSKReporter: %s", e)
                        wait, delay = delay, min(120, delay * 2)
            elif self.state.get("connected") or self.spots:
                self.spots.clear()
                self._seen.clear()
                self.state = {"ok": None, "error": None, "connected": False, "updated": None, "since": None}
                self.mon.dirty = True
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except asyncio.TimeoutError:
                pass

    def snapshot(self) -> dict:
        return {**self.state, "enabled": bool(self.cfg.get("enabled")), "count": len(self.spots),
                "call": self.my_call(), "grid": self.grid()}


def latlon_loc6(lat: float, lon: float) -> str:
    g = grid4(lat, lon)
    return g + chr(97 + int(((lon + 180) % 2) * 12)) + chr(97 + int(((lat + 90) % 1) * 24))
