/**
 * Monitor for BrandMeister – check-in-kirjaus Google-taulukkoon.
 *
 * Asennus: Laajennukset → Apps Script → liitä tämä tiedosto (Code.gs) ja appsscript.json,
 * sitten Ota käyttöön → Uusi käyttöönotto → Verkkosovellus (Suorita käyttäjänä: minä,
 * Käyttöoikeus: kuka tahansa). Tarkka ohje: sheets/README.md.
 *
 * Turvallisuus
 *  - Jokaisella ylläpitäjällä on oma avain. Omistaja luo parituskoodin valikosta
 *    "Check-in → Lisää ylläpitäjä"; ohjelma vaihtaa koodin avaimeen (koodi kelpaa kerran, 15 min).
 *  - Avainta ei lähetetä pyynnöissä: jokainen pyyntö allekirjoitetaan (HMAC-SHA256) avaimella,
 *    aikaleimalla ja kertakäyttöisellä tunnisteella. Vanhat ja toistetut pyynnöt hylätään.
 *  - Kirjaaja (Oper) päätellään avaimesta, joten kukaan ei voi kirjata toisen nimissä.
 *  - Avaimet ovat skriptin asetuksissa (Script Properties), eivät taulukossa: taulukon
 *    katsojat eivät näe niitä. Skriptin oikeudet on rajattu vain tähän taulukkoon.
 *  - Kaavat estetään (= + - @ -alkuiset arvot tallennetaan tekstinä), syötteet tarkistetaan,
 *    pyyntöjen määrää rajoitetaan ja kaikki muutokset kirjataan piilotetulle välilehdelle.
 */

var CONFIG = {
  TEMPLATE_PREFIX: 'LOGIPOHJA',          // uuden kierroksen pohjavälilehti (nimen alku)
  TAB_PREFIX: 'Kierros',                 // kierrosvälilehdet: "Kierros 176 151126"
  FIRST_ROW: 3,                          // ensimmäinen tietorivi
  // sarakkeet (A=1): NRO, kutsu, RPT/HOTSPOT, KUNTA, Lisätietoja, Oper, BM-laite
  COLS: { nro: 1, call: 2, via: 3, kunta: 4, info: 5, oper: 6, dev: 8 },
  CELL_DATE: 'D1',
  CELL_ROUND: 'F1',
  CELL_OPER: 'G2',
  AUDIT_SHEET: 'Check-in-tapahtumat',    // piilotettu tapahtumaloki
  MAX_SKEW_S: 300,                       // pyynnön aikaleiman sallittu ero
  RATE_PER_MIN: 120,                     // pyyntöjä / ylläpitäjä / minuutti
  PAIR_MINUTES: 15
};

var VERSION = 1;
var CALL_RE = /^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,4}[A-Z](\/[A-Z0-9]{1,4})?$/;
var FIELD_MAX = { call: 12, via: 80, kunta: 60, info: 200, dev: 80 };
var EDITABLE = ['via', 'kunta', 'info'];

// ============================================================ valikko (omistaja)

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Check-in')
    .addItem('Lisää ylläpitäjä / uusi parituskoodi', 'menuAddOperator')
    .addItem('Poista ylläpitäjä', 'menuRemoveOperator')
    .addItem('Näytä ylläpitäjät', 'menuListOperators')
    .addToUi();
}

