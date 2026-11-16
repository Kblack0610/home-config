#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mne>=1.8", "numpy"]
# ///
"""Decrypt one Emotiv session from the NAS archive and turn it into a cleaned FIF plus an event table.

Usage:
  scripts/eeg-review.py <session>  [--out DIR]
    <session>: a local session directory (the bridge's *.jsonl.gz.age chunks, plain *.jsonl.gz,
    or a P1 capture with *.jsonl plus cols.json), or `<date>/<session-dir>` under
    /mnt/nas/private/eeg, fetched from asus-laptop over ssh.

Writes to <out>/<session-dir>/ (default ~/eeg/review):
  events.csv        one row per blink, wink, frown, clench, smile, mental command or head movement
  cleaned_raw.fif   band power, motion and contact quality on one 8 Hz grid, events and bad
                    stretches (poor contact, gaps) as MNE annotations
  eeg_raw.fif       only when the archive holds raw `eeg` (a paid license): band-passed and notched
  summary.json      sample count, span and rate per stream

The decrypt key is the EEG age identity in ansible/roles/emotiv-bridge/eeg-identity.sops.yaml. It
is piped from sops to age and never written to disk. How the archive is made:
ansible/roles/emotiv-bridge/README.md. Tests: scripts/test_eeg_review.py (stdlib only).
"""
import argparse
import csv
import gzip
import io
import json
import math
import os
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IDENTITY_FILE = REPO / 'ansible/roles/emotiv-bridge/eeg-identity.sops.yaml'
NAS_SSH = ['ssh', '-p', '2222', '192.168.1.152']
NAS_ROOT = '/mnt/nas/private/eeg'

GRID_HZ = 8.0                 # pow's rate, the slowest dense stream
HEAD_MOVE_DEG_S = 30.0        # angular speed above which the head counts as moving
HEAD_MOVE_MIN_S = 0.1         # shorter bursts are sensor jitter
HEAD_MOVE_MERGE_S = 0.25      # two bursts closer than this are one movement
CONTACT_MIN = 50              # eq "overall" (0-100) below this is a BAD_contact stretch
GAP_S = 0.5                   # a pow gap longer than this is a BAD_gap stretch
EEG_BAND = (1.0, 45.0)
LINE_HZ = 60.0
SENSORS = ['AF3', 'F7', 'F3', 'FC5', 'T7', 'P7', 'O1', 'O2', 'P8', 'T8', 'FC6', 'F4', 'F8', 'AF4']

# Categorical action streams: (stream, act column, power column or None, source label).
ACTIONS = [('fac', 'eyeAct', None, 'eye'), ('fac', 'uAct', 'uPow', 'upper'),
           ('fac', 'lAct', 'lPow', 'lower'), ('com', 'act', 'pow', 'command')]



def identity_from_sops():
    r = subprocess.run(['sops', 'decrypt', '--extract', '["identity"]', str(IDENTITY_FILE)],
                       capture_output=True, check=True)
    return r.stdout


def read_chunk(path, identity=None):
    """Lines of one chunk file. `.age` is decrypted in memory with the identity on stdin."""
    name = str(path)
    if name.endswith('.age'):
        if identity is None:
            raise SystemExit(f'{path}: encrypted, and no identity given')
        r = subprocess.run(['age', '-d', '-i', '-', name], input=identity, capture_output=True)
        if r.returncode:
            raise SystemExit(f'age failed on {path}: {r.stderr.decode().strip()}')
        data = gzip.decompress(r.stdout)
    elif name.endswith('.gz') or name.endswith('.gz.part'):
        data = gzip.decompress(Path(path).read_bytes())
    else:
        data = Path(path).read_bytes()
    return [json.loads(line) for line in data.decode().splitlines() if line.strip()]


def stream_of(path):
    return Path(path).name.split('-')[0].split('.')[0]


def load_session(sdir, identity=None):
    """{stream: {'cols': [...], 'rows': [(time, values), ...]}}, rows sorted by time.

    A bridge chunk's first line is its header (stream, cols, session, headset, chunk). A P1
    capture has no header; its columns are in cols.json beside the stream files.
    """
    sdir = Path(sdir)
    p1_cols = json.loads((sdir / 'cols.json').read_text()) if (sdir / 'cols.json').exists() else {}
    out = {}
    for f in sorted(sdir.iterdir()):
        if f.name == 'cols.json':
            continue
        lines = read_chunk(f, identity)
        stream = stream_of(f)
        s = out.setdefault(stream, {'cols': p1_cols.get(stream), 'rows': []})
        for line in lines:
            if 'cols' in line and 'time' not in line:
                s['cols'] = line['cols'] or s['cols']
            elif stream in line:
                s['rows'].append((line['time'], line[stream]))
    for s in out.values():
        s['rows'].sort(key=lambda r: r[0])
    return out


