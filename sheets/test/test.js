// Code.gs:n testit simuloitua taulukkoa vastaan:  node sheets/test/test.js
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const { load } = require('./gas-mock');

const g = load();
const post = body => JSON.parse(g.doPost({postData: {contents: JSON.stringify(body)}}).getContent());

function signed(call, key, action, data, opts = {}) {
  const ts = opts.ts ?? Math.floor(Date.now() / 1000);
  const nonce = opts.nonce ?? crypto.randomBytes(16).toString('hex');
  const d = JSON.stringify(data || {});
  const sig = crypto.createHmac('sha256', key).update([call, ts, nonce, action, d].join('\n')).digest('hex');
  return {call, ts, nonce, action, data: d, sig: opts.sig ?? sig};
}

let passed = 0;
function test(name, fn) { fn(); passed++; console.log('ok  ' + name); }

// --- paritus
const code = g.createPairingCode_('OH2ABC');
test('paritus antaa avaimen ja koodi kelpaa vain kerran', () => {
  const r = post({op: 'pair', code: code.replace('-', '').toLowerCase()});
  assert.strictEqual(r.ok, true); assert.strictEqual(r.call, 'OH2ABC'); assert.match(r.key, /^[0-9a-f]{64}$/);
  g.__key = r.key;
  const again = post({op: 'pair', code});
  assert.strictEqual(again.ok, false); assert.strictEqual(again.code, 'pair');
});
test('vanhentunut parituskoodi hylätään', () => {
  const c = g.createPairingCode_('OH6HL');
  const p = JSON.parse(g.__props.get('pair:' + c)); p.exp = Date.now() - 1; g.__props.set('pair:' + c, JSON.stringify(p));
  assert.strictEqual(post({op: 'pair', code: c}).ok, false);
});
const key = g.__key;

// --- tunnistus
test('oikein allekirjoitettu pyyntö kelpaa', () => {
  const r = post(signed('OH2ABC', key, 'hello', {}));
  assert.strictEqual(r.ok, true); assert.strictEqual(r.call, 'OH2ABC');
});
test('väärä allekirjoitus, väärä avain ja tuntematon ylläpitäjä hylätään', () => {
  assert.strictEqual(post(signed('OH2ABC', key, 'hello', {}, {sig: '0'.repeat(64)})).code, 'auth');
  assert.strictEqual(post(signed('OH2ABC', 'f'.repeat(64), 'hello', {})).code, 'auth');
  assert.strictEqual(post(signed('OH9XXX', key, 'hello', {})).code, 'auth');
});
test('toistettu pyyntö hylätään', () => {
  const req = signed('OH2ABC', key, 'hello', {});
  assert.strictEqual(post(req).ok, true);
  assert.strictEqual(post(req).code, 'replay');
});
test('vanha aikaleima hylätään', () => {
  assert.strictEqual(post(signed('OH2ABC', key, 'hello', {}, {ts: Math.floor(Date.now() / 1000) - 3600})).code, 'clock');
});
test('muokattu sisältö (allekirjoitus ei täsmää) hylätään', () => {
  const req = signed('OH2ABC', key, 'log', {round: '177', call: 'OH1AAA'});
  req.data = JSON.stringify({round: '177', call: 'OH1BBB'});
  assert.strictEqual(post(req).code, 'auth');
});

