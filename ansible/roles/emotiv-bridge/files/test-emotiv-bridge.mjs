// Offline tests for emotiv-bridge.mjs: a fake Cortex websocket, a fake age and a fake rsync.
// Run: node --test ansible/roles/emotiv-bridge/files/test-emotiv-bridge.mjs
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { Bridge, Chunks, Uploader, sealSpool, mqttConnect, mqttPublish, WARN } from './emotiv-bridge.mjs';

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

// A Cortex that answers the bridge's calls; `headsets` and `createFails` steer it.
function fakeCortex() {
  const state = { headsets: [{ id: 'EPOCX-1', status: 'connected' }], createFails: false, calls: [] };
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
            : reply({ success: [{ streamName: s, cols: [`${s}-col`] }], failure: [] });
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
