"""Compare a profiled mutation baseline with a real concurrent full-suite gate."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def main():
    baseline, gate_tree, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=True)
    python = '/private/tmp/factory-899-mutenv.iGXmKr/bin/python'
    packages = '/private/tmp/factory-899-mutenv.iGXmKr/lib/python3.12/site-packages'
    script = Path(__file__).with_name('profile.py').resolve()
    envs = []
    for name in ('baseline', 'gate'):
        home = output / (name + '-home')
        (home / 'config').mkdir(parents=True, exist_ok=True)
        envs.append(dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                         UV_CACHE_DIR='/Users/mike/.cache/uv',
                         UV_PYTHON='/Users/mike/.local/share/uv/tools/factory/bin/python'))
    envs[1]['UV_PROJECT_ENVIRONMENT'] = str(gate_tree / '.venv')
    envs[1]['UV_NO_EDITABLE'] = '1'
    started = time.monotonic()
    with (output / 'baseline.log').open('w') as log:
        child = subprocess.Popen([python, '-I', '-B', str(script), str(baseline), packages,
                                  str(output / 'baseline')], env=envs[0], stdout=log, stderr=log)
        # Historical check entries differ by 337.038 seconds. Preserve that phase offset.
        threading.Event().wait(337.038)
        gate_started = time.monotonic() - started
        with (output / 'gate.log').open('w') as gate_log:
            gate = subprocess.Popen([python, '-m', 'factory', 'gate', '--base', 'HEAD',
                                     '--skip', 'ruff', '--report', str(output / 'gate-report.md')],
                                    cwd=gate_tree, env=envs[1], stdout=gate_log, stderr=gate_log)
            code = child.wait()
            baseline_done = time.monotonic() - started
            gate_code = gate.wait()
    (output / 'result.json').write_text(json.dumps({
        'baseline_code': code, 'gate_code': gate_code, 'gate_start_offset': gate_started,
        'baseline_done': baseline_done, 'both_done': time.monotonic() - started,
        'note': 'Real full-suite gate load; no mutant saturation. Phase offset matches historical check entries, not baseline-specific receipts.'
    }, indent=2) + '\n')
    return code or gate_code


if __name__ == '__main__':
    sys.exit(main())
