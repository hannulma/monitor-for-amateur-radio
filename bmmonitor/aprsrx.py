"""APRS-kuuluvuusanalyysi: radioiden (Graywolf / Dire Wolf KISS TCP, KISS-TNC sarjaportissa, AGWPE,
simuloitu radio – ks. radios.py) vastaanottamat paketit.

Ohjelma kuuntelee radioita; radiolla lähetetään vain, jos käyttäjä on sallinut APRS-viestien lähetyksen
radiolla (messaging.rf_tx) – silloin viestit ja kuittaukset lähetetään radiolla, jolle lähetys on sallittu. Jokaisesta paketista tallennetaan, keneltä se
kuultiin radiotiellä (lähettäjä itse = suoraan, tai viimeinen digipiitteri, jonka H-bitti on asetettu),
koko polku ja lähettäjän sijainti, jos paketissa on sellainen. Näistä lasketaan lämpökartta,
digipiitterien osuudet, yleisimmät polut ja hälytys, kun tuttu digi tai iGate hiljenee.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import time
from pathlib import Path

from .i18n import tr
from .radios import FEND, FESC, TFEND, TFESC, LINKS, KissDecoder, default_radios, describe, kiss_frame, normalize_radios  # noqa: F401

log = logging.getLogger("bmmonitor.aprsrx")

DEFAULT_APRSRX = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 8001,              # Graywolfin / Dire Wolfin KISS TCP -oletusportti
    "alerts": True,            # hälytä, kun seurattu digi tai iGate hiljenee
    "silence_min": 60,         # kuinka pitkä hiljaisuus hälyttää
    "auto_digis": True,        # seuraa automaattisesti digejä, joiden kautta paketteja on kuultu
    "min_heard": 5,            # digin pitää olla välittänyt vähintään näin monta pakettia (7 vrk) ennen seurantaa
    "watch": [],               # lisäksi seurattavat kutsut (esim. iGatet, majakat)
    "sms": False,
    "is_km": 0,                # APRS-IS-asemat kartalle tältä säteeltä kotisijainnista (km); 0 = pois
    # "radios": radiolista (radios.normalize_radios); puuttuessa tehdään host/port-asetuksesta (ennen 0.16)
}
DEFAULT_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_TILES_ATTR = "© OpenStreetMap contributors"
PERIODS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
KEEP_DAYS = 31
# Digipiitterien yleisnimet: jos vain tällainen on merkitty käytetyksi, välittäjän kutsu ei ole tiedossa.
ALIASES = re.compile(r"^(WIDE\d?|TRACE\d?|RELAY|ECHO|GATE|TEMP\d?|NCA|NOGATE|RFONLY|TCPIP|TCPXX|qA.|[A-Z]{2}\d?)$")
CALL_RE = re.compile(r"^[A-Z0-9]{1,6}(-\d{1,2})?$")


# --------------------------------------------------------------------------
# KISS ja AX.25
# --------------------------------------------------------------------------
# KISS-kehystys on radios-moduulissa (yhteinen kaikille radioyhteyksille)


AX25_CALL = re.compile(r"^[A-Z0-9]{1,6}(-([0-9]|1[0-5]))?$")


def _addr(b: bytes) -> tuple[str, bool, bool]:
    call = "".join(chr(c >> 1) for c in b[:6]).strip()
    ssid = (b[6] >> 1) & 0x0F
    return (f"{call}-{ssid}" if ssid else call), bool(b[6] & 0x80), bool(b[6] & 0x01)


def encode_ax25(src: str, dest: str, path: list[str], info: bytes) -> bytes:
    """Rakentaa AX.25 UI -kehyksen. Polun alkio, jonka lopussa on *, merkitään käytetyksi."""
    for c in [src, dest, *[p.rstrip("*") for p in path]]:
        if not AX25_CALL.match(c):
            raise ValueError(tr("Kutsu ei kelpaa radiolähetykseen: {x}", x=c))
    def enc(call: str, last: bool, h: bool = False) -> bytes:
        c, _, s = call.partition("-")
        b = bytes((ord(ch) << 1) for ch in c.ljust(6)[:6])
        return b + bytes([0x60 | (int(s or 0) << 1) | (0x80 if h else 0) | (1 if last else 0)])
    out = enc(dest, False) + enc(src, not path)
    for i, p in enumerate(path):
        out += enc(p.rstrip("*"), i == len(path) - 1, p.endswith("*"))
    return out + b"\x03\xf0" + info


def parse_ax25(frame: bytes) -> dict | None:
    """AX.25 UI -kehys → {src, dest, path: [(kutsu, käytetty)], info}."""
    if len(frame) < 16:
        return None
    addrs, i = [], 0
    while i + 7 <= len(frame):
        addrs.append(_addr(frame[i:i + 7]))
        i += 7
        if addrs[-1][2]:                 # osoitekentän viimeinen
            break
        if len(addrs) > 10:
            return None
    if len(addrs) < 2 or i + 2 > len(frame) or frame[i] != 0x03 or frame[i + 1] != 0xF0:
        return None                      # vain UI-kehykset (APRS)
    return {"dest": addrs[0][0], "src": addrs[1][0], "path": [(a[0], a[1]) for a in addrs[2:]],
            "info": frame[i + 2:]}


def heard_via(src: str, path: list[tuple[str, bool]]) -> tuple[str, bool]:
    """Keneltä paketti kuultiin radiotiellä: (kutsu, suoraan). Viimeinen käytetyksi merkitty digi välitti sen."""
    used = [c for c, h in path if h]
    if not used:
        return src, True
    return used[-1], False


def is_alias(call: str) -> bool:
    """WIDE2-1, RELAY, TCPIP … ovat polun yleisnimiä, eivät asemia."""
    return bool(ALIASES.match(call.split("-")[0]))


# --------------------------------------------------------------------------
# APRS-sijainnit
# --------------------------------------------------------------------------
def _b91(s: str) -> int:
    v = 0
    for ch in s:
        v = v * 91 + (ord(ch) - 33)
    return v


def _uncompressed(s: str):
    m = re.match(r"^(\d{2})([\d ]{2})\.([\d ]{2})([NS])(.)(\d{3})([\d ]{2})\.([\d ]{2})([EW])", s)
    if not m:
        return None
    f = lambda x: x.replace(" ", "0")  # noqa: E731  sijainnin epätarkkuus (välilyönnit)
    lat = int(m[1]) + float(f(m[2]) + "." + f(m[3])) / 60
    lon = int(m[6]) + float(f(m[7]) + "." + f(m[8])) / 60
    return (-lat if m[4] == "S" else lat), (-lon if m[9] == "W" else lon)


def _compressed(s: str):
    if len(s) < 10 or not (s[0] in "/\\" or "A" <= s[0] <= "Z" or "a" <= s[0] <= "j"):
        return None
    try:
        lat = 90 - _b91(s[1:5]) / 380926
        lon = -180 + _b91(s[5:9]) / 190463
    except Exception:  # noqa: BLE001
        return None
    return lat, lon


def _mic_e(dest: str, info: bytes):
    d = dest.split("-")[0]
    if len(d) != 6 or len(info) < 9:
        return None
    digits = []
    for ch in d:
        if ch.isdigit():
            digits.append(ord(ch) - 48)
        elif "A" <= ch <= "J":
            digits.append(ord(ch) - 65)
        elif "P" <= ch <= "Y":
            digits.append(ord(ch) - 80)
        elif ch in "KLZ":
            digits.append(0)
        else:
            return None
    lat = digits[0] * 10 + digits[1] + (digits[2] * 10 + digits[3] + (digits[4] * 10 + digits[5]) / 100) / 60
    north = "P" <= d[3] <= "Z"
    lon_off = 100 if "P" <= d[4] <= "Z" else 0
    west = "P" <= d[5] <= "Z"
    deg = info[1] - 28 + lon_off
    if 180 <= deg <= 189:
        deg -= 80
    elif 190 <= deg <= 199:
        deg -= 190
    mins = info[2] - 28
    if mins >= 60:
        mins -= 60
    hund = info[3] - 28
    lon = deg + (mins + hund / 100) / 60
    return (lat if north else -lat), (-lon if west else lon)


def parse_position(dest: str, info: bytes):
    """Lähettäjän oma sijainti (lat, lon) tai None. Objektit ja kolmannen osapuolen paketit ohitetaan."""
    if not info:
        return None
    t = chr(info[0])
    try:
        if t in "`'\x1c\x1d":
            pos = _mic_e(dest, info)
        elif t == "$":
            pos = parse_nmea(info)
        elif t in "!=/@":
            s = info.decode("latin-1")
            body = s[8:] if t in "/@" else s[1:]
            pos = _uncompressed(body) or _compressed(body)
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    if not pos:
        return None
    lat, lon = pos
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (abs(lat) < 0.01 and abs(lon) < 0.01):
        return None
    return round(lat, 5), round(lon, 5)


def parse_symbol(dest: str, info: bytes) -> str | None:
    """APRS-symboli kahtena merkkinä (taulu + koodi), esim. "/>" auto, "/#" digi, "I&" iGate, "/_" sää."""
    if not info:
        return None
    t = chr(info[0])
    try:
        if t in "`'\x1c\x1d":
            return info[8:9].decode("latin-1") + info[7:8].decode("latin-1") if len(info) >= 9 else None
        if t in "!=/@":
            s = info.decode("latin-1")
            body = s[8:] if t in "/@" else s[1:]
            if _uncompressed(body) and len(body) >= 19:
                return body[8] + body[18]
            if _compressed(body):
                return body[0] + body[9]
    except Exception:  # noqa: BLE001
        return None
    return None


# --------------------------------------------------------------------------
# Sää, objektit, itemit, kolmannen osapuolen paketit ja NMEA (0.16.0b2)
# --------------------------------------------------------------------------
def _pos_sym(body: str):
    """Sijaintikenttä (tavallinen tai pakattu) → (lat, lon, symboli, loppuosa) tai None."""
    p = _uncompressed(body)
    if p and len(body) >= 19:
        return p[0], p[1], body[8] + body[18], body[19:]
    p = _compressed(body)
    if p and len(body) >= 13:
        return p[0], p[1], body[0] + body[9], body[13:]
    return None


def _valid(lat, lon) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 0.01 and abs(lon) < 0.01)


WX_LEN = {"c": 3, "s": 3, "g": 3, "t": 3, "r": 3, "p": 3, "P": 3, "h": 2, "b": 5, "L": 3, "l": 3}


def parse_weather(text: str) -> dict | None:
    """APRS-säätiedot (esim. "220/004g005t041r000p000P000h85b10115") → metriset arvot.

    Palauttaa avaimet: wind_dir (°), wind (m/s), gust (m/s), temp (°C), rain_1h / rain_24h / rain_mid (mm),
    humidity (%), pressure (hPa), lum (W/m²). Puuttuvat arvot jätetään pois.
    """
    out: dict = {}
    m = re.match(r"^(\d{3}|\.{3}| {3})/(\d{3}|\.{3}| {3})", text)
    if m:
        if m[1].strip(". "):
            out["wind_dir"] = int(m[1]) % 360
        if m[2].strip(". "):
            out["wind"] = round(int(m[2]) * 0.44704, 1)
        text = text[7:]
    i = 0
    while i < len(text):
        ch = text[i]
        n = WX_LEN.get(ch)
        if not n:
            break
        val = text[i + 1:i + 1 + n]
        i += 1 + n
        if not val.strip(". ") or not re.fullmatch(r"-?\d+", val.strip()):
            continue
        v = int(val)
        if ch == "c":
            out["wind_dir"] = v % 360
        elif ch == "s":
            out["wind"] = round(v * 0.44704, 1)
        elif ch == "g":
            out["gust"] = round(v * 0.44704, 1)
        elif ch == "t":
            out["temp"] = round((v - 32) * 5 / 9, 1)
        elif ch == "r":
            out["rain_1h"] = round(v * 0.254, 1)
        elif ch == "p":
            out["rain_24h"] = round(v * 0.254, 1)
        elif ch == "P":
            out["rain_mid"] = round(v * 0.254, 1)
        elif ch == "h":
            out["humidity"] = 100 if v == 0 else v
        elif ch == "b":
            out["pressure"] = round(v / 10, 1)
        elif ch == "L":
            out["lum"] = v
        elif ch == "l":
            out["lum"] = v + 1000
    if not ({"temp", "wind", "pressure", "humidity", "rain_1h"} & out.keys()):
        return None
    return out


def _nmea_deg(v: str, h: str, w: int) -> float:
    d = int(v[:w]) + float(v[w:]) / 60
    return -d if h in "SW" else d


def parse_nmea(info: bytes):
    """$GPRMC / $GPGGA (myös $GN…) → (lat, lon) tai None."""
    try:
        s = info.decode("ascii", "replace").split("*")[0]
        f = s.split(",")
        if f[0][3:] == "RMC" and len(f) > 6 and f[2] == "A":
            lat, lon = _nmea_deg(f[3], f[4], 2), _nmea_deg(f[5], f[6], 3)
        elif f[0][3:] == "GGA" and len(f) > 6 and f[6] not in ("", "0"):
            lat, lon = _nmea_deg(f[2], f[3], 2), _nmea_deg(f[4], f[5], 3)
        else:
            return None
    except (ValueError, IndexError):
        return None
    return (round(lat, 5), round(lon, 5)) if _valid(lat, lon) else None


def parse_object(info: bytes) -> dict | None:
    """Objekti (;NIMI_____*111111z…) tai item ()NIMI!…) → {name, kind, alive, lat, lon, sym, comment, wx}."""
    s = info.decode("latin-1")
    if s[:1] == ";" and len(s) >= 18 and s[10] in "*_":
        name, alive, body, kind = s[1:10].strip(), s[10] == "*", s[18:], "obj"
    elif s[:1] == ")":
        m = re.match(r"^\)([^!_]{3,9})([!_])", s)
        if not m:
            return None
        name, alive, body, kind = m[1].strip(), m[2] == "!", s[m.end():], "item"
    else:
        return None
    ps = _pos_sym(body)
    if not ps or not name:
        return None
    lat, lon, sym, rest = ps
    if not _valid(lat, lon):
        return None
    wx = parse_weather(rest) if sym[1:] == "_" else None
    return {"name": name, "kind": kind, "alive": alive, "lat": round(lat, 5), "lon": round(lon, 5), "sym": sym,
            "comment": rest.strip()[:120] if not wx else "", "wx": wx}


def parse_third_party(info: bytes) -> dict | None:
    """}LÄHETTÄJÄ>KOHDE,POLKU:SISÄLTÖ → {src, dest, path, info} (iGaten radioon välittämä paketti)."""
    if info[:1] != b"}":
        return None
    try:
        head, _, payload = info[1:].partition(b":")
        h = head.decode("ascii")
    except UnicodeDecodeError:
        return None
    m = re.match(r"^([A-Z0-9]{1,6}(?:-[0-9A-Z]{1,2})?)>([A-Z0-9-]{1,9})((?:,[A-Za-z0-9*-]{1,10})*)$", h)
    if not m or not payload:
        return None
    return {"src": m[1], "dest": m[2], "path": [p for p in m[3].split(",") if p], "info": payload}


def station_weather(dest: str, info: bytes) -> dict | None:
    """Aseman oma sääpaketti: sijainniton (_MMDDhhmm…) tai sijainti, jossa sääsymboli."""
    if not info:
        return None
    t = chr(info[0])
    s = info.decode("latin-1")
    if t == "_" and len(s) > 9:
        return parse_weather(s[9:])
    if t in "!=/@":
        ps = _pos_sym(s[8:] if t in "/@" else s[1:])
        if ps and ps[2][1:] == "_":
            return parse_weather(ps[3])
    return None


MOBILE = set(">kvjbR<sY'^uUfaO[")


def symbol_kind(sym: str | None) -> str:
    """Karkea luokka kartan merkkiä varten: digi / igate / wx / mobile / fixed."""
    if not sym or len(sym) != 2:
        return "fixed"
    table, code = sym
    if code == "#":
        return "digi"
    if code == "&":
        return "igate"
    if code in "_W" and table in "/\\":
        return "wx"
    if code in MOBILE:
        return "mobile"
    return "fixed"


def km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def normalize_aprsrx(new: dict, old: dict) -> dict:
    out = {**DEFAULT_APRSRX, **(old or {})}
    for k in ("enabled", "alerts", "auto_digis", "sms"):
        if k in new:
            out[k] = bool(new[k])
    if "host" in new:
        h = str(new["host"] or "").strip()
        if not re.match(r"^[A-Za-z0-9._:-]{1,253}$", h):
            raise ValueError(tr("Virheellinen palvelin: {x}", x=h))
        out["host"] = h
    for k, lo, hi in (("port", 1, 65535), ("silence_min", 5, 7 * 24 * 60), ("min_heard", 1, 1000), ("is_km", 0, 500)):
        if k in new:
            try:
                v = int(new[k])
            except (TypeError, ValueError):
                raise ValueError(tr("Virheellinen arvo: {x}", x=new[k])) from None
            if not lo <= v <= hi:
                raise ValueError(tr("Arvon pitää olla {a}–{b}", a=lo, b=hi))
            out[k] = v
    if "radios" in new:
        out["radios"] = normalize_radios(new["radios"], out.get("radios"))
    elif not isinstance(out.get("radios"), list):
        out["radios"] = default_radios(out)
    if "watch" in new:
        w = new["watch"]
        items = w.split() if isinstance(w, str) else list(w or [])
        calls = []
        for c in items:
            c = str(c).strip().upper()
            if not c:
                continue
            if not CALL_RE.match(c):
                raise ValueError(tr("Virheellinen kutsu: {x}", x=c))
            if c not in calls:
                calls.append(c)
        out["watch"] = calls
    return out


# --------------------------------------------------------------------------
# Tallennus, tilastot ja hälytykset
# --------------------------------------------------------------------------
class AprsRx:
    def __init__(self, mon, db_path: Path):
        self.mon = mon
        self.db_path = db_path
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")      # raportit lukevat omalla yhteydellään kirjoituksen aikana
        self.db.execute("""CREATE TABLE IF NOT EXISTS packets (ts REAL, src TEXT, via TEXT, direct INTEGER,
                           path TEXT, lat REAL, lon REAL)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS packets_ts ON packets(ts)")
        # muut kartan kohteet (0.16.0b2): sääasemat, objektit/itemit, iGaten välittämät ja APRS-IS:n asemat
        self.db.execute("""CREATE TABLE IF NOT EXISTS extra (kind TEXT, name TEXT, source TEXT, ts REAL, lat REAL,
                           lon REAL, sym TEXT, reporter TEXT, via TEXT, direct INTEGER, data TEXT, alive INTEGER,
                           PRIMARY KEY (kind, name, source))""")
        if "sym" not in {r[1] for r in self.db.execute("PRAGMA table_info(packets)")}:
            self.db.execute("ALTER TABLE packets ADD COLUMN sym TEXT")     # APRS-symboli (0.14.0b14 alkaen)
        self.db.commit()
        self.status = {"connected": False, "error": None, "since": None, "last": None, "count": 0}
        self.wake = asyncio.Event()
        self.pending = 0
        self.last_heard: dict[str, float] = {}     # kutsu → viimeksi kuultu radiotiellä (suoraan tai välittäjänä)
        self.heard_src: dict[str, float] = {}      # kutsu → viimeksi kuultu sen oma paketti (mitä reittiä tahansa)
        self.links: dict = {}                     # radio-id → RadioLink
        self.tasks: dict[str, asyncio.Task] = {}
        self.recent: dict[bytes, float] = {}       # sama kehys toiselta radiolta → ei tallenneta kahdesti
        self.silent: set[str] = set()              # hälytetty hiljaisuudesta, odottaa paluuta
        self._load_last_heard()

    @property
    def cfg(self) -> dict:
        return self.mon.cfg["aprsrx"]

    def _load_last_heard(self):
        since = time.time() - 7 * 86400
        for call, t in self.db.execute("SELECT via, MAX(ts) FROM packets WHERE ts > ? GROUP BY via", (since,)):
            self.last_heard[call] = t
        for call, t in self.db.execute("SELECT src, MAX(ts) FROM packets WHERE ts > ? AND direct = 1 GROUP BY src", (since,)):
            self.last_heard[call] = max(t, self.last_heard.get(call, 0))
        for call, t in self.db.execute("SELECT src, MAX(ts) FROM packets WHERE ts > ? GROUP BY src", (since,)):
            self.heard_src[call] = t

    def heard_recently(self, call: str, secs: float) -> bool:
        return time.time() - self.heard_src.get(call.upper(), 0) < secs

    def tx_link(self):
        """Radio, jolla lähetetään: ensimmäinen yhdistetty radio, jolle lähetys on sallittu."""
        for r in self.cfg.get("radios") or []:
            ln = self.links.get(r["id"])
            if ln is not None and ln.can_tx():
                return ln
        return None

    def can_transmit(self) -> bool:
        return self.tx_link() is not None

    def transmit(self, src: str, dest: str, path: list[str], info: bytes):
        """Lähettää APRS-paketin radiolla (vain viestien lähetys, ks. messaging)."""
        ln = self.tx_link()
        if ln is None:
            raise ConnectionError(tr("Ei radiota, jolla voi lähettää"))
        ln.transmit(encode_ax25(src.upper(), dest, path, info))

    def reconfigure(self):
        self.wake.set()

    # ---------------- vastaanotto ----------------
    def _make_link(self, rcfg: dict):
        cls = LINKS[rcfg["type"]]
        kw = {"home": self._home} if rcfg["type"] == "sim" else {}
        return cls(rcfg, self._on_frame, self._update_status, **kw)

    def _home(self):
        try:
            loc = self.mon.cfg["services"]["location"]
            return (loc["lat"], loc["lon"]) if loc.get("lat") is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _sync_links(self):
        """Käynnistää, pysäyttää ja päivittää radioyhteydet asetusten mukaan."""
        want = {}
        if self.cfg.get("enabled"):
            want = {r["id"]: r for r in self.cfg.get("radios") or [] if r.get("enabled")}
        for rid in list(self.links):
            ln = self.links[rid]
            r = want.get(rid)
            if r is None or ln.signature() != self._make_link(r).signature():
                t = self.tasks.pop(rid, None)
                if t:
                    t.cancel()
                ln.close()
                del self.links[rid]
            else:
                ln.cfg = dict(r)            # nimi ja lähetysvalinta päivittyvät katkaisematta
        for rid, r in want.items():
            if rid not in self.links:
                ln = self._make_link(r)
                self.links[rid] = ln
                self.tasks[rid] = asyncio.create_task(ln.run())
        self._update_status()

    def _update_status(self):
        on = [ln for ln in self.links.values() if ln.status["connected"]]
        errs = [f"{ln.name}: {ln.status['error']}" for ln in self.links.values() if ln.status.get("error")]
        since = min((ln.status["since"] for ln in on if ln.status.get("since")), default=None)
        if not self.cfg.get("enabled"):
            errs = []
        elif not self.links:
            errs = [tr("Yhtään radiota ei ole käytössä")]
        self._set_status(connected=bool(on), since=since if on else None, error="; ".join(errs) or None)

    def _on_frame(self, frame: bytes, link):
        now = time.time()
        if len(self.links) > 1:
            t = self.recent.get(frame)
            self.recent[frame] = now
            if len(self.recent) > 500:
                self.recent = {k: v for k, v in self.recent.items() if now - v < 10}
            if t is not None and now - t < 10:
                return                      # sama kehys kuultiin jo toisella radiolla
        self.handle_frame(frame, now)

    async def run(self):
        dec_tasks = [asyncio.create_task(self._watchdog()), asyncio.create_task(self._housekeeping())]
        try:
            while True:
                self._sync_links()
                await self._sleep(5)
        finally:
            for t in [*dec_tasks, *self.tasks.values()]:
                t.cancel()
            for ln in self.links.values():
                ln.close()
            self._commit()

    async def _sleep(self, s):
        try:
            await asyncio.wait_for(self.wake.wait(), s)
        except asyncio.TimeoutError:
            pass
        self.wake.clear()

    def _set_status(self, **kw):
        if any(self.status.get(k) != v for k, v in kw.items()):
            self.status.update(kw)
            self.mon.touch()

    def _extra(self, kind, name, source, ts, lat=None, lon=None, sym=None, reporter="", via="", direct=False,
               data=None, alive=True):
        """Päivittää kohteen viimeisimmän tiedon (sijainti säilyy, jos uudessa paketissa ei ole sitä)."""
        self.db.execute("""INSERT INTO extra (kind, name, source, ts, lat, lon, sym, reporter, via, direct, data, alive)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(kind, name, source) DO UPDATE SET ts=excluded.ts,
                             lat=COALESCE(excluded.lat, lat), lon=COALESCE(excluded.lon, lon), sym=COALESCE(excluded.sym, sym),
                             reporter=excluded.reporter, via=excluded.via, direct=excluded.direct,
                             data=COALESCE(excluded.data, data), alive=excluded.alive""",
                        (kind, name, source, ts, lat, lon, sym, reporter, via, 1 if direct else 0,
                         json.dumps(data) if data is not None else None, 1 if alive else 0))
        self.pending += 1

    def _extras(self, src: str, dest: str, info: bytes, source: str, now: float, via="", direct=False, reporter=""):
        """Objektit, sääasemat ja (iGaten / APRS-IS:n) asemat yhdestä APRS-sisällöstä."""
        ob = parse_object(info)
        if ob:
            self._extra("obj", ob["name"], source, now, ob["lat"], ob["lon"], ob["sym"], reporter or src, via, direct,
                        {"comment": ob["comment"], "type": ob["kind"]}, ob["alive"])
            if ob["wx"]:
                self._extra("wx", ob["name"], source, now, ob["lat"], ob["lon"], ob["sym"], reporter or src, via, direct, ob["wx"])
            return
        wx = station_weather(dest, info)
        pos = parse_position(dest, info)
        if wx:
            self._extra("wx", src, source, now, pos[0] if pos else None, pos[1] if pos else None,
                        parse_symbol(dest, info) if pos else None, reporter, via, direct, wx)
        if source != "rf" and pos:
            self._extra("sta", src, source, now, pos[0], pos[1], parse_symbol(dest, info), reporter, via, direct)

    def is_filter(self) -> str:
        """APRS-IS-aluesuodatin (r/lat/lon/km) kartan APRS-IS-tasolle, jos käytössä."""
        k = int(self.cfg.get("is_km") or 0)
        home = self._home()
        return f"r/{home[0]:.3f}/{home[1]:.3f}/{k}" if k and home else ""

    def handle_is_line(self, line: str):
        """APRS-IS:n paketti (aluesuodattimen asemat kartalle; ei vaikuta RF-tilastoihin)."""
        m = re.match(r"^([A-Z0-9]{1,6}(?:-[0-9A-Z]{1,2})?)>([A-Z0-9-]{1,9})([^:]*):(.+)$", line)
        if not m:
            return
        src, dest, path, info = m[1], m[2], m[3], m[4].encode("latin-1", "replace")
        if info[:1] == b"}":
            tp = parse_third_party(info)
            if not tp:
                return
            src, dest, info = tp["src"], tp["dest"], tp["info"]
        gate = re.search(r",qA.,([A-Z0-9-]+)", path)
        try:
            self._extras(src, dest, info, "is", time.time(), reporter=gate[1] if gate else "")
        except sqlite3.Error as e:
            log.debug("APRS-IS-tallennus: %s", e)

    def handle_frame(self, frame: bytes, now: float | None = None):
        p = parse_ax25(frame)
        if not p:
            return
        now = now or time.time()
        if p["info"][:1] == b"}":               # kolmannen osapuolen paketti: iGate lähetti, sijainti ei ole sen
            pos = None
        else:
            pos = parse_position(p["dest"], p["info"])
        via, direct = heard_via(p["src"], p["path"])
        path = ",".join(c + ("*" if h else "") for c, h in p["path"])
        sym = parse_symbol(p["dest"], p["info"]) if pos else None
        self.db.execute("INSERT INTO packets (ts, src, via, direct, path, lat, lon, sym) VALUES (?,?,?,?,?,?,?,?)",
                        (now, p["src"], via, 1 if direct else 0, path, pos[0] if pos else None, pos[1] if pos else None, sym))
        self.pending += 1
        self.last_heard[via] = now
        if direct:
            self.last_heard[p["src"]] = now
        self.heard_src[p["src"]] = now
        info = p["info"]
        try:
            tp = parse_third_party(info)
            if tp:          # iGaten radioon välittämä: alkuperäinen asema ei kuulunut radiotiellä
                self._extras(tp["src"], tp["dest"], tp["info"], "gate", now, via, direct, reporter=p["src"])
            else:
                self._extras(p["src"], p["dest"], info, "rf", now, via, direct)
        except Exception:  # noqa: BLE001
            log.exception("APRS-kohteen jäsennys")
        if info[:1] == b":" and len(info) >= 11 and info[10:11] == b":":    # APRS-viesti tai kuittaus
            hub = getattr(self.mon, "hub", None)
            if hub is not None:
                try:
                    hub.handle_message(p["src"].upper(), info[1:10].decode("ascii", "replace").strip().upper(),
                                       info[11:].decode("utf-8", "replace").rstrip("\r\n"), "rf")
                except Exception:  # noqa: BLE001
                    log.exception("APRS-viesti radiolta")
        self.status["count"] += 1
        self.status["last"] = now
        if via in self.silent:
            self.silent.discard(via)
            self._alert(tr("{call} kuuluu taas", call=via), tr("APRS-asema kuultiin uudelleen radiotiellä."), "ok")
        self.mon.touch()

    def _commit(self):
        if self.pending:
            try:
                self.db.commit()
            except sqlite3.Error as e:
                log.warning("APRS-tallennus: %s", e)
            self.pending = 0

    async def _housekeeping(self):
        last_prune = 0.0
        while True:
            await asyncio.sleep(5)
            self._commit()
            if time.time() - last_prune > 6 * 3600:
                last_prune = time.time()
                self.db.execute("DELETE FROM packets WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
                self.db.execute("DELETE FROM extra WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
                self.db.commit()

    # ---------------- hiljaisuushälytykset ----------------
    def watched(self, db=None) -> dict[str, str]:
        """Seurattavat asemat: kutsu → syy ('digi' tai 'oma')."""
        db = db or self.db
        out = {c: "oma" for c in self.cfg.get("watch") or []}
        if self.cfg.get("auto_digis"):
            since = time.time() - 7 * 86400
            for (call,) in db.execute("SELECT via FROM packets WHERE ts > ? AND direct = 0 GROUP BY via "
                                           "HAVING COUNT(*) >= ?", (since, int(self.cfg.get("min_heard", 5)))):
                if not is_alias(call):
                    out.setdefault(call, "digi")
        return out

    def check_silence(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        cfg = self.cfg
        lim = int(cfg.get("silence_min", 60)) * 60
        st = self.status
        # Hälytetään vain, kun oma vastaanotto toimii: yhteys on ollut auki koko jakson ja jotain on kuultu.
        if not (cfg.get("enabled") and cfg.get("alerts") and st.get("connected") and st.get("since")):
            return []
        if now - st["since"] < lim or not st.get("last") or now - st["last"] > lim:
            return []
        fired = []
        for call in self.watched():
            t = self.last_heard.get(call)
            if t is None or call in self.silent:
                continue
            if now - t > lim:
                self.silent.add(call)
                fired.append(call)
                mins = int((now - t) // 60)
                self._alert(tr("{call} hiljentynyt", call=call),
                            tr("APRS-asemaa ei ole kuultu radiotiellä {m} minuuttiin.", m=mins), "bad",
                            sms=tr("{call} ei kuulu APRS:ssä ({m} min)", call=call, m=mins))
        return fired

    async def _watchdog(self):
        while True:
            await asyncio.sleep(60)
            try:
                self.check_silence()
            except Exception:  # noqa: BLE001
                log.exception("APRS-hiljaisuustarkistus")

    def _alert(self, title: str, body: str, level: str, sms: str | None = None):
        w = self.mon.watcher
        w.seq += 1
        w.alerts.appendleft({"seq": w.seq, "time": time.time(), "kind": "palvelu", "service": "aprsrx",
                             "service_title": tr("APRS-kuuluvuus"), "title": title, "body": body,
                             "level": level, "call": "", "tg": None, "via": ""})
        if sms and self.cfg.get("sms"):
            self.mon.hub.announce("", sms, source="aprsrx")
        self.mon.touch()

    # ---------------- raportit ----------------
    def snapshot(self) -> dict:
        s = self.status
        radios = []
        for r in self.cfg.get("radios") or []:
            ln = self.links.get(r["id"])
            st = ln.status if ln else {}
            radios.append({"id": r["id"], "name": r.get("name") or describe(r), "type": r["type"],
                           "connected": bool(st.get("connected")), "error": st.get("error"),
                           "count": st.get("count", 0), "last": st.get("last"), "tx": st.get("tx", 0),
                           "active": ln is not None})
        return {"enabled": bool(self.cfg.get("enabled")), "connected": s["connected"], "error": s["error"],
                "last": s["last"], "count": s["count"], "silent": sorted(self.silent), "radios": radios,
                "can_tx": self.can_transmit()}

    def _since(self, period: str) -> float:
        return time.time() - PERIODS.get(period, PERIODS["24h"])

    def _reader(self):
        """Raportit lasketaan taustasäikeessä omalla lukuyhteydellä."""
        return sqlite3.connect(str(self.db_path))

    def positions(self, db=None) -> dict[str, tuple[float, float]]:
        """Asemien viimeisin oma sijainti (30 vrk)."""
        # SQLite: MAX()-koosteen kanssa muut sarakkeet tulevat samalta (uusimmalta) riviltä
        return {src: (lat, lon) for src, lat, lon, _ in (db or self.db).execute(
            "SELECT src, lat, lon, MAX(ts) FROM packets WHERE lat IS NOT NULL AND ts > ? GROUP BY src",
            (time.time() - KEEP_DAYS * 86400,))}

    def summary(self, period: str, home: tuple[float, float] | None) -> dict:
        db = self._reader()
        try:
            return self._summary(db, period, home)
        finally:
            db.close()

    def _summary(self, db, period: str, home) -> dict:
        since = self._since(period)
        q = db.execute
        total, direct, stations, with_pos = q(
            "SELECT COUNT(*), SUM(direct), COUNT(DISTINCT src), SUM(lat IS NOT NULL) FROM packets WHERE ts > ?",
            (since,)).fetchone()
        pos = self.positions(db)
        watched = self.watched(db)
        now = time.time()
        lim = int(self.cfg.get("silence_min", 60)) * 60

        def where(call):
            p = pos.get(call)
            if not p:
                return None, None, None
            return p[0], p[1], (round(km(home[0], home[1], p[0], p[1]), 1) if home else None)
        digis = []
        for call, n, st, last in q("SELECT via, COUNT(*), COUNT(DISTINCT src), MAX(ts) FROM packets "
                                   "WHERE ts > ? AND direct = 0 GROUP BY via ORDER BY COUNT(*) DESC LIMIT 60", (since,)):
            lat, lon, d = where(call)
            lh = self.last_heard.get(call, last)
            digis.append({"call": call, "packets": n, "stations": st, "last": lh, "lat": lat, "lon": lon, "km": d,
                          "alias": is_alias(call), "watched": call in watched,
                          "silent": call in self.silent or (call in watched and now - lh > lim)})
        listed = {d["call"] for d in digis}
        for call, why in watched.items():          # seuratut, joita ei kuultu tällä jaksolla
            if call in listed:
                continue
            lat, lon, d = where(call)
            lh = self.last_heard.get(call)
            digis.append({"call": call, "packets": 0, "stations": 0, "last": lh, "lat": lat, "lon": lon, "km": d,
                          "alias": False, "watched": True, "own": why == "oma",
                          "silent": lh is None or now - lh > lim})
        paths = [{"path": p or "", "count": n} for p, n in
                 q("SELECT path, COUNT(*) FROM packets WHERE ts > ? GROUP BY path ORDER BY COUNT(*) DESC LIMIT 25",
                   (since,))]
        far = []
        if home:
            seen = {}
            for src, lat, lon, ts in q("SELECT src, lat, lon, MAX(ts) FROM packets WHERE ts > ? AND direct = 1 "
                                       "AND lat IS NOT NULL GROUP BY src", (since,)):
                seen[src] = (round(km(home[0], home[1], lat, lon), 1), lat, lon, ts)
            far = [{"call": c, "km": v[0], "lat": v[1], "lon": v[2], "last": v[3]}
                   for c, v in sorted(seen.items(), key=lambda kv: -kv[1][0])[:10]]
        return {"period": period, "status": self.snapshot(), "home": list(home) if home else None,
                "totals": {"packets": total or 0, "direct": direct or 0, "via_digi": (total or 0) - (direct or 0),
                           "stations": stations or 0, "with_pos": with_pos or 0},
                "digis": digis, "paths": paths, "far": far}

    def cells(self, period: str, mode: str, via: str | None, res_km: float, home) -> dict:
        since = self._since(period)
        lat0 = home[0] if home else 60.0
        dlat = res_km / 111.0
        dlon = res_km / (111.0 * max(0.2, math.cos(math.radians(lat0))))
        sql = "SELECT CAST(lat / ? AS INTEGER) - (lat < 0), CAST(lon / ? AS INTEGER) - (lon < 0), COUNT(*), " \
              "COUNT(DISTINCT src), SUM(direct) FROM packets WHERE ts > ? AND lat IS NOT NULL"
        args: list = [dlat, dlon, since]
        if mode == "direct":
            sql += " AND direct = 1"
        elif mode == "digi":
            sql += " AND direct = 0"
        if via:
            sql += " AND via = ?"
            args.append(via)
        sql += " GROUP BY 1, 2"
        cells = [[round(i * dlat, 5), round(j * dlon, 5), n, st, d or 0]
                 for i, j, n, st, d in self._query(sql, args)]
        return {"dlat": dlat, "dlon": dlon, "cells": cells}

    def stations(self, period: str, mode: str, via: str | None, home) -> dict:
        """APRS-asemat-taso: radiolla kuultujen asemien viimeisin oma sijainti jaksolla."""
        since = self._since(period)
        cond, args = "ts > ?", [since]
        if mode == "direct":
            cond += " AND direct = 1"
        elif mode == "digi":
            cond += " AND direct = 0"
        if via:
            cond += " AND via = ?"
            args.append(via)
        db = self._reader()
        try:
            stats = {src: (n, d or 0, last) for src, n, d, last in db.execute(
                f"SELECT src, COUNT(*), SUM(direct), MAX(ts) FROM packets WHERE {cond} GROUP BY src", args)}
            rows = db.execute(f"SELECT src, lat, lon, sym, via, direct, path, MAX(ts) FROM packets "
                              f"WHERE {cond} AND lat IS NOT NULL GROUP BY src", args).fetchall()
            # symboli voi puuttua uusimmasta paketista (vanhat rivit): viimeisin tunnettu symboli
            syms = {src: sym for src, sym, _ in db.execute(
                "SELECT src, sym, MAX(ts) FROM packets WHERE ts > ? AND sym IS NOT NULL GROUP BY src", (since,))}
        finally:
            db.close()
        out = []
        for src, lat, lon, sym, v, direct, path, ts in rows:
            n, nd, last = stats.get(src, (0, 0, ts))
            sym = sym or syms.get(src)
            out.append({"call": src, "lat": lat, "lon": lon, "sym": sym, "kind": symbol_kind(sym), "pos_time": ts,
                        "last": last, "packets": n, "direct": nd, "via": v, "via_direct": bool(direct), "path": path or "",
                        "km": round(km(home[0], home[1], lat, lon), 1) if home else None})
        out.sort(key=lambda x: -x["last"])
        return {"stations": out[:2000]}

    def extra(self, layer: str, period: str, home) -> dict:
        """Kartan lisätasot: wx (sääasemat), obj (objektit ja itemit), gate (iGaten välittämät), is (APRS-IS)."""
        since = self._since(period)
        kind, sources = {"wx": ("wx", None), "obj": ("obj", ("rf", "gate")), "gate": ("sta", ("gate",)),
                         "is": ("sta", ("is",))}.get(layer, (None, None))
        if not kind:
            return {"items": []}
        db = self._reader()
        try:
            cond, args = "kind = ? AND ts > ?", [kind, since]
            if sources:
                cond += f" AND source IN ({','.join('?' * len(sources))})"
                args += list(sources)
            rows = db.execute(f"SELECT name, source, ts, lat, lon, sym, reporter, via, direct, data, alive FROM extra "
                              f"WHERE {cond} ORDER BY ts DESC LIMIT 3000", args).fetchall()
            pos = self.positions(db) if layer == "wx" else {}
        finally:
            db.close()
        out, seen = [], set()
        for name, source, ts, lat, lon, sym, rep_, via, direct, data, alive in rows:
            if layer == "obj" and not alive:
                seen.add(name)
                continue
            if name in seen:
                continue
            seen.add(name)
            if lat is None and name in pos:
                lat, lon = pos[name]
            if lat is None:
                continue
            out.append({"name": name, "source": source, "time": ts, "lat": lat, "lon": lon, "sym": sym,
                        "kind": symbol_kind(sym), "reporter": rep_ or "", "via": via or "", "direct": bool(direct),
                        "data": json.loads(data) if data else None,
                        "km": round(km(home[0], home[1], lat, lon), 1) if home else None})
        return {"items": out}

    def _query(self, sql, args):
        db = self._reader()
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()

    def close(self):
        self._commit()
        try:
            self.db.close()
        except sqlite3.Error:
            pass
