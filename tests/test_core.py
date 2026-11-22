"""Perustestit: ajetaan GitHub Actionsissa Windowsilla, macOS:llä ja Linuxilla (ei verkkoyhteyksiä)."""
import asyncio
import calendar
import json
import re
import socket
import time
import urllib.request
from pathlib import Path

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
    # keskikesän aurinkokeskipäivä Helsingissä (~10.20 UTC): aurinko n. 53° korkeudella.
    # UTC-aika, jotta testi ei riipu koneen aikavyöhykkeestä.
    noon = calendar.timegm((2026, 6, 21, 10, 20, 0))
    assert 50 < sun_elevation(60.17, 24.94, noon) < 56
    midnight = calendar.timegm((2026, 12, 21, 22, 20, 0))
    assert sun_elevation(60.17, 24.94, midnight) < -40


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


def test_language_switch():
    from bmmonitor import i18n
    from bmmonitor.features import Commands  # noqa: F401  (tuonti varmistaa, ettei kierrosta ole)
    try:
        i18n.set_lang("en")
        assert i18n.tr("Anna puheryhmä") == "Enter a talkgroup"
        assert i18n.tr("Virheellinen kutsu tai DMR-ID: {x}", x="X") == "Invalid callsign or DMR ID: X"
        assert i18n.num(1.25, 2) == "1.25" and i18n.bearing(1) == "NE"
        i18n.set_lang("fi")
        assert i18n.tr("Anna puheryhmä") == "Anna puheryhmä" and i18n.num(1.25, 2) == "1,25"
        # jokaisen käännöksen paikkamerkit täsmäävät suomenkielisen avaimen kanssa
        ph = re.compile(r"\{(\w+)\}")
        for fi, en in i18n.EN.items():
            assert set(ph.findall(fi)) == set(ph.findall(en)), fi
    finally:
        i18n.set_lang("fi")


def test_ui_dictionary_placeholders():
    """static/i18n.js: käännösten paikkamerkit ({n} jne.) vastaavat suomenkielisiä avaimia."""
    js = (Path(__file__).resolve().parents[1] / "bmmonitor" / "static" / "i18n.js").read_text("utf-8")
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*\n?\s*"((?:[^"\\]|\\.)*)"', js)
    assert len(pairs) > 300
    ph = re.compile(r"\{(\w+)\}")
    for fi, en in pairs:
        assert set(ph.findall(fi)) == set(ph.findall(en)), fi


