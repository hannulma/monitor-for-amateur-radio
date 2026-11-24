"""Oma ikkuna (pywebview) ja kuvake ilmaisinalueella (Windows) tai valikkorivillä (macOS).

Paikallinen palvelin pyörii omassa säikeessään kuten ennenkin; tämä moduuli näyttää saman käyttöliittymän
käyttöjärjestelmän omalla selainmoottorilla (Windows: WebView2, macOS: WKWebView). Ikkunan sulkeminen
piilottaa ikkunan ja ohjelma jatkaa taustalla; kokonaan ohjelma suljetaan kuvakkeen valikosta,
Asetukset → Sulje ohjelma -painikkeesta tai macOS:ssä Cmd+Q:lla.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from .i18n import tr

log = logging.getLogger("bmmonitor.desktop")
ICONS = Path(__file__).resolve().parent / "static" / "icons"
TITLE = "Monitor for Amateur Radio"

_available: bool | None = None
_DESK: "Desktop | None" = None      # macOS-valikon ja Dockin käsittelijät tarvitsevat viittauksen


def available() -> bool:
    """Onko oma ikkuna mahdollinen tällä koneella (Windows/macOS ja pywebview asennettu)?"""
    global _available
    if _available is None:
        _available = False
        if sys.platform in ("win32", "darwin"):
            try:
                import webview  # noqa: F401
                _available = True
            except Exception as e:  # noqa: BLE001
                log.info("Omaa ikkunaa ei voi käyttää: %s", e)
    return _available


class Api:
    """Käyttöliittymän JavaScriptistä kutsuttavat toiminnot: window.pywebview.api.<nimi>(...)."""

    def __init__(self, desk: "Desktop"):
        self._desk = desk

    def open_url(self, url):
        url = str(url or "")
        if re.match(r"^https?://", url):
            webbrowser.open(url)
            return True
        return False

    def open_browser(self):
        self._desk.open_browser()
        return True

    def notify(self, title, body=""):
        self._desk.notify(str(title or "")[:120], str(body or "")[:300])
        return True

    def set_theme(self, theme):
        """Teema vaihtui: muistetaan ikkunan taustaväriksi seuraavaa käynnistystä varten."""
        self._desk.bg = "#f3f5f7" if theme == "light" else "#0f1419"
        return True

    def save_file(self, path):
        """Lataa paikallisen palvelimen tiedoston (esim. check-in-CSV) ja tallentaa sen käyttäjän valitsemaan paikkaan."""
        return self._desk.save_file(str(path or ""))


class Desktop:
    def __init__(self, start_hidden: bool = False, light: bool = False):
        self.start_hidden = start_hidden
        self.bg = "#f3f5f7" if light else "#0f1419"     # ikkunan väri ennen kuin sivu on piirretty
        self.url: str | None = None
        self.window = None
        self.quitting = False
        self.ready = threading.Event()       # palvelin käynnissä (url asetettu) tai käynnistys peruttu
        self.on_quit = None                  # kutsutaan, kun käyttäjä sulkee ohjelman ikkunan puolelta
        self._tray = None
        self._hint_shown = False
        self._mac_refs = []

    # --- palvelimen puolelta ---
    def server_started(self, url: str, on_quit) -> None:
        self.url, self.on_quit = url, on_quit
        self.ready.set()

    def cancel(self) -> None:
        self.ready.set()

    def server_stopped(self) -> None:
        """Palvelin sammui (Sulje ohjelma -painike, päivitys): suljetaan ikkuna ja kuvake."""
        self.quitting = True
        self._stop_tray()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception as e:  # noqa: BLE001
                log.debug("Ikkunan sulkeminen: %s", e)

    # --- ikkuna ---
    def run(self, storage: Path) -> None:
        """Avaa ikkunan ja pyörittää käyttöliittymäsilmukkaa pääsäikeessä, kunnes ohjelma suljetaan."""
        global _DESK
        import webview
        _DESK = self
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        self.window = webview.create_window(
            TITLE, self.url, js_api=Api(self), width=1380, height=900, min_size=(760, 520),
            hidden=self.start_hidden, background_color=self.bg, text_select=True, zoomable=True)
        self.window.events.closing += self._on_closing
        if self.quitting:          # palvelin ehti jo sammua
            return
        if sys.platform == "darwin":
            self._mac_patch_delegate()
        storage.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            _win_log_unhandled()
        # Windows ottaa ikkunan kuvakkeen .exe-tiedostosta (PNG ei kelpaa WinFormsin Iconiksi).
        icon = None if sys.platform == "win32" else str(ICONS / "icon-256.png")
        webview.start(self._gui_started, private_mode=False, storage_path=str(storage), icon=icon)

    def _gui_started(self):
        try:
            if sys.platform == "win32":
                self._win_tray()
                self._win_session_hook()
            elif sys.platform == "darwin":
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._mac_status_item)
        except Exception:  # noqa: BLE001
            log.exception("Ilmaisinalueen kuvaketta ei voitu luoda")

    def _on_closing(self):
        if self.quitting:
            return True
        # Ikkunan sulkeminen vain piilottaa ikkunan: seuranta ja hälytykset jatkuvat taustalla.
        threading.Thread(target=self._hide, daemon=True).start()
        return False

    def _hide(self):
        try:
            self.window.hide()
        except Exception as e:  # noqa: BLE001
            log.debug("hide: %s", e)
        if not self._hint_shown:
            self._hint_shown = True
            where = tr("valikkoriviltä") if sys.platform == "darwin" else tr("ilmaisinalueelta")
            self.notify(TITLE, tr("Ohjelma toimii taustalla. Avaa ikkuna kuvakkeesta {where}.", where=where))

    def show(self) -> None:
        if self.window is None:
            return
        try:
            self.window.show()
            self.window.restore()
        except Exception as e:  # noqa: BLE001
            log.debug("show: %s", e)
        if sys.platform == "darwin":
            try:
                import AppKit
                from PyObjCTools import AppHelper
                AppHelper.callAfter(lambda: AppKit.NSApp.activateIgnoringOtherApps_(True))
            except Exception:  # noqa: BLE001
                pass

    def open_browser(self) -> None:
        if self.url:
            webbrowser.open(self.url)

    def quit(self) -> None:
        """Käyttäjä sulki ohjelman kuvakkeen valikosta: palvelin sammutetaan, ikkuna suljetaan sen jälkeen."""
        self.quitting = True
        if self.on_quit:
            self.on_quit()
        else:
            self.server_stopped()

    # --- ilmoitukset ---
    def notify(self, title: str, body: str) -> None:
        try:
            if sys.platform == "darwin":
                q = lambda s: json.dumps(s, ensure_ascii=False)  # noqa: E731  AppleScript-merkkijono lainausmerkkeineen
                subprocess.Popen(["osascript", "-e", f"display notification {q(body)} with title {q(title)}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif self._tray is not None:
                self._tray.notify(body or " ", title)
        except Exception as e:  # noqa: BLE001
            log.debug("Ilmoitus epäonnistui: %s", e)

    # --- tiedoston tallennus ---
    def save_file(self, path: str):
        if not path.startswith("/api/") or not self.url:
            return {"ok": False}
        import webview
        try:
            with urllib.request.urlopen(self.url.rstrip("/") + path, timeout=30) as r:
                data = r.read()
                cd = r.headers.get("Content-Disposition", "")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        m = re.search(r'filename="?([^";]+)"?', cd)
        name = m.group(1) if m else "export.csv"
        res = self.window.create_file_dialog(getattr(webview, "FileDialog").SAVE, save_filename=name)
        if not res:
            return {"ok": False, "cancelled": True}
        dest = res if isinstance(res, str) else res[0]
        Path(dest).write_bytes(data)
        return {"ok": True, "path": str(dest)}

    # --- Windows: ilmaisinalue ---
    def _win_tray(self):
        import pystray
        from PIL import Image
        menu = pystray.Menu(
            pystray.MenuItem(tr("Avaa"), lambda *_: self.show(), default=True),
            pystray.MenuItem(tr("Avaa selaimessa"), lambda *_: self.open_browser()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(tr("Sulje ohjelma"), lambda *_: self.quit()))
        self._tray = pystray.Icon("monitor-for-amateur-radio", Image.open(ICONS / "icon-64.png"), TITLE, menu)
        threading.Thread(target=self._tray.run, name="tray", daemon=True).start()

    def _win_session_hook(self):
        """Windowsin sammutus tai uloskirjautuminen: ikkunaa ei piiloteta vaan ohjelma suljetaan."""
        try:
            from Microsoft.Win32 import SystemEvents  # pythonnet, ladattu pywebviewin mukana
            SystemEvents.SessionEnding += lambda *_: setattr(self, "quitting", True)
        except Exception as e:  # noqa: BLE001
            log.debug("SessionEnding: %s", e)
        try:
            from System.Windows.Forms import CloseReason
            from webview.platforms.winforms import BrowserView
            form = BrowserView.instances.get(self.window.uid)

            def closing(_sender, args):
                if args.CloseReason in (CloseReason.WindowsShutDown, CloseReason.TaskManagerClosing):
                    self.quitting = True
                    args.Cancel = False
            if form is not None:
                form.FormClosing += closing
        except Exception as e:  # noqa: BLE001
            log.debug("FormClosing: %s", e)

    def _stop_tray(self):
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:  # noqa: BLE001
                pass
            self._tray = None
        if self._mac_refs:
            try:
                import AppKit
                AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._mac_refs[0])
            except Exception:  # noqa: BLE001
                pass
            self._mac_refs = []

    # --- macOS: valikkorivi ja Dock ---
    def _mac_patch_delegate(self):
        """Cmd+Q, valikon Lopeta ja uloskirjautuminen sulkevat ohjelman; Dock-kuvakkeen klikkaus avaa ikkunan."""
        try:
            import objc
            from webview.platforms.cocoa import BrowserView

            def should_terminate(_self, _app):
                # Ei lopeteta heti (palvelimen tila jäisi tallentamatta): sammutetaan palvelin hallitusti,
                # jolloin ikkuna suljetaan ja käyttöliittymäsilmukka päättyy itsestään.
                if _DESK is None or _DESK.window is None:
                    return 1   # NSTerminateNow
                if not _DESK.quitting:
                    threading.Thread(target=_DESK.quit, daemon=True).start()
                return 0       # NSTerminateCancel

            def reopen(_self, _app, _flag):
                if _DESK is not None:
                    _DESK.show()
                return True
            cls = BrowserView.AppDelegate
            try:
                sig = cls.applicationShouldTerminate_.signature    # pyobjc:n oma tyyppi (I tai Q)
            except AttributeError:
                sig = b"Q@:@"
            for fn, sel, sg in ((should_terminate, b"applicationShouldTerminate:", sig),
                                (reopen, b"applicationShouldHandleReopen:hasVisibleWindows:", b"Z@:@Z")):
                try:
                    objc.classAddMethods(cls, [objc.selector(fn, selector=sel, signature=sg)])
                except Exception as e:  # noqa: BLE001
                    log.warning("macOS-käsittelijä %s: %s", sel.decode(), e)
        except Exception:  # noqa: BLE001
            log.exception("macOS-sovelluksen käsittelijöitä ei voitu asettaa")

    def _mac_status_item(self):
        import AppKit
        target = _mac_target_class().alloc().init()
        item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(ICONS / "tray-template@2x.png"))
        if img is not None:
            img.setSize_((18, 18))
            img.setTemplate_(True)
            item.button().setImage_(img)
        else:
            item.button().setTitle_("MAR")
        item.button().setToolTip_(TITLE)
        menu = AppKit.NSMenu.alloc().init()
        for title, sel in ((tr("Avaa"), "openWindow:"), (tr("Avaa selaimessa"), "openBrowser:"), (None, None),
                           (tr("Sulje ohjelma"), "quitApp:")):
            if title is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            mi.setTarget_(target)
            menu.addItem_(mi)
        item.setMenu_(menu)
        self._mac_refs = [item, target, menu]


def _win_log_unhandled():
    """Käsittelemättömät .NET-poikkeukset lokiin ennen kuin prosessi kaatuu (vianetsintä)."""
    try:
        import clr  # noqa: F401
        from System import AppDomain

        def handler(_sender, e):
            log.error("Käsittelemätön .NET-poikkeus: %s", e.ExceptionObject)
            for h in logging.getLogger().handlers:
                try:
                    h.flush()
                except Exception:  # noqa: BLE001
                    pass
        AppDomain.CurrentDomain.UnhandledException += handler
    except Exception as e:  # noqa: BLE001
        log.debug("UnhandledException-käsittelijä: %s", e)


_MacTarget = None


def _mac_target_class():
    global _MacTarget
    if _MacTarget is None:
        import AppKit

        class MonitorMenuTarget(AppKit.NSObject):
            def openWindow_(self, _sender):
                if _DESK:
                    _DESK.show()

            def openBrowser_(self, _sender):
                if _DESK:
                    _DESK.open_browser()

            def quitApp_(self, _sender):
                if _DESK:
                    threading.Thread(target=_DESK.quit, daemon=True).start()
        _MacTarget = MonitorMenuTarget
    return _MacTarget
