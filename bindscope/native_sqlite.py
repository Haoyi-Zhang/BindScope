"""Small explicit SQLite C-API client for engine negative controls.

Unlike sqlite_oracle.py, this module submits ORIGINAL SQL text and retains real
sqlite3_stmt handles. It never rewrites table names or calls the binding model.
All code is local; the client must not share a handle between simultaneous users.
"""
from __future__ import annotations
import ctypes as C
import ctypes.util
import sys
from pathlib import Path
from typing import Sequence

class SQLiteError(RuntimeError):
    def __init__(self, code:int, message:str):
        super().__init__(message); self.code=code

_libname=None
if sys.platform == 'win32':
    # Use the DLL shipped with this interpreter, not an unrelated sqlite3.dll
    # found on PATH (for example, a command-line client's private dependency).
    bundled = Path(sys.base_prefix) / 'DLLs' / 'sqlite3.dll'
    if bundled.is_file():
        _libname = str(bundled)
if not _libname:
    _libname=ctypes.util.find_library('sqlite3')
if not _libname: raise ImportError('system libsqlite3 is required')
lib=C.CDLL(_libname)
P=C.c_void_p; I=C.c_int; S=C.c_char_p

def _fn(name,args,result):
    f=getattr(lib,name);f.argtypes=args;f.restype=result;return f
_open=_fn('sqlite3_open',[S,C.POINTER(P)],I)
_close=_fn('sqlite3_close',[P],I)
_err=_fn('sqlite3_errmsg',[P],S)
_exec=_fn('sqlite3_exec',[P,S,P,P,C.POINTER(S)],I)
_free=_fn('sqlite3_free',[P],None)
_prepare=_fn('sqlite3_prepare_v2',[P,S,I,C.POINTER(P),C.POINTER(S)],I)
_finalize=_fn('sqlite3_finalize',[P],I)
_step=_fn('sqlite3_step',[P],I)
_reset=_fn('sqlite3_reset',[P],I)
_clear=_fn('sqlite3_clear_bindings',[P],I)
_nparams=_fn('sqlite3_bind_parameter_count',[P],I)
_bindint=_fn('sqlite3_bind_int64',[P,I,C.c_int64],I)
_ncols=_fn('sqlite3_column_count',[P],I)
_name=_fn('sqlite3_column_name',[P,I],S)
_decl=_fn('sqlite3_column_decltype',[P,I],S)
_type=_fn('sqlite3_column_type',[P,I],I)
_int=_fn('sqlite3_column_int64',[P,I],C.c_int64)
_float=_fn('sqlite3_column_double',[P,I],C.c_double)
_text=_fn('sqlite3_column_text',[P,I],P)
_blob=_fn('sqlite3_column_blob',[P,I],P)
_bytes=_fn('sqlite3_column_bytes',[P,I],I)
_status=_fn('sqlite3_stmt_status',[P,I,I],I)
_version=_fn('sqlite3_libversion',[],S)
_AUTH=C.CFUNCTYPE(I,P,I,S,S,S,S)
_authorizer=_fn('sqlite3_set_authorizer',[P,_AUTH,P],I)

def version(): return _version().decode('utf-8')
def quote(name:str):
    if '\x00' in name:raise ValueError('NUL identifier')
    return '"'+name.replace('"','""')+'"'
def literal(text:str):
    if '\x00' in text:raise ValueError('NUL literal')
    return "'"+text.replace("'","''")+"'"

