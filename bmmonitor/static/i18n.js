/* Monitor for BrandMeister – käyttöliittymän kieliversiot (suomi / englanti).
 *
 * Sivu kirjoitetaan suomeksi. Englanniksi vaihdettaessa jokainen sivun tekstisolmu ja
 * placeholder/title-attribuutti käännetään tästä sanakirjasta (avaimena suomenkielinen teksti,
 * välilyönnit normalisoituina). MutationObserver kääntää myös myöhemmin piirretyt osat.
 * Vaihtelevat lauseet tehdään koodissa T("… {n} …", {n})-kutsulla.
 */
(function () {
  "use strict";

  const EN = {
    // ---- yläpalkki ja välilehdet ----
    "Yhdistetään…": "Connecting…",
    "Seuranta": "Monitor", "Kaverit": "Friends", "Laitteet": "Devices", "Tilastot": "Statistics",
    "Palvelut": "Services", "Syötteet ja viestit": "Feeds & messages", "Asetukset": "Settings",
    "Päivitys {v} saatavilla": "Update {v} available",

    // ---- asetukset ----
    "Seurattavat puheryhmät": "Talkgroups to monitor",
    "Erota välilyönnillä tai pilkulla.": "Separate with spaces or commas.",
    "= mikä tahansa merkkijono (244* = kaikki 244-alkuiset),": "= any digits (244* = everything starting with 244),",
    "= yksi numero.": "= one digit.",
    "Tekstiviestit ja data": "Text messages and data",
    "Koko verkko": "Whole network",
    "Vain seurattujen ryhmien / ID:iden": "Only monitored groups / IDs",
    "Ohita lyhyet avaukset: alle (sekuntia)": "Ignore short keyups: under (seconds)",
    "0 = kaikki puhelut tallentuvat. Listan pituus valitaan Viimeisimmät puhelut -paneelista.":
      "0 = all calls are recorded. The list length is chosen in the Latest calls panel.",
    "Tallenna CSV-lokiin": "Save to CSV log",
    "Tallenna": "Save", "Tyhjennä historia": "Clear history", "Tallennettu": "Saved",
    "Brandmeister API-avain (vapaaehtoinen)": "BrandMeister API key (optional)",
    "API-avain laitteen hallintaan": "API key for device management",
    "Luo avain: brandmeister.network → SelfCare → Security / API Keys. Avainta tarvitaan vain Laitteet-välilehden hallintapainikkeisiin, ja Brandmeister sallii ne vain omille laitteillesi. Avain tallentuu vain tämän koneen asetustiedostoon.":
      "Create a key: brandmeister.network → SelfCare → Security / API Keys. The key is only needed for the management buttons on the Devices tab, and BrandMeister only allows them for your own devices. The key is stored only in this computer's settings file.",
    "Tallenna avain": "Save key", "Poista avain": "Delete key",
    "Avain tallennettu": "Key saved", "Ei avainta": "No key",
    "Seuranta ja historia": "Monitoring and history", "Avaa kaikki": "Expand all", "Pienennä kaikki": "Collapse all",
    "Avaa kaikki tämän välilehden osiot": "Expand all sections on this tab",
    "Pienennä kaikki tämän välilehden osiot otsikoiksi": "Collapse all sections on this tab to their headers",
    "Avaa osio": "Expand section", "Pienennä osio otsikoksi": "Collapse section to its header",
    "Kieli": "Language", "Käyttöliittymän kieli": "User interface language",
    "Kieli koskee myös ohjelman omia hälytyksiä ja radioon lähetettäviä vastauksia. Ilmatieteen laitoksen varoitukset ja Digitrafficin tiedotteet ovat lähteen kielellä (suomeksi).":
      "The language also applies to the program's own alerts and to replies sent to your radio. Finnish Meteorological Institute warnings and Digitraffic bulletins stay in their source language (Finnish).",
    "Versio ja päivitykset": "Version and updates",
    "Tarkista päivitykset automaattisesti (kerran päivässä)": "Check for updates automatically (once a day)",
    "Tarkista nyt": "Check now",
    "Ohjelma": "Program", "Sulje ohjelma": "Quit program",
    "Sulkee taustalla toimivan ohjelman. Ohjelman voi avata uudelleen käynnistämällä sen; jos se on jo käynnissä, käynnistys avaa vain tämän sivun.":
      "Stops the program running in the background. Start it again to reopen; if it is already running, starting it just opens this page.",
    "Puheluloki": "Call log", "SMS/data-loki": "SMS/data log", "Datan raakatapahtumat": "Raw data events",
    "Suljetaanko Monitor for BrandMeister? Seuranta ja hälytykset loppuvat, kunnes käynnistät ohjelman uudelleen.":
      "Quit Monitor for BrandMeister? Monitoring and alerts stop until you start the program again.",
    "Ohjelma on suljettu. Voit sulkea tämän välilehden.": "The program has been closed. You can close this tab.",
    "Sovellus ei vastaa – onko se suljettu? Yritetään uudelleen…": "The program is not responding – has it been closed? Retrying…",

    // ---- päivitykset ----
    "Asennettu versio: {v}": "Installed version: {v}",
    "Windows (asennettu)": "Windows (installed)", "Windows (ilman asennusta)": "Windows (portable)",
    "Ladataan päivitystä…": "Downloading update…", "Asennetaan…": "Installing…", "Käynnistetään uudelleen…": "Restarting…",
    "Uusi versio {v} on saatavilla.": "New version {v} is available.",
    "Mitä uutta": "What's new", "Päivitä nyt": "Update now",
    "Uusi versio ladataan, sen tarkistussumma tarkistetaan ja ohjelma käynnistyy uudelleen. Tämä sivu yhdistää automaattisesti.":
      "The new version is downloaded, its checksum is verified and the program restarts. This page reconnects automatically.",
    "Päivitä komennolla:": "Update with:",
    "Lataa {name}": "Download {name}",
    "Pura zip-tiedosto ja korvaa vanha ohjelmakansio, kun ohjelma on suljettu.":
      "Extract the zip file and replace the old program folder while the program is closed.",
    "Uusi versio ladataan ja sen tarkistussumma tarkistetaan. Sen jälkeen asennusohjelma avautuu: vahvista asennus, niin ohjelma käynnistyy uudelleen ja tämä sivu yhdistää automaattisesti.":
      "The new version is downloaded and its checksum is verified. Then the installer opens: confirm the installation and the program restarts; this page reconnects automatically.",
    "Sulje ohjelma, avaa levykuva ja vedä ohjelma Ohjelmat-kansioon (Korvaa).":
      "Quit the program, open the disk image and drag the app to the Applications folder (Replace).",
    "Korvaa vanha ohjelmatiedosto uudella, kun ohjelma on suljettu.": "Replace the old program file with the new one while the program is closed.",
    "Avaa julkaisusivu": "Open the release page",
    "Ohjelma on ajan tasalla (tarkistettu {ago}).": "The program is up to date (checked {ago}).",
    "Päivityksiä ei ole vielä tarkistettu.": "Updates have not been checked yet.",
    "Päivitetään – ohjelma käynnistyy uudelleen…": "Updating – the program is restarting…",
    "Päivitetäänkö ohjelma versioon {v}? Ohjelma käynnistyy uudelleen.": "Update the program to version {v}? The program will restart.",

    // ---- syötteet ja viestit ----
    "Syötteet → DMR-tekstiviestit": "Feeds → DMR text messages",
    "Uusien uutisten otsikot lähetetään radioosi APRS-viesteinä. Brandmeister välittää ne DMR-tekstiviesteinä sille radiolle, jonka DMR-ID:hen kutsu ja SSID on liitetty":
      "Headlines of new items are sent to your radio as APRS messages. BrandMeister delivers them as DMR text messages to the radio whose DMR ID the callsign and SSID are linked to",
    "BM SelfCaren APRS-asetuksissa": "in the APRS settings of BM SelfCare",
    ". Vastaanottajaksi kirjoitetaan sama kutsu ja SSID (esim.": ". Enter the same callsign and SSID as the recipient (e.g.",
    "). Viesti tulee perille, jos radio on kuultu verkossa äskettäin tai SelfCaressa on":
      "). The message is delivered if the radio has been heard on the network recently or SelfCare has",
    "päällä.": "enabled.",
    "Lähetys päällä": "Sending enabled",
    "Oma APRS-IS-tunnus": "Your APRS-IS login",
    "Ei samaa SSID:tä kuin Graywolfilla/iGatella": "Not the same SSID as your iGate",
    "tyhjä = lasketaan kutsusta": "empty = calculated from callsign",
    "tyhjä = {n} (laskettu)": "empty = {n} (calculated)",
    "APRS-IS-palvelin": "APRS-IS server",
    "Vastaanottajat (radion kutsu-SSID)": "Recipients (radio callsign-SSID)",
    "Näytä APRS-viestit kutsuille": "Show APRS messages for callsigns",
    "oma kutsu (oletus)": "your callsign (default)",
    "Kaikki SSID:t, esim. OH2ABC = OH2ABC-1 … -15": "All SSIDs, e.g. OH2ABC = OH2ABC-1 … -15",
    "Viestejä / otsikko": "Messages / headline",
    "67 merkkiä / viesti": "67 characters / message",
    "Tarkistusväli (min)": "Check interval (min)",
    "Muunna ääkköset (ä→a)": "Convert accented letters (ä→a)",
    "Älä odota kuittausta": "Don't wait for acknowledgement",
    "Viesti lähetetään kerran eikä tule koskaan kahdesti, mutta perillemenoa ei voi seurata.":
      "The message is sent once and never arrives twice, but delivery cannot be tracked.",
    "Syötteet (RSS, Atom tai JSON Feed)": "Feeds (RSS, Atom or JSON Feed)",
    "+ Lisää syöte": "+ Add feed",
    "Komento-kenttä (esim. SAA) tekee syötteen uusimmasta otsikosta radiosta kysyttävän. Uutta syötettä seurattaessa nykyiset uutiset ohitetaan, ja viesti lähtee vasta seuraavasta uudesta uutisesta. Etuliite näkyy viestin alussa, esim.":
      "The Command field (e.g. WX) lets you request the feed's latest headline from your radio. When a new feed is added, current items are skipped and a message is sent from the next new item. The prefix is shown at the start of the message, e.g.",
    "Tarkista syötteet nyt": "Check feeds now",
    "Testiviesti": "Test message",
    "Monitor for BrandMeister: testiviesti": "Monitor for BrandMeister: test message",
    "Lähetä testiviesti": "Send test message",
    "Komennot radiosta": "Commands from your radio",
    "Lähetä radiosta tekstiviesti Brandmeisterin APRS-palvelunumeroon": "Send a text message from your radio to BrandMeister's APRS service number",
    "(yksityisviesti) muodossa": "(private message) in the form",
    ", esim.": ", e.g.",
    ". Ohjelma vastaa radioosi.": ". The program replies to your radio.",
    "Komennot:": "Commands:",
    "(kuka puhui viimeksi) ·": "(who spoke last) ·",
    "(milloin ja missä kuultu) ·": "(when and where heard) ·",
    "(lähetykset seuratuissa ryhmissä) · syötteiden komennot (esim.": "(transmissions in monitored groups) · feed commands (e.g.",
    "= uusin otsikko).": "= latest headline).",
    "Vastaa komentoihin": "Reply to commands",
    "Salli kaikille (ei suositella)": "Allow everyone (not recommended)",
    "Lisäksi sallitut kutsut": "Additional allowed callsigns",
    "Oletuksena komentoja saa lähettää oma kutsusi ja vastaanottajien kutsut (kaikki SSID:t).":
      "By default, commands are accepted from your own callsign and the recipients' callsigns (all SSIDs).",
    "Syötteiden tila": "Feed status", "Syöte": "Feed", "Tarkistettu": "Checked", "Uusin otsikko": "Latest headline", "Tila": "Status",
    "Viestiloki": "Message log", "Aika": "Time", "Suunta": "Direction", "Kenelle / keneltä": "To / from", "Teksti": "Text",
    "Ei viestejä vielä.": "No messages yet.",
    "Lähetys ei ole päällä.": "Sending is not enabled.",
    "APRS-IS yhdistetty": "APRS-IS connected", ", tunnus vahvistettu": ", login verified",
    "APRS-IS ei yhteyttä": "APRS-IS not connected",
    "pois päältä": "disabled", "odottaa": "waiting", "OK · {n} uutista": "OK · {n} items",
    "↓ saapunut": "↓ incoming", "↑ lähtevä": "↑ outgoing",
    "Esikatsele": "Preview", "Poista": "Remove", "Etuliite": "Prefix", "Komento": "Command",
    "Radiokomento, esim. SAA": "Radio command, e.g. WX",
    "Virhe: {e}": "Error: {e}",
    // viestien tilat (palvelimelta)
    "kuitattu": "acknowledged", "vastaanotettu": "received", "lähetetty (ei kuittausta)": "sent (no ack)",
    "lähetetty": "sent", "jonossa": "queued", "uudelleen": "retrying", "hylätty": "rejected",
    "ei kuittauspyyntöä": "no ack requested", "lähetys pois päältä": "sending disabled",
    "ei vastaanottajia": "no recipients", "ei kuittausta": "no ack", "virhe: passcode": "error: passcode",

    // ---- seuranta ----
    "Puheryhmät": "Talkgroups", "Kuuntele Hoselinella": "Listen on Hoseline",
    "Nimi": "Name", "Viimeksi": "Last", "Puhuja": "Talker", "Kesto": "Duration", "Kautta": "Via",
    "Avauksia": "Keyups", "Yht. aika": "Total time",
    "Ei liikennettä vielä seuratuissa puheryhmissä.": "No traffic in the monitored talkgroups yet.",
    "Viimeisimmät puhelut": "Latest calls", "Kaikki seuratut": "All monitored",
    "Näytettävien puheluiden määrä": "Number of calls to show",
    "10 viimeisintä": "Last 10", "15 viimeisintä": "Last 15", "20 viimeisintä": "Last 20", "25 viimeisintä": "Last 25",
    "Kutsu": "Callsign", "Ei puheluita.": "No calls.",
    "LÄHETYS": "ON AIR", "ei liikennettä": "no traffic",
    "Lopeta kuuntelu": "Stop listening", "Kuuntele TG {tg}": "Listen to TG {tg}", "Lopeta TG {tg}": "Stop TG {tg}",
    "Näytä kaikki puhelut": "Show all calls",
    "Alle {n} s puhelut ohitetaan asetusten mukaan ({k} ohitettu).": "Calls shorter than {n} s are skipped per your settings ({k} skipped).",
    "Yhdistetty · {n} tapahtumaa": "Connected · {n} events", " · viimeisin {ago}": " · latest {ago}",
    "Ei yhteyttä": "Not connected", "Seurataan: {list}": "Monitoring: {list}",
    "{n} s sitten": "{n} s ago", "{n} min sitten": "{n} min ago", "{h} h {m} min sitten": "{h} h {m} min ago", "{n} pv sitten": "{n} d ago",
    "APRS-viestit": "APRS messages", "Keneltä → kenelle": "From → to", "Viesti": "Message",
    "Lähettäjä": "Sender", "Vastaanottaja": "Recipient",
    "Klikkaa viestiä vastataksesi tai kirjoita uusi": "Click a message to reply or write a new one",
    "Lähetä": "Send", "Lähetetään…": "Sending…",
    "Lähetetty": "Sent", " ({n} osaa)": " ({n} parts)", " – tila päivittyy listaan": " – status updates in the list",
    "{n} merkkiä": "{n} chars", " · {p} viestiä": " · {p} messages", "(ohjelma)": "(program)",
    "Aseta oma APRS-IS-tunnus ja passcode välilehdellä Syötteet ja viestit, niin viestit näkyvät tässä.":
      "Set your APRS-IS login and passcode on the Feeds & messages tab and messages will appear here.",
    "APRS-IS-yhteys ei ole auki": "The APRS-IS connection is not open",
    "Ei viestejä vielä. Näytetään kutsujen {c} kaikki saapuvat ja lähtevät APRS-viestit.":
      "No messages yet. All incoming and outgoing APRS messages for {c} are shown here.",

    // ---- check-in ----
    "Check-in": "Check-in",
    "Check-in-netti": "Check-in net", "Netti": "Net", "Kierros": "Round", "Päivämäärä": "Date", "Operaattori": "Operator",
    "Aloita kierros": "Start round", "Päätä kierros": "End round",
    "Suomi": "Finland", "Viro": "Estonia", "Worldwide": "Worldwide", "Muu puheryhmä…": "Other talkgroup…",
    "kirjattu": "logged", "odottaa": "waiting", "kuultu verkossa": "heard on network", "eri toistinta": "repeaters",
    "Kuullut asemat": "Heard stations",
    "saapumisjärjestyksessä – rivit eivät vaihda paikkaa": "in order of arrival – rows never move",
    "Hae kutsu": "Find callsign", "esim. KXA": "e.g. KXA",
    "Enter kirjaa, kun haku osuu yhteen kirjaamattomaan asemaan. Esc tyhjentää.":
      "Enter logs the station when the search matches exactly one unlogged station. Esc clears.",
    "Tila": "Status", "Kutsu / nimi": "Callsign / name", "RPT / hotspot": "RPT / hotspot", "Kunta": "Town", "Kirjaus": "Log",
    "Aloita kierros, niin puheryhmässä avaavat asemat tulevat tähän.": "Start a round and stations keying up in the talkgroup appear here.",
    "Odotetaan ensimmäistä asemaa puheryhmässä {tg}…": "Waiting for the first station in talkgroup {tg}…",
    "Lista ei vieritä itsestään. Kutsua klikkaamalla aukeaa aseman kortti.": "The list never scrolls by itself. Click a callsign to open the station card.",
    "↓ {n} uutta asemaa listan lopussa": "↓ {n} new stations at the end of the list",
    "{c} (NRO {n}, jo kirjattu)": "{c} (NRO {n}, already logged)", "lähettää nyt": "transmitting now", "lähetti {ago}": "transmitted {ago}",
    "{n} asemaa pelkällä DMR ID:llä piilotettu (asema näkyy, kun kutsu saadaan).": "{n} stations with only a DMR ID hidden (a station appears once its callsign is known).",
    "Kierroksen loki": "Round log", "Ei kirjattuja asemia.": "No logged stations.",
    "Kopioi uudet rivit": "Copy new rows", "Kopioi uudet rivit ({n})": "Copy new rows ({n})", "Kopioi kaikki": "Copy all", "Lataa CSV": "Download CSV",
    "Rivit kopioituvat ilman otsikkoriviä sarakkeisiin A–F: NRO, kutsu, RPT/HOTSPOT, KUNTA, Lisätietoja, Oper. Liitä ne Google-taulukkoon ensimmäiseen tyhjään riviin sarakkeeseen A. \"Kopioi uudet rivit\" kopioi vain edellisen kopioinnin jälkeen kirjatut. Jos taulukko on yhdistetty, kopiointia ei tarvita.":
      "Rows are copied without a header row into columns A–F: NRO, callsign, RPT/HOTSPOT, KUNTA, Lisätietoja, Oper. Paste them into column A of the first empty row of the Google sheet. \"Copy new rows\" copies only the rows logged since the last copy. When the sheet is connected, copying is not needed.",
    "Päättyneet kierrokset tallentuvat kansioon {p}": "Finished rounds are saved in {p}",
    "LÄHETYS": "ON AIR", "päällekkäin": "overlapping", "uusi": "new",
    "Kuultu – kirjaa": "Heard – log", "Peru kirjaus": "Undo log", "Kirjaa {c} lokiin": "Log {c}",
    "● Kierros {r} käynnissä · TG {tg} · {d}": "● Round {r} running · TG {tg} · {d}",
    "Kierros {r} päättyi {t}": "Round {r} ended {t}",
    "Aloitetaanko uusi kierros? Edellisen kierroksen loki on tallessa CSV-tiedostona.": "Start a new round? The previous round's log is saved as a CSV file.",
    "Päätetäänkö kierros? Loki tallennetaan CSV-tiedostona.": "End the round? The log is saved as a CSV file.",
    "Perutaanko aseman {c} kirjaus (NRO {n})?": "Undo logging {c} (NRO {n})?",
    "Perutaanko aseman {c} kirjaus (NRO {n})? Rivi on jo kopioitu taulukkoon – sen jälkeiset numerot siirtyvät yhdellä.":
      "Undo logging {c} (NRO {n})? The row has already been copied to the sheet – the following numbers shift by one.",
    "Haku osuu {n} asemaan – tarkenna": "The search matches {n} stations – type more",
    "Kopioitu taulukkoon": "Copied to the sheet",
    "{n} riviä kopioitu – liitä taulukkoon": "{n} rows copied – paste them into the sheet",
    "Kopiointi ei onnistunut": "Copying failed",
    "BM-laite": "BM device", "Oper": "Oper", "Lisätietoja": "Additional info",

    "Google-taulukko": "Google Sheet", "Päivitä nyt": "Refresh now", "Irrota taulukko": "Disconnect sheet",
    "Skriptin osoite": "Script address", "Parituskoodi": "Pairing code", "Yhdistä": "Connect",
    "Kun taulukko on yhdistetty, kirjaukset menevät taulukkoon heti ja lokinumeron antaa taulukko, joten useampi ylläpitäjä voi kirjata samaan aikaan. Taulukon omistaja antaa skriptin osoitteen ja kertakäyttöisen parituskoodin (taulukon valikko Check-in → Lisää ylläpitäjä). Ilman taulukkoa lokin voi kopioida leikepöydän kautta.":
      "When the sheet is connected, entries go to the sheet immediately and the sheet assigns the log numbers, so several operators can log at the same time. The sheet owner gives you the script address and a one-time pairing code (sheet menu Check-in → Add operator). Without a sheet, copy the log via the clipboard.",
    "Yhdistetään…": "Connecting…",
    "Irrotetaanko taulukko? Avain poistetaan tältä koneelta, ja yhdistäminen vaatii uuden parituskoodin.":
      "Disconnect the sheet? The key is removed from this computer and reconnecting needs a new pairing code.",
    "virhe": "error", "yhdistetty": "connected", "ei käytössä – loki kopioidaan leikepöydän kautta": "not in use – copy the log via the clipboard",
    "Ylläpitäjä {c}": "Operator {c}", "välilehti {s}": "tab {s}", "päivitetty {ago}": "updated {ago}",
    "avain salasanavarastossa": "key in the system keychain", "avain asetustiedostossa": "key in the settings file",
    // ---- kuuntelu ----
    "KUUNTELU": "LISTENING", "YHDISTETÄÄN": "CONNECTING", "VIRHE": "ERROR",
    "Odotetaan liikennettä…": "Waiting for traffic…",
    "Ääni alkaa, kun joku puhuu valitussa ryhmässä.": "Audio starts when someone talks in the selected group.",
    "yhteys katkesi, yhdistetään uudelleen…": "connection lost, reconnecting…", "yhteysvirhe": "connection error",
    "Signaalin taso": "Signal level", "Äänenvoimakkuus": "Volume", "Lopeta": "Stop", "Sulje": "Close",

    // ---- kutsukortti ----
    "haetaan…": "loading…", "virhe": "error",
    "Poista kaverilistalta": "Remove from friends", "Lisää kaverilistaan": "Add to friends",
    "Brandmeister-profiili ↗": "BrandMeister profile ↗",
    "Rekisteri": "Registry", "APRS-tunnus": "APRS callsign", "ei asetettu": "not set", "Ei rekisteritietoja.": "No registry data.",
    "Laitteet (toistimet ja hotspotit)": "Devices (repeaters and hotspots)",
    "Tyyppi": "Type", "Sijainti": "Location", "Taajuus": "Frequency", "Nähty": "Seen", "Seuraa": "Watch",
    "Ei laitteita tällä kutsulla.": "No devices for this callsign.",
    "Kuultu verkossa (ohjelman käynnistyksen jälkeen)": "Heard on the network (since the program started)",
    "Missä": "Where", "NYT": "NOW", "Ei kuultu vielä.": "Not heard yet.",
    "Puhelut seuratuissa ryhmissä": "Calls in monitored groups", "Seurannassa ✓": "Watching ✓",

    // ---- kaverit ----
    "Kaverilista": "Friends list", "Seurattavat kutsut tai DMR-ID:t": "Callsigns or DMR IDs to watch",
    "Hälytä, kun asema ollut hiljaa (min)": "Alert when the station has been quiet for (min)",
    "0 = hälytä jokaisesta avauksesta": "0 = alert on every keyup",
    "Vain seuratut puheryhmät": "Only monitored talkgroups",
    "Työpöytäilmoitus": "Desktop notification", "Äänimerkki": "Sound", "Tekstiviesti radioon": "Text message to radio",
    "Testaa hälytys": "Test alert", "Salli työpöytäilmoitukset": "Allow desktop notifications",
    "Kaverien tila": "Friends' status", "Lisää kutsuja kaverilistaan.": "Add callsigns to the friends list.",
    "Hälytykset": "Alerts", "Kuka / mitä": "Who / what", "Ei hälytyksiä.": "No alerts.",
    "kuultu": "heard", "ei kuultu": "not heard",
    "Palvelu": "Service", "Kaveri": "Friend", "Laite pois": "Device offline", "Laite palasi": "Device back", "Testi": "Test",
    "Laite {call} pois verkosta": "Device {call} went offline", "Laite {call} palasi verkkoon": "Device {call} is back online",
    "{who} aktiivinen": "{who} active",
    "Selain ei tue työpöytäilmoituksia.": "The browser does not support desktop notifications.",
    "Työpöytäilmoitukset sallittu.": "Desktop notifications allowed.",
    "Työpöytäilmoitukset estetty selaimen asetuksissa.": "Desktop notifications are blocked in the browser settings.",
    "Työpöytäilmoituksia ei ole vielä sallittu.": "Desktop notifications have not been allowed yet.",
    "Testihälytys": "Test alert",

    // ---- laitteet ----
    "Seurattavat toistimet ja hotspotit": "Watched repeaters and hotspots",
    "Lisää laitteen ID": "Add device ID", "244207 tai 244203901": "244207 or 244203901",
    "Lisää": "Add", "tai hae kutsulla": "or search by callsign", "Hae laitteet": "Find devices",
    "Hälytä, kun ei nähty (min)": "Alert when not seen for (min)",
    "Ei seurattavia laitteita. Lisää oma hotspotisi tai paikallinen toistin.": "No watched devices. Add your own hotspot or a local repeater.",
    "API-avain käytössä – hallintapainikkeet toimivat omille laitteillesi.": "API key in use – the management buttons work for your own devices.",
    "Hallintapainikkeet (puheryhmien lisäys ja poisto, tyhjennys, katkaisu, nollaus) vaativat API-avaimen: Asetukset → Brandmeister API-avain.":
      "The management buttons (add/remove talkgroups, clear, drop call, reset) need an API key: Settings → BrandMeister API key.",
    "Haetaan…": "Searching…", "Lisää seurantaan": "Watch", "Lisätty ✓": "Added ✓",
    "tarkistetaan…": "checking…", "● Verkossa": "● Online", "● Ei verkossa": "● Offline",
    "Poista pysyvä TG": "Remove static TG", "ei": "none", "Poista seurannasta": "Stop watching",
    "nähty {ago}": "seen {ago}", "Viimeisin liikenne:": "Latest traffic:", "nyt": "now",
    "Pysyvät:": "Static:", "Dynaamiset:": "Dynamic:", "Ajastetut:": "Timed:",
    "Lisää pysyvä TG": "Add static TG", "Nollaa yhteys": "Reset connection",
    "Tyhjennä dynaamiset ({s})": "Clear dynamic ({s})", "Katkaise puhelu ({s})": "Drop call ({s})",
    "Anna puheryhmä": "Enter a talkgroup", "Tehty": "Done",
    "Nollataanko laitteen yhteys Brandmeisteriin? Laite yhdistää uudelleen hetken kuluttua.":
      "Reset the device's connection to BrandMeister? The device reconnects shortly.",

    // ---- tilastot ----
    "Aktiivisuus seuratuissa puheryhmissä": "Activity in monitored talkgroups",
    "7 pv": "7 d", "14 pv": "14 d", "Puheluita aikajaksoittain": "Calls per period",
    "Näytä taulukkona": "Show as table", "Piilota taulukko": "Hide table",
    "Eniten puhuneet": "Top talkers", "Puheaika": "Talk time", "Vilkkaimmat puheryhmät": "Busiest talkgroups", "Asemia": "Stations",
    "Brandmeisterin historia: ryhmässä kuullut asemat": "BrandMeister history: stations heard in a group",
    "Puheryhmä": "Talkgroup", "Hae": "Search", "Viimeksi kuultu": "Last heard",
    "Puheluita": "Calls", "Eri asemia": "Unique stations", "Vilkkain tunti": "Busiest hour", "Vilkkain päivä": "Busiest day",
    "Tunti": "Hour", "Päivä": "Day", "Ei dataa": "No data", " t.": "k",
    "Ei vielä tallennettuja puheluita.": "No recorded calls yet.",
    "Tilastot perustuvat tämän ohjelman tallentamiin puheluihin {t} alkaen (seuratut puheryhmät).":
      "Statistics are based on calls recorded by this program since {t} (monitored talkgroups).",
    "{n} puhelua · {d}": "{n} calls · {d}",
    "Anna puheryhmän numero": "Enter a talkgroup number", "Haetaan Brandmeisteriltä…": "Fetching from BrandMeister…",
    "TG {tg} {name}: {u} asemaa, {c} avausta": "TG {tg} {name}: {u} stations, {c} keyups", " (vanhin {t})": " (oldest {t})",

    // ---- palvelut ----
    "Oma sijainti": "Your location", "Koordinaatit tai lokaattori": "Coordinates or locator",
    "60.17, 24.94 tai KP20LE": "60.17, 24.94 or KP20LE",
    "Tallenna sijainti": "Save location", "Käytä selaimen sijaintia": "Use browser location",
    "Sijaintia käyttävät salamahälytys, säävaroitukset ja tieliikennetiedotteet. Sijainti tallentuu vain tälle koneelle.":
      "The location is used by lightning alerts, weather warnings and road traffic bulletins. It is stored only on this computer.",
    "Selain ei tue sijaintia": "The browser does not support location", "Haetaan sijaintia…": "Getting location…",
    "Sijainti tallennettu": "Location saved", "Sijaintia ei saatu: {e}": "Could not get location: {e}",
    "· lokaattori": "· locator", "Sijaintia ei ole asetettu": "Location not set",
    "Päällä": "On", "Pois": "Off", "Pois päältä.": "Off.",
    "Ensimmäinen tarkistus käynnissä…": "First check in progress…",
    "Tarkistettu {ago}": "Checked {ago}", " · viimeisin hälytys {ago}": " · last alert {ago}",
    "⚡ Salamahälytys": "⚡ Lightning alert", "Säde (km)": "Radius (km)",
    "Uusi hälytys aikaisintaan (min)": "Next alert at the earliest after (min)",
    "Hälyttää heti uudestaan, jos salamat lähestyvät yli 5 km.": "Alerts again immediately if the strikes come more than 5 km closer.",
    "Vain maasalamat": "Ground strikes only",
    "Iskuja 60 min / {r} km": "Strikes 60 min / {r} km", "Lähin isku": "Nearest strike",
    "maasalama": "ground", "pilvisalama": "cloud",
    "⚠ Säävaroitukset": "⚠ Weather warnings", "Vähintään": "At least",
    "Keltainen": "Yellow", "Oranssi": "Orange", "Punainen": "Red",
    "Myös liikennesää": "Also road weather",
    "Ilmatieteen laitoksen viralliset varoitukset, jotka koskevat omaa sijaintiasi (kuntarajojen mukaan).":
      "Official Finnish Meteorological Institute warnings covering your location (by municipality).",
    "Klikkaa nähdäksesi tarkemmin": "Click for details",
    "Ei voimassa olevia varoituksia sijainnissasi.": "No warnings in effect at your location.",
    "☀ Avaruussää": "☀ Space weather", "Hälytä, kun Kp ≥": "Alert when Kp ≥",
    "Auringonpurkaukset (≥ M5)": "Solar flares (≥ M5)", "NOAA:n K-ennusteet": "NOAA K forecasts",
    "Kp 5+ = geomagneettinen myrsky: revontulikelit VHF:llä mahdollisia, HF-kelit heikkenevät. Ei vaadi sijaintia.":
      "Kp 5+ = geomagnetic storm: VHF aurora propagation possible, HF conditions degrade. No location needed.",
    "Kp nyt": "Kp now", "myrsky": "storm", "levoton": "unsettled", "rauhallinen": "quiet", "Suurin 6 h": "Max 6 h",
    "NOAA:n viimeisimmät ilmoitukset (Kp-raja {thr} tai purkaukset)": "Latest NOAA alerts (Kp threshold {thr} or flares)",
    "🌌 Revontulihälytys": "🌌 Aurora alert", "Hälytysraja (nT/h)": "Alert threshold (nT/h)",
    "Vain kun on pimeää": "Only when dark", "Ei kun täysin pilvistä": "Not when fully overcast",
    "Lähimmän Ilmatieteen laitoksen magnetometrin kentän vaihtelu viimeisen tunnin aikana. Raja = mahdollisia, 2× = todennäköisiä, 4× = voimakkaita (uusi hälytys tason noustessa). Etelä-Suomessa 100 nT/h on hyvä lähtökohta, Lapissa noin 50.":
      "Field variation at the nearest Finnish Meteorological Institute magnetometer during the last hour. Threshold = possible, 2× = likely, 4× = strong (new alert when the level rises). 100 nT/h is a good start in southern Finland, about 50 in Lapland.",
    "rauhallista": "quiet", "mahdollisia": "possible", "todennäköisiä": "likely", "voimakkaita": "strong",
    "Pimeys": "Darkness", "pimeää": "dark", "valoisaa": "light", "aurinko {s}°": "sun {s}°", "Pilvisyys": "Cloud cover",
    "Asemat pohjoisesta etelään. Raja {thr} nT/h.": "Stations from north to south. Threshold {thr} nT/h.",
    "☢ Säteilyvalvonta": "☢ Radiation monitoring", "Hälytysraja (µSv/h)": "Alert threshold (µSv/h)", "0 = koko Suomi": "0 = all of Finland",
    "STUK:n noin 250 mittausasemaa, päivitys 10 min välein. Normaali taso Suomessa n. 0,05–0,3 µSv/h; sade voi nostaa lukemaa hetkellisesti. Oletusraja 0,4 µSv/h on STUK:n oma hälytysraja.":
      "About 250 STUK monitoring stations, updated every 10 min. The normal level in Finland is approx. 0.05–0.3 µSv/h; rain can raise the reading temporarily. The default threshold of 0.4 µSv/h is STUK's own alert level.",
    "Lähin: {name} ({km} km)": "Nearest: {name} ({km} km)",
    "Korkein {r} km säteellä ({n} asemaa)": "Highest within {r} km ({n} stations)",
    "Korkein Suomessa ({n} asemaa)": "Highest in Finland ({n} stations)",
    "Korkein koko Suomessa": "Highest in all of Finland",
    "🚧 Tieliikennetiedotteet": "🚧 Road traffic bulletins",
    "Ilmoittaa tiedotteet, jotka osuvat": "Reports bulletins that fall within the",
    "säteelle": "radius", ", valittuihin": ", in the selected", "maakuntiin": "regions",
    "tai valituille": "or on the selected", "teille": "roads", "(riittää, että yksi ehto täyttyy).": "(any one condition is enough).",
    "Säde oman sijainnin ympärillä": "Radius around your location", "tai muu keskipiste": "or another centre point",
    "61.50, 23.76 tai KP11RL": "61.50, 23.76 or KP11RL", "0 = ei sädettä": "0 = no radius",
    "Tienumerot": "Road numbers", "Maakunnat": "Regions", "Tiedotetyypit": "Bulletin types",
    "Ilmoita myös päivitykset": "Report updates too", "Tarkka paikka hälytykseen": "Exact place in alert",
    "{n} voimassa olevaa tiedotetta valituilla ehdoilla · klikkaa nähdäksesi tarkemmin": "{n} active bulletins matching your conditions · click for details",
    "Ei tiedotteita valituilla ehdoilla.": "No bulletins matching your conditions.",
  };

  const ATTRS = ["placeholder", "title", "aria-label"];
  const SKIP = "script,style,code,textarea,[data-noi18n]";
  const orig = new WeakMap();       // tekstisolmu -> {fi, out}
  const origAttr = new WeakMap();   // elementti -> {attr: {fi, out}}

  let lang = null;
  try { lang = localStorage.getItem("bmm-lang"); } catch (e) { /* ei tallennusta */ }
  if (lang !== "fi" && lang !== "en") lang = (navigator.language || "").toLowerCase().startsWith("fi") ? "fi" : "en";
  window.LANG = lang;

  function lookup(raw) {
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(raw);
    const key = m[2].replace(/\s+/g, " ");
    if (!key) return raw;
    const t = EN[key];
    return t == null ? raw : m[1] + t + m[3];
  }

  window.T = function (s, vars) {
    let out = window.LANG === "en" && EN[s] != null ? EN[s] : s;
    if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null ? vars[k] : m));
    return out;
  };
  window.LOC = () => (window.LANG === "en" ? "en-GB" : "fi-FI");
  window.dec = s => (window.LANG === "fi" ? String(s).replace(".", ",") : String(s));

  function skipped(el) { return el && el.closest && el.closest(SKIP); }

  function doText(n) {
    if (skipped(n.parentElement)) return;
    let o = orig.get(n);
    if (!o || (n.nodeValue !== o.fi && n.nodeValue !== o.out)) { o = {fi: n.nodeValue}; orig.set(n, o); }
    o.out = window.LANG === "en" ? lookup(o.fi) : o.fi;
    if (n.nodeValue !== o.out) n.nodeValue = o.out;
  }

  function doAttrs(el) {
    if (skipped(el)) return;
    let rec = origAttr.get(el);
    for (const a of ATTRS) {
      if (!el.hasAttribute(a)) continue;
      const v = el.getAttribute(a);
      rec = rec || {};
      let o = rec[a];
      if (!o || (v !== o.fi && v !== o.out)) { o = rec[a] = {fi: v}; }
      o.out = window.LANG === "en" ? lookup(o.fi) : o.fi;
      if (v !== o.out) el.setAttribute(a, o.out);
    }
    if (rec) origAttr.set(el, rec);
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1) return;
    doAttrs(root);
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = it.nextNode())) { if (n.nodeType === 3) doText(n); else doAttrs(n); }
  }

  window.setLang = function (l) {
    window.LANG = l === "en" ? "en" : "fi";
    try { localStorage.setItem("bmm-lang", window.LANG); } catch (e) { /* ei tallennusta */ }
    document.documentElement.lang = window.LANG;
    walk(document.body);
  };

  const mo = new MutationObserver(muts => {
    for (const m of muts) {
      if (m.type === "characterData") doText(m.target);
      else if (m.type === "attributes") doAttrs(m.target);
      else m.addedNodes.forEach(walk);
    }
  });
  document.documentElement.lang = window.LANG;
  walk(document.body);
  mo.observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS});
  window.I18N_EN = EN;   // testejä varten
})();
