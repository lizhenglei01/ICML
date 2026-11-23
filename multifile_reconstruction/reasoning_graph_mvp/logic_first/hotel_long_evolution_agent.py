"""Run paired standard-start and carried hotel milestones M4–M8."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from alignment_probe.codex_transport import configured_route

from . import hotel_agent
from .hotel_graph import read_json, save_json
from .hotel_long_evolution import DEFAULT, EARLIER_RUN, EARLIER_SUBMISSION, prepare_stage
from .hotel_revenue_milestones_agent import attribute_changes, freeze_carry


MODEL = 'gpt-6.2-sol'


def evaluate(label, frozen, output, route, timeout):
    package = Path(frozen['package'])
    directory = output/label
    oracle = read_json(package/'private/oracle.json')
    record = hotel_agent.run_layout(label,frozen,output,route,'medium',timeout,False)
    result = directory/'native_evaluation.json'
    notices = record['trace']['runtime_events']
    compatibility_only = bool(notices) and all(
        item.get('type')=='item.completed' and item.get('item',{}).get('message','').startswith(
            f'Model metadata for `{route["model"]}` not found. Defaulting to fallback metadata;') for item in notices)
    if (record['status']=='RUN_ERROR' and record['returncode']==0 and record['trace']['usage'] and compatibility_only
            and record['originals_and_oracle_unchanged'] and record['reference_unchanged'] and not record['missing_office_files']):
        graded = hotel_agent.grade(directory/'submission',oracle)
        result = directory/'submitted_files_rescore.json'
        save_json(result,graded)
        record.update(raw_status='RUN_ERROR',status='COMPLETED',native_pass=graded['pass'],score_groups=graded['groups'])
    if result.is_file():
        end = read_json(result)
        start = hotel_agent.grade(package/'public',oracle)
        metadata = read_json(package/'private/milestone.json')
        record['attribution'] = attribute_changes(start,end,oracle,metadata)
        checks = {row['object']:row['pass'] for row in end['checks']}
        groups = {}
        for identifier, origin in metadata['formula_check_stage'].items():
            stats = groups.setdefault(str(origin),{'pass':0,'total':0,'failures':[]})
            stats['total'] += 1
            if checks.get(identifier,False):
                stats['pass'] += 1
            else:
                stats['failures'].append(identifier)
        record['functional_by_milestone'] = groups
    return record


def run(source, output, timeout, comparison, *, carry_only=False):
    if not read_json(source/'summary.json')['passed']:
        raise ValueError('Validate the build before model calls')
    preceding = read_json(EARLIER_RUN/'publication_rescore.json')
    existing = preceding['results']['stage_03_carry_forward']
    if not existing['pass']:
        raise ValueError('The archived actual predecessor has not passed its updated checks')
    hashes = read_json(EARLIER_RUN/'results.json')['stage_03_carry_forward']['submission_sha256']
    for name in hotel_agent.OFFICE_FILES:
        path = EARLIER_SUBMISSION/name
        if hashes[name] != hotel_agent.digest(path):
            raise ValueError('The frozen earlier model result changed')
    route = configured_route()
    if route['provider']!='openai' or not route['use_saved_chatgpt_login'] or route['model']!=MODEL:
        raise RuntimeError('Use the official GPT-6.2-sol route')
    packages = {stage:source/f'stage_{stage:02d}' for stage in range(4,9)}
    frozen = hotel_agent.freeze_packages(packages,original_oracle=True)
    output.mkdir(parents=True)
    save_json(output/'plan.json',{'model':MODEL,'secondary_standard_m8':comparison,'stages':frozen,
        'carry_only':carry_only,'carry_starts_from':str(EARLIER_SUBMISSION),
        'agent_calls_planned':5 if carry_only else 10+bool(comparison),
        'control':'Use standard preceding files for each control and carry actual submissions without repair.'})
    records = {}
    lock = Lock()

    def finished(label, record):
        with lock:
            records[label] = record
            save_json(output/'results.json', records)
        print('HOTEL_EIGHT_RESULT',label,record['status'],record.get('native_pass'),flush=True)

    def attempt(label, current, provider):
        try:
            record = evaluate(label,current,output,provider,timeout)
        except Exception as exc:
            record = {'status':'RUN_ERROR','native_pass':None,'error':f'{type(exc).__name__}: {exc}'}
        finished(label,record)
        return record

    def carry_chain():
        path = EARLIER_SUBMISSION
        for stage, package in packages.items():
            label=f'stage_{stage:02d}_carry'
            try:
                if path is None:
                    raise ValueError('The previous run did not deliver usable Office files')
                carry = freeze_carry(path,package,output/'carried_inputs'/package.name,frozen[stage],prepare_stage=prepare_stage)
            except (ValueError,OSError,KeyError) as exc:
                finished(label,{'status':'HISTORY_UNAVAILABLE','native_pass':None,'error':str(exc)})
                path=None
                continue
            result=attempt(label,carry,route)
            path=(output/label/'submission' if result['status']=='COMPLETED' and not result['missing_office_files'] else None)

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(carry_chain)]
        if not carry_only:
            for stage in packages:
                jobs.append(pool.submit(attempt,f'stage_{stage:02d}_standard',frozen[stage],route))
            if comparison:
                jobs.append(pool.submit(attempt,'stage_08_secondary_standard',frozen[8],{**route,'model':comparison}))
        for future in jobs:
            future.result()
    pairs = {}
    for stage in packages:
        label=f'stage_{stage:02d}'
        prompts=[output/f'{label}_{arm}'/'prompt.txt' for arm in ('carry','standard')]
        if all(path.is_file() for path in prompts):
            if hotel_agent.digest(prompts[0])!=hotel_agent.digest(prompts[1]):
                raise ValueError(f'The {label} comparison received a different prompt')
            pairs[label]={'same_prompt':True,'same_oracle':True}
    save_json(output/'comparisons.json',pairs)
    print(output,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=DEFAULT)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--comparison-model',default=None)
    parser.add_argument('--carry-only',action='store_true',help='Run the actual continuation without new standard-start calls')
    args=parser.parse_args()
    if args.timeout<=0:
        parser.error('--timeout must be positive')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=args.out or args.source/'agent_runs'/f'official_{stamp}'
    run(args.source.resolve(),output.resolve(),args.timeout,args.comparison_model,carry_only=args.carry_only)


if __name__=='__main__':
    main()
