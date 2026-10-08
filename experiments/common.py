from __future__ import annotations
import json, platform, sys, os, time, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bindscope.syntax import parse
from bindscope.model import World

def queries():
    texts=[
        'SELECT value FROM accounts WHERE id = $1',
        'SELECT id, value FROM world WHERE id = $1',
        'SELECT SUM(value) FROM accounts WHERE id >= $1',
        'SELECT COUNT(*) FROM orders WHERE value > $1',
        'SELECT a.value, b.value FROM accounts a JOIN branches b ON a.id = b.id WHERE a.id = $1',
        'SELECT a.value, b.value FROM accounts a JOIN accounts b ON a.id = b.id WHERE a.id = $1',
        'SELECT value FROM base.accounts WHERE id = $1',
        'SELECT a.value, b.value FROM base.accounts a JOIN branches b ON a.id = b.id WHERE a.id = $1',
        'SELECT * FROM accounts WHERE id = $1',
        'SELECT value FROM customers WHERE id >= $1 ORDER BY value',
        'SELECT a.value, b.value, c.value FROM orders a JOIN items b ON a.id = b.id JOIN stock c ON b.id = c.id WHERE a.id = $1',
        'SELECT value FROM tellers WHERE id <= $1',
    ]
    return [parse(t) for t in texts]

def initial_world():
    w=World()
    for i,name in enumerate(['accounts','branches','tellers','world','orders','items','customers','stock']):
        w.create('base',name,base=i*100)
        w.create('late',name,base=1000+i*100)
    return w

def write_json(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')

def environment():
    import sqlite3
    out={'python':sys.version,'platform':platform.platform(),'sqlite':sqlite3.sqlite_version,
         'cpu_count':os.cpu_count(),'clock':time.get_clock_info('perf_counter')._asdict() if hasattr(time.get_clock_info('perf_counter'),'_asdict') else vars(time.get_clock_info('perf_counter'))}
    for f,key in [('/sys/fs/cgroup/cpu.max','cgroup_cpu_max'),('/sys/fs/cgroup/memory.max','cgroup_memory_max')]:
        try:out[key]=Path(f).read_text().strip()
        except OSError:pass
    try:
        lines=Path('/proc/cpuinfo').read_text().splitlines()
        out['cpu_model']=next(x.split(':',1)[1].strip() for x in lines if x.startswith('model name'))
    except (OSError,StopIteration):pass
    return out
