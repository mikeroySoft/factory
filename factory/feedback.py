"""Schema-1, read-only PR feedback. Transport is supplied by the full snapshot.

No state, model, or mutation lives here. Missing evidence is never authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote

from factory import lifecycle

ITEM_LIMIT = 100
PAGE_LIMIT = 2
BODY_LIMIT = 20_000
ERROR_LIMIT = 32
DETAIL_TIMEOUT = 30
SOURCES = ('pr', 'reviews', 'threads', 'checks')
NOT_COLLECTED = 'not_collected'
NOT_COLLECTED_MESSAGE = ('Detail collection is limited to open pull requests; this source was not read. '
                         'Intentional noncollection is unknown, not empty, resolved, or unsupported.')
REVISION_FIELDS = ('kind', 'source_id', 'review_id', 'thread_id', 'check_run_id',
                   'run_attempt', 'source_head_sha', 'source_updated_at', 'author',
                   'body', 'truncated', 'location', 'disposition', 'summary')
HEAD_QUERY = '''query($owner:String!,$name:String!,$number:Int!){
 repository(owner:$owner,name:$name){id pullRequest(number:$number){
 id number url headRefOid state isDraft
 closingIssuesReferences(first:100){nodes{id number url} pageInfo{hasNextPage}}
 }}}'''
REVIEW_QUERY = '''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
 repository(owner:$owner,name:$name){pullRequest(number:$number){
 reviews(first:50,after:$cursor){pageInfo{hasNextPage endCursor} nodes{
 id url body state updatedAt author{login __typename} commit{oid}
 }}}}}'''
THREAD_QUERY = '''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
 repository(owner:$owner,name:$name){pullRequest(number:$number){
 reviewThreads(first:50,after:$cursor){pageInfo{hasNextPage endCursor} nodes{
 id isResolved isOutdated comments(first:100){pageInfo{hasNextPage} nodes{
 id url body updatedAt author{login __typename} commit{oid} originalCommit{oid}
 path line originalLine diffSide pullRequestReview{id}
 }}}}}}}'''


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value: object) -> str:
    return 'sha256:' + hashlib.sha256(canonical(value)).hexdigest()


def identity(value: object) -> str | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    return unicodedata.normalize('NFC', str(value).strip()) or None


def sha(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{40,64}', value) else None


def utc(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except ValueError:
        pass
    return None


def collect(read, *, repository: dict, pr: dict, issue: dict | None = None,
            events: list[dict] = (), provenance_complete: bool = True,
            producer_revision: str | None = None, observed_at: str | None = None,
            collect_details: bool = True) -> dict:
    """Collect one PR using read(endpoint=... | query=..., variables=..., timeout=...).

    The transport returns decoded REST JSON or GraphQL data and enforces timeout.
    All source failures become bounded, sanitized coverage; independent facts survive.
    events must be retained, committed rows from the repository's bounded journal read.
    collect_details=False reads nothing: the caller's independently known identities
    and state are retained and every source is unavailable, never complete-empty.
    """
    observed_at = utc(observed_at) or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    repository = {"id": identity(repository.get('id')), "slug": repository['slug'].strip().lower(),
                  "host": repository['host'].strip().lower()}
    result = {'schema_version': 1, 'producer': {'name': 'factory.pr-feedback', 'revision': sha(producer_revision)},
              'observed_at': observed_at, 'observation_id': None, 'repository': repository,
              'pr': {'id': identity(pr.get('id')), 'number': pr['number'], 'url': pr['url'], 'head_sha': None,
                     'state': pr['state'].lower() if isinstance(pr.get('state'), str)
                     and pr['state'].lower() in ('open', 'closed', 'merged') else 'unknown',
                     'draft': pr['draft'] if type(pr.get('draft')) is bool else None},
              'owner': {'issue': None, 'relation': 'unverified', 'evidence': []},
              'coverage': {s: {'status': 'unavailable', 'observed_at': None, 'truncated': False, 'reason': None} for s in SOURCES},
              'items': [], 'errors': []}
    reasons = {s: set() for s in SOURCES}
    deadline = time.monotonic() + DETAIL_TIMEOUT
    owner, name = repository['slug'].split('/', 1)
    variables = {'owner': owner, 'name': name, 'number': pr['number']}
    prefix = 'repos/' + repository['slug']
    counts = {s: 0 for s in SOURCES}
    identities = {}
    conflicts = set()

    def gap(source, code, message, *, truncated=False):
        coverage = result['coverage'][source]
        if coverage['observed_at'] is not None:
            coverage['status'] = 'partial'
        coverage['truncated'] |= truncated
        reasons[source].add(code + ': ' + message)
        error = {'source': source, 'code': code, 'message': message}
        if error not in result['errors'] and len(result['errors']) < ERROR_LIMIT:
            result['errors'].append(error)

    def success(source):
        c = result['coverage'][source]
        c['observed_at'] = observed_at
        c['status'] = 'partial' if reasons[source] else 'complete'

    def request(source, **kwargs):
        # Historical PRs are never fanned out: noncollection is recorded, not read.
        if not collect_details:
            gap(source, NOT_COLLECTED, NOT_COLLECTED_MESSAGE)
            return None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            value = read(**kwargs, timeout=remaining)
            if time.monotonic() > deadline:
                raise TimeoutError
            if not isinstance(value, (dict, list)):
                raise ValueError
            return value
        except (TimeoutError, subprocess.TimeoutExpired):
            gap(source, 'timeout', 'Feedback detail group reached its 30-second limit.')
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            gap(source, 'source_unavailable', 'Provider source could not be read or decoded.')
        return None

    def head():
        data = request('pr', query=HEAD_QUERY, variables=variables)
        if data is None:
            return None
        try:
            repo = data['repository']
            p = repo['pullRequest']
            if not isinstance(p, dict) or p.get('number') != pr['number']:
                raise ValueError
            if (not identity(repo.get('id')) or not identity(p.get('id'))
                    or repository['id'] not in (None, identity(repo['id']))
                    or result['pr']['id'] not in (None, identity(p['id']))):
                gap('pr', 'identity_conflict', 'Provider repository or PR native identity is missing or contradictory.')
                return None
            repository['id'] = identity(repo['id'])
            result['pr']['id'] = identity(p['id'])
            success('pr')
            if not sha(p.get('headRefOid')):
                gap('pr', 'head_missing', 'Provider PR head is unavailable.')
            p = dict(p)
            if p.get('state') not in ('OPEN', 'CLOSED', 'MERGED'):
                p['state'] = 'UNKNOWN'
                gap('pr', 'state_unknown', 'Provider PR state is unavailable or unsupported.')
            if type(p.get('isDraft')) is not bool:
                gap('pr', 'draft_unknown', 'Provider PR draft state is unavailable.')
            links = p.get('closingIssuesReferences')
            if (not isinstance(links, dict) or not isinstance(links.get('nodes'), list)
                    or not isinstance(links.get('pageInfo'), dict)
                    or type(links['pageInfo'].get('hasNextPage')) is not bool):
                p['closingIssuesReferences'] = {}
                gap('pr', 'links_unknown', 'Provider issue-link coverage is unavailable.')
            elif links['pageInfo']['hasNextPage']:
                gap('pr', 'issue_link_limit', 'Issue links stop after 100 entries; ownership is unknown.', truncated=True)
            return p
        except (KeyError, TypeError, ValueError, AttributeError):
            gap('pr', 'malformed_response', 'Provider PR response is malformed.')
            return None

    def item(source, kind, source_id, *, body='', updated=None, source_sha=None, url=None,
             author=None, review_id=None, thread_id=None, check_run_id=None, attempt=None,
             location=None, disposition=None, summary=None, factory=False):
        sid = identity(source_id)
        if not sid or not repository['id'] or not result['pr']['id']:
            gap(source, 'identity_missing', 'Required native source, repository, or PR identity is unavailable.')
            return
        if counts[source] >= ITEM_LIMIT:
            gap(source, 'item_limit', 'Source retains at most 100 items.', truncated=True)
            return
        if not isinstance(body, str):
            gap(source, 'malformed_body', 'Provider body is not text.')
            body = ''
        encoded = body.encode('utf-8')
        cut = len(encoded) > BODY_LIMIT
        body = encoded[:BODY_LIMIT].decode('utf-8', errors='ignore')
        updated = utc(updated)
        if cut:
            gap(source, 'body_limit', 'Source body retains at most 20000 UTF-8 bytes.', truncated=True)
            if updated is None:
                gap(source, 'change_detection_incomplete', 'No reliable update signal: changes beyond retained body cannot be detected.', truncated=True)
        source_sha = sha(source_sha)
        if source_sha is None:
            gap(source, 'source_head_missing', 'Source commit identity is unavailable; relevance is unknown.')
        actor = author if isinstance(author, dict) else {}
        author_kind = 'factory_reviewer' if factory else 'bot' if actor.get('type', actor.get('__typename')) == 'Bot' else 'unknown'
        value = {'evidence_id': ':'.join(quote(v, safe='') for v in ('github', repository['host'], repository['id'], result['pr']['id'], kind, sid)),
                 'kind': kind, 'source_id': sid, 'source_url': url if isinstance(url, str) else None,
                 'source_revision': None, 'source_updated_at': updated,
                 'review_id': identity(review_id), 'thread_id': identity(thread_id),
                 'check_run_id': identity(check_run_id), 'run_attempt': attempt if type(attempt) is int and attempt > 0 else None,
                 'observed_head_sha': None, 'source_head_sha': source_sha, 'relevance': 'unknown',
                 'disposition': dict.fromkeys(('review_state', 'thread_resolved', 'thread_outdated', 'check_status', 'check_conclusion')),
                 'author': {'login': identity(actor.get('login')), 'kind': author_kind}, 'body': body, 'truncated': cut,
                 'location': dict.fromkeys(('path', 'line', 'side', 'original_line', 'original_commit_sha')),
                 'summary': summary if isinstance(summary, str) else None}
        value['location'].update(location or {})
        value['disposition'].update(disposition or {})
        for field, allowed in {
            'review_state': ('APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'),
            'thread_resolved': (True, False), 'thread_outdated': (True, False),
            'check_status': ('queued', 'in_progress', 'completed', 'waiting', 'requested', 'pending', 'success', 'failure', 'error'),
            'check_conclusion': ('success', 'failure', 'neutral', 'cancelled', 'skipped', 'timed_out', 'action_required', 'stale', 'startup_failure'),
        }.items():
            retained = value['disposition'][field]
            if retained is not None and (retained not in allowed or type(retained) is not type(allowed[0])):
                value['disposition'][field] = None
                gap(source, 'disposition_unknown', 'Provider disposition is missing or unsupported.')
        for field in ('line', 'original_line'):
            retained = value['location'][field]
            if retained is not None and (type(retained) is not int or retained <= 0):
                value['location'][field] = None
                gap(source, 'location_unknown', 'Provider line location is malformed.')
        for field in ('path', 'side'):
            retained = value['location'][field]
            if retained is not None and not isinstance(retained, str):
                value['location'][field] = None
                gap(source, 'location_unknown', 'Provider path or side is malformed.')
        value['source_revision'] = digest({k: value[k] for k in REVISION_FIELDS})
        key = value['evidence_id']
        if key in conflicts:
            return
        previous = identities.get(key)
        if previous is not None:
            if previous['source_revision'] != value['source_revision']:
                result['items'].remove(previous)
                conflicts.add(key)
                gap(source, 'source_identity_conflict', 'One source identity returned contradictory retained facts.')
            return
        identities[key] = value
        result['items'].append(value)
        counts[source] += 1

    # Real reviews retain native IDs, the reviewed commit, and the provider's own
    # update time, which (unlike a submission time) moves when a review is edited.
    initial = head()
    initial_sha = sha((initial or {}).get('headRefOid'))
    cursor = None
    for page in range(PAGE_LIMIT):
        values = request('reviews', query=REVIEW_QUERY, variables={**variables, 'cursor': cursor})
        if values is None:
            break
        try:
            connection = values['repository']['pullRequest']['reviews']
            rows = connection['nodes']
            page_info = connection['pageInfo']
            if not isinstance(rows, list) or type(page_info['hasNextPage']) is not bool:
                raise ValueError
            success('reviews')
            for row in rows[:ITEM_LIMIT]:
                if not isinstance(row, dict):
                    gap('reviews', 'malformed_response', 'Provider review record is malformed.')
                    continue
                item('reviews', 'review', row.get('id'), body=row.get('body') or '', updated=row.get('updatedAt'),
                     source_sha=(row.get('commit') or {}).get('oid'), url=row.get('url'), author=row.get('author'),
                     review_id=row.get('id'), disposition={'review_state': row.get('state')})
            if not page_info['hasNextPage']:
                break
            if page + 1 == PAGE_LIMIT or counts['reviews'] >= ITEM_LIMIT:
                gap('reviews', 'page_limit', 'Reviews stopped at two pages or 100 items; unseen evidence is unknown.', truncated=True)
                break
            cursor = identity(page_info.get('endCursor'))
            if cursor is None:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            gap('reviews', 'malformed_response', 'Provider reviews response is malformed.')
            break

    cursor = None
    for page in range(PAGE_LIMIT):
        values = request('threads', query=THREAD_QUERY, variables={**variables, 'cursor': cursor})
        if values is None:
            break
        try:
            connection = values['repository']['pullRequest']['reviewThreads']
            threads = connection['nodes']
            page_info = connection['pageInfo']
            if not isinstance(threads, list) or type(page_info['hasNextPage']) is not bool:
                raise ValueError
            success('threads')
            for thread in threads[:ITEM_LIMIT]:
                if not identity(thread.get('id')):
                    gap('threads', 'identity_missing', 'Native thread identity is unavailable.')
                    continue
                if type(thread.get('isResolved')) is not bool or type(thread.get('isOutdated')) is not bool:
                    gap('threads', 'thread_state_missing', 'Thread resolution or outdated state is unavailable.')
                comments = thread['comments']
                if not isinstance(comments['nodes'], list):
                    raise ValueError
                for row in comments['nodes'][:ITEM_LIMIT]:
                    item('threads', 'review_comment', row.get('id'), body=row.get('body') or '', updated=row.get('updatedAt'),
                         source_sha=(row.get('commit') or {}).get('oid'), url=row.get('url'), author=row.get('author'),
                         review_id=(row.get('pullRequestReview') or {}).get('id'), thread_id=thread['id'],
                         location={'path': row.get('path'), 'line': row.get('line'), 'side': row.get('diffSide'),
                                   'original_line': row.get('originalLine'), 'original_commit_sha': sha((row.get('originalCommit') or {}).get('oid'))},
                         disposition={'thread_resolved': thread.get('isResolved'), 'thread_outdated': thread.get('isOutdated')})
                if comments['pageInfo']['hasNextPage'] or len(comments['nodes']) > ITEM_LIMIT:
                    gap('threads', 'comment_page_limit', 'Thread comment page retains at most 100 comments; further comments are unknown.', truncated=True)
            if not page_info['hasNextPage']:
                break
            if page + 1 == PAGE_LIMIT or counts['threads'] >= ITEM_LIMIT:
                gap('threads', 'page_limit', 'Threads stopped at two pages or 100 comments; further evidence is unknown.', truncated=True)
                break
            cursor = identity(page_info.get('endCursor'))
            if cursor is None:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            gap('threads', 'malformed_response', 'Provider thread response is malformed.')
            break

    # Two pages total for the combined checks source: one native check-run page
    # and one commit-status page. Never spend the status page budget on check runs.
    if initial_sha:
        for kind, endpoint in (('check_run', f'{prefix}/commits/{initial_sha}/check-runs?per_page=100&filter=all'),
                               ('commit_status', f'{prefix}/commits/{initial_sha}/statuses?per_page=100')):
            values = request('checks', endpoint=endpoint)
            if values is None:
                continue
            try:
                rows = values['check_runs'] if kind == 'check_run' else values
                if not isinstance(rows, list):
                    raise ValueError
                success('checks')
                for row in rows[:ITEM_LIMIT]:
                    if not isinstance(row, dict):
                        raise ValueError
                    is_run = kind == 'check_run'
                    item('checks', kind, row.get('node_id'), url=row.get('html_url', row.get('target_url')),
                         body='\n\n'.join(v for v in ((row.get('output') or {}).get('summary'), (row.get('output') or {}).get('text')) if isinstance(v, str) and v) if is_run else (row.get('description') or ''),
                         updated=row.get('updated_at'), source_sha=row.get('head_sha') if is_run else initial_sha,
                         author=row.get('creator'), check_run_id=row.get('id') if is_run else None,
                         attempt=row.get('run_attempt'), summary=row.get('name') if is_run else row.get('context'),
                         disposition={'check_status': row.get('status') if is_run else row.get('state'),
                                      'check_conclusion': row.get('conclusion') if is_run else None})
                if (values.get('total_count', len(rows)) > len(rows) if kind == 'check_run' else len(rows) >= ITEM_LIMIT):
                    gap('checks', 'page_limit', 'Checks/statuses stop after one page each; unseen evidence is unknown.', truncated=True)
            except (KeyError, TypeError, ValueError, AttributeError):
                gap('checks', 'malformed_response', 'Provider checks response is malformed.')
    elif collect_details:
        gap('checks', 'head_unavailable', 'Checks cannot be queried without an observed immutable commit.')
    else:
        gap('checks', NOT_COLLECTED, NOT_COLLECTED_MESSAGE)

    final = head()
    final_sha = sha((final or {}).get('headRefOid'))
    if final:
        result['pr'].update(head_sha=final_sha, state=final.get('state', 'unknown').lower(),
                            draft=final.get('isDraft') if type(final.get('isDraft')) is bool else None)
        if result['pr']['state'] not in ('open', 'closed', 'merged'):
            result['pr']['state'] = 'unknown'
    raced = collect_details and (initial_sha != final_sha or initial_sha is None or final_sha is None)
    if raced:
        for source in SOURCES:
            gap(source, 'head_race' if initial_sha and final_sha else 'head_unavailable',
                f'Collection heads initial={initial_sha or "unknown"}, final={final_sha or "unknown"}; applicability is unknown.')

    # Same-repository native issue/link plus a retained Factory claim. Conflicting
    # links remain ambiguous; a branch name or shared credential never proves this.
    links = ((final or initial or {}).get('closingIssuesReferences') or {})
    linked = links.get('nodes')
    claimed = issue and any(e.get('event') == 'claimed' and e.get('ticket') == issue['number'] for e in events)
    if isinstance(linked, list):
        candidates = [v for v in linked if isinstance(v, dict)]
        if len(candidates) > 1 or candidates and issue and candidates[0].get('number') != issue['number']:
            result['owner']['relation'] = 'ambiguous'
            result['owner']['evidence'] = ['Conflicting provider closing-issue links.']
        elif candidates and issue and candidates[0].get('id') == issue.get('id') and candidates[0].get('url') == issue.get('url') and claimed and not links.get('pageInfo', {}).get('hasNextPage'):
            result['owner'] = {'issue': {k: issue[k] for k in ('id', 'number', 'url')}, 'relation': 'factory_issue',
                               'evidence': ['Provider same-repository closing-issue link: ' + issue['url'], 'Retained Factory claimed event for issue #' + str(issue['number'])]}
        elif not candidates and not issue:
            result['owner']['relation'] = 'none'
    elif not collect_details and claimed:
        result['owner']['evidence'] = ['Retained Factory claimed event for issue #' + str(issue['number'])
                                       + '; the provider issue link was not read, so ownership stays unverified.']
    if collect_details and not provenance_complete:
        gap('reviews', 'provenance_partial', 'Bounded Factory event provenance is incomplete; unseen reviews are unknown.', truncated=True)
    if collect_details and result['owner']['relation'] != 'factory_issue':
        gap('pr', 'ownership_unverified', 'Factory issue ownership is missing, conflicting, or unverified.')

    # Accepted lifecycle result IDs are the source, never rendered VERDICT prose.
    valid = [e for e in events if lifecycle._lifecycle(e)]
    if result['owner']['relation'] == 'factory_issue':
        for event in valid:
            if event['stage'] != 'review' or event['kind'] != 'result' or event['ticket'] != issue['number']:
                continue
            group = [e for e in valid if e['execution_id'] == event['execution_id']]
            if (event.get('returncode') != 0 or event.get('parsed') is not True
                    or not sha(event.get('head')) or event.get('head') != event.get('actual_head')
                    or event.get('verdict') not in ('APPROVE', 'REVISE')
                    or not any(e['kind'] == 'enter' and e['sequence'] < event['sequence'] for e in group)
                    or not any(e['kind'] == 'exit' and e['sequence'] > event['sequence']
                               and e['outcome'] == ('approved' if event['verdict'] == 'APPROVE' else 'product_feedback') for e in group)):
                continue
            item('reviews', 'factory_review', event['event_id'], source_sha=event['head'], updated=event['at'],
                 factory=True, disposition={'review_state': 'APPROVED' if event['verdict'] == 'APPROVE' else 'CHANGES_REQUESTED'})

    for value in result['items']:
        value['observed_head_sha'] = final_sha
        source_sha = value['source_head_sha']
        if not raced and source_sha:
            value['relevance'] = 'current_head' if source_sha == final_sha else 'historical'
            if value['kind'] == 'review_comment':
                outdated = value['disposition']['thread_outdated']
                if outdated is True:
                    value['relevance'] = 'historical'
                elif outdated is None or value['location']['line'] is None:
                    value['relevance'] = 'unknown'
                    gap('threads', 'comment_position_unknown', 'Current comment position/outdated evidence is unavailable.')
    for source in SOURCES:
        result['coverage'][source]['reason'] = '; '.join(sorted(reasons[source]))[:1000] or None
    result['items'].sort(key=lambda v: (v['evidence_id'], v['source_revision']))
    result['errors'].sort(key=lambda e: (e['source'], e['code'], e['message']))
    result['observation_id'] = digest({'repository_id': repository['id'], 'pr_id': result['pr']['id'], 'head_sha': final_sha,
        'items': [(v['evidence_id'], v['source_revision']) for v in result['items']],
        'coverage': {s: {k: result['coverage'][s][k] for k in ('status', 'truncated', 'reason')} for s in SOURCES}})
    return result
