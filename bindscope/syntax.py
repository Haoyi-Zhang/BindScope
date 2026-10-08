"""Deliberately small SELECT parser; unsupported SQL fails closed.

Grammar: SELECT column-list | * | COUNT(*) | SUM(column)
FROM relation [alias] { [INNER] JOIN relation [alias] ON comparison }
[WHERE comparison {AND comparison}] [ORDER BY column {, column}].
Columns are optionally qualified; comparison operands are columns, integer
literals, or positional $N parameters. There are no implicit/user functions,
CTEs, strings, casts, subqueries, unions, writes, comments, or LIMIT.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

class UnsupportedSQL(ValueError):
    pass

@dataclass(frozen=True)
class Ref:
    name: str
    schema: str | None = None

@dataclass(frozen=True)
class Operand:
    kind: str
    value: str | int
    qualifier: str | None = None

@dataclass(frozen=True)
class Query:
    text: str
    relations: tuple[tuple[Ref, str], ...]
    outputs: tuple[Operand, ...]
    aggregate: str | None
    conditions: tuple[tuple[Operand, str, Operand], ...]
    order: tuple[Operand, ...]
    nparams: int

    @property
    def refs(self) -> tuple[Ref, ...]:
        return tuple(ref for ref, _ in self.relations)

    def bound_sql(self, oids: tuple[int, ...]) -> str:
        """Render only table names differently, for SQLite bridge checking."""
        if len(oids) != len(self.relations):
            raise ValueError('one binding required per relation occurrence')
        def op(x):
            if x.kind == 'star': return '*'
            if x.kind == 'param': return '?' + str(x.value)
            if x.kind == 'int': return str(x.value)
            return (f'"{x.qualifier}".' if x.qualifier else '') + f'"{x.value}"'
        out = ', '.join(op(x) for x in self.outputs)
        if self.aggregate: out = f'{self.aggregate}({out})'
        sql = 'SELECT ' + out + ' FROM ' + ', '.join(
            f'"r_{oid}" AS "{alias}"' for oid, (_, alias) in zip(oids, self.relations))
        if self.conditions:
            sql += ' WHERE ' + ' AND '.join(f'{op(a)} {c} {op(b)}' for a,c,b in self.conditions)
        if self.order: sql += ' ORDER BY ' + ', '.join(op(x) for x in self.order)
        return sql

_TOKEN = re.compile(r'\s*(\$[1-9][0-9]*|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|<=|>=|<>|!=|[.,()*=<>;-])')
_RESERVED = {'select','from','where','join','inner','on','and','order','by','as','count','sum'}

def parse(text: str) -> Query:
    tokens=[]; pos=0
    while pos < len(text):
        if not text[pos:].strip(): break
        m=_TOKEN.match(text,pos)
        if not m: raise UnsupportedSQL(f'unsupported token at byte {pos}')
        tokens.append(m.group(1).lower()); pos=m.end()
    if tokens and tokens[-1]==';': tokens.pop()
    i=0; nparams=0
    def peek(): return tokens[i] if i<len(tokens) else None
    def take(t=None):
        nonlocal i
        if i>=len(tokens) or (t is not None and tokens[i]!=t):
            raise UnsupportedSQL(f'expected {t}, got {peek()}')
        v=tokens[i]; i+=1; return v
    def ident():
        v=take()
        if not re.fullmatch(r'[a-z_][a-z_0-9]*',v) or v in _RESERVED:
            raise UnsupportedSQL(f'unsupported identifier {v}')
        return v
    def operand(star=False):
        nonlocal nparams
        if star and peek()=='*': take(); return Operand('star','*')
        if peek() and peek().startswith('$'):
            n=int(take()[1:]); nparams=max(nparams,n); return Operand('param',n)
        if peek()=='-' or (peek() and peek().isdigit()):
            s=-1 if peek()=='-' and take() else 1
            v=take()
            if not v.isdigit(): raise UnsupportedSQL('integer expected')
            return Operand('int',s*int(v))
        a=ident()
        if peek()=='.': take(); return Operand('column',ident(),a)
        return Operand('column',a)
    def comparison():
        a=operand(); op=take()
        if op not in ('=','<>','!=','<','>','<=','>='):
            raise UnsupportedSQL('comparison required')
        return a,op,operand()
    def relation():
        a=ident(); ref=Ref(a)
        if peek()=='.': take(); ref=Ref(ident(),a)
        alias=ref.name
        if peek()=='as': take(); alias=ident()
        elif peek() and peek() not in _RESERVED and re.fullmatch(r'[a-z_][a-z_0-9]*',peek()):
            alias=ident()
        return ref,alias
    take('select'); aggregate=None
    if peek() in ('count','sum'):
        aggregate=take().upper(); take('('); outputs=[operand(star=aggregate=='COUNT')]; take(')')
    else:
        outputs=[operand(star=True)]
        while peek()==',': take(); outputs.append(operand())
    if any(x.kind not in ('column','star') for x in outputs):
        raise UnsupportedSQL('output must be a column or supported aggregate')
    if len(outputs)>1 and any(x.kind=='star' for x in outputs):
        raise UnsupportedSQL('mixed star output unsupported')
    take('from'); relations=[relation()]; conditions=[]
    while peek() in ('join','inner'):
        if peek()=='inner': take()
        take('join'); relations.append(relation()); take('on'); conditions.append(comparison())
    if peek()=='where':
        take(); conditions.append(comparison())
        while peek()=='and': take(); conditions.append(comparison())
    order=[]
    if peek()=='order':
        take(); take('by'); order.append(operand())
        while peek()==',': take(); order.append(operand())
    if i != len(tokens): raise UnsupportedSQL(f'unsupported suffix {tokens[i:]}')
    aliases=[alias for _,alias in relations]
    if len(set(aliases))!=len(aliases): raise UnsupportedSQL('duplicate aliases')
    for x in (*outputs,*order,*(x for a,_,b in conditions for x in (a,b))):
        if x.qualifier and x.qualifier not in aliases:
            raise UnsupportedSQL('unknown qualifier')
    used={int(x.value) for a,_,b in conditions for x in (a,b) if x.kind=='param'}
    if used and used != set(range(1,nparams+1)):
        raise UnsupportedSQL('parameter numbers must be contiguous')
    return Query(text.strip(),tuple(relations),tuple(outputs),aggregate,tuple(conditions),tuple(order),nparams)
