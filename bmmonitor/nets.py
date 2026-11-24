"""Päivän netit: ICS-kalentereista (esim. FinDMR-kierros, W0WC:n BM DMR Nets) ja omasta listasta.

Kalenterit haetaan muutaman tunnin välein ja tallennetaan välimuistiin, joten lista toimii myös hetken
ilman verkkoa. Toistuvat tapahtumat (RRULE) ja aikavyöhykkeet puretaan recurring-ical-events-kirjastolla.
Puheryhmä poimitaan tapahtuman tekstistä ("TG 244", "(31424)").
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import time
from pathlib import Path

from .i18n import tr

log = logging.getLogger("bmmonitor.nets")

FINDMR_ICS = "https://6kw.fi/findmr/?kalenteri=ics"
W0WC_ICS = ("https://calendar.google.com/calendar/ical/"
            "1hcefmud5mr7dm282iof59s8j8%40group.calendar.google.com/public/basic.ics")
DEFAULT_NETS = {
    "enabled": True,
    "remind": True,          # muistutus ennen nettiä, jonka puheryhmää seurataan
    "remind_min": 15,
    "sources": [
        {"name": "FinDMR-kierros (6kw.fi)", "url": FINDMR_ICS, "enabled": True},
        {"name": "BM DMR Nets (W0WC)", "url": W0WC_ICS, "enabled": True},
    ],
    "own": [],               # [{"name", "tg", "day" (0 = ma … 6 = su), "time": "HH:MM", "minutes"}]
}
REFRESH = 6 * 3600
TG_RES = (re.compile(r"\bTG\s*:?\s*(\d{1,7})\b", re.I), re.compile(r"\((\d{2,7})\)"),
          re.compile(r"\btalk\s*group\s*:?\s*(\d{1,7})\b", re.I))


def find_tg(*texts) -> int | None:
    for rx in TG_RES:
        for t in texts:
            m = rx.search(t or "")
            if m:
                return int(m.group(1))
    return None


def normalize_nets(new: dict, old: dict) -> dict:
    out = {**DEFAULT_NETS, **(old or {})}
    for k in ("enabled", "remind"):
        if k in new:
            out[k] = bool(new[k])
    if "remind_min" in new:
        try:
            v = int(new["remind_min"])
        except (TypeError, ValueError):
            raise ValueError(tr("Virheellinen arvo: {x}", x=new["remind_min"])) from None
        if not 1 <= v <= 180:
            raise ValueError(tr("Arvon pitää olla {a}–{b}", a=1, b=180))
        out["remind_min"] = v
    if "sources" in new:
        srcs = []
        for s in new["sources"] or []:
            url = str(s.get("url") or "").strip()
            if not url:
                continue
            if url.startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            if not re.match(r"^https?://\S+$", url):
                raise ValueError(tr("Virheellinen osoite: {x}", x=url))
            srcs.append({"name": str(s.get("name") or "").strip()[:60] or url.split("/")[2],
                         "url": url, "enabled": bool(s.get("enabled", True))})
        out["sources"] = srcs[:10]
    if "own" in new:
        own = []
        for n in new["own"] or []:
            name = str(n.get("name") or "").strip()[:60]
            if not name:
                continue
            tm = str(n.get("time") or "").strip()
            if not re.match(r"^([01]?\d|2[0-3])[:.][0-5]\d$", tm):
                raise ValueError(tr("Virheellinen kellonaika: {x}", x=tm))
            try:
                day = int(n.get("day"))
                minutes = int(n.get("minutes") or 60)
                tg = int(n["tg"]) if str(n.get("tg") or "").strip() else None
            except (TypeError, ValueError):
                raise ValueError(tr("Virheellinen netti: {x}", x=name)) from None
            if not 0 <= day <= 7:
                raise ValueError(tr("Virheellinen netti: {x}", x=name))
            own.append({"name": name, "tg": tg, "day": day, "time": tm.replace(".", ":"),
                        "minutes": max(5, min(minutes, 600))})
        out["own"] = own[:50]
    return out


class NetCalendar:
    def __init__(self, mon, cache_dir: Path):
        self.mon = mon
        self.cache_dir = cache_dir
        self.cals: dict[str, object] = {}          # url → icalendar.Calendar
        self.status: dict[str, dict] = {}          # url → {fetched, error, events}
        self.wake = asyncio.Event()
        self.reminded: set[str] = set()

    @property
    def cfg(self) -> dict:
        return self.mon.cfg["nets"]

    def reconfigure(self):
        self.wake.set()

    def _cache(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.ics"

    def _parse(self, url: str, text: str):
        import icalendar
        cal = icalendar.Calendar.from_ical(text)
        self.cals[url] = cal
        self.status.setdefault(url, {})["events"] = len(cal.walk("VEVENT"))

    async def refresh(self, force: bool = False):
        from .services import fetch_text
        from . import __version__
        ua = f"MonitorForAmateurRadio/{__version__} (+https://github.com/hannulma/monitor-for-amateur-radio)"
        for src in self.cfg.get("sources") or []:
            url = src["url"]
            st = self.status.setdefault(url, {"fetched": None, "error": None, "events": 0})
            if not src.get("enabled", True):
                continue
            cache = self._cache(url)
            if url not in self.cals and cache.exists():           # käynnistyksessä välimuistista heti
                try:
                    self._parse(url, cache.read_text(encoding="utf-8"))
                    st["fetched"] = cache.stat().st_mtime
                except Exception as e:  # noqa: BLE001
                    log.debug("Välimuistin kalenteri %s: %s", url, e)
            if not force and st.get("fetched") and time.time() - st["fetched"] < REFRESH and not st.get("error"):
                continue
            try:
                text = await fetch_text(url, timeout=30, headers={"User-Agent": ua})
                self._parse(url, text)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding="utf-8")
                st.update(fetched=time.time(), error=None)
            except Exception as e:  # noqa: BLE001
                st["error"] = str(e) or e.__class__.__name__
                log.warning("Net-kalenteri %s: %s", url, st["error"])
        self.mon.touch()

    async def run(self):
        last = 0.0
        while True:
            if self.cfg.get("enabled"):
                if time.time() - last > REFRESH or self.wake.is_set():
                    self.wake.clear()
                    await self.refresh(force=last > 0)
                    last = time.time()
                try:
                    self.check_reminders()
                except Exception:  # noqa: BLE001
                    log.exception("Net-muistutus")
            try:
                await asyncio.wait_for(self.wake.wait(), 60)
            except asyncio.TimeoutError:
                pass

    # ---------------- tapahtumat ----------------
    def occurrences(self, start: float, end: float) -> list[dict]:
        import recurring_ical_events
        a = dt.datetime.fromtimestamp(start, dt.timezone.utc)
        b = dt.datetime.fromtimestamp(end, dt.timezone.utc)
        out = []
        for src in self.cfg.get("sources") or []:
            cal = self.cals.get(src["url"])
            if cal is None or not src.get("enabled", True):
                continue
            try:
                evs = recurring_ical_events.of(cal).between(a, b)
            except Exception as e:  # noqa: BLE001
                self.status.setdefault(src["url"], {})["error"] = str(e)
                continue
            for ev in evs:
                s, e_ = ev.get("DTSTART"), ev.get("DTEND")
                if s is None or not isinstance(s.dt, dt.datetime):
                    continue                                     # koko päivän tapahtumat eivät ole nettejä
                st = s.dt if s.dt.tzinfo else s.dt.astimezone()
                en = (e_.dt if e_ is not None and isinstance(e_.dt, dt.datetime) else st + dt.timedelta(hours=1))
                en = en if en.tzinfo else en.astimezone()
                summ, loc, desc = str(ev.get("SUMMARY") or ""), str(ev.get("LOCATION") or ""), str(ev.get("DESCRIPTION") or "")
                out.append({"name": summ.strip() or "?", "start": st.timestamp(), "end": en.timestamp(),
                            "tg": find_tg(summ, loc, desc), "where": loc.strip()[:120], "info": desc.strip()[:400],
                            "url": str(ev.get("URL") or ""), "source": src["name"], "uid": str(ev.get("UID") or summ)})
        out += self._own(start, end)
        out.sort(key=lambda n: (n["start"], n["name"]))
        return out

    def _own(self, start: float, end: float) -> list[dict]:
        res = []
        day0 = dt.datetime.fromtimestamp(start).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        d = day0
        while d.timestamp() < end:
            for n in self.cfg.get("own") or []:
                if n["day"] != 7 and n["day"] != d.weekday():    # 7 = joka päivä
                    continue
                h, m = map(int, n["time"].split(":"))
                st = d.replace(hour=h, minute=m)
                st = st.astimezone()                             # kesä-/talviaika oikein
                en = st + dt.timedelta(minutes=n.get("minutes") or 60)
                if en.timestamp() > start and st.timestamp() < end:
                    res.append({"name": n["name"], "start": st.timestamp(), "end": en.timestamp(), "tg": n.get("tg"),
                                "where": "", "info": "", "url": "", "source": tr("Oma lista"),
                                "uid": f"own:{n['name']}:{n['day']}:{n['time']}"})
            d = (d + dt.timedelta(days=1, hours=2)).replace(hour=0)
        return res

    def check_reminders(self, now: float | None = None):
        cfg = self.cfg
        if not cfg.get("remind"):
            return []
        now = now or time.time()
        lead = int(cfg.get("remind_min", 15)) * 60
        fired = []
        for n in self.occurrences(now, now + lead + 60):
            key = f"{n['uid']}@{int(n['start'])}"
            if key in self.reminded or not (0 <= n["start"] - now <= lead):
                continue
            if n["tg"] is None or not self.mon.matcher.match(n["tg"]):
                continue                                         # vain seurattujen puheryhmien netit
            self.reminded.add(key)
            fired.append(n["name"])
            hhmm = dt.datetime.fromtimestamp(n["start"]).strftime("%H:%M")
            w = self.mon.watcher
            w.seq += 1
            w.alerts.appendleft({"seq": w.seq, "time": now, "kind": "palvelu", "service": "nets",
                                 "service_title": tr("Netit"), "title": tr("{name} alkaa klo {t}", name=n["name"], t=hhmm),
                                 "body": f"TG {n['tg']} · {n['source']}", "level": "info", "call": "", "tg": n["tg"], "via": ""})
            self.mon.touch()
        return fired

    def snapshot(self, days: int = 2) -> dict:
        start = dt.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=days)
        return {"nets": self.occurrences(start.timestamp(), end.timestamp()),
                "sources": [{"name": s["name"], "url": s["url"], "enabled": s.get("enabled", True),
                             **{k: (self.status.get(s["url"]) or {}).get(k) for k in ("fetched", "error", "events")}}
                            for s in self.cfg.get("sources") or []]}
