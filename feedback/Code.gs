/**
 * Monitor for Amateur Radio – palautteen vastaanotin (Google Apps Script -verkkosovellus).
 *
 * Ohjelma lähettää JSON-viestin: viesti, kutsu, vastausosoite, versio ja zip-paketti (base64).
 * Skripti lähettää ne ylläpidon sähköpostiin liitteenä. Mitään ei tallenneta Driveen.
 *
 * Asennus: katso feedback/README.md. Ilmoitusten osoite asetetaan skriptin ominaisuuksiin
 * (Project Settings → Script Properties → NOTIFY_EMAIL), ei tähän tiedostoon.
 */
const MAX_ZIP_B64 = 6800000;      // noin 5 Mt zip
const MAX_PER_DAY = 40;           // palautteita vuorokaudessa (kaikki lähettäjät yhteensä)
const MAX_PER_CALL_HOUR = 5;      // samalta kutsulta tunnissa

function doGet() {
  return json_({ ok: true, app: 'monitor-feedback', configured: !!notifyEmail_() });
}

function doPost(e) {
  let b;
  try {
    b = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return json_({ ok: false, code: 'bad', error: 'Virheellinen pyyntö' });
  }
  if (b.op !== 'feedback') return json_({ ok: false, code: 'bad', error: 'Tuntematon pyyntö' });
  const to = notifyEmail_();
  if (!to) return json_({ ok: false, code: 'config', error: 'Vastaanottajaa ei ole asetettu' });

  const message = String(b.message || '').slice(0, 5000).trim();
  const call = String(b.call || '').toUpperCase().replace(/[^A-Z0-9-]/g, '').slice(0, 12) || 'ANON';
  const reply = String(b.email || '').trim().slice(0, 120);
  const version = String(b.version || '').slice(0, 20);
  const plat = String(b.platform || '').slice(0, 120);
  const zip = String(b.zip || '');
  if (!message) return json_({ ok: false, code: 'bad', error: 'Viesti puuttuu' });
  if (zip.length > MAX_ZIP_B64) return json_({ ok: false, code: 'size', error: 'Paketti on liian suuri' });

  // määrärajat (CacheService: päivä ja kutsu/tunti)
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const cache = CacheService.getScriptCache();
    const dayKey = 'day-' + Utilities.formatDate(new Date(), 'UTC', 'yyyyMMdd');
    const callKey = 'call-' + call + '-' + Utilities.formatDate(new Date(), 'UTC', 'yyyyMMddHH');
    const nDay = Number(cache.get(dayKey) || 0), nCall = Number(cache.get(callKey) || 0);
    if (nDay >= MAX_PER_DAY || nCall >= MAX_PER_CALL_HOUR) return json_({ ok: false, code: 'rate', error: 'Liikaa palautteita' });
    cache.put(dayKey, String(nDay + 1), 21600 * 4);
    cache.put(callKey, String(nCall + 1), 3600);
  } finally {
    lock.releaseLock();
  }

  const name = String(b.name || 'monitor-support.zip').replace(/[^A-Za-z0-9._-]/g, '').slice(0, 80) || 'monitor-support.zip';
  const opts = {
    name: 'Monitor for Amateur Radio',
    body: [
      'Palaute: ' + call + (reply ? ' <' + reply + '>' : ''),
      'Versio: ' + version + ' · ' + plat,
      '',
      message,
      '',
      '— Liitteenä lokit ja asetukset ilman salaisuuksia (' + name + ').',
    ].join('\n'),
  };
  if (/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(reply)) opts.replyTo = reply;
  if (zip) {
    try {
      opts.attachments = [Utilities.newBlob(Utilities.base64Decode(zip), 'application/zip', name)];
    } catch (err) {
      return json_({ ok: false, code: 'bad', error: 'Virheellinen liite' });
    }
  }
  MailApp.sendEmail(to, 'Monitor-palaute: ' + call + ' (' + version + ')', opts.body, opts);
  return json_({ ok: true });
}

function notifyEmail_() {
  return PropertiesService.getScriptProperties().getProperty('NOTIFY_EMAIL') || '';
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
