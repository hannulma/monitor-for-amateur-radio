# Check-in-taulukon skripti (Google Sheets)

Tällä skriptillä Monitor for BrandMeister -ohjelma kirjaa check-in-netin asemat suoraan Google-taulukkoon.
Kirjaukset näkyvät taulukossa heti, ja useampi ylläpitäjä voi kirjata samaan aikaan: lokinumeron antaa
taulukko, joten numerot eivät mene päällekkäin.

Asennuksen tekee **taulukon omistaja** kerran. Ylläpitäjät eivät tarvitse Google-tunnuksia eivätkä
muokkausoikeutta taulukkoon.

## Turvallisuus lyhyesti

- Jokaisella ylläpitäjällä on **oma avain**. Omistaja luo ylläpitäjälle kertakäyttöisen parituskoodin
  taulukon valikosta; ohjelma vaihtaa koodin avaimeen. Koodi on voimassa 15 minuuttia.
- Avainta ei lähetetä verkossa: jokainen pyyntö **allekirjoitetaan** avaimella, aikaleimalla ja
  kertakäyttöisellä tunnisteella. Vanhat, muutetut ja toistetut pyynnöt hylätään.
- Kirjaaja (Oper) päätellään avaimesta – kukaan ei voi kirjata toisen nimissä.
- Avaimet ovat skriptin asetuksissa (*Script Properties*), eivät taulukossa: **taulukon katsojat eivät
  näe niitä**. Ne näkee vain se, jolla on oikeus muokata skriptiä (taulukon muokkaajat).
- Skriptillä on oikeus **vain tähän taulukkoon** (`spreadsheets.currentonly`), ei omistajan muihin
  tiedostoihin.
- Skripti kirjoittaa vain kierrosvälilehdille ja sovittuihin sarakkeisiin, estää kaavat (esim. `=IMPORTXML`),
  tarkistaa syötteet ja rajoittaa pyyntöjen määrää.
- Kaikki tapahtumat (paritukset, kirjaukset, perumiset, muokkaukset) tallentuvat piilotetulle
  välilehdelle **Check-in-tapahtumat**.
- Ylläpitäjän oikeuden voi poistaa milloin tahansa valikosta.

## Asennus

> Kokeile ensin taulukon **kopiolla** (Tiedosto → Tee kopio). Kun kaikki toimii, tee sama varsinaiseen
> taulukkoon.

1. Avaa taulukko ja valitse **Laajennukset → Apps Script**.
2. Avautuvassa editorissa:
   - Korvaa tiedoston `Code.gs` sisältö tämän kansion tiedostolla [`Code.gs`](Code.gs).
   - Valitse vasemmalta **Projektin asetukset** (ratas) ja rastita **Näytä "appsscript.json"-luettelotiedosto
     editorissa**. Palaa editoriin ja korvaa `appsscript.json` tämän kansion tiedostolla
     [`appsscript.json`](appsscript.json).
   - Tallenna (Ctrl+S / Cmd+S).
3. Tarkista `Code.gs`:n alusta kohta `CONFIG` (oletukset sopivat FinDMR-taulukkoon v0.68):

   | Asetus | Oletus | Merkitys |
   |---|---|---|
   | `TEMPLATE_PREFIX` | `LOGIPOHJA` | välilehti, josta uusi kierros kopioidaan |
   | `TAB_PREFIX` | `Kierros` | kierrosvälilehdet, esim. `Kierros 177 221126` |
   | `FIRST_ROW` | `3` | ensimmäinen tietorivi |
   | `COLS` | A NRO, B kutsu, C RPT/HOTSPOT, D KUNTA, E Lisätietoja, F Oper, **H BM-laite** | sarakkeet (G jätetään huomautuksille) |
   | `CELL_DATE`, `CELL_ROUND`, `CELL_OPER` | `D1`, `F1`, `G2` | päivämäärä, kierrosnumero ja operaattori |

   Jos pohjavälilehdellä on kaavoja sarakkeissa A–H riviltä 3 alkaen, skripti korvaa ne kirjoittamillaan
   arvoilla.

   **Alasvetovalikot:** jos RPT/HOTSPOT-sarakkeessa on valintalista toiselta välilehdeltä (esim.
   *Toistimet-data*), skripti käyttää listan arvoa, kun sama toistin löytyy (sama teksti tai sama toistimen
   kutsu). Muuten se lisää BrandMeisterin tiedon (esim. `OH2RUP Jarvenpaa`) listan ensimmäiseen tyhjään
   soluun ja käyttää sitä – lista täydentyy vähitellen BrandMeisterin tiedoilla. Lisäykset näkyvät
   tapahtumalokissa. Jos valintalistan alue on täynnä, laajenna aluetta (Tiedot → Tietojen vahvistus).
   Ohjelmasta käsin muokattaessa arvon pitää olla jo listalla, jotta kirjoitusvirheet eivät päädy listalle.
