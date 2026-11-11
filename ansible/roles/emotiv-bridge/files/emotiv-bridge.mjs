#!/usr/bin/env node
// emotiv-bridge: while the EPOC X is connected in the EMOTIV Launcher, records every Cortex
// stream the license allows into gzip JSONL chunks, encrypts each closed chunk with age to the
// EEG recipient (the mini holds no identity, so it cannot read what it wrote), and pushes it to
// the NAS over rsync with a key that rrsync confines to the archive directory. A heartbeat goes
// to MQTT. No dependencies beyond Node (WebSocket is built in) and the age and rsync binaries.
//
// Usage: node emotiv-bridge.mjs <config.json>   (rendered by roles/emotiv-bridge)
// Tests: node --test test-emotiv-bridge.mjs (beside this file).
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';

// Cortex warning codes, from Emotiv's cortex-example (python/cortex.py).
export const WARN = {
  STOP_ALL_STREAMS: 0,
  CLOSE_SESSION: 1,
  CANNOT_CONNECT_TIMEOUT: 102,
  DISCONNECTED_TIMEOUT: 103,
  HEADSET_CONNECTED: 104,
  SCANNING_FINISHED: 142,
};

const CALL_TIMEOUT_MS = 15000;
const RETRY_MIN_MS = 30000;
const RETRY_MAX_MS = 15 * 60000;

const log = (...a) => console.error(new Date().toISOString(), ...a);
const nextBackoff = (ms) => Math.min(ms ? ms * 2 : RETRY_MIN_MS, RETRY_MAX_MS);

// Runs a command; resolves with its exit code and stderr, never rejects.
export function run(bin, args) {
  return new Promise((resolve) => {
    let err = '';
    const p = spawn(bin, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    p.stderr.on('data', (d) => { err += d; });
    p.on('error', (e) => resolve({ code: -1, err: String(e) }));
    p.on('close', (code) => resolve({ code, err: err.trim() }));
  });
}

function walk(dir) {
  let out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    out = e.isDirectory() ? out.concat(walk(p)) : out.concat(p);
  }
  return out;
}

export function spoolBytes(dir) {
  return walk(dir).reduce((n, f) => n + fs.statSync(f).size, 0);
}

function pruneEmptyDirs(dir, root = dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) pruneEmptyDirs(path.join(dir, e.name), root);
  }
  if (dir !== root && fs.readdirSync(dir).length === 0) fs.rmdirSync(dir);
}

// Encrypts every finished plaintext chunk in the spool. A `.part` left by a crash is a gzip
// stream cut short; its complete members still decompress, so it is sealed as it is.
export async function sealSpool(cfg) {
  for (const f of walk(cfg.spool_dir)) {
    if (!f.endsWith('.jsonl.gz') && !f.endsWith('.jsonl.gz.part')) continue;
    const plain = f.endsWith('.part') ? f.slice(0, -5) : f;
    if (plain !== f) fs.renameSync(f, plain);
    const r = await run(cfg.age_bin, ['-r', cfg.age_recipient, '-o', `${plain}.age`, plain]);
    if (r.code === 0) fs.unlinkSync(plain);
    else log('age failed, plaintext kept for the next seal:', plain, r.err);
  }
}

// One wear's chunk files: <spool>/<YYYY-MM-DD>/<start>-<session>/<stream>-<NNNN>.jsonl.gz
export class Chunks {
  constructor(cfg, session, headset, now = new Date()) {
    this.cfg = cfg;
    this.session = session;
    this.headset = headset;
    const iso = now.toISOString();
    const stamp = iso.replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
    this.dir = path.join(cfg.spool_dir, iso.slice(0, 10), `${stamp}-${session.slice(0, 8)}`);
    fs.mkdirSync(this.dir, { recursive: true, mode: 0o700 });
    this.open = new Map();
    this.seq = new Map();
    this.cols = new Map();
  }

  setCols(stream, cols) { this.cols.set(stream, cols); }

  write(stream, time, values) {
    let c = this.open.get(stream);
    if (!c) {
      const n = (this.seq.get(stream) ?? 0) + 1;
      this.seq.set(stream, n);
      const file = path.join(this.dir, `${stream}-${String(n).padStart(4, '0')}.jsonl.gz.part`);
      const gz = zlib.createGzip();
      c = { file, gz, done: pipeline(gz, fs.createWriteStream(file, { mode: 0o600 })) };
      this.open.set(stream, c);
      gz.write(JSON.stringify({ stream, cols: this.cols.get(stream) ?? null, session: this.session,
        headset: this.headset, chunk: n }) + '\n');
    }
    c.gz.write(JSON.stringify({ time, [stream]: values }) + '\n');
  }

