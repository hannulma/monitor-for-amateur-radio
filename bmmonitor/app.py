"""Monitor for BrandMeister - BrandMeister-puheryhmien ja APRS/DMR-viestien seuranta.

Kuuntelee Brandmeisterin julkista Last Heard -virtaa (socket.io,
https://api.brandmeister.network/lh/socket.io) ja näyttää valittujen
puheryhmien tiedot paikallisessa selainkäyttöliittymässä.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import fnmatch
import json
import logging
import os
import random
import socket
import sys
import time
import webbrowser
from collections import deque
from pathlib import Path

import aiohttp
import socketio
from aiohttp import web

from . import APP_ID, APP_NAME, __version__
from .messaging import DEFAULT_MESSAGING, MessagingHub, friendly_error, http_session, normalize_messaging, split_message, ws_options
from .bmapi import BMApi, BMApiError
from .services import DEFAULT_SERVICES, ServiceHub, normalize_services
from .features import (CALL_OR_ID, DEFAULT_DEVICES, DEFAULT_WATCH, Commands, DeviceMonitor, Heard, Stats,
                       Watcher, bm_user_history)

BM_URL = "https://api.brandmeister.network"
LH_PATH = "/lh"
HISTORY_ROWS = 500   # BM palauttaa enintään n. 500 viimeisintä tapahtumaa
TG_NAMES_URL = "https://api.brandmeister.network/v2/talkgroup"

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_CALLS = 500
MAX_DATA = 500
STALE_ACTIVE_SEC = 300          # aktiivinen sessio ilman Stopia -> unohdetaan
RAW_LOG_MAX_BYTES = 5_000_000

log = logging.getLogger("bmmonitor")

DEFAULT_CONFIG = {
    "talkgroups": ["244*", "91"],
    "data_filter": "all",       # "all" = koko verkon dataliikenne, "matched" = vain seuratut TG:t
    "min_duration": 0,          # sekuntia; lyhyemmät avaukset ohitetaan
    "port": 8765,
    "log_csv": True,
    "calls_limit": 15,          # Viimeisimmät puhelut -listan pituus (10/15/20/25)
    "bm_api_key": "",           # BM SelfCare API-avain (vain laitteen hallintaan)
}
CALLS_LIMITS = (10, 15, 20, 25)


# --------------------------------------------------------------------------
# Apufunktiot
# --------------------------------------------------------------------------
def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "BMMonitor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_patterns(items) -> list[str]:
    """Hyväksyy listan tai merkkijonon. Sallitut: 244, 244*, 24?1 ."""
    if isinstance(items, str):
        items = items.replace(",", " ").replace(";", " ").split()
    out: list[str] = []
    for raw in items:
        p = str(raw).strip()
        if not p:
            continue
        if not all(c.isdigit() or c in "*?" for c in p):
            raise ValueError(f"Virheellinen puheryhmä: {p!r} (sallitut: numerot, * ja ?)")
        if p not in out:
            out.append(p)
    return out


class Matcher:
    def __init__(self, patterns: list[str]):
        self.patterns = patterns
        self.exact = {int(p) for p in patterns if p.isdigit()}
        self.wild = [p for p in patterns if not p.isdigit()]

    def match(self, tg) -> str | None:
        if tg is None:
            return None
        try:
            tg = int(tg)
        except (TypeError, ValueError):
            return None
        if tg in self.exact:
            return str(tg)
        s = str(tg)
        for p in self.wild:
            if fnmatch.fnmatchcase(s, p):
                return p
        return None


def _lower_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).lower() for x in v]
    if isinstance(v, str):
        return [v.lower()]
    return []


def is_data_session(p: dict) -> bool:
    ct = _lower_list(p.get("CallTypes"))
    if any(k in t for t in ct for k in ("data", "text", "sms", "message")):
        return True
    if "voice" not in ct and (p.get("DataCount") or 0) > 0:
        return True
    return False


def is_group_call(p: dict) -> bool:
    ct = _lower_list(p.get("CallTypes"))
    if not ct:
        return True
    return "group" in ct


TEXT_FIELDS = ("Text", "Message", "TextMessage", "SMS", "Payload", "Data")


# --------------------------------------------------------------------------
# Sovelluksen tila
# --------------------------------------------------------------------------
class Monitor:
    def __init__(self, cfg_path: Path, demo: bool = False):
        self.cfg_path = cfg_path
        self.demo = demo
        self.cfg = dict(DEFAULT_CONFIG)
        if cfg_path.exists():
            try:
                self.cfg.update(json.loads(cfg_path.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                log.warning("Asetustiedoston luku epäonnistui: %s", e)
        self.cfg["talkgroups"] = parse_patterns(self.cfg.get("talkgroups", []))
        self.cfg["messaging"] = {**DEFAULT_MESSAGING, **(self.cfg.get("messaging") or {})}
        self.cfg["watch"] = {**DEFAULT_WATCH, **(self.cfg.get("watch") or {})}
        self.cfg["devices"] = {**DEFAULT_DEVICES, **(self.cfg.get("devices") or {})}
        svc = self.cfg.get("services") or {}
        self.cfg["services"] = {k: {**v, **(svc.get(k) or {})} for k, v in DEFAULT_SERVICES.items()}
        self.matcher = Matcher(self.cfg["talkgroups"])

        self.tg_names: dict[int, str] = {}
        self.tg_stats: dict[int, dict] = {}
        self.sessions: dict[str, dict] = {}        # aktiiviset puhesessiot
        self.calls: deque[dict] = deque(maxlen=MAX_CALLS)
        self.data: deque[dict] = deque(maxlen=MAX_DATA)
        self.data_index: dict[str, dict] = {}
        self.dedup: dict[tuple, float] = {}

        self.status = {"connected": False, "since": None, "events": 0,
                       "last_event": None, "error": None, "demo": demo}
        self.started = time.time()
        self.shutdown_event = asyncio.Event()
        self.clients: set[web.WebSocketResponse] = set()
        self.dirty = True

        d = config_dir()
        self.calls_csv = d / "calls.csv"
        self.data_csv = d / "sms_data.csv"
        self.raw_log = d / "raw_data_events.jsonl"
        self.names_cache = d / "tg_names.json"
        self.hub = MessagingHub(self.cfg["messaging"], d, self.touch)
        self.heard = Heard()
        self.watcher = Watcher(self)
        self.api = BMApi(lambda: self.cfg.get("bm_api_key", ""))
        self.devmon = DeviceMonitor(self, self.api)
        self.stats = Stats(self)
        self.stats.load_csv(self.calls_csv)
        self.commands = Commands(self)
        self.services = ServiceHub(self)
        self.hub.command_handler = self.commands.handle

    def touch(self):
        self.dirty = True

    # ---------------- asetukset ----------------
    def save_config(self):
        self.cfg_path.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    def update_config(self, new: dict):
        if "talkgroups" in new:
            pats = parse_patterns(new["talkgroups"])
            self.cfg["talkgroups"] = pats
            self.matcher = Matcher(pats)
            # poista tilastot, jotka eivät enää kuulu seurattaviin
            self.tg_stats = {tg: s for tg, s in self.tg_stats.items() if self.matcher.match(tg)}
            for s in self.tg_stats.values():
                s["pattern"] = self.matcher.match(s["tg"])
        if "data_filter" in new and new["data_filter"] in ("all", "matched"):
            self.cfg["data_filter"] = new["data_filter"]
        if "min_duration" in new:
            self.cfg["min_duration"] = max(0, float(new["min_duration"] or 0))
        if "log_csv" in new:
            self.cfg["log_csv"] = bool(new["log_csv"])
        if "calls_limit" in new:
            n = int(new["calls_limit"])
            if n not in CALLS_LIMITS:
                raise ValueError(f"Sallitut arvot: {', '.join(map(str, CALLS_LIMITS))}")
            self.cfg["calls_limit"] = n
        if "watch" in new:
            w = new["watch"]
            out = dict(self.cfg["watch"])
            if "list" in w:
                items = w["list"].replace(",", " ").split() if isinstance(w["list"], str) else w["list"]
                lst = []
                for x in items:
                    x = str(x).strip().upper().split("-")[0]
                    if not x:
                        continue
                    if not CALL_OR_ID.fullmatch(x):
                        raise ValueError(f"Virheellinen kutsu tai DMR-ID: {x}")
                    if x not in lst:
                        lst.append(x)
                out["list"] = lst
            if "quiet_min" in w:
                out["quiet_min"] = max(0, min(1440, float(w["quiet_min"] or 0)))
            if w.get("scope") in ("all", "matched"):
                out["scope"] = w["scope"]
            for k in ("desktop", "sound", "sms"):
                if k in w:
                    out[k] = bool(w[k])
            self.cfg["watch"] = out
        if "devices" in new:
            dv = new["devices"]
            out = dict(self.cfg["devices"])
            if "list" in dv:
                lst, seen = [], set()
                for d in dv["list"]:
                    did = str(d.get("id") if isinstance(d, dict) else d).strip()
                    if not did:
                        continue
                    if not did.isdigit() or not 4 <= len(did) <= 10:
                        raise ValueError(f"Virheellinen laitteen ID: {did}")
                    if int(did) in seen:
                        continue
                    seen.add(int(did))
                    lst.append({"id": int(did), "note": str(d.get("note", "") if isinstance(d, dict) else "")[:40]})
                out["list"] = lst
            if "offline_min" in dv:
                out["offline_min"] = max(2, min(1440, float(dv["offline_min"] or 15)))
            if "sms" in dv:
                out["sms"] = bool(dv["sms"])
            self.cfg["devices"] = out
            self.devmon._ids_cache = self.devmon.ids()
            self.devmon.wake.set()
        if "services" in new:
            out, changed = normalize_services(new["services"], self.cfg["services"])
            self.cfg["services"] = out
            self.services.reconfigure(changed)
        if "bm_api_key" in new:
            key = str(new["bm_api_key"] or "").strip()
            if key != "********":
                self.cfg["bm_api_key"] = key
        if "messaging" in new:
            self.cfg["messaging"] = normalize_messaging(new["messaging"], self.cfg["messaging"])
            self.hub.reconfigure(self.cfg["messaging"])
        self.save_config()
        self.dirty = True

    # ---------------- puheryhmien nimet ----------------
    async def load_tg_names(self):
        if self.names_cache.exists():
            try:
                self.tg_names = {int(k): v for k, v in json.loads(self.names_cache.read_text("utf-8")).items()}
            except Exception:  # noqa: BLE001
                pass
            if time.time() - self.names_cache.stat().st_mtime < 86400 and self.tg_names:
                return
        if self.demo:
            return
        try:
            async with http_session(20) as s:
                async with s.get(TG_NAMES_URL) as r:
                    r.raise_for_status()
                    data = await r.json(content_type=None)
            if isinstance(data, dict):
                self.tg_names = {int(k): str(v) for k, v in data.items() if str(k).isdigit()}
            elif isinstance(data, list):
                self.tg_names = {int(x["ID"]): str(x.get("Name", "")) for x in data if "ID" in x}
            self.names_cache.write_text(json.dumps(self.tg_names, ensure_ascii=False), encoding="utf-8")
            log.info("Ladattu %d puheryhmän nimeä", len(self.tg_names))
            self.dirty = True
        except Exception as e:  # noqa: BLE001
            log.warning("Puheryhmien nimien haku epäonnistui: %s", e)

    def tg_name(self, tg) -> str:
        try:
            return self.tg_names.get(int(tg), "")
        except (TypeError, ValueError):
            return ""

    # ---------------- tapahtumien käsittely ----------------
    def handle_payload(self, p: dict, history: bool = False):
        try:
            self._handle_payload(p, history)
        except Exception:  # noqa: BLE001
            n = self.status.get("handler_errors", 0) + 1
            self.status["handler_errors"] = n
            if n <= 5:
                log.exception("Virhe tapahtuman käsittelyssä: %s", str(p)[:300])

    def _handle_payload(self, p: dict, history: bool = False):
        for k in ("Start", "Stop", "DestinationID", "SourceID"):
            v = p.get(k)
            if isinstance(v, str) and v.strip().lstrip("-").isdigit():
                p[k] = int(v)
        for k in ("SourceCall", "LinkCall", "TalkerAlias", "SourceName", "DestinationName", "DestinationCall"):
            if isinstance(p.get(k), str):
                p[k] = p[k].strip()
        if not p.get("SourceCall") and p.get("TalkerAlias"):
            p["SourceCall"] = p["TalkerAlias"].split()[0]
        if p.get("DestinationName") and p.get("DestinationID") is not None:
            try:
                self.tg_names.setdefault(int(p["DestinationID"]), p["DestinationName"])
            except (TypeError, ValueError):
                pass
        self.status["events"] += 1
        self.status["last_event"] = time.time()
        if is_data_session(p):
            self._handle_data(p)
            return
        self.heard.update(p, self.tg_name)
        self.watcher.on_event(p, history)
        self.devmon.on_event(p)
        tg = p.get("DestinationID")
        pattern = self.matcher.match(tg)
        if not pattern or not is_group_call(p):
            return
        self._handle_voice(p, int(tg), pattern)

    def _stats(self, tg: int, pattern: str) -> dict:
        s = self.tg_stats.get(tg)
        if s is None:
            s = {"tg": tg, "pattern": pattern, "count": 0, "airtime": 0.0,
                 "last": None, "active": None}
            self.tg_stats[tg] = s
        return s

    def _handle_voice(self, p: dict, tg: int, pattern: str):
        ev = p.get("Event", "")
        sid = p.get("SessionID") or f"{p.get('SourceID')}-{tg}-{p.get('Start')}"
        start = p.get("Start") or time.time()
        stop = p.get("Stop") or 0
        info = {
            "tg": tg,
            "tg_name": self.tg_name(tg),
            "call": p.get("SourceCall") or "",
            "name": p.get("SourceName") or "",
            "id": p.get("SourceID"),
            "alias": p.get("TalkerAlias") or "",
            "via": p.get("LinkCall") or p.get("LinkName") or "",
            "context": p.get("ContextID"),
            "master": p.get("Master"),
            "slot": p.get("Slot"),
            "start": start,
            "stop": stop,
        }
        s = self._stats(tg, pattern)

        if stop and stop > 0 or ev == "Session-Stop":
            self.sessions.pop(sid, None)
            if s["active"] and s["active"].get("sid") == sid:
                s["active"] = None
            if not stop:
                stop = time.time()
            dur = max(0.0, float(stop) - float(start))
            info["stop"] = stop
            info["duration"] = round(dur, 1)
            key = (info["id"], tg, int(float(start) // 3))
            if key in self.dedup:
                return
            self.dedup[key] = time.time()
            try:
                min_dur = float(self.cfg.get("min_duration") or 0)
            except (TypeError, ValueError):
                min_dur = 0.0
            if dur < min_dur:
                self.status["skipped_short"] = self.status.get("skipped_short", 0) + 1
                self.dirty = True
                return
            s["count"] += 1
            s["airtime"] += dur
            s["last"] = info
            keep = True
            if len(self.calls) == self.calls.maxlen:
                oldest = min(self.calls, key=lambda c: float(c.get("start") or 0))
                if float(info["start"]) < float(oldest.get("start") or 0):
                    keep = False          # vanhempi kuin kaikki listassa olevat
                else:
                    self.calls.remove(oldest)
            if keep:
                self.calls.appendleft(info)
            self.stats.add(info)
            self._csv(self.calls_csv,
                      ["aika", "tg", "tg_nimi", "kutsu", "nimi", "dmr_id", "kesto_s", "kautta"],
                      [_iso(start), tg, info["tg_name"], info["call"], info["name"],
                       info["id"], info["duration"], info["via"]])
        else:
            info["sid"] = sid
            self.sessions[sid] = info
            if s["active"] is None or s["active"].get("sid") == sid or \
                    float(start) >= float(s["active"].get("start", 0)):
                s["active"] = info
        self.dirty = True

    def _handle_data(self, p: dict):
        dst = p.get("DestinationID")
        if self.cfg.get("data_filter") == "matched" and not self.matcher.match(dst) \
                and not self.matcher.match(p.get("SourceID")):
            return
        sid = p.get("SessionID") or f"{p.get('SourceID')}-{dst}-{p.get('Start')}"
        text = ""
        for f in TEXT_FIELDS:
            v = p.get(f)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break
        ct = p.get("CallTypes") or []
        entry = self.data_index.get(sid)
        if entry is None:
            entry = {
                "sid": sid,
                "start": p.get("Start") or time.time(),
                "from_call": p.get("SourceCall") or "",
                "from_name": p.get("SourceName") or "",
                "from_id": p.get("SourceID"),
                "to_call": p.get("DestinationCall") or "",
                "to_id": dst,
                "to_name": self.tg_name(dst) if "Group" in ct else (p.get("DestinationName") or ""),
                "kind": "ryhmä" if "Group" in ct else ("yksityinen" if "Private" in ct else ""),
                "via": p.get("LinkCall") or p.get("LinkName") or "",
                "types": ", ".join(map(str, ct)) if isinstance(ct, list) else str(ct),
                "count": p.get("DataCount") or 0,
                "text": text,
                "done": False,
            }
            self.data_index[sid] = entry
            self.data.appendleft(entry)
            # siivoa indeksi
            if len(self.data_index) > MAX_DATA * 2:
                keep = {e["sid"] for e in self.data}
                self.data_index = {k: v for k, v in self.data_index.items() if k in keep}
        else:
            entry["count"] = p.get("DataCount") or entry["count"]
            if text:
                entry["text"] = text
        if (p.get("Stop") or 0) > 0 or p.get("Event") == "Session-Stop":
            if not entry["done"]:
                entry["done"] = True
                self._csv(self.data_csv,
                          ["aika", "lahettaja", "lahettaja_id", "vastaanottaja", "vastaanottaja_id",
                           "tyyppi", "kautta", "datablokit", "teksti"],
                          [_iso(entry["start"]), entry["from_call"], entry["from_id"], entry["to_call"],
                           entry["to_id"], entry["kind"], entry["via"], entry["count"], entry["text"]])
        self._raw(p)
        self.dirty = True

    def _csv(self, path: Path, header: list, row: list):
        if not self.cfg.get("log_csv", True):
            return
        try:
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(header)
                w.writerow(row)
        except Exception as e:  # noqa: BLE001
            log.warning("CSV-kirjoitus epäonnistui: %s", e)

    def _raw(self, p: dict):
        """Tallentaa dataliikenteen raakatapahtumat, jotta kenttiä voi tutkia."""
        try:
            if self.raw_log.exists() and self.raw_log.stat().st_size > RAW_LOG_MAX_BYTES:
                self.raw_log.replace(self.raw_log.with_suffix(".old.jsonl"))
            with self.raw_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def housekeeping(self):
        now = time.time()
        for sid, info in list(self.sessions.items()):
            if now - float(info.get("start", now)) > STALE_ACTIVE_SEC:
                self.sessions.pop(sid, None)
                s = self.tg_stats.get(info["tg"])
                if s and s["active"] and s["active"].get("sid") == sid:
                    s["active"] = None
                    self.dirty = True
        self.dedup = {k: t for k, t in self.dedup.items() if now - t < 600}

    def watch_status(self) -> list[dict]:
        out = []
        for k in self.cfg["watch"].get("list", []):
            hits = self.heard.lookup(dmr_id=k) if k.isdigit() else self.heard.lookup(call=k)
            e = hits[0] if hits else {}
            out.append({"key": k, "call": e.get("call") or ("" if k.isdigit() else k), "id": e.get("id"),
                        "name": e.get("name", ""), "tg": e.get("tg"), "tg_name": e.get("tg_name", ""),
                        "to": e.get("to_call"), "via": e.get("via", ""), "start": e.get("start"),
                        "active": bool(e.get("active")) and time.time() - float(e.get("start") or 0) < 300,
                        "last_activity": self.watcher.last_activity.get(k)})
        return out

    # ---------------- tila käyttöliittymälle ----------------
    def snapshot(self) -> dict:
        rows = []
        seen = set()
        for tg, s in self.tg_stats.items():
            seen.add(tg)
            rows.append({**s, "name": self.tg_name(tg)})
        for p in self.cfg["talkgroups"]:
            if p.isdigit() and int(p) not in seen:
                rows.append({"tg": int(p), "pattern": p, "name": self.tg_name(p), "count": 0,
                             "airtime": 0, "last": None, "active": None})
        return {
            "type": "snapshot",
            "now": time.time(),
            "version": __version__,
            "config": {**self.cfg, "bm_api_key": "********" if self.cfg.get("bm_api_key") else ""},
            "status": self.status,
            "started": self.started,
            "talkgroups": rows,
            "messaging": self.hub.status(),
            "alerts": list(self.watcher.alerts)[:50],
            "watch_status": self.watch_status(),
            "devices": self.devmon.snapshot(),
            "services": self.services.snapshot(),
            "calls": sorted(self.calls, key=lambda c: float(c.get("start") or 0), reverse=True)[:200],
            "data": sorted(self.data, key=lambda c: float(c.get("start") or 0), reverse=True)[:200],
            "paths": {"config": str(self.cfg_path), "calls_csv": str(self.calls_csv),
                      "data_csv": str(self.data_csv), "raw": str(self.raw_log)},
        }

    async def broadcast_loop(self):
        while True:
            await asyncio.sleep(0.5)
            self.housekeeping()
            if not self.dirty or not self.clients:
                continue
            self.dirty = False
            msg = json.dumps(self.snapshot(), ensure_ascii=False, default=str)
            for ws in list(self.clients):
                try:
                    await ws.send_str(msg)
                except Exception:  # noqa: BLE001
                    self.clients.discard(ws)


def _iso(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ""


# --------------------------------------------------------------------------
# Brandmeister-yhteys
# --------------------------------------------------------------------------
def decode_payload(msg):
    """BM lähettää {'topic': ..., 'payload': '<json-merkkijono>'}; hyväksytään myös muut muodot."""
    if isinstance(msg, (list, tuple)) and msg:
        msg = msg[0]
    payload = msg.get("payload", msg) if isinstance(msg, dict) else msg
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else None


def bm_thread(mon: Monitor, loop: asyncio.AbstractEventLoop):
    """Sama synkroninen socket.io-asiakas kuin Brandmeisterin virallisessa
    Python-esimerkissä. Ajetaan omassa säikeessään."""
    sio = socketio.Client(reconnection=True, reconnection_delay=2,
                          reconnection_delay_max=30, logger=False, engineio_logger=False,
                          websocket_extra_options=ws_options())
    warned = {"parse": False}

    def call(fn, *a):
        loop.call_soon_threadsafe(fn, *a)

    def set_status(**kw):
        mon.status.update(**kw)
        mon.dirty = True

    history: list[dict] = []

    @sio.event
    def connect():
        log.info("Yhdistetty Brandmeisteriin")
        call(lambda: set_status(connected=True, since=time.time(), error=None))
        # Ilman join-viestiä palvelin ei lähetä mitään. "everything" = koko verkko,
        # jota tarvitaan *-hakuihin ja koko verkon dataliikenteeseen.
        history.clear()
        sio.emit("join", "everything")
        sio.emit("searchHouse", {"query": None, "amount": HISTORY_ROWS})

    @sio.on("searchHouseComplete")
    def history_done(*_):
        rows = sorted(history, key=lambda p: float(p.get("Start") or 0))
        history.clear()
        log.info("Historiasta ladattu %d tapahtumaa", len(rows))
        for p in rows:
            call(mon.handle_payload, p, True)

    @sio.event
    def connect_error(data):
        log.warning("Yhteysvirhe: %s", data)
        call(lambda: set_status(connected=False, error=str(data)))

    @sio.event
    def disconnect(*_):
        log.warning("Yhteys Brandmeisteriin katkesi")
        call(lambda: set_status(connected=False))

    def on_any(event, data):
        mon.status["raw"] = mon.status.get("raw", 0) + 1
        mon.status["last_raw_event"] = event
        try:
                p = decode_payload(data)
        except Exception as e:  # noqa: BLE001
            if not warned["parse"]:
                warned["parse"] = True
                log.warning("Tuntematon viestimuoto (%s): %s – %r", event, e, str(data)[:300])
            return
        if p is not None:
            call(mon.handle_payload, p)

    @sio.on("mqtt")
    def on_mqtt(data):
        if isinstance(data, dict) and data.get("topic") == "LH-Startup":
            try:
                p = decode_payload(data)
                if p:
                    history.append(p)
            except Exception:  # noqa: BLE001
                pass
            return
        on_any("mqtt", data)

    @sio.on("*")
    def catch_all(event, data=None):
        if mon.status.get("raw", 0) < 5:
            log.info("Tapahtuma '%s': %s", event, str(data)[:200])
        on_any(event, data)

    while True:
        try:
            sio.connect(BM_URL, socketio_path=LH_PATH, transports=["websocket"], wait_timeout=20)
            sio.wait()
        except Exception as e:  # noqa: BLE001
            log.warning("Yhteysvirhe: %s – yritetään uudelleen 10 s päästä", e)
            call(lambda e=e: set_status(connected=False, error=str(e)))
        time.sleep(10)


async def bm_loop(mon: Monitor):
    import threading
    loop = asyncio.get_running_loop()
    threading.Thread(target=bm_thread, args=(mon, loop), daemon=True).start()
    while True:
        await asyncio.sleep(3600)


def diagnose(seconds: int = 30):
    """Tulostaa mitä Brandmeisterin palvelimelta oikeasti tulee."""
    import platform
    import engineio
    print(f"{APP_NAME} {__version__} – vianetsintä")
    print(f"Python {platform.python_version()} / {platform.system()} {platform.release()}")
    print(f"python-socketio {getattr(socketio, '__version__', '?')}, "
          f"python-engineio {getattr(engineio, '__version__', '?')}")
    counts: dict[str, int] = {}
    shown = [0]
    sio = socketio.Client(logger=False, engineio_logger=False, websocket_extra_options=ws_options())

    @sio.event
    def connect():
        print(f"[{time.strftime('%H:%M:%S')}] YHDISTETTY, kuunnellaan {seconds} s…")
        sio.emit("join", "everything")

    @sio.event
    def connect_error(data):
        print("YHTEYSVIRHE:", data)

    @sio.event
    def disconnect(*_):
        print("Yhteys katkesi")

    @sio.on("*")
    def any_event(event, data=None):
        counts[event] = counts.get(event, 0) + 1
        if shown[0] < 5:
            shown[0] += 1
            print(f"\n--- tapahtuma '{event}' ---\n{str(data)[:800]}")

    @sio.on("mqtt")
    def mqtt(data):
        any_event("mqtt", data)

    try:
        sio.connect(BM_URL, socketio_path=LH_PATH, transports=["websocket"], wait_timeout=20)
        time.sleep(seconds)
    except Exception as e:  # noqa: BLE001
        print("VIRHE:", repr(e))
    finally:
        try:
            sio.disconnect()
        except Exception:  # noqa: BLE001
            pass
    print("\nYhteenveto:", counts or "EI YHTÄÄN TAPAHTUMAA")


async def demo_loop(mon: Monitor):
    """Tuottaa keksittyä liikennettä käyttöliittymän testaamiseen."""
    mon.status.update(connected=True, since=time.time())
    mon.tg_names.update({244: "Finland", 2441: "Uusimaa", 2442: "Turku", 2443: "Tampere",
                         2449: "Lappi", 91: "Worldwide", 24400: "Finland Chat"})
    users = [("OH2ABC", "Matti", 2440001), ("OH3XYZ", "Pekka", 2440002), ("OH6QQ", "Liisa", 2440003),
             ("OH1TT", "Juha", 2440004), ("DL1AAA", "Hans", 2620001), ("OH8RR", "Anna", 2440005)]
    tgs = [244, 2441, 2442, 2443, 2449, 91, 24400, 3100]
    n = 0
    while True:
        n += 1
        call, name, uid = random.choice(users)
        start = time.time()
        sid = f"demo-{n}"
        if random.random() < 0.2:
            to = random.choice(users)
            base = {"SessionID": sid, "SourceCall": call, "SourceName": name, "SourceID": uid,
                    "DestinationID": to[2], "DestinationCall": to[0], "CallTypes": ["Data", "Private"],
                    "LinkCall": "OH2RUA", "Start": int(start), "Stop": 0, "DataCount": 3}
            mon.handle_payload({**base, "Event": "Session-Start"})
            await asyncio.sleep(1)
            mon.handle_payload({**base, "Event": "Session-Stop", "Stop": int(time.time())})
            await asyncio.sleep(random.uniform(1, 3))
            continue
        tg = random.choice(tgs)
        base = {"SessionID": sid, "SourceCall": call, "SourceName": name, "SourceID": uid,
                "DestinationID": tg, "CallTypes": ["Voice", "Group"], "LinkCall": "OH2RUC",
                "Slot": 2, "Start": int(start), "Stop": 0}
        mon.handle_payload({**base, "Event": "Session-Start"})
        dur = random.uniform(1, 12)
        await asyncio.sleep(dur)
        mon.handle_payload({**base, "Event": "Session-Stop", "Stop": int(start + dur)})
        await asyncio.sleep(random.uniform(0.5, 4))


# --------------------------------------------------------------------------
# Web-palvelin
# --------------------------------------------------------------------------
def make_app(mon: Monitor) -> web.Application:
    app = web.Application()

    async def index(_):
        return web.FileResponse(STATIC_DIR / "index.html")

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        mon.clients.add(ws)
        await ws.send_str(json.dumps(mon.snapshot(), ensure_ascii=False, default=str))
        try:
            async for _ in ws:
                pass
        finally:
            mon.clients.discard(ws)
        return ws

    async def get_config(_):
        return web.json_response(mon.cfg)

    async def post_config(request):
        try:
            body = await request.json()
            mon.update_config(body)
            return web.json_response({"ok": True, "config": mon.cfg})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def clear(_):
        mon.calls.clear()
        mon.data.clear()
        mon.data_index.clear()
        for s in mon.tg_stats.values():
            s.update(count=0, airtime=0.0, last=None)
        mon.dirty = True
        return web.json_response({"ok": True})

    async def test_message(request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        text = (body.get("text") or "").strip() or f"Monitor for BrandMeister: testiviesti {time.strftime('%H:%M')}"
        m = mon.cfg["messaging"]
        if not m.get("enabled"):
            return web.json_response({"ok": False, "error": "Lähetys ei ole päällä"}, status=400)
        if not m.get("recipients"):
            return web.json_response({"ok": False, "error": "Vastaanottajia ei ole määritetty"}, status=400)
        mon.hub.announce("", text, source="testi")
        return web.json_response({"ok": True})

    async def check_feeds(_):
        mon.hub.feeds.wake.set()
        return web.json_response({"ok": True})

    async def preview_feed(request):
        url = request.query.get("url", "")
        try:
            title, items = await mon.hub.feeds.fetch(url)
            return web.json_response({"ok": True, "title": title, "items": items[:5]})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=400)

    async def station(request):
        q = (request.query.get("q") or "").strip().upper().split("-")[0]
        if not q:
            return web.json_response({"ok": False, "error": "Anna kutsu tai DMR-ID"}, status=400)
        is_id = q.isdigit()
        out = {"ok": True, "q": q, "registry": [], "devices": [], "heard": [], "errors": []}
        call = q
        try:
            if is_id:
                r = await mon.api.registry(int(q))
                if r:
                    out["registry"] = [r] if isinstance(r, dict) else list(r)
                    call = (out["registry"][0].get("Call") or "").upper() or q
            if not is_id or call != q:
                out["registry"] = out["registry"] or await mon.api.registry_by_call(call)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"Rekisteri: {friendly_error(e)}")
        try:
            if not call.isdigit():
                out["devices"] = await mon.api.devices_by_call(call)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"Laitteet: {friendly_error(e)}")
        ids = {int(r.get("ID")) for r in out["registry"] if str(r.get("ID", "")).isdigit()}
        if is_id:
            ids.add(int(q))
        heard = []
        for i in ids:
            heard += mon.heard.lookup(dmr_id=i)
        if not call.isdigit():
            heard += [e for e in mon.heard.lookup(call=call) if e not in heard]
        out["heard"] = sorted(heard, key=lambda e: -float(e.get("start") or 0))[:10]
        out["call"] = call
        out["local_calls"] = [c for c in list(mon.stats.calls)[-5000:] if (c["call"] or "").upper() == call][-10:][::-1]
        out["watched"] = call in mon.cfg["watch"]["list"] or q in mon.cfg["watch"]["list"]
        return web.json_response(out, dumps=lambda o: json.dumps(o, default=str))

    async def stats(request):
        try:
            hours = int(request.query.get("hours") or 24)
            tg = request.query.get("tg")
            tg = int(tg) if tg else None
        except ValueError:
            return web.json_response({"ok": False, "error": "Virheellinen parametri"}, status=400)
        hours = max(1, min(24 * 14, hours))
        return web.json_response({"ok": True, **mon.stats.compute(hours, tg)}, dumps=lambda o: json.dumps(o, default=str))

    async def stats_bm(request):
        try:
            tg = int(request.query.get("tg") or 0)
        except ValueError:
            tg = 0
        if tg <= 0:
            return web.json_response({"ok": False, "error": "Anna puheryhmä"}, status=400)
        hit = mon.stats.bm_cache.get(str(tg))
        if hit and time.time() - hit[0] < 300:
            users = hit[1]
        else:
            try:
                users = await asyncio.get_running_loop().run_in_executor(None, bm_user_history, [tg])
            except Exception as e:  # noqa: BLE001
                return web.json_response({"ok": False, "error": friendly_error(e)}, status=502)
            mon.stats.bm_cache[str(tg)] = (time.time(), users)
        users = sorted(users, key=lambda u: -u["count"])
        return web.json_response({"ok": True, "tg": tg, "name": mon.tg_name(tg), "total_users": len(users),
                                  "total_calls": sum(u["count"] for u in users),
                                  "oldest": min((u["last"] for u in users), default=None), "users": users[:25]})

    async def device_action(request):
        try:
            b = await request.json()
            dev = int(b["id"])
            if dev not in mon.devmon.ids():
                raise ValueError("Laite ei ole seurattavien listalla")
            act = b.get("action")
            slot = int(b.get("slot", 0))
            if act == "add":
                r = await mon.api.add_static(dev, slot, int(b["tg"]))
            elif act == "delete":
                r = await mon.api.delete_static(dev, slot, int(b["tg"]))
            elif act == "drop_dynamic":
                r = await mon.api.drop_dynamic(dev, slot)
            elif act == "drop_call":
                r = await mon.api.drop_call(dev, slot)
            elif act == "reset":
                r = await mon.api.reset_connection(dev)
            else:
                raise ValueError("Tuntematon toiminto")
            await asyncio.sleep(1)
            mon.devmon.wake.set()
            return web.json_response({"ok": True, "result": r}, dumps=lambda o: json.dumps(o, default=str))
        except BMApiError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=400)

    async def devices_refresh(_):
        mon.api.invalidate("/device/")
        mon.devmon.wake.set()
        return web.json_response({"ok": True})

    async def device_lookup(request):
        call = (request.query.get("call") or "").strip().upper()
        try:
            return web.json_response({"ok": True, "devices": await mon.api.devices_by_call(call)},
                                     dumps=lambda o: json.dumps(o, default=str))
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=400)

    async def test_alert(_):
        mon.watcher.seq += 1
        mon.watcher.alerts.appendleft({"seq": mon.watcher.seq, "time": time.time(), "kind": "testi", "call": "TESTI",
                                       "name": "Testihälytys", "id": 0, "tg": 91, "tg_name": mon.tg_name(91), "via": ""})
        mon.touch()
        return web.json_response({"ok": True})

    async def ping(_):
        return web.json_response({"app": APP_ID, "version": __version__})

    async def shutdown(_):
        log.info("Sammutus käyttöliittymästä")
        asyncio.get_running_loop().call_later(0.3, mon.shutdown_event.set)
        return web.json_response({"ok": True})

    app.router.add_get("/api/ping", ping)
    app.router.add_post("/api/shutdown", shutdown)
    app.router.add_get("/api/station", station)
    app.router.add_get("/api/stats", stats)
    app.router.add_get("/api/stats/bm", stats_bm)
    app.router.add_post("/api/device/action", device_action)
    app.router.add_post("/api/devices/refresh", devices_refresh)
    app.router.add_get("/api/devices/lookup", device_lookup)
    app.router.add_post("/api/alerts/test", test_alert)
    async def aprs_send(request):
        from .messaging import clean_text, split_message, validate_call
        try:
            body = await request.json()
            m = mon.cfg["messaging"]
            if not m.get("login"):
                raise ValueError("Aseta oma APRS-IS-tunnus (Syötteet ja viestit)")
            a = mon.hub.aprs
            if not a.connected:
                raise ValueError("APRS-IS-yhteys ei ole auki")
            if a.verified is False:
                raise ValueError("Passcode ei kelpaa – viestejä ei välitetä")
            to = validate_call(str(body.get("to", "")))
            frm = validate_call(str(body.get("from") or m["login"]))
            if frm.split("-")[0] not in mon.hub.watch_bases():
                raise ValueError("Lähettäjän pitää olla oma kutsusi tai seurattava kutsu")
            text = clean_text(str(body.get("text", "")), bool(m.get("ascii")))
            if not text:
                raise ValueError("Kirjoita viesti")
            parts = split_message(text, 3)
            for part in parts:
                a.send(to, part, source="vastaus", src=frm)
            return web.json_response({"ok": True, "parts": len(parts)})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    app.router.add_post("/api/aprs/send", aprs_send)
    app.router.add_post("/api/test_message", test_message)
    app.router.add_post("/api/feeds/check", check_feeds)
    app.router.add_get("/api/feeds/preview", preview_feed)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/config", get_config)
    app.router.add_post("/api/config", post_config)
    app.router.add_post("/api/clear", clear)
    app.router.add_static("/static", STATIC_DIR)
    return app


def free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Vapaata porttia ei löytynyt")


def running_instance(port: int) -> bool:
    """Onko ohjelma jo käynnissä tässä portissa?"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read().decode()).get("app") == APP_ID
    except Exception:  # noqa: BLE001
        return False


