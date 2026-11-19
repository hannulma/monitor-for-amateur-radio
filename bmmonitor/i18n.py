"""Ohjelman omien tekstien kieli (suomi/englanti).

Avaimina ovat suomenkieliset tekstit, joten suomi toimii ilman sanakirjaa.
Käyttö:  tr("Virheellinen kutsu: {x}", x=call)

Käyttöliittymän (selaimen) käännökset ovat tiedostossa static/i18n.js.
Avoimesta datasta tulevat tekstit (Ilmatieteen laitoksen varoitukset, Digitrafficin
tiedotteet) ovat lähteen kielellä.
"""
from __future__ import annotations

LANGS = ("fi", "en")
_lang = "fi"


def set_lang(lang: str | None) -> None:
    global _lang
    _lang = lang if lang in LANGS else "fi"


def get_lang() -> str:
    return _lang


def tr(text: str, **kw) -> str:
    out = EN.get(text, text) if _lang == "en" else text
    return out.format(**kw) if kw else out


def num(x: float, digits: int = 1) -> str:
    """Desimaaliluku kielen mukaisella erottimella."""
    s = f"{x:.{digits}f}"
    return s.replace(".", ",") if _lang == "fi" else s


# Ilmansuunnat (salamahälytys: "12 km koillisessa")
BEARINGS = {
    "fi": ["pohjoisessa", "koillisessa", "idässä", "kaakossa", "etelässä", "lounaassa", "lännessä", "luoteessa"],
    "en": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
}


def bearing(index: int) -> str:
    return BEARINGS[_lang][index % 8]


