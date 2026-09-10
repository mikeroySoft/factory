"""Behavioral contract for the shared read-only PR feedback collector."""
import copy
import unittest

from factory import feedback

H = "a" * 40
OLD = "b" * 40
AT = "2026-09-10T12:00:00Z"
REPO = {"id": "R_1", "slug": "example/project", "host": "github.com"}
ISSUE = {"id": "I_79", "number": 79, "url": "https://github.com/example/project/issues/79"}
PR = {"id": "PR_80", "number": 80, "url": "https://github.com/example/project/pull/80"}


class Provider:
    def __init__(self):
        self.heads = [H, H]
        self.calls = []
        self.fail = None
        self.reviews = [{"node_id": "RV_1", "html_url": PR["url"] + "#pullrequestreview-1", "commit_id": H,
                         "state": "CHANGES_REQUESTED", "body": "Fix boundary", "user": {"login": "shared", "type": "User"}}]
        self.comments = [{"id": "RC_1", "url": PR["url"] + "#discussion_r1", "body": "<img onerror=alert(1)>",
                          "updatedAt": AT, "commit": {"oid": H}, "originalCommit": {"oid": OLD},
                          "path": "factory/example.py", "line": 7, "originalLine": 6, "diffSide": "RIGHT",
                          "author": {"login": "reader", "__typename": "User"}, "pullRequestReview": {"id": "RV_1"}}]
        self.thread = {"id": "T_1", "isResolved": False, "isOutdated": False,
                       "comments": {"nodes": self.comments, "pageInfo": {"hasNextPage": False}}}
        self.checks = [{"node_id": "CR_1", "id": 1, "html_url": PR["url"] + "/checks?check_run_id=1",
                        "head_sha": H, "name": "CI", "status": "completed", "conclusion": "failure", "run_attempt": 1}]
        self.statuses = []

    def __call__(self, *, endpoint=None, query=None, variables=None, timeout=None):
        self.calls.append((endpoint, query, timeout))
        source = 'threads' if query and 'reviewThreads' in query else 'pr' if query else 'reviews' if '/reviews?' in endpoint else 'checks'
        if self.fail == source:
            raise RuntimeError('token=SECRET provider config')
        if source == 'pr':
            return {"repository": {"id": "R_1", "pullRequest": {**PR, "headRefOid": self.heads.pop(0), "state": "OPEN", "isDraft": False,
                    "closingIssuesReferences": {"nodes": [ISSUE], "pageInfo": {"hasNextPage": False}}}}}
        if source == 'threads':
            return {"repository": {"pullRequest": {"reviewThreads": {"nodes": [self.thread], "pageInfo": {"hasNextPage": False}}}}}
        if source == 'reviews':
            return self.reviews
        if '/check-runs?' in endpoint:
            return {"total_count": len(self.checks), "check_runs": self.checks}
        return self.statuses


def collect(provider=None, **kwargs):
    return feedback.collect(provider or Provider(), repository=REPO, pr=PR, issue=ISSUE,
                            events=[{"event": "claimed", "ticket": 79}], observed_at=AT, **kwargs)


