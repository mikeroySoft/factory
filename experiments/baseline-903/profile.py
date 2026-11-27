"""Read-only parent-process baseline profile; no subprocess argument contents saved."""
import cProfile
import importlib.util
import json
import os
import pstats
import subprocess
import sys
import time
from pathlib import Path

clock = time.monotonic


def main():
    root, packages, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(root), str(packages)]
    os.chdir(root)
    original = subprocess.Popen
    records = []

    class Measured(original):
        def __init__(self, args, *a, **kw):
            self.measure_start = clock()
            self.measure_record = {'program': Path(os.fsdecode(args[0])).name if isinstance(args, (list, tuple)) else 'shell'}
            super().__init__(args, *a, **kw)
            self.measure_record['launch_seconds'] = clock() - self.measure_start
            records.append(self.measure_record)

        def wait(self, *a, **kw):
            result = super().wait(*a, **kw)
            self.measure_record['lifetime_seconds'] = clock() - self.measure_start
            self.measure_record['returncode'] = result
            return result

    subprocess.Popen = Measured
    spec = importlib.util.spec_from_file_location('baseline_measurement', root / 'scripts/mutation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    profile = cProfile.Profile()
    started = clock()
    try:
        code = profile.runcall(module.run_tests, '')
    finally:
        with (output / 'profile.txt').open('w') as stream:
            pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats()
        (output / 'subprocess.json').write_text(json.dumps(records, indent=2) + '\n')
        (output / 'result.json').write_text(json.dumps({'seconds': clock() - started, 'returncode': locals().get('code'), 'python': sys.version, 'source': str(root)}, indent=2) + '\n')
    return code

if __name__ == '__main__':
    sys.exit(main())
