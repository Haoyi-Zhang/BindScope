"""Audit recorded raw decisions against summaries; does not rerun experiments.

Fail on any disagreement. Uses only Python's standard library. Run after
reproduce.py and report.py. This validates recorded finite outputs, not SQL-wide
correctness, PostgreSQL behavior, or independent peer review.
"""
from __future__ import annotations
import argparse
import ast
import collections
import csv
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', newline='') as stream:
        yield from csv.DictReader(stream)


def require(condition: bool, explanation: str) -> None:
    if not condition:
        raise AssertionError(explanation)


def key(row):
    return tuple(row[x] for x in ('profile', 'seed', 'policy'))


def result_identity(result, *, ordered=False):
    """Typed descriptor and row multiset identity of a decoded result.

    Keep multiplicity; do not use Python scalar equality, where True == 1.
    JSON serialization retains the tagged cells used by the native recorder.
    The retained query corpus has no ORDER BY contract.
    """
    if result[0] != 'ok' or ordered:
        return json.dumps(result, sort_keys=True, separators=(',', ':'))
    descriptor = json.dumps(result[1], sort_keys=True, separators=(',', ':'))
    values = tuple(sorted(json.dumps(row, sort_keys=True, separators=(',', ':'))
                          for row in result[2]))
    return result[0], descriptor, values


