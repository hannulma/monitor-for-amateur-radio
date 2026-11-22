"""Check-in-netin kirjaus.

Ylläpitäjä aloittaa kierroksen (puheryhmä, kierrosnumero, päivämäärä, operaattori). Ohjelma kerää
puheryhmässä avanneet asemat saapumisjärjestykseen (järjestys ei koskaan muutu) ja ylläpitäjä
kirjaa ne, jotka hän kuuli. Lokinumero annetaan kirjausjärjestyksessä.

Lokin sarakkeet vastaavat FinDMR-kierrostaulukkoa: NRO, kutsu, RPT/HOTSPOT, KUNTA, Lisätietoja, Oper.
RPT/HOTSPOT täytetään BrandMeisterin laitetiedoista (toistimen kutsu ja paikkakunta).

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
import unicodedata
import uuid

from . import secret_store
from .i18n import tr
from .sheetsync import SheetClient, SheetError, pair, valid_url

log = logging.getLogger("bmmonitor.checkin")

NETS = [244, 248, 91]                    # oletusvalinnat käyttöliittymässä
COLUMNS = ["NRO", "Kutsu", "RPT/HOTSPOT", "KUNTA", "Lisätietoja", "Oper"]
EDITABLE = ("via", "kunta", "info")
MAX_STATIONS = 1000
SYNC_EVERY = 5                           # s, taulukon tilan haku kierroksen aikana
SECRET_NAME = "checkin-sheet-key"
_LOCATOR = re.compile(r",?\s*\b[A-R]{2}\d{2}([A-X]{2})?\b", re.I)


def clean_city(city: str) -> str:
    """'Jarvenpaa, KP20NK' -> 'Jarvenpaa'."""
    return _LOCATOR.sub("", city or "").strip(" ,")


# Brandmeisterin yhteystyyppi (LinkName / LinkCall) -> RPT/HOTSPOT-sarakkeen arvo.
# Muut tyypit käsitellään laitteen ID:n mukaan (toistin tai Hotspot).
LINK_KINDS = (("detroit bridge", "TetraPack"), ("tetrapack", "TetraPack"), ("hytera hytalk", "POC"),
              ("ysf client", "Fusion"), ("wires-x", "Fusion"), ("open dmr terminal", "Open DMR"))


def link_kind(p: dict) -> str:
    """'Detroit Bridge' / 'TetraPack Core …' -> 'TetraPack', 'Hytera HyTalk/RoIP' -> 'POC' jne."""
    text = f"{p.get('LinkName') or ''} {p.get('LinkCall') or ''}".lower()
    return next((v for k, v in LINK_KINDS if k in text), "")


def _fold(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def device_label(dcall: str, city: str) -> str:
    """Toistimen tunnus ja paikkakunta, mutta paikkakunta vain kerran:
    'OH8DMRO Oulu N' + 'Oulu' -> 'OH8DMRO Oulu N', 'OH2RUP' + 'Jarvenpaa' -> 'OH2RUP Jarvenpaa'."""
    dcall, city = (dcall or "").strip(), (city or "").strip()
    if city and re.search(r"(?<!\w)" + re.escape(_fold(city)) + r"(?!\w)", _fold(dcall)):
        return dcall
    return f"{dcall} {city}".strip()


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
        self.sheet: SheetClient | None = None
        self.sheet_state = {"error": None, "synced": None, "sheet": None, "key_store": None}
        self._load_sheet()

    # ---------------- tila ----------------
    def _empty(self) -> dict:
        return {"active": False, "tg": None, "round": None, "date": "", "operator": "", "started": None,
                "ended": None, "stations": [], "log": [], "copied_upto": 0, "next_idx": 1}

    def _load(self):
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, dict) and "stations" in data:
                self.s.update({k: data.get(k, v) for k, v in self._empty().items()})
                for st in self.s["stations"]:
                    st["on_air"] = False
                    if str(st.get("call", "")).isdigit():     # vanhan version pelkän ID:n rivit piiloon
                        st["call"], st["idx"] = "", None
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
                             for st in s["stations"] if st["call"]],
                "id_only": sum(1 for st in s["stations"] if not st["call"]),
                "log": s["log"], "unique_repeaters": len(rpts), "archive": str(self.archive_dir),
                "sheet": self.sheet_snapshot()}

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
        # Asema tunnistetaan DMR ID:stä: BrandMeister lähettää joskus avauksen alun pelkällä ID:llä ja
        # kutsun vasta myöhemmin. Pelkän ID:n asemat pysyvät piilossa, kunnes kutsu tiedetään.
        src = p.get("SourceID")
        call = (p.get("SourceCall") or "").strip().upper()
        if call.isdigit():
            call = ""
        if not call and not src:
            return
        st_call = next((x for x in s["stations"] if call and x["call"] == call), None)
        st_id = next((x for x in s["stations"] if src and x.get("id") == src), None)
        if st_call and st_id and st_call is not st_id and not st_id["call"]:
            for k in ("keys", "overlap"):                 # yhdistä piilotettu ID-rivi kutsulliseen
                st_call[k] += st_id[k]
            st_call["first"] = min(st_call["first"], st_id["first"])
            st_call["on_air"] = st_call["on_air"] or st_id["on_air"]
            s["stations"].remove(st_id)
            st_id = None
        st = st_call or st_id
        stop = float(p.get("Stop") or 0)
        ended = bool(stop) or p.get("Event") == "Session-Stop"
        sid = p.get("SessionID") or f"{src}-{int(start)}"
        if st is None:
            if len(s["stations"]) >= MAX_STATIONS:
                return
            st = {"idx": None, "call": "", "name": "", "id": src,
                  "first": start, "last": start, "keys": 0, "on_air": False, "overlap": 0,
                  "dev_id": None, "link": "", "via": "", "kunta": "", "dev": "", "repeater": False}
            s["stations"].append(st)
        if call and not st["call"]:
            st["call"] = call
            s["stations"].remove(st)                      # tulee näkyviin: aina listan loppuun
            s["stations"].append(st)
        if st["call"] and not st.get("idx"):
            st["idx"] = s.get("next_idx") or max((x.get("idx") or 0 for x in s["stations"]), default=0) + 1
            s["next_idx"] = st["idx"] + 1
        st["name"] = (p.get("SourceName") or st["name"] or "").strip()
        st["last"] = max(st["last"], stop or start)
        dev_id = p.get("ContextID") or None
        kind = link_kind(p)
        if kind and (kind != st.get("kind") or dev_id != st["dev_id"]):
            # TetraPack, POC, Fusion, Open DMR: tyyppi riittää, laiterekisteristä ei haeta paikkakuntaa
            st.update(kind=kind, dev_id=dev_id, link=(p.get("LinkCall") or p.get("LinkName") or "").strip(), repeater=False)
            self._fill_device(st)
        elif not kind and dev_id and (dev_id != st["dev_id"] or st.get("kind")):
            st["kind"] = ""
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
        kind = st.get("kind") or ""
        dev = {} if kind else (self.devices.get(st["dev_id"]) or {})
        dcall = (dev.get("call") or ("" if kind else st.get("link")) or "").strip()
        city = clean_city(dev.get("city", ""))
        cp = country_prefix(self.s["tg"] or 0)
        foreign = bool(cp and st.get("id") and not str(st["id"]).startswith(cp))
        via = "Ulkomaat" if foreign else (kind or (device_label(dcall, city) if st["repeater"] else "Hotspot"))
        st["dev"] = kind or ", ".join(x for x in (dcall, city) if x)
        if not st.get("edited_via"):
            st["via"] = via
        if not st.get("edited_kunta"):
            st["kunta"] = city
        for e in ([] if self.sheet else self.s["log"]):
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
        asyncio.get_running_loop().create_task(self.sync_loop())
        for st in self.s["stations"]:
            if st.get("dev_id") and not st.get("kind"):
                self._want_device(st["dev_id"])
        while True:
            dev_id = await self.queue.get()
            try:
                d = await self.api.device(int(dev_id), ttl=6 * 3600) or {}
                self.devices[dev_id] = {"call": (d.get("callsign") or "").strip(), "city": d.get("city") or ""}
            except Exception as e:  # noqa: BLE001  (hotspotit puuttuvat usein rekisteristä)
                log.debug("Laite %s: %s", dev_id, e)
            for st in self.s["stations"]:
                if st.get("dev_id") == dev_id and not st.get("kind"):
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

    # ---------------- Google-taulukko ----------------
    def _sheet_cfg(self) -> dict:
        return self.mon.cfg.setdefault("checkin", {}).setdefault("sheet", {})

    def _load_sheet(self):
        c = self.mon.cfg.get("checkin", {}).get("sheet") or {}
        if not (c.get("url") and c.get("call")):
            return
        key = secret_store.get(SECRET_NAME) if c.get("key_store") == "keyring" else c.get("key")
        if key:
            self.sheet = SheetClient(c["url"], c["call"], key)
            self.sheet_state["key_store"] = c.get("key_store")
        else:
            self.sheet_state["error"] = tr("Taulukon avainta ei löytynyt – yhdistä uudelleen parituskoodilla")

    def sheet_snapshot(self) -> dict:
        c = self.mon.cfg.get("checkin", {}).get("sheet") or {}
        return {"connected": self.sheet is not None, "call": c.get("call"), "url": c.get("url"),
                "host": self.sheet.host if self.sheet else None, **self.sheet_state}

    async def connect_sheet(self, url: str, code: str, allow_test: bool = False):
        url = (url or "").strip()
        if not valid_url(url, allow_test):
            raise ValueError(tr("Skriptin osoitteen pitää olla muotoa https://script.google.com/macros/s/…/exec"))
        call, key = await pair(url, code)
        stored = secret_store.put(SECRET_NAME, key)
        c = self._sheet_cfg()
        c.clear()
        c.update(url=url, call=call, key_store="keyring" if stored else "config")
        if not stored:
            c["key"] = key
        self.mon.save_config()
        self.sheet = SheetClient(url, call, key)
        self.sheet_state.update(error=None, synced=None, sheet=None, key_store=c["key_store"])
        await self.sheet.request("hello")
        if self.s["active"]:
            await self._remote_start()
        self._changed()

    def disconnect_sheet(self):
        secret_store.delete(SECRET_NAME)
        self.mon.cfg.setdefault("checkin", {})["sheet"] = {}
        self.mon.save_config()
        self.sheet = None
        self.sheet_state = {"error": None, "synced": None, "sheet": None, "key_store": None}
        self._changed()

    async def _remote(self, action: str, data: dict) -> dict:
        try:
            res = await self.sheet.request(action, {"round": self.s["round"], **data})
        except SheetError as e:
            self.sheet_state["error"] = str(e)
            self.mon.touch()
            raise ValueError(tr("Taulukko: {e}", e=e)) from None
        self.sheet_state.update(error=None, synced=time.time())
        if res.get("sheet"):
            self.sheet_state["sheet"] = res["sheet"]
        if "rows" in res:
            self._apply_rows(res["rows"])
        return res

    async def _remote_start(self):
        await self._remote("start", {"date": self.s["date"], "tg": self.s["tg"]})

    def _apply_rows(self, rows: list[dict]):
        """Taulukon rivit ovat totuus: lokinumerot, kirjaajat ja muokkaukset kaikilta ylläpitäjiltä."""
        old = {e["call"]: e for e in self.s["log"]}
        st = {x["call"]: x for x in self.s["stations"]}
        new = []
        for r in rows:
            call = str(r.get("call") or "").upper()
            if not call:
                continue
            prev = old.get(call, {})
            new.append({"uid": prev.get("uid") or uuid.uuid4().hex[:12], "nro": r.get("nro") or 0, "call": call,
                        "name": (st.get(call) or {}).get("name", prev.get("name", "")),
                        "via": r.get("via", ""), "kunta": r.get("kunta", ""), "info": r.get("info", ""),
                        "oper": r.get("oper", ""), "dev": r.get("dev", ""),
                        "repeater": (st.get(call) or {}).get("repeater", prev.get("repeater", False)),
                        "ts": prev.get("ts") or time.time()})
        new.sort(key=lambda e: e["nro"])
        if new != self.s["log"]:
            self.s["log"] = new
            self.save()
        self.mon.touch()

    async def sync_loop(self):
        while True:
            await asyncio.sleep(SYNC_EVERY)
            if self.sheet and self.s["active"]:
                try:
                    await self._remote("list", {})
                except ValueError:
                    pass                                   # virhe näkyy tilassa, yritetään uudelleen

    # ---- toiminnot, jotka kulkevat taulukon kautta kun se on yhdistetty ----
    async def a_start(self, tg, round_no=None, date: str = "", operator: str = ""):
        self.start(tg, round_no, date, operator)
        if self.sheet:
            await self._remote_start()

    async def a_log(self, call: str):
        if not self.sheet:
            return self.log_station(call)
        call = (call or "").strip().upper()
        st = next((x for x in self.s["stations"] if x["call"] == call), None)
        if st is None:
            raise ValueError(tr("Asemaa {c} ei ole kuultu tällä kierroksella", c=call))
        await self._remote("log", {"call": call, "via": st["via"], "kunta": st["kunta"], "info": "", "dev": st["dev"]})

    async def a_unlog(self, call: str):
        if not self.sheet:
            return self.unlog(call)
        await self._remote("unlog", {"call": (call or "").strip().upper()})

    async def a_edit(self, call: str, field: str, value: str):
        if not self.sheet:
            return self.edit(call, field, value)
        if field not in EDITABLE:
            raise ValueError(tr("Virheellinen parametri"))
        await self._remote("edit", {"call": (call or "").strip().upper(), "field": field, "value": str(value or "")[:200]})

    # ---------------- vienti ----------------
    def rows(self, since: int = 0) -> list[list[str]]:
        out = []
        for e in sorted(self.s["log"], key=lambda x: x["nro"]):
            if e["nro"] > since:
                out.append([str(e["nro"]), e["call"], e["via"], e["kunta"], e["info"], e["oper"]])
        return out

    def export(self, fmt: str = "tsv", since: int = 0, header: bool = False) -> str:
        rows = self.rows(since)
        if fmt == "tsv":
            # leikepöytä taulukkoon: sarakkeet A–F
            lines = (["\t".join(COLUMNS)] if header else []) + [
                "\t".join(c.replace("\t", " ").replace("\n", " ") for c in r) for r in rows]
            return "\n".join(lines)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow([f"TG {self.s['tg']}", f"Kierros {self.s['round']}", self.s["date"], f"Oper: {self.s['operator']}"])
        w.writerow(COLUMNS)
        w.writerows(rows)
        return buf.getvalue()

