"""Summarize recorded runs; no network calls or ground-truth assumptions."""
import collections
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads((ROOT / path).read_text())


def latency(values):
    values = sorted(values)
    if not values:
        return None
    return {"n": len(values), "median_seconds": statistics.median(values),
            "p95_nearest_rank_seconds": values[max(0, math.ceil(len(values) * .95) - 1)]}


def main():
    cases = load('cases.json')['cases']
    jev = {r['id']: r for r in load('replay/results.json')}
    base = {r['id']: r for r in load('baseline/results.json')}
    j_manifest = load('replay/manifest.json')
    b_manifest = load('baseline/manifest.json')
    if b_manifest['status'] == 'running':
        raise RuntimeError('Baseline is still running; wait before summarizing')
    source_hash = hashlib.sha256((ROOT / 'cases.json').read_bytes()).hexdigest()
    assert j_manifest['sha256']['cases.json'] == b_manifest['cases_sha256'] == source_hash
    rows = []
    for case in cases:
        j, b = jev.get(case['id'], {}), base.get(case['id'], {})
        jd = j.get('decision') if j.get('deterministic') else j.get('answer', {}).get('choice')
        bd = (b.get('decision') or {}).get('decision')
        rows.append({'id':case['id'], 'split':case['split'], 'number':case['issue']['number'],
                     'jev':jd, 'baseline':bd, 'deterministic':bool(j.get('deterministic')),
                     'confidence':j.get('answer', {}).get('confidence'),
                     'disagrees':jd != bd if jd is not None and bd is not None else None,
                     'human_label':None, 'human_evidence':None})
    paid = [r for r in jev.values() if r.get('ok') and not r.get('deterministic')]
    paired_model = [r for r in rows if not r['deterministic'] and r['disagrees'] is not None]
    cuts = {}
    for split in ('development','evaluation'):
        subset = [r for r in rows if r['split'] == split and not r['deterministic'] and r['confidence'] is not None]
        cuts[split] = {str(cut): {'eligible':len(subset),
                      'retained':sum(r['confidence'] >= cut and r['jev'] != 'insufficient-evidence' for r in subset),
                      'ready_for_agent':sum(r['confidence'] >= cut and r['jev'] == 'ready-for-agent' for r in subset)}
                      for cut in (0,.5,.8,.95)}
    smoke = load('smoke/results.json')
    summary = {'cases':len(cases), 'jev_recorded':len(jev), 'baseline_recorded':len(base),
               'deterministic_cases':sum(r['deterministic'] for r in rows),
               'jev_successful_model_calls':len(paid),
               'jev_model_failures':sum(not r.get('ok') for r in jev.values()),
               'baseline_errors':sum(r.get('error') is not None for r in base.values()),
               'baseline_status':b_manifest['status'],
               'paired_model_cases':len(paired_model),
               'paired_model_disagreements':sum(r['disagrees'] for r in paired_model),
               'jev_routes':dict(collections.Counter(r['jev'] for r in rows if not r['deterministic'])),
               'deterministic_routes':dict(collections.Counter(r['jev'] for r in rows if r['deterministic'])),
               'resolved_models':sorted({r['model'] for r in paid}),
               'jev_input_tokens':sum(r['usage']['input_tokens'] for r in paid),
               'jev_output_tokens':sum(r['usage']['output_tokens'] for r in paid),
               'jev_estimated_usd_including_smoke':sum(r.get('estimated_usd',0) for r in paid + smoke),
               'jev_latency':latency([r['seconds'] for r in paid]),
               'baseline_latency':latency([r['seconds'] for r in base.values() if r['network_attempts'] and not r['error']]),
               'confidence_coverage_not_accuracy':cuts,
               'human_adjudication':'pending; no accuracy or false-admission rate established'}
    (ROOT / 'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    # Preserve human annotations if this analysis is rerun.
    worksheet = ROOT / 'human-review.json'
    if not worksheet.exists():
        worksheet.write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