function menuAddOperator() {
  var ui = SpreadsheetApp.getUi();
  var r = ui.prompt('Lisää ylläpitäjä', 'Ylläpitäjän kutsu (esim. OH6HL):', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  var call = normCall_(r.getResponseText());
  if (!call) { ui.alert('Virheellinen kutsu.'); return; }
  var code = createPairingCode_(call);
  audit_(call, 'paritus luotu', '');
  ui.alert('Parituskoodi',
    'Parituskoodi ylläpitäjälle ' + call + ':\n\n' + code + '\n\n' +
    'Koodi on voimassa ' + CONFIG.PAIR_MINUTES + ' minuuttia ja kelpaa kerran. Ylläpitäjä syöttää sen ohjelmaan ' +
    '(Check-in → Google-taulukko) yhdessä skriptin osoitteen kanssa.\n\n' +
    'Jos ylläpitäjällä oli jo avain, se lakkaa toimimasta, kun uusi koodi otetaan käyttöön.',
    ui.ButtonSet.OK);
}

function menuRemoveOperator() {
  var ui = SpreadsheetApp.getUi();
  var r = ui.prompt('Poista ylläpitäjä', 'Kutsu, jonka oikeudet poistetaan:', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  var call = normCall_(r.getResponseText());
  var props = PropertiesService.getScriptProperties();
  if (!call || !props.getProperty('op:' + call)) { ui.alert('Ylläpitäjää ei löytynyt.'); return; }
  props.deleteProperty('op:' + call);
  audit_(call, 'ylläpitäjä poistettu', '');
  ui.alert(call + ' poistettu. Hänen ohjelmansa ei voi enää kirjata taulukkoon.');
}

function menuListOperators() {
  var all = PropertiesService.getScriptProperties().getProperties();
  var lines = [];
  Object.keys(all).sort().forEach(function (k) {
    if (k.indexOf('op:') !== 0) return;
    var o = JSON.parse(all[k]);
    lines.push(k.substring(3) + ' – lisätty ' + fmtTime_(o.created) + (o.used ? ', viimeksi käytetty ' + fmtTime_(o.used) : ''));
  });
  SpreadsheetApp.getUi().alert('Ylläpitäjät', lines.length ? lines.join('\n') : 'Ei ylläpitäjiä.', SpreadsheetApp.getUi().ButtonSet.OK);
}

function createPairingCode_(call) {
  var alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  var code = '';
  for (var i = 0; i < 6; i++) code += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
  code = code.substring(0, 3) + '-' + code.substring(3);
  PropertiesService.getScriptProperties().setProperty('pair:' + code,
    JSON.stringify({ call: call, exp: Date.now() + CONFIG.PAIR_MINUTES * 60000 }));
  return code;
}

// ============================================================ verkkosovellus

function doGet() {
  return json_({ ok: true, app: 'monitor-for-brandmeister-checkin', v: VERSION });
}

function doPost(e) {
  try {
    var req = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (req.op === 'pair') return json_(pair_(req));
    var op = authenticate_(req);
    var data = JSON.parse(req.data || '{}');
    var res;
    switch (req.action) {
      case 'hello': res = { call: op }; break;
      case 'start': res = withLock_(function () { return start_(op, data); }); break;
      case 'log':   res = withLock_(function () { return logStation_(op, data); }); break;
      case 'unlog': res = withLock_(function () { return unlog_(op, data); }); break;
      case 'edit':  res = withLock_(function () { return edit_(op, data); }); break;
      case 'list':  res = list_(data); break;
      default: throw new UserError_('Tuntematon toiminto');
    }
    res.ok = true;
    res.oper = op;
    return json_(res);
  } catch (err) {
    var known = err instanceof UserError_;
    if (!known) console.error(err && err.stack || err);
    return json_({ ok: false, error: known ? err.message : 'Palvelinvirhe', code: known ? err.code : 'server' });
  }
}

function UserError_(message, code) { this.message = message; this.code = code || 'bad_request'; }

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// ------------------------------------------------------------ paritus ja tunnistus

function pair_(req) {
  var code = String(req.code || '').trim().toUpperCase();
  if (!/^[A-Z0-9]{3}-?[A-Z0-9]{3}$/.test(code)) throw new UserError_('Virheellinen parituskoodi', 'pair');
  if (code.indexOf('-') < 0) code = code.substring(0, 3) + '-' + code.substring(3);
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var props = PropertiesService.getScriptProperties();
    var raw = props.getProperty('pair:' + code);
    if (!raw) throw new UserError_('Parituskoodi ei kelpaa tai se on jo käytetty', 'pair');
    props.deleteProperty('pair:' + code);
    var p = JSON.parse(raw);
    if (Date.now() > p.exp) throw new UserError_('Parituskoodi on vanhentunut – pyydä uusi', 'pair');
    var key = (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, '');
    props.setProperty('op:' + p.call, JSON.stringify({ key: key, created: Date.now(), used: null }));
    audit_(p.call, 'paritettu', '');
    return { ok: true, call: p.call, key: key, v: VERSION };
  } finally {
    lock.releaseLock();
  }
}

function authenticate_(req) {
  var call = normCall_(req.call);
  if (!call) throw new UserError_('Tunnistus epäonnistui', 'auth');
  var raw = PropertiesService.getScriptProperties().getProperty('op:' + call);
  if (!raw) throw new UserError_('Ylläpitäjää ' + call + ' ei ole lisätty tai oikeus on poistettu', 'auth');
  var o = JSON.parse(raw);
  var ts = Number(req.ts);
  if (!isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > CONFIG.MAX_SKEW_S)
    throw new UserError_('Pyynnön aikaleima ei kelpaa – tarkista koneen kello', 'clock');
  var nonce = String(req.nonce || '');
  if (!/^[A-Za-z0-9]{16,64}$/.test(nonce)) throw new UserError_('Tunnistus epäonnistui', 'auth');
  var msg = [call, String(req.ts), nonce, String(req.action || ''), String(req.data || '')].join('\n');
  var expected = hex_(Utilities.computeHmacSha256Signature(msg, o.key, Utilities.Charset.UTF_8));
  if (!safeEqual_(expected, String(req.sig || '').toLowerCase())) throw new UserError_('Tunnistus epäonnistui', 'auth');
  var cache = CacheService.getScriptCache();
  if (cache.get('n:' + nonce)) throw new UserError_('Pyyntö on jo käsitelty', 'replay');
  cache.put('n:' + nonce, '1', 2 * CONFIG.MAX_SKEW_S + 60);
  var rk = 'r:' + call + ':' + Math.floor(Date.now() / 60000);
  var n = Number(cache.get(rk) || 0) + 1;
  cache.put(rk, String(n), 120);
  if (n > CONFIG.RATE_PER_MIN) throw new UserError_('Liikaa pyyntöjä – odota hetki', 'rate');
  if (!o.used || Date.now() - o.used > 3600000) {
    o.used = Date.now();
    PropertiesService.getScriptProperties().setProperty('op:' + call, JSON.stringify(o));
  }
  return call;
}

function hex_(bytes) {
  var s = '';
  for (var i = 0; i < bytes.length; i++) {
    var b = bytes[i] < 0 ? bytes[i] + 256 : bytes[i];
    s += (b < 16 ? '0' : '') + b.toString(16);
  }
  return s;
}

function safeEqual_(a, b) {
  if (a.length !== b.length) return false;
  var d = 0;
  for (var i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try { return fn(); } finally { lock.releaseLock(); }
}

// ------------------------------------------------------------ kierrokset ja rivit

function start_(op, d) {
  var round = cleanRound_(d.round);
  var date = clean_(d.date, 20);
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = findRoundSheet_(round);
  var created = false;
  if (!sh) {
    var tpl = ss.getSheets().filter(function (s) { return s.getName().indexOf(CONFIG.TEMPLATE_PREFIX) === 0; })[0];
    if (!tpl) throw new UserError_('Pohjavälilehteä "' + CONFIG.TEMPLATE_PREFIX + '…" ei löytynyt', 'config');
    sh = tpl.copyTo(ss);
    sh.setName(CONFIG.TAB_PREFIX + ' ' + round + ' ' + ddmmyy_(date));
    ss.setActiveSheet(sh);
    ss.moveActiveSheet(1);
    sh.getRange(CONFIG.CELL_DATE).setValue(safe_(date));
    sh.getRange(CONFIG.CELL_ROUND).setValue(/^\d+$/.test(round) ? Number(round) : safe_(round));
    created = true;
  }
  var operCell = sh.getRange(CONFIG.CELL_OPER);
  if (!String(operCell.getValue()).trim()) operCell.setValue(op);
  audit_(op, created ? 'kierros luotu' : 'kierros avattu', sh.getName() + ' TG ' + clean_(d.tg, 10));
  return { sheet: sh.getName(), created: created, rows: readRows_(sh) };
}

function logStation_(op, d) {
  var sh = roundSheet_(d.round);
  var call = normCall_(d.call);
  if (!call) throw new UserError_('Virheellinen kutsu');
  var rows = readRows_(sh);
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].call === call) return { duplicate: true, row: rows[i], rows: rows };
  }
  var nro = rows.reduce(function (m, r) { return Math.max(m, Number(r.nro) || 0); }, 0) + 1;
  var rowIdx = firstEmptyRow_(sh);
  var dev = clean_(d.dev, FIELD_MAX.dev);
  var row = { nro: nro, call: call, via: clean_(d.via, FIELD_MAX.via), kunta: clean_(d.kunta, FIELD_MAX.kunta),
              info: clean_(d.info, FIELD_MAX.info), oper: op, dev: dev };
  // Alasvetovalikot (esim. RPT/HOTSPOT -> Toistimet-data): käytä listan arvoa, jos sama toistin löytyy.
  // Muuten lisää BrandMeisterin tieto listalle (vain RPT/HOTSPOT); muut kentät jätetään tyhjiksi.
  var unmatched = {}, added = [];
  ['via', 'kunta', 'info'].forEach(function (f) {
    var list = allowedValues_(sh.getRange(rowIdx, CONFIG.COLS[f]));
    if (!list || !row[f]) return;
    var m = matchAllowed_(row[f], list.values, [row.via, dev]);
    if (m !== null) { row[f] = m; return; }
    if (f === 'via' && list.source) {
      appendToList_(list.source, safe_(row[f]));
      added.push(row[f]);
      audit_(op, 'toistin lisätty listalle', list.source.getSheet().getName() + ': ' + row[f]);
    } else {
      unmatched[f] = row[f];
      row[f] = '';
    }
  });
  try {
    setRow_(sh, rowIdx, row);
  } catch (err) {
    clearRow_(sh, rowIdx);                       // ei puolivalmiita rivejä
    throw new UserError_('Riviä ei voitu tallentaa: ' + (err && err.message || err));
  }
  audit_(op, 'kirjaus', sh.getName() + ' NRO ' + nro + ' ' + call +
         (Object.keys(unmatched).length ? ' (ei listalla: ' + JSON.stringify(unmatched) + ')' : ''));
  var fresh = readRows_(sh);
  return { duplicate: false, row: fresh.filter(function (r) { return r.call === call; })[0], rows: fresh,
           unmatched: unmatched, added: added };
}

