"""APRS-radioyhteydet: KISS TCP, KISS sarjaportti (USB/Bluetooth), AGWPE ja simuloitu radio.

Jokainen yhteys välittää vastaanotetut AX.25-kehykset (ilman FCS:ää) kutsujalle ja osaa
tarvittaessa lähettää kehyksen. Yhteydet ovat toisistaan riippumattomia: yksi radio voi olla
poikki muiden toimiessa.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import struct
import time
import uuid
from typing import Callable

from .i18n import tr

log = logging.getLogger("bmmonitor.radios")

TYPES = ("kiss_tcp", "kiss_serial", "kenwood", "kpc3", "agwpe", "sim")
SERIAL_TYPES = ("kiss_serial", "kenwood", "kpc3")
# TNC KISS-tilaan käynnistyksessä ja takaisin suljettaessa (kokeellinen: ei testattu oikeilla laitteilla)
KISS_INIT = {
    "kenwood": [b"\r", b"TC 1\r", b"TN 2,0\r", b"KISS ON\r", b"RESTART\r"],     # TM-D710 / TH-D72: sisäinen TNC
    "kpc3": [b"\r", b"INTFACE KISS\r", b"RESET\r"],                               # Kantronics KPC-3+
}
INIT_DELAY = 0.6                                         # TNC:n käskyjen väli (s)
KISS_EXIT = bytes([0xC0, 0xFF, 0xC0])                    # KISS-tilasta pois (TNC palaa komentotilaan)
DEFAULT_PORTS = {"kiss_tcp": 8001, "agwpe": 8000}
BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)
NAME_MAX = 40
HOST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:/\\-]{1,200}$")

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


class LinkError(Exception):
    """Yhteysvirhe, jonka teksti näytetään käyttäjälle sellaisenaan."""


# --------------------------------------------------------------------------
# KISS
# --------------------------------------------------------------------------
class KissDecoder:
    """Purkaa KISS-virran kehyksiksi (vain datakehykset, portti 0–15)."""

    def __init__(self):
        self.buf = bytearray()
        self.in_frame = False
        self.esc = False

    def feed(self, data: bytes) -> list[bytes]:
        out = []
        for b in data:
            if b == FEND:
                if self.in_frame and len(self.buf) > 1 and (self.buf[0] & 0x0F) == 0:
                    out.append(bytes(self.buf[1:]))
                self.buf.clear()
                self.in_frame = True
                self.esc = False
                continue
            if not self.in_frame:
                continue
            if self.esc:
                b = FEND if b == TFEND else FESC if b == TFESC else b
                self.esc = False
            elif b == FESC:
                self.esc = True
                continue
            self.buf.append(b)
            if len(self.buf) > 2048:          # roskaa: aloitetaan alusta
                self.buf.clear()
                self.in_frame = False
        return out


def kiss_frame(ax25: bytes, port: int = 0) -> bytes:
    body = bytes([(port & 0x0F) << 4]) + ax25
    body = body.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + body + bytes([FEND])


# --------------------------------------------------------------------------
# AGWPE
# --------------------------------------------------------------------------
AGW_HDR = struct.Struct("<B3xcxBx10s10sII")      # portti, tyyppi, PID, lähettäjä, vastaanottaja, pituus, käyttäjä


def agw_frame(kind: bytes, data: bytes = b"", port: int = 0, pid: int = 0) -> bytes:
    return AGW_HDR.pack(port, kind, pid, b"", b"", len(data), 0) + data


class AgwDecoder:
    """Purkaa AGWPE-virran (otsake 36 tavua + data) ja palauttaa raa'at AX.25-kehykset ('K')."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buf += data
        out = []
        while len(self.buf) >= AGW_HDR.size:
            _port, kind, _pid, _f, _t, n, _u = AGW_HDR.unpack_from(self.buf)
            if n > 65536:                     # rikkinäinen virta
                self.buf.clear()
                break
            if len(self.buf) < AGW_HDR.size + n:
                break
            payload = bytes(self.buf[AGW_HDR.size:AGW_HDR.size + n])
            del self.buf[:AGW_HDR.size + n]
            if kind == b"K" and len(payload) > 1:
                out.append(payload[1:])       # ensimmäinen tavu on KISS-komentotavu (portti)
        return out


# --------------------------------------------------------------------------
# Asetukset
# --------------------------------------------------------------------------
def new_radio(kind: str = "kiss_tcp", **kw) -> dict:
    r = {"id": uuid.uuid4().hex[:8], "name": "", "type": kind, "enabled": True, "tx": True,
         "host": "127.0.0.1", "port": DEFAULT_PORTS.get(kind, 8001), "serial": "", "baud": 9600}
    r.update(kw)
    return r