def test_update_versions():
    from bmmonitor import updater as U
    pv = U.parse_version
    assert pv("0.9.0b1") < pv("0.9.0rc1") < pv("0.9.0") < pv("0.9.1a1") < pv("1.0")
    assert pv("v0.8.0b3") == (0, 8, 0, 1, 3) and pv("latest") is None
    rels = [{"tag_name": "v1.0.0b1"}, {"tag_name": "v0.9.0"}, {"tag_name": "v2.0.0", "draft": True}, {"tag_name": "nightly"}]
    assert U.pick_latest(rels, "0.8.0")["tag_name"] == "v0.9.0"          # vakaa käyttäjä: ei betoja
    assert U.pick_latest(rels, "0.8.0b3")["tag_name"] == "v1.0.0b1"      # beta-käyttäjä: myös betat
    sums = U.parse_sums("a" * 64 + "  file.dmg\n" + "B" * 64 + " *other.exe\nroska\n")
    assert sums == {"file.dmg": "a" * 64, "other.exe": "b" * 64}
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><updated>2026-11-19T17:00:00Z</updated>
        <link rel="alternate" href="https://github.com/hannulma/monitor-for-brandmeister/releases/tag/v0.9.0b1"/></entry>
      <entry><link rel="alternate" href="https://github.com/hannulma/monitor-for-brandmeister/releases/tag/nightly"/></entry>
    </feed>"""
    rel = U.releases_from_atom(atom)
    assert [r["tag_name"] for r in rel] == ["v0.9.0b1"]
    a = U.find_asset(rel[0], "macos")
    assert a["name"] == "MonitorForBrandMeister-0.9.0b1-macos-universal.dmg"
    assert a["url"].endswith("/releases/download/v0.9.0b1/MonitorForBrandMeister-0.9.0b1-macos-universal.dmg")
    assert U.find_asset(rel[0], "windows-setup")["name"].endswith("-windows-setup.exe")


def test_checkin_round(tmp_path):
    """Check-in: saapumisjärjestys pysyy, päällekkäisyys, kirjaus, numerointi ja vienti."""
    from bmmonitor.checkin import CheckIn, clean_city, country_prefix

    class Mon:
        cfg_path = tmp_path / "config.json"
        cfg = {}
        def touch(self): pass
        def save_config(self): pass
        def tg_name(self, tg): return "Finland"

    assert clean_city("Jarvenpaa, KP20NK") == "Jarvenpaa" and clean_city("Oulu") == "Oulu"
    assert country_prefix(244) == "244" and country_prefix(91) is None
    ci = CheckIn(Mon(), api=None)
    ci.start(244, "177", "20.11.2026", "oh2abc")
    t = time.time()

    def ev(call, sid, src, start, stop=0, ctx=244207):
        ci.on_event({"SourceCall": call, "SourceName": "X", "SourceID": src, "DestinationID": 244,
                     "SessionID": sid, "Start": start, "Stop": stop, "ContextID": ctx,
                     "Event": "Session-Stop" if stop else "Session-Start"})

    ev("OH2AAA", "a", 2440001, t)
    ev("ES1BBB", "b", 2480001, t + 1)                 # avaa toisen päälle -> päällekkäin
    ev("OH2AAA", "a", 2440001, t, t + 3)
    ev("OH3CCC", "c", 2440002, t + 5, ctx=2440002001)
    ev("OH2AAA", "d", 2440001, t + 9)                 # uusi avaus: sama rivi, ei siirry
    ev("OH9ZZZ", "z", 2440009, t - 600)               # ennen kierrosta -> ei mukaan
    ev("OH4XXX", "x", 2440004, t, 0)
    ci.on_event({"SourceCall": "OH5TG", "DestinationID": 2441, "Start": t, "SessionID": "o"})   # eri TG
    calls = [s["call"] for s in ci.s["stations"]]
    assert calls == ["OH2AAA", "ES1BBB", "OH3CCC", "OH4XXX"]
    st = {s["call"]: s for s in ci.s["stations"]}
    assert st["OH2AAA"]["keys"] == 2 and st["ES1BBB"]["overlap"] == 1
    assert st["ES1BBB"]["via"] == "Ulkomaat" and st["OH3CCC"]["via"] == "Hotspot"

    ci.devices[244207] = {"call": "OH2RUP", "city": "Jarvenpaa, KP20NK"}
    ci._fill_device(st["OH2AAA"])
    assert st["OH2AAA"]["via"] == "OH2RUP Jarvenpaa" and st["OH2AAA"]["dev"] == "OH2RUP, Jarvenpaa"

    for c in ("OH3CCC", "OH2AAA", "ES1BBB"):
        ci.log_station(c)
    assert [(e["nro"], e["call"], e["oper"]) for e in ci.s["log"]] == [(1, "OH3CCC", "OH2ABC"), (2, "OH2AAA", "OH2ABC"), (3, "ES1BBB", "OH2ABC")]
    ci.log_station("OH2AAA")                           # kahdesti -> ei uutta riviä
    assert len(ci.s["log"]) == 3
    with pytest.raises(ValueError):
        ci.log_station("OH9NOPE")
    ci.edit("OH2AAA", "info", "mobiili")
    ci.edit("OH2AAA", "kunta", "Tuusula")
    ci.mark_copied(3)
    ci.unlog("OH3CCC")                                 # numerot tiivistyvät, kopioidut merkitään uudelleen
    assert [(e["nro"], e["call"]) for e in ci.s["log"]] == [(1, "OH2AAA"), (2, "ES1BBB")] and ci.s["copied_upto"] == 0
    ci.devices[244207] = {"call": "OH2RUP", "city": "Kerava"}
    ci._fill_device(st["OH2AAA"])                      # käsin muutettu kunta säilyy
    tsv = ci.export("tsv")
    assert tsv.splitlines()[0] == "1\tOH2AAA\tOH2RUP Kerava\tTuusula\tmobiili\tOH2ABC"
    assert ci.export("tsv", since=1).count("\n") == 0

    ci.stop()
    files = list((tmp_path / "checkin").glob("*.csv"))
    assert len(files) == 1 and "OH2AAA" in files[0].read_text("utf-8-sig")
    ci2 = CheckIn(Mon(), api=None)                     # tila säilyy uudelleenkäynnistyksessä
    assert [e["call"] for e in ci2.s["log"]] == ["OH2AAA", "ES1BBB"] and not ci2.s["active"]
    ci2.start(244)
    assert ci2.s["round"] == "178" and ci2.s["log"] == []


def test_sheet_signature_matches_apps_script():
    """Allekirjoitus vastaa Apps Scriptin laskemaa (vektori laskettu Node.js:n HMAC-SHA256:lla, UTF-8)."""
    from bmmonitor.sheetsync import sign, valid_url
    assert sign("k" * 64, "OH2ABC", 1795000000, "abcdef0123456789", "edit", '{"value":"Järvenpää"}') == "c3cba856808a6fead5bdc2d84c3c0e4f4a6757dac07e3998c93a08abfa5e8131"
    assert valid_url("https://script.google.com/macros/s/AKfycbx_abc-123/exec")
    assert not valid_url("https://evil.example.com/macros/s/x/exec") and not valid_url("http://127.0.0.1:8840/exec")
    assert valid_url("http://127.0.0.1:8840/exec", allow_test=True)
