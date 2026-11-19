/* BM Monitor – Hoseline-soitin.
 *
 * Käyttää samaa julkista rajapintaa kuin Brandmeisterin Hoseline-verkkosivu:
 *   wss://hose.brandmeister.network/spotter/?token=test:test  (alaprotokolla "spotter")
 * Viestit ovat MessagePack-taulukoita:
 *   lähetettävät:  [1, [tg, ...]] liity · [2, [tg, ...]] poistu · [3] nollaa
 *   saapuvat:      [11, ?, lähettäjäId, tg, ?] puhelu alkaa · [13] puhelu loppuu
 *                  [20, <960 tavua G.711 μ-law, 8 kHz>] ääni · [21, "alias"] · [22, dB] taso
 */
(function () {
  "use strict";
  const HOSE_URL = "wss://hose.brandmeister.network/spotter/?token=test:test";
  const T = { JOIN: 1, LEAVE: 2, RESET: 3, CALL_START: 11, CALL_DROP: 12, CALL_END: 13, AUDIO: 20, ALIAS: 21, METER: 22 };
  const JITTER = 0.30;       // s puskuri ennen toistoa

  // ---------------- MessagePack (riittävä osajoukko) ----------------
  function mpEncode(v) {
    const out = [];
    const enc = x => {
      if (Array.isArray(x)) {
        if (x.length < 16) out.push(0x90 | x.length); else out.push(0xdc, x.length >> 8, x.length & 255);
        x.forEach(enc);
      } else if (Number.isInteger(x) && x >= 0) {
        if (x < 128) out.push(x);
        else if (x < 256) out.push(0xcc, x);
        else if (x < 65536) out.push(0xcd, x >> 8, x & 255);
        else out.push(0xce, (x >>> 24) & 255, (x >>> 16) & 255, (x >>> 8) & 255, x & 255);
      } else throw new Error("mpEncode: tukematon arvo " + x);
    };
    enc(v);
    return new Uint8Array(out);
  }

  function mpDecode(buf) {
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    let p = 0;
    const str = n => { const s = new TextDecoder().decode(buf.subarray(p, p + n)); p += n; return s; };
    const bin = n => { const b = buf.subarray(p, p + n); p += n; return b; };
    const arr = n => { const a = new Array(n); for (let i = 0; i < n; i++) a[i] = read(); return a; };
    const map = n => { const m = {}; for (let i = 0; i < n; i++) { const k = read(); m[k] = read(); } return m; };
    function read() {
      const t = buf[p++];
      if (t < 0x80) return t;
      if (t >= 0xe0) return t - 256;
      if ((t & 0xf0) === 0x90) return arr(t & 15);
      if ((t & 0xf0) === 0x80) return map(t & 15);
      if ((t & 0xe0) === 0xa0) return str(t & 31);
      let v;
      switch (t) {
        case 0xc0: return null;
        case 0xc2: return false;
        case 0xc3: return true;
        case 0xc4: v = buf[p]; p += 1; return bin(v);
        case 0xc5: v = dv.getUint16(p); p += 2; return bin(v);
        case 0xc6: v = dv.getUint32(p); p += 4; return bin(v);
        case 0xca: v = dv.getFloat32(p); p += 4; return v;
        case 0xcb: v = dv.getFloat64(p); p += 8; return v;
        case 0xcc: return buf[p++];
        case 0xcd: v = dv.getUint16(p); p += 2; return v;
        case 0xce: v = dv.getUint32(p); p += 4; return v;
        case 0xcf: v = Number(dv.getBigUint64(p)); p += 8; return v;
        case 0xd0: v = dv.getInt8(p); p += 1; return v;
        case 0xd1: v = dv.getInt16(p); p += 2; return v;
        case 0xd2: v = dv.getInt32(p); p += 4; return v;
        case 0xd3: v = Number(dv.getBigInt64(p)); p += 8; return v;
        case 0xd9: v = buf[p]; p += 1; return str(v);
        case 0xda: v = dv.getUint16(p); p += 2; return str(v);
        case 0xdb: v = dv.getUint32(p); p += 4; return str(v);
        case 0xdc: v = dv.getUint16(p); p += 2; return arr(v);
        case 0xdd: v = dv.getUint32(p); p += 4; return arr(v);
        case 0xde: v = dv.getUint16(p); p += 2; return map(v);
        default: throw new Error("mpDecode: tuntematon tyyppi 0x" + t.toString(16));
      }
    }
    return read();
  }

  // ---------------- G.711 μ-law ----------------
  const ULAW = new Float32Array(256);
  (() => {
    const C = [0, 132, 396, 924, 1980, 4092, 8316, 16764];
    for (let i = 0; i < 256; i++) {
      const b = ~i & 255, e = (b >> 4) & 7, m = b & 15;
      let v = C[e] + (m << (e + 3));
      if (b & 128) v = -v;
      ULAW[i] = v / 32768;
    }
  })();

  // ---------------- Soitin ----------------
  class HosePlayer {
    constructor() {
      this.groups = new Set();
      this.ws = null;
      this.ctx = null;
      this.gain = null;
      this.nextTime = 0;
      this.volume = Number(localStorage.getItem("bmVolume") || 1.5);
      this.call = null;          // {src, tg, alias, start}
      this.level = -120;
      this.state = "off";        // off | connecting | on | error
      this.error = "";
      this.listeners = new Set();
      this.reconnectTimer = null;
    }
    on(fn) { this.listeners.add(fn); }
    emit() { this.listeners.forEach(fn => { try { fn(this); } catch (e) { console.error(e); } }); }

    ensureAudio() {
      if (!this.ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        try { this.ctx = new AC({ sampleRate: 8000 }); } catch (e) { this.ctx = new AC(); }
        this.gain = this.ctx.createGain();
        this.gain.gain.value = this.volume;
        this.gain.connect(this.ctx.destination);
      }
      if (this.ctx.state === "suspended") this.ctx.resume();
    }

    setVolume(v) {
      this.volume = Number(v);
      try { localStorage.setItem("bmVolume", String(v)); } catch (e) { /* ei tallennusta */ }
      if (this.gain) this.gain.gain.value = this.volume;
    }

    isOn(tg) { return this.groups.has(Number(tg)); }

    toggle(tg) {
      tg = Number(tg);
      if (!(tg > 90)) return;      // Hoseline ei tue TG:itä 1–90
      this.ensureAudio();          // käyttäjän painallus sallii äänen
      if (this.groups.has(tg)) {
        this.groups.delete(tg);
        this.send([T.LEAVE, [tg]]);
        if (!this.groups.size) this.stop();
      } else {
        this.groups.add(tg);
        if (this.ws && this.ws.readyState === 1) this.send([T.JOIN, [tg]]);
        else this.connect();
      }
      this.emit();
    }

    stop() {
      this.groups.clear();
      clearTimeout(this.reconnectTimer);
      if (this.ws) { const w = this.ws; this.ws = null; try { w.close(); } catch (e) { /* */ } }
      this.call = null; this.state = "off"; this.error = "";
      this.emit();
    }

    send(msg) {
      if (this.ws && this.ws.readyState === 1) this.ws.send(mpEncode(msg));
    }

    connect() {
      if (this.ws) return;
      this.state = "connecting"; this.error = ""; this.emit();
      let ws;
      try { ws = new WebSocket(HOSE_URL, "spotter"); } catch (e) { this.fail(String(e)); return; }
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      ws.onopen = () => {
        if (this.ws !== ws) return;
        this.state = "on";
        if (this.groups.size) this.send([T.JOIN, [...this.groups]]);
        this.emit();
      };
      ws.onmessage = ev => {
        if (this.ws !== ws || typeof ev.data === "string") return;
        let m;
        try { m = mpDecode(new Uint8Array(ev.data)); } catch (e) { return; }
        if (Array.isArray(m)) this.handle(m);
      };
      ws.onclose = () => {
        if (this.ws !== ws) return;
        this.ws = null; this.call = null;
        if (this.groups.size) {
          this.state = "connecting"; this.error = "yhteys katkesi, yhdistetään uudelleen…";
          this.reconnectTimer = setTimeout(() => this.connect(), 4000);
        } else this.state = "off";
        this.emit();
      };
      ws.onerror = () => { if (this.ws === ws) this.error = "yhteysvirhe"; };
    }

    fail(msg) { this.state = "error"; this.error = msg; this.ws = null; this.emit(); }

    handle(m) {
      switch (m[0]) {
        case T.CALL_START:
          this.call = { src: m[2], tg: m[3], alias: "", start: Date.now() / 1000 };
          this.nextTime = 0;
          this.emit();
          break;
        case T.CALL_END:
          this.call = null; this.level = -120;
          this.emit();
          break;
        case T.ALIAS:
          if (this.call) { this.call.alias = String(m[1] || "").trim(); this.emit(); }
          break;
        case T.METER:
          this.level = Number(m[1]);
          if (this.onLevel) this.onLevel(this.level);
          break;
        case T.AUDIO:
          this.play(m[1]);
          break;
      }
    }

    play(bytes) {
      if (!bytes || !bytes.length || !this.ctx) return;
      const buf = this.ctx.createBuffer(1, bytes.length, 8000);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < bytes.length; i++) ch[i] = ULAW[bytes[i]];
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.gain);
      const now = this.ctx.currentTime;
      if (this.nextTime < now + 0.02) this.nextTime = now + JITTER;   // alku tai alivuoto
      if (this.nextTime > now + 2) this.nextTime = now + JITTER;      // liian suuri viive
      src.start(this.nextTime);
      this.nextTime += buf.duration;
    }
  }

  window.HosePlayer = HosePlayer;
  window._mp = { mpEncode, mpDecode };
})();