  // Closes every open chunk and encrypts it. The next write starts the stream's next chunk.
  async rotate() {
    const closing = [...this.open.values()];
    this.open.clear();
    for (const c of closing) {
      c.gz.end();
      await c.done;
      fs.renameSync(c.file, c.file.slice(0, -5));
    }
    await sealSpool(this.cfg);
  }
}

// Pushes sealed chunks to the NAS. rsync deletes a source file only once it is transferred
// and checked, so a failed upload leaves the spool as it was and the next attempt resends it.
export class Uploader {
  constructor(cfg, onResult = () => {}) {
    this.cfg = cfg;
    this.onResult = onResult;
    this.running = null;
    this.again = false;
    this.backoff = 0;
    this.timer = null;
  }

  args() {
    const u = this.cfg.upload;
    const ssh = ['ssh', '-i', u.key, '-p', String(u.port), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
      '-o', `UserKnownHostsFile=${u.known_hosts}`, '-o', 'StrictHostKeyChecking=yes'].join(' ');
    // -rt, not -a: the far side is an unprivileged user, so owner and group cannot be kept.
    return ['-rt', '--chmod=D0750,F0640', '--remove-source-files', '--include=*/', '--include=*.age', '--exclude=*', '--prune-empty-dirs',
      '-e', ssh, `${this.cfg.spool_dir}/`, `${u.user}@${u.host}:`];
  }

  kick() {
    if (this.running) { this.again = true; return this.running; }
    clearTimeout(this.timer);
    this.running = this.once().finally(() => {
      this.running = null;
      if (this.again) { this.again = false; this.kick(); }
    });
    return this.running;
  }

  async once() {
    if (!walk(this.cfg.spool_dir).some((f) => f.endsWith('.age'))) return;
    const r = await run(this.cfg.rsync_bin, this.args());
    if (r.code === 0) {
      pruneEmptyDirs(this.cfg.spool_dir);
      this.backoff = 0;
      this.onResult({ ok: true });
      return;
    }
    this.backoff = nextBackoff(this.backoff);
    log(`upload failed (rsync ${r.code}), retry in ${this.backoff / 1000}s:`, r.err);
    this.onResult({ ok: false, code: r.code });
    this.timer = setTimeout(() => this.kick(), this.backoff);
    this.timer.unref?.();
  }
}

// Minimal MQTT 3.1.1 publisher: CONNECT and QoS 0 PUBLISH, nothing else. The broker is
// anonymous on the LAN (apps/mosquitto); a heartbeat lost while disconnected is not resent.
function mqttString(s) {
  const b = Buffer.from(s, 'utf8');
  return Buffer.concat([Buffer.from([b.length >> 8, b.length & 0xff]), b]);
}

function mqttPacket(first, body) {
  const len = [];
  let n = body.length;
  do { let d = n % 128; n = Math.floor(n / 128); if (n > 0) d |= 0x80; len.push(d); } while (n > 0);
  return Buffer.concat([Buffer.from([first, ...len]), body]);
}

export const mqttConnect = (clientId, keepalive) => mqttPacket(0x10, Buffer.concat([
  mqttString('MQTT'), Buffer.from([0x04, 0x02, keepalive >> 8, keepalive & 0xff]), mqttString(clientId)]));

export const mqttPublish = (topic, payload, retain) => mqttPacket(0x30 | (retain ? 1 : 0),
  Buffer.concat([mqttString(topic), Buffer.from(payload, 'utf8')]));

export class Mqtt {
  constructor(cfg) { this.cfg = cfg; this.sock = null; this.backoff = 0; this.stopped = false; }

  connect() {
    const s = net.createConnection({ host: this.cfg.host, port: this.cfg.port });
    s.on('connect', () => { this.backoff = 0; s.write(mqttConnect(this.cfg.client_id, this.cfg.keepalive_seconds)); this.sock = s; });
    s.on('data', () => {});
    s.on('error', (e) => log('mqtt', e.message));
    s.on('close', () => {
      this.sock = null;
      if (this.stopped) return;
      this.backoff = nextBackoff(this.backoff);
      setTimeout(() => this.connect(), this.backoff).unref?.();
    });
  }

  publish(topic, obj) { if (this.sock) this.sock.write(mqttPublish(topic, JSON.stringify(obj), true)); }

  stop() { this.stopped = true; this.sock?.end(); }
}

