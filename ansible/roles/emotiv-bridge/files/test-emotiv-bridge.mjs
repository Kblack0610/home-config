// Offline tests for emotiv-bridge.mjs: a fake Cortex websocket, a fake age and a fake rsync.
// Run: node --test ansible/roles/emotiv-bridge/files/test-emotiv-bridge.mjs
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { Bridge, Chunks, Uploader, Mqtt, Summary, sealSpool, mqttConnect, mqttPublish, MQTT_DISCONNECT, WARN } from './emotiv-bridge.mjs';

const roots = [];
after(() => { for (const r of roots) fs.rmSync(r, { recursive: true, force: true }); });

function tmpCfg() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'emotiv-bridge-test-'));
  roots.push(root);
  fs.mkdirSync(path.join(root, 'spool'));
  const bin = path.join(root, 'bin');
  fs.mkdirSync(bin);
  // age -r R -o OUT IN: the fake copies IN to OUT, so the test can read the chunk back.
  fs.writeFileSync(path.join(bin, 'age'), '#!/bin/sh\ncp "$5" "$4"\n', { mode: 0o755 });
  fs.writeFileSync(path.join(bin, 'rsync-fail'), '#!/bin/sh\necho "connection refused" >&2\nexit 255\n', { mode: 0o755 });
  return {
    root,
    spool_dir: path.join(root, 'spool'),
    age_bin: path.join(bin, 'age'),
    age_recipient: 'age1test',
    rsync_bin: path.join(bin, 'rsync-fail'),
    streams: ['eeg', 'mot', 'fac'],
    chunk_seconds: 3600,
    upload: { host: 'nas', port: 2222, user: 'emotiv-archive', key: '/k', known_hosts: '/kh' },
  };
}

const files = (dir) => fs.readdirSync(dir, { recursive: true }).filter((f) => fs.statSync(path.join(dir, f)).isFile()).sort();
const lines = (file) => zlib.gunzipSync(fs.readFileSync(file)).toString().trim().split('\n').map((l) => JSON.parse(l));

test('a chunk is gzip JSONL with a header, encrypted on rotate, plaintext removed', async () => {
  const cfg = tmpCfg();
  const c = new Chunks(cfg, 'abcdef123456', 'EPOCX-1', new Date('2026-11-11T10:00:00Z'));
  c.setCols('mot', ['ACCX']);
  c.write('mot', 1.5, [0.1]);
  c.write('mot', 1.6, [0.2]);
  await c.rotate();
  c.write('mot', 2.0, [0.3]);
  await c.rotate();
  const got = files(cfg.spool_dir);
  assert.deepEqual(got, ['2026-11-11/20261111T100000Z-abcdef12/mot-0001.jsonl.gz.age', '2026-11-11/20261111T100000Z-abcdef12/mot-0002.jsonl.gz.age']);
  const first = lines(path.join(cfg.spool_dir, got[0]));
  assert.deepEqual(first[0], { stream: 'mot', cols: ['ACCX'], session: 'abcdef123456', headset: 'EPOCX-1', chunk: 1 });
  assert.deepEqual(first.slice(1), [{ time: 1.5, mot: [0.1] }, { time: 1.6, mot: [0.2] }]);
  assert.equal(lines(path.join(cfg.spool_dir, got[1])).length, 2);
});

test('a .part left by a crash is sealed at startup', async () => {
  const cfg = tmpCfg();
  const dir = path.join(cfg.spool_dir, '2026-11-11', 's');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'fac-0003.jsonl.gz.part'), zlib.gzipSync('{"time":1}\n'));
  await sealSpool(cfg);
  assert.deepEqual(files(cfg.spool_dir), ['2026-11-11/s/fac-0003.jsonl.gz.age']);
});

test('a failed age keeps the plaintext for the next seal', async () => {
  const cfg = tmpCfg();
  cfg.age_bin = '/nonexistent/age';
  const dir = path.join(cfg.spool_dir, 'd');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'mot-0001.jsonl.gz'), zlib.gzipSync('x\n'));
  await sealSpool(cfg);
  assert.deepEqual(files(cfg.spool_dir), ['d/mot-0001.jsonl.gz']);
});

