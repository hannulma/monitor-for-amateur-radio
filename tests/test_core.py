"""Perustestit: ajetaan GitHub Actionsissa Windowsilla, macOS:llä ja Linuxilla (ei verkkoyhteyksiä)."""
import asyncio
import json
import socket
import time
import urllib.request

import pytest

from bmmonitor import APP_ID, __version__
from bmmonitor.app import Matcher, parse_patterns
from bmmonitor.features import Commands
from bmmonitor.messaging import aprs_passcode, clean_text, parse_feed, split_message
from bmmonitor.services import (distance_km, latlon_to_locator, locator_to_latlon, parse_location,
                                point_in_polygon, short_traffic, short_warning, sun_elevation)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Asetukset ja lokit väliaikaiseen kansioon kaikilla alustoilla."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


def test_patterns_and_matcher():
    pats = parse_patterns("244*, 91 2441?")
    assert pats == ["244*", "91", "2441?"]
    m = Matcher(pats)
    assert m.match(24420) == "244*"
    assert m.match(91) == "91"
    assert m.match(24415) == "244*"
    assert m.match(3100) is None
    with pytest.raises(ValueError):
        parse_patterns("abc")


def test_aprs_helpers():
    assert aprs_passcode("N0CALL") == 13023
    assert aprs_passcode("n0call-13") == 13023
    parts = split_message("x" * 150, 2)
    assert len(parts) == 2 and all(len(p) <= 67 for p in parts)
    assert split_message("lyhyt", 2) == ["lyhyt"]
    assert clean_text("<b>A</b> | B ~ {C}", False) == "A / B - (C}"
    assert clean_text("äö", True) == "ao"


def test_locator_and_geo():
    lat, lon = locator_to_latlon("KP20LE")
    assert abs(lat - 60.19) < 0.05 and abs(lon - 24.96) < 0.05
    assert latlon_to_locator(60.1699, 24.9384) == "KP20LE"
    assert parse_location("60,17 24,94") == (60.17, 24.94)
    assert 160 < distance_km(60.17, 24.94, 61.5, 23.76) < 170
    sq = [(0, 0), (0, 1), (1, 1), (1, 0)]
    assert point_in_polygon(0.5, 0.5, sq) and not point_in_polygon(1.5, 0.5, sq)
    # keskikesän keskipäivä Helsingissä: aurinko korkealla
    noon = time.mktime((2026, 6, 21, 13, 20, 0, 0, 0, -1))
    assert sun_elevation(60.17, 24.94, noon) > 45


def test_rss_parse():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Testi</title>
      <item><title>Uusin</title><guid>2</guid></item><item><title>Vanha</title><guid>1</guid></item></channel></rss>"""
    title, items = parse_feed(rss)
    assert title == "Testi" and [i["id"] for i in items] == ["2", "1"]


def test_short_traffic():
    props = {"situationType": "TRAFFIC_ANNOUNCEMENT", "trafficAnnouncementType": "GENERAL"}
    ann = {
        "title": "Tie 101, eli Kehä I, Espoo. Liikennetiedote. ",
        "location": {"description": "Tie 101, eli Kehä I, Espoo.\nTarkempi paikka: Kalevalantie."},
        "locationDetails": {"roadAddressLocation": {"primaryPoint": {
            "municipality": "Espoo", "roadAddress": {"road": 101}, "roadName": "Kehä I",
            "alertCLocation": {"name": "Kalevalantie"}}}},
        "features": [{"name": "Liikennevalot"}, {"name": "Epäkunnossa"}],
    }
    assert short_traffic(props, ann) == "Tie 101 Espoo, Kalevalantie: Liikennevalot, epäkunnossa"


def test_short_warning():
    w = {"event": "Liikennesää", "color": "yellow", "msgType": "Update",
         "params": {"eventCause": "snowOrSleet", "probability": "30"},
         "onset": time.time() - 60, "expires": None}
    s = short_warning(w)
    assert s.startswith("Keltainen liikennesää: lumi/räntä, nyt") and s.endswith("(30 %)")
    assert short_warning({**w, "msgType": "Cancel"}) == "POISTETTU: Keltainen liikennesää"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_ping_and_commands():
    """Käynnistää web-palvelimen ilman BM-yhteyttä ja tarkistaa rajapinnan sekä radiokomennot."""
    from aiohttp import web

    from bmmonitor.app import Monitor, config_dir, make_app

    async def run():
        mon = Monitor(config_dir() / "config.json")
        mon.update_config({"messaging": {"login": "OH2ABC-13", "recipients": "OH2ABC-7"}})
        port = _free_port()
        runner = web.AppRunner(make_app(mon))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        loop = asyncio.get_running_loop()
        body = await loop.run_in_executor(None, lambda: urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/ping", timeout=5).read())
        await runner.cleanup()
        info = json.loads(body)
        assert info == {"app": APP_ID, "version": __version__}
        c = Commands(mon)
        assert c.handle("OH2ABC-7", "apua").startswith("Komennot:")
        assert "ei liikennettä" in c.handle("OH2ABC-7", "TG244")
        assert c.handle("OH9EVIL", "TG244") is None   # ei sallittu lähettäjä

    asyncio.run(run())