4. Valitse **Ota käyttöön → Uusi käyttöönotto**. Valitse tyypiksi (ratas) **Verkkosovellus** ja aseta:
   - *Suorita käyttäjänä:* **Minä**
   - *Kenellä on käyttöoikeus:* **Kuka tahansa**

   Valitse **Ota käyttöön**. Google pyytää valtuutusta: valitse tilisi. Koska skripti on oma eikä
   Googlen tarkastama, näkyy varoitus *"Google ei ole vahvistanut tätä sovellusta"* → **Lisäasetukset →
   Siirry projektiin (ei turvallinen)** → **Salli**. Valtuutus koskee vain tätä taulukkoa.
5. Kopioi **verkkosovelluksen URL-osoite** (`https://script.google.com/macros/s/…/exec`).
6. Lataa taulukko uudelleen selaimessa. Valikkoriville ilmestyy **Check-in**.

"Kuka tahansa" tarkoittaa, että osoitteeseen voi lähettää pyyntöjä kirjautumatta – skripti hyväksyy silti
vain pyynnöt, jotka on allekirjoitettu lisätyn ylläpitäjän avaimella.

## Ylläpitäjien hallinta

- **Check-in → Lisää ylläpitäjä / uusi parituskoodi:** anna ylläpitäjän kutsu. Skripti näyttää
  parituskoodin (esim. `K7M-42Q`). Anna ylläpitäjälle koodi ja verkkosovelluksen osoite. Ylläpitäjä
  syöttää ne ohjelmaan: **Check-in → Google-taulukko → Yhdistä**. Sama toiminto antaa uuden koodin, jos
  ylläpitäjä vaihtaa konetta – vanha avain lakkaa silloin toimimasta.
- **Check-in → Poista ylläpitäjä:** ylläpitäjän ohjelma ei voi enää kirjata.
- **Check-in → Näytä ylläpitäjät:** lisätyt ylläpitäjät ja milloin he ovat viimeksi käyttäneet taulukkoa.

## Käyttö netin aikana

Ylläpitäjä aloittaa kierroksen ohjelmassa. Jos kierroksen välilehteä ei ole, skripti luo sen pohjasta
nimellä `Kierros <numero> <ppkkvv>`, siirtää sen ensimmäiseksi ja täyttää päivämäärän, kierrosnumeron ja
operaattorin. Jos välilehti on jo olemassa (esim. toinen ylläpitäjä aloitti kierroksen), kaikki kirjaavat
samaan välilehteen. Ohjelma hakee taulukon tilan 5 sekunnin välein, joten toisen ylläpitäjän kirjaukset
näkyvät myös omassa ohjelmassa.

## Skriptin päivittäminen

Korvaa `Code.gs`, tallenna ja valitse **Ota käyttöön → Hallinnoi käyttöönottoja → muokkaa (kynä) →
Versio: Uusi versio → Ota käyttöön**. Osoite pysyy samana, eikä ylläpitäjien tarvitse tehdä mitään.

## Testaus ilman Googlea (kehittäjille)

`node sheets/test/test.js` ajaa skriptin simuloitua taulukkoa vastaan. `node sheets/test/gas-mock.js 8840`
käynnistää simuloidun verkkosovelluksen, johon ohjelma voi yhdistää (`BMM_SHEET_TEST=1`, osoite
`http://127.0.0.1:8840/exec`, parituskoodi osoitteesta `http://127.0.0.1:8840/pair/<KUTSU>`).
