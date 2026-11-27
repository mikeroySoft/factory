from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, stats


class SelfWorkTest(unittest.TestCase):
    def test_weekly_command_shares_and_cross_repo_watch_receipts(self):
        now = datetime(2026, 11, 27, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp:
            roots = {}
            for slug in ('mikeroySoft/factory', 'owner/Benito'):
                root = Path(temp) / slug.split('/')[-1]
                root.mkdir()
                (root / '.factory').mkdir()
                roots[slug] = config.Config(root, slug)
            def row(event, **fields):
                return {'at': now.isoformat(), 'event': event, **fields}
            factory_rows = [row('attempt'), row('attempt'),
                            row('llm-usage', stage='worker', cost=6),
                            row('llm-usage', stage='review', cost=100),
                            row('watch', repo='mikeroySoft/factory', issue=1)]
            product_rows = [row('attempt'), row('llm-usage', stage='worker', cost=2),
                            row('watch', repo='mikeroySoft/factory', issue=1),
                            row('watch', repo='mikeroySoft/factory', issue=2, commented=True),
                            row('attempt', at=(now - timedelta(days=8)).isoformat())]
            for slug, rows in zip(roots, (factory_rows, product_rows)):
                (roots[slug].factory / 'events.jsonl').write_text(
                    ''.join(json.dumps(r) + '\n' for r in rows))
            output = io.StringIO()
            with mock.patch.object(stats, 'resolve', return_value=roots['mikeroySoft/factory']), \
                 mock.patch.object(stats, '_now', return_value=now), \
                 mock.patch.object(config, 'host_config', return_value={'repo': {
                     slug: {'path': str(cfg.root)} for slug, cfg in roots.items()}}), \
                 mock.patch.object(config, 'load', side_effect=lambda path: next(
                     cfg for cfg in roots.values() if cfg.root == path)), \
                 mock.patch.object(stats, 'gh', return_value=[{'number': 1}, {'number': 2}]), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(stats.main(['--week', '--self-work', '--json']), 0)
            report = json.loads(output.getvalue())
            factory = report['repositories']['mikeroySoft/factory']
            self.assertEqual(factory['attempt_share'], 2 / 3)
            self.assertEqual(factory['known_spend_share'], .75)
            self.assertEqual(report['factory_watch_filed_share'], .5)
            self.assertEqual(report['factory_watch_filed'], 1)

    def test_factory_only_and_unknown_cost_are_not_missing_product_zeros(self):
        now = datetime(2026, 11, 27, tzinfo=UTC)
        rows = [{'at': now.isoformat(), 'event': 'attempt'},
                {'at': now.isoformat(), 'event': 'llm-usage', 'stage': 'worker'}]
        result = stats.self_work_metrics({'mikeroySoft/factory': rows}, {}, now, now, None)
        self.assertEqual(list(result['repositories']), ['mikeroySoft/factory'])
        factory = result['repositories']['mikeroySoft/factory']
        self.assertEqual(factory['attempt_share'], 1)
        self.assertIsNone(factory['known_spend_share'])
        self.assertEqual(factory['unpriced_usage'], 1)
        self.assertIsNone(result['factory_watch_filed_share'])
