#!/usr/bin/env python3
# This file is part of MIDNIGHT-ZK.
# Copyright (C) Midnight Foundation
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('--out', required=True, type=Path)
p.add_argument('--bin-dir', required=True, type=Path)
p.add_argument('--cache', type=Path)
p.add_argument('--sizes', nargs='+', type=int, default=[14, 16, 18, 20, 21])
p.add_argument('--threads', type=int, default=24)
p.add_argument('--warm', type=int, default=5)
p.add_argument('--repeats', type=int, choices=[1, 2, 3], default=3)
p.add_argument('--max-load', type=float, default=8)
a = p.parse_args()
a.out = a.out.resolve()
a.bin_dir = a.bin_dir.resolve()
a.out.mkdir(parents=True, exist_ok=True)
raw = a.out / 'raw'
raw.mkdir(exist_ok=False)
env = os.environ.copy()
env.update(RAYON_NUM_THREADS=str(a.threads), RC1_WARM=str(a.warm),
           RC1_CACHE=str(a.cache.resolve() if a.cache else a.out / 'test-key-cache'),
           RC1_MAX_LOAD=str(a.max_load), RC1_STOCK_BIN=str(a.bin_dir / 'rc1-cpu'))
for key in list(env):
    if key.startswith('MIDNIGHT_METAL_'):
        del env[key]

def capture(cmd):
    return subprocess.check_output(cmd, text=True).strip()

meta = {
    'started_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
    'prover_commit': capture(['git', 'rev-parse', 'HEAD']),
    'chip': capture(['sysctl', '-n', 'machdep.cpu.brand_string']),
    'memory_bytes': int(capture(['sysctl', '-n', 'hw.memsize'])),
    'logical_cpus': int(capture(['sysctl', '-n', 'hw.logicalcpu'])),
    'macos': capture(['sw_vers', '-productVersion']),
    'threads': a.threads, 'warm_per_process': a.warm, 'repeats': a.repeats,
    'sizes': a.sizes, 'max_start_load1': a.max_load,
    'shader_path': 'source compiled at first use (no offline Metal compiler)',
    'binary_sha256': {m: hashlib.sha256((a.bin_dir / ('rc1-' + m)).read_bytes()).hexdigest()
                      for m in ['cpu', 'metal']},
    'notes': ['Public deterministic test SRS; no real user witnesses.',
              'The CPU build has no gpu feature. CPU and Metal share each key cache.',
              'Default Metal thresholds and CPU/GPU work sharing are preserved.',
              'Setup, key loading, device registration, cleanup and verification are outside prove_s.',
              'Metal shader initialization happens before proof 0.',
              'Desktop workload, no thermal or energy measurements; exploratory measurements.'],
}
(a.out / 'environment.json').write_text(json.dumps(meta, indent=2) + '\n')

def run(cmd, env, stdout, stderr, timeout=900):
    with stderr.open('w') as err:
        result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=err,
                                text=True, timeout=timeout)
    stdout.write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f'{cmd[-1]} failed ({result.returncode}); see {stderr}')
    return result.stdout

for k in a.sizes:
    e = dict(env, RC1_K=str(k))
    print(f'prepare k={k}', flush=True)
    run([str(a.bin_dir / 'rc1-cpu'), 'prepare'], e,
        a.out / f'prepare-k{k}.out', a.out / f'prepare-k{k}.log')

