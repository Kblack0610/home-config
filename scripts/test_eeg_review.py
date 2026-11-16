"""Offline tests for scripts/eeg-review.py: chunk loading, decrypt, event detection. Stdlib only;
the FIF step needs mne and runs through `uv run scripts/eeg-review.py` instead."""
import gzip
import importlib.util
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('eeg_review', Path(__file__).with_name('eeg-review.py'))
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)

FAC = ['eyeAct', 'uAct', 'uPow', 'lAct', 'lPow']
MOT = ['COUNTER_MEMS', 'INTERPOLATED_MEMS', 'Q0', 'Q1', 'Q2', 'Q3', 'ACCX', 'ACCY', 'ACCZ', 'MAGX', 'MAGY', 'MAGZ']


def write_chunk(d, stream, n, cols, rows):
    lines = [json.dumps({'stream': stream, 'cols': cols, 'session': 'abc', 'headset': 'EPOCX', 'chunk': n})]
    lines += [json.dumps({'time': t, stream: v}) for t, v in rows]
    (d / f'{stream}-{n:04d}.jsonl.gz').write_bytes(gzip.compress(('\n'.join(lines) + '\n').encode()))


def mot_row(t, yaw_deg):
    h = math.radians(yaw_deg) / 2
    return (t, [0, 0, math.cos(h), 0, 0, math.sin(h), 1, 0, 0, 0, 0, 0])


def fac_rows(t0, pattern, hz=32):
    return [(t0 + i / hz, v) for i, v in enumerate(pattern)]


class Loading(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_chunks_join_in_time_order_with_header_cols(self):
        write_chunk(self.d, 'com', 2, ['act', 'pow'], [(110.0, ['push', 0.5])])
        write_chunk(self.d, 'com', 1, ['act', 'pow'], [(100.0, ['neutral', 0]), (101.0, ['neutral', 0])])
        s = er.load_session(self.d)
        self.assertEqual(s['com']['cols'], ['act', 'pow'])
        self.assertEqual([t for t, _ in s['com']['rows']], [100.0, 101.0, 110.0])
        self.assertEqual(er.summarize(s)['com']['samples'], 3)

    def test_p1_capture_takes_cols_from_cols_json(self):
        (self.d / 'cols.json').write_text(json.dumps({'com': ['act', 'pow']}))
        (self.d / 'com.jsonl').write_text(json.dumps({'time': 1.0, 'com': ['lift', 0.9]}) + '\n')
        s = er.load_session(self.d)
        self.assertEqual(s['com']['cols'], ['act', 'pow'])
        self.assertEqual(er.action_events(s)[0]['kind'], 'lift')

    @unittest.skipUnless(shutil.which('age') and shutil.which('age-keygen'), 'age not installed')
    def test_age_chunk_decrypts_in_memory_and_wrong_key_fails(self):
        key = subprocess.run(['age-keygen'], capture_output=True, check=True).stdout
        other = subprocess.run(['age-keygen'], capture_output=True, check=True).stdout
        recipient = subprocess.run(['age-keygen', '-y'], input=key, capture_output=True, check=True).stdout.decode().strip()
        write_chunk(self.d, 'com', 1, ['act', 'pow'], [(1.0, ['neutral', 0])])
        plain = self.d / 'com-0001.jsonl.gz'
        subprocess.run(['age', '-r', recipient, '-o', f'{plain}.age', str(plain)], check=True)
        plain.unlink()
        self.assertEqual(er.load_session(self.d, key)['com']['rows'], [(1.0, ['neutral', 0])])
        with self.assertRaises(SystemExit):
            er.load_session(self.d, other)
        with self.assertRaises(SystemExit):
            er.load_session(self.d)


class Events(unittest.TestCase):
    def session(self, fac=(), mot=(), eq=(), pow_=()):
        return {'fac': {'cols': FAC, 'rows': list(fac)}, 'mot': {'cols': MOT, 'rows': list(mot)},
                'eq': {'cols': ['batteryPercent', 'overall'], 'rows': list(eq)},
                'pow': {'cols': ['AF3/theta'], 'rows': list(pow_)}}

    def test_blink_and_clench_become_one_event_each_with_peak_strength(self):
        n = ['neutral', 'neutral', 0, 'neutral', 0]
        pattern = [n] * 5 + [['blink', 'neutral', 0, 'neutral', 0]] * 3 + [n] * 5 \
            + [['neutral', 'neutral', 0, 'clench', p] for p in (0.2, 0.9, 0.4)] + [n] * 5
        ev = er.action_events(self.session(fac=fac_rows(1000.0, pattern)))
        self.assertEqual([(e['kind'], e['source']) for e in ev], [('blink', 'eye'), ('clench', 'lower')])
        self.assertAlmostEqual(ev[0]['onset'], 1000.0 + 5 / 32)
        self.assertEqual(ev[1]['strength'], 0.9)

    def test_all_neutral_gives_no_events(self):
        rows = fac_rows(0.0, [['neutral', 'neutral', 0, 'neutral', 0]] * 50)
        self.assertEqual(er.action_events(self.session(fac=rows)), [])

    def test_head_turn_is_one_movement_with_its_peak_speed(self):
        rows = [mot_row(i / 32, 0) for i in range(32)]                       # still 1 s
        rows += [mot_row(1 + i / 32, 90 * (i + 1) / 32) for i in range(16)]  # 90 deg/s for 0.5 s
        rows += [mot_row(1.5 + i / 32, 45) for i in range(32)]               # still 1 s
        ev = er.head_events(er.angular_speed(self.session(mot=rows)))
        self.assertEqual(len(ev), 1)
        self.assertAlmostEqual(ev[0]['strength'], 90, delta=1)
        self.assertAlmostEqual(ev[0]['duration_s'], 0.5, delta=0.05)

    def test_slow_drift_and_single_spike_are_not_movements(self):
        drift = [mot_row(i / 32, 10 * i / 32) for i in range(64)]            # 10 deg/s
        self.assertEqual(er.head_events(er.angular_speed(self.session(mot=drift))), [])
        spike = [mot_row(i / 32, 0) for i in range(32)] + [mot_row(1 + i / 32, 3) for i in range(32)]
        self.assertEqual(er.head_events(er.angular_speed(self.session(mot=spike))), [])

    def test_poor_contact_and_pow_gaps_are_bad_spans(self):
        eq = [(0.0, [80, 90]), (1.0, [80, 20]), (2.0, [80, 30]), (3.0, [80, 95])]
        pw = [(t / 8, [0.1]) for t in range(8)] + [(5.0, [0.1])]
        self.assertEqual(er.bad_spans(self.session(eq=eq, pow_=pw)),
                         [(1.0, 2.0, 'BAD_contact'), (7 / 8, 5.0 - 7 / 8, 'BAD_gap')])


if __name__ == '__main__':
    unittest.main()