def audit(out: Path) -> dict:
    model = collections.defaultdict(collections.Counter)
    pairs = {'direct': {}, 'bindscope': {}, 'direct_native': {}, 'native_aware': {}}
    for r in rows(out/'decisions.csv.gz'):
        p = r['policy']; actual = ast.literal_eval(r['actual_identity'])
        fresh = ast.literal_eval(r['fresh_identity'])
        require(int(r['binding_mismatch']) == int(actual != fresh), 'model mismatch flag')
        model[key(r)].update(executions=1, mismatches=int(r['binding_mismatch']),
            existing=int(r['existing']), prepares=int(r['action'].startswith('prepare')),
            native_sufficient_prepares=int(r['native_sufficient_prepare']),
            resolutions=int(r['resolution_calls']), errors=int(actual[0]=='error'))
        if p in pairs:
            k = tuple(r[x] for x in ('profile','seed','step','session','query'))
            require(k not in pairs[p], 'duplicate model decision')
            pairs[p][k] = r['action']
    for r in rows(out/'traces_summary.csv'):
        for f, v in model[key(r)].items():
            require(v == int(r[f]), f'model summary {key(r)} {f}: {v} != {r[f]}')
    require(len(model) == sum(1 for _ in rows(out/'traces_summary.csv')), 'model summary cardinality')
    paired_counts = {}
    for a,b in [('direct','bindscope'),('direct_native','native_aware')]:
        require(pairs[a] == pairs[b], f'decision disagreement {a}/{b}')
        paired_counts[a+'/'+b] = len(pairs[a])
    del pairs

    engine = collections.defaultdict(collections.Counter)
    engine_pairs = {'direct': {}, 'bindscope': {}}
    for r in rows(out/'native_sqlite_decisions.csv.gz'):
        actual, fresh = json.loads(r['actual']), json.loads(r['fresh'])
        read_a, read_f = json.loads(r['actual_reads']), json.loads(r['fresh_reads'])
        require(int(r['mismatch']) == int(result_identity(actual) != result_identity(fresh)),
                'engine result mismatch flag')
        require(int(r['read_mismatch']) == int(actual[0] == fresh[0] == 'ok' and read_a != read_f),
                'engine read-set mismatch flag')
        engine[key(r)].update(executions=1, fresh_controls=1, mismatches=int(r['mismatch']),
            read_mismatches=int(r['read_mismatch']), explicit_prepares=int(r['explicit_prepare']),
            internal_reprepares=int(r['internal_reprepare']), errors=int(actual[0]=='error'),
            catalog_sync_prepares=3)
        if r['policy'] in engine_pairs:
            k=tuple(r[x] for x in ('profile','seed','step','session','query'))
            require(k not in engine_pairs[r['policy']], 'duplicate engine decision')
            engine_pairs[r['policy']][k]=(r['decision'],r['explicit_prepare'])
    for r in rows(out/'native_sqlite_summary.csv'):
        for f,v in engine[key(r)].items():
            require(v == int(r[f]), f'engine summary {key(r)} {f}: {v} != {r[f]}')
    require(len(engine) == sum(1 for _ in rows(out/'native_sqlite_summary.csv')), 'engine summary cardinality')
    require(engine_pairs['direct'] == engine_pairs['bindscope'], 'engine Direct/BindScope decision disagreement')

    waits=collections.Counter()
    for r in rows(out/'native_pool_waits.csv.gz'):
        require(int(r['wait_ns']) >= 0, 'negative wait')
        require(int(r['lease']) == waits[r['order']], 'duplicate/missing pool lease')
        waits[r['order']]+=1
    pool=list(rows(out/'native_pool.csv'))
    require(len(pool) == len(waits), 'missing pool measurement')
    for r in pool:
        require(int(r['executions']) == waits[r['order']], 'pool wait count')
        require(int(r['mismatches']) == 0, 'pool result mismatch')
        qps=int(r['executions'])/float(r['seconds'])
        require(abs(qps-float(r['throughput'])) < 1e-6*max(1,qps), 'pool throughput formula')
    pool_meta=json.loads((out/'native_pool.json').read_text())
    require(sum(waits.values()) == pool_meta['total_executions'], 'pool execution total')
    require(len(pool) == pool_meta['measurements'], 'pool measurement total')

    repro=json.loads((out/'reproduction.json').read_text())
    require(all(x['returncode'] == 0 for x in repro['commands']), 'failed recorded reproduction stage')
    changed=[]
    evaluated_snapshots=[]
    for name, expected in repro['source_sha256'].items():
        path=ROOT/name
        require(path.exists(), f'missing evaluated source {name}')
        current=hashlib.sha256(path.read_bytes()).hexdigest()
        if current != expected:
            frozen = ROOT/'provenance/evaluated-source'/name
            if frozen.is_file() and hashlib.sha256(frozen.read_bytes()).hexdigest() == expected:
                evaluated_snapshots.append(name)
            else:
                changed.append(name)
    # Reporting can be revised after a run: it is never used to produce raw
    # outcomes. Any changed tested/evaluated module remains a hard failure.
    require(set(changed) <= {'experiments/report.py','experiments/verify_results.py'},
            'evaluated source changed after run: '+repr(changed))

    diagnostic=json.loads((out/'binding_observation.json').read_text())
    records=diagnostic['records']
    invisible=sum(not x['result_mismatch'] and x['read_mismatch'] for x in records)
    require(invisible == diagnostic['result_equal_read_different'], 'binding diagnostic total')
    require(len(records) == diagnostic['cases'], 'binding diagnostic case count')

    totals={}
    for name, groups in [('model',model),('actual_sqlite',engine)]:
        total=collections.defaultdict(collections.Counter)
        for (_,_,p), c in groups.items(): total[p].update(c)
        totals[name]={p:dict(c) for p,c in total.items()}
    result={'status':'passed','scope':'Raw output/summary and paired-decision audit, not independent scientific validation',
        'paired_model_decisions':paired_counts,
        'paired_actual_sqlite_decisions':len(engine_pairs['direct']),
        'model_rows':sum(c['executions'] for c in model.values()),
        'actual_sqlite_pairs':sum(c['executions'] for c in engine.values()),
        'pool_measurements':len(pool),'pool_executions':sum(waits.values()),
        'binding_diagnostic_cases':len(records),'result_equal_read_different':invisible,
        'postmeasurement_reporting_changes':changed,
        'evaluated_source_snapshots':evaluated_snapshots,
        'evaluated_source_manifest_checked':len(repro['source_sha256']),
        'totals':totals}
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'results')
    args=parser.parse_args()
    result=audit(args.out)
    print(json.dumps({k:v for k,v in result.items() if k != 'totals'},indent=2))


if __name__=='__main__':
    main()