test('a failed upload leaves the spool untouched and reports the rsync code', async () => {
  const cfg = tmpCfg();
  const dir = path.join(cfg.spool_dir, 'd');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'mot-0001.jsonl.gz.age'), 'sealed');
  const results = [];
  const u = new Uploader(cfg, (r) => results.push(r));
  await u.kick();
  clearTimeout(u.timer);
  assert.deepEqual(results, [{ ok: false, code: 255 }]);
  assert.deepEqual(files(cfg.spool_dir), ['d/mot-0001.jsonl.gz.age']);
  const args = u.args();
  assert.ok(args.includes('--remove-source-files'));
  assert.deepEqual(args.slice(-2), [`${cfg.spool_dir}/`, 'emotiv-archive@nas:']);
});

test('mqtt packets: CONNECT and a retained QoS 0 PUBLISH', () => {
  assert.equal(mqttConnect('c', 120).toString('hex'), '100d00044d5154540402007800' + '0163');
  assert.equal(mqttPublish('t/x', '{}', true).toString('hex'), '3107' + '0003742f78' + '7b7d');
  assert.equal(mqttPublish('t', 'x'.repeat(200), false).subarray(0, 3).toString('hex'), '30cb01');
});

// A Cortex that answers the bridge's calls; `headsets`, `createFails` and `cols` steer it.
function fakeCortex() {
  const state = { headsets: [{ id: 'EPOCX-1', status: 'connected' }], createFails: false, calls: [], cols: {} };
  class FakeWS {
    constructor() { state.ws = this; }
    open() { return this.onopen(); }
    send(raw) {
      const m = JSON.parse(raw);
      state.calls.push({ method: m.method, params: m.params });
      const reply = (result, error) => setImmediate(() => this.onmessage({ data: JSON.stringify({ id: m.id, result, error }) }));
      switch (m.method) {
        case 'authorize': return reply({ cortexToken: `tok-${m.params.debit ?? 0}` });
        case 'getLicenseInfo': return reply({ license: { localQuota: 0, validTo: '2036-11-07T00:00:00.000+07:00' } });
        case 'queryHeadsets': return reply(state.headsets);
        case 'createSession': return state.createFails ? reply(undefined, { code: -32005, message: 'session exists' }) : reply({ id: 'sess-0001-xyz' });
        case 'subscribe': {
          const s = m.params.streams[0];
          return s === 'eeg' ? reply({ success: [], failure: [{ streamName: 'eeg', code: -32016, message: 'no license' }] })
            : reply({ success: [{ streamName: s, cols: state.cols[s] ?? [`${s}-col`] }], failure: [] });
        }
        default: return reply({});
      }
    }
    push(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
    close() {}
  }
  return { state, FakeWS };
}

const noUpload = { kick: () => Promise.resolve() };

test('a connected headset gets one active session with every licensed stream; disconnect seals it', async () => {
  const cfg = tmpCfg();
  const { state, FakeWS } = fakeCortex();
  const b = new Bridge(cfg, { clientId: 'id', clientSecret: 'sec' }, { WebSocket: FakeWS, uploader: noUpload });
  b.connect();
  await state.ws.open();
  assert.equal(b.session.id, 'sess-0001-xyz');
  const debits = state.calls.filter((c) => c.method === 'authorize').map((c) => c.params.debit);
  assert.deepEqual(debits, [undefined, 1]);
  assert.deepEqual(state.calls.filter((c) => c.method === 'createSession').map((c) => c.params.status), ['active']);
  state.ws.push({ sid: 'sess-0001-xyz', time: 10.5, mot: [1, 2] });
  state.ws.push({ sid: 'other-session', time: 10.6, mot: [9] });
  state.ws.push({ sid: 'sess-0001-xyz', time: 10.7, fac: ['blink'] });
  assert.equal(b.heartbeat().wearing, 1);

  state.headsets = [];
  state.ws.push({ warning: { code: WARN.DISCONNECTED_TIMEOUT, message: { headsetId: 'EPOCX-1' } } });
  await b.reconciling;
  assert.equal(b.session, null);
  assert.ok(state.calls.some((c) => c.method === 'updateSession' && c.params.status === 'close'));
  const got = files(cfg.spool_dir);
  assert.equal(got.length, 2);
  const mot = lines(path.join(cfg.spool_dir, got.find((f) => f.includes('mot-0001'))));
  assert.deepEqual(mot[0].cols, ['mot-col']);
  assert.deepEqual(mot.slice(1), [{ time: 10.5, mot: [1, 2] }]);
  assert.equal(b.heartbeat().wearing, 0);
  assert.equal(b.heartbeat().license_valid_to_epoch, Math.floor(Date.parse('2036-11-07T00:00:00.000+07:00') / 1000));
});

test('a session the headset refuses is tried once per connection, not once per scan', async () => {
  const cfg = tmpCfg();
  const { state, FakeWS } = fakeCortex();
  state.createFails = true;
  const b = new Bridge(cfg, { clientId: 'id', clientSecret: 'sec' }, { WebSocket: FakeWS, uploader: noUpload });
  b.connect();
  await state.ws.open();
  await b.reconcile();
  await b.reconcile();
  assert.equal(state.calls.filter((c) => c.method === 'createSession').length, 1);
  assert.equal(b.heartbeat().last_error_code, -32005);

  // Off and on again: the bridge tries once more.
  state.headsets = [];
  await b.reconcile();
  state.headsets = [{ id: 'EPOCX-1', status: 'connected' }];
  state.createFails = false;
  await b.reconcile();
  assert.equal(state.calls.filter((c) => c.method === 'createSession').length, 2);
  assert.ok(b.session);
  await b.endSession('test', false);
});

test('no headset asks Cortex to scan; a discovered one is connected', async () => {
  const cfg = tmpCfg();
  const { state, FakeWS } = fakeCortex();
  state.headsets = [];
  const b = new Bridge(cfg, { clientId: 'id', clientSecret: 'sec' }, { WebSocket: FakeWS, uploader: noUpload });
  b.connect();
  await state.ws.open();
  state.headsets = [{ id: 'EPOCX-1', status: 'discovered' }];
  await b.reconcile();
  const ctl = state.calls.filter((c) => c.method === 'controlDevice').map((c) => c.params.command);
  assert.deepEqual(ctl, ['refresh', 'connect']);
  assert.equal(b.session, null);
});

test('run through a symlinked directory, the script still starts main', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'emotiv-bridge-link-'));
  roots.push(root);
  const link = path.join(root, 'share');
  fs.symlinkSync(path.dirname(fileURLToPath(import.meta.url)), link);
  const r = spawnSync(process.execPath, [path.join(link, 'emotiv-bridge.mjs'), path.join(root, 'missing.json')], { encoding: 'utf8' });
  assert.equal(r.status, 1);
  assert.match(r.stdout + r.stderr, /fatal/);
});

