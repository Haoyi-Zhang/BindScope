"""Regenerate all principal paper tables/plots from saved measurements.

No performance number is synthesized or edited. Error bars are observed ranges,
not confidence intervals. The source records remain the reporting authority.
"""
from pathlib import Path
import argparse,csv,json,statistics as st,shutil
ROOT=Path(__file__).resolve().parents[1]
NAMES={'native_model':'Native model','checkout':'Checkout','dependency':'Dependency','global_epoch':'Global epoch','direct':'Direct','prefix_strict':'Strict prefix','bindscope':'BindScope','native_aware':'Native-aware','direct_native':'Direct-native'}
def rows(path):
    with path.open(newline='') as f:return list(csv.DictReader(f))
def aggregate(data,keys,order):
    return {p:{k:sum(int(r[k]) for r in data if r['policy']==p) for k in keys} for p in order}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--results',type=Path,default=ROOT/'results');ap.add_argument('--paper-dir',type=Path);a=ap.parse_args()
    r=a.results;o=r/'generated';o.mkdir(parents=True,exist_ok=True)
    def write(name,text):(o/name).write_text(text+'\n')
    def lines(name,rs):write(name,'\n'.join(rs))
    t=rows(r/'traces_summary.csv');h=rows(r/'histories_summary.csv');n=rows(r/'native_sqlite_summary.csv');p=rows(r/'native_pool.csv');m=rows(r/'microbench.csv')
    ta=aggregate(t,('executions','mismatches','prepares','resolutions','native_sufficient_prepares'),NAMES)
    na=aggregate(n,('executions','mismatches','read_mismatches','explicit_prepares','internal_reprepares','errors'),('native','checkout','dependency','global_epoch','direct','prefix_strict','bindscope'))
    assert all(ta[x]['mismatches']==0 for x in NAMES if x not in ('native_model','dependency'))
    assert all(na[x]['mismatches']==na[x]['read_mismatches']==0 for x in na if x not in ('native','dependency'))
    lines('model_rows.tex',[f"{NAMES[x]} & {ta[x]['mismatches']:,} & {ta[x]['prepares']:,} & {ta[x]['resolutions']:,} & {ta[x]['native_sufficient_prepares']:,} \\\\" for x in NAMES])
    lines('history_rows.tex',[f"{NAMES[x['policy']]} & {int(x['exact_mismatches']):,} & {int(x['prepares']):,} & {int(x['native_delegations']):,} \\\\" for x in h])
    lines('native_rows.tex',[f"{NAMES.get(x,'Native')} & {na[x]['mismatches']:,} & {na[x]['read_mismatches']:,} & {na[x]['explicit_prepares']:,} & {na[x]['internal_reprepares']:,} \\\\" for x in na])
    profs=('stable','sparse_ddl','shadow_heavy','path_churn','cancel_pairs','namespace_churn')
    lines('profile_rows.tex',[pr.replace('_',' ')+' & '+' & '.join(f"{sum(int(x['prepares']) for x in t if x['profile']==pr and x['policy']==pol):,}" for pol in ('global_epoch','prefix_strict','bindscope','native_aware'))+r' \\' for pr in profs])
    # Table includes all three contention settings; every policy and workload.
    poolstats={}
    for b in (1,4,8):
        rs=[]
        for w in ('point','aggregate','join4','join12'):
            cells=[]
            for pol in ('native','checkout','direct','bindscope'):
                vals=[float(x['throughput'])/1000 for x in p if int(x['borrowers'])==b and x['workload']==w and x['policy']==pol]
                assert len(vals)==5
                stats={'median':st.median(vals),'min':min(vals),'max':max(vals)};poolstats[f'{b}/{w}/{pol}']=stats
                cells.append('%.2f [%.2f, %.2f]'%(stats['median'],stats['min'],stats['max']))
            rs.append(f'{b}/{1 if b==1 else 2} & {w} & '+' & '.join(cells)+r' \\')
        lines(f'pool_{b}_rows.tex',rs)
    lines('micro_rows.tex',[f"{size:,} & {length} & {deps} & "+' & '.join('%.2f [%.2f, %.2f]'%(st.median(v),min(v),max(v)) for v in [[float(x['ns_per_op'])/1000 for x in m if x['mode']=='clean_decide' and int(x['cache'])==size and int(x['path'])==length and int(x['deps'])==deps and x['policy']==pol] for pol in ('bindscope','direct')])+r' \\' for size,length,deps in ((64,2,1),(1024,8,1),(4096,8,4))])
    macro={'ModelPrepares':ta['bindscope']['prepares'],'NativeAwarePrepares':ta['native_aware']['prepares'],'ModelResolves':ta['bindscope']['resolutions'],'EnginePrepares':na['bindscope']['explicit_prepares']}
    lines('metrics.tex',['\\newcommand{\\'+k+'}{'+f'{v:,}'+'}' for k,v in macro.items()])
    write('summary.json',json.dumps({'model':ta,'engine':na,'pool_1000_queries_per_second':poolstats},indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    # Serif labels match scientific typesetting; the installed publication font
    # is preferred, with ordinary serif fallbacks on machines without TeX fonts.
    plt.rcParams.update({'font.family': 'serif',
        'font.serif': ['Linux Libertine O', 'Libertinus Serif', 'Times New Roman', 'DejaVu Serif'],
        'pdf.fonttype': 42, 'ps.fonttype': 42,
        'axes.prop_cycle': plt.cycler(color=['#235789', '#9b4c24', '#717171']),
        'axes.linewidth': .65, 'lines.linewidth': 1.1,
        'legend.frameon': False, 'xtick.major.width': .65, 'ytick.major.width': .65})
    def finish(fig,name):
        fig.tight_layout();fig.savefig(o/(name+'.pdf'),bbox_inches='tight');fig.savefig(o/(name+'.png'),dpi=180,bbox_inches='tight');plt.close(fig)
    fig,ax=plt.subplots(figsize=(3.5,2.15))
    xx=np.arange(len(profs));width=.24
    for i,pol in enumerate(('prefix_strict','bindscope','native_aware')):
        yy=[sum(int(x['prepares']) for x in t if x['profile']==pr and x['policy']==pol)/16000 for pr in profs]
        ax.bar(xx+(i-1)*width,yy,width,label=NAMES[pol])
    ax.set_xticks(xx,['Stable','Sparse','Shadow','Path','Cancel','Schema'],rotation=25,ha='right',fontsize=8)
    ax.set_ylabel('Prepare fraction',fontsize=9);ax.tick_params(labelsize=8);ax.legend(fontsize=7,loc='upper left');ax.set_ylim(0,1.05)
    finish(fig,'model_prepares')
    fig,ax=plt.subplots(figsize=(3.5,2.2))
    for pol in ('bindscope','direct'):
        cache=(64,1024,4096); vv=[[float(x['ns_per_op'])/1000 for x in m if x['mode']=='clean_decide' and int(x['cache'])==c and int(x['path'])==8 and int(x['deps'])==4 and x['policy']==pol] for c in cache]
        med=np.array([st.median(v) for v in vv]);err=np.array([[med[i]-min(v) for i,v in enumerate(vv)],[max(v)-med[i] for i,v in enumerate(vv)]])
        ax.errorbar(cache,med,yerr=err,marker='o',capsize=3,label=NAMES[pol])
    ax.set_xscale('log',base=2);ax.set_xticks(cache,[str(c) for c in cache]);ax.set_xlabel('Cached statements',fontsize=9);ax.set_ylabel(r'Decision time ($\mu$s)',fontsize=9);ax.tick_params(labelsize=8);ax.legend(fontsize=8)
    finish(fig,'clean_guard')
    fig,ax=plt.subplots(figsize=(3.5,2.2))
    for mode,label in [('irrelevant_event','Unwatched slot'),('all_watchers_event','Slot watched by all entries')]:
        cache=(64,1024,4096); vv=[[float(x['ns_per_op'])/1000 for x in m if x['mode']==mode and int(x['cache'])==c] for c in cache]
        med=np.array([st.median(v) for v in vv]);err=np.array([[med[i]-min(v) for i,v in enumerate(vv)],[max(v)-med[i] for i,v in enumerate(vv)]])
        ax.errorbar(cache,med,yerr=err,marker='o',capsize=3,label=label)
    ax.set_xscale('log',base=2);ax.set_yscale('log');ax.set_xticks(cache,[str(c) for c in cache]);ax.set_xlabel('Cached statements',fontsize=9);ax.set_ylabel(r'Marking time ($\mu$s)',fontsize=9);ax.tick_params(labelsize=8);ax.legend(fontsize=7,loc='upper left')
    finish(fig,'event_fanout')
    tables = {
        'history_table': ('@{}lrrr@{}', 'Policy & Mismatches & Prepares & Delegations', ['history_rows.tex']),
        'model_table': ('lrrrr', 'Policy & Binding mismatches & Explicit prepares & Resolution calls & Native-sufficient prepares', ['model_rows.tex']),
        'native_table': ('@{}lrrrr@{}', 'Policy & Result & Reads & Prepares & Internal', ['native_rows.tex']),
        'pool_table': ('@{}llrrrr@{}', 'B/S & Query & Native & Checkout & Direct & BindScope', ['pool_1_rows.tex','pool_4_rows.tex','pool_8_rows.tex'])}
    for name,(fmt,header,files) in tables.items():
        write(name+'.tex',r'\begin{tabular}{'+fmt+r'}\toprule'+'\n'+header+r'\\\midrule'+'\n'+ ('\n'+r'\midrule'+'\n').join((o/f).read_text() for f in files)+r'\bottomrule\end{tabular}')
    if a.paper_dir:
        dest=a.paper_dir/'generated';dest.mkdir(parents=True,exist_ok=True)
        # Remove superseded generated outputs; only one reporting lineage.
        for f in dest.iterdir():
            if f.is_file():f.unlink()
        for f in o.iterdir():
            if f.name.endswith('_table.tex') or f.name in {'model_rows.tex','history_rows.tex','native_rows.tex','profile_rows.tex','pool_1_rows.tex','pool_4_rows.tex','pool_8_rows.tex','micro_rows.tex','metrics.tex','summary.json','model_prepares.pdf','clean_guard.pdf','event_fanout.pdf'}:shutil.copy2(f,dest/f.name)
    print('reported',len(na),'actual engine policies;',len(p),'pool measurements')
if __name__=='__main__':main()
