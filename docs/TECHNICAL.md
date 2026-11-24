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


## Languages

`bmmonitor/i18n.py` holds the program's own texts (errors, alerts, radio replies) with the Finnish text as the
key: `tr("Virheellinen kutsu: {x}", x=c)`. The UI dictionary is `bmmonitor/static/i18n.js`; the page is written
in Finnish and a `MutationObserver` translates every text node and `placeholder`/`title` attribute, so newly
rendered parts are translated too. Sentences with variable parts use `T("… {n} …", {n})`. The tests check that
placeholders match between the languages.

## Updates

`bmmonitor/updater.py` checks `api.github.com/repos/hannulma/monitor-for-amateur-radio/releases` once a day
(fallback: `releases.atom`, which has no API rate limit). Beta users get pre-releases, stable users only stable
ones. Installation downloads the platform asset, verifies it against `SHA256SUMS.txt` and then:

| Install kind | How it is updated |
|---|---|
| Windows installer (`unins000.exe` next to the exe) | opens the new setup with `/UPDATE` (visible wizard: license, folder and task pages skipped); the user confirms and the `[Run]` entry for `IsUpdate` restarts the app |
| macOS `.app` (writable parent folder) | mounts the dmg, a helper script waits for the app to exit, swaps the bundle with `ditto` (rolls back on failure) and reopens it |
| Linux binary | a helper script swaps the binary and restarts it |
| Windows portable / pip | download link or `pip install --upgrade <wheel URL>` |

The restarted app gets `--wait-for-exit` and the original arguments, waits for the old instance to release the
port and binds the same port, so the open browser tab reconnects and reloads itself when it sees a new version.
Child processes are started without PyInstaller's `_PYI_*` variables (`PYINSTALLER_RESET_ENVIRONMENT=1`).
`BMM_RELEASES_API` overrides the releases URL for testing.

## Check-in nets

`bmmonitor/checkin.py`. Group-call events to the round's talkgroup (from the round's start) are collected per
callsign in arrival order; a new `SessionID` counts as a keyup and a keyup that starts while another station is
on the air is counted as overlapping. `ContextID` is the device: 6-digit IDs are repeaters, others hotspots;
`/v2/device/{id}` gives its callsign and city (the locator suffix is removed). Log entries carry a uid,
timestamp and operator so that numbering can later move to a shared service (Google Apps Script) for several
operators. State is kept in `checkin.json`; a finished round is written to `checkin/<date>_TG<tg>_kierros<n>.csv`.
API: `POST /api/checkin` (`start`, `stop`, `log`, `unlog`, `edit`, `copied`) and
`GET /api/checkin/export?fmt=tsv|csv&since=<nro>`.

## Windows build and antivirus false positives

The Windows app is a PyInstaller **one-folder** build (`MonitorForBrandMeister.exe` + `_internal\`) since
0.10.0b2: one-file builds unpack themselves into a temp folder at every start, which machine-learning
antivirus heuristics (e.g. Defender's `…!ml` detections) often flag. Updates open the installer visibly
instead of running it silently. The release job smoke-tests the built exe (`--diag` must connect to
BrandMeister). If the repository has a `VT_API_KEY` secret (a free VirusTotal API key), the Windows
installer and exe are uploaded to VirusTotal and the report links are shown in the job summary.

## Check-in Google Sheet

`sheets/Code.gs` is a container-bound Apps Script web app (execute as owner, access anyone; scopes
`spreadsheets.currentonly` and `script.container.ui`). The owner's sheet menu creates one-time pairing codes
(15 min); `op:"pair"` exchanges a code for a per-operator 256-bit key stored in Script Properties. Every other
request carries `call, ts, nonce, action, data, sig` with `sig = HMAC-SHA256(key, call\nts\nnonce\naction\ndata)`
(UTF-8); the script rejects unknown operators, timestamps off by more than 5 min, reused nonces (CacheService)
and more than 120 requests/min per operator. Actions: `hello`, `start` (finds `Kierros <round> …` or copies the
`LOGIPOHJA…` template), `log` (assigns NRO under a script lock, Oper = authenticated operator), `unlog`
(deletes the row and renumbers), `edit` (`via`, `kunta`, `info` only), `list`. Columns A–F only; a dropdown
on RPT/HOTSPOT sourced from another sheet (Toistimet-data) reuses an entry with the same text or repeater
callsign, otherwise appends the BrandMeister value to the list range (extending the range and the rule when
full). Values starting with `= + - @`
are written as text; every change is appended to the hidden `Check-in-tapahtumat` sheet.

The app (`bmmonitor/sheetsync.py`) posts with `allow_redirects=False` and fetches the 302 target with GET
(Apps Script's response pattern). The sheet is authoritative while connected: the app polls `list` every 5 s
and replaces its log with the sheet rows. The key is stored with `keyring` (Windows Credential Manager, macOS
Keychain; config file fallback). `node sheets/test/test.js` runs the script against simulated Google services
(`sheets/test/gas-mock.js`), which can also serve it over HTTP for end-to-end tests (`BMM_SHEET_TEST=1`).
