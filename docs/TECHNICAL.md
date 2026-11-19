# Technical notes

Implementation notes for developers. The app is a Python asyncio application (`aiohttp` web server + a
`python-socketio` client) with a single-page UI in `bmmonitor/static/`.

| Module | Purpose |
|---|---|
| `bmmonitor/app.py` | web server, BrandMeister Last Heard client, talkgroup state, REST/WebSocket API, startup |
| `bmmonitor/messaging.py` | APRS-IS client (messages, acks, retries), feeds, message log |
| `bmmonitor/features.py` | heard-station index, friend alerts, device monitor, statistics, radio commands |
| `bmmonitor/services.py` | open-data services (lightning, warnings, space weather, aurora, radiation, traffic) |
| `bmmonitor/bmapi.py` | BrandMeister Halligan API v2 client |
| `bmmonitor/static/index.html` | user interface |
| `bmmonitor/static/player.js` | Hoseline audio player (MessagePack + G.711 μ-law) |

## BrandMeister

**Last Heard** – socket.io at `https://api.brandmeister.network`, path `/lh`, event `mqtt`; no API key.
- After connecting the client must `emit("join", "everything")` – otherwise nothing is sent. Narrower rooms
  exist: `dst_<tg>`, `src_<id>`, `con_<id>`.
- `emit("searchHouse", {"query": null, "amount": 500})` returns roughly the last 500 events (about 3–4 minutes
  of the whole network) as `mqtt` messages with `topic: "LH-Startup"`, followed by `searchHouseComplete`.
  `Start`/`Stop` are strings in history rows.
- `emit("lastUserList", {"talkgroups": [tg]})` → `userHistoryQuery`: users heard in a talkgroup with
  call counts and last-heard time.
- One call produces several sessions (one per device/link), and short key-ups often arrive as a bare
  `Session-Stop` without `Session-Start`; friend alerts therefore identify a call by source ID + start time.
- Data/SMS sessions appear only as metadata (sender, recipient, time, route); message text is not published.

**Halligan API v2** – `https://api.brandmeister.network/v2/`
- Public: `talkgroup`, `registry/byCall/{call}`, `registry/{id}`, `device/byCall?callsign=`, `device/{id}`,
  `device/{id}/profile` (static, dynamic, timed and blocked subscriptions). `last_seen` is UTC.
- With `Authorization: Bearer <key>` (own devices only): `POST /device/{id}/talkgroup {slot, group}`,
  `DELETE /device/{id}/talkgroup/{slot}/{group}`, `GET /device/{id}/action/dropDynamicGroups/{slot}`,
  `…/dropCallRoute/{slot}`, `…/removeContext`. Simplex hotspots use slot 0.
- Requests with a custom `User-Agent` containing the app name returned HTTP 403, so the default client UA is used.

**Hoseline audio** – `wss://hose.brandmeister.network/spotter/?token=test:test`, sub-protocol `spotter`
(the same public interface as the Hoseline web page). Messages are MessagePack arrays:
- client → server: `[1, [tg, …]]` join, `[2, [tg, …]]` leave, `[3]` reset
- server → client: `[11, _, srcId, tg, _]` call start, `[13]` call end, `[20, <960 bytes>]` audio
  (G.711 μ-law, 8 kHz, 120 ms), `[21, "alias"]` talker alias, `[22, dB]` level.
Talkgroups 1–90 are not supported by Hoseline.

## APRS

- APRS-IS login `user CALL-SSID pass <passcode> vers … filter g/BASE*/… b/BASE*/…` – group filter for
  messages addressed to any SSID of the watched callsigns and budlist filter for messages sent by them.
- Messages are sent as `CALL>APZBMM,TCPIP*::ADDRESSEE:text{id}`; up to 3 tries 45 s apart until an `ack`.
  Optional *no-ack* mode sends once without a message ID.
- The app acknowledges only messages addressed to its own login; messages to other SSIDs (e.g. a radio or
  another APRS client) are acknowledged by that station / the BrandMeister gateway.
- BrandMeister APRS gateway: APRS messages to the callsign-SSID configured for a DMR ID in SelfCare are
  delivered as DMR SMS; from the radio, send a private message to the national gateway ID (*country code + 999*,
  e.g. 244999) as `<RECIPIENT> <text>`.

## Open data services

| Service | Endpoint | Notes |
|---|---|---|
| Lightning | FMI WFS `fmi::observations::lightning::simple` (`parameters=cloud_indicator`, bbox, last hour) | `cloud_indicator` 0 = ground flash |
| Weather warnings | `https://alerts.fmi.fi/cap/feed/atom_fi-FI.xml` (CAP 1.2 in Atom) | municipality polygons → point-in-polygon; short text from `event`, `eventCause`, `color`, `onset/expires`, `probability` |
| Space weather | NOAA SWPC `json/planetary_k_index_1m.json`, `products/alerts.json` | flare and K-index warnings parsed from message text |
| Aurora | FMI WFS `fmi::observations::magnetometer::simple` (`MAGNX_PT1M_AVG`) | hourly range of the X component at the nearest station; cloud cover from `fmi::observations::weather::multipointcoverage` (`n_man`) |
| Radiation | FMI WFS `stuk::observations::external-radiation::latest::multipointcoverage` | `DR_PT10M_avg` µSv/h, station names included |
| Road traffic | `https://tie.digitraffic.fi/api/traffic-message/v1/messages` | requires `Accept-Encoding: gzip`; precise place from `alertCLocation.name` of primary/secondary points |

Short message rules (traffic): `<Tie N | street> <municipality>, <place>: <event>, <effects>`; to fit 67 characters
the least important effects are dropped first, then a segment is reduced to its start point, then the place name
is shortened, and finally the precise place is omitted.

## Build and release

`.github/workflows/release.yml` runs when a new `__version__` (no existing `v<version>` tag) reaches `main`, on `v*` tags or manually: PyInstaller builds on `windows-latest` (one-file exe without
console + Inno Setup installer from `installer/windows.iss`), `macos-15-intel` (universal2 `.app` → `.dmg`, built with the python.org universal2 Python and x86_64+arm64 wheels fused by `delocate-merge`),
`ubuntu-22.04` (one-file binary), plus wheel/sdist; all artifacts and `SHA256SUMS.txt` are attached to a GitHub
release. `.github/workflows/ci.yml` runs the test suite on all three operating systems.
