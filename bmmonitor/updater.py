"""Päivitysten tarkistus ja asennus GitHubin julkaisuista.

- Tarkistus kerran vuorokaudessa (ja käsin). Beta-versiota käyttävä saa myös uudet betat,
  vakaata versiota käyttävä vain vakaat versiot.
- Asennus ladataan, tarkistetaan julkaisun SHA256SUMS.txt-tiedostoa vasten ja asennetaan:
    Windows (asennusohjelmalla asennettu): uusi asennusohjelma avautuu, käyttäjä vahvistaa, ohjelma käynnistyy uudelleen
    macOS: .app vaihdetaan levykuvasta paikalleen apuskriptillä, ohjelma käynnistyy uudelleen
    Linux: ohjelmatiedosto vaihdetaan apuskriptillä, ohjelma käynnistyy uudelleen
  Muissa tapauksissa (Windows ilman asennusta, pip) tarjotaan latauslinkki / komento.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from . import __version__
from .i18n import tr
from .messaging import friendly_error, http_session

log = logging.getLogger("bmmonitor.updater")

REPO = "hannulma/monitor-for-amateur-radio"
RELEASES_API = os.environ.get("BMM_RELEASES_API") or f"https://api.github.com/repos/{REPO}/releases?per_page=100"  # testejä varten
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
RELEASES_ATOM = f"https://github.com/{REPO}/releases.atom"   # varareitti, jos API:n kyselyraja on täynnä
DOWNLOAD = f"https://github.com/{REPO}/releases/download"
CHECK_EVERY = 24 * 3600
FIRST_DELAY = 15

# Julkaisutiedostojen nimet ({v} = versio) – käytetään, kun tiedot luetaan Atom-syötteestä
ASSET_NAMES = {
    "windows-setup": "MonitorForBrandMeister-{v}-windows-setup.exe",
    "windows-portable": "MonitorForBrandMeister-{v}-windows-portable.zip",
    "macos": "MonitorForBrandMeister-{v}-macos-universal.dmg",
    "linux": "MonitorForBrandMeister-{v}-linux-x86_64.tar.gz",
    "pip": "monitor_for_brandmeister-{v}-py3-none-any.whl",
}
ASSETS = {
    "windows-setup": r"-windows-setup\.exe$",
    "windows-portable": r"-windows-portable\.zip$",
    "macos": r"-macos-universal\.dmg$",
    "linux": r"-linux-x86_64\.tar\.gz$",
    "pip": r"-py3-none-any\.whl$",
}
AUTO_INSTALL = {"windows-setup", "macos", "linux"}

_VER = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?")
_STAGE = {"a": 0, "b": 1, "rc": 2, None: 3}


def parse_version(v: str) -> tuple | None:
    m = _VER.fullmatch((v or "").strip())
    if not m:
        return None
    return (int(m[1]), int(m[2]), int(m[3] or 0), _STAGE[m[4]], int(m[5] or 0))


def is_prerelease(v: str) -> bool:
    p = parse_version(v)
    return bool(p and p[3] < 3)


def pick_latest(releases: list[dict], current: str) -> dict | None:
    """Uusin julkaisu. Betaa käyttävälle myös betat, vakaalle vain vakaat."""
    allow_pre = is_prerelease(current)
    best, best_v = None, None
    for r in releases:
        if r.get("draft"):
            continue
        v = parse_version(r.get("tag_name", ""))
        if not v or (v[3] < 3 and not allow_pre):
            continue
        if best_v is None or v > best_v:
            best, best_v = r, v
    return best


def install_kind() -> str:
    if not getattr(sys, "frozen", False):
        return "pip"
    if sys.platform == "win32":
        return "windows-setup" if (Path(sys.executable).parent / "unins000.exe").exists() else "windows-portable"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def find_asset(release: dict, kind: str) -> dict | None:
    pat = re.compile(ASSETS[kind])
    for a in release.get("assets", []):
        if pat.search(a.get("name", "")):
            return {"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
    return None


def releases_from_atom(xml_text: str) -> list[dict]:
    """GitHubin releases.atom -> API:n kaltaiset julkaisut (tiedostot nimeämiskäytännön mukaan)."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(xml_text).findall("a:entry", ns):
        link = e.find("a:link", ns)
        url = link.get("href") if link is not None else ""
        tag = url.rstrip("/").rsplit("/", 1)[-1]
        if not parse_version(tag):
            continue
        v = tag.lstrip("v")
        assets = [{"name": n.format(v=v), "browser_download_url": f"{DOWNLOAD}/{tag}/{n.format(v=v)}", "size": 0}
                  for n in ASSET_NAMES.values()]
        assets.append({"name": "SHA256SUMS.txt", "browser_download_url": f"{DOWNLOAD}/{tag}/SHA256SUMS.txt"})
        out.append({"tag_name": tag, "html_url": url, "draft": False, "body": "",
                    "published_at": (e.findtext("a:updated", "", ns) or None), "assets": assets})
    return out


