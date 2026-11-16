#!/usr/bin/env python3
"""Check notification destinations with Alertmanager's own matcher implementation."""
import pathlib
import subprocess
import tempfile
from urllib.parse import urlparse

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
config = yaml.safe_load((ROOT / 'apps/monitoring/helm-values.yaml').read_text())['alertmanager']['config']
cases = yaml.safe_load((ROOT / 'apps/monitoring/tests/alertmanager-routes.yaml').read_text())
receivers = {r['name']: r for r in config['receivers']}

with tempfile.TemporaryDirectory() as tmp:
    # The offsite URL is a mounted secret in production; no notification is sent here.
    secret = pathlib.Path(tmp) / 'offsite-url'
    secret.write_text('http://localhost/offsite')
    for receiver in receivers.values():
        for webhook in receiver.get('webhook_configs', []):
            if 'url_file' in webhook:
                webhook['url_file'] = str(secret)
    path = pathlib.Path(tmp) / 'alertmanager.yaml'
    path.write_text(yaml.safe_dump(config))
    subprocess.run(['amtool', 'check-config', str(path)], check=True)
    for case in cases:
        subprocess.run(['amtool', 'config', 'routes', 'test', '--config.file=' + str(path),
                        '--verify.receivers=' + case['receiver'], *case['labels']], check=True, capture_output=True)
        if 'topic' in case:
            for webhook in receivers[case['receiver']]['webhook_configs']:
                if 'url' in webhook:
                    assert urlparse(webhook['url']).path == '/' + case['topic'], case
    print(f'{len(cases)} notification route cases passed')