// The Cortex side: one websocket, one activated session per wear.
export class Bridge {
  constructor(cfg, creds, deps = {}) {
    this.cfg = cfg;
    this.creds = creds;
    this.WebSocket = deps.WebSocket ?? globalThis.WebSocket;
    this.uploader = deps.uploader ?? new Uploader(cfg, (r) => this.onUpload(r));
    this.ws = null;
    this.id = 0;
    this.pending = new Map();
    this.token = null;
    this.session = null; // { id, headset, chunks, rotateTimer, started }
    this.declined = null; // headset id whose createSession failed; not retried until it disconnects
    this.reconciling = null;
    this.reconnectMs = 0;
    this.stats = { lastUploadEpoch: 0, lastErrorCode: 0, localQuota: null, validToEpoch: null };
  }

  onUpload(r) {
    if (r.ok) this.stats.lastUploadEpoch = Math.floor(Date.now() / 1000);
    else this.stats.lastErrorCode = r.code;
  }

  call(method, params = {}) {
    return new Promise((resolve) => {
      const id = ++this.id;
      const t = setTimeout(() => { this.pending.delete(id); resolve({ error: { code: -1, message: `${method} timed out` } }); }, CALL_TIMEOUT_MS);
      this.pending.set(id, (m) => { clearTimeout(t); resolve(m); });
      this.ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
    });
  }

  connect() {
    const ws = new this.WebSocket(this.cfg.cortex_url);
    this.ws = ws;
    ws.onmessage = (ev) => this.onMessage(JSON.parse(ev.data));
    ws.onerror = (e) => log('cortex', e.message ?? 'websocket error');
    ws.onclose = () => this.onClose();
    ws.onopen = () => this.onOpen();
  }

  async onOpen() {
    this.reconnectMs = 0;
    const au = await this.call('authorize', { clientId: this.creds.clientId, clientSecret: this.creds.clientSecret });
    if (!au.result) { this.fail('authorize', au.error); return; }
    this.token = au.result.cortexToken;
    await this.readLicense();
    await this.reconcile();
  }

  async readLicense() {
    const li = await this.call('getLicenseInfo', { cortexToken: this.token });
    if (!li.result) return;
    this.stats.localQuota = li.result.license?.localQuota ?? null;
    const to = li.result.license?.validTo;
    this.stats.validToEpoch = to ? Math.floor(Date.parse(to) / 1000) : null;
  }

  async onClose() {
    log('cortex connection closed');
    for (const r of this.pending.values()) r({ error: { code: -1, message: 'connection closed' } });
    this.pending.clear();
    this.token = null;
    await this.endSession('cortex connection closed', false);
    if (this.stopping) return;
    this.reconnectMs = nextBackoff(this.reconnectMs);
    setTimeout(() => this.connect(), this.reconnectMs).unref?.();
  }

  onMessage(m) {
    if (m.id !== undefined && this.pending.has(m.id)) {
      const r = this.pending.get(m.id);
      this.pending.delete(m.id);
      r(m);
      return;
    }
    if (m.warning) { this.onWarning(m.warning); return; }
    const s = this.session;
    if (!s || (m.sid && m.sid !== s.id)) return;
    for (const stream of this.cfg.streams) {
      if (stream in m) { s.chunks.write(stream, m.time, m[stream]); return; }
    }
  }

  onWarning(w) {
    log('warning', w.code, JSON.stringify(w.message));
    if (w.code === WARN.STOP_ALL_STREAMS || w.code === WARN.CLOSE_SESSION) {
      this.endSession(`cortex warning ${w.code}`, false).then(() => this.reconcile());
    } else if ([WARN.CANNOT_CONNECT_TIMEOUT, WARN.DISCONNECTED_TIMEOUT, WARN.HEADSET_CONNECTED, WARN.SCANNING_FINISHED].includes(w.code)) {
      this.reconcile();
    }
  }

  fail(what, error) {
    this.stats.lastErrorCode = error?.code ?? -1;
    log(what, 'failed:', JSON.stringify(error));
  }

  // Brings the session in line with the headset: start one when it is connected, end it when it
  // is gone. Driven by Cortex warnings; the scan timer (main) covers the one change Cortex does
  // not push, a headset appearing.
  reconcile() {
    if (!this.reconciling) {
      this.reconciling = this.reconcileOnce().finally(() => { this.reconciling = null; });
    }
    return this.reconciling;
  }

