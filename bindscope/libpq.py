"""Minimal synchronous libpq wrapper using the system library, no pip needed.

Integration requires a real PostgreSQL server. SQL errors are surfaced, not
converted to synthetic success. Only text parameters/results are exposed.
"""
from __future__ import annotations
import ctypes as C
from ctypes.util import find_library

class PGError(RuntimeError):
    def __init__(self,message,sqlstate=None):super().__init__(message);self.sqlstate=sqlstate

class Connection:
    def __init__(self,dsn:str):
        name=find_library('pq')
        if not name:raise PGError('system libpq not installed')
        self.lib=C.CDLL(name);L=self.lib;P=C.c_void_p;S=C.c_char_p;I=C.c_int
        signatures={
            'PQconnectdb':([S],P),'PQstatus':([P],I),'PQerrorMessage':([P],S),'PQfinish':([P],None),
            'PQexec':([P,S],P),'PQprepare':([P,S,S,I,C.POINTER(C.c_uint)],P),
            'PQexecPrepared':([P,S,I,C.POINTER(S),C.POINTER(I),C.POINTER(I),I],P),
            'PQresultStatus':([P],I),'PQresultErrorMessage':([P],S),'PQresultErrorField':([P,I],S),
            'PQntuples':([P],I),'PQnfields':([P],I),'PQfname':([P,I],S),'PQftype':([P,I],C.c_uint),
            'PQgetisnull':([P,I,I],I),'PQgetvalue':([P,I,I],S),'PQclear':([P],None),
            'PQserverVersion':([P],I),'PQlibVersion':([],I),'PQbackendPID':([P],I)}
        for fn,(a,r) in signatures.items():getattr(L,fn).argtypes=a;getattr(L,fn).restype=r
        self.conn=L.PQconnectdb(dsn.encode());self.closed=False
        if not self.conn or L.PQstatus(self.conn)!=0:
            message=L.PQerrorMessage(self.conn).decode(errors='replace') if self.conn else 'null PGconn'
            if self.conn:L.PQfinish(self.conn)
            self.closed=True;raise PGError(message)
        self.server_version=L.PQserverVersion(self.conn);self.backend_pid=L.PQbackendPID(self.conn)
    def _result(self,res):
        if not res:raise PGError('libpq returned null result')
        L=self.lib
        try:
            status=L.PQresultStatus(res)
            if status not in (1,2):
                state=L.PQresultErrorField(res,ord('C'))
                raise PGError(L.PQresultErrorMessage(res).decode(errors='replace'),state.decode() if state else None)
            n=L.PQnfields(res);names=tuple(L.PQfname(res,j).decode() for j in range(n));types=tuple(L.PQftype(res,j) for j in range(n))
            rows=tuple(tuple(None if L.PQgetisnull(res,i,j) else L.PQgetvalue(res,i,j).decode() for j in range(n)) for i in range(L.PQntuples(res)))
            return names,types,rows
        finally:L.PQclear(res)
    def execute(self,sql):
        if self.closed:raise PGError('connection closed')
        return self._result(self.lib.PQexec(self.conn,sql.encode()))
    def prepare(self,name,sql,param_types=()):
        if self.closed:raise PGError('connection closed')
        a=(C.c_uint*len(param_types))(*param_types) if param_types else None
        return self._result(self.lib.PQprepare(self.conn,name.encode(),sql.encode(),len(param_types),a))
    def prepared(self,name,params=()):
        if self.closed:raise PGError('connection closed')
        values=[None if p is None else str(p).encode() for p in params]
        a=(C.c_char_p*len(values))(*values) if values else None
        return self._result(self.lib.PQexecPrepared(self.conn,name.encode(),len(values),a,None,None,0))
    def close(self):
        if not self.closed:self.lib.PQfinish(self.conn);self.closed=True
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def identifier(name):
    if '\x00' in name:raise ValueError('NUL in identifier')
    return '"'+name.replace('"','""')+'"'

def literal(value):
    if '\x00' in value:raise ValueError('NUL in literal')
    return "'"+value.replace("'","''")+"'"
