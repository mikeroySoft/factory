from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path


class MutationAnnotationsTest(unittest.TestCase):
    def sample_numbers(self, future):
        source = f"from __future__ import {future}\n" + '''é: Literal[1] = 2
class Example:
    value: Literal[3] = 4
async def answer(a: Literal[5] = 6, /, *args: Literal[7], key: Literal[8] = 9,
                 **kwargs: Literal[10]) -> tuple[
                     Literal[11], Literal[12]
                 ]:
    local: Literal[13] = 14
    return 15
'''
        positions = {int(token.string): token.start
                     for token in tokenize.generate_tokens(io.StringIO(source).readline)
                     if token.type == tokenize.NUMBER}
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="mutation-annotations-") as directory:
            root = Path(directory)
            module = root / "example.py"
            module.write_text(source)
            script = (
                "import json,sys; from pathlib import Path; from scripts import mutation; "
                "path = sys.argv[1]; "
                "mutants, _ = mutation.sample({path: set(range(1, 20))}, Path(sys.argv[2])); "
                "print(json.dumps([m.start_pos for m in mutants "
                "if m.operator_name == 'core/NumberReplacer']))"
            )
            result = subprocess.run(
                ["uv", "run", "--project", str(repository), "--frozen", "--no-managed-python",
                 "--python", sys.executable, "--group", "mutation", "python", "-c", script,
                 str(module), str(root)],
                cwd=repository,
                env={key: value for key, value in os.environ.items()
                     if key not in {"UV_NO_SYNC", "VIRTUAL_ENV"}} | {
                         "UV_PROJECT_ENVIRONMENT": str(root / "venv"), "UV_OFFLINE": "1",
                         "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root)},
                capture_output=True, text=True, timeout=90, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return {tuple(position) for position in json.loads(result.stdout)}, positions

    def test_future_annotations_skip_annotation_mutants(self):
        sampled, positions = self.sample_numbers("annotations")
        self.assertEqual(sampled, {positions[value] for value in (2, 4, 6, 9, 14, 15)})

    def test_without_future_annotations_keeps_annotation_mutants(self):
        sampled, positions = self.sample_numbers("division")
        self.assertEqual(sampled, set(positions.values()))