async def run(args):
    cfg_path = Path(args.config) if args.config else config_dir() / "config.json"
    mon = Monitor(cfg_path, demo=args.demo)
    if not cfg_path.exists():
        mon.save_config()
    preferred = args.port or int(mon.cfg.get("port", 8765))
    if not args.demo and running_instance(preferred):
        url = f"http://127.0.0.1:{preferred}/"
        print(f"{APP_NAME} on jo käynnissä: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    port = free_port(preferred)

    runner = web.AppRunner(make_app(mon))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    url = f"http://127.0.0.1:{port}/"
    print(f"{APP_NAME} {__version__} käynnissä: {url}")
    print(f"Asetukset: {cfg_path}")
    print("Lopeta painamalla Ctrl+C tai Asetukset → Sulje ohjelma.")
    log.info("%s %s käynnissä: %s", APP_NAME, __version__, url)
    if not args.no_browser:
        webbrowser.open(url)

    tasks = [asyncio.create_task(mon.broadcast_loop()), asyncio.create_task(mon.load_tg_names()),
             asyncio.create_task(mon.hub.run()), asyncio.create_task(mon.devmon.run()),
             asyncio.create_task(mon.services.run())]
    tasks.append(asyncio.create_task(demo_loop(mon) if args.demo else bm_loop(mon)))
    try:
        await mon.shutdown_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        await runner.cleanup()


def main():
    ap = argparse.ArgumentParser(prog="bmmonitor", description=f"{APP_NAME} – BrandMeister-puheryhmien seuranta")
    ap.add_argument("--port", type=int, default=0, help="paikallinen portti (oletus 8765)")
    ap.add_argument("--no-browser", action="store_true", help="älä avaa selainta automaattisesti")
    ap.add_argument("--config", help="asetustiedoston polku")
    ap.add_argument("--demo", action="store_true", help="keksitty testiliikenne, ei yhteyttä BM:ään")
    ap.add_argument("--diag", action="store_true", help="vianetsintä: näytä mitä BM lähettää (30 s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.diag:
        diagnose()
        return
    # Ilman komentoikkunaa (Windows .exe / macOS .app) tulosteet ja loki menevät tiedostoon.
    if sys.stdout is None or sys.stderr is None:
        logf = config_dir() / "monitor.log"
        try:
            if logf.exists() and logf.stat().st_size > 2_000_000:
                logf.replace(logf.with_suffix(".old.log"))
        except OSError:
            pass
        stream = open(logf, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        stream=sys.stderr)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
