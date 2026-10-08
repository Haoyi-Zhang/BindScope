"""Explicit finite relation-binding model, NOT a PostgreSQL emulator."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable
from .syntax import Query, Ref

Slot=tuple[str,str]
class MissingRelation(LookupError):
    sqlstate='42P01'

@dataclass
class Relation:
    oid: int
    version: int = 0
    stats: int = 0
    columns: tuple[str,...] = ('id','value')
    rows: tuple[tuple[int,...],...] = ()

@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    slots: tuple[Slot,...] = ()
    session: str | None = None
    schema: str | None = None

@dataclass(frozen=True)
class Resolution:
    bindings: tuple[tuple[int,int],...]
    slots: frozenset[Slot]
    path_sensitive: bool
    descriptor: tuple[str,...] = ()

    @property
    def oids(self): return tuple(x[0] for x in self.bindings)

@dataclass
class World:
    catalog: dict[Slot,Relation] = field(default_factory=dict)
    next_oid: int = 1
    sequence: int = 0

    def event(self, kind, slots=(), session=None, schema=None):
        self.sequence+=1
        return Event(self.sequence,kind,tuple(slots),session,schema)

    def create(self, schema, name, *, base=0, columns=('id','value')):
        slot=(schema,name)
        if slot in self.catalog: raise ValueError('slot already occupied')
        oid=self.next_oid; self.next_oid+=1
        rows=tuple(tuple([i,base+i]+[base+i+j for j in range(2,len(columns))]) for i in range(1,9))
        self.catalog[slot]=Relation(oid,columns=tuple(columns),rows=rows)
        return self.event('ddl',(slot,))

    def drop(self, schema, name):
        self.catalog.pop((schema,name),None)
        return self.event('ddl',((schema,name),))

    def alter(self, schema, name, add_column=False):
        r=self.catalog[(schema,name)]; r.version+=1
        if add_column:
            r.columns+=('extra'+str(r.version),)
            r.rows=tuple(row+(r.version,) for row in r.rows)
        return self.event('ddl',((schema,name),))

    def analyze(self, schema, name):
        self.catalog[(schema,name)].stats+=1
        return self.event('stats',((schema,name),))

    def data(self, schema, name, delta=1):
        r=self.catalog[(schema,name)]
        r.rows=tuple((row[0],row[1]+delta)+row[2:] for row in r.rows)
        return self.event('data',((schema,name),))

    def move(self, source:Slot, dest:Slot):
        if dest in self.catalog: raise ValueError('destination occupied')
        self.catalog[dest]=self.catalog.pop(source)
        self.catalog[dest].version+=1
        return self.event('ddl',(source,dest))

    def by_oid(self): return {r.oid:r for r in self.catalog.values()}


def resolve(world:World, query:Query, path:tuple[str,...]) -> Resolution:
    """Guard's forward resolution and minimal slot witness."""
    bindings=[]; selected=[]; slots=set(); sensitive=False
    for ref in query.refs:
        schemas=(ref.schema,) if ref.schema is not None else path
        sensitive |= ref.schema is None
        for schema in schemas:
            slot=(schema,ref.name); slots.add(slot)
            if slot in world.catalog:
                r=world.catalog[slot]; bindings.append((r.oid,r.version)); selected.append(r); break
        else: raise MissingRelation(ref)
    descriptor=describe(query,selected)
    return Resolution(tuple(bindings),frozenset(slots),sensitive,descriptor)


def resolve_direct(world:World, query:Query, path:tuple[str,...]) -> Resolution:
    """Strong nonincremental baseline: no absent-slot witnesses or reverse index."""
    bindings=[];selected=[]
    for ref in query.refs:
        schemas=(ref.schema,) if ref.schema is not None else path
        for schema in schemas:
            r=world.catalog.get((schema,ref.name))
            if r is not None:
                bindings.append((r.oid,r.version));selected.append(r);break
        else:raise MissingRelation(ref)
    return Resolution(tuple(bindings),frozenset(),False,describe(query,selected))


def oracle_binding(world:World, query:Query, path:tuple[str,...]):
    """Independent catalog-first, rank-minimization oracle (no resolve call)."""
    out=[]
    for ref in query.refs:
        candidates=[]
        for (schema,name),rel in world.catalog.items():
            if name!=ref.name: continue
            if ref.schema is not None:
                if schema==ref.schema: candidates.append((0,rel.oid,rel.version))
            elif schema in path:
                candidates.append((path.index(schema),rel.oid,rel.version))
        if not candidates: return ('error','42P01')
        _,oid,version=min(candidates); out.append((oid,version))
    by=world.by_oid()
    return ('ok',tuple(out),describe(query,[by[oid] for oid,_ in out]))

@dataclass
class Plan:
    resolution: Resolution
    path: tuple[str,...]
    native_versions: tuple[tuple[int,int,int],...]

    @classmethod
    def fresh(cls,world,query,path,resolution=None):
        if resolution is None: resolution=resolve(world,query,path)
        by=world.by_oid()
        stamps=tuple((oid,by[oid].version,by[oid].stats) for oid in resolution.oids)
        return cls(resolution,path,stamps)

    def native_outcome(self,world,query,path):
        """Document-motivated lazy positive-dependency policy; not PG code."""
        by=world.by_oid()
        changed=path!=self.path or any(
            oid not in by or (by[oid].version,by[oid].stats)!=(v,s)
            for oid,v,s in self.native_versions)
        if changed:
            out=oracle_binding(world,query,path)
            if out[0]=='ok' and out[2]!=self.resolution.descriptor:
                return ('error','0A000'),False
            return out,True
        return ('ok',self.resolution.bindings,self.resolution.descriptor),False

    def execute(self,world,query,path):
        out,rebound=self.native_outcome(world,query,path)
        if rebound and out[0]=='ok':
            other=Plan.fresh(world,query,path)
            self.resolution=other.resolution; self.path=other.path
            self.native_versions=other.native_versions
        return out


def identity(out):
    """Ignore conservative version stamps, but never ignore relation identity."""
    return ('ok',tuple(x[0] for x in out[1]),out[2]) if out[0]=='ok' else out


def describe(query,relations):
    """Result-name descriptor for the declared integer-table fragment.

    Workload construction guarantees well-formed columns. This function does
    not make the name-binding oracle an independent SQL semantic oracle.
    """
    if query.aggregate:return (query.aggregate.lower(),)
    if query.outputs[0].kind=='star':
        return tuple(col for rel in relations for col in rel.columns)
    return tuple(str(x.value) for x in query.outputs)
