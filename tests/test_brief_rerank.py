import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory import brief, config, lifecycle


class BriefRerankTest(unittest.TestCase):
    def response(self, scores, **extra):
        return io.BytesIO(json.dumps({
            "model": "jev-1.13.0", "usage": {"input_tokens": 100},
            "answers": {f"c{i}": {"noul": score} for i, score in enumerate(scores)},
            **extra,
        }).encode())

    def test_pinned_request_and_model_cost_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("first\nprivate second line\n")
            (root / "b.py").write_text("second\n")
            issue = {"number": 7, "title": "Change `a.py`", "body": "Fix it", "comments": ["not sent"]}
            with patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}), patch.object(
                brief.urllib.request, "urlopen", return_value=self.response([0.1, 0.9])
            ) as provider, redirect_stdout(io.StringIO()) as out:
                ranked = brief.rerank(root, issue, ["a.py", "b.py"])
            self.assertEqual(ranked, ["b.py", "a.py"])
            request = provider.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "jev-1.13.0")
            self.assertEqual(payload["state"], {
                "issue": {"title": issue["title"], "body": "Fix it"},
                "candidates": [{"path": "a.py", "summary": "first"}, {"path": "b.py", "summary": "second"}],
            })
            self.assertEqual(set(payload), {"model", "state", "questions"})
            self.assertEqual(out.getvalue(), "")
            row, = lifecycle.read_events(root / ".factory/brief-rerank.jsonl")
            self.assertEqual(row["model_requested"], "jev-1.13.0")
            self.assertEqual(row["model_resolved"], "jev-1.13.0")
            self.assertEqual(row["input_tokens"], 100)
            self.assertAlmostEqual(row["cost"], 0.0000042)

    def test_configured_model_and_endpoint_reach_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".factory.toml").write_text('[repo]\nslug = "acme/repo"\n[brief]\nmodel = "jev-1.14.0"\nurl = "http://127.0.0.1:9000/v1/systemone"\n')
            with patch.object(config, "repo_root", return_value=root), patch.object(config, "host_config", return_value={}):
                cfg = config.load(root)
            self.assertEqual(config.unknown_keys({"brief": {"model": "x", "url": "x"}}), [])
            with patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}), patch.object(
                brief.urllib.request, "urlopen", return_value=self.response([0.9], model="jev-1.14.0")
            ) as provider:
                brief.rerank(root, {}, ["gone.py"], cfg=cfg)
            self.assertEqual(provider.call_args.args[0].full_url, cfg.brief_url)
            self.assertEqual(json.loads(provider.call_args.args[0].data)["model"], "jev-1.14.0")
            row, = lifecycle.read_events(root / ".factory/brief-rerank.jsonl")
            self.assertEqual(row["model_resolved"], "jev-1.14.0")

    def test_failure_journals_unknown_usage_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}):
            root = Path(directory)
            with patch.object(brief.urllib.request, "urlopen", side_effect=OSError("offline")), self.assertLogs("factory.brief", "WARNING"):
                self.assertEqual(brief.rerank(root, {}, ["a.py", "b.py"]), ["a.py", "b.py"])
            row, = lifecycle.read_events(root / ".factory/brief-rerank.jsonl")
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["model_requested"], "jev-1.13.0")
            self.assertIsNone(row["model_resolved"])
            self.assertIsNone(row["input_tokens"])
            self.assertIsNone(row["cost"])

    def test_invalid_scores_retain_reported_usage(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}):
            root = Path(directory)
            with patch.object(brief.urllib.request, "urlopen", return_value=self.response([2])), self.assertLogs("factory.brief", "WARNING"):
                self.assertEqual(brief.rerank(root, {}, ["a.py"]), ["a.py"])
            row, = lifecycle.read_events(root / ".factory/brief-rerank.jsonl")
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["input_tokens"], 100)
            self.assertAlmostEqual(row["cost"], 0.0000042)

    def test_cached_brief_records_each_attempt_against_original_pool(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}):
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            pool = [f"f{i}.py" for i in range(16)]
            for path in [*pool, "outside.py"]:
                (root / path).write_text("before\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            cfg = config.Config(root, "acme/repo")
            path = root / ".factory/brief-7.md"
            with patch.object(brief, "files_for", return_value=pool), patch.object(
                brief.urllib.request, "urlopen", return_value=self.response([0.1] * 15 + [0.9])
            ) as provider:
                original = brief.ensure(path, root, {"title": "Change `f0.py`"}, "", cfg=cfg)
                self.assertEqual(brief.ensure(path, root, {"title": "ignored"}, "", cfg=cfg), original)
                self.assertEqual(provider.call_count, 1)
            for attempt, changed in [(1, ["f15.py", "f14.py", "outside.py"]), (2, ["f0.py"])]:
                for name in changed:
                    (root / name).write_text(f"attempt {attempt}\n")
                git("add", *changed)
                git("commit", "-qm", f"attempt {attempt}")
                brief.journal_attempt(path, root, cfg, ticket=7, attempt=attempt, base=base, head=git("rev-parse", "HEAD"))
            rows = lifecycle.read_events(root / ".factory/brief-rerank.jsonl")
            coverage = [row for row in rows if row["event"] == "brief-coverage"]
            self.assertEqual([row["attempt"] for row in coverage], [1, 2])
            self.assertEqual(coverage[0]["changed_files"], {
                "f14.py": {"in_candidate_pool": True, "in_top_k": False},
                "f15.py": {"in_candidate_pool": True, "in_top_k": True},
                "outside.py": {"in_candidate_pool": False, "in_top_k": False},
            })
            self.assertEqual(coverage[0]["retrieval_misses"], 1)
            self.assertEqual(coverage[0]["ranking_misses"], 1)
            self.assertEqual(coverage[1]["changed_files"]["f0.py"], {"in_candidate_pool": True, "in_top_k": True})
            self.assertEqual(coverage[0]["rerank_id"], rows[0]["rerank_id"])
            self.assertEqual(coverage[1]["rerank_id"], rows[0]["rerank_id"])

    def test_missing_or_corrupt_snapshot_reports_unknown_not_zero_misses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(root, "acme/repo")
            path = root / ".factory/brief-7.md"
            for attempt, snapshot in enumerate((None, "[]", '{"candidate_pool": null, "top_k": []}'), 1):
                if snapshot is not None:
                    path.with_suffix(".json").write_text(snapshot)
                with patch.object(brief.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, stdout="a.py\0", stderr=""
                )):
                    brief.journal_attempt(path, root, cfg, ticket=7, attempt=attempt, base="base", head="head")
            for row in lifecycle.read_events(cfg.factory / "brief-rerank.jsonl"):
                self.assertEqual(row["status"], "unavailable")
                self.assertIsNone(row["changed_files"])
                self.assertIsNone(row["retrieval_misses"])
                self.assertIsNone(row["ranking_misses"])

    def test_invalid_brief_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for values in ('model = ""', 'url = "file:///tmp/provider"', 'url = "http://"',
                           'url = "http://localhost:bad"'):
                (root / ".factory.toml").write_text('[repo]\nslug = "acme/repo"\n[brief]\n' + values)
                with self.subTest(values=values), patch.object(
                    config, "repo_root", return_value=root
                ), patch.object(config, "host_config", return_value={}), self.assertRaises(config.ConfigError):
                    config.load(root)

    def test_skipped_retrieval_preserves_lifecycle_journal(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}):
            root = Path(directory)
            journal = root / ".factory/events.jsonl"
            lifecycle.append(journal, {"event": "claimed", "ticket": 7})
            self.assertEqual(brief.rerank(root, {"number": 7}, ["a.py"]), ["a.py"])
            self.assertEqual(lifecycle.read_events(journal), [{"event": "claimed", "ticket": 7}])
            row, = lifecycle.read_events(root / ".factory/brief-rerank.jsonl")
            self.assertEqual(row["status"], "skipped")
            self.assertEqual(row["candidate_pool"], ["a.py"])
            self.assertIsNone(row["cost"])
