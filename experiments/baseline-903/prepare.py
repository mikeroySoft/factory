"""Time the producer's shared-clone/copytree preparation outside baseline timing."""
import importlib.util
import json
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('preparation_measurement', root / 'scripts/mutation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rows = []
usage = resource.getrusage(resource.RUSAGE_SELF)
with tempfile.TemporaryDirectory(prefix='factory-903-preparation-') as scratch:
    for index in range(16):
        destination = Path(scratch) / str(index)
        started = time.monotonic()
        subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout', str(root), str(destination)], check=True)
        cloned = time.monotonic()
        shutil.copytree(root, destination, ignore=module.COPY_IGNORE, dirs_exist_ok=True)
        copied = time.monotonic()
        rows.append({'worker': index, 'clone_seconds': cloned - started, 'copy_seconds': copied - cloned})
    after = resource.getrusage(resource.RUSAGE_SELF)
    print(json.dumps({'workers': rows, 'parent_input_blocks': after.ru_inblock - usage.ru_inblock,
                      'parent_output_blocks': after.ru_oublock - usage.ru_oublock,
                      'note': 'Warm-cache preparation only; no sampling, proof exclusion or fingerprint validation. Outside reported baseline timer.'}, indent=2))