def default_radios(old: dict) -> list[dict]:
    """Ennen 0.16:ta oli yksi Graywolf-yhteys (host/port): siitä tulee listan ensimmäinen radio."""
    return [new_radio("kiss_tcp", id="graywolf", name="Graywolf", host=old.get("host") or "127.0.0.1",
                      port=int(old.get("port") or 8001))]


def normalize_radios(items, old: list[dict] | None = None) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError(tr("Virheellinen valinta"))
    if len(items) > 8:
        raise ValueError(tr("Enintään {n} radiota", n=8))
    prev = {r.get("id"): r for r in old or []}
    out, ids = [], set()
    for it in items:
        if not isinstance(it, dict):
            raise ValueError(tr("Virheellinen valinta"))
        kind = it.get("type") or "kiss_tcp"
        if kind not in TYPES:
            raise ValueError(tr("Tuntematon radion tyyppi: {x}", x=kind))
        rid = str(it.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9]{1,16}", rid) or rid in ids:
            rid = uuid.uuid4().hex[:8]
        ids.add(rid)
        r = new_radio(kind, **{k: v for k, v in prev.get(rid, {}).items() if k in ("host", "port", "serial", "baud")})
        r["id"] = rid
        r["name"] = str(it.get("name") or "").strip()[:NAME_MAX]
        r["enabled"] = bool(it.get("enabled", True))
        r["tx"] = bool(it.get("tx", kind != "sim"))
        if kind in ("kiss_tcp", "agwpe"):
            h = str(it.get("host") or "").strip() or "127.0.0.1"
            if not HOST_RE.match(h):
                raise ValueError(tr("Virheellinen palvelin: {x}", x=h))
            try:
                p = int(it.get("port") or DEFAULT_PORTS[kind])
            except (TypeError, ValueError):
                raise ValueError(tr("Virheellinen arvo: {x}", x=it.get("port"))) from None
            if not 1 <= p <= 65535:
                raise ValueError(tr("Arvon pitää olla {a}–{b}", a=1, b=65535))
            r["host"], r["port"] = h, p
        elif kind in SERIAL_TYPES:
            s = str(it.get("serial") or "").strip()
            if s and not SERIAL_RE.match(s):
                raise ValueError(tr("Virheellinen sarjaportti: {x}", x=s))
            try:
                b = int(it.get("baud") or 9600)
            except (TypeError, ValueError):
                raise ValueError(tr("Virheellinen arvo: {x}", x=it.get("baud"))) from None
            if b not in BAUDS:
                raise ValueError(tr("Virheellinen nopeus: {x}", x=b))
            r["serial"], r["baud"] = s, b
        out.append(r)
    return out


def describe(r: dict) -> str:
    k = r.get("type")
    if k in ("kiss_tcp", "agwpe"):
        return f"{r.get('host')}:{r.get('port')}"
    if k in SERIAL_TYPES:
        return f"{r.get('serial')} {r.get('baud')} bit/s" if r.get("serial") else tr("sarjaportti valitsematta")
    return tr("simuloitu")


def serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in sorted(list_ports.comports(), key=lambda x: x.device):
        out.append({"device": p.device, "desc": (p.description or "").strip() if p.description != "n/a" else ""})
    return out


# --------------------------------------------------------------------------
# Yhteydet
# --------------------------------------------------------------------------
class RadioLink:
    """Yksi radio. run() pitää yhteyden auki ja yrittää uudelleen katkon jälkeen."""
    retry = 15

    def __init__(self, cfg: dict, on_frame: Callable[[bytes, "RadioLink"], None], on_change: Callable[[], None]):
        self.cfg = dict(cfg)
        self.on_frame = on_frame
        self.on_change = on_change
        self.status = {"connected": False, "error": None, "since": None, "last": None, "count": 0, "tx": 0}

    @property
    def id(self) -> str:
        return self.cfg["id"]

    @property
    def name(self) -> str:
        return self.cfg.get("name") or describe(self.cfg)

    def signature(self) -> tuple:
        c = self.cfg
        return (c.get("type"), c.get("host"), c.get("port"), c.get("serial"), c.get("baud"), c.get("enabled"))

    def can_tx(self) -> bool:
        return bool(self.cfg.get("tx")) and bool(self.status["connected"])

    def _set(self, **kw):
        if any(self.status.get(k) != v for k, v in kw.items()):
            self.status.update(kw)
            self.on_change()

    def _got(self, frame: bytes):
        self.status["count"] += 1
        self.status["last"] = time.time()
        try:
            self.on_frame(frame, self)
        except Exception:  # noqa: BLE001
            log.exception("Kehyksen käsittely (%s)", self.name)

    def transmit(self, ax25: bytes):
        raise ConnectionError(tr("Radio {n} ei ole yhteydessä", n=self.name))

    async def run(self):
        while True:
            try:
                await self.session()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._set(connected=False, since=None, error=self.explain(e))
                log.warning("Radio %s: %s", self.name, e)
            else:
                self._set(connected=False, since=None)
            await asyncio.sleep(self.retry)

    def explain(self, e: Exception) -> str:
        return str(e) or e.__class__.__name__

    async def session(self):
        raise NotImplementedError

    def close(self):
        pass