function unlog_(op, d) {
  var sh = roundSheet_(d.round);
  var call = normCall_(d.call);
  var rows = readRows_(sh);
  var hit = rows.filter(function (r) { return r.call === call; })[0];
  if (!hit) return { rows: rows };
  sh.deleteRow(hit.row);
  var C = CONFIG.COLS;
  readRows_(sh).forEach(function (r) {
    if (Number(r.nro) > Number(hit.nro)) sh.getRange(r.row, C.nro).setValue(Number(r.nro) - 1);
  });
  audit_(op, 'kirjaus peruttu', sh.getName() + ' NRO ' + hit.nro + ' ' + call);
  return { rows: readRows_(sh) };
}

function edit_(op, d) {
  var sh = roundSheet_(d.round);
  var call = normCall_(d.call);
  var field = String(d.field || '');
  if (EDITABLE.indexOf(field) < 0) throw new UserError_('Kenttää ei voi muokata');
  var hit = readRows_(sh).filter(function (r) { return r.call === call; })[0];
  if (!hit) throw new UserError_('Asemaa ' + call + ' ei ole kirjattu');
  var value = clean_(d.value, FIELD_MAX[field]);
  var cell = sh.getRange(hit.row, CONFIG.COLS[field]);
  var allowed = allowedValues_(cell);
  if (allowed && value) {
    var m = matchAllowed_(value, allowed.values, [value]);
    if (m === null) throw new UserError_('Arvoa "' + value + '" ei ole sarakkeen valintalistalla – valitse arvo taulukon alasvetovalikosta tai lisää se listalle');
    value = m;
  }
  try {
    cell.setValue(safe_(value));
  } catch (err) {
    throw new UserError_('Arvoa ei voitu tallentaa: ' + (err && err.message || err));
  }
  audit_(op, 'muokkaus', sh.getName() + ' ' + call + ' ' + field + '=' + value);
  return { rows: readRows_(sh) };
}

