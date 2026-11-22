// Kevyt simulaatio Google Apps Scriptin palveluista Code.gs:n testaamiseen ilman Googlea.
// Käyttö:  const gas = require('./gas-mock'); const g = gas.load();  g.doPost({postData:{contents: '...'}})
// Palvelin: node gas-mock.js 8840  (POST / -> 302 -> GET /result/<id>, kuten oikea verkkosovellus)
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');
const http = require('http');

function colToNum(s) { let n = 0; for (const c of s) n = n * 26 + (c.charCodeAt(0) - 64); return n; }

class Sheet {
  constructor(ss, name, rows) { this.ss = ss; this.name = name; this.cells = rows ? rows.map(r => r.slice()) : []; this.hidden = false; this.validations = {}; }
  // valintalista sarakkeelle riviltä 3 alkaen: {col: () => sallitut arvot}
  rule(r, c) {
    const v = r >= 3 && this.validations[c];
    if (!v) return null;
    return makeRule(this.ss, v);
  }
  getMaxRows() { return Math.max(this.maxRows || 1000, this.cells.length); }
  insertRowsAfter(after, n) { this.maxRows = this.getMaxRows() + n; }
  getName() { return this.name; }
  setName(n) { if (this.ss.sheets.some(s => s !== this && s.name === n)) throw new Error('name exists'); this.name = n; return this; }
  getLastRow() { for (let i = this.cells.length - 1; i >= 0; i--) if ((this.cells[i] || []).some(v => v !== '' && v != null)) return i + 1; return 0; }
  get(r, c) { return ((this.cells[r - 1] || [])[c - 1]) ?? ''; }
  set(r, c, v) { while (this.cells.length < r) this.cells.push([]); const row = this.cells[r - 1]; while (row.length < c) row.push(''); row[c - 1] = v; }
  getRange(a, b, nr, nc) {
    let r, c;
    if (typeof a === 'string') { const m = /^([A-Z]+)(\d+)$/.exec(a); c = colToNum(m[1]); r = Number(m[2]); nr = 1; nc = 1; }
    else { r = a; c = b; nr = nr || 1; nc = nc || 1; }
    const sh = this;
    return {
      getValue: () => sh.get(r, c),
      setValue: v => {
        const rule = sh.rule(r, c);
        if (rule && v !== '' && !rule.getCriteriaValues()[0].getValues().some(x => String(x[0]) === String(v)))
          throw new Error(`Syöttämäsi tiedot solussa ${String.fromCharCode(64 + c)}${r} rikkovat tietojen vahvistussääntöjä`);
        sh.set(r, c, v);
      },
      getDataValidation: () => sh.rule(r, c),
      getValues: () => Array.from({length: nr}, (_, i) => Array.from({length: nc}, (_, j) => sh.get(r + i, c + j))),
      getSheet: () => sh, getColumn: () => c, getRow: () => r, getLastRow: () => r + nr - 1,
      setDataValidation: rule => { sh.validations[c] = {...rule._v}; },
    };
  }
  appendRow(vals) { const r = this.getLastRow() + 1; vals.forEach((v, i) => this.set(r, i + 1, v)); }
  deleteRow(r) { this.cells.splice(r - 1, 1); }
  copyTo(ss) { const s = new Sheet(ss, 'Kopio: ' + this.name, this.cells); s.validations = {...this.validations}; ss.sheets.push(s); return s; }
  hideSheet() { this.hidden = true; }
}

function makeRule(ss, v) {
  return {
    _v: v,
    getAllowInvalid: () => false,
    getCriteriaType: () => 'VALUE_IN_RANGE',
    getCriteriaValues: () => [ss.getSheetByName(v.sheet).getRange(v.from, v.col, v.to - v.from + 1, 1), true],
    copy() {
      const b = { _v: {...v},
        requireValueInRange(range, dd) { b._v = {sheet: range.getSheet().getName(), col: range.getColumn(), from: range.getRow(), to: range.getLastRow()}; return b; },
        build() { return makeRule(ss, b._v); } };
      return b;
    },
  };
}

class Spreadsheet {
  constructor() { this.sheets = []; this.active = null; }
  getSheets() { return this.sheets.slice(); }
  getSheetByName(n) { return this.sheets.find(s => s.name === n) || null; }
  insertSheet(n) { const s = new Sheet(this, n); this.sheets.push(s); return s; }
  setActiveSheet(s) { this.active = s; return s; }
  moveActiveSheet(pos) { const i = this.sheets.indexOf(this.active); this.sheets.splice(i, 1); this.sheets.splice(pos - 1, 0, this.active); }
}

