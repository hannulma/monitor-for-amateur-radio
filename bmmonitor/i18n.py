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


UNITS = ("metric", "imperial")
_units = "metric"
KM_PER_MI = 1.609344


def set_units(units: str | None) -> None:
    global _units
    _units = units if units in UNITS else "metric"


def get_units() -> str:
    return _units


def dist(km: float | None, digits: int = 0) -> str:
    """Etäisyys valitussa yksikössä: '12 km' tai '7 mi'."""
    if km is None:
        return ""
    if _units == "imperial":
        return f"{num(float(km) / KM_PER_MI, digits)} mi"
    return f"{num(float(km), digits)} km"


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
    "Aseta oma APRS-IS-tunnus (APRS-välilehti)": "Set your own APRS-IS login (APRS tab)",
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
    "⚡ {kind} {km} {dir}": "⚡ {kind} {km} {dir}",
    "Salama {km} {dir}, {n} iskua/15 min": "Lightning {km} {dir}, {n} strikes/15 min",
    "{n} iskua 15 min aikana {r} säteellä": "{n} strikes in 15 min within {r}",
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
    "Magneettinen aktiivisuus lähimmällä magnetometrillä ({name}, {km}). VHF-revontulikelit mahdollisia.":
        "Magnetic activity at the nearest magnetometer ({name}, {km}). VHF aurora propagation possible.",
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
    # --- check-in ---
    "Asemaa {c} ei ole kuultu tällä kierroksella": "Station {c} has not been heard in this round",
    "Asemaa {c} ei ole kirjattu": "Station {c} has not been logged",
    "Check-in-kierrosta ei ole käynnissä": "No check-in round in progress",
    "Taulukkoon ei saatu yhteyttä: {e}": "Could not connect to the sheet: {e}",
    "Tarkista skriptin osoite ja että verkkosovelluksen käyttöoikeus on \"Kuka tahansa\".":
        "Check the script address and that the web app access is \"Anyone\".",
    "Taulukko vastasi odottamattomasti (HTTP {s}). {h}": "The sheet responded unexpectedly (HTTP {s}). {h}",
    "Taulukko hylkäsi pyynnön": "The sheet rejected the request",
    "Taulukon avainta ei löytynyt – yhdistä uudelleen parituskoodilla": "The sheet key was not found – connect again with a pairing code",
    "Skriptin osoitteen pitää olla muotoa https://script.google.com/macros/s/…/exec": "The script address must look like https://script.google.com/macros/s/…/exec",
    "Taulukko: {e}": "Sheet: {e}",
    "Tunnistus epäonnistui – yhdistä uudelleen uudella parituskoodilla": "Authentication failed – reconnect with a new pairing code",
    "Tietokoneen kello on väärässä – korjaa kello ja yritä uudelleen": "The computer's clock is wrong – fix it and try again",
    "Liikaa pyyntöjä – odota hetki": "Too many requests – wait a moment",
    # --- päivitykset ---
    "Päivityksen tarkistus epäonnistui: {e}": "Update check failed: {e}",
    "Tarkistussumma ei täsmää – päivitystä ei asennettu": "Checksum mismatch – the update was not installed",
    "Tälle alustalle ei ole päivityspakettia": "No update package for this platform",
    "Uutta versiota ei ole": "No new version available",
    "Päivitys on jo käynnissä": "An update is already in progress",
    "Itävalta": "Austria",
    "Belgia": "Belgium",
    "Bosnia ja Hertsegovina": "Bosnia and Herzegovina",
    "Kroatia": "Croatia",
    "Kypros": "Cyprus",
    "Tšekki": "Czechia",
    "Tanska": "Denmark",
    "Viro": "Estonia",
    "Suomi": "Finland",
    "Ranska": "France",
    "Saksa": "Germany",
    "Kreikka": "Greece",
    "Unkari": "Hungary",
    "Islanti": "Iceland",
    "Irlanti": "Ireland",
    "Italia": "Italy",
    "Liettua": "Lithuania",
    "Luxemburg": "Luxembourg",
    "Alankomaat": "Netherlands",
    "Pohjois-Makedonia": "North Macedonia",
    "Norja": "Norway",
    "Puola": "Poland",
    "Portugali": "Portugal",
    "Espanja": "Spain",
    "Ruotsi": "Sweden",
    "Sveitsi": "Switzerland",
    "Ukraina": "Ukraine",
    "Iso-Britannia": "United Kingdom",
    "Keltainen": "Yellow",
    "Oranssi": "Orange",
    "Punainen": "Red",
    "Valitse maa": "Choose a country",
    "Valitse alue: tämän maan varoituksissa ei ole aluerajoja, joten sijaintia ei voi käyttää": "Choose a region: this country's warnings have no area boundaries, so your location cannot be used",
    "Aseta oma sijainti tai valitse alue": "Set your location or choose a region",
    "Tuntematon maa: {c}": "Unknown country: {c}",
    "Revontulet {word}: NOAA-ennuste {p} %{cl}": "Aurora {word}: NOAA forecast {p} %{cl}",
    "NOAA:n OVATION-ennuste: näkyvien revontulien todennäköisyys {r} säteellä. VHF-revontulikelit mahdollisia.": "NOAA OVATION forecast: probability of visible aurora within {r}. VHF aurora propagation possible.",
    "Virheellinen kutsu: {x}": "Invalid callsign: {x}",
    "Brandmeister ei salli tätä toimintoa tälle laitteelle, joten se ei ole tämän API-avaimen tilin laite. Hallintatyökalut piilotettiin; ne saa takaisin valinnalla Oma laite.": "BrandMeister does not allow this action for this device, so it does not belong to the account of this API key. The management tools were hidden; turn on Own device to show them again.",
    "Satelliittien ylilennot": "Satellite passes",
    "🛰 {name} ylittää klo {t} ({m} min)": "🛰 {name} pass at {t} (in {m} min)",
    "Nousu {a} {az1}, korkein {el}° {az2} klo {tca}, lasku {l} {az3}.": "Rise {a} {az1}, max {el}° {az2} at {tca}, set {l} {az3}.",
    "Näkyy taivaalla klo {a}–{b} (korkeimmillaan {el}°).": "Visible in the sky {a}–{b} (up to {el}°).",
    "Taajuudet: {f}.": "Frequencies: {f}.",
    "{name} klo {t} max {el}° {az}": "{name} at {t} max {el}° {az}",
    "näkyy {a}–{b}": "visible {a}–{b}",
    "Virheellinen yksikkö": "Invalid unit",
    "Virheellinen valinta": "Invalid choice",
    "Avaa": "Open", "Avaa selaimessa": "Open in browser", "Sulje ohjelma": "Quit",
    "valikkoriviltä": "in the menu bar", "ilmaisinalueelta": "in the notification area",
    "Ohjelma toimii taustalla. Avaa ikkuna kuvakkeesta {where}.":
        "The program keeps running in the background. Open the window from the icon {where}.",
    "Palautteen lähetys ei ole vielä käytössä – tallenna paketti ja lähetä se itse.": "Sending feedback is not enabled yet – save the package and send it yourself.",
    "Palautteen lähetys epäonnistui: {e}": "Sending feedback failed: {e}",
    "Vastaanotin vastasi odottamattomasti (HTTP {s})": "The receiver answered unexpectedly (HTTP {s})",
    "Palautteita on lähetetty liikaa – yritä myöhemmin uudelleen": "Too much feedback has been sent – try again later",
    "Paketti on liian suuri – jätä puhelu- ja SMS-lokit pois": "The package is too large – leave out the call and SMS logs",
    "Vastaanotin hylkäsi palautteen": "The receiver rejected the feedback",
}
