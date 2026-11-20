"""Check-in-netin kirjaus.

Ylläpitäjä aloittaa kierroksen (puheryhmä, kierrosnumero, päivämäärä, operaattori). Ohjelma kerää
puheryhmässä avanneet asemat saapumisjärjestykseen (järjestys ei koskaan muutu) ja ylläpitäjä
kirjaa ne, jotka hän kuuli. Lokinumero annetaan kirjausjärjestyksessä.

Lokin sarakkeet vastaavat FinDMR-kierrostaulukkoa:
NRO, kutsu, RPT/HOTSPOT, KUNTA, Lisätietoja, Oper, BM-laite (laitteen kutsu ja paikkakunta).

Rakenne on valmis useammalle ylläpitäjälle: jokaisella kirjauksella on oma tunniste, aikaleima ja
kirjaaja, ja lokinumero annetaan yhdessä paikassa (myöhemmin Google-taulukon skripti).
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
import uuid

from .i18n import tr

log = logging.getLogger("bmmonitor.checkin")

NETS = [244, 248, 91]                    # oletusvalinnat käyttöliittymässä
COLUMNS = ["NRO", "Kutsu", "RPT/HOTSPOT", "KUNTA", "Lisätietoja", "Oper", "BM-laite"]
EDITABLE = ("via", "kunta", "info")
MAX_STATIONS = 1000
_LOCATOR = re.compile(r",?\s*\b[A-R]{2}\d{2}([A-X]{2})?\b", re.I)


def clean_city(city: str) -> str:
    """'Jarvenpaa, KP20NK' -> 'Jarvenpaa'."""
    return _LOCATOR.sub("", city or "").strip(" ,")


def is_repeater_id(dev_id) -> bool:
    try:
        return len(str(int(dev_id))) == 6
    except (TypeError, ValueError):
        return False


def country_prefix(tg: int) -> str | None:
    """Maakohtainen puheryhmä (esim. 244, 248) -> maakoodi; muuten None."""
    s = str(tg)
    return s if len(s) == 3 and s[0] in "234567" else None


class CheckIn:
    def __init__(self, mon, api):
        self.mon = mon
        self.api = api
        self.path = mon.cfg_path.parent / "checkin.json"
        self.archive_dir = mon.cfg_path.parent / "checkin"
        self.devices: dict[int, dict] = {}       # laite-ID -> {"call", "city"} (tai {} jos ei löytynyt)
        self.queue: asyncio.Queue | None = None
        self._sessions: set = set()
        self.s = self._empty()
        self._load()

    # ---------------- tila ----------------
    def _empty(self) -> dict:
        return {"active": False, "tg": None, "round": None, "date": "", "operator": "", "started": None,
                "ended": None, "stations": [], "log": [], "copied_upto": 0}

    def _load(self):
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, dict) and "stations" in data:
                self.s.update({k: data.get(k, v) for k, v in self._empty().items()})
                for st in self.s["stations"]:
                    st["on_air"] = False
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("checkin.json: %s", e)

    def save(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.s, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("Check-in-tilan tallennus epäonnistui: %s", e)

    def _changed(self):
        self.save()
        self.mon.touch()

    def snapshot(self) -> dict:
        s = self.s
        logged = {e["call"]: e for e in s["log"]}
        rpts = {e["dev"] for e in s["log"] if e.get("dev") and e.get("repeater")}
        return {**{k: s[k] for k in ("active", "tg", "round", "date", "operator", "started", "ended", "copied_upto")},
                "tg_name": self.mon.tg_name(s["tg"]) if s["tg"] else "",
                "nets": NETS,
                "stations": [{**st, "nro": logged[st["call"]]["nro"] if st["call"] in logged else None,
                              "by": logged[st["call"]]["oper"] if st["call"] in logged else None}
                             for st in s["stations"]],
                "log": s["log"], "unique_repeaters": len(rpts), "archive": str(self.archive_dir)}

    # ---------------- kierros ----------------
    def start(self, tg, round_no=None, date: str = "", operator: str = "") -> dict:
        try:
            tg = int(str(tg).strip())
            if tg <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(tr("Virheellinen puheryhmä: {p} (sallitut: numerot, * ja ?)", p=repr(tg))) from None
        if self.s["active"]:
            self.stop()
        prev = self.s.get("round")
        if round_no in (None, ""):
            round_no = (int(prev) + 1) if str(prev or "").isdigit() else 1
        self.s = self._empty()
        self.s.update(active=True, tg=tg, round=str(round_no).strip(), date=(date or time.strftime("%d.%m.%Y")).strip(),
                      operator=(operator or "").strip().upper(), started=time.time())
        self._sessions.clear()
        cfg = self.mon.cfg.setdefault("checkin", {})
        cfg.update(tg=tg, operator=self.s["operator"])
        self.mon.save_config()
        log.info("Check-in-kierros %s alkoi (TG %s)", self.s["round"], tg)
        self._changed()
        return self.snapshot()

    def stop(self) -> dict:
        if not self.s["active"]:
            return self.snapshot()
        self.s["active"] = False
        self.s["ended"] = time.time()
        for st in self.s["stations"]:
            st["on_air"] = False
        self._archive()
        self._changed()
        return self.snapshot()

    def _archive(self):
        try:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            day = time.strftime("%Y-%m-%d", time.localtime(self.s["started"] or time.time()))
            name = f"{day}_TG{self.s['tg']}_kierros{re.sub(r'[^0-9A-Za-z]', '', str(self.s['round'])) or 'x'}.csv"
            (self.archive_dir / name).write_text(self.export("csv"), encoding="utf-8-sig")
            log.info("Kierroksen loki tallennettu: %s", self.archive_dir / name)
        except OSError as e:
            log.warning("Arkistointi epäonnistui: %s", e)

    # ---------------- tapahtumat ----------------
    def on_event(self, p: dict, history: bool = False):
        s = self.s
        if not s["active"] or p.get("DestinationID") != s["tg"]:
            return
        start = float(p.get("Start") or 0) or time.time()
        if start < (s["started"] or 0) - 5:
            return                                           # ennen kierroksen alkua
        call = (p.get("SourceCall") or "").strip().upper() or str(p.get("SourceID") or "")
        if not call:
            return
        by_call = {st["call"]: st for st in s["stations"]}
        st = by_call.get(call)
        stop = float(p.get("Stop") or 0)
        ended = bool(stop) or p.get("Event") == "Session-Stop"
        sid = p.get("SessionID") or f"{p.get('SourceID')}-{int(start)}"
        if st is None:
            if len(s["stations"]) >= MAX_STATIONS:
                return
            st = {"idx": len(s["stations"]) + 1, "call": call, "name": "", "id": p.get("SourceID"),
                  "first": start, "last": start, "keys": 0, "on_air": False, "overlap": 0,
                  "dev_id": None, "link": "", "via": "", "kunta": "", "dev": "", "repeater": False}
            s["stations"].append(st)
        st["name"] = (p.get("SourceName") or st["name"] or "").strip()
        st["last"] = max(st["last"], stop or start)
        dev_id = p.get("ContextID")
        if dev_id and dev_id != st["dev_id"]:
            st["dev_id"] = dev_id
            st["link"] = p.get("LinkCall") or p.get("LinkName") or ""
            st["repeater"] = is_repeater_id(dev_id)
            self._fill_device(st)
            self._want_device(dev_id)
        if sid not in self._sessions:
            self._sessions.add(sid)
            st["keys"] += 1
            others = [o for o in s["stations"] if o is not st and o["on_air"]]
            if others and not ended:
                st["overlap"] += 1                        # avasi toisen lähetyksen päälle
        st["on_air"] = not ended
        self.mon.touch()
        if not history:
            self.save()

    def _fill_device(self, st: dict):
        """RPT/HOTSPOT, KUNTA ja BM-laite laitetiedoista (vain jos ylläpitäjä ei ole muuttanut niitä)."""
        dev = self.devices.get(st["dev_id"]) or {}
        dcall = (dev.get("call") or st.get("link") or "").strip()
        city = clean_city(dev.get("city", ""))
        cp = country_prefix(self.s["tg"] or 0)
        foreign = bool(cp and st.get("id") and not str(st["id"]).startswith(cp))
        via = "Ulkomaat" if foreign else (f"{dcall} {city}".strip() if st["repeater"] else "Hotspot")
        st["dev"] = ", ".join(x for x in (dcall, city) if x)
        if not st.get("edited_via"):
            st["via"] = via
        if not st.get("edited_kunta"):
            st["kunta"] = city
        for e in self.s["log"]:
            if e["call"] == st["call"]:
                e["dev"] = st["dev"]
                if not e.get("edited_via"):
                    e["via"] = st["via"]
                if not e.get("edited_kunta"):
                    e["kunta"] = st["kunta"]

    def _want_device(self, dev_id):
        if dev_id in self.devices or self.queue is None:
            return
        self.devices[dev_id] = {}              # merkitään haetuksi
        self.queue.put_nowait(dev_id)

    async def run(self):
        """Hakee laitetiedot (kutsu, paikkakunta) BrandMeisterista taustalla."""
        self.queue = asyncio.Queue()
        for st in self.s["stations"]:
            if st.get("dev_id"):
                self._want_device(st["dev_id"])
        while True:
            dev_id = await self.queue.get()
            try:
                d = await self.api.device(int(dev_id), ttl=6 * 3600) or {}
                self.devices[dev_id] = {"call": (d.get("callsign") or "").strip(), "city": d.get("city") or ""}
            except Exception as e:  # noqa: BLE001  (hotspotit puuttuvat usein rekisteristä)
                log.debug("Laite %s: %s", dev_id, e)
            for st in self.s["stations"]:
                if st.get("dev_id") == dev_id:
                    self._fill_device(st)
            self._changed()
            await asyncio.sleep(0.3)

    # ---------------- kirjaus ----------------
    def log_station(self, call: str, oper: str | None = None) -> dict:
        call = (call or "").strip().upper()
        st = next((x for x in self.s["stations"] if x["call"] == call), None)
        if st is None:
            raise ValueError(tr("Asemaa {c} ei ole kuultu tällä kierroksella", c=call))
        e = next((x for x in self.s["log"] if x["call"] == call), None)
        if e is None:
            nro = max((x["nro"] for x in self.s["log"]), default=0) + 1
            e = {"uid": uuid.uuid4().hex[:12], "nro": nro, "call": call, "name": st["name"], "via": st["via"],
                 "kunta": st["kunta"], "info": "", "oper": (oper or self.s["operator"] or "").upper(),
                 "dev": st["dev"], "repeater": st["repeater"], "ts": time.time()}
            self.s["log"].append(e)
            self._changed()
        return e

    def unlog(self, call: str):
        call = (call or "").strip().upper()
        e = next((x for x in self.s["log"] if x["call"] == call), None)
        if e is None:
            return
        self.s["log"].remove(e)
        # numerot pysyvät aukottomina; poistettua seuraavat rivit kopioidaan taulukkoon uudelleen
        for x in self.s["log"]:
            if x["nro"] > e["nro"]:
                x["nro"] -= 1
        self.s["copied_upto"] = min(self.s["copied_upto"], e["nro"] - 1)
        self._changed()

    def edit(self, call: str, field: str, value: str):
        if field not in EDITABLE:
            raise ValueError(tr("Virheellinen parametri"))
        call = (call or "").strip().upper()
        e = next((x for x in self.s["log"] if x["call"] == call), None)
        if e is None:
            raise ValueError(tr("Asemaa {c} ei ole kirjattu", c=call))
        e[field] = str(value or "")[:200]
        e[f"edited_{field}"] = True
        self._changed()

    def mark_copied(self, upto: int):
        self.s["copied_upto"] = max(0, min(int(upto), len(self.s["log"])))
        self._changed()

    # ---------------- vienti ----------------
    def rows(self, since: int = 0) -> list[list[str]]:
        out = []
        for e in sorted(self.s["log"], key=lambda x: x["nro"]):
            if e["nro"] > since:
                out.append([str(e["nro"]), e["call"], e["via"], e["kunta"], e["info"], e["oper"], e["dev"]])
        return out

    def export(self, fmt: str = "tsv", since: int = 0, header: bool = False) -> str:
        rows = self.rows(since)
        if fmt == "tsv":
            lines = (["\t".join(COLUMNS)] if header else []) + [
                "\t".join(c.replace("\t", " ").replace("\n", " ") for c in r) for r in rows]
            return "\n".join(lines)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow([f"TG {self.s['tg']}", f"Kierros {self.s['round']}", self.s["date"], f"Oper: {self.s['operator']}"])
        w.writerow(COLUMNS)
        w.writerows(rows)
        return buf.getvalue()

