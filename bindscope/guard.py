"""Session-local, event-indexed witness guard and comparison policies."""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
from .syntax import Query
from .model import World, Resolution, Plan, Event, MissingRelation, resolve, resolve_direct

BASE_POLICIES=('native_model','checkout','dependency','global_epoch','direct','prefix_strict','bindscope')
POLICIES=BASE_POLICIES+('native_aware','direct_native')
SAFE_POLICIES=tuple(p for p in POLICIES if p not in ('native_model','dependency'))
@dataclass
class Entry:
    query: Query
    certificate: Resolution
    plan: Plan
    context_epoch: int
    hard_epoch: int
    native_path: tuple[str,...] | None=None
    dirty: bool=False
    reasons: set[str]=field(default_factory=set)

@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    resolved: bool

class Guard:
    def __init__(self,session:str,policy='bindscope'):
        if policy not in POLICIES: raise ValueError(policy)
        self.session=session; self.policy=policy
        self.entries:dict[str,Entry]={}
        self.slot_index=defaultdict(set); self.schema_index=defaultdict(set)
        self.path_keys=set(); self.context_epoch=0; self.hard_epoch=0
        self.last_sequence=None; self.observed_events=0; self.dispatch_visits=0
        self.resolution_calls=0; self.prepare_attempts=0; self.invalidations=0

    def _unlink(self,key):
        old=self.entries.get(key)
        if old is None:return
        for slot in old.certificate.slots:
            self.slot_index[slot].discard(key)
            if not self.slot_index[slot]: del self.slot_index[slot]
        for schema in {s for s,_ in old.certificate.slots}:
            self.schema_index[schema].discard(key)
            if not self.schema_index[schema]: del self.schema_index[schema]
        self.path_keys.discard(key)

    def _link(self,key,entry):
        self.entries[key]=entry
        for slot in entry.certificate.slots:self.slot_index[slot].add(key)
        for schema in {s for s,_ in entry.certificate.slots}:self.schema_index[schema].add(key)
        if entry.certificate.path_sensitive:self.path_keys.add(key)

    def evict(self,key):
        self._unlink(key); self.entries.pop(key,None)

    def clear(self):
        self.entries.clear(); self.slot_index.clear(); self.schema_index.clear(); self.path_keys.clear()
        self.hard_epoch+=1

    def observe(self,event:Event):
        # Every physical guard receives the same global sequence, even for
        # another session's private event. Filtering BEFORE sequencing is wrong.
        if self.last_sequence is not None and event.seq != self.last_sequence+1:
            self.hard_epoch+=1
        self.last_sequence=event.seq; self.observed_events+=1
        if event.session is not None and event.session!=self.session:return
        if event.kind in ('data','stats'):return
        if event.kind in ('gap','unknown','security','reset'):
            self.hard_epoch+=1; self.context_epoch+=1; return
        self.context_epoch+=1
        if self.policy not in ('bindscope','prefix_strict','native_aware'):return
        affected=set()
        if event.kind=='path':affected.update(self.path_keys)
        elif event.kind=='schema':affected.update(self.schema_index.get(event.schema,()))
        elif event.kind=='ddl':
            for slot in event.slots:affected.update(self.slot_index.get(slot,()))
        else:
            self.hard_epoch+=1; return
        self.dispatch_visits+=len(affected)
        for key in affected:
            e=self.entries[key]; e.dirty=True; e.reasons.add(event.kind)

    def _resolve(self,world,query,path):
        self.resolution_calls+=1
        return (resolve_direct if self.policy in ('direct','direct_native') else resolve)(world,query,path)

    def decide(self,key:str,world:World,query:Query,path:tuple[str,...]):
        e=self.entries.get(key); p=self.policy
        if e is None:return Decision('prepare','cold',True),None
        if e.query is not query and e.query!=query:raise ValueError('cache key aliases different SQL')
        if p=='native_model':return Decision('reuse','native',False),None
        if p=='checkout':return Decision('prepare','checkout',True),None
        if e.hard_epoch!=self.hard_epoch:return Decision('prepare','barrier',True),None
        if p=='global_epoch':
            return (Decision('prepare','context_epoch',True),None) if e.context_epoch!=self.context_epoch else (Decision('reuse','stable',False),None)
        if p=='dependency':
            by=world.by_oid()
            changed=any(oid not in by or by[oid].version!=v for oid,v in e.certificate.bindings)
            return (Decision('prepare','positive_dependency',True),None) if changed else (Decision('reuse','positive_unchanged',False),None)
        if p in ('bindscope','prefix_strict','native_aware') and not e.dirty:
            return Decision('reuse','clean_witness',False),None
        if p=='prefix_strict':return Decision('prepare','dirty_witness',True),None
        current=self._resolve(world,query,path)
        if current.bindings==e.certificate.bindings:
            return Decision('reuse','validated_equal',True),current
        if p in ('native_aware','direct_native') and self._delegation_witness(e,world,path,current):
            return Decision('reuse','native_reanalysis',True),current
        return Decision('prepare','binding_changed',True),current

    @staticmethod
    def _delegation_witness(e,world,path,current):
        """Sufficient condition under the declared native contract, NOT an oracle.

        Uses the application's last successful binding/path and current catalog;
        never invokes Plan.native_outcome or a future engine execution. Shape
        equality is mandatory because some native objects retain a fixed result
        descriptor. Actual PostgreSQL activation requires a validated adapter.
        """
        if current.descriptor != e.certificate.descriptor:
            return False
        if e.native_path is not None and path != e.native_path:
            return True
        by=world.by_oid()
        return any(oid not in by or by[oid].version != version
                   for oid,version in e.certificate.bindings)

    def synchronize_watermark(self,watermark:int):
        """Called under the context gate with an AUTHORITATIVE watermark.

        A missed tail notification is detectable even without a later event.
        This method does not obtain a watermark or refresh a catalog from a DB.
        Its caller must do both; the model World is the authoritative source.
        """
        if not isinstance(watermark,int) or watermark < 0:
            raise ValueError('invalid context watermark')
        if self.last_sequence is None:
            self.last_sequence=watermark
        elif watermark < self.last_sequence:
            raise ValueError('authoritative watermark regressed')
        elif watermark != self.last_sequence:
            self.hard_epoch+=1
            self.context_epoch+=1
            self.last_sequence=watermark

    def execute(self,key,world,query,path):
        """Model backend. Caller must hold the read gate through this method."""
        self.synchronize_watermark(world.sequence)
        attempted=False
        try:
            decision,current=self.decide(key,world,query,path)
            if decision.action=='prepare':
                self.prepare_attempts+=1; attempted=True
                # Never retain failed prepares / negative query results.
                self.evict(key)
                if current is None:current=self._resolve(world,query,path)
                plan=Plan.fresh(world,query,path,current)
                e=Entry(query,plan.resolution,plan,self.context_epoch,self.hard_epoch,native_path=path)
                self._link(key,e)
            else:
                e=self.entries[key]
                if current is not None:
                    self._unlink(key); e.certificate=current; e.dirty=False; e.reasons.clear()
                    e.context_epoch=self.context_epoch; self._link(key,e)
            out=e.plan.execute(world,query,path)
            if out[0]=='error':self.evict(key)
            else:e.native_path=path
            return out,decision
        except MissingRelation:
            if not attempted:self.prepare_attempts+=1
            # A resolution failure is a fresh failed-prepare outcome, not reuse.
            self.evict(key)
            return ('error','42P01'),Decision('prepare_error','missing_relation',True)

    def assert_index_consistent(self):
        for key,e in self.entries.items():
            assert (key in self.path_keys)==e.certificate.path_sensitive
            for slot in e.certificate.slots: assert key in self.slot_index[slot]
            for schema in {s for s,_ in e.certificate.slots}: assert key in self.schema_index[schema]
        for slot,keys in self.slot_index.items():
            for key in keys: assert key in self.entries and slot in self.entries[key].certificate.slots
        for schema,keys in self.schema_index.items():
            for key in keys: assert key in self.entries and schema in {s for s,_ in self.entries[key].certificate.slots}
