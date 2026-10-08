"""Sequential fail-fast reproduction; no network or installation is performed.

The optional PostgreSQL run is REQUIRED to succeed when explicitly requested:
unavailability is an error exit, never a validation pass. Stable-context timing
is opt-in because host-dependent numbers will vary.
"""
from pathlib import Path
import subprocess,sys,time,json,hashlib
ROOT=Path(__file__).resolve().parents[1]
def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--timings',action='store_true');ap.add_argument('--postgres',action='store_true');a=ap.parse_args()
    commands=[('unit-tests',['-m','unittest','discover','-s','tests','-v'])]
    commands += [(n,['experiments/run_'+n+'.py']) for n in ('faults','exhaustive','histories','traces','catalog_sync','native_sqlite','binding_observation')]
    if a.timings:commands += [(n,['experiments/run_'+n+'.py']) for n in ('microbench','native_pool')]
    if a.postgres:commands += [('postgres_pilot',['experiments/run_postgres.py','--run'])]
    out=ROOT/'results';out.mkdir(exist_ok=True);log=[]
    manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['bindscope','experiments','tests','data'] for p in sorted((ROOT/folder).rglob('*')) if p.is_file() and '__pycache__' not in str(p)}
    def save():
        (out/'reproduction.json').write_text(json.dumps({'commands':log,'source_sha256':manifest,'statement':'Optional PostgreSQL unavailability is NOT an engine validation pass. Timings are steady-state host-dependent measurements.'},indent=2)+'\n')
    for name,cmd in commands:
        start=time.perf_counter()
        with (out/(name+'.log')).open('w') as stream:
            p=subprocess.run([sys.executable]+cmd,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        log.append(dict(command='python '+' '.join(cmd),returncode=p.returncode,seconds=time.perf_counter()-start))
        save();print(name,'exit',p.returncode,flush=True)
        if p.returncode:return p.returncode
    return 0
if __name__=='__main__':sys.exit(main())