function defaultSpreadsheet() {
  const ss = new Spreadsheet();
  ss.sheets.push(new Sheet(ss, 'Kierros 176 151126', [
    ['FinDMR kierros loki v0.68', '', 'PVM:', '15.11.2026', 'KIERROSNUMERO', 176],
    ['NRO', '', 'RPT/HOTSPOT', 'KUNTA', 'Lisätietoja', 'Oper:', 'OH6HL'],
    [1, 'WD2DW', 'Ulkomaat', 'SOUTH CAROLINA US', '', '', 'Nykyinen ennätys 150 osallistujaa kierroksella 175'],
  ]));
  ss.sheets.push(new Sheet(ss, 'LOGIPOHJA v0.68', [
    ['FinDMR kierros loki v0.68', '', 'PVM:', '', 'KIERROSNUMERO', ''],
    ['NRO', '', 'RPT/HOTSPOT', 'KUNTA', 'Lisätietoja', 'Oper:', ''],
  ]));
  ss.sheets.push(new Sheet(ss, 'Toistimet-data', [['Järvenpää OH2RUP'], ['Mikkeli OH4RUC'], ['Oulu Linnanmaa 70cm'],
    ['Helsinki Pasila 70cm'], ['Helsinki Malmi 2m'], ['Hotspot'], ['Ulkomaat'], ['POC']]));
  for (const sh of ss.sheets.slice(0, 2)) sh.validations[3] = { sheet: 'Toistimet-data', col: 1, from: 1, to: 12 };
  return ss;
}

function load(ss) {
  ss = ss || defaultSpreadsheet();
  const props = new Map(), cache = new Map();
  const ctx = {
    console,
    SpreadsheetApp: { getActiveSpreadsheet: () => ss, getUi: () => { throw new Error('no ui in tests'); },
                      DataValidationCriteria: { VALUE_IN_LIST: 'VALUE_IN_LIST', VALUE_IN_RANGE: 'VALUE_IN_RANGE' } },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: k => (props.has(k) ? props.get(k) : null),
      setProperty: (k, v) => { props.set(k, String(v)); },
      deleteProperty: k => { props.delete(k); },
      getProperties: () => Object.fromEntries(props),
    }) },
    CacheService: { getScriptCache: () => ({
      get: k => { const e = cache.get(k); return e && e.exp > Date.now() ? e.v : null; },
      put: (k, v, s) => { cache.set(k, {v: String(v), exp: Date.now() + (s || 600) * 1000}); },
    }) },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    Utilities: {
      Charset: { UTF_8: 'utf8' },
      getUuid: () => crypto.randomUUID(),
      computeHmacSha256Signature: (msg, key) => [...crypto.createHmac('sha256', key).update(msg, 'utf8').digest()].map(b => (b > 127 ? b - 256 : b)),
      formatDate: (d, tz, fmt) => {
        const p = n => String(n).padStart(2, '0');
        return fmt.replace('dd', p(d.getDate())).replace('MM', p(d.getMonth() + 1)).replace('yy', String(d.getFullYear()).slice(-2));
      },
    },
    ContentService: { MimeType: { JSON: 'json' }, createTextOutput: s => ({ s, setMimeType() { return this; }, getContent() { return this.s; } }) },
    Date, Math, JSON, Number, String, Object, Array, isFinite,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8'), ctx, {filename: 'Code.gs'});
  ctx.__ss = ss; ctx.__props = props;
  return ctx;
}

module.exports = { load, defaultSpreadsheet };

if (require.main === module) {
  const port = Number(process.argv[2] || 8840);
  const g = load();
  const results = new Map();
  let n = 0;
  http.createServer((req, res) => {
    if (req.method === 'POST') {
      let body = '';
      req.on('data', c => { body += c; });
      req.on('end', () => {
        const out = g.doPost({postData: {contents: body}}).getContent();
        const id = String(++n);
        results.set(id, out);
        res.writeHead(302, {Location: `http://127.0.0.1:${port}/result/${id}`}); res.end();
      });
    } else if (req.url.startsWith('/result/')) {
      res.writeHead(200, {'Content-Type': 'application/json'}); res.end(results.get(req.url.split('/')[2]) || '{}');
    } else if (req.url.startsWith('/pair/')) {        // testiapu: luo parituskoodi (oikeassa taulukossa valikosta)
      const code = g.createPairingCode_(decodeURIComponent(req.url.split('/')[2]));
      res.writeHead(200, {'Content-Type': 'text/plain'}); res.end(code);
    } else if (req.url === '/sheet') {                // testiapu: taulukon sisältö
      res.writeHead(200, {'Content-Type': 'application/json'});
      res.end(JSON.stringify(g.__ss.sheets.map(s => ({name: s.name, hidden: s.hidden, cells: s.cells}))));
    } else {
      res.writeHead(200, {'Content-Type': 'application/json'}); res.end(g.doGet().getContent());
    }
  }).listen(port, '127.0.0.1', () => console.log('gas-mock listening on ' + port));
}