test('mqtt goes through the nc relay: CONNECT, the retained PUBLISH, then DISCONNECT', async () => {
  const cfg = tmpCfg();
  const out = path.join(cfg.root, 'wire');
  const nc = path.join(cfg.root, 'bin', 'nc');
  fs.writeFileSync(nc, `#!/bin/sh\necho "$1 $2" > "${out}.args"\ncat > "${out}"\n`, { mode: 0o755 });
  const m = new Mqtt({ nc_bin: nc, host: 'broker', port: 31883, client_id: 'c', keepalive_seconds: 120 });
  m.connect();
  await new Promise((r) => m.proc.once('spawn', r));
  m.publish('t/x', {});
  const closed = new Promise((r) => m.proc.once('close', r));
  m.stop();
  await closed;
  assert.equal(fs.readFileSync(`${out}.args`, 'utf8'), 'broker 31883\n');
  assert.deepEqual(fs.readFileSync(out), Buffer.concat([mqttConnect('c', 120), mqttPublish('t/x', '{}', true), MQTT_DISCONNECT]));
});

// Columns as Cortex answered subscribe in P1 (mini ~/emotiv-capture/20261110T164237/cols.json).
const SENSORS = ['AF3', 'F7', 'F3', 'FC5', 'T7', 'P7', 'O1', 'O2', 'P8', 'T8', 'FC6', 'F4', 'F8', 'AF4'];
const P1_COLS = {
  pow: SENSORS.flatMap((s) => ['theta', 'alpha', 'betaL', 'betaH', 'gamma'].map((b) => `${s}/${b}`)),
  met: ['attention.isActive', 'attention', 'eng.isActive', 'eng', 'exc.isActive', 'exc', 'lex', 'str.isActive', 'str', 'rel.isActive', 'rel', 'int.isActive', 'int'],
  fac: ['eyeAct', 'uAct', 'uPow', 'lAct', 'lPow'],
  mot: ['COUNTER_MEMS', 'INTERPOLATED_MEMS', 'Q0', 'Q1', 'Q2', 'Q3', 'ACCX', 'ACCY', 'ACCZ', 'MAGX', 'MAGY', 'MAGZ'],
  dev: ['Battery', 'Signal', [...SENSORS, 'OVERALL'], 'BatteryPercent'],
  eq: ['batteryPercent', 'overall', 'sampleRateQuality', ...SENSORS],
};