class Connection:
    def __init__(self,filename:str=':memory:',observe_reads:bool=False):
        self.handle=P();self.statements=set();self.prepare_calls=0;self.closed=False
        self.observe_reads=observe_reads;self.read_log=[];self._auth_callback=None
        rc=_open(filename.encode(),C.byref(self.handle))
        if rc:
            message=_err(self.handle).decode();_close(self.handle)
            raise SQLiteError(rc,message)
        if observe_reads:
            # Install ONCE, before preparing any statements. Replacing an
            # authorizer between uses would itself change the experiment.
            # This is instrumentation on a newly created, unrestricted private
            # test DB, NOT a replacement for an application's security policy.
            def record(unused,action,table,column,database,trigger):
                if action==20:  # SQLITE_READ, including count(*)'s empty column.
                    self.read_log.append(tuple((x or b'').decode('utf-8') for x in (database,table,column)))
                return 0  # Preserve this private test DB's default authorization.
            self._auth_callback=_AUTH(record)
            rc=_authorizer(self.handle,self._auth_callback,None)
            if rc:raise SQLiteError(rc,_err(self.handle).decode())
        self.execute('PRAGMA busy_timeout=5000')
    def _check(self):
        if self.closed:raise RuntimeError('connection is closed')
    def execute(self,sql:str):
        self._check()
        if '\x00' in sql:raise ValueError('NUL SQL')
        error=S();rc=_exec(self.handle,sql.encode(),None,None,C.byref(error))
        if rc:
            message=error.value.decode() if error.value else _err(self.handle).decode()
            if error: _free(C.cast(error,P))
            raise SQLiteError(rc,message)
    def prepare(self,sql:str):
        self._check();self.prepare_calls+=1
        return Statement(self,sql)
    def query(self,sql:str,params:Sequence[int]=()):
        with self.prepare(sql) as statement:return statement.run(params)[0]
    def close(self):
        if self.closed:return
        for stmt in list(self.statements):stmt.close()
        rc=_close(self.handle)
        if rc:raise SQLiteError(rc,_err(self.handle).decode())
        self.closed=True
    def __enter__(self):return self
    def __exit__(self,*exc):self.close()

class Statement:
    def __init__(self,connection:Connection,sql:str):
        if '\x00' in sql:raise ValueError('NUL SQL')
        self.connection=connection;self.handle=P();tail=S();self.closed=False
        connection.read_log.clear()
        # sqlite3_prepare_v2 returns tail pointing INTO the supplied SQL buffer.
        # Keep that buffer alive until tail inspection (a temporary .encode()
        # argument can be freed before .value is read by ctypes).
        sql_buffer=sql.encode()
        rc=_prepare(connection.handle,sql_buffer,-1,C.byref(self.handle),C.byref(tail))
        self.reads=tuple(sorted(set(connection.read_log)))
        if rc:raise SQLiteError(rc,_err(connection.handle).decode())
        if not self.handle:raise ValueError('empty SQL')
        if tail.value and tail.value.strip():
            _finalize(self.handle);raise ValueError('exactly one SQL statement required')
        connection.statements.add(self)
    def run(self,params:Sequence[int]=()):
        self.connection._check()
        if self.closed:raise RuntimeError('statement is finalized')
        if len(params)!=_nparams(self.handle):raise ValueError('parameter count differs')
        _reset(self.handle);_clear(self.handle);_status(self.handle,5,1)
        for i,v in enumerate(params,1):
            if not isinstance(v,int):raise TypeError('this experimental client binds integers only')
            if not -(1<<63)<=v<(1<<63):raise OverflowError('not an int64')
            rc=_bindint(self.handle,i,v)
            if rc:raise SQLiteError(rc,_err(self.connection.handle).decode())
        self.connection.read_log.clear()
        rows=[];descriptor=None
        while True:
            rc=_step(self.handle)
            if rc not in (100,101):
                error=SQLiteError(rc,_err(self.connection.handle).decode());_reset(self.handle)
                raise error
            # Recompilation can change columns, so capture AFTER the first step.
            if descriptor is None:
                descriptor=tuple(((_name(self.handle,i) or b'').decode(),(_decl(self.handle,i) or b'').decode()) for i in range(_ncols(self.handle)))
            if rc==101:break
            row=[]
            for i in range(_ncols(self.handle)):
                typ=_type(self.handle,i)
                if typ==1:value=int(_int(self.handle,i))
                elif typ==2:value=float(_float(self.handle,i))
                elif typ==5:value=None
                else:
                    pointer=(_text if typ==3 else _blob)(self.handle,i)
                    raw=C.string_at(pointer,_bytes(self.handle,i)) if pointer else b''
                    value=raw.decode('utf-8') if typ==3 else raw.hex()
                row.append((typ,value))
            rows.append(tuple(row))
        reparses=_status(self.handle,5,1)
        if self.connection.read_log:self.reads=tuple(sorted(set(self.connection.read_log)))
        _reset(self.handle)
        return ('ok',descriptor,tuple(rows)),reparses
    @property
    def read_relations(self):
        return tuple(sorted(set((db,table) for db,table,column in self.reads if db)))
    def close(self):
        if not self.closed:
            _finalize(self.handle);self.closed=True;self.connection.statements.discard(self)
    def __enter__(self):return self
    def __exit__(self,*exc):self.close()

def canonical(out,ordered=False):
    if out[0]!='ok':return out
    return out if ordered else (out[0],out[1],tuple(sorted(out[2],key=repr)))
