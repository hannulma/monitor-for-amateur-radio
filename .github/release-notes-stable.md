**Vakaa versio 0.16.0.** Tämän version tärkeimmät uutuudet ovat useat APRS-radiot ja TNC:t, monipuolisempi APRS-kartta ja saksankielinen käyttöliittymä.
*Stable release 0.16.0 – several APRS radios and TNCs, a richer APRS map and a German user interface.*

### Uutta 0.15.0:n jälkeen / New since 0.15.0

- **Radiot ja TNC:t:** useita radioita yhtä aikaa (APRS → Asetukset → Radiot ja TNC:t): KISS TCP (Graywolf, Dire Wolf), KISS TNC sarjaportissa (USB tai Bluetooth), AGWPE (Dire Wolf, soundmodem), Kenwood TM-D710 / TH-D72 ja Kantronics KPC-3+ (kokeellinen) sekä simuloitu radio testaukseen. Aiempi Graywolf-yhteys siirtyy listan ensimmäiseksi radioksi automaattisesti.
- **APRS-kartan uudet tasot:** sääasemat (lämpötila, tuuli, kosteus, ilmanpaine, sade), objektit ja itemit sekä valinnaiset APRS-IS-asemat kotisijainnin ympäriltä. iGaten radioon välittämät asemat näkyvät onttoina, eivätkä ne vaikuta RF-kuuluvuuteen.
- **APRS-viestien merkki:** uusien viestien määrä näkyy APRS-välilehden nimen perässä; työpöytäilmoituksen voi ottaa käyttöön APRS-asetuksista.
- **Liikennemerkki:** BrandMeister-välilehden perässä näkyy antennikuvake, kun jossakin seuratussa puheryhmässä on lähetys käynnissä.
- **Deutsch:** käyttöliittymä, hälytykset, radioon menevät vastaukset ja Google-taulukon ohje saksaksi (konekäännös – korjausehdotukset palautetoiminnolla). Kieli valitaan ensimmäisellä käynnistyksellä käyttöjärjestelmän mukaan.
- **Ohjelman kuvaus** ja README:t päivitetty vastaamaan nykyistä ohjelmaa; näkyvä nimi on kaikkialla *Monitor for Amateur Radio*.
- Korjauksia: palautelomakkeen tekstikentän ohje käännetään, oman kutsun toiselta SSID:ltä tullut viesti näkyy uutena viestinä.

*Several radios and TNCs at the same time: KISS TCP, KISS TNC on a serial port (USB/Bluetooth), AGWPE, Kenwood TM-D710 / TH-D72 and Kantronics KPC-3+ (experimental) and a simulated radio; the previous Graywolf connection becomes the first radio automatically. New APRS map layers: weather stations, objects and items, and optional APRS-IS stations around your home; stations relayed by an iGate are shown hollow and do not affect RF coverage. New-message count after the APRS tab name with an optional desktop notification, and an antenna icon on the BrandMeister tab while a monitored talkgroup is on the air. German user interface (machine translation – corrections welcome via the feedback function). Updated program description and READMEs. Fixes: feedback form placeholder translated; messages from another SSID of your own callsign count as new.*

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
