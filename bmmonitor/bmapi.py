"""Brandmeister Halligan API v2 -asiakas (https://api.brandmeister.network/v2/).

Julkiset haut toimivat ilman avainta. Laitteen hallinta (pysyvät TG:t, dynaamisten
tyhjennys, puhelun katkaisu, yhteyden nollaus) vaatii SelfCaressa luodun API-avaimen,
joka lähetetään otsakkeena  Authorization: Bearer <avain>.  BM sallii hallinnan vain
omille laitteille.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import quote

from .messaging import http_session

API = "https://api.brandmeister.network/v2"


class BMApiError(Exception):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


def parse_bm_time(s) -> float | None:
    """BM:n 'last_seen' on UTC-muodossa 'YYYY-MM-DD HH:MM:SS'."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def default_slot(device_id: int) -> int:
    """Hotspotin (9-numeroinen ID) simplex-aikaväli on BM:ssä 0, toistimilla 1/2."""
    return 0 if len(str(device_id)) >= 9 else 2


class BMApi:
    def __init__(self, get_key):
        self.get_key = get_key
        self.cache: dict[str, tuple[float, object]] = {}

    def invalidate(self, prefix: str):
        for k in [k for k in self.cache if k.startswith(prefix)]:
            self.cache.pop(k, None)

    async def request(self, method: str, path: str, body=None, auth=False, ttl=0):
        if method == "GET" and ttl:
            hit = self.cache.get(path)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        headers = {}
        if auth:
            key = (self.get_key() or "").strip()
            if not key:
                raise BMApiError("Toiminto vaatii Brandmeister API-avaimen (Asetukset)", 401)
            headers["Authorization"] = f"Bearer {key}"
        async with http_session(20) as s:
            async with s.request(method, API + path, json=body, headers=headers) as r:
                text = await r.text()
                if r.status in (401, 403) and auth:
                    raise BMApiError("API-avain hylättiin tai laite ei ole sinun (HTTP %d)" % r.status, r.status)
                if r.status == 404:
                    raise BMApiError("Ei löytynyt", 404)
                if r.status >= 400:
                    raise BMApiError(f"BM API HTTP {r.status}: {text[:200]}", r.status)
                try:
                    data = json.loads(text) if text.strip() else None
                except ValueError:
                    data = text
        if method == "GET" and ttl:
            self.cache[path] = (time.time(), data)
        return data

    # ---------------- julkiset ----------------
    async def registry_by_call(self, call: str):
        try:
            return await self.request("GET", f"/registry/byCall/{quote(call.upper())}", ttl=3600) or []
        except BMApiError as e:
            if e.status == 404:
                return []
            raise

    async def registry(self, dmr_id: int):
        try:
            return await self.request("GET", f"/registry/{int(dmr_id)}", ttl=3600)
        except BMApiError as e:
            if e.status == 404:
                return None
            raise

    async def devices_by_call(self, call: str):
        try:
            return await self.request("GET", f"/device/byCall?callsign={quote(call.upper())}", ttl=300) or []
        except BMApiError as e:
            if e.status == 404:
                return []
            raise

    async def device(self, dev_id: int, ttl=30):
        return await self.request("GET", f"/device/{int(dev_id)}", ttl=ttl)

    async def device_profile(self, dev_id: int, ttl=30):
        return await self.request("GET", f"/device/{int(dev_id)}/profile", ttl=ttl)

    # ---------------- avaimella ----------------
    async def add_static(self, dev_id: int, slot: int, group: int):
        r = await self.request("POST", f"/device/{int(dev_id)}/talkgroup",
                               {"slot": int(slot), "group": int(group)}, auth=True)
        self.invalidate(f"/device/{int(dev_id)}")
        return r

    async def delete_static(self, dev_id: int, slot: int, group: int):
        r = await self.request("DELETE", f"/device/{int(dev_id)}/talkgroup/{int(slot)}/{int(group)}", auth=True)
        self.invalidate(f"/device/{int(dev_id)}")
        return r

    async def drop_dynamic(self, dev_id: int, slot: int):
        r = await self.request("GET", f"/device/{int(dev_id)}/action/dropDynamicGroups/{int(slot)}", auth=True)
        self.invalidate(f"/device/{int(dev_id)}")
        return r

    async def drop_call(self, dev_id: int, slot: int):
        return await self.request("GET", f"/device/{int(dev_id)}/action/dropCallRoute/{int(slot)}", auth=True)

    async def reset_connection(self, dev_id: int):
        r = await self.request("GET", f"/device/{int(dev_id)}/action/removeContext", auth=True)
        self.invalidate(f"/device/{int(dev_id)}")
        return r
