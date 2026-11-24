# SignPath Foundation – käyttöönotto (ylläpitäjälle)

Ohjelma on valmisteltu allekirjoitettavaksi: versiotiedot, tietosuojaseloste (PRIVACY.md), Code signing policy
(CODE_SIGNING.md) ja julkaisutyönkulku, joka allekirjoittaa Windows-tiedostot heti, kun avain on asetettu.

## 1. Kaksivaiheinen tunnistautuminen
- GitHub: Settings → Password and authentication → Two-factor authentication (pakollinen).
- SignPath-tilillä sama, kun tili on luotu.

## 2. Hakemus
- https://signpath.org/apply – projektin tiedot:
  - Repository: https://github.com/hannulma/monitor-for-amateur-radio
  - License: MIT
  - Download page: https://github.com/hannulma/monitor-for-amateur-radio/releases
  - Code signing policy: https://github.com/hannulma/monitor-for-amateur-radio/blob/main/CODE_SIGNING.md
  - Privacy policy: https://github.com/hannulma/monitor-for-amateur-radio/blob/main/PRIVACY.md
  - Build: GitHub Actions, GitHub-hosted runners, PyInstaller + Inno Setup
  - Artifacts: MonitorForBrandMeister.exe ja MonitorForBrandMeister-<versio>-windows-setup.exe

## 3. Kun hakemus on hyväksytty (SignPathin sivulla)
1. Lisää organisaatioon *Trusted Build System* "GitHub.com" ja liitä se projektiin. Asenna SignPathin GitHub App repoon.
2. Projektin slug: `monitor-for-amateur-radio`.
3. Artefaktikonfiguraatiot (sisältö tämän kansion XML-tiedostoista):
   - slug `exe` ← `artifact-exe.xml`
   - slug `installer` ← `artifact-installer.xml`
4. Allekirjoituskäytäntö (signing policy) slug `release-signing`, hyväksyjäksi oma tunnuksesi.
5. Luo API-token (CI-käyttäjälle, oikeus lähettää allekirjoituspyyntöjä).

## 4. GitHubiin (repo → Settings → Secrets and variables → Actions)
- **Secret** `SIGNPATH_API_TOKEN` = API-token.
- **Variable** `SIGNPATH_ORGANIZATION_ID` = organisaation tunnus (näkyy SignPathin osoitteessa app.signpath.io/Web/<ID>/...).
- Valinnaiset muuttujat, jos käytät eri nimiä: `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_POLICY_SLUG`,
  `SIGNPATH_ARTIFACT_EXE`, `SIGNPATH_ARTIFACT_SETUP`.

## 5. Julkaisu allekirjoituksen kanssa
- Versionumeron nosto käynnistää julkaisun kuten ennenkin.
- Windows-vaihe pysähtyy kahdesti odottamaan hyväksyntää (ohjelmatiedosto ja asennusohjelma). Hyväksy pyynnöt
  SignPathin sivulla; odotusaika on enintään 4 tuntia per pyyntö.
- Jos hyväksyntää ei tule, julkaisu ei valmistu – ajon voi käynnistää uudelleen GitHubissa.
- Ilman `SIGNPATH_API_TOKEN`-salaisuutta julkaisu tehdään kuten ennenkin, allekirjoittamattomana.
