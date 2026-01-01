"""Verify numerical table consistency, supplied input splits, and regenerated OBSERVE logs."""
import argparse,sys,json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from evaluation.paths import data_root,output_path
from evaluation.reproduce_comparison import add_ranks,build_wide,build_latex,build_markdown,METHOD_ORDER,DATASETS,METRICS

def run(inputs,comparison,regenerated,output):
    per=pd.read_csv(comparison/'comparison_per_run.csv');long=pd.read_csv(comparison/'comparison_long.csv')
    assert len(per)==200 and len(long)==100
    assert not per.duplicated(['method','dataset','run']).any() and np.isfinite(per[METRICS]).all().all()
    rows=[]
    for (method,ds),g in per.groupby(['method','dataset']):
        assert len(g)==10
        for metric in METRICS:rows.append({'metric':metric,'method':method,'dataset':ds,'mean':g[metric].mean(),'std':g[metric].std(ddof=0)})
    expected=add_ranks(pd.DataFrame(rows),list(DATASETS.values()),METHOD_ORDER)
    keys=['metric','method','dataset'];pd.testing.assert_frame_equal(long.sort_values(keys).reset_index(drop=True),expected.sort_values(keys).reset_index(drop=True),check_dtype=False,rtol=1e-12,atol=1e-10)
    wide=build_wide(long,list(DATASETS.values()),METHOD_ORDER)
    assert wide.to_csv()==(comparison/'comparison_wide.csv').read_text()
    assert build_markdown(wide,list(DATASETS.values()))+'\n'==(comparison/'comparison_table.md').read_text()
    assert build_latex(long,list(DATASETS.values()),list(DATASETS.values()),METHOD_ORDER)==(comparison/'comparison_table.tex').read_text()
    ours=long[long.method=='OBSERVE'];wins=sum(abs(r['mean']-long[(long.metric==r.metric)&(long.dataset==r.dataset)]['mean'].min())<1e-4 for _,r in ours.iterrows())
    rounded_wins=sum(round(r['mean'],2)==round(long[(long.metric==r.metric)&(long.dataset==r.dataset)]['mean'].min(),2) for _,r in ours.iterrows())
    splits=[];log_matches=[]
    for ds in DATASETS:
        train=pd.read_csv(inputs/'as_is/datasets'/ds/'train_preprocessed.csv',dtype={'case_id':str});test=pd.read_csv(inputs/'as_is/datasets'/ds/'test_preprocessed.csv',dtype={'case_id':str})
        overlap=len(set(train.case_id)&set(test.case_id));last=pd.to_datetime(train.end_timestamp,format='mixed',utc=True).max();first=pd.to_datetime(test.start_timestamp,format='mixed',utc=True).min()
        splits.append({'dataset':ds,'training_cases':train.case_id.nunique(),'test_cases':test.case_id.nunique(),'overlap_case_ids':overlap,'train_end_before_test_start':bool(last<=first),'training_end':last.isoformat(),'test_start':first.isoformat()})
        for i in range(10):
            ref=inputs/'as_is/reference/OBSERVE'/ds/f'simulated_log_{i}.csv';new=regenerated/ds/ref.name
            a=pd.read_csv(ref);b=pd.read_csv(new);pd.testing.assert_frame_equal(a,b,check_dtype=False,rtol=1e-12,atol=1e-10)
            log_matches.append({'dataset':ds,'run':i,'byte_identical':ref.read_bytes()==new.read_bytes(),'numerically_equivalent':True})
    ranks={r.metric:float(r.avg_rank) for _,r in ours.drop_duplicates('metric').iterrows()}
    report={'raw_logs_scored':200,'mean_std_cells':100,'table_representations_agree':5,'observe_average_ranks':ranks,'observe_lowest_cells_tolerance_0_0001':int(wins),'observe_lowest_cells_after_rounding_2dp':int(rounded_wins),'regenerated_logs_equivalent':40,'split_checks':splits,'regenerated_logs':log_matches}
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ['regenerated_logs','split_checks']},indent=2));return report
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-root',type=Path);p.add_argument('--comparison',type=Path,default=ROOT/'generated-results/as_is/comparison');p.add_argument('--regenerated',type=Path,default=ROOT/'generated-results/as_is/OBSERVE');p.add_argument('--output',type=Path,default=ROOT/'generated-results/as_is/verification');a=p.parse_args();root=data_root(a.data_root);out=output_path(a.output,root);out.mkdir(parents=True,exist_ok=True);run(root,a.comparison,a.regenerated,out)