function list_(d) {
  var sh = findRoundSheet_(cleanRound_(d.round));
  return { sheet: sh ? sh.getName() : null, rows: sh ? readRows_(sh) : [] };
}

function roundSheet_(round) {
  var sh = findRoundSheet_(cleanRound_(round));
  if (!sh) throw new UserError_('Kierroksen välilehteä ei löytynyt – aloita kierros ohjelmassa');
  return sh;
}

function findRoundSheet_(round) {
  var prefix = CONFIG.TAB_PREFIX + ' ' + round;
  var sheets = SpreadsheetApp.getActiveSpreadsheet().getSheets();
  for (var i = 0; i < sheets.length; i++) {
    var n = sheets[i].getName();
    if (n === prefix || n.indexOf(prefix + ' ') === 0) return sheets[i];
  }
  return null;
}

function readRows_(sh) {
  var C = CONFIG.COLS;
  var last = sh.getLastRow();
  if (last < CONFIG.FIRST_ROW) return [];
  var width = Math.max(C.nro, C.call, C.via, C.kunta, C.info, C.oper, C.dev);
  var values = sh.getRange(CONFIG.FIRST_ROW, 1, last - CONFIG.FIRST_ROW + 1, width).getValues();
  var out = [];
  for (var i = 0; i < values.length; i++) {
    var v = values[i];
    var call = String(v[C.call - 1] || '').trim().toUpperCase();
    if (!call) continue;
    out.push({ row: CONFIG.FIRST_ROW + i, nro: Number(v[C.nro - 1]) || null, call: call,
               via: str_(v[C.via - 1]), kunta: str_(v[C.kunta - 1]), info: str_(v[C.info - 1]),
               oper: str_(v[C.oper - 1]), dev: str_(v[C.dev - 1]) });
  }
  return out;
}

