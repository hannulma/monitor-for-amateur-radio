**Vakaa versio 0.15.0.** Tämän version tärkein uutuus on yhteysloki ja sen synkronointi QRZ.comiin. Lisäksi asetukset löytyvät nyt sieltä, missä niitä käytetään.
*Stable release 0.15.0 – contact log with QRZ.com sync, settings moved to the tabs where they are used, and more.*

### Uutta 0.14.0:n jälkeen / New since 0.14.0

- **Loki (uusi Lokit-välilehti):** BrandMeister-puheluista tunnistetaan yhteysehdokkaat (vuorottelu samassa puheryhmässä tai yksityispuhelu), ja ne voi kirjata lokiin yhdellä painalluksella. Yhteyksiä voi lisätä myös käsin millä tahansa tilalla (SSB, CW, FM, FT8, D-STAR, C4FM, DMR …). Lokissa on haku, suodatus tilan mukaan sekä ADIF- ja CSV-vienti.
- **QRZ Logbook:** lokiin lisätyt yhteydet voi synkronoida QRZ.comin lokikirjaan (vaatii maksullisen QRZ.com-tilauksen). Loki tallentuu aina ensin omalle koneelle.
- **Asetukset välilehdillä:** BrandMeisterin, APRS:n, Syötteiden ja Lokien asetukset ovat kunkin välilehden alimmassa Asetukset-osiossa. Ohjelma-välilehdellä ovat kieli, ulkoasu, päivitykset sekä palaute ja vikailmoitukset.
- **Välilehtien piilotus:** käyttämättömät välilehdet voi piilottaa kohdassa Ohjelma → Ulkoasu. Niiden toiminnot jatkavat taustalla.
- **Välilehdet ryhmiteltyinä:** BrandMeister · Tilastot · Kaverit · Laitteet · Check-in │ APRS · Syötteet │ Lokit │ Palvelut · Satelliitit │ Ohjelma.
- **Check-in:** vaiheittainen ohje Google-taulukon käyttöönottoon ja painikkeet skriptitiedostojen kopiointiin.
- **Radiokomennot:** komentolistan saa komennolla **OHJE** (englanniksi **CMD**). APUA ja HELP on poistettu, koska ne voisivat sekoittua hätäliikenteeseen.

*Contact log: contact candidates detected from BrandMeister calls, manual entries for any mode, search, mode filter, ADIF/CSV export and optional QRZ Logbook sync (paid QRZ.com subscription). Settings now live at the bottom of each tab; the App tab has language, appearance, updates and feedback. Unused tabs can be hidden (App → Appearance). Step-by-step guide for the check-in Google Sheet. Radio command list is now OHJE / CMD (APUA and HELP removed to avoid confusion with emergency traffic).*

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