def fetch_from_nas(name, dest):
    """Copies <date>/<session-dir> from the NAS into dest (still encrypted) and returns its path."""
    r = subprocess.run(NAS_SSH + [f'sudo tar -C {NAS_ROOT} -cf - {name}'], capture_output=True)
    if r.returncode:
        raise SystemExit(f'fetch {name} failed: {r.stderr.decode().strip()}')
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
        t.extractall(dest, filter='data')
    return Path(dest) / name



def summarize(session):
    out = {}
    for name, s in sorted(session.items()):
        rows = s['rows']
        if not rows:
            continue
        span = rows[-1][0] - rows[0][0]
        out[name] = {'samples': len(rows), 'first': rows[0][0], 'last': rows[-1][0],
                     'span_s': round(span, 3), 'rate_hz': round((len(rows) - 1) / span, 3) if span > 0 else None}
    return out


def action_events(session):
    """One event per run of the same non-neutral action: onset, duration, peak power."""
    events = []
    for stream, act_col, pow_col, source in ACTIONS:
        s = session.get(stream)
        if not s or not s['cols'] or act_col not in s['cols']:
            continue
        ai = s['cols'].index(act_col)
        pi = s['cols'].index(pow_col) if pow_col else None
        run = None  # [act, onset, last, peak]
        for t, v in s['rows'] + [(None, None)]:
            act = v[ai] if v else None
            if run and act != run[0]:
                end = t if t is not None else run[2]  # the run lasts until the sample that ends it
                events.append({'onset': run[1], 'duration_s': end - run[1], 'kind': run[0],
                               'source': source, 'strength': run[3]})
                run = None
            if act and act != 'neutral':
                p = v[pi] if pi is not None and isinstance(v[pi], (int, float)) else 1.0
                if run:
                    run[2], run[3] = t, max(run[3], p)
                else:
                    run = [act, t, t, p]
    return events


def quat(v, cols):
    q = [v[cols.index(k)] for k in ('Q0', 'Q1', 'Q2', 'Q3')]
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return [x / n for x in q]


def angular_speed(session):
    """[(time, deg/s)] between consecutive motion samples, from the headset's quaternion."""
    s = session.get('mot')
    if not s or not s['cols'] or 'Q0' not in s['cols']:
        return []
    out, prev = [], None
    for t, v in s['rows']:
        q = quat(v, s['cols'])
        if prev and t > prev[0]:
            dot = min(1.0, abs(sum(a * b for a, b in zip(q, prev[1]))))
            out.append((t, math.degrees(2 * math.acos(dot)) / (t - prev[0])))
        prev = (t, q)
    return out


def head_events(speeds):
    """Stretches above HEAD_MOVE_DEG_S, merged across short dips, minus jitter-length bursts."""
    spans = []
    for t, w in speeds:
        if w < HEAD_MOVE_DEG_S:
            continue
        if spans and t - spans[-1][1] <= HEAD_MOVE_MERGE_S:
            spans[-1][1], spans[-1][2] = t, max(spans[-1][2], w)
        else:
            spans.append([t, t, w])
    dt = (speeds[-1][0] - speeds[0][0]) / max(1, len(speeds) - 1) if speeds else 0
    return [{'onset': a - dt, 'duration_s': b - a + dt, 'kind': 'head_move', 'source': 'motion',
             'strength': round(w, 1)} for a, b, w in spans if b - a + dt >= HEAD_MOVE_MIN_S]


def events_table(session):
    ev = action_events(session) + head_events(angular_speed(session))
    ev.sort(key=lambda e: e['onset'])
    return ev


def bad_spans(session):
    """(onset, duration, label) for poor contact and gaps in band power."""
    out = []
    eq = session.get('eq')
    if eq and eq['cols'] and 'overall' in eq['cols']:
        i, start = eq['cols'].index('overall'), None
        for t, v in eq['rows'] + [(None, None)]:
            bad = v is not None and isinstance(v[i], (int, float)) and v[i] < CONTACT_MIN
            if bad and start is None:
                start = t
            elif not bad and start is not None:
                out.append((start, (t or eq['rows'][-1][0]) - start, 'BAD_contact'))
                start = None
    pw = session.get('pow')
    if pw:
        for (a, _), (b, _) in zip(pw['rows'], pw['rows'][1:]):
            if b - a > GAP_S:
                out.append((a, b - a, 'BAD_gap'))
    return out


def write_events(path, events, t0):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['onset_epoch', 'offset_s', 'duration_s', 'kind', 'source', 'strength'])
        for e in events:
            w.writerow([f"{e['onset']:.4f}", f"{e['onset'] - t0:.3f}", f"{e['duration_s']:.3f}",
                        e['kind'], e['source'], e['strength']])



