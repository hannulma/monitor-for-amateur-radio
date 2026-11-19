"""Kaverihälytykset, asemarekisteri, laitevalvonta, tilastot ja SMS-komennot."""
from __future__ import annotations

import asyncio
import csv
import logging
import re
import time
from collections import deque
from pathlib import Path

from .bmapi import BMApi, BMApiError, default_slot, parse_bm_time

log = logging.getLogger("bmmonitor.features")

DEFAULT_WATCH = {
    "list": [],            # kutsut tai DMR-ID:t, esim. ["OH2ABC", "2440123"]
    "quiet_min": 30,       # hälytä vasta kun asema on ollut hiljaa näin kauan
    "scope": "all",        # "all" = koko verkko, "matched" = vain seuratut puheryhmät
    "desktop": True,
    "sound": True,
    "sms": False,
}
DEFAULT_DEVICES = {
    "list": [],            # [{"id": 244207, "note": ""}]
    "offline_min": 15,
    "poll_sec": 120,
    "sms": False,
}
CALL_OR_ID = re.compile(r"^([A-Z0-9]{3,8}|\d{5,9})$")


def _hm(ts) -> str:
    return time.strftime("%H:%M", time.localtime(float(ts))) if ts else "?"


def _ago(ts) -> str:
    s = max(0, int(time.time() - float(ts)))
    if s < 90:
        return f"{s}s sitten"
    if s < 5400:
        return f"{s // 60}min sitten"
    if s < 172800:
        return f"{s // 3600}h sitten"
    return f"{s // 86400}pv sitten"


