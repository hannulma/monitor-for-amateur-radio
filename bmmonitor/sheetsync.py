"""Yhteys check-in-taulukon Apps Script -verkkosovellukseen (sheets/Code.gs).

Paritus: parituskoodi -> ylläpitäjän oma avain. Muut pyynnöt allekirjoitetaan avaimella
(HMAC-SHA256: kutsu, aikaleima, kertakäyttöinen tunniste, toiminto ja sisältö); avainta ei lähetetä.
Apps Script vastaa POST-pyyntöön uudelleenohjauksella (302), jonka vastaus haetaan GET-pyynnöllä.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlparse

from .i18n import tr
from .messaging import friendly_error, http_session

_URL = re.compile(r"^https://script\.google(usercontent)?\.com/macros/s/[A-Za-z0-9_-]+/exec$")


class SheetError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def valid_url(url: str, allow_test: bool = False) -> bool:
    url = (url or "").strip()
    if _URL.match(url):
        return True
    return allow_test and url.startswith("http://127.0.0.1:")


def sign(key: str, call: str, ts: int, nonce: str, action: str, data: str) -> str:
    msg = "\n".join([call, str(ts), nonce, action, data])
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


async def _post(url: str, body: dict) -> dict:
    try:
        async with http_session(30) as s:
            async with s.post(url, data=json.dumps(body), headers={"Content-Type": "text/plain"},
                              allow_redirects=False) as r:
                if r.status in (301, 302, 303, 307, 308):
                    loc = r.headers.get("Location", "")
                    async with s.get(loc) as r2:
                        text, status = await r2.text(), r2.status
                else:
                    text, status = await r.text(), r.status
    except Exception as e:  # noqa: BLE001
        raise SheetError(tr("Taulukkoon ei saatu yhteyttä: {e}", e=friendly_error(e)), "network") from e
    try:
        res = json.loads(text)
    except ValueError:
        hint = tr("Tarkista skriptin osoite ja että verkkosovelluksen käyttöoikeus on \"Kuka tahansa\".")
        raise SheetError(tr("Taulukko vastasi odottamattomasti (HTTP {s}). {h}", s=status, h=hint), "response") from None
    if not res.get("ok"):
        code = res.get("code") or "error"
        msg = {"auth": tr("Tunnistus epäonnistui – yhdistä uudelleen uudella parituskoodilla"),
               "clock": tr("Tietokoneen kello on väärässä – korjaa kello ja yritä uudelleen"),
               "rate": tr("Liikaa pyyntöjä – odota hetki")}.get(code) or res.get("error") or tr("Taulukko hylkäsi pyynnön")
        raise SheetError(msg, code)
    return res


async def pair(url: str, code: str) -> tuple[str, str]:
    res = await _post(url, {"op": "pair", "code": code.strip()})
    return res["call"], res["key"]


class SheetClient:
    def __init__(self, url: str, call: str, key: str):
        self.url, self.call, self.key = url, call, key

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc

    async def request(self, action: str, data: dict | None = None) -> dict:
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        payload = json.dumps(data or {}, ensure_ascii=False)
        body = {"call": self.call, "ts": ts, "nonce": nonce, "action": action, "data": payload,
                "sig": sign(self.key, self.call, ts, nonce, action, payload)}
        return await _post(self.url, body)