def parse_sums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def clean_env() -> dict:
    """Ympäristö käynnistettäville ohjelmille ilman PyInstallerin omia muuttujia.

    Muuten uusi (myös PyInstallerilla tehty) versio luulisi olevansa vanhan aliprosessi, ja
    Linuxin LD_LIBRARY_PATH osoittaisi vanhan version purkuhakemistoon."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("_PYI_", "_MEIPASS")):
            env.pop(k)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        elif getattr(sys, "frozen", False):
            env.pop(var, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def mac_bundle() -> Path | None:
    """Käynnissä olevan .app-paketin polku (…/Monitor for BrandMeister.app)."""
    exe = Path(sys.executable).resolve()
    for p in exe.parents:
        if p.suffix == ".app":
            return p
    return None


def relaunch_args() -> str:
    """Uuden version käynnistysparametrit: samat kuin nyt (esim. --port), ei selainta, odota vanhan sulkeutumista."""
    keep = [a for a in sys.argv[1:] if a not in ("--wait-for-exit", "--no-browser")]
    return " ".join(shlex.quote(a) for a in keep + ["--no-browser", "--wait-for-exit"])


class Updater:
    def __init__(self, mon):
        self.mon = mon
        self.wake = asyncio.Event()
        self.state_file = mon.cfg_path.parent / "update_state.json"
        self.kind = install_kind()
        self.state = {"current": __version__, "kind": self.kind, "checked": None, "latest": None, "available": False,
                      "url": RELEASES_PAGE, "notes": "", "published": None, "asset": None,
                      "auto": False, "error": None, "busy": False, "progress": None, "message": ""}
        self._release = None
        try:
            saved = json.loads(self.state_file.read_text("utf-8"))
            if saved.get("current") == __version__:
                self.state.update({k: saved.get(k) for k in ("checked", "latest", "url", "notes", "published", "asset")})
                self._evaluate()
        except Exception:  # noqa: BLE001
            pass

    # ---------------- tila ----------------
    def snapshot(self) -> dict:
        return dict(self.state)

    def _set(self, **kw):
        self.state.update(kw)
        self.mon.touch()

    def _evaluate(self):
        latest = self.state.get("latest")
        cur, new = parse_version(__version__), parse_version(latest or "")
        available = bool(cur and new and new > cur)
        self.state["available"] = available
        self.state["auto"] = available and self.kind in AUTO_INSTALL and bool(self.state.get("asset"))
        if self.kind == "macos" and available:
            b = mac_bundle()
            self.state["auto"] = self.state["auto"] and bool(b and os.access(b.parent, os.W_OK))

    def _save(self):
        try:
            keep = {k: self.state.get(k) for k in ("current", "checked", "latest", "url", "notes", "published", "asset")}
            self.state_file.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # ---------------- tarkistus ----------------
    async def run(self):
        await asyncio.sleep(FIRST_DELAY)
        while True:
            last = self.state.get("checked") or 0
            if self.mon.cfg.get("update_check", True) and time.time() - last >= CHECK_EVERY:
                await self.check()
            try:
                await asyncio.wait_for(self.wake.wait(), 3600)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def check(self) -> dict:
        try:
            async with http_session(30) as s:
                try:
                    async with s.get(RELEASES_API, headers={"Accept": "application/vnd.github+json"}) as r:
                        r.raise_for_status()
                        releases = await r.json(content_type=None)
                except Exception as e:  # noqa: BLE001  (esim. 403 kyselyraja)
                    log.info("GitHub API ei vastannut (%s) – luetaan releases.atom", e)
                    async with s.get(RELEASES_ATOM) as r:
                        r.raise_for_status()
                        releases = releases_from_atom(await r.text())
            rel = pick_latest(releases, __version__)
            self._release = rel
            if rel:
                self.state.update(latest=rel["tag_name"].lstrip("v"), url=rel.get("html_url") or RELEASES_PAGE,
                                  notes=(rel.get("body") or "")[:4000], published=rel.get("published_at"),
                                  asset=find_asset(rel, self.kind))
            self.state.update(checked=time.time(), error=None)
            self._evaluate()
            self._save()
            if self.state["available"]:
                log.info("Uusi versio saatavilla: %s (nyt %s)", self.state["latest"], __version__)
        except Exception as e:  # noqa: BLE001
            self.state.update(checked=time.time(), error=tr("Päivityksen tarkistus epäonnistui: {e}", e=friendly_error(e)))
            log.warning("%s", self.state["error"])
        self.mon.touch()
        return self.snapshot()

    # ---------------- asennus ----------------
    async def install(self):
        if self.state["busy"]:
            raise RuntimeError(tr("Päivitys on jo käynnissä"))
        if not self.state["available"]:
            raise RuntimeError(tr("Uutta versiota ei ole"))
        asset = self.state.get("asset")
        if not asset or self.kind not in AUTO_INSTALL:
            raise RuntimeError(tr("Tälle alustalle ei ole päivityspakettia"))
        self._set(busy=True, progress=0.0, error=None, message="download")
        try:
            tmp = Path(tempfile.mkdtemp(prefix="bmmonitor-update-"))
            path = tmp / asset["name"]
            await self._download(asset["url"], path, asset.get("size") or 0)
            await self._verify(path)
            self._set(message="install", progress=1.0)
            await asyncio.get_running_loop().run_in_executor(None, self._launch, path, tmp)
            self._set(message="restart")
            log.info("Päivitys %s asennetaan, ohjelma käynnistyy uudelleen", self.state["latest"])
            asyncio.get_running_loop().call_later(1.0, self.mon.shutdown_event.set)
        except Exception as e:  # noqa: BLE001
            log.warning("Päivitys epäonnistui: %s", e)
            self._set(busy=False, progress=None, message="", error=friendly_error(e))
            raise

    async def _download(self, url: str, path: Path, size: int):
        async with http_session(900) as s:
            async with s.get(url) as r:
                r.raise_for_status()
                total = size or int(r.headers.get("Content-Length") or 0)
                done, last = 0, 0.0
                with open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        if total and time.time() - last > 0.3:
                            last = time.time()
                            self._set(progress=min(0.99, done / total))

    async def _verify(self, path: Path):
        rel = self._release
        sums_url = None
        for a in (rel or {}).get("assets", []):
            if a.get("name") == "SHA256SUMS.txt":
                sums_url = a["browser_download_url"]
        if not sums_url:   # tila ladattu tiedostosta: rakennetaan osoite julkaisun tagista
            sums_url = f"https://github.com/{REPO}/releases/download/v{self.state['latest']}/SHA256SUMS.txt"
        async with http_session(60) as s:
            async with s.get(sums_url) as r:
                r.raise_for_status()
                sums = parse_sums(await r.text())
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if sums.get(path.name) != h.hexdigest():
            raise RuntimeError(tr("Tarkistussumma ei täsmää – päivitystä ei asennettu"))

    def _launch(self, path: Path, tmp: Path):
        pid = os.getpid()
        if self.kind == "windows-setup":
            # Asennusohjelma avataan näkyvänä: käyttäjä vahvistaa asennuksen, ohjelma käynnistyy sen jälkeen
            flags = 0x00000200   # CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(path), "/UPDATE"], creationflags=flags, close_fds=True, env=clean_env())
            return
        if self.kind == "macos":
            bundle = mac_bundle()
            mnt = tmp / "mnt"
            mnt.mkdir()
            subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mnt), str(path)],
                           check=True, capture_output=True, env=clean_env())
            apps = list(mnt.glob("*.app"))
            if not apps:
                raise RuntimeError("dmg: .app not found")
            script = MAC_SCRIPT.format(pid=pid, src=shlex.quote(str(apps[0])), dst=shlex.quote(str(bundle)),
                                       old=shlex.quote(str(bundle) + ".old"), mnt=shlex.quote(str(mnt)),
                                       args=relaunch_args())
        else:
            with tarfile.open(path) as t:
                member = next(m for m in t.getmembers() if m.isfile() and m.name.endswith("/monitor-for-brandmeister"))
                member.name = "monitor-for-brandmeister.new"
                t.extract(member, tmp)
            exe = Path(sys.executable).resolve()
            script = LINUX_SCRIPT.format(pid=pid, src=shlex.quote(str(tmp / "monitor-for-brandmeister.new")),
                                         dst=shlex.quote(str(exe)), old=shlex.quote(str(exe) + ".old"),
                                         args=relaunch_args())
        sh = tmp / "update.sh"
        sh.write_text(script, encoding="utf-8")
        log_path = self.mon.cfg_path.parent / "update.log"
        with open(log_path, "ab") as lf:
            subprocess.Popen(["/bin/sh", str(sh)], start_new_session=True, close_fds=True, env=clean_env(),
                             stdin=subprocess.DEVNULL, stdout=lf, stderr=lf)


# Odota että ohjelma on sulkeutunut, vaihda uusi versio paikalleen (palauta vanha virheessä), käynnistä.
MAC_SCRIPT = """#!/bin/sh
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
rm -rf {old}
if mv {dst} {old} && ditto {src} {dst}; then rm -rf {old}; else rm -rf {dst}; mv {old} {dst}; fi
hdiutil detach {mnt} -quiet || true
open -n {dst} --args {args}
"""

LINUX_SCRIPT = """#!/bin/sh
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
chmod 755 {src}
if mv {dst} {old} && mv {src} {dst}; then rm -f {old}; else mv {old} {dst}; fi
nohup {dst} {args} >/dev/null 2>&1 &
echo "päivitys valmis $(date)"
"""
