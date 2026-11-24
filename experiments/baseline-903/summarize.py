"""Summarize captured subprocess observations without adding overlapping lifetimes."""
import collections
import json
from pathlib import Path
import sys

for directory in map(Path, sys.argv[1:]):
    groups = collections.defaultdict(list)
    for row in json.loads((directory / 'subprocess.json').read_text()):
        groups[row['program']].append(row)
    result = {
        program: {
            'count': len(rows),
            'launch_seconds': sum(row['launch_seconds'] for row in rows),
            'inclusive_lifetime_seconds': sum(row.get('lifetime_seconds', 0) for row in rows),
        }
        for program, rows in sorted(groups.items())
    }
    (directory / 'subprocess-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(directory, json.dumps(result))