function firstEmptyRow_(sh) {
  var C = CONFIG.COLS;
  var last = Math.max(sh.getLastRow(), CONFIG.FIRST_ROW);
  var vals = sh.getRange(CONFIG.FIRST_ROW, C.call, last - CONFIG.FIRST_ROW + 1, 1).getValues();
  for (var i = 0; i < vals.length; i++) if (!String(vals[i][0]).trim()) return CONFIG.FIRST_ROW + i;
  return last + 1;
}

function setRow_(sh, row, r) {
  var C = CONFIG.COLS;
  Object.keys(C).forEach(function (k) {
    if (r[k] === undefined || r[k] === null) return;
    sh.getRange(row, C[k]).setValue(k === 'nro' ? r[k] : safe_(r[k]));
  });
}

function clearRow_(sh, row) {
  var C = CONFIG.COLS;
  Object.keys(C).forEach(function (k) {
    try { sh.getRange(row, C[k]).setValue(''); } catch (e) { /* tyhjä kelpaa aina */ }
  });
}

// Solun valintalistan sallitut arvot (alasvetovalikko), tai null jos rajoitusta ei ole.
function allowedValues_(range) {
  var rule = range.getDataValidation && range.getDataValidation();
  if (!rule || (rule.getAllowInvalid && rule.getAllowInvalid())) return null;
  var T = SpreadsheetApp.DataValidationCriteria;
  var type = rule.getCriteriaType();
  var args = rule.getCriteriaValues();
  var vals = [], source = null;
  if (type === T.VALUE_IN_LIST) vals = args[0] || [];
  else if (type === T.VALUE_IN_RANGE) {
    source = args[0];
    source.getValues().forEach(function (r) { r.forEach(function (v) { vals.push(v); }); });
  } else return null;
  vals = vals.map(function (v) { return String(v).trim(); }).filter(function (v) { return v; });
  return { values: vals, source: source };
}