def hold(rows, idx, grid):
    """Zero-order hold of rows[i][1][idx] onto grid times; NaN before the first sample."""
    import numpy as np
    times = np.array([r[0] for r in rows])
    vals = np.array([[r[1][i] if isinstance(r[1][i], (int, float)) and not isinstance(r[1][i], bool)
                      else np.nan for i in idx] for r in rows], dtype=float)
    pos = np.searchsorted(times, grid, side='right') - 1
    out = np.where(pos[:, None] >= 0, vals[np.clip(pos, 0, None)], np.nan)
    return out.T


def annotations(mne, events, bads, t0):
    a = mne.Annotations(onset=[], duration=[], description=[])
    for e in events:
        a.append(e['onset'] - t0, max(e['duration_s'], 0.0), e['kind'])
    for onset, dur, label in bads:
        a.append(onset - t0, dur, label)
    return a


def write_fifs(session, events, outdir, t0):
    import mne
    import numpy as np
    mne.set_log_level('WARNING')
    t1 = max(s['rows'][-1][0] for s in session.values() if s['rows'])
    grid = np.arange(t0, t1, 1 / GRID_HZ)
    names, data = [], []
    for stream in ('pow', 'mot', 'eq', 'met'):
        s = session.get(stream)
        if not s or not s['rows'] or not s['cols']:
            continue
        idx = [i for i, c in enumerate(s['cols']) if isinstance(c, str) and not c.endswith('isActive')
               and not c.startswith(('COUNTER', 'INTERPOLATED'))]
        names += [f'{stream}/{s["cols"][i]}' for i in idx]
        data.append(hold(s['rows'], idx, grid))
    speeds = angular_speed(session)
    if speeds:
        names.append('mot/head_deg_s')
        data.append(hold([(t, [w]) for t, w in speeds], [0], grid))
    written = []
    if data:
        x = np.nan_to_num(np.vstack(data))
        info = mne.create_info(names, GRID_HZ, 'misc')
        raw = mne.io.RawArray(x, info)
        raw.set_meas_date(t0)
        raw.set_annotations(annotations(mne, events, bad_spans(session), t0))
        raw.save(outdir / 'cleaned_raw.fif', overwrite=True)
        written.append('cleaned_raw.fif')
    eeg = session.get('eeg')
    if eeg and eeg['rows'] and eeg['cols']:
        idx = [eeg['cols'].index(c) for c in SENSORS if c in eeg['cols']]
        times = np.array([r[0] for r in eeg['rows']])
        sfreq = round((len(times) - 1) / (times[-1] - times[0]))
        x = np.array([[r[1][i] for i in idx] for r in eeg['rows']], dtype=float).T * 1e-6  # uV -> V
        raw = mne.io.RawArray(x, mne.create_info([eeg['cols'][i] for i in idx], sfreq, 'eeg'))
        raw.set_meas_date(times[0])
        raw.filter(*EEG_BAND)
        raw.notch_filter(LINE_HZ)
        raw.set_annotations(annotations(mne, events, bad_spans(session), times[0]))
        raw.save(outdir / 'eeg_raw.fif', overwrite=True)
        written.append('eeg_raw.fif')
    return written



def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('session')
    ap.add_argument('--out', default=os.path.expanduser('~/eeg/review'))
    args = ap.parse_args(argv)

    src = Path(args.session)
    if not src.is_dir():
        src = fetch_from_nas(args.session, Path(args.out) / '.fetch')
    identity = identity_from_sops() if any(f.name.endswith('.age') for f in src.iterdir()) else None
    session = load_session(src, identity)
    if not any(s['rows'] for s in session.values()):
        raise SystemExit(f'{src}: no samples')
    outdir = Path(args.out) / src.name
    outdir.mkdir(parents=True, exist_ok=True)

    summary = summarize(session)
    t0 = min(v['first'] for v in summary.values())
    events = events_table(session)
    write_events(outdir / 'events.csv', events, t0)
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    written = write_fifs(session, events, outdir, t0)

    for name, v in summary.items():
        print(f"{name:4} {v['samples']:7d} samples  {v['span_s']:9.1f} s  {v['rate_hz'] or 0:7.2f} Hz")
    kinds = {}
    for e in events:
        kinds[e['kind']] = kinds.get(e['kind'], 0) + 1
    print('events:', ', '.join(f'{k} {n}' for k, n in sorted(kinds.items())) or 'none')
    print('wrote', outdir, ' '.join(['events.csv', 'summary.json'] + written))


if __name__ == '__main__':
    sys.exit(main())