  async reconcileOnce() {
    if (!this.token) return;
    const q = await this.call('queryHeadsets');
    if (!q.result) { this.fail('queryHeadsets', q.error); return; }
    const connected = q.result.find((h) => h.status === 'connected');
    if (this.declined && !q.result.some((h) => h.id === this.declined && h.status === 'connected')) this.declined = null;
    if (this.session && this.session.headset !== connected?.id) await this.endSession('headset disconnected', true);
    if (connected && !this.session && this.declined !== connected.id) { await this.startSession(connected.id); return; }
    if (connected) return;
    const found = q.result.find((h) => h.status === 'discovered');
    if (found) await this.call('controlDevice', { command: 'connect', headset: found.id });
    else await this.call('controlDevice', { command: 'refresh' });
  }

  // An activated session needs an authorize with debit 1 (P1: -32019 otherwise). One debit per
  // wear: a failed createSession is not retried until the headset disconnects, so a headset held
  // by EmotivPRO costs one attempt, not one per scan.
  async startSession(headset) {
    const au = await this.call('authorize', { clientId: this.creds.clientId, clientSecret: this.creds.clientSecret, debit: 1 });
    if (!au.result) { this.fail('authorize debit', au.error); this.declined = headset; return; }
    this.token = au.result.cortexToken;
    const cs = await this.call('createSession', { cortexToken: this.token, headset, status: 'active' });
    if (!cs.result) { this.fail('createSession', cs.error); this.declined = headset; return; }
    const chunks = new Chunks(this.cfg, cs.result.id, headset);
    this.session = { id: cs.result.id, headset, chunks, started: Date.now() };
    const subscribed = [];
    for (const stream of this.cfg.streams) {
      const r = await this.call('subscribe', { cortexToken: this.token, session: cs.result.id, streams: [stream] });
      for (const ok of r.result?.success ?? []) { chunks.setCols(ok.streamName, ok.cols); subscribed.push(ok.streamName); }
      for (const f of r.result?.failure ?? []) log('subscribe', stream, 'refused:', f.code, f.message);
      if (r.error) log('subscribe', stream, 'error:', JSON.stringify(r.error));
    }
    log('session', cs.result.id, 'on', headset, 'streams', subscribed.join(','));
    this.session.rotateTimer = setInterval(() => this.rotate(), this.cfg.chunk_seconds * 1000);
    await this.readLicense();
  }

  async rotate() {
    if (!this.session) return;
    await this.session.chunks.rotate();
    this.uploader.kick();
  }

  async endSession(reason, closeInCortex) {
    const s = this.session;
    if (!s) return;
    this.session = null;
    clearInterval(s.rotateTimer);
    log('session', s.id, 'ended:', reason);
    if (closeInCortex && this.token) await this.call('updateSession', { cortexToken: this.token, session: s.id, status: 'close' });
    await s.chunks.rotate();
    this.uploader.kick();
  }

  heartbeat() {
    return {
      up: 1,
      wearing: this.session ? 1 : 0,
      session_seconds: this.session ? Math.round((Date.now() - this.session.started) / 1000) : 0,
      spool_bytes: spoolBytes(this.cfg.spool_dir),
      last_upload_epoch: this.stats.lastUploadEpoch,
      last_error_code: this.stats.lastErrorCode,
      local_quota: this.stats.localQuota,
      license_valid_to_epoch: this.stats.validToEpoch,
    };
  }

  async stop() {
    this.stopping = true;
    await this.endSession('bridge stopping', true);
    this.ws?.close();
  }
}

async function main(configPath) {
  const cfg = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const creds = JSON.parse(fs.readFileSync(cfg.credentials_file, 'utf8'));
  fs.mkdirSync(cfg.spool_dir, { recursive: true, mode: 0o700 });
  await sealSpool(cfg);
  const bridge = new Bridge(cfg, creds);
  bridge.uploader.kick();
  bridge.connect();
  // Cortex pushes no event when a headset is switched on or comes into range, so the bridge
  // asks for a scan on this one cadence. Every other change arrives as a warning.
  setInterval(() => bridge.reconcile(), cfg.scan_interval_seconds * 1000);
  const mqtt = new Mqtt(cfg.mqtt);
  mqtt.connect();
  // The heartbeat is a liveness signal for Prometheus, so it is published on a fixed cadence.
  setInterval(() => mqtt.publish(cfg.mqtt.topic, bridge.heartbeat()), cfg.heartbeat_seconds * 1000);
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await bridge.stop();
    mqtt.publish(cfg.mqtt.topic, { ...bridge.heartbeat(), up: 0 });
    mqtt.stop();
    await bridge.uploader.kick();
    process.exit(0);
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv[2]).catch((e) => { log('fatal', e.stack ?? e); process.exit(1); });
}
