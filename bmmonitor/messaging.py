"""Syötteiden seuranta ja viestien lähetys DMR-radioon Brandmeisterin
APRS-yhdyskäytävän kautta.

Brandmeister välittää APRS-viestin DMR-tekstiviestinä radiolle, jonka DMR-ID:hen
kutsu+SSID on liitetty BM SelfCaressa (APRS-asetukset). Viesti lähetetään
APRS-IS-verkkoon omalla kutsulla ja passcodella.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import ssl
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

import aiohttp

from .i18n import tr

log = logging.getLogger("bmmonitor.msg")

APRS_MSG_MAX = 67
TOCALL = "APZBMM"
RETRY_INTERVAL = 45
MAX_TRIES = 3

DEFAULT_MESSAGING = {
    "enabled": False,
    "login": "",              # esim. OH2ABC-13 (ei sama kuin iGaten/Graywolfin tunnus)
    "passcode": "",           # tyhjä = lasketaan kutsusta
    "server": "rotate.aprs2.net",
    "port": 14580,
    "recipients": [],         # esim. ["OH2ABC-7"] = radion kutsu+SSID BM SelfCaresta
    "ascii": False,           # muunna ääkköset (ä->a) jos radio näyttää ne väärin
    "no_ack": False,          # True = lähetä kerran ilman viestinumeroa, älä odota kuittausta
    "aprs_watch": [],         # kutsut (ilman SSID:tä), joiden kaikkien SSID:iden APRS-viestit näytetään
    "max_parts": 2,           # pitkä otsikko jaetaan enintään näin moneen viestiin
    "max_per_check": 3,       # enintään näin monta uutta uutista per syöte kerralla
    "feed_interval": 600,     # sekuntia
    "feeds": [
        {"name": "BM", "url": "https://news.brandmeister.network/feed/", "enabled": True, "cmd": "BM"},
    ],
    "commands_enabled": True,  # vastaa radiosta lähetettyihin komentoihin (APUA, TG244, KUKA ...)
    "commands_all": False,     # False = vain vastaanottajat, oma kutsu ja sallittujen lista
    "commands_allow": [],
}

_SSL_CTX = None


def ssl_context() -> ssl.SSLContext:
    """Varmennetarkistus käyttöjärjestelmän omalla tarkistimella (truststore), kuten selaimessa.
    Windows osaa silloin hakea puuttuvat juuri- ja välivarmenteet. Varalla certifi-paketti."""
    global _SSL_CTX
    if _SSL_CTX is None:
        try:
            import truststore
            _SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:  # noqa: BLE001
            try:
                import certifi
                _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
            except Exception:  # noqa: BLE001
                _SSL_CTX = ssl.create_default_context()
    return _SSL_CTX


def ws_options() -> dict:
    """socket.io/websocket-client-asetukset: sama käyttöjärjestelmän varmennetarkistus kuin muissa hauissa.
    Ilman tätä websocket-client lukee varmenteet OpenSSL:n oletuspolusta, joka on tyhjä mm. macOS-sovelluspaketissa."""
    return {"sslopt": {"context": ssl_context()}}


def http_session(timeout: int = 30) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),
                                 connector=aiohttp.TCPConnector(ssl=ssl_context()))
    # Huom: ei omaa User-Agentia – Brandmeisterin API palauttaa silloin 403.


def friendly_error(e: Exception) -> str:
    msg = str(e) or e.__class__.__name__
    if "CERTIFICATE_VERIFY_FAILED" in msg or isinstance(e, aiohttp.ClientConnectorCertificateError):
        host = getattr(e, "host", "") or ""
        return tr("Varmenteen tarkistus epäonnistui ({host}). ", host=host) + tr(
            "Päivitä: pip install --upgrade truststore certifi")
    if isinstance(e, asyncio.TimeoutError):
        return tr("Aikakatkaisu")
    return msg


CALL_RE = re.compile(r"^[A-Z0-9]{3,7}(-[A-Z0-9]{1,2})?$")


def aprs_passcode(call: str) -> int:
    base = call.split("-")[0].upper()
    h = 0x73E2
    for i, ch in enumerate(base):
        h ^= ord(ch) << 8 if i % 2 == 0 else ord(ch)
    return h & 0x7FFF


def clean_text(text: str, ascii_only: bool) -> str:
    text = html.unescape(re.sub(r"<[^<>]*>", " ", text or ""))   # lineaarinen myös monilla <-merkeillä
    text = re.sub(r"\s+", " ", text).strip()
    # APRS-viestissä ei saa olla merkkejä | ~ {
    text = text.replace("|", "/").replace("~", "-").replace("{", "(")
    text = text.replace("–", "-").replace("—", "-").replace("’", "'") \
        .replace("‘", "'").replace("“", '"').replace("”", '"').replace("…", "...")
    if ascii_only:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return text


def split_message(text: str, max_parts: int) -> list[str]:
    if len(text) <= APRS_MSG_MAX:
        return [text]
    parts: list[str] = []
    rest = text
    while rest and len(parts) < max_parts:
        last = len(parts) == max_parts - 1
        if len(rest) <= APRS_MSG_MAX:
            parts.append(rest)
            rest = ""
            break
        limit = APRS_MSG_MAX - 3
        cut = rest.rfind(" ", 0, limit)
        if cut < limit * 0.6:
            cut = limit
        chunk = rest[:cut].rstrip()
        if last:
            parts.append(chunk + "...")
            rest = ""
        else:
            parts.append(chunk + " >>")
            rest = rest[cut:].lstrip()
    return parts


def validate_call(c: str) -> str:
    c = c.strip().upper()
    if not CALL_RE.match(c):
        raise ValueError(tr("Virheellinen kutsu: {x} (muoto esim. OH2ABC-7)", x=repr(c)))
    return c


# --------------------------------------------------------------------------
# APRS-IS
# --------------------------------------------------------------------------
class AprsClient:
    def __init__(self, hub: "MessagingHub"):
        self.hub = hub
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.connected = False
        self.verified = None          # True/False palvelimen logresp-vastauksesta
        self.server_name = ""
        self.error = None
        self.msgid = int(time.time()) % 900
        self.pending: dict[str, dict] = {}     # msgid -> lokirivi
        self.queue: asyncio.Queue = asyncio.Queue()
        self._config_version = 0

    @property
    def cfg(self):
        return self.hub.cfg

    def next_id(self) -> str:
        self.msgid = (self.msgid + 1) % 1000
        return str(self.msgid)

    async def run(self):
        while True:
            ver = self.hub.config_version
            if not self.cfg.get("login"):
                self.connected = False
                await asyncio.sleep(2)
                continue
            try:
                await self._session(ver)
            except Exception as e:  # noqa: BLE001
                self.error = str(e) or e.__class__.__name__
                log.warning("APRS-IS: %s", self.error)
            self.connected = False
            self.hub.touch()
            await self._close()
            if self.hub.config_version == ver:
                await asyncio.sleep(15)

    async def _close(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001
                pass
        self.reader = self.writer = None

    async def _session(self, ver: int):
        login = self.cfg["login"].upper()
        passcode = str(self.hub.passcode_secret or self.cfg.get("passcode") or aprs_passcode(login))
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.cfg.get("server") or "rotate.aprs2.net",
                                    int(self.cfg.get("port") or 14580)), 20)
        from . import __version__
        pats = "/".join(f"{b}*" for b in sorted(self.hub.watch_bases()))
        self.writer.write(f"user {login} pass {passcode} vers BMMonitor {__version__} filter g/{pats} b/{pats}\r\n"
                          .encode())
        await self.writer.drain()
        self.connected = True
        self.error = None
        self.verified = None
        self.hub.touch()
        log.info("APRS-IS yhdistetty (%s)", login)
        sender = asyncio.create_task(self._sender(login))
        keepalive_at = time.time() + 600
        try:
            while self.hub.config_version == ver:
                try:
                    line = await asyncio.wait_for(self.reader.readline(), 5)
                except asyncio.TimeoutError:
                    if time.time() > keepalive_at:
                        self.writer.write(b"#keepalive\r\n")
                        await self.writer.drain()
                        keepalive_at = time.time() + 600
                    continue
                if not line:
                    raise ConnectionError(tr("APRS-IS sulki yhteyden"))
                self._handle_line(line.decode("utf-8", "replace").strip(), login)
        finally:
            sender.cancel()

    def _handle_line(self, line: str, login: str):
        if line.startswith("#"):
            if "logresp" in line:
                self.verified = " verified" in line and "unverified" not in line
                m = re.search(r"server (\S+)", line)
                self.server_name = m.group(1) if m else ""
                if not self.verified:
                    self.error = tr("Passcode ei kelpaa (unverified) – viestejä ei välitetä")
                self.hub.touch()
            return
        # APRS-viesti:  SRC>DST,PATH::ADDRESSEE:ackNN / rejNN / teksti{NN
        m = re.match(r"^([^>]+)>[^:]*::([^:]{9}):(.*)$", line)
        if not m:
            return
        src, addressee, body = m.group(1).upper(), m.group(2).strip().upper(), m.group(3)
        bases = self.hub.watch_bases()
        login_base = login.split("-")[0].upper()
        if src.split("-")[0] not in bases and addressee.split("-")[0] not in bases:
            return
        am = re.match(r"^(ack|rej)([A-Za-z0-9}]{1,5})", body)
        if am:
            mid = am.group(2).split("}")[0]
            entry = self.pending.get(mid)
            if entry and entry.get("from", login).upper() == addressee and entry["to"].upper() == src:
                self.pending.pop(mid, None)
                entry["status"] = "kuitattu" if am.group(1) == "ack" else "hylätty"
                entry["ack_time"] = time.time()
            self.hub.mark_ack(sender=addressee, recipient=src, msgid=mid, ok=am.group(1) == "ack")
            self.hub.touch()
            return
        txt, _, mid = body.partition("{")
        mid = mid.split("}")[0] if mid else ""
        if mid and addressee == login.upper():
            self.queue.put_nowait(("raw", f"{login}>{TOCALL},TCPIP*::{src:<9}:ack{mid}"))
        if not self.hub.add_aprs(src, addressee, txt, mid):
            return          # uudelleenlähetys, jo käsitelty
        if addressee.split("-")[0] == login_base:
            self.hub.add_log({"time": time.time(), "dir": "in", "to": addressee, "from": src,
                              "text": txt, "status": "vastaanotettu"})
        if addressee == login.upper() and self.hub.command_handler:
            try:
                reply = self.hub.command_handler(src, txt)
            except Exception as e:  # noqa: BLE001
                log.warning("Komennon käsittely: %s", e)
                reply = None
            if reply:
                text = clean_text(reply, bool(self.cfg.get("ascii")))
                for part in split_message(text, max(2, int(self.cfg.get("max_parts") or 2))):
                    self.send(src.upper(), part, source="komento")

    async def _sender(self, login: str):
        while True:
            item = await self.queue.get()
            if item[0] == "raw":
                line = item[1]
            else:
                _, entry = item
                if entry["status"] not in ("jonossa", "uudelleen"):
                    continue
                if not self.verified and self.verified is not None:
                    entry["status"] = "virhe: passcode"
                    self.hub.touch()
                    continue
                entry["tries"] += 1
                entry["sent_time"] = time.time()
                if entry.get("no_ack"):
                    # Ei viestinumeroa -> vastaanottaja ei kuittaa eikä BM lähetä uudelleen; yksi lähetys.
                    entry["status"] = "lähetetty (ei kuittausta)"
                    line = f"{entry.get('from') or login}>{TOCALL},TCPIP*::{entry['to']:<9}:{entry['text']}"
                else:
                    entry["status"] = "lähetetty"
                    self.pending[entry["msgid"]] = entry
                    line = f"{entry.get('from') or login}>{TOCALL},TCPIP*::{entry['to']:<9}:{entry['text']}{{{entry['msgid']}"
                    asyncio.get_running_loop().call_later(RETRY_INTERVAL, self._retry, entry)
                self.hub.touch()
            self.writer.write((line + "\r\n").encode("utf-8"))
            await self.writer.drain()
            log.info("APRS > %s", line)
            await asyncio.sleep(2)      # älä tulvi verkkoa

    def _retry(self, entry: dict):
        if entry["status"] != "lähetetty" or entry["msgid"] not in self.pending:
            return
        if entry["tries"] >= MAX_TRIES:
            self.pending.pop(entry["msgid"], None)
            entry["status"] = "ei kuittausta"
            self.hub.touch()
            return
        entry["status"] = "uudelleen"
        self.queue.put_nowait(("msg", entry))

    def send(self, to: str, text: str, source: str = "", src: str | None = None) -> dict:
        no_ack = bool(self.cfg.get("no_ack"))
        frm = (src or self.cfg.get("login") or "").upper()
        entry = {"time": time.time(), "dir": "out", "to": to.upper(), "from": frm, "text": text, "source": source,
                 "msgid": "" if no_ack else self.next_id(), "no_ack": no_ack, "tries": 0, "status": "jonossa"}
        self.hub.add_log(entry)
        bases = self.hub.watch_bases()
        if frm.split("-")[0] in bases or entry["to"].split("-")[0] in bases:
            self.hub.aprs_msgs.appendleft(entry)
        self.queue.put_nowait(("msg", entry))
        return entry


# --------------------------------------------------------------------------
# Syötteet
# --------------------------------------------------------------------------
def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def parse_feed(data: bytes, content_type: str = "") -> tuple[str, list[dict]]:
    """Palauttaa (syötteen otsikko, [{'id','title','link'}]) uusin ensin."""
    text = data.lstrip()
    if text[:1] in (b"{", b"[") or "json" in content_type:
        j = json.loads(data)
        items = [{"id": str(i.get("id") or i.get("url") or i.get("title")),
                  "title": i.get("title") or i.get("summary") or "",
                  "link": i.get("url") or ""} for i in j.get("items", [])]
        return j.get("title", ""), items
    root = ET.fromstring(data)
    items: list[dict] = []
    title = ""
    for el in root.iter():
        t = _tag(el)
        if t in ("item", "entry"):
            d = {"id": "", "title": "", "link": ""}
            for c in el:
                ct = _tag(c)
                if ct == "title":
                    d["title"] = "".join(c.itertext())
                elif ct in ("guid", "id"):
                    d["id"] = (c.text or "").strip()
                elif ct == "link":
                    d["link"] = c.get("href") or (c.text or "").strip()
            d["id"] = d["id"] or d["link"] or d["title"]
            items.append(d)
        elif t == "title" and not title:
            title = "".join(el.itertext()).strip()
    return title, items


class FeedWatcher:
    def __init__(self, hub: "MessagingHub"):
        self.hub = hub
        self.state_path = hub.state_dir / "feeds_state.json"
        self.seen: dict[str, list[str]] = {}
        self.status: dict[str, dict] = {}
        self.wake = asyncio.Event()
        if self.state_path.exists():
            try:
                self.seen = json.loads(self.state_path.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                self.seen = {}

    def save(self):
        self.state_path.write_text(json.dumps(self.seen), encoding="utf-8")

    async def run(self):
        while True:
            try:
                await self.check_all()
            except Exception as e:  # noqa: BLE001
                log.warning("Syötteiden tarkistus: %s", e)
            interval = max(60, int(self.hub.cfg.get("feed_interval") or 600))
            try:
                await asyncio.wait_for(self.wake.wait(), interval)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def fetch(self, url: str) -> tuple[str, list[dict]]:
        async with http_session() as s:
            async with s.get(url) as r:
                r.raise_for_status()
                return parse_feed(await r.read(), r.headers.get("Content-Type", ""))

    async def check_all(self):
        feeds = [f for f in self.hub.cfg.get("feeds", []) if f.get("url")]
        for f in feeds:
            url = f["url"]
            st = self.status.setdefault(url, {})
            try:
                title, items = await self.fetch(url)
            except Exception as e:  # noqa: BLE001
                st.update(error=friendly_error(e), checked=time.time())
                self.hub.touch()
                continue
            st.update(error=None, checked=time.time(), title=title, count=len(items),
                      latest=items[0]["title"] if items else "")
            first_time = url not in self.seen
            seen = set(self.seen.get(url, []))
            new = [i for i in items if i["id"] not in seen]
            if first_time:
                # Ensimmäisellä kerralla vain merkitään nykyiset nähdyiksi – ei viestitulvaa.
                new = []
            if f.get("enabled", True) and new:
                for item in reversed(new[: int(self.hub.cfg.get("max_per_check") or 3)]):
                    self.hub.announce(f.get("name") or title, item["title"], source=url)
                st["last_new"] = time.time()
            self.seen[url] = [i["id"] for i in items][:200] + [s for s in self.seen.get(url, []) if s not in
                                                                 {i["id"] for i in items}][:100]
            self.hub.touch()
        self.save()


# --------------------------------------------------------------------------
class MessagingHub:
    def __init__(self, cfg: dict, state_dir: Path, touch):
        self.cfg = cfg
        self.state_dir = state_dir
        self.touch = touch
        self.config_version = 0
        self.log: deque[dict] = deque(maxlen=200)
        self.command_handler = None
        self.aprs_msgs: deque[dict] = deque(maxlen=300)
        self.aprs = AprsClient(self)
        self.feeds = FeedWatcher(self)

    def add_log(self, entry: dict):
        self.log.appendleft(entry)
        self.touch()

    def watch_bases(self) -> set[str]:
        bases = {c.upper().split("-")[0] for c in self.cfg.get("aprs_watch") or [] if c}
        if self.cfg.get("login"):
            bases.add(self.cfg["login"].upper().split("-")[0])
        return bases

    def add_aprs(self, src: str, dst: str, text: str, msgid: str) -> bool:
        """Lisää vastaanotettu viesti näkymään. False = uudelleenlähetys (jo nähty)."""
        now = time.time()
        for e in self.aprs_msgs:
            if now - e["time"] > 1800:
                break
            if e.get("dir") == "in" and e["from"] == src and e["to"] == dst and \
                    ((msgid and e.get("msgid") == msgid) or (not msgid and e["text"] == text)):
                e["copies"] = e.get("copies", 1) + 1
                return False
        self.aprs_msgs.appendleft({"time": now, "dir": "in", "from": src, "to": dst, "text": text,
                                   "msgid": msgid, "status": "vastaanotettu" if msgid else "ei kuittauspyyntöä"})
        self.touch()
        return True

    def mark_ack(self, sender: str, recipient: str, msgid: str, ok: bool):
        for e in self.aprs_msgs:
            if e.get("msgid") == msgid and e["from"] == sender and e["to"] == recipient:
                if e.get("dir") == "in":
                    e["status"] = "kuitattu" if ok else "hylätty"
                e["acked"] = ok
                break

    passcode_secret: str = ""          # oma passcode salasanavarastosta (ei asetustiedostossa)

    def reconfigure(self, cfg: dict):
        self.cfg = cfg
        self.config_version += 1
        self.feeds.wake.set()

    def announce(self, prefix: str, title: str, source: str = ""):
        text = clean_text(f"{prefix}: {title}" if prefix else title, bool(self.cfg.get("ascii")))
        recips = self.cfg.get("recipients") or []
        if not recips:
            self.add_log({"time": time.time(), "dir": "out", "to": "-", "text": text, "source": source,
                          "status": "ei vastaanottajia"})
            return
        if not self.cfg.get("enabled"):
            self.add_log({"time": time.time(), "dir": "out", "to": ", ".join(recips), "text": text,
                          "source": source, "status": "lähetys pois päältä"})
            return
        for part in split_message(text, int(self.cfg.get("max_parts") or 2)):
            for r in recips:
                self.aprs.send(r, part, source)

    def status(self) -> dict:
        a = self.aprs
        return {
            "aprs": {"connected": a.connected, "verified": a.verified, "server": a.server_name,
                     "error": a.error, "enabled": bool(self.cfg.get("enabled")),
                     "passcode_auto": aprs_passcode(self.cfg["login"]) if self.cfg.get("login") else None},
            "feeds": self.feeds.status,
            "log": list(self.log)[:100],
            "aprs_msgs": [{k: e.get(k) for k in ("time", "dir", "from", "to", "text", "msgid", "status", "tries", "copies")}
                          for e in list(self.aprs_msgs)[:150]],
            "watch_bases": sorted(self.watch_bases()),
        }

    async def run(self):
        await asyncio.gather(self.aprs.run(), self.feeds.run())


def validate_passcode(value) -> str:
    p = str(value or "").strip()
    if p and not p.lstrip("-").isdigit():
        raise ValueError(tr("Passcoden pitää olla numero"))
    return p


def normalize_messaging(new: dict, old: dict) -> dict:
    """Muut viestiasetukset; passcode käsitellään erikseen (salasanavarasto, ks. app.Monitor)."""
    out = dict(old)
    if "enabled" in new:
        out["enabled"] = bool(new["enabled"])
    if "login" in new:
        out["login"] = validate_call(new["login"]) if str(new["login"]).strip() else ""
    if "server" in new:
        out["server"] = str(new["server"]).strip() or "rotate.aprs2.net"
    if "port" in new:
        out["port"] = int(new["port"] or 14580)
    if "recipients" in new:
        r = new["recipients"]
        if isinstance(r, str):
            r = r.replace(",", " ").split()
        out["recipients"] = [validate_call(x) for x in r if str(x).strip()]
    if "aprs_watch" in new:
        r = new["aprs_watch"]
        r = r.replace(",", " ").split() if isinstance(r, str) else r
        out["aprs_watch"] = list(dict.fromkeys(validate_call(x).split("-")[0] for x in r if str(x).strip()))
    if "commands_allow" in new:
        r = new["commands_allow"]
        if isinstance(r, str):
            r = r.replace(",", " ").split()
        out["commands_allow"] = [validate_call(x) for x in r if str(x).strip()]
    for k in ("ascii", "no_ack", "commands_enabled", "commands_all"):
        if k in new:
            out[k] = bool(new[k])
    for k, lo, hi in (("max_parts", 1, 5), ("max_per_check", 1, 20), ("feed_interval", 60, 86400)):
        if k in new:
            out[k] = min(hi, max(lo, int(float(new[k] or lo))))
    if "feeds" in new:
        feeds = []
        for f in new["feeds"]:
            url = str(f.get("url", "")).strip()
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                raise ValueError(tr("Syötteen osoitteen pitää alkaa http:// tai https:// ({url})", url=url))
            cmd = re.sub(r"[^A-Z0-9ÄÖÅ]", "", str(f.get("cmd", "")).upper())[:10]
            feeds.append({"name": str(f.get("name", "")).strip()[:20], "url": url,
                          "enabled": bool(f.get("enabled", True)), "cmd": cmd})
        out["feeds"] = feeds
    if out.get("enabled") and not out.get("login"):
        raise ValueError(tr("Anna oma kutsu (APRS-IS-tunnus) ennen kuin otat lähetyksen käyttöön"))
    return out