class FeedbackTests(unittest.TestCase):
    def test_simultaneous_native_sources_and_explicit_unknown_attribution(self):
        result = collect()
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual({i['kind'] for i in result['items']}, {'review', 'review_comment', 'check_run'})
        self.assertEqual(result['owner']['relation'], 'factory_issue')
        self.assertTrue(all(c['status'] == 'complete' for c in result['coverage'].values()))
        for item in result['items']:
            self.assertTrue(item['source_url'].startswith(PR['url']))
            self.assertEqual(item['observed_head_sha'], H)
            self.assertEqual(item['source_head_sha'], H)
            self.assertEqual(item['relevance'], 'current_head')
            self.assertIn('run_attempt', item)
            self.assertIn('summary', item)
        comment = next(i for i in result['items'] if i['kind'] == 'review_comment')
        self.assertEqual(comment['location']['original_commit_sha'], OLD)
        self.assertEqual(comment['location']['line'], 7)
        self.assertFalse(comment['disposition']['thread_resolved'])
        review = next(i for i in result['items'] if i['kind'] == 'review')
        self.assertEqual(review['author']['kind'], 'unknown')
        self.assertIsNone(review['check_run_id'])
        self.assertEqual(review['disposition']['review_state'], 'CHANGES_REQUESTED')

    def test_factory_provenance_is_not_rendered_verdict_text(self):
        result = feedback.collect(Provider(), repository=REPO, pr=PR, issue=ISSUE,
                                  events=factory_events(), observed_at=AT)
        self.assertEqual(len(result['items']), 4)
        factory = next(i for i in result['items'] if i['kind'] == 'factory_review')
        self.assertEqual(factory['source_id'], 'result-79')
        self.assertEqual(factory['author']['kind'], 'factory_reviewer')
        self.assertEqual(factory['source_head_sha'], H)
        self.assertEqual(factory['body'], '')
        self.assertIsNone(factory['source_url'])
        for change in ({'returncode': 1}, {'parsed': False}, {'actual_head': OLD}):
            events = factory_events()
            events[2].update(change)
            result = feedback.collect(Provider(), repository=REPO, pr=PR, issue=ISSUE,
                                      events=events + [{'body': 'VERDICT: APPROVE'}])
            self.assertNotIn('factory_review', {i['kind'] for i in result['items']})

    def test_equivalent_polls_and_semantic_revisions(self):
        provider = Provider()
        second = {**provider.reviews[0], 'node_id': 'RV_2', 'commit_id': OLD}
        provider.reviews.append(second)
        first = collect(provider)
        provider.heads = [H, H]
        provider.reviews.reverse()
        other = feedback.collect(provider, repository=REPO, pr=PR, issue=ISSUE,
                                 events=[{'event': 'claimed', 'ticket': 79}], observed_at='2026-09-11T00:00:00Z')
        self.assertEqual(first['observation_id'], other['observation_id'])
        self.assertEqual(first['items'], other['items'])
        baseline = {i['source_id']: i for i in first['items']}
        for target, change, changed_id in [
            ('review', {'body': 'edited'}, 'RV_1'),
            ('review', {'state': 'DISMISSED'}, 'RV_1'),
            ('comment', {'line': 9}, 'RC_1'),
            ('thread', {'isResolved': True}, 'RC_1'),
            ('check', {'conclusion': 'success'}, 'CR_1'),
            ('check', {'run_attempt': 2}, 'CR_1'),
        ]:
            p = Provider()
            p.reviews.append(copy.deepcopy(second))
            row = {'review': p.reviews[0], 'comment': p.comments[0], 'thread': p.thread, 'check': p.checks[0]}[target]
            row.update(change)
            revised = collect(p)
            self.assertNotEqual(first['observation_id'], revised['observation_id'])
            for i in revised['items']:
                self.assertEqual(i['evidence_id'], baseline[i['source_id']]['evidence_id'])
                self.assertEqual(i['source_revision'] == baseline[i['source_id']]['source_revision'], i['source_id'] != changed_id)
        p = Provider()
        p.checks[0].update(node_id='CR_2', id=2)
        self.assertNotEqual(next(i for i in collect(p)['items'] if i['kind'] == 'check_run')['evidence_id'], baseline['CR_1']['evidence_id'])

    def test_head_changes_do_not_revise_old_sources(self):
        baseline = collect()
        p = Provider()
        p.heads = [OLD, OLD]
        advanced = collect(p)
        self.assertEqual([i['source_revision'] for i in baseline['items']], [i['source_revision'] for i in advanced['items']])
        self.assertTrue(all(i['relevance'] == 'historical' for i in advanced['items']))
        p = Provider()
        p.heads = [H, OLD]
        race = collect(p)
        self.assertTrue(all(i['relevance'] == 'unknown' for i in race['items']))
        self.assertTrue(all(i['observed_head_sha'] == OLD for i in race['items']))
        self.assertTrue(all(c['status'] == 'partial' for c in race['coverage'].values()))
        self.assertIn(H, race['coverage']['pr']['reason'])
        self.assertIn(OLD, race['coverage']['pr']['reason'])
        p = Provider()
        p.heads = [H, None]
        missing = collect(p)
        self.assertIsNone(missing['pr']['head_sha'])
        self.assertTrue(all(i['relevance'] == 'unknown' for i in missing['items']))
        self.assertEqual(len(missing['items']), 3)

    def test_source_failures_do_not_erase_independent_evidence(self):
        for source in ('reviews', 'threads', 'checks'):
            p = Provider()
            p.fail = source
            result = collect(p)
            self.assertEqual(result['coverage'][source]['status'], 'unavailable')
            self.assertTrue(result['items'])
            self.assertNotIn('SECRET', str(result))
        p = Provider()
        p.reviews = []
        p.comments.clear()
        p.checks.clear()
        result = collect(p)
        self.assertEqual(result['items'], [])
        self.assertTrue(all(c['status'] == 'complete' for c in result['coverage'].values()))

    def test_unknown_source_head_and_outdated_thread_are_not_current(self):
        p = Provider()
        p.reviews[0]['commit_id'] = None
        p.thread['isOutdated'] = True
        p.thread['isResolved'] = True
        result = collect(p)
        by_kind = {i['kind']: i for i in result['items']}
        self.assertEqual(by_kind['review']['relevance'], 'unknown')
        self.assertEqual(by_kind['review_comment']['relevance'], 'historical')
        self.assertTrue(by_kind['review_comment']['disposition']['thread_resolved'])
        self.assertEqual(result['coverage']['reviews']['status'], 'partial')
        for status, conclusion in [('queued', None), ('in_progress', None), ('completed', 'cancelled'),
                                   ('completed', 'success'), ('completed', 'failure'),
                                   ('completed', 'neutral'), ('completed', 'skipped')]:
            p = Provider()
            p.checks[0].update(status=status, conclusion=conclusion)
            check = next(i for i in collect(p)['items'] if i['kind'] == 'check_run')
            self.assertEqual(check['disposition']['check_status'], status)
            self.assertEqual(check['disposition']['check_conclusion'], conclusion)

    def test_body_count_pagination_timeout_and_malformed_bounds(self):
        p = Provider()
        p.reviews[0]['body'] = 'é' * 20000
        result = collect(p)
        review = next(i for i in result['items'] if i['kind'] == 'review')
        self.assertEqual(len(review['body'].encode()), 20000)
        self.assertTrue(review['truncated'])
        self.assertIn('change_detection_incomplete', result['coverage']['reviews']['reason'])
        p = Provider()
        p.reviews = [{**p.reviews[0], 'node_id': f'RV_{n}'} for n in range(100)]
        result = collect(p)
        self.assertLessEqual(len([i for i in result['items'] if i['kind'] == 'review']), 100)
        self.assertEqual(sum('/reviews?' in (c[0] or '') for c in p.calls), 2)
        self.assertTrue(result['coverage']['reviews']['truncated'])
        p = Provider()
        p.thread['comments']['pageInfo']['hasNextPage'] = True
        self.assertEqual(collect(p)['coverage']['threads']['status'], 'partial')
        p = Provider()
        p.reviews = {'bad': 'response'}
        self.assertEqual(collect(p)['coverage']['reviews']['status'], 'unavailable')
        p = Provider()
        def timed(**kwargs):
            if '/reviews?' in (kwargs.get('endpoint') or ''):
                raise TimeoutError('secret')
            return p(**kwargs)
        result = collect(timed)
        self.assertEqual(result['coverage']['reviews']['status'], 'unavailable')
        self.assertTrue(any(i['kind'] == 'check_run' for i in result['items']))
        self.assertTrue(any(e['code'] == 'timeout' for e in result['errors']))
        self.assertLessEqual(len(result['errors']), 32)

    def test_missing_conflicting_identity_and_ownership(self):
        p = Provider()
        p.reviews.append({**p.reviews[0], 'body': 'conflict'})
        result = collect(p)
        self.assertNotIn('review', {i['kind'] for i in result['items']})
        p = Provider()
        p.checks[0]['node_id'] = None
        self.assertNotIn('check_run', {i['kind'] for i in collect(p)['items']})
        result = feedback.collect(Provider(), repository=REPO, pr=PR, issue=ISSUE)
        self.assertEqual(result['owner']['relation'], 'unverified')
        self.assertIsNone(result['owner']['issue'])

    def test_native_status_survives_failed_check_run_read(self):
        p = Provider()
        p.statuses = [{'node_id': 'CS_1', 'id': 12, 'target_url': 'https://ci.example/status/12',
                       'state': 'pending', 'context': 'deployment', 'updated_at': AT,
                       'creator': {'login': 'ci', 'type': 'Bot'}}]
        def read(**kwargs):
            if '/check-runs?' in (kwargs.get('endpoint') or ''):
                raise RuntimeError('credentials')
            return p(**kwargs)
        result = collect(read)
        status = next(i for i in result['items'] if i['kind'] == 'commit_status')
        self.assertEqual(status['source_head_sha'], H)
        self.assertEqual(status['author']['kind'], 'bot')
        self.assertEqual(status['source_url'], 'https://ci.example/status/12')
        self.assertEqual(status['disposition']['check_status'], 'pending')
        self.assertEqual(result['coverage']['checks']['status'], 'partial')

    def test_final_head_failure_retains_details_without_promoting_them(self):
        p = Provider()
        def read(**kwargs):
            if kwargs.get('query') == feedback.HEAD_QUERY and len(p.heads) == 1:
                raise RuntimeError('head unavailable')
            return p(**kwargs)
        result = collect(read)
        self.assertEqual(len(result['items']), 3)
        self.assertIsNone(result['pr']['head_sha'])
        self.assertTrue(all(i['observed_head_sha'] is None and i['relevance'] == 'unknown' for i in result['items']))


def factory_events():
    rows = [{'event': 'claimed', 'ticket': 79}]
    for sequence, kind in enumerate(('enter', 'result', 'exit'), 1):
        rows.append({'event': 'lifecycle', 'schema_version': 1, 'event_id': f'{kind}-79',
                     'execution_id': 'review-79', 'root_execution_id': 'root-79',
                     'dispatcher_run_id': None, 'parent_execution_id': None, 'ticket': 79,
                     'attempt': None, 'review_round': None, 'stage': 'review', 'kind': kind,
                     'sequence': sequence, 'at': AT, 'outcome': 'approved' if kind == 'exit' else None,
                     'reason': None, 'process': None, 'locks': [], 'head': H, 'actual_head': H,
                     'returncode': 0, 'parsed': True, 'verdict': 'APPROVE'})
    return rows