EN: dict[str, str] = {
    # --- asetukset ja syötteet ---
    "Virheellinen puheryhmä: {p} (sallitut: numerot, * ja ?)": "Invalid talkgroup: {p} (allowed: digits, * and ?)",
    "Sallitut arvot: {v}": "Allowed values: {v}",
    "Virheellinen kutsu tai DMR-ID: {x}": "Invalid callsign or DMR ID: {x}",
    "Virheellinen laitteen ID: {x}": "Invalid device ID: {x}",
    "Virheellinen kutsu: {x} (muoto esim. OH2ABC-7)": "Invalid callsign: {x} (format e.g. OH2ABC-7)",
    "Passcoden pitää olla numero": "The passcode must be a number",
    "Syötteen osoitteen pitää alkaa http:// tai https:// ({url})": "Feed address must start with http:// or https:// ({url})",
    "Anna oma kutsu (APRS-IS-tunnus) ennen kuin otat lähetyksen käyttöön":
        "Enter your own callsign (APRS-IS login) before enabling sending",
    "Virheellinen lokaattori (esim. KP20LE)": "Invalid locator (e.g. KP20LE)",
    "Koordinaatit eivät ole sallitulla alueella": "Coordinates are out of range",
    "Tienumeron pitää olla numero: {x}": "Road number must be a number: {x}",
    "Aseta ensin oma sijainti (koordinaatit tai lokaattori)": "Set your location first (coordinates or locator)",
    "Virheellinen kieli": "Invalid language",
    # --- API-vastaukset ---
    "Lähetys ei ole päällä": "Sending is not enabled",
    "Vastaanottajia ei ole määritetty": "No recipients configured",
    "Anna kutsu tai DMR-ID": "Enter a callsign or DMR ID",
    "Virheellinen parametri": "Invalid parameter",
    "Anna puheryhmä": "Enter a talkgroup",
    "Laite ei ole seurattavien listalla": "The device is not on your watch list",
    "Tuntematon toiminto": "Unknown action",
    "Aseta oma APRS-IS-tunnus (Syötteet ja viestit)": "Set your own APRS-IS login (Feeds & messages)",
    "APRS-IS-yhteys ei ole auki": "The APRS-IS connection is not open",
    "Passcode ei kelpaa – viestejä ei välitetä": "Invalid passcode – messages are not relayed",
    "Passcode ei kelpaa (unverified) – viestejä ei välitetä": "Invalid passcode (unverified) – messages are not relayed",
    "Lähettäjän pitää olla oma kutsusi tai seurattava kutsu": "The sender must be your own callsign or a watched callsign",
    "Kirjoita viesti": "Type a message",
    "APRS-IS sulki yhteyden": "APRS-IS closed the connection",
    "Rekisteri: {e}": "Registry: {e}",
    "Laitteet: {e}": "Devices: {e}",
    "Monitor for BrandMeister: testiviesti {t}": "Monitor for BrandMeister: test message {t}",
    "Testihälytys": "Test alert",
    "Vapaata porttia ei löytynyt": "No free port found",
    # --- BM API ---
    "Toiminto vaatii Brandmeister API-avaimen (Asetukset)": "This action needs a BrandMeister API key (Settings)",
    "API-avain hylättiin tai laite ei ole sinun (HTTP {s})": "API key rejected or the device is not yours (HTTP {s})",
    "Ei löytynyt": "Not found",
    # --- virheiden selitykset ---
    "Varmenteen tarkistus epäonnistui ({host}). ": "Certificate verification failed ({host}). ",
    "Päivitä: pip install --upgrade truststore certifi": "Update: pip install --upgrade truststore certifi",
    "Aikakatkaisu": "Timed out",
    "Yhteysvirhe: {e}": "Connection error: {e}",
    "Palvelin vastasi HTTP {s}": "Server responded HTTP {s}",
    # --- palvelut ---
    "Salamahälytys": "Lightning alert",
    "Säävaroitukset": "Weather warnings",
    "Avaruussää": "Space weather",
    "Revontulihälytys": "Aurora alert",
    "Säteilyvalvonta": "Radiation monitoring",
    "Tieliikennetiedotteet": "Road traffic bulletins",
    "Aseta oma sijainti": "Set your location",
    "Aseta oma sijainti tai valitse koko Suomi (säde 0)": "Set your location or choose all of Finland (radius 0)",
    "Valitse sijainti, maakunta tai tienumero": "Choose a location, region or road number",
    "Magnetometridataa ei saatu": "No magnetometer data received",
    "Säteilydataa ei saatu": "No radiation data received",
    "Liikennetiedotteet (onnettomuudet, häiriöt)": "Traffic announcements (accidents, disruptions)",
    "Tietyöt": "Road works",
    "Painorajoitukset": "Weight restrictions",
    "Erikoiskuljetukset": "Exempted (oversize) transports",
    "maasalama": "ground strike",
    "salama": "lightning",
    "⚡ {kind} {km} km {dir}": "⚡ {kind} {km} km {dir}",
    "Salama {km} km {dir}, {n} iskua/15 min": "Lightning {km} km {dir}, {n} strikes/15 min",
    "{n} iskua 15 min aikana {r} km säteellä": "{n} strikes in 15 min within {r} km",
    "Geomagneettinen myrsky: Kp {kp}": "Geomagnetic storm: Kp {kp}",
    "Revontulikelit VHF:llä mahdollisia, HF-kelit voivat heiketä.":
        "VHF aurora propagation possible, HF conditions may degrade.",
    "Avaruussaa: Kp {kp} - myrsky, revontulikelit mahdollisia": "Space wx: Kp {kp} storm, aurora prop possible",
    "HF-yhteydet voivat katketa auringon puoleisella maapallolla.": "HF radio blackouts possible on the sunlit side of Earth.",
    "NOAA:n ennuste": "NOAA forecast",
    "Auringonpurkaus: röntgenvuo ylitti {c}": "Solar flare: X-ray flux exceeded {c}",
    "Ennuste: K-indeksi {k} odotettavissa": "Forecast: K-index {k} expected",
    " (voimassa {t} UTC asti)": " (valid until {t} UTC)",
    "mahdollisia": "possible",
    "todennäköisiä": "likely",
    "voimakkaita": "strong",
    ", pilvisyys {c}/8": ", cloud cover {c}/8",
    "Revontulet {word}: {name} {rng} nT/h{cl}": "Aurora {word}: {name} {rng} nT/h{cl}",
    "Magneettinen aktiivisuus lähimmällä magnetometrillä ({name}, {km} km). VHF-revontulikelit mahdollisia.":
        "Magnetic activity at the nearest magnetometer ({name}, {km} km). VHF aurora propagation possible.",
    " (+{n} asemaa)": " (+{n} stations)",
    "Säteily koholla: {name} {dr} µSv/h{more}": "Radiation elevated: {name} {dr} µSv/h{more}",
    "Raja {thr} µSv/h{dist}. Normaali taso Suomessa n. 0,05–0,3 µSv/h. Tarkista tilanne STUK:n sivuilta.":
        "Threshold {thr} µSv/h{dist}. Normal level in Finland approx. 0.05–0.3 µSv/h. Check STUK's website.",
    "tie {roads}": "road {roads}",
    # --- radiokomennot (vastaukset ASCII-muodossa radioon) ---
    "Komennot: TG<nro>, KUKA <kutsu>, NYT{extra}": "Commands: TG<no>, WHO <call>, NOW{extra}",
    "Tuntematon komento. Laheta APUA": "Unknown command. Send HELP",
    "Ei lahetyksia seuratuissa ryhmissa": "No transmissions in watched groups",
    "NYT: {x}": "NOW: {x}",
    "{t}: ei uutisia (syötettä ei vielä luettu)": "{t}: no news yet (feed not read yet)",
    "{q}: ei kuultu ohjelman kaynnistyksen jalkeen": "{q}: not heard since the program started",
    "NYT": "NOW",
    "{n}s sitten": "{n}s ago", "{n}min sitten": "{n}min ago", "{n}h sitten": "{n}h ago", "{n}pv sitten": "{n}d ago",
    "{label}: ei liikennettä ohjelman käynnistyksen jälkeen": "{label}: no traffic since the program started",
    # --- päivitykset ---
    "Päivityksen tarkistus epäonnistui: {e}": "Update check failed: {e}",
    "Tarkistussumma ei täsmää – päivitystä ei asennettu": "Checksum mismatch – the update was not installed",
    "Tälle alustalle ei ole päivityspakettia": "No update package for this platform",
    "Uutta versiota ei ole": "No new version available",
    "Päivitys on jo käynnissä": "An update is already in progress",
}
