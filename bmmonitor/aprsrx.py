"""APRS-kuuluvuusanalyysi: Graywolfin (tai Dire Wolfin) KISS TCP -portin vastaanottamat paketit.

Ohjelma vain kuuntelee – se ei lähetä mitään radiotielle. Jokaisesta paketista tallennetaan, keneltä se
kuultiin radiotiellä (lähettäjä itse = suoraan, tai viimeinen digipiitteri, jonka H-bitti on asetettu),
koko polku ja lähettäjän sijainti, jos paketissa on sellainen. Näistä lasketaan lämpökartta,
digipiitterien osuudet, yleisimmät polut ja hälytys, kun tuttu digi tai iGate hiljenee.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import sqlite3
import time
from pathlib import Path

from .i18n import tr

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
FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


class KissDecoder:
    """Pilkkoo TCP-virran KISS-kehyksiksi ja palauttaa datakehysten sisällön (AX.25)."""

    def __init__(self):
        self.buf = bytearray()
        self.in_frame = False
        self.esc = False

    def feed(self, data: bytes) -> list[bytes]:
        out = []
        for b in data:
            if b == FEND:
                if self.in_frame and self.buf:
                    frame = bytes(self.buf)
                    if frame and (frame[0] & 0x0F) == 0:      # komento 0 = data (portti ylänibblessä)
                        out.append(frame[1:])
                self.buf.clear()
                self.in_frame = True
                self.esc = False
                continue
            if not self.in_frame:
                continue
            if self.esc:
                self.buf.append(FEND if b == TFEND else FESC if b == TFESC else b)
                self.esc = False
            elif b == FESC:
                self.esc = True
            else:
                self.buf.append(b)
            if len(self.buf) > 2048:          # rikkinäinen virta: aloitetaan alusta
                self.buf.clear()
                self.in_frame = False
        return out


def kiss_frame(ax25: bytes, port: int = 0) -> bytes:
    """Testejä varten: AX.25-kehys KISS-muotoon."""
    body = bytes([port << 4]) + ax25
    body = body.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + body + bytes([FEND])


def _addr(b: bytes) -> tuple[str, bool, bool]:
    call = "".join(chr(c >> 1) for c in b[:6]).strip()
    ssid = (b[6] >> 1) & 0x0F
    return (f"{call}-{ssid}" if ssid else call), bool(b[6] & 0x80), bool(b[6] & 0x01)


def encode_ax25(src: str, dest: str, path: list[str], info: bytes) -> bytes:
    """Testejä varten: rakentaa AX.25 UI -kehyksen. Polun alkio, jonka lopussa on *, merkitään käytetyksi."""
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
    for k, lo, hi in (("port", 1, 65535), ("silence_min", 5, 7 * 24 * 60), ("min_heard", 1, 1000)):
        if k in new:
            try:
                v = int(new[k])
            except (TypeError, ValueError):
                raise ValueError(tr("Virheellinen arvo: {x}", x=new[k])) from None
            if not lo <= v <= hi:
                raise ValueError(tr("Arvon pitää olla {a}–{b}", a=lo, b=hi))
            out[k] = v
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
        self.db.commit()
        self.status = {"connected": False, "error": None, "since": None, "last": None, "count": 0}
        self.wake = asyncio.Event()
        self.pending = 0
        self.last_heard: dict[str, float] = {}     # kutsu → viimeksi kuultu radiotiellä (suoraan tai välittäjänä)
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

    def reconfigure(self):
        self.wake.set()

    # ---------------- vastaanotto ----------------
    async def run(self):
        dec_tasks = [asyncio.create_task(self._watchdog()), asyncio.create_task(self._housekeeping())]
        try:
            while True:
                if not self.cfg.get("enabled"):
                    self._set_status(connected=False, error=None, since=None)
                    await self._sleep(5)
                    continue
                host, port = self.cfg["host"], int(self.cfg["port"])
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
                except Exception as e:  # noqa: BLE001
                    self._set_status(connected=False, error=tr("Ei yhteyttä KISS-porttiin {h}:{p} ({e})",
                                                               h=host, p=port, e=e.__class__.__name__ if not str(e) else e))
                    await self._sleep(15)
                    continue
                log.info("KISS-yhteys %s:%s", host, port)
                self._set_status(connected=True, error=None, since=time.time())
                dec = KissDecoder()
                try:
                    while self.cfg.get("enabled") and (self.cfg["host"], int(self.cfg["port"])) == (host, port):
                        try:
                            data = await asyncio.wait_for(reader.read(4096), 30)
                        except asyncio.TimeoutError:
                            continue
                        if not data:
                            raise ConnectionError(tr("KISS-portti sulki yhteyden"))
                        for fr in dec.feed(data):
                            self.handle_frame(fr)
                except Exception as e:  # noqa: BLE001
                    self._set_status(connected=False, error=str(e) or e.__class__.__name__)
                    log.warning("KISS: %s", e)
                finally:
                    writer.close()
                    self._set_status(connected=False, since=None)
                await self._sleep(5)
        finally:
            for t in dec_tasks:
                t.cancel()
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
        self.db.execute("INSERT INTO packets VALUES (?,?,?,?,?,?,?)",
                        (now, p["src"], via, 1 if direct else 0, path, pos[0] if pos else None, pos[1] if pos else None))
        self.pending += 1
        self.last_heard[via] = now
        if direct:
            self.last_heard[p["src"]] = now
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
        return {"enabled": bool(self.cfg.get("enabled")), "connected": s["connected"], "error": s["error"],
                "last": s["last"], "count": s["count"], "silent": sorted(self.silent)}

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