class _StreamLink(RadioLink):
    """TCP-yhteys (KISS TCP ja AGWPE)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.writer: asyncio.StreamWriter | None = None

    def decoder(self):
        raise NotImplementedError

    def hello(self) -> bytes:
        return b""

    def wrap(self, ax25: bytes) -> bytes:
        raise NotImplementedError

    def explain(self, e):
        h, p = self.cfg["host"], self.cfg["port"]
        if isinstance(e, ConnectionRefusedError):
            why = tr("yhteys estetty – onko ohjelma käynnissä ja portti oikein?")
        elif isinstance(e, asyncio.TimeoutError):
            why = tr("aikakatkaisu")
        elif isinstance(e, OSError):
            why = e.strerror or str(e) or e.__class__.__name__
        else:
            return super().explain(e)
        return tr("Ei yhteyttä {h}:{p} ({e})", h=h, p=p, e=why)
        return super().explain(e)

    async def session(self):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.cfg["host"], int(self.cfg["port"])), 10)
        self.writer = writer
        try:
            if self.hello():
                writer.write(self.hello())
                await writer.drain()
            log.info("Radio %s yhdistetty (%s)", self.name, describe(self.cfg))
            self._set(connected=True, error=None, since=time.time())
            dec = self.decoder()
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(4096), 30)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    raise LinkError(tr("Yhteys suljettiin ({h}:{p})", h=self.cfg["host"], p=self.cfg["port"]))
                for fr in dec.feed(data):
                    self._got(fr)
        finally:
            self.writer = None
            writer.close()

    def transmit(self, ax25: bytes):
        if self.writer is None or not self.status["connected"]:
            super().transmit(ax25)
        self.writer.write(self.wrap(ax25))
        self.status["tx"] += 1

    def close(self):
        if self.writer:
            self.writer.close()


class KissTcpLink(_StreamLink):
    def decoder(self):
        return KissDecoder()

    def wrap(self, ax25):
        return kiss_frame(ax25)


class AgwpeLink(_StreamLink):
    def decoder(self):
        return AgwDecoder()

    def hello(self):
        return agw_frame(b"k")                 # raakakehykset päälle

    def wrap(self, ax25):
        return agw_frame(b"K", b"\x00" + ax25)


class KissSerialLink(RadioLink):
    """KISS TNC sarjaportissa: USB-kaapeli tai Bluetooth (SPP näkyy sarjaporttina)."""
    retry = 10

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ser = None

    def explain(self, e):
        name = e.__class__.__name__
        if name == "SerialException" or isinstance(e, OSError):
            if self.status.get("connected"):
                return tr("Sarjaportti {p} katkesi ({e})", p=self.cfg.get("serial") or "–", e=str(e) or name)
            return tr("Sarjaporttia {p} ei voi avata ({e})", p=self.cfg.get("serial") or "–", e=str(e) or name)
        return super().explain(e)

    async def session(self):
        port = self.cfg.get("serial")
        if not port:
            raise LinkError(tr("Valitse sarjaportti"))
        try:
            import serial
        except ImportError:
            raise LinkError(tr("Sarjaporttituki puuttuu (pyserial)")) from None
        ser = await asyncio.to_thread(serial.Serial, port, int(self.cfg.get("baud") or 9600), timeout=0.5)
        self.ser = ser
        try:
            for cmd in KISS_INIT.get(self.cfg.get("type"), []):      # TNC KISS-tilaan
                await asyncio.to_thread(ser.write, cmd)
                await asyncio.sleep(INIT_DELAY)
            if self.cfg.get("type") in KISS_INIT:
                await asyncio.to_thread(ser.reset_input_buffer)
            log.info("Radio %s yhdistetty (%s)", self.name, describe(self.cfg))
            self._set(connected=True, error=None, since=time.time())
            dec = KissDecoder()
            while True:
                data = await asyncio.to_thread(ser.read, 4096)
                for fr in dec.feed(data or b""):
                    self._got(fr)
        finally:
            self.ser = None
            if self.cfg.get("type") in KISS_INIT:
                try:
                    await asyncio.to_thread(ser.write, KISS_EXIT)
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.to_thread(ser.close)

    def transmit(self, ax25: bytes):
        if self.ser is None or not self.status["connected"]:
            super().transmit(ax25)
        self.ser.write(kiss_frame(ax25))
        self.status["tx"] += 1

    def close(self):
        if self.ser:
            try:
                if self.cfg.get("type") in KISS_INIT:
                    self.ser.write(KISS_EXIT)
                self.ser.close()
            except Exception:  # noqa: BLE001
                pass


class SimLink(RadioLink):
    """Simuloitu radio: keksittyjä APRS-asemia ja digipiittereitä kotisijainnin ympärillä (testaukseen)."""
    STATIONS = [("OH0SIM-9", ">", 0.12, 40), ("OH0SIM-7", "[", 0.05, 200), ("OH0SIM", "-", 0.22, 100),
                ("OH0WX", "_", 0.3, 300), ("OH0SIM-5", "k", 0.4, 30)]
    DIGIS = ["OH0DGA", "OH0DGB"]

    def __init__(self, *a, home: Callable[[], tuple[float, float] | None] = None, interval: float = 8.0, **kw):
        super().__init__(*a, **kw)
        self.home = home or (lambda: None)
        self.interval = interval
        self.rand = random.Random(4242)
        self.sent: list[bytes] = []

    def frame(self, n: int) -> bytes:
        from .aprsrx import encode_ax25
        call, sym, dist_deg, bearing = self.STATIONS[n % len(self.STATIONS)]
        lat0, lon0 = self.home() or (60.17, 24.94)
        b = math.radians(bearing + self.rand.uniform(-20, 20))
        d = dist_deg * self.rand.uniform(0.6, 1.4)
        lat = lat0 + d * math.cos(b)
        lon = lon0 + d * math.sin(b) / max(0.2, math.cos(math.radians(lat0)))

        def dm(v, pos, neg, w):
            h = pos if v >= 0 else neg
            v = abs(v)
            return f"{int(v):0{w}d}{(v - int(v)) * 60:05.2f}{h}"
        pos = f"{dm(lat, 'N', 'S', 2)}/{dm(lon, 'E', 'W', 3)}"
        if n % 7 == 6:            # iGaten radioon välittämä paketti
            info = f"}}OH0NET-9>APRS,TCPIP,OH0IGT*:!{pos}>Simuloitu APRS-IS-asema".encode()
            return encode_ax25("OH0IGT", "APRS", ["WIDE1-1"], info)
        if n % 9 == 7:            # objekti
            info = f";SIM-REP  *111111z{pos.replace('/', 'R')}&145.000 Simuloitu toistin".encode()
        elif sym == "_":          # sääasema
            t = int(self.rand.uniform(20, 60))
            info = f"!{pos}_{self.rand.randint(0, 359):03d}/{self.rand.randint(0, 15):03d}g{self.rand.randint(5, 25):03d}t{t:03d}h{self.rand.randint(40, 99):02d}b{self.rand.randint(9900, 10300):05d}".encode()
        else:
            info = f"!{pos}{sym}Simuloitu asema".encode()
        r = self.rand.random()
        if r < 0.4:
            path = []                                           # kuultu suoraan
        elif r < 0.75:
            path = [self.DIGIS[0] + "*", "WIDE2-1"]
        else:
            path = [self.DIGIS[0] + "*", self.DIGIS[1] + "*", "WIDE2"]
        return encode_ax25(call, "APZSIM", path, info)

    async def session(self):
        self._set(connected=True, error=None, since=time.time())
        n = 0
        while True:
            self._got(self.frame(n))
            n += 1
            await asyncio.sleep(self.interval)

    def transmit(self, ax25: bytes):
        if not self.status["connected"]:
            super().transmit(ax25)
        self.sent.append(ax25)
        del self.sent[:-50]
        self.status["tx"] += 1


LINKS = {"kiss_tcp": KissTcpLink, "agwpe": AgwpeLink, "kiss_serial": KissSerialLink, "kenwood": KissSerialLink,
         "kpc3": KissSerialLink, "sim": SimLink}