all_rows = []
for k in a.sizes:
    for rep in range(1, a.repeats + 1):
        modes = ['cpu', 'metal'] if rep % 2 else ['metal', 'cpu']
        for mode in modes:
            stem = f'k{k}-rep{rep}-{mode}'
            e = dict(env, RC1_K=str(k), RC1_REP=str(rep))
            print(f'start {stem}, load={os.getloadavg()[0]:.2f}', flush=True)
            event = {'run': stem, 'start_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                     'start_loadavg': os.getloadavg()}
            top = subprocess.run(['top', '-l', '2', '-n', '0', '-s', '1'],
                                 text=True, capture_output=True, timeout=10)
            event['cpu_usage_before'] = [s for s in top.stdout.splitlines() if s.startswith('CPU usage:')][-1:]
            start = time.monotonic()
            output = run([str(a.bin_dir / ('rc1-' + mode)), 'run'], e,
                         raw / (stem + '.tsv'), raw / (stem + '.stderr'))
            rows = list(csv.DictReader(io.StringIO(output), delimiter='\t'))
            if len(rows) != a.warm + 1:
                raise RuntimeError(f'{stem}: incomplete cold/warm sequence')
            for i, row in enumerate(rows):
                expected = {'mode': 'gpu' if mode == 'metal' else 'cpu', 'rep': str(rep),
                            'k': str(k), 'threads': str(a.threads), 'proof': str(i),
                            'state': 'cold' if i == 0 else 'warm', 'verified': 'true'}
                if any(row[key] != value for key, value in expected.items()):
                    raise RuntimeError(f'{stem}: invalid identity or verification fields')
                for key in ['prove_s', 'verify_s', 'start_load1']:
                    if not math.isfinite(float(row[key])) or float(row[key]) < 0:
                        raise RuntimeError(f'{stem}: invalid {key}')
                if float(row['start_load1']) >= a.max_load:
                    raise RuntimeError(f'{stem}: quiet gate was not respected')
                if mode == 'metal':
                    if int(row['device_errors']) or any(int(row[c]) <= 0 for c in ['msm_calls', 'quotient_calls']):
                        raise RuntimeError(f'{stem}: device error or missing GPU phase')
                    declines = json.loads(row['declines'])
                    if any(reason != 'below-min-k' for _, reason, _ in declines):
                        raise RuntimeError(f'{stem}: unexpected device decline: {declines}')
                    if int(row['ntt_calls']) == 0 and not any(c in ['fft', 'ifft'] and n > 0 for c, _, n in declines):
                        raise RuntimeError(f'{stem}: missing transforms without a threshold decline')
                all_rows.append(dict(row, backend=mode))
            event.update(elapsed_s=time.monotonic()-start, end_loadavg=os.getloadavg())
            with (a.out / 'events.jsonl').open('a') as f:
                f.write(json.dumps(event) + '\n')
            print(f'passed {stem}: warm median {statistics.median(float(r["prove_s"]) for r in rows[1:]):.6f}s', flush=True)

fields = list(all_rows[0])
with (a.out / 'all-rows.tsv').open('w') as f:
    writer = csv.DictWriter(f, fields, delimiter='\t', lineterminator='\n')
    writer.writeheader()
    writer.writerows(all_rows)

summary = []
for k in a.sizes:
    item = {'k': k}
    for mode in ['cpu', 'metal']:
        selected = [r for r in all_rows if int(r['k']) == k and r['backend'] == mode]
        warm = [float(r['prove_s']) for r in selected if r['state'] == 'warm']
        cold = [float(r['prove_s']) for r in selected if r['state'] == 'cold']
        item.update({f'{mode}_warm_median_s': statistics.median(warm),
                     f'{mode}_warm_min_s': min(warm), f'{mode}_warm_max_s': max(warm),
                     f'{mode}_cold_median_s': statistics.median(cold)})
        if mode == 'metal':
            item['metal_peak_bytes'] = max(int(r['peak_device_bytes']) for r in selected)
            item['metal_fallback_counts'] = sorted({int(r['fallbacks']) for r in selected})
    item['speedup'] = item['cpu_warm_median_s'] / item['metal_warm_median_s']
    summary.append(item)
(a.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
meta.update(finished_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
            verified_proofs=len(all_rows))
(a.out / 'environment.json').write_text(json.dumps(meta, indent=2) + '\n')
print(json.dumps(summary, indent=2), flush=True)
