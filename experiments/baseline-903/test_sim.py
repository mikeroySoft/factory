"""Simulate test-suite workload: tempfile + git + subprocess patterns."""
import json
import os
import subprocess
import sys
import tempfile
import time


def simulate_test_workload(repo_path, n_iterations=50):
    """Simulate what a typical test does: create temp dirs, run git, write files."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    results = []

    for i in range(n_iterations):
        t0 = time.monotonic()

        # 1. Create a temp directory (like test setUp)
        td = tempfile.mkdtemp()

        # 2. Write some config/fixture files
        for j in range(10):
            with open(os.path.join(td, f'fixture_{j}.txt'), 'w') as f:
                f.write(f'[section]\nkey = value{j}\npath = /tmp/test\n' * 5)

        # 3. Run git commands (common in factory tests)
        subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo_path,
                       capture_output=True, check=False, env=env)
        subprocess.run(['git', 'log', '--oneline', '-5'], cwd=repo_path,
                       capture_output=True, check=False, env=env)
        subprocess.run(['git', 'diff', '--quiet'], cwd=repo_path,
                       capture_output=True, check=False, env=env)

        # 4. Read files back
        for j in range(10):
            with open(os.path.join(td, f'fixture_{j}.txt')) as f:
                f.read()

        # 5. Some Python computation (parsing, dict ops)
        d = {}
        for j in range(5000):
            d[f'key_{j}'] = {'value': j, 'name': f'test_{j}', 'active': j % 2 == 0}
        filtered = {k: v for k, v in d.items() if v['active']}  # noqa: F841

        # 6. Clean up temp dir
        for j in range(10):
            os.unlink(os.path.join(td, f'fixture_{j}.txt'))
        os.rmdir(td)

        results.append(time.monotonic() - t0)

    return {
        'n': n_iterations,
        'total': sum(results),
        'per_iteration': sum(results) / n_iterations,
        'min': min(results),
        'max': max(results),
        'median': sorted(results)[len(results) // 2],
    }


def main():
    import platform
    repo = sys.argv[1] if len(sys.argv) > 1 else '.'
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    result = {
        'hostname': platform.node(),
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    }

    print('Running test simulation...', flush=True)
    result['simulation'] = simulate_test_workload(repo)
    print(f'  {result["simulation"]["n"]} iterations: '
          f'{result["simulation"]["total"]:.3f}s total, '
          f'{result["simulation"]["per_iteration"]*1000:.1f}ms/iter')

    out = json.dumps(result, indent=2) + '\n'
    if out_path:
        with open(out_path, 'w') as f:
            f.write(out)
    else:
        print(out)


if __name__ == '__main__':
    main()
