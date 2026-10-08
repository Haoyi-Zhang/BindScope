"""Seeded, ordered histories; raw decisions and periodic real SQLite checks."""
from __future__ import annotations
from common import *
import random,gzip,csv,collections
from bindscope.model import oracle_binding,identity
from bindscope.guard import Guard,POLICIES,SAFE_POLICIES
from bindscope.sqlite_oracle import SQLiteBridge

def mutate(w,paths,rng,profile,step):
    events=[];sid=str(rng.randrange(2));names=['accounts','branches','tellers','world','orders','items','customers','stock']
    name=rng.choice(names)
    if profile=='stable':
        if step%17==0:events.append(w.data('base',name))
        return events
    rates={'sparse_ddl':.03,'shadow_heavy':.5,'path_churn':.5,'cancel_pairs':.35,'namespace_churn':.3}
    if rng.random()>=rates[profile]:return events
    if profile=='path_churn':
        paths[sid]=('tmp'+sid,)+rng.choice([('early','base','late'),('late','base','early'),('base','early','late'),('early','base','unused')])
        events.append(w.event('path',session=sid));return events
    if profile=='cancel_pairs':
        slot=('early',name)
        if slot not in w.catalog:
            events.append(w.create(*slot,base=9000));events.append(w.drop(*slot))
        else:
            # State cancellation is explicit, not a fabricated rollback.
            events.append(w.analyze(*slot))
        return events
    if profile=='namespace_churn':
        action=rng.choice(['temp','move','outside','schema','unknown','gap','data'])
        if action=='temp':
            slot=('tmp'+sid,name)
            events.append(w.drop(*slot) if slot in w.catalog else w.create(*slot,base=8000))
        elif action=='move':
            source=('early',name);dest=('unused',name)
            if source in w.catalog and dest not in w.catalog:events.append(w.move(source,dest))
            elif source not in w.catalog and dest in w.catalog:events.append(w.move(dest,source))
            elif source not in w.catalog:events.append(w.create(*source,base=7000))
        elif action=='schema':
            for slot in list(w.catalog):
                if slot[0]=='early':w.catalog.pop(slot)
            events.append(w.event('schema',schema='early'))
        elif action=='gap':
            # Mutate authoritative catalog but omit one notification; a later
            # sequence number exposes the gap. All guards had a prior event.
            slot=('early',name)
            if slot not in w.catalog:w.create(*slot,base=6000)
            else:w.drop(*slot)
            events.append(w.event('gap'))
        elif action=='unknown':events.append(w.event('unknown'))
        elif action=='data':events.append(w.data('base',name))
        else:
            slot=('unused',name);events.append(w.drop(*slot) if slot in w.catalog else w.create(*slot,base=5000))
        return events
    action=rng.choice(['shadow','shadow','alter','column','stats','data','unrelated'])
    if action=='shadow':
        slot=('early',name);events.append(w.drop(*slot) if slot in w.catalog else w.create(*slot,base=4000))
    elif action=='alter':events.append(w.alter('base',name))
    elif action=='column':
        # At most one extra column per relation; finite width, no runaway schema.
        events.append(w.alter('base',name,add_column=len(w.catalog[('base',name)].columns)==2))
    elif action=='stats':events.append(w.analyze('base',name))
    elif action=='data':events.append(w.data('base',name))
    else:
        slot=('unused',name);events.append(w.drop(*slot) if slot in w.catalog else w.create(*slot,base=5000))
    return events

