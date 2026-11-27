**Vakaa versio 0.17.0.** Tämän version tärkeimmät uutuudet ovat laivat APRS-kartalla, espanjan- ja ranskankielinen käyttöliittymä sekä eQSL- ja HamQTH-lokipalvelut.
*Stable release 0.17.0 – ships on the APRS map, Spanish and French user interfaces, and eQSL and HamQTH log upload.*

### Uutta 0.16.0:n jälkeen / New since 0.16.0

- **Laivat (AIS) APRS-kartalla:** oma karttataso, lähteenä Fintrafficin Digitraffic (Suomi ja Itämeri, ei avainta) tai aisstream.io (koko maailma, oma ilmainen avain). Alustyypit valitaan kartan yläpuolelta *Laivat*-valikosta, ja laiturissa tai ankkurissa olevat voi piilottaa. APRS-asetuksiin uudet osiot *Kartta* ja *Laivat (AIS)*.
- **Lokipalvelut:** loki voidaan lähettää QRZ.comin lisäksi eQSL:ään (salasana enintään 14 merkkiä) ja HamQTH:hon. Lokiin uusi etenemistapa (ADIF PROP_MODE): BrandMeisterin, toistinten ja EchoLinkin kautta pidetyt yhteydet merkitään internetin kautta pidetyiksi, ja niiden lähetyksen voi ohittaa palvelukohtaisesti.
- **Viimeisimmät puhelut:** käynnissä oleva lähetys näkyy heti listan alussa.
- **Español ja Français:** käyttöliittymä, hälytykset, radioon menevät vastaukset ja Google-taulukon ohje espanjaksi ja ranskaksi (konekäännös – korjausehdotukset palautetoiminnolla).
- **Viestien muotoilu** (ääkkösten muunnos, viestien määrä) on nyt APRS-asetuksissa kohdassa *Viestit radioon*.
- Korjauksia: välilehtirivi pysyy aina yläpalkin alla (aiemmin sisältöä saattoi näkyä niiden välistä), selaimesta haetun sijainnin nimi ei ole enää suomeksi muilla kielillä, englanniksi "Update now".

*Ships (AIS) on the APRS map from Fintraffic Digitraffic (Finland and the Baltic Sea, no key) or aisstream.io (worldwide, own free key), with vessel-type filters and hiding of moored/anchored ships; new Map and Ships (AIS) sections in the APRS settings. Log upload to eQSL and HamQTH in addition to QRZ.com, and ADIF PROP_MODE: contacts via BrandMeister, repeaters or EchoLink are marked as internet-assisted and can be skipped per service. Ongoing transmissions at the top of Latest calls. Spanish and French user interfaces (machine translation – corrections welcome via the feedback function). Message formatting settings moved to APRS → Settings → APRS messages on the radio. Fixes: the tab bar always stays right below the header, the browser location label is no longer Finnish in other languages, "Update now" in English.*

### Lataa / Download

| Käyttöjärjestelmä / OS | Tiedosto / File |
|---|---|
| Windows 10/11 – asennusohjelma / installer | `MonitorForBrandMeister-*-windows-setup.exe` |
| Windows – ilman asennusta / portable | `MonitorForBrandMeister-*-windows-portable.zip` |
| macOS 11+ (Intel ja Apple Silicon M1–M4 / *Intel and Apple Silicon*) | `MonitorForBrandMeister-*-macos-universal.dmg` |
| Linux x86_64 | `MonitorForBrandMeister-*-linux-x86_64.tar.gz` |
| Python (pip) | `monitor_for_brandmeister-*.whl` |

Ohjelma päivittyy automaattisesti, jos päivitysten tarkistus on päällä (Ohjelma → Versio ja päivitykset).
*The app updates itself if update checks are on (App → Version and updates).*

[Code signing policy](https://github.com/hannulma/monitor-for-amateur-radio/blob/main/CODE_SIGNING.md) · [Tietosuoja / Privacy](https://github.com/hannulma/monitor-for-amateur-radio/blob/main/PRIVACY.md)

Ohjelmat eivät (vielä) ole allekirjoitettuja: Windows voi näyttää SmartScreen-varoituksen (*Lisätietoja → Suorita silti*) ja macOS vaatii ensimmäisellä kerralla avaamisen *Järjestelmäasetukset → Tietosuoja ja turvallisuus → Avaa silti*.
*The binaries are unsigned; see README for first-launch instructions.*