// --- kierros ja kirjaukset
test('kierroksen aloitus luo välilehden pohjasta ensimmäiseksi', () => {
  const r = post(signed('OH2ABC', key, 'start', {round: '177', date: '22.11.2026', tg: 244}));
  assert.strictEqual(r.ok, true); assert.strictEqual(r.created, true); assert.strictEqual(r.sheet, 'Kierros 177 221126');
  const sh = g.__ss.sheets[0];
  assert.strictEqual(sh.name, 'Kierros 177 221126');
  assert.strictEqual(sh.get(1, 4), '22.11.2026'); assert.strictEqual(sh.get(1, 6), 177); assert.strictEqual(sh.get(2, 7), 'OH2ABC');
  const again = post(signed('OH2ABC', key, 'start', {round: '177', date: '22.11.2026', tg: 244}));
  assert.strictEqual(again.created, false);
});
test('kirjaus antaa juoksevan numeron ja Oper tulee avaimesta', () => {
  const a = post(signed('OH2ABC', key, 'log', {round: '177', call: 'oh2kxa', via: 'OH2RUP Jarvenpaa', kunta: 'Jarvenpaa', dev: 'OH2RUP, Jarvenpaa', oper: 'HUIJARI'}));
  assert.strictEqual(a.row.nro, 1); assert.strictEqual(a.row.oper, 'OH2ABC'); assert.strictEqual(a.row.call, 'OH2KXA');
  const b = post(signed('OH2ABC', key, 'log', {round: '177', call: 'ES1BBB', via: 'Ulkomaat'}));
  assert.strictEqual(b.row.nro, 2);
  const sh = g.__ss.sheets[0];
  // RPT/HOTSPOT sovitetaan Toistimet-data-listaan toistimen kutsun perusteella
  assert.deepStrictEqual([sh.get(3, 1), sh.get(3, 2), sh.get(3, 3), sh.get(3, 6), sh.get(3, 7), sh.get(3, 8)], [1, 'OH2KXA', 'Järvenpää OH2RUP', 'OH2ABC', '', '']);
  assert.strictEqual(sh.get(4, 3), 'Ulkomaat');
});
test('ääkköset allekirjoituksessa ja taulukossa', () => {
  const r = post(signed('OH2ABC', key, 'edit', {round: '177', call: 'OH2KXA', field: 'kunta', value: 'Järvenpää'}));
  assert.strictEqual(r.ok, true); assert.strictEqual(g.__ss.sheets[0].get(3, 4), 'Järvenpää');
});
test('uusi toistin lisätään Toistimet-dataan BrandMeisterin muodossa', () => {
  const list = () => g.__ss.getSheetByName('Toistimet-data').cells.map(r => r[0]).filter(Boolean);
  const r = post(signed('OH2ABC', key, 'log', {round: '177', call: 'OH8XYZ', via: 'OH8RMD Kempele', kunta: 'Kempele', dev: 'OH8RMD, Kempele'}));
  assert.strictEqual(r.ok, true); assert.strictEqual(r.row.via, 'OH8RMD Kempele'); assert.deepStrictEqual(r.added, ['OH8RMD Kempele']);
  assert.ok(list().includes('OH8RMD Kempele'));
  // sama toistin uudelleen: ei toista riviä listalle
  const r2 = post(signed('OH2ABC', key, 'log', {round: '177', call: 'OH8XYY', via: 'OH8RMD Kempele', dev: 'OH8RMD, Kempele'}));
  assert.deepStrictEqual(r2.added, []); assert.strictEqual(list().filter(x => x === 'OH8RMD Kempele').length, 1);
  // pelkkä sama kaupunki ei riitä: uusi Helsingin toistin lisätään omana rivinään
  const r3 = post(signed('OH2ABC', key, 'log', {round: '177', call: 'OH2AMB', via: 'OH2RZZ Helsinki', dev: 'OH2RZZ, Helsinki'}));
  assert.strictEqual(r3.row.via, 'OH2RZZ Helsinki');
  // hotspot löytyy listalta isoista/pienistä kirjaimista välittämättä
  assert.strictEqual(post(signed('OH2ABC', key, 'log', {round: '177', call: 'OH3HSP', via: 'hotspot', dev: 'OH3HSP'})).row.via, 'Hotspot');
  // käsin muokkaus: listalla oleva arvo kelpaa, listalta puuttuva antaa selvän virheen eikä lisää listalle
  assert.strictEqual(post(signed('OH2ABC', key, 'edit', {round: '177', call: 'OH8XYZ', field: 'via', value: 'oulu linnanmaa 70cm'})).ok, true);
  assert.strictEqual(g.__ss.sheets[0].cells.find(x => x[1] === 'OH8XYZ')[2], 'Oulu Linnanmaa 70cm');
  const bad = post(signed('OH2ABC', key, 'edit', {round: '177', call: 'OH8XYZ', field: 'via', value: 'Kempelee'}));
  assert.strictEqual(bad.ok, false); assert.match(bad.error, /valintalistalla/); assert.ok(!list().includes('Kempelee'));
  // täysi lista (alue rivit 1–12): alue laajenee itsestään ja sääntö päivittyy kierrokselle ja pohjalle
  for (let k = 0; k < 3; k++) post(signed('OH2ABC', key, 'log', {round: '177', call: 'OH9F' + 'ABC'[k], via: 'OH9R' + 'ABC'[k] + 'Z Kemi', dev: 'x'}));
  const full = post(signed('OH2ABC', key, 'log', {round: '177', call: 'OH9FZZ', via: 'OH9RZZ Tornio', dev: 'x'}));
  assert.strictEqual(full.ok, true); assert.strictEqual(full.row.via, 'OH9RZZ Tornio');
  assert.ok(list().includes('OH9RZZ Tornio') && list().includes('OH9RCZ Kemi'));
  assert.ok(g.__ss.sheets[0].validations[3].to > 12);
  assert.ok(g.__ss.getSheetByName('LOGIPOHJA v0.68').validations[3].to > 12);
  assert.ok(g.__ss.getSheetByName('Check-in-tapahtumat').cells.some(x => x[2] === 'valintalista laajennettu'));
  assert.ok(g.__ss.getSheetByName('Check-in-tapahtumat').cells.some(x => x[2] === 'toistin lisätty listalle'));
  for (const c of ['OH8XYZ', 'OH8XYY', 'OH2AMB', 'OH3HSP', 'OH9FA', 'OH9FB', 'OH9FC', 'OH9FZZ']) post(signed('OH2ABC', key, 'unlog', {round: '177', call: c}));
});
test('saman kutsun kirjaus kahdesti ei tee uutta riviä', () => {
  const r = post(signed('OH2ABC', key, 'log', {round: '177', call: 'OH2KXA'}));
  assert.strictEqual(r.duplicate, true); assert.strictEqual(r.rows.length, 2);
});
test('toinen ylläpitäjä kirjaa samaan välilehteen omalla nimellään', () => {
  const k2 = post({op: 'pair', code: g.createPairingCode_('OH6HL')}).key;
  const r = post(signed('OH6HL', k2, 'log', {round: '177', call: 'OH3MTL', via: 'Hotspot'}));
  assert.strictEqual(r.row.nro, 3); assert.strictEqual(r.row.oper, 'OH6HL');
  const l = post(signed('OH2ABC', key, 'list', {round: '177'}));
  assert.deepStrictEqual(l.rows.map(x => [x.nro, x.call, x.oper]), [[1, 'OH2KXA', 'OH2ABC'], [2, 'ES1BBB', 'OH2ABC'], [3, 'OH3MTL', 'OH6HL']]);
});
test('kaavat estetään (tallennetaan tekstinä)', () => {
  post(signed('OH2ABC', key, 'edit', {round: '177', call: 'OH2KXA', field: 'info', value: '=IMPORTXML("http://x","//a")'}));
  assert.strictEqual(g.__ss.sheets[0].get(3, 5), '\'=IMPORTXML("http://x","//a")');
  const bad = post(signed('OH2ABC', key, 'edit', {round: '177', call: 'OH2KXA', field: 'oper', value: 'X'}));
  assert.strictEqual(bad.ok, false);
});
test('peruminen poistaa rivin ja tiivistää numerot', () => {
  const r = post(signed('OH2ABC', key, 'unlog', {round: '177', call: 'OH2KXA'}));
  assert.deepStrictEqual(r.rows.map(x => [x.nro, x.call]), [[1, 'ES1BBB'], [2, 'OH3MTL']]);
});
test('virheelliset syötteet hylätään', () => {
  assert.strictEqual(post(signed('OH2ABC', key, 'log', {round: '177', call: '=1+1'})).ok, false);
  assert.strictEqual(post(signed('OH2ABC', key, 'log', {round: '../x', call: 'OH1AAA'})).ok, false);
  assert.strictEqual(post(signed('OH2ABC', key, 'log', {round: '999', call: 'OH1AAA'})).ok, false);
  assert.strictEqual(post(signed('OH2ABC', key, 'nuke', {})).ok, false);
});
test('tapahtumaloki on piilotetulla välilehdellä', () => {
  const a = g.__ss.sheets.find(s => s.name === 'Check-in-tapahtumat');
  assert.ok(a && a.hidden);
  assert.ok(a.cells.some(r => r[2] === 'kirjaus') && a.cells.some(r => r[2] === 'kirjaus peruttu'));
});
test('poistetun ylläpitäjän pyynnöt hylätään', () => {
  g.__props.delete('op:OH6HL');
  const r = post(signed('OH6HL', 'x'.repeat(64), 'hello', {}));
  assert.strictEqual(r.code, 'auth');
});
test('pyyntöjen määrää rajoitetaan', () => {
  let last;
  for (let i = 0; i < 130; i++) last = post(signed('OH2ABC', key, 'hello', {}));
  assert.strictEqual(last.code, 'rate');
});
console.log(`\n${passed} testiä läpi`);
