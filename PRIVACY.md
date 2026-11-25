# Tietosuojaseloste / Privacy policy

*Monitor for Amateur Radio (Monitor for BrandMeister)* · ylläpitäjä / maintainer: OH2BEA ·
https://github.com/hannulma/monitor-for-amateur-radio

[English below](#english)

## Suomeksi

**Ohjelma ei kerää käyttäjistään mitään tietoja ylläpitäjälle.** Siinä ei ole käyttötilastoja, analytiikkaa
eikä mainoksia. Kaikki asetukset, lokit ja tallennetut tiedot ovat vain omalla koneellasi
(Windows: `%APPDATA%\BMMonitor`, macOS: `~/Library/Application Support/BMMonitor`, Linux: `~/.config/BMMonitor`).
API-avain ja APRS-passcode tallennetaan käyttöjärjestelmän salasanavarastoon.

Käyttöliittymä toimii paikallisen palvelimen (`127.0.0.1`) kautta, joka ei näy verkkoon. Ohjelma ottaa yhteyttä
alla lueteltuihin palveluihin vain silloin, kun kyseinen toiminto on käytössä. Jokainen palvelu näkee yhteyden
tavallisesti tapaan IP-osoitteesi ja sen, mitä pyydetään; palveluiden omat tietosuojakäytännöt koskevat niitä.

| Palvelu | Milloin | Mitä lähetetään | Miten pois |
|---|---|---|---|
| BrandMeister (api.brandmeister.network) | aina (ohjelman perustoiminto) | yhteys Last Heard -syötteeseen; puheryhmä- ja asemahaut; API-avaimella omien laitteiden hallinta | sulje ohjelma; API-avaimen voi jättää antamatta |
| BrandMeister Hoseline (hose.brandmeister.network) | kun kuuntelet puheryhmää | valittu puheryhmä | älä käynnistä kuuntelua |
| APRS-IS (esim. rotate.aprs2.net) | kun APRS-viestit / syötteet radioon ovat käytössä | oma kutsu ja passcode, lähtevät APRS-viestit | Syötteet- ja APRS-välilehden kytkimet; jätä APRS-IS-tunnus tyhjäksi |
| GitHub (api.github.com, github.com) | päivitystarkistus kerran päivässä ja päivitys | versiotarkistus, julkaisun lataus | Asetukset → Versio ja päivitykset → automaattinen tarkistus pois |
| Ilmatieteen laitos (opendata.fmi.fi, alerts.fmi.fi), MeteoAlarm, NOAA SWPC, STUK (IL:n avoin data), Fintraffic Digitraffic, Open-Meteo, CelesTrak, SatNOGS DB | kun kyseinen palvelu on päällä Palvelut-välilehdellä | haut sijaintisi lähialueelta (koordinaatit tai alue) | Palvelut-välilehden kytkimet |
| OpenStreetMap-karttatiilet (tile.openstreetmap.org tai itse valittu) | kun katsot APRS-kuuluvuuskarttaa | katsotut karttatiilet; ohjelman nimi ja versio User-Agentissa | älä avaa karttaa; tiilipalvelimen voi vaihtaa |
| Net-kalenterit: FinDMR-kierros (6kw.fi), W0WC:n BM DMR Nets (Google-kalenteri) ja itse lisäämäsi | kun Netit-osio on päällä (BrandMeister-välilehti), noin 6 tunnin välein | kalenterin haku | Netit-kytkin pois tai poista kalenteri |
| Uutis- ja kalenterisyötteet, jotka itse lisäät | kun syötteet ovat käytössä | syötteen haku | poista syöte |
| Google Apps Script: check-in-taulukko | vain jos yhdistät check-in-taulukon | kirjatut check-in-rivit omaan taulukkoosi | irrota taulukko |
| Google Apps Script: palaute (script.google.com) | **vain kun itse lähetät palautteen** | viestisi ja halutessasi lokit; salaisuudet poistetaan ja sijainti pyöristetään 4-merkkiseksi lokaattoriksi; paketin voi tarkistaa ennen lähetystä | älä lähetä palautetta |
| QRZ Logbook (logbook.qrz.com) | vain jos kytket QRZ-synkronoinnin päälle (Lokit-välilehti) | lokiin lisäämäsi yhteydet (kutsu, aika, kaista, raportit, nimi, kommentti) ja oma API-avaimesi; poistettaessa halutessasi poisto QRZ:sta | Lokit → QRZ Logbook → lähetys pois |
| Graywolf / Dire Wolf (KISS TCP, oletus 127.0.0.1) | kun APRS-kuuluvuus on päällä | ei mitään; vain jos sallit APRS-viestien lähetyksen radiolla, viestit ja kuittaukset lähetetään radiolla (ja Graywolfin iGate voi välittää ne APRS-IS:ään) | APRS → Graywolf-yhteys pois tai Asetukset → APRS-viestit radiolla pois |

Ohjelman lähettämät APRS- ja DMR-viestit ovat radioamatööriliikennettä, joka on luonteeltaan julkista.

Kysymykset: https://github.com/hannulma/monitor-for-amateur-radio/issues

## English

**The program collects no data about its users for the maintainer.** There is no telemetry, analytics or
advertising. All settings, logs and stored data stay on your own computer (paths above). The API key and the APRS
passcode are kept in the operating system's credential store.

The user interface runs through a local server (`127.0.0.1`) that is not reachable from the network. The program
contacts the services listed above only while the corresponding feature is in use. Each service sees your IP address
and what is requested, as with any internet connection; their own privacy policies apply.

- **BrandMeister** – always (core function): Last Heard feed, talkgroup and station lookups; with an API key, managing
  your own devices.
- **BrandMeister Hoseline** – only while you listen to a talkgroup.
- **APRS-IS** – only when APRS messaging or feeds to your radio are enabled (your callsign, passcode, outgoing messages).
- **GitHub** – daily update check and downloading updates (can be switched off in Settings).
- **Open data services** (FMI, MeteoAlarm, NOAA SWPC, STUK via FMI, Fintraffic Digitraffic, Open-Meteo, CelesTrak,
  SatNOGS DB) – only for services you switch on; requests concern your area.
- **OpenStreetMap tiles** – only when you view the APRS coverage map; the tile server can be changed.
- **Net calendars** (FinDMR round at 6kw.fi, W0WC's BM DMR Nets on Google Calendar, and any you add) – fetched about
  every 6 hours while the Nets section is switched on.
- **Feeds and calendars you add** – fetched while enabled.
- **Google Apps Script** – only if you connect a check-in sheet (rows go to your own sheet), and **only when you send
  feedback yourself** (your message and, if you choose, logs with secrets removed and location rounded to a
  4-character locator; you can inspect the bundle before sending).
- **QRZ Logbook** (logbook.qrz.com) – only if you switch QRZ sync on (Logs tab): the contacts you add to your log and
  your own API key; optionally deletions.
- **Graywolf / Dire Wolf KISS TCP** – receive only, unless you allow APRS messages on the radio (Settings);
  then your messages and acks are transmitted on the radio (and Graywolf's iGate may pass them to APRS-IS).

APRS and DMR messages sent by the program are amateur radio traffic, which is public by nature.

Questions: https://github.com/hannulma/monitor-for-amateur-radio/issues
