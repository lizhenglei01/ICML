"""Per-goal absolute correctness, impact detection and conditional preservation."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import shutil

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from medical_cf.oracle import structural_snapshot
from schema_cf.upstream_office import patcher


def validate_submission(submission, goals, stage):
    if not isinstance(submission,dict) or type(submission.get('stage')) is not int or submission.get('stage') != stage:
        raise ValueError('Wrong stage or non-object submission')
    required={'stage','answers'} | ({'source_edits'} if stage==2 else set())
    if set(submission) != required or not isinstance(submission['answers'],dict):
        raise ValueError('Unexpected/missing top-level fields')
    if set(submission['answers']) != {g['id'] for g in goals}:
        raise ValueError('Every frozen goal must be submitted exactly once')
    for g in goals:
        answer=submission['answers'][g['id']]
        fields={'value','evidence'} | ({'changed'} if stage==2 else set())
        if not isinstance(answer,dict) or set(answer)!=fields:
            raise ValueError('Incorrect answer fields for '+g['id'])
        if not isinstance(answer['evidence'],str) or not answer['evidence'].strip():
            raise ValueError('Missing evidence for '+g['id'])
        value=answer['value']
        if g['type']=='count' and (type(value) is not int or value<0):
            raise ValueError('Counts must be non-negative JSON integers')
        if g['type']=='id_set' and (not isinstance(value,list) or any(type(v) is not int for v in value) or len(set(value))!=len(value)):
            raise ValueError('ID sets must be duplicate-free integer arrays')
        if stage==2 and type(answer['changed']) is not bool:
            raise ValueError('changed must be a JSON boolean')
    if stage==2 and not isinstance(submission['source_edits'],list):
        raise ValueError('source_edits must be an array')


def equal(actual, expected, kind):
    if kind=='id_set':return isinstance(actual,list) and set(actual)==set(expected) and len(actual)==len(set(actual))
    return type(actual) is type(expected) and actual==expected


def materialize_and_check(package, response, output):
    """Execute only the model's edits with upstream patch_xlsx; never fill missing edits."""
    package,output=Path(package),Path(output)
    output.mkdir(parents=True,exist_ok=True)
    source=package/'stage1/source.xlsx';target=output/'source.xlsx'
    shutil.copy2(source,target)
    rows=json.loads((package/'private/before_data.json').read_text())
    expected=json.loads((package/'private/after_data.json').read_text())
    schemas=json.loads((package/'private/schemas.json').read_text())
    current=deepcopy(rows)
    checks=[];ops=[]
    try:
        for edit in response['source_edits']:
            if set(edit)!={'table','row_key','column','value'}:raise ValueError('Invalid edit fields')
            table=edit['table'];columns=[c['name'] for c in schemas[table]['columns']]
            if set(edit['row_key'])!=set(schemas[table]['primary_key']) or not edit['row_key']:
                raise ValueError('Edit needs the complete declared primary key')
            indexes=[i for i,r in enumerate(current[table]) if all(r[k]==v for k,v in edit['row_key'].items())]
            if len(indexes)!=1:raise ValueError('Edit must identify one original record')
            index=indexes[0];column=edit['column'];col_index=columns.index(column)
            if type(edit['value']) not in (str,int,float,bool,type(None)):raise ValueError('Scalar edit required')
            if type(edit['value']) is float and not math.isfinite(edit['value']):raise ValueError('Finite edit value required')
            current[table][index][column]=edit['value']
            ops.append({'type':'xlsx_cell','sheet':table,'cell':get_column_letter(col_index+1)+str(index+2),'value':edit['value']})
        patcher.patch_xlsx(target,ops)
        checks.append({'id':'model_edit_execution','pass':True,'operations':len(ops),'executor':'OfficeEditBench.patch_xlsx'})
    except (KeyError,ValueError,TypeError) as e:
        checks.append({'id':'model_edit_execution','pass':False,'error':str(e)})
    wb=load_workbook(target,data_only=False)
    bindings=[]
    for table,records in rows.items():
        columns=[c['name'] for c in schemas[table]['columns']]
        for i,record in enumerate(records):
            for j,column in enumerate(columns,1):
                address=get_column_letter(j)+str(i+2)
                actual=wb[table][address].value;wanted=expected[table][i][column]
                changed=record[column]!=wanted
                checks.append({'id':f'{table}!{address}','category':'required' if changed else 'protected',
                               'expected':wanted,'actual':actual,'pass':actual==wanted})
                bindings.append({'id':f'{table}!{address}','file':'source.xlsx','kind':'xlsx','sheet':table,'cell':address})
    left=structural_snapshot(source.parent,bindings,('source.xlsx',))
    right=structural_snapshot(output,bindings,('source.xlsx',))
    checks.append({'id':'native_structure','pass':left==right,'different_parts':[p for p in set(left)|set(right) if left.get(p)!=right.get(p)]})
    return {'pass':all(c['pass'] for c in checks),'checks':checks,'artifact':str(target)}