// Lisää arvon valintalistan alueen ensimmäiseen tyhjään soluun (esim. Toistimet-data).
function appendToList_(range, value) {
  var sh = range.getSheet(), col = range.getColumn(), first = range.getRow();
  var n = range.getLastRow() - first + 1;
  var vals = sh.getRange(first, col, n, 1).getValues();
  for (var i = 0; i < vals.length; i++) {
    if (!String(vals[i][0]).trim()) { sh.getRange(first + i, col).setValue(value); return; }
  }
  throw new UserError_('Valintalistan alue (' + sh.getName() + ') on täynnä – laajenna alasvetovalikon aluetta taulukossa');
}

// Etsii listalta arvon, joka vastaa annettua: täsmälleen (isoista/pienistä ja ääkkösistä välittämättä)
// tai saman toistimen kutsun perusteella. Paikkakunnan perusteella ei arvata. Palauttaa listan arvon tai null.
function matchAllowed_(value, allowed, hints) {
  var norm = function (x) {
    return String(x || '').toUpperCase().replace(/[ÄÅ]/g, 'A').replace(/Ö/g, 'O').replace(/[^A-Z0-9]+/g, ' ').trim();
  };
  var v = norm(value);
  if (!v) return '';
  for (var i = 0; i < allowed.length; i++) if (norm(allowed[i]) === v) return allowed[i];
  var words = [];
  (hints || []).concat([value]).forEach(function (h) { norm(h).split(' ').forEach(function (w) { if (w) words.push(w); }); });
  var isCall = function (w) { return /^[A-Z]{1,2}[0-9][A-Z]{1,4}$/.test(w); };
  var calls = words.filter(isCall);
  var hits = allowed.filter(function (a) {
    var toks = ' ' + norm(a) + ' ';
    return calls.some(function (w) { return toks.indexOf(' ' + w + ' ') >= 0; });
  });
  return hits.length === 1 ? hits[0] : null;          // sama toistimen kutsu yhdellä rivillä
}

// ------------------------------------------------------------ apufunktiot

function normCall_(c) {
  c = String(c || '').trim().toUpperCase();
  return CALL_RE.test(c) && c.length <= FIELD_MAX.call ? c : '';
}

function cleanRound_(r) {
  r = String(r || '').trim();
  if (!/^[0-9A-Za-z_-]{1,12}$/.test(r)) throw new UserError_('Virheellinen kierrosnumero');
  return r;
}

function clean_(v, max) {
  return String(v === undefined || v === null ? '' : v).replace(/[\u0000-\u001f\u007f]/g, ' ').trim().substring(0, max || 100);
}

// Kaavojen esto: =, +, -, @ alussa -> teksti
function safe_(v) {
  var s = String(v);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function str_(v) { return v === null || v === undefined ? '' : String(v); }

function ddmmyy_(date) {
  var m = /^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$/.exec(date);
  if (!m) return Utilities.formatDate(new Date(), 'Europe/Helsinki', 'ddMMyy');
  return ('0' + m[1]).slice(-2) + ('0' + m[2]).slice(-2) + m[3].slice(-2);
}

function fmtTime_(t) { return t ? Utilities.formatDate(new Date(t), 'Europe/Helsinki', 'd.M.yyyy HH:mm') : ''; }

function audit_(op, action, details) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName(CONFIG.AUDIT_SHEET);
    if (!sh) {
      sh = ss.insertSheet(CONFIG.AUDIT_SHEET);
      sh.appendRow(['Aika', 'Ylläpitäjä', 'Tapahtuma', 'Tiedot']);
      sh.hideSheet();
    }
    sh.appendRow([new Date(), safe_(op), safe_(action), safe_(details)]);
  } catch (e) {
    console.error('audit: ' + e);
  }
}
