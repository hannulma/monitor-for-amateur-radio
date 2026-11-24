"""Palaute ja lokit ylläpidolle.

- Paketti (zip): viesti, ohjelman loki (tiedosto ja muistissa olevat viimeisimmät rivit), asetukset ilman
  salaisuuksia, versio- ja tilatiedot. Puhelu- ja SMS-lokit vain käyttäjän valinnalla.
- Salaisuudet poistetaan aina: BM API-avain, APRS-passcode, check-in-avain ja kaikki avaimet, joiden nimessä on
  key/pass/token/secret. Lokista poistetaan JWT-avaimet ja vastaavat.
- Oma sijainti pyöristetään 4-merkkiseksi lokaattoriksi (noin 1° x 2°).
- Lähetys: JSON-POST ylläpidon Google Apps Script -verkkosovellukseen (feedback/Code.gs), joka lähettää viestin
  ja zip-liitteen ylläpidon sähköpostiin. Ohjelmassa ei ole salaisuuksia; vastaanotin rajoittaa koon ja määrän.
"""
from __future__ import annotations

import base64
import collections
import io
import json
import logging
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

from . import __date__, __version__
from .i18n import tr

# Ylläpidon vastaanottimen osoite (Google Apps Script -verkkosovellus). Asetuksen "feedback_url" voi ohittaa.
FEEDBACK_URL = ""
MAX_ZIP = 4_500_000          # vastaanotin hylkää yli 5 Mt (base64 kasvattaa noin kolmanneksella)
MAX_LOG_TAIL = 400_000       # lokitiedostosta viimeiset 400 kt
MAX_CSV_TAIL = 300_000

SECRET_KEYS = re.compile(r"(key|pass|token|secret|auth|sig)", re.I)
SECRET_TEXT = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[JWT poistettu]"),
    (re.compile(r"(?i)((?:api[_-]?key|passcode|pass|password|token|secret|key)\s*[=:]\s*)[^\s,;\"']+"), r"\1[poistettu]"),
    (re.compile(r"(?i)(pass\s+)-?\d{3,5}"), r"\1[poistettu]"),
    (re.compile(r"\b[0-9a-f]{40,}\b"), "[avain poistettu]"),
]


class MemoryLog(logging.Handler):
    """Viimeisimmät lokirivit muistissa (myös kun loki ei mene tiedostoon, esim. pip-asennuksessa)."""

    def __init__(self, n: int = 3000):
        super().__init__(logging.DEBUG)
        self.lines: collections.deque[str] = collections.deque(maxlen=n)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


MEMLOG = MemoryLog()


def install_memory_log() -> None:
    root = logging.getLogger()
    if MEMLOG not in root.handlers:
        root.addHandler(MEMLOG)


def scrub_text(text: str) -> str:
    for rx, rep in SECRET_TEXT:
        text = rx.sub(rep, text)
    return text