def evaluate(package, first, second, artifact_output=None):
    package=Path(package)
    oracle=json.loads((package/'private/oracle.json').read_text())
    if isinstance(first,(Path,str)):first=json.loads(Path(first).read_text())
    if isinstance(second,(Path,str)):second=json.loads(Path(second).read_text())
    for stage,submission in [(1,first),(2,second)]:
        try:validate_submission(submission,oracle['goals'],stage)
        except (ValueError,KeyError,TypeError) as e:
            return {'pass':False,'format_error':{'stage':stage,'message':str(e)},'goals':[]}
    results=[]
    for g in oracle['goals']:
        gid=g['id'];a,b=first['answers'][gid],second['answers'][gid]
        ok1=equal(a['value'],oracle['before'][gid],g['type'])
        ok2=equal(b['value'],oracle['after'][gid],g['type'])
        impact=b['changed']==oracle['changed'][gid]
        eligible=ok1 and not oracle['changed'][gid]
        results.append({'goal_id':gid,'question':g['question'],'expected_before':oracle['before'][gid],'actual_before':a['value'],
            'before_correct':ok1,'expected_after':oracle['after'][gid],'actual_after':b['value'],'after_correct':ok2,
            'expected_changed':oracle['changed'][gid],'actual_changed':b['changed'],'impact_correct':impact,
            'preservation_eligible':eligible,'preservation_correct':ok2 if eligible else None,
            'self_correction':not ok1 and ok2,'inherited_wrong_answer':not ok1 and not ok2 and a['value']==b['value'],
            'evidence_present':bool(a['evidence'].strip() and b['evidence'].strip())})
    metric=lambda hits,total:{'correct':hits,'total':total,'rate':hits/total if total else None}
    changed=[r for r in results if r['expected_changed']]
    preserved=[r for r in results if r['preservation_eligible']]
    summary={'stage1_accuracy':metric(sum(r['before_correct'] for r in results),len(results)),
        'stage2_accuracy':metric(sum(r['after_correct'] for r in results),len(results)),
        'impact_accuracy':metric(sum(r['impact_correct'] for r in results),len(results)),
        'affected_goal_update':metric(sum(r['after_correct'] for r in changed),len(changed)),
        'correct_invariant_preservation':metric(sum(r['after_correct'] for r in preserved),len(preserved)),
        'self_corrections':[r['goal_id'] for r in results if r['self_correction']],
        'inherited_wrong_answers':[r['goal_id'] for r in results if r['inherited_wrong_answer']]}
    native=materialize_and_check(package,second,artifact_output) if artifact_output is not None else None
    return {'pass':all(r['before_correct'] and r['after_correct'] and r['impact_correct'] for r in results) and (native is None or native['pass']),
        'stage2_pass':all(r['after_correct'] and r['impact_correct'] for r in results) and (native is None or native['pass']),
        'summary':summary,'goals':results,'native':native,
        'limitations':['Evidence presence is enforced, but free-text reasoning correctness is not automatically judged.',
                       'The model submits explicit edits; existing OfficeEditBench code executes them. This is not a GUI editing evaluation.']}