def run(outdir,steps=None,seeds=None):
    spec=json.loads((ROOT/'data/workload_spec.json').read_text());outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
    steps=steps or spec['steps_per_seed_per_profile'];seeds=seeds or spec['evaluation_seeds'];qs=queries();summary=[];bridge_counts=collections.Counter();examples={}
    fields=['profile','seed','step','session','query','policy','event_kinds','action','reason','binding_mismatch','existing','native_sufficient_prepare','resolution_calls','fresh_identity','actual_identity']
    with gzip.open(outdir/'decisions.csv.gz','wt',newline='') as raw:
        writer=csv.DictWriter(raw,fieldnames=fields);writer.writeheader()
        for profile in spec['profiles']:
            for seed in seeds:
                rng=random.Random(seed);w=initial_world();paths={str(i):('tmp'+str(i),'early','base','late') for i in range(2)}
                guards={p:{str(i):Guard(str(i),p) for i in range(2)} for p in POLICIES}
                for gs in guards.values():
                    for g in gs.values():g.last_sequence=w.sequence
                counts={p:collections.Counter() for p in POLICIES};bridge=SQLiteBridge()
                for step in range(steps):
                    events=mutate(w,paths,rng,profile,step)
                    for event in events:
                        for gs in guards.values():
                            for g in gs.values():g.observe(event)
                    sid=str(rng.randrange(2));path=paths[sid];qi=rng.randrange(len(qs));q=qs[qi];key=q.text
                    wanted=oracle_binding(w,q,path);wid=identity(wanted)
                    check_sql=(step%25==0)
                    if check_sql:
                        bridge.load(w)
                        fresh_rows=bridge.execute(q,tuple(x[0] for x in wanted[1]),(3,)) if wanted[0]=='ok' else wanted
                        bridge_counts['fresh_executions']+=wanted[0]=='ok'
                    for p,gs in guards.items():
                        g=gs[sid];old=g.entries.get(key);existing=old is not None
                        before=g.resolution_calls
                        native=old.plan.native_outcome(w,q,path)[0] if existing else None
                        actual,decision=g.execute(key,w,q,path)
                        bad=identity(actual)!=wid
                        avoidable=bool(existing and decision.action.startswith('prepare') and identity(native)==wid)
                        counts[p].update(executions=1,mismatches=int(bad),existing=int(existing),prepares=int(decision.action.startswith('prepare')),native_sufficient_prepares=int(avoidable),resolutions=g.resolution_calls-before,errors=int(actual[0]=='error'))
                        if bad and p not in examples:
                            examples[p]={'profile':profile,'seed':seed,'step':step,'query':q.text,'events':[vars(e) for e in events],'fresh':wanted,'actual':actual}
                        if check_sql and actual[0]=='ok':
                            result=bridge.execute(q,tuple(x[0] for x in actual[1]),(3,));bridge_counts[p+'_executions']+=1
                            row_bad=result!=fresh_rows;bridge_counts[p+'_mismatches']+=row_bad
                            if p in SAFE_POLICIES:assert not row_bad
                        writer.writerow(dict(profile=profile,seed=seed,step=step,session=sid,query=qi,policy=p,event_kinds=';'.join(e.kind for e in events),action=decision.action,reason=decision.reason,binding_mismatch=int(bad),existing=int(existing),native_sufficient_prepare=int(avoidable),resolution_calls=g.resolution_calls-before,fresh_identity=repr(wid),actual_identity=repr(identity(actual))))
                        if p in SAFE_POLICIES:assert not bad,(profile,seed,step,p)
                    if step%100==0:
                        for gs in guards.values():
                            for g in gs.values():g.assert_index_consistent()
                bridge.close()
                for p,c in counts.items():summary.append(dict(profile=profile,seed=seed,policy=p,**c))
                raw.flush()
                write_json(outdir/'trace-progress.json',{'completed':len(summary)//len(POLICIES),'rows':summary})
                print(profile,seed,'completed',flush=True)
    with (outdir/'traces_summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(summary[0]));writer.writeheader();writer.writerows(summary)
    write_json(outdir/'traces.json',{'specification':spec,'actual_steps':steps,'actual_seeds':seeds,'scope':'model binding outcomes plus separate SQLite relational execution','sqlite_bridge':dict(bridge_counts),'first_counterexamples':examples})

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'results'));ap.add_argument('--steps',type=int);args=ap.parse_args();run(args.out,args.steps)
