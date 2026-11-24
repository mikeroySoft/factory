"""Microbenchmarks for Ultron vs Tank attribution: CPU, startup, git, filesystem."""
import json
import os
import subprocess
import sys
import tempfile
import time


def bench_cpu_pystone(loops=500000, repeats=5):
    """Pure-Python CPU benchmark: inlined pystone-equivalent workload."""
    results = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        # Dhrystone-like mixed workload
        int_glob = 0
        str_1 = 'DHRYSTONE PROGRAM, 1ST STRING'
        str_2 = 'DHRYSTONE PROGRAM, 2ND STRING'
        for i in range(loops):
            int_glob = (int_glob + i) % 65536
            _ = str_1 == str_2  # noqa: F841
            _ = str_1 < str_2   # noqa: F841
            _ = str_1[10:16]    # noqa: F841
        elapsed = time.perf_counter() - t0
        results.append({'duration': elapsed, 'loops': loops,
                        'loops_per_sec': loops / elapsed})
    return results


def bench_cpu_compute(repeats=5):
    """Pure-Python compute: dict/list/math workload, no I/O."""
    import hashlib
    import math
    results = []
    for _ in range(repeats):
        t0 = time.monotonic()
        # Mixed workload: hashing, math, dict ops, list comprehensions
        d = {}
        for i in range(200000):
            d[i] = hashlib.sha256(str(i).encode()).hexdigest()
        total = sum(math.sqrt(int(v[:8], 16)) for v in d.values() if int(v[0], 16) > 7)  # noqa: F841
        s = sorted(d.values())  # noqa: F841
        elapsed = time.monotonic() - t0
        results.append({'duration': elapsed})
    return results


def bench_cpu_loop(n=20_000_000, repeats=3):
    """Tight arithmetic loop — measures raw bytecode dispatch speed."""
    results = []
    for _ in range(repeats):
        t0 = time.monotonic()
        x = 0
        for i in range(n):
            x += i * i
        elapsed = time.monotonic() - t0
        results.append({'duration': elapsed, 'result': x})
    return results


def bench_startup(python, n=200):
    """Measure Python interpreter startup: `python -c pass`."""
    # Warmup
    for _ in range(5):
        subprocess.run([python, '-I', '-c', 'pass'], capture_output=True, check=False)
    results = []
    t0 = time.monotonic()
    for _ in range(n):
        subprocess.run([python, '-I', '-c', 'pass'], capture_output=True, check=False)
    total = time.monotonic() - t0
    per_launch = total / n
    results.append({'total': total, 'n': n, 'per_launch': per_launch})
    return results


def bench_git(repo_path, n=500):
    """Measure git subprocess operations in a repo."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    results = {}

    # git rev-parse HEAD
    times = []
    for _ in range(n):
        t0 = time.monotonic()
        subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=repo_path, capture_output=True, check=False, env=env,
        )
        times.append(time.monotonic() - t0)
    results['rev-parse'] = {
        'total': sum(times), 'n': n,
        'per_call': sum(times) / n,
        'min': min(times), 'max': max(times),
    }

    # git log --oneline -1
    times = []
    for _ in range(n):
        t0 = time.monotonic()
        subprocess.run(
            ['git', 'log', '--oneline', '-1'],
            cwd=repo_path, capture_output=True, check=False, env=env,
        )
        times.append(time.monotonic() - t0)
    results['log-1'] = {
        'total': sum(times), 'n': n,
        'per_call': sum(times) / n,
        'min': min(times), 'max': max(times),
    }

    # git diff --quiet (no changes expected in clean tree)
    times = []
    for _ in range(n):
        t0 = time.monotonic()
        subprocess.run(
            ['git', 'diff', '--quiet'],
            cwd=repo_path, capture_output=True, check=False, env=env,
        )
        times.append(time.monotonic() - t0)
    results['diff-quiet'] = {
        'total': sum(times), 'n': n,
        'per_call': sum(times) / n,
        'min': min(times), 'max': max(times),
    }

    # git status --porcelain
    times = []
    for _ in range(n):
        t0 = time.monotonic()
        subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=repo_path, capture_output=True, check=False, env=env,
        )
        times.append(time.monotonic() - t0)
    results['status'] = {
        'total': sum(times), 'n': n,
        'per_call': sum(times) / n,
        'min': min(times), 'max': max(times),
    }

    return results


def bench_filesystem(base_dir, n_files=2000, repeats=3):
    """Measure filesystem I/O: create, read, stat, delete small files."""
    results = []
    for _ in range(repeats):
        d = tempfile.mkdtemp(dir=base_dir)
        trial = {}

        # Create files
        t0 = time.monotonic()
        for i in range(n_files):
            with open(os.path.join(d, f'f{i}.txt'), 'w') as f:
                f.write(f'content-{i}\n' * 10)
        trial['create'] = time.monotonic() - t0

        # Stat files
        t0 = time.monotonic()
        for i in range(n_files):
            os.stat(os.path.join(d, f'f{i}.txt'))
        trial['stat'] = time.monotonic() - t0

        # Read files
        t0 = time.monotonic()
        for i in range(n_files):
            with open(os.path.join(d, f'f{i}.txt')) as f:
                f.read()
        trial['read'] = time.monotonic() - t0

        # List directory
        t0 = time.monotonic()
        for _ in range(20):
            os.listdir(d)
        trial['listdir_x20'] = time.monotonic() - t0

        # Delete files
        t0 = time.monotonic()
        for i in range(n_files):
            os.unlink(os.path.join(d, f'f{i}.txt'))
        trial['delete'] = time.monotonic() - t0

        os.rmdir(d)
        trial['n_files'] = n_files
        results.append(trial)
    return results


def bench_process_launch(n=500):
    """Measure raw process launch cost: /usr/bin/true."""
    true_path = '/usr/bin/true'
    if not os.path.exists(true_path):
        true_path = '/bin/true'
    # Warmup
    for _ in range(10):
        subprocess.run([true_path], capture_output=True, check=False)
    times = []
    t0_all = time.monotonic()
    for _ in range(n):
        t0 = time.monotonic()
        subprocess.run([true_path], capture_output=True, check=False)
        times.append(time.monotonic() - t0)
    total = time.monotonic() - t0_all
    return {
        'total': total, 'n': n,
        'per_launch': total / n,
        'min': min(times), 'max': max(times),
    }


def main():
    import platform
    python = sys.executable
    repo_path = sys.argv[1] if len(sys.argv) > 1 else '.'
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    result = {
        'hostname': platform.node(),
        'platform': platform.platform(),
        'python': sys.version,
        'python_path': python,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    }

    print('CPU pystone...', flush=True)
    result['pystone'] = bench_cpu_pystone()

    print('CPU compute...', flush=True)
    result['compute'] = bench_cpu_compute()

    print('CPU loop...', flush=True)
    result['loop'] = bench_cpu_loop()

    print('Python startup...', flush=True)
    result['startup'] = bench_startup(python)

    print('Process launch (/usr/bin/true)...', flush=True)
    result['process_launch'] = bench_process_launch()

    print('Git operations...', flush=True)
    result['git'] = bench_git(repo_path)

    # Use repo's tmp or system tmp for filesystem bench
    tmp_base = os.environ.get('TMPDIR', tempfile.gettempdir())
    print(f'Filesystem I/O ({tmp_base})...', flush=True)
    result['filesystem'] = bench_filesystem(tmp_base)

    out = json.dumps(result, indent=2) + '\n'
    if output_path:
        with open(output_path, 'w') as f:
            f.write(out)
        print(f'Written to {output_path}')
    else:
        print(out)


if __name__ == '__main__':
    main()
