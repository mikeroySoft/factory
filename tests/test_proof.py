from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, gate, lifecycle

ROOT = Path(__file__).resolve().parents[1]


def proof_text(
    command: list[str], claimed_exit: int = 0, *, proof: bool = True, not_checked: bool = True,
    output: str = "tank ok",
) -> str:
    parts = []
    if proof:
        parts += [
            "## Proof on ROCm tank",
            "",
            f"### Command: `{shlex.join(command)}`",
            "- Started: `2026-10-29T12:00:00Z`",
            f"- Exit code: `{claimed_exit}`",
            "",
            "```text",
            output,
            "```",
        ]
    if not_checked:
        parts += ["", "## Not checked", "", "nothing"]
    return "\n".join(parts) + "\n"


class ProofGateTest(unittest.TestCase):
    command = (sys.executable, "-c", "print('tank ok')")

    def check(self, text: str) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "proof.md"
            artifact.write_text(text)
            return gate.check_proof(artifact, root)

    def test_missing_proof_section_fails(self) -> None:
        passed, detail = self.check(proof_text(self.command, proof=False))
        self.assertFalse(passed)
        self.assertIn("missing or empty ## Proof on ROCm tank", detail)

    def test_missing_not_checked_section_fails(self) -> None:
        passed, detail = self.check(proof_text(self.command, not_checked=False))
        self.assertFalse(passed)
        self.assertIn("missing or empty ## Not checked", detail)

    def test_changed_exit_code_fails(self) -> None:
        passed, detail = self.check(proof_text(self.command, claimed_exit=7))
        self.assertFalse(passed)
        self.assertIn("claimed exit 7, re-run exit 0", detail)

    def test_matching_rerun_passes_and_records_output(self) -> None:
        passed, detail = self.check(proof_text(self.command))
        self.assertTrue(passed, detail)
        self.assertIn(shlex.join(self.command), detail)
        self.assertIn("tank ok", detail)

    def test_environment_dump_output_is_rejected_without_echoing_values(self) -> None:
        secret = "AWS_SECRET_ACCESS_KEY=plain-text-secret"
        command = ("sh", "-c", f"printf '{secret}\\n'")
        passed, detail = self.check(proof_text(command, output=secret))
        self.assertFalse(passed)
        self.assertIn("environment dump", detail)
        self.assertNotIn(secret, detail)

    def test_environment_mapping_rerun_is_rejected_without_echoing_values(self) -> None:
        secret = "AWS_SECRET_ACCESS_KEY"
        result = subprocess.CompletedProcess(
            self.command, 0, f"environ({{{secret!r}: 'plain-text-secret'}})", "",
        )
        with mock.patch.object(gate, "timed", return_value=result):
            passed, detail = self.check(proof_text(self.command))
        self.assertFalse(passed)
        self.assertIn("environment dump", detail)
        self.assertNotIn(secret, detail)

    def test_environment_dump_after_output_limit_is_rejected(self) -> None:
        secret = "AWS_SECRET_ACCESS_KEY=plain-text-secret"
        output = "\n".join(["safe"] * 40 + [secret])
        result = subprocess.CompletedProcess(self.command, 0, output, "")
        with mock.patch.object(gate, "timed", return_value=result):
            passed, detail = self.check(proof_text(self.command))
        self.assertFalse(passed)
        self.assertIn("environment dump", detail)
        self.assertNotIn(secret, detail)


