# Palautteen vastaanotin (ylläpitäjälle)

Ohjelman **Asetukset → Ohjelma → Lähetä palaute / lokit** lähettää viestin ja zip-paketin tähän Google Apps
Script -verkkosovellukseen. Skripti lähettää ne sähköpostiin liitteenä. Ohjelmassa ei ole salaisuuksia:
skriptin osoite on julkinen, ja skripti rajoittaa koon (noin 5 Mt) ja määrän (40 / vrk, 5 / kutsu / tunti).

## Asennus (kerran)

1. Avaa https://script.google.com → **Uusi projekti**. Nimeä se esim. "Monitor-palaute".
2. Korvaa `Code.gs`:n sisältö tämän kansion `Code.gs`:llä.
3. **Project Settings** (rataskuvake) → rastita **Show "appsscript.json" manifest file in editor**.
   Korvaa editorissa `appsscript.json`:n sisältö tämän kansion `appsscript.json`:lla.
4. **Project Settings → Script Properties → Add script property:**
   `NOTIFY_EMAIL` = osoite, johon palautteet tulevat.
5. **Deploy → New deployment →** tyyppi **Web app**:
   - *Execute as:* **Me**
   - *Who has access:* **Anyone**
   - **Deploy** → hyväksy oikeus "Send email as you" → kopioi **Web app URL** (`https://script.google.com/macros/s/…/exec`).
6. Testaa: avaa osoite selaimessa. Vastauksen pitää olla `{"ok":true,"app":"monitor-feedback","configured":true}`.
7. Lisää osoite ohjelmaan (`bmmonitor/feedback.py`, `FEEDBACK_URL`) ja julkaise uusi versio.

Kun muutat skriptiä myöhemmin: **Deploy → Manage deployments → muokkaa → Version: New version**, jolloin
osoite pysyy samana.