def locator4(lat: float, lon: float) -> str:
    lon += 180
    lat += 90
    return (chr(65 + int(lon // 20)) + chr(65 + int(lat // 10)) + str(int(lon % 20 // 2)) + str(int(lat % 10)))


def scrub_config(cfg) -> object:
    """Asetukset ilman salaisuuksia; koordinaatit pyöristetään lokaattoriksi."""
    if isinstance(cfg, dict):
        out = {}
        for k, v in cfg.items():
            if SECRET_KEYS.search(str(k)) and not isinstance(v, (dict, list, bool)):
                out[k] = "[poistettu]" if v not in (None, "", 0) else v
                continue
            if k in ("lat", "lon", "latitude", "longitude"):
                continue
            out[k] = scrub_config(v)
        if "lat" in cfg and "lon" in cfg:
            try:
                out["locator"] = locator4(float(cfg["lat"]), float(cfg["lon"]))
            except (TypeError, ValueError):
                pass
        if "label" in out and "locator" in out:
            out["label"] = "[poistettu]"
        return out
    if isinstance(cfg, list):
        return [scrub_config(x) for x in cfg]
    if isinstance(cfg, str):
        return scrub_text(cfg)
    return cfg


def _tail(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
                f.readline()
            return f.read()
    except OSError:
        return b""


def build_bundle(mon, message: str = "", include_logs: bool = False) -> tuple[bytes, list[str]]:
    """Zip-paketti ja sen tiedostojen nimet."""
    cdir = mon.cfg_path.parent
    files: dict[str, bytes] = {}
    info = {
        "app": "Monitor for Amateur Radio", "version": __version__, "date": __date__,
        "created": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "platform": platform.platform(), "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)), "language": mon.cfg.get("language"),
    }
    try:
        snap = mon.snapshot()
        info["status"] = {k: snap.get(k) for k in ("connected", "status", "events", "last_event", "demo") if k in snap}
        info["update"] = snap.get("update")
        svc = snap.get("services") or {}
        info["services"] = {n: {k: v for k, v in (svc.get(n) or {}).items()
                                if k in ("enabled", "error", "checked", "level", "source")}
                            for n in svc if isinstance(svc.get(n), dict)}
    except Exception as e:  # noqa: BLE001
        info["status_error"] = str(e)
    files["info.json"] = json.dumps(scrub_config(info), ensure_ascii=False, indent=2).encode()
    files["config.json"] = json.dumps(scrub_config(mon.cfg), ensure_ascii=False, indent=2).encode()
    if message:
        files["viesti.txt"] = message.encode()
    for name in ("monitor.log", "monitor.old.log", "update.log"):
        data = _tail(cdir / name, MAX_LOG_TAIL)
        if data:
            files[name] = scrub_text(data.decode("utf-8", "replace")).encode()
    if MEMLOG.lines:
        files["recent.log"] = scrub_text("\n".join(MEMLOG.lines)).encode()
    if include_logs:
        for p in (mon.calls_csv, mon.data_csv):
            data = _tail(Path(p), MAX_CSV_TAIL)
            if data:
                files[Path(p).name] = data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
    data = buf.getvalue()
    if len(data) > MAX_ZIP and include_logs:
        return build_bundle(mon, message, False)
    return data, list(files)


def bundle_name(call: str) -> str:
    c = re.sub(r"[^A-Za-z0-9-]", "", call or "")[:12] or "anon"
    return f"monitor-support-{c.upper()}-{time.strftime('%Y%m%d-%H%M')}.zip"


async def send(url: str, payload: dict) -> dict:
    """POST Apps Scriptiin (302-uudelleenohjaus luetaan GET:llä, kuten check-in-taulukossa)."""
    from .messaging import friendly_error, http_session
    try:
        async with http_session(60) as s:
            async with s.post(url, data=json.dumps(payload), headers={"Content-Type": "text/plain"},
                              allow_redirects=False) as r:
                if r.status in (301, 302, 303, 307, 308):
                    async with s.get(urljoin(url, r.headers.get("Location", ""))) as r2:
                        text, status = await r2.text(), r2.status
                else:
                    text, status = await r.text(), r.status
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(tr("Palautteen lähetys epäonnistui: {e}", e=friendly_error(e))) from e
    try:
        res = json.loads(text)
    except ValueError:
        raise RuntimeError(tr("Vastaanotin vastasi odottamattomasti (HTTP {s})", s=status)) from None
    if not res.get("ok"):
        raise RuntimeError({"rate": tr("Palautteita on lähetetty liikaa – yritä myöhemmin uudelleen"),
                            "size": tr("Paketti on liian suuri – jätä puhelu- ja SMS-lokit pois")}.get(
            res.get("code"), res.get("error") or tr("Vastaanotin hylkäsi palautteen")))
    return res


def payload(message: str, call: str, email: str, zip_bytes: bytes, name: str) -> dict:
    return {"op": "feedback", "message": message[:5000], "call": (call or "")[:12], "email": (email or "")[:120],
            "version": __version__, "platform": platform.platform()[:120], "name": name,
            "zip": base64.b64encode(zip_bytes).decode()}