# --------------------------------------------------------------------------
# Kaikkien kuultujen asemien rekisteri (koko verkko)
# --------------------------------------------------------------------------
class Heard:
    MAX = 150_000

    def __init__(self):
        self.by_id: dict[int, dict] = {}
        self.by_call: dict[str, int] = {}
        self.last_by_tg: dict[int, dict] = {}
        self.active_by_tg: dict[int, dict] = {}

    def update(self, p: dict, tg_name) -> dict | None:
        sid = p.get("SourceID")
        if not isinstance(sid, int) or sid <= 0:
            return None
        ct = [str(x).lower() for x in (p.get("CallTypes") or [])]
        group = "group" in ct or not ct
        e = self.by_id.get(sid)
        if e is None:
            e = self.by_id[sid] = {"id": sid, "call": "", "name": "", "first": time.time()}
            if len(self.by_id) > self.MAX:
                for k in sorted(self.by_id, key=lambda k: self.by_id[k].get("start", 0))[: self.MAX // 10]:
                    self.by_id.pop(k, None)
        call = (p.get("SourceCall") or "").upper()
        if call:
            e["call"] = call
            self.by_call[call] = sid
        if p.get("SourceName"):
            e["name"] = p["SourceName"]
        dst = p.get("DestinationID")
        e.update(start=p.get("Start") or time.time(), tg=dst if group else None,
                 to_call=None if group else (p.get("DestinationCall") or str(dst)),
                 tg_name=tg_name(dst) if group else "", via=p.get("LinkCall") or p.get("LinkName") or "",
                 ctx=p.get("ContextID"), group=group)
        stop = p.get("Stop") or 0
        if stop:
            e["stop"] = stop
            e["dur"] = max(0, float(stop) - float(e["start"]))
            e["active"] = False
        else:
            e["active"] = p.get("Event") != "Session-Stop"
        if group and isinstance(dst, int):
            snap = {k: e.get(k) for k in ("id", "call", "name", "start", "stop", "dur", "via", "tg", "tg_name")}
            if e["active"]:
                self.active_by_tg[dst] = snap
            else:
                self.last_by_tg[dst] = snap
                a = self.active_by_tg.get(dst)
                if a and a["id"] == sid:
                    self.active_by_tg.pop(dst, None)
        return e

    def lookup(self, call: str = "", dmr_id=None) -> list[dict]:
        out = []
        if dmr_id:
            try:
                e = self.by_id.get(int(dmr_id))
                if e:
                    out.append(e)
            except ValueError:
                pass
        if call:
            call = call.upper().split("-")[0]
            out += [e for e in self.by_id.values() if e.get("call") == call and e not in out]
        return sorted(out, key=lambda e: -float(e.get("start") or 0))


# --------------------------------------------------------------------------
# Kaverihälytykset
# --------------------------------------------------------------------------
class Watcher:
    def __init__(self, mon):
        self.mon = mon
        self.last_activity: dict[str, float] = {}
        self.alerts: deque[dict] = deque(maxlen=200)
        self.last_call_start: dict[str, float] = {}
        self.seq = 0

    @property
    def cfg(self):
        return self.mon.cfg["watch"]

    def key_for(self, p: dict) -> str | None:
        lst = self.cfg.get("list") or []
        if not lst:
            return None
        call = (p.get("SourceCall") or "").upper()
        sid = str(p.get("SourceID") or "")
        for w in lst:
            if w == call or w == sid:
                return w
        return None

    def on_event(self, p: dict, history: bool):
        key = self.key_for(p)
        if not key:
            return
        now = time.time()
        prev = self.last_activity.get(key, 0)
        self.last_activity[key] = now if not history else max(prev, float(p.get("Stop") or p.get("Start") or 0))
        # Yksi puhelu näkyy BM:ssä usealla istunnolla (yksi per laite), osa ilman Session-Startia,
        # ja vanhoja Session-Stoppeja voi saapua myöhässä. Tunnistetaan puhelu lähettäjän ja
        # alkuajan perusteella ja hälytetään sen ensimmäisestä tuoreesta tapahtumasta.
        start = float(p.get("Start") or now)
        if history or now - start > 120:
            return
        last_start = self.last_call_start.get(key)
        self.last_call_start[key] = max(start, last_start or 0)
        if last_start is not None and abs(start - last_start) < 15:
            return
        if self.cfg.get("scope") == "matched" and not self.mon.matcher.match(p.get("DestinationID")):
            return
        if prev and now - prev < float(self.cfg.get("quiet_min", 30)) * 60:
            return
        ct = [str(x).lower() for x in (p.get("CallTypes") or [])]
        dst = p.get("DestinationID")
        group = "group" in ct or not ct
        self.seq += 1
        a = {"seq": self.seq, "time": now, "key": key, "call": p.get("SourceCall") or str(p.get("SourceID")),
             "name": p.get("SourceName") or "", "id": p.get("SourceID"),
             "tg": dst if group else None, "tg_name": self.mon.tg_name(dst) if group else "",
             "to": None if group else (p.get("DestinationCall") or str(dst)),
             "via": p.get("LinkCall") or p.get("LinkName") or "", "kind": "kaveri"}
        self.alerts.appendleft(a)
        log.info("Kaverihälytys: %s TG %s", a["call"], a["tg"])
        if self.cfg.get("sms"):
            where = f"TG{a['tg']} {a['tg_name']}".strip() if group else f"-> {a['to']}"
            self.mon.hub.announce("", f"{a['call']} {a['name']} aktiivinen {where} ({a['via']})".replace("  ", " "),
                                  source="kaveri")
        self.mon.touch()

    def device_alert(self, dev: dict, online: bool):
        self.seq += 1
        a = {"seq": self.seq, "time": time.time(), "kind": "laite_ok" if online else "laite_pois",
             "call": dev.get("callsign") or str(dev.get("id")), "id": dev.get("id"), "name": dev.get("note") or "",
             "tg": None, "tg_name": "", "via": dev.get("city") or ""}
        self.alerts.appendleft(a)
        if self.mon.cfg["devices"].get("sms"):
            txt = f"Laite {a['call']} ({a['id']}) " + ("palasi verkkoon" if online else "POIS VERKOSTA")
            self.mon.hub.announce("", txt, source="laite")
        self.mon.touch()


# --------------------------------------------------------------------------
# Laitevalvonta
# --------------------------------------------------------------------------
class DeviceMonitor:
    def __init__(self, mon, api: BMApi):
        self.mon = mon
        self.api = api
        self.state: dict[int, dict] = {}
        self.activity: dict[int, dict] = {}    # ContextID -> viimeisin puhelu laitteen kautta
        self.wake = asyncio.Event()

    @property
    def cfg(self):
        return self.mon.cfg["devices"]

    def ids(self) -> set[int]:
        return {int(d["id"]) for d in self.cfg.get("list", [])}

    def on_event(self, p: dict):
        ctx = p.get("ContextID")
        if isinstance(ctx, int) and ctx in self._ids_cache:
            self.activity[ctx] = {"call": p.get("SourceCall") or str(p.get("SourceID")), "name": p.get("SourceName") or "",
                                  "tg": p.get("DestinationID"), "start": p.get("Start"), "stop": p.get("Stop") or 0}
            self.mon.touch()

    _ids_cache: set = set()

    async def run(self):
        while True:
            self._ids_cache = self.ids()
            try:
                await self.poll_all()
            except Exception as e:  # noqa: BLE001
                log.warning("Laitevalvonta: %s", e)
            try:
                await asyncio.wait_for(self.wake.wait(), max(60, int(self.cfg.get("poll_sec", 120))))
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def poll_all(self):
        notes = {int(d["id"]): d.get("note", "") for d in self.cfg.get("list", [])}
        for dev_id in list(notes):
            st = self.state.setdefault(dev_id, {"id": dev_id, "online": None})
            st["note"] = notes[dev_id]
            try:
                dev = await self.api.device(dev_id, ttl=20)
                prof = await self.api.device_profile(dev_id, ttl=20)
            except BMApiError as e:
                st.update(error=str(e), checked=time.time())
                self.mon.touch()
                continue
            except Exception as e:  # noqa: BLE001
                st.update(error=str(e) or e.__class__.__name__, checked=time.time())
                self.mon.touch()
                continue
            last = parse_bm_time(dev.get("last_seen")) if isinstance(dev, dict) else None
            online = bool(last and time.time() - last < float(self.cfg.get("offline_min", 15)) * 60)
            was = st.get("online")
            st.update(error=None, checked=time.time(), last_seen=last, online=online,
                      callsign=dev.get("callsign"), hardware=dev.get("hardware"), firmware=dev.get("firmware"),
                      linkname=dev.get("linkname"), city=dev.get("city"), tx=dev.get("tx"), rx=dev.get("rx"),
                      colorcode=dev.get("colorcode"), statusText=dev.get("statusText"),
                      master=dev.get("lastKnownMaster"), lat=dev.get("lat"), lng=dev.get("lng"),
                      default_slot=default_slot(dev_id),
                      static=_subs((prof or {}).get("staticSubscriptions")),
                      dynamic=_subs((prof or {}).get("dynamicSubscriptions")),
                      timed=_subs((prof or {}).get("timedSubscriptions")),
                      blocked=_subs((prof or {}).get("blockedGroups")))
            if was is not None and was != online:
                self.mon.watcher.device_alert({**st, "id": dev_id}, online)
            self.mon.touch()
        for gone in set(self.state) - set(notes):
            self.state.pop(gone, None)

    def snapshot(self) -> list[dict]:
        out = []
        for d in self.cfg.get("list", []):
            st = dict(self.state.get(int(d["id"]), {"id": int(d["id"])}))
            st["activity"] = self.activity.get(int(d["id"]))
            out.append(st)
        return out


def _subs(v) -> list[dict]:
    """BM palauttaa tilaukset listana tai objektina; normalisoidaan [{'tg','slot'}]."""
    items = []
    if isinstance(v, dict):
        v = list(v.values())
    for x in v or []:
        if isinstance(x, dict):
            tg = x.get("talkgroup") or x.get("group") or x.get("id")
            if tg is None:
                continue
            items.append({"tg": int(tg), "slot": int(x.get("slot") or 0)})
        elif isinstance(x, (int, str)) and str(x).isdigit():
            items.append({"tg": int(x), "slot": 0})
    return sorted(items, key=lambda i: (i["slot"], i["tg"]))


# --------------------------------------------------------------------------
# Tilastot
# --------------------------------------------------------------------------
class Stats:
    KEEP_DAYS = 14

    def __init__(self, mon):
        self.mon = mon
        self.calls: deque[dict] = deque(maxlen=200_000)
        self.bm_cache: dict[str, tuple[float, list]] = {}

    def load_csv(self, path: Path):
        if not path.exists():
            return
        cutoff = time.time() - self.KEEP_DAYS * 86400
        n = 0
        try:
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        ts = time.mktime(time.strptime(row["aika"], "%Y-%m-%d %H:%M:%S"))
                    except (KeyError, ValueError):
                        continue
                    if ts < cutoff:
                        continue
                    self.calls.append({"start": ts, "tg": int(row.get("tg") or 0), "call": row.get("kutsu") or "",
                                       "name": row.get("nimi") or "", "id": row.get("dmr_id") or "",
                                       "duration": float(row.get("kesto_s") or 0)})
                    n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("CSV-historian luku: %s", e)
        log.info("Tilastoihin ladattu %d puhelua lokista", n)

    def add(self, info: dict):
        self.calls.append({"start": float(info["start"]), "tg": info["tg"], "call": info.get("call") or "",
                           "name": info.get("name") or "", "id": info.get("id"),
                           "duration": float(info.get("duration") or 0)})

    def compute(self, hours: int = 24, tg: int | None = None) -> dict:
        now = time.time()
        since = now - hours * 3600
        rows = [c for c in self.calls if c["start"] >= since and (tg is None or c["tg"] == tg)]
        # tuntisarakkeet paikallisessa ajassa
        bucket = 3600 if hours <= 48 else 86400
        lt = time.localtime(now)
        if bucket == 3600:
            end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1)) + 3600
        else:
            end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) + 86400
        nb = int(round((end - since) / bucket))
        bins = [{"t": end - (nb - i) * bucket, "calls": 0, "airtime": 0.0} for i in range(nb)]
        talkers: dict[str, dict] = {}
        groups: dict[int, dict] = {}
        for c in rows:
            i = int((c["start"] - (end - nb * bucket)) // bucket)
            if 0 <= i < nb:
                bins[i]["calls"] += 1
                bins[i]["airtime"] += c["duration"]
            k = c["call"] or str(c["id"])
            t = talkers.setdefault(k, {"call": c["call"], "name": c["name"], "id": c["id"], "calls": 0, "airtime": 0.0})
            t["calls"] += 1
            t["airtime"] += c["duration"]
            if c["name"]:
                t["name"] = c["name"]
            g = groups.setdefault(c["tg"], {"tg": c["tg"], "name": self.mon.tg_name(c["tg"]), "calls": 0, "airtime": 0.0,
                                            "users": set()})
            g["calls"] += 1
            g["airtime"] += c["duration"]
            g["users"].add(k)
        for g in groups.values():
            g["users"] = len(g["users"])
        return {
            "hours": hours, "tg": tg, "bucket": bucket, "total_calls": len(rows),
            "total_airtime": sum(c["duration"] for c in rows),
            "unique": len(talkers), "bins": bins,
            "talkers": sorted(talkers.values(), key=lambda t: (-t["airtime"], -t["calls"]))[:15],
            "groups": sorted(groups.values(), key=lambda g: (-g["airtime"], -g["calls"]))[:15],
            "oldest": min((c["start"] for c in self.calls), default=None),
        }


def bm_user_history(talkgroups: list[int], timeout: float = 15) -> list[dict]:
    """Brandmeisterin oma kysely: ryhmässä kuullut käyttäjät, viimeisin aika ja puhekerrat.
    Synkroninen – ajetaan säikeessä."""
    import socketio
    sio = socketio.Client(logger=False, engineio_logger=False)
    box: dict = {}

    @sio.event
    def connect():
        sio.emit("lastUserList", {"talkgroups": [int(t) for t in talkgroups]})

    @sio.on("userHistoryQuery")
    def got(data):
        box["data"] = data

    try:
        sio.connect("https://api.brandmeister.network", socketio_path="/lh", transports=["websocket"],
                    wait_timeout=timeout)
        t0 = time.time()
        while "data" not in box and time.time() - t0 < timeout:
            time.sleep(0.2)
    finally:
        try:
            sio.disconnect()
        except Exception:  # noqa: BLE001
            pass
    out = []
    for x in box.get("data") or []:
        try:
            out.append({"id": int(x.get("_id")), "call": x.get("call") or "", "name": x.get("name") or "",
                        "last": float(x.get("last") or 0), "count": int(x.get("count") or 0)})
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------
# SMS-komennot radiosta
# --------------------------------------------------------------------------
class Commands:
    RATE = 10          # komentoa / minuutti / lähettäjä

    def __init__(self, mon):
        self.mon = mon
        self.recent: dict[str, deque] = {}

    @property
    def cfg(self):
        return self.mon.cfg["messaging"]

    def allowed(self, src: str) -> bool:
        if self.cfg.get("commands_all"):
            return True
        base = src.upper().split("-")[0]
        allow = {r.split("-")[0] for r in self.cfg.get("recipients", [])}
        allow |= {a.upper().split("-")[0] for a in self.cfg.get("commands_allow", [])}
        login = (self.cfg.get("login") or "").upper().split("-")[0]
        if login:
            allow.add(login)
        return base in allow

    def handle(self, src: str, text: str) -> str | None:
        if not self.cfg.get("commands_enabled", True):
            return None
        if not self.allowed(src):
            log.info("Komento hylätty (ei sallittu lähettäjä): %s %r", src, text)
            return None
        q = self.recent.setdefault(src, deque(maxlen=self.RATE))
        if len(q) == self.RATE and time.time() - q[0] < 60:
            return None
        q.append(time.time())
        return self.answer(text.strip())

    def feed_cmds(self) -> dict[str, dict]:
        out = {}
        for f in self.cfg.get("feeds", []):
            c = (f.get("cmd") or "").strip().upper()
            if c:
                out[c] = f
        return out

    def answer(self, text: str) -> str:
        t = re.sub(r"\s+", " ", text.upper()).strip(" ?!.")
        feeds = self.feed_cmds()
        if t in ("", "APUA", "HELP", "OHJE", "KOMENNOT"):
            extra = " ".join(sorted(feeds))
            return f"Komennot: TG<nro>, KUKA <kutsu>, NYT{(', ' + extra) if extra else ''}"
        m = re.fullmatch(r"TG ?(\d{1,9})", t)
        if m:
            return self.tg_info(int(m.group(1)))
        if t in ("NYT", "AKTIIVISET", "NOW"):
            return self.now_info()
        m = re.fullmatch(r"(?:KUKA|WHO|MISSA|MISSÄ) ([A-Z0-9]{3,8}|\d{5,9})", t)
        if m:
            return self.who_info(m.group(1))
        if t in feeds:
            f = feeds[t]
            st = self.mon.hub.feeds.status.get(f["url"], {})
            latest = st.get("latest")
            return f"{f.get('name') or t}: {latest}" if latest else f"{t}: ei uutisia (syötettä ei vielä luettu)"
        if CALL_OR_ID.fullmatch(t) and not t.isdigit() and re.search(r"\d", t):
            return self.who_info(t)
        return "Tuntematon komento. Laheta APUA"

    def tg_info(self, tg: int) -> str:
        h = self.mon.heard
        name = self.mon.tg_name(tg)
        label = f"TG{tg}{(' ' + name) if name else ''}"
        a = h.active_by_tg.get(tg)
        if a and time.time() - float(a.get("start") or 0) < 300:
            return f"{label}: NYT {a['call'] or a['id']} {a.get('name') or ''} ({a.get('via') or ''})".replace("  ", " ")
        last = h.last_by_tg.get(tg)
        s = self.mon.tg_stats.get(tg)
        if s and s.get("last") and (not last or float(s["last"]["start"]) >= float(last.get("start") or 0)):
            last = s["last"]
        if not last:
            return f"{label}: ei liikennettä ohjelman käynnistyksen jälkeen"
        dur = last.get("dur") if last.get("dur") is not None else last.get("duration")
        return (f"{label}: {last.get('call') or last.get('id')} {last.get('name') or ''} {_hm(last.get('start'))} "
                f"{int(dur or 0)}s ({last.get('via') or ''})").replace("  ", " ")

    def now_info(self) -> str:
        act = [(tg, s["active"]) for tg, s in self.mon.tg_stats.items() if s.get("active")]
        if not act:
            return "Ei lahetyksia seuratuissa ryhmissa"
        return "NYT: " + ", ".join(f"TG{tg} {a.get('call') or a.get('id')}" for tg, a in act[:5])

    def who_info(self, q: str) -> str:
        res = self.mon.heard.lookup(call="" if q.isdigit() else q, dmr_id=q if q.isdigit() else None)
        if not res:
            return f"{q}: ei kuultu ohjelman kaynnistyksen jalkeen"
        e = res[0]
        where = f"TG{e['tg']}" if e.get("group") else f"-> {e.get('to_call')}"
        state = "NYT" if e.get("active") else f"{_hm(e.get('start'))} ({_ago(e.get('start'))})"
        return f"{e.get('call') or e['id']} {e.get('name') or ''} {state} {where} via {e.get('via') or '?'}".replace("  ", " ")