function p1Summary() {
  const s = new Summary();
  for (const [k, c] of Object.entries(P1_COLS)) s.setCols(k, c);
  return s;
}

test('summary: band power is the mean over sensors and samples, per band', () => {
  const s = p1Summary();
  // theta 1, alpha 2, betaL 3, betaH 4, gamma 5 on every sensor, then all doubled.
  s.add('pow', SENSORS.flatMap(() => [1, 2, 3, 4, 5]));
  s.add('pow', SENSORS.flatMap(() => [2, 4, 6, 8, 10]));
  const pow = Object.fromEntries(s.take()).pow;
  assert.deepEqual(pow, { theta: 1.5, alpha: 3, beta_l: 4.5, beta_h: 6, gamma: 7.5 });
  assert.equal(Object.fromEntries(s.take()).pow, undefined, 'a new window starts empty');
});

test('summary: metrics skip inactive (null) values and take the names, not the codes', () => {
  const s = p1Summary();
  s.add('met', [false, null, true, 0.61, true, 0.2, 0.3, true, 0.4, false, null, true, 0.5]);
  assert.deepEqual(Object.fromEntries(s.take()).met, { engagement: 0.61, excitement: 0.2, long_excitement: 0.3, stress: 0.4, interest: 0.5 });
});

test('summary: a facial action held over many samples counts once, and counters survive windows', () => {
  const s = p1Summary();
  const first = Object.fromEntries(s.take()).fac;
  assert.equal(first.clench, 0, 'known actions exist before the first event');
  for (const row of [
    ['neutral', 'neutral', 0, 'neutral', 0],
    ['blink', 'neutral', 0, 'clench', 0.9],
    ['blink', 'neutral', 0, 'clench', 0.8],
    ['neutral', 'neutral', 0, 'clench', 0.7],
    ['winkL', 'surprise', 1, 'neutral', 0],
  ]) s.add('fac', row);
  s.take();
  s.add('fac', ['neutral', 'neutral', 0, 'clench', 1]);
  const fac = Object.fromEntries(s.take()).fac;
  assert.equal(fac.blink, 1);
  assert.equal(fac.clench, 2);
  assert.equal(fac.wink_l, 1);
  assert.equal(fac.surprise, 1);
  assert.equal(fac.smirk_left, 0);
});

test('summary: motion is the accel magnitude mean and its spread; contact counts sensors at 4', () => {
  const s = p1Summary();
  s.add('mot', [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0]);
  s.add('mot', [2, 0, 0, 0, 0, 0, 3, 0, 4, 0, 0, 0]);
  s.add('dev', [3, 1, [4, 4, 4, 0, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 92], 61]);
  s.add('eq', [61, 83, 1, ...SENSORS.map(() => 4)]);
  const out = Object.fromEntries(s.take());
  assert.deepEqual(out.mot, { acc_magnitude: 3, motion: 2 });
  assert.deepEqual(out.dev, { battery_percent: 61, signal: 1, contact_good: 13, contact_sensors: 14, contact_overall: 92, eeg_quality: 83, sample_rate_quality: 1 });
});

test('the bridge feeds its session streams to the summary and signals each wear start and end', async () => {
  const cfg = tmpCfg();
  const { state, FakeWS } = fakeCortex();
  const b = new Bridge(cfg, { clientId: 'id', clientSecret: 'sec' }, { WebSocket: FakeWS, uploader: noUpload });
  const changes = [];
  b.onChange = () => changes.push(b.heartbeat().wearing);
  state.cols.fac = P1_COLS.fac;
  b.connect();
  await state.ws.open();
  state.ws.push({ sid: 'sess-0001-xyz', time: 1, fac: ['neutral', 'neutral', 0, 'clench', 1] });
  state.ws.push({ sid: 'other-session', time: 1, fac: ['blink', 'neutral', 0, 'neutral', 0] });
  assert.equal(b.summary.facial.clench, 1);
  assert.equal(b.summary.facial.blink, 0, 'another session is ignored');
  state.headsets = [];
  state.ws.push({ warning: { code: WARN.DISCONNECTED_TIMEOUT, message: {} } });
  await b.reconciling;
  assert.deepEqual(changes, [1, 0]);
});