class ProofDispatchTest(unittest.TestCase):
    def repo(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "git@github.com:acme/repo.git"], check=True)
        (root / "README.md").write_text("base\n")
        subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
        return root

    @staticmethod
    def publish_run(command: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(command, 0, "1\n", "")
        if command[:3] == ["gh", "pr", "create"]:
            return subprocess.CompletedProcess(command, 0, "https://github.com/acme/repo/pull/17\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    @staticmethod
    def gate_run(repo: Path, report: str, *args: str) -> tuple[subprocess.CompletedProcess[str], str]:
        result = subprocess.run(
            [sys.executable, "-m", "factory", "gate", "--report", f".factory/{report}", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            check=False,
        )
        return result, (repo / ".factory" / report).read_text()

    def test_config_loads_proof_repositories_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            (repo / config.CONFIG_NAME).write_text(
                '[proof]\nrepos = ["rocm-cli"]\ncommands = [["rocm", "diagnose"], ["make", "smoke"]]\n'
            )
            loaded = config.load(repo)
        self.assertEqual(loaded.proof_repos, ["rocm-cli"])
        self.assertEqual(loaded.proof_commands, [["rocm", "diagnose"], ["make", "smoke"]])

    def test_required_repo_body_appends_ordered_scrubbed_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            dispatch.configure(config.Config(root=repo, repo="acme/rocm-cli"))
            dispatch.FACTORY.mkdir(exist_ok=True)
            token = "ghp_" + "x" * 36
            (repo / ".factory" / "proof-7.md").write_text(
                proof_text(["rocm", "examine", "--json"], output=f"gfx1201 {token}")
            )
            body = "Closes #7\n\n## Gate report\n\nPASS\n"
            with (
                mock.patch.object(dispatch, "run", side_effect=self.publish_run),
                mock.patch.object(dispatch, "gh_json", return_value=[]),
            ):
                self.assertTrue(dispatch.push_and_pr(repo, "agent/7", "feature", body, ticket=7))
            published = (dispatch.FACTORY / "pr-body-7.md").read_text()
        headings = [published.index(value) for value in ("Closes #7", "## Gate report", "## Proof on ROCm tank", "## Not checked")]
        self.assertEqual(headings, sorted(headings))
        self.assertNotIn(token, published)
        self.assertIn("[REDACTED]", published)

    def test_existing_required_repo_pr_body_is_refreshed_with_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            dispatch.configure(config.Config(root=repo, repo="acme/rocm-cli"))
            dispatch.FACTORY.mkdir(exist_ok=True)
            (repo / ".factory" / "proof-7.md").write_text(
                proof_text(["rocm", "examine", "--json"], output="gfx1201")
            )
            body = "Closes #7\n\n## Gate report\n\nPASS\n"
            with (
                mock.patch.object(dispatch, "run", side_effect=self.publish_run) as run,
                mock.patch.object(
                    dispatch, "gh_json", return_value=[{"number": 17, "baseRefName": "main"}],
                ),
            ):
                self.assertTrue(dispatch.push_and_pr(repo, "agent/7", "feature", body, ticket=7))
            published = (dispatch.FACTORY / "pr-body-7.md").read_text()
            commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("## Proof on ROCm tank", published)
        self.assertIn(
            ["gh", "pr", "edit", "17", "--repo", "acme/rocm-cli",
             "--body-file", str(dispatch.FACTORY / "pr-body-7.md")],
            commands,
        )

    def test_worker_proof_with_environment_dump_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            dispatch.configure(config.Config(root=repo, repo="acme/rocm-cli"))
            dispatch.FACTORY.mkdir(exist_ok=True)
            secret = "AWS_SECRET_ACCESS_KEY=plain-text-secret"
            (repo / ".factory" / "proof-7.md").write_text(
                proof_text(["true"], output=secret)
            )
            with (
                mock.patch.object(dispatch, "run", side_effect=self.publish_run) as run,
                mock.patch.object(dispatch, "gh_json", return_value=[]),
                self.assertRaisesRegex(ValueError, "environment dump"),
            ):
                dispatch.push_and_pr(repo, "agent/7", "feature", "body", ticket=7)
            commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(command[:3] == ["gh", "pr", "create"] for command in commands))

    def test_required_repo_gate_without_ticket_skips_proof_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            (repo / config.CONFIG_NAME).write_text('[proof]\nrepos = ["repo"]\n')
            result, report = self.gate_run(repo, "gate-report-upstream.md")
        self.assertEqual(result.returncode, 0, report + result.stdout + result.stderr)
        self.assertNotIn("- proof:", report)

    def test_gate_report_keeps_every_bounded_proof_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            (repo / config.CONFIG_NAME).write_text('[proof]\nrepos = ["repo"]\n')
            (repo / ".factory").mkdir(exist_ok=True)
            commands = [
                [
                    sys.executable,
                    "-c",
                    f"print('first-{index}-' + '\\nfirst-{index}-'.join(map(str, range(40))))",
                ]
                for index in range(3)
            ]
            records = [
                proof_text(command, not_checked=False, output=f"claimed-{index}")
                .removeprefix("## Proof on ROCm tank\n\n").strip()
                for index, command in enumerate(commands)
            ]
            (repo / ".factory" / "proof-7.md").write_text(
                "## Proof on ROCm tank\n\n"
                + "\n\n".join(records)
                + "\n\n## Not checked\n\nnothing\n"
            )
            result, report = self.gate_run(repo, "gate-report-7.md", "--ticket", "7")
        self.assertEqual(result.returncode, 0, report + result.stdout + result.stderr)
        for command in commands:
            self.assertIn(f"$ {shlex.join(command)}", report)

    def test_unlisted_repo_body_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            dispatch.configure(config.Config(root=repo, repo="acme/factory"))
            dispatch.FACTORY.mkdir(exist_ok=True)
            (repo / ".factory" / "proof-7.md").write_text(proof_text(["true"]))
            body = "Closes #7\n\n## Gate report\n\nPASS\n"
            with (
                mock.patch.object(dispatch, "run", side_effect=self.publish_run),
                mock.patch.object(dispatch, "gh_json", return_value=[]),
            ):
                self.assertTrue(dispatch.push_and_pr(repo, "agent/7", "feature", body, ticket=7))
            published = (dispatch.FACTORY / "pr-body-7.md").read_text()
        self.assertEqual(published, body)

    def test_required_worker_prompt_names_proof_file_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            cfg = config.Config(
                root=repo, repo="acme/gpuflo", proof_commands=[["make", "gpu-smoke"]],
            )
            dispatch.configure(cfg)
            with (
                mock.patch.object(dispatch, "gh_json", return_value={"title": "t", "body": "b", "comments": []}),
                mock.patch.object(dispatch.brief, "ensure", return_value=""),
            ):
                prompt = dispatch.build_prompt(7, repo)
        self.assertIn(".factory/proof-7.md", prompt)
        self.assertIn("make gpu-smoke", prompt)
        self.assertIn("## Not checked", prompt)

    def test_missing_proof_overrides_model_approval_with_typed_reason(self) -> None:
        reply = '```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE'
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(Path(directory))
            dispatch.configure(config.Config(
                root=repo,
                repo="acme/rocm-cli",
                reviewer=[sys.executable, "-c", f"print({reply!r})"],
            ))
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
            ).stdout.strip()
            verdict, findings = dispatch.review(repo, 7, "gate PASS", head)
            pending = dispatch._PENDING_REVIEWS[7]
            self.assertEqual(verdict, "REVISE")
            self.assertTrue(pending["parsed"])
            self.assertEqual(pending["rejection_reason"], dispatch.MISSING_PROOF)
            with mock.patch.object(
                dispatch,
                "run",
                return_value=subprocess.CompletedProcess(
                    ["gh"], 0, "https://github.com/acme/rocm-cli/pull/17#issuecomment-1\n", "",
                ),
            ):
                dispatch.pr_comment(7, findings)
            result = next(
                event for event in lifecycle.read_events(dispatch.EVENTS)
                if event.get("event") == "review-result"
            )
        self.assertEqual(result["verdict"], "REQUEST_CHANGES")
        self.assertEqual(result["reason"], dispatch.MISSING_PROOF)


class ProofMappingEnvironmentTest(unittest.TestCase):
    command = (sys.executable, "-c", "print('tank ok')")
    dumps = (
        '{"AWS_SECRET_ACCESS_KEY": "synthetic-secret", "PATH": "/synthetic/bin"}',
        "{'DATABASE_PASSWORD': 'synthetic-password', 'HOME': '/synthetic/home'}",
        '{\n  "AWS_SECRET_ACCESS_KEY":\n    "synthetic-secret"\n}',
        "{\n  'DATABASE_PASSWORD':\n    'synthetic-password'\n}",
    )

    def test_mapping_reruns_are_rejected_before_truncation_without_values(self) -> None:
        for dump in self.dumps:
            with self.subTest(dump=dump):
                result = subprocess.CompletedProcess(
                    self.command, 0, "\n".join(["safe"] * 40 + [dump]), "",
                )
                with mock.patch.object(gate, "timed", return_value=result):
                    passed, detail = ProofGateTest.check(self, proof_text(self.command))
                self.assertFalse(passed, detail)
                self.assertIn("environment dump", detail)
                self.assertNotIn("synthetic", detail)
                self.assertNotIn("AWS_SECRET_ACCESS_KEY", detail)
                self.assertNotIn("DATABASE_PASSWORD", detail)

    def test_mapping_artifacts_are_rejected_before_publication(self) -> None:
        for dump in self.dumps:
            with self.subTest(dump=dump), tempfile.TemporaryDirectory() as directory:
                repo = ProofDispatchTest.repo(self, Path(directory))
                dispatch.configure(config.Config(root=repo, repo="acme/rocm-cli"))
                dispatch.FACTORY.mkdir(exist_ok=True)
                (dispatch.FACTORY / "proof-7.md").write_text(
                    proof_text(self.command, output=dump)
                )
                with (
                    mock.patch.object(
                        dispatch, "run", side_effect=ProofDispatchTest.publish_run,
                    ) as run,
                    mock.patch.object(dispatch, "gh_json", return_value=[]),
                    self.assertRaisesRegex(ValueError, "environment dump") as caught,
                ):
                    dispatch.push_and_pr(repo, "agent/7", "feature", "body", ticket=7)
                self.assertNotIn("synthetic", str(caught.exception))
                self.assertFalse((dispatch.FACTORY / "pr-body-7.md").exists())
                self.assertFalse(any(
                    call.args[0][:2] == ["gh", "pr"] for call in run.call_args_list
                ))

    def test_rocm_examine_json_remains_valid_at_both_boundaries(self) -> None:
        output = json.dumps({
            "detected_gfx_target": "gfx1201",
            "driver_status": "amdgpu_available",
            "active_runtime": {"id": "therock-7.0"},
        }, indent=2)
        result = subprocess.CompletedProcess(self.command, 0, output, "")
        with mock.patch.object(gate, "timed", return_value=result):
            passed, detail = ProofGateTest.check(self, proof_text(self.command, output=output))
        self.assertTrue(passed, detail)
        self.assertIn("gfx1201", detail)
        with tempfile.TemporaryDirectory() as directory:
            repo = ProofDispatchTest.repo(self, Path(directory))
            dispatch.configure(config.Config(root=repo, repo="acme/rocm-cli"))
            dispatch.FACTORY.mkdir(exist_ok=True)
            (dispatch.FACTORY / "proof-7.md").write_text(proof_text(self.command, output=output))
            with (
                mock.patch.object(dispatch, "run", side_effect=ProofDispatchTest.publish_run),
                mock.patch.object(dispatch, "gh_json", return_value=[]),
            ):
                self.assertTrue(dispatch.push_and_pr(repo, "agent/7", "feature", "body", ticket=7))
            self.assertIn("gfx1201", (dispatch.FACTORY / "pr-body-7.md").read_text())


class ProofMappingShapeTest(ProofMappingEnvironmentTest):
    dumps = (
        '{"https_proxy": "https://synthetic-user:synthetic-password@example.invalid"}',
        "{'https_proxy': 'https://synthetic-user:synthetic-password@example.invalid'}",
        '{\n  "https_proxy":\n    "https://synthetic-user:synthetic-password@example.invalid"\n}',
        "{\n  'https_proxy':\n    'https://synthetic-user:synthetic-password@example.invalid'\n}",
    )

    def test_examination_diagnostic_environment_remains_valid(self) -> None:
        output = json.dumps({
            "os_family": "linux",
            "env": {"PATH": "/synthetic/bin", "HIP_VISIBLE_DEVICES": "0"},
            "summary": {
                "detected_gfx_target": "gfx1201",
                "driver": {"status": "amdgpu_available"},
                "active_runtime_id": "therock-7.0",
            },
        }, indent=2)
        result = subprocess.CompletedProcess(self.command, 0, output, "")
        with mock.patch.object(gate, "timed", return_value=result):
            passed, detail = ProofGateTest.check(self, proof_text(self.command, output=output))
        self.assertTrue(passed, detail)
        with tempfile.TemporaryDirectory() as directory:
            repo = ProofDispatchTest.repo(self, Path(directory))
            dispatch.configure(config.Config(root=repo, repo="acme/rocm-cli"))
            dispatch.FACTORY.mkdir(exist_ok=True)
            (dispatch.FACTORY / "proof-7.md").write_text(proof_text(self.command, output=output))
            with (
                mock.patch.object(dispatch, "run", side_effect=ProofDispatchTest.publish_run),
                mock.patch.object(dispatch, "gh_json", return_value=[]),
            ):
                self.assertTrue(dispatch.push_and_pr(repo, "agent/7", "feature", "body", ticket=7))
            self.assertIn("gfx1201", (dispatch.FACTORY / "pr-body-7.md").read_text())

    def test_examination_envelope_does_not_allow_extra_environment_keys(self) -> None:
        text = json.dumps({
            "os_family": "linux",
            "env": {"PATH": "/synthetic/bin", "https_proxy": "synthetic-password"},
            "summary": {"detected_gfx_target": "gfx1201"},
        })
        self.assertTrue(gate.proof_contains_environment_dump(text))

    def test_proof_reruns_hold_the_host_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = ProofDispatchTest.repo(self, Path(directory))
            (repo / config.CONFIG_NAME).write_text('[proof]\nrepos = ["repo"]\n')
            lock = object()

            def check(*args: object) -> tuple[bool, str]:
                acquire.assert_called_once()
                release.assert_not_called()
                return True, "GPU proof checked"

            previous = Path.cwd()
            try:
                os.chdir(repo)
                with (
                    mock.patch.object(gate, "_acquire_exclusive", return_value=lock) as acquire,
                    mock.patch.object(gate, "_release_exclusive") as release,
                    mock.patch.object(gate, "check_proof", side_effect=check),
                ):
                    self.assertEqual(gate.main(["--ticket", "7"]), 0)
                    self.assertIs(release.call_args.args[1], lock)
            finally:
                os.chdir(previous)


class ProofExaminationBoundaryTest(unittest.TestCase):
    def test_duplicate_environment_fields_cannot_hide_dump_output(self) -> None:
        text = (
            '{"os_family":"linux","summary":{"detected_gfx_target":"gfx1201"},'
            '"env":{"AWS_SECRET_ACCESS_KEY":"synthetic-secret"},'
            '"env":{"PATH":"/synthetic/bin"}}'
        )
        self.assertTrue(gate.proof_contains_environment_dump(text))

    def test_numeric_token_measurements_are_not_environment_dumps(self) -> None:
        self.assertFalse(gate.proof_contains_environment_dump(
            '{"prompt_tokens": 12, "completion_tokens": 3, "status": "ok"}'
        ))


if __name__ == "__main__":
    unittest.main()
