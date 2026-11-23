"""One reproducible run: build, controls, locked reference session, real two-turn model."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import uuid

from medical_cf.run import save
from .build import build,DEFAULT_CASE,DEFAULT_PROFILE,digest
from .checks import controls
from .session import Session
from .evaluate import evaluate
from .model import run as model_run


def run(output,case=DEFAULT_CASE,profile=DEFAULT_PROFILE,real_model=True,medical_check=False):
    output=Path(output).resolve()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:6]
    root=output/'runs'/stamp;root.mkdir(parents=True)
    package=root/'package'
    summary={'run':str(root),'build':build(package,case,profile)}
    print('PACKAGE',package,'GOALS',summary['build'],flush=True)
    summary['oracle_controls']=controls(package,root/'oracle_controls')
    reference=Session(package,root/'reference_session');reference.start()
    a=json.loads((package/'private/reference_stage1.json').read_text())
    b=json.loads((package/'private/reference_stage2.json').read_text())
    reference.commit(1,a);reference.reveal();reference.commit(2,b);reference.verify_locked()
    reference_report=evaluate(package,a,b,root/'reference_session/delivered')
    if not reference_report['pass']:raise AssertionError(reference_report)
    save(root/'reference_session/evaluation.json',reference_report)
    summary['reference_pass']=reference_report['pass']
    if medical_check:
        from medical_cf.upstream import REPO
        med=root/'medical_package'
        summary['medical_reuse']={'build':build(med,REPO/'dataset/medical/case1',Path(__file__).with_name('medical.json'))}
        med_a=json.loads((med/'private/reference_stage1.json').read_text())
        med_b=json.loads((med/'private/reference_stage2.json').read_text())
        med_report=evaluate(med,med_a,med_b,root/'medical_reference/delivered')
        save(root/'medical_reference/evaluation.json',med_report)
        if not med_report['pass']:raise AssertionError(med_report)
        summary['medical_reuse']['controls']=controls(med,root/'medical_controls')
        summary['medical_reuse']['reference_pass']=med_report['pass']
        summary['medical_reuse']['real_model_executed']=False
    if real_model:
        report=model_run(package,root/'model')
        summary['real_model']={'executed':True,'pass':report['pass'],'stage2_pass':report['stage2_pass'],'summary':report['summary']}
    else:
        summary['real_model']={'executed':False,'reason':'Explicit --skip-model; does not count as real-model validation'}
    summary['input_files_unchanged']=all(digest(x['path'])==x['sha256'] for x in json.loads((package/'private/input_manifest.json').read_text())['files'])
    if not summary['input_files_unchanged']:raise AssertionError('Upstream source data changed')
    save(root/'summary.json',summary);save(output/'latest.json',{'run':str(root)})
    print('RUN_SUMMARY',root/'summary.json',flush=True)
    return root


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=Path(__file__).with_name('artifacts'))
    p.add_argument('--case',type=Path,default=DEFAULT_CASE);p.add_argument('--profile',type=Path,default=DEFAULT_PROFILE)
    p.add_argument('--skip-model',action='store_true')
    p.add_argument('--medical-check',action='store_true')
    a=p.parse_args();run(a.out,a.case,a.profile,not a.skip_model,a.medical_check)


if __name__=='__main__':main()
