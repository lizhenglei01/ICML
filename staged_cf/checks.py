"""Executable positive/negative oracle controls; kept separate from real-model results."""
from copy import deepcopy
import json
from pathlib import Path

from medical_cf.run import save
from .evaluate import evaluate


def controls(package,output):
    package,output=Path(package),Path(output)
    first=json.loads((package/'private/reference_stage1.json').read_text())
    second=json.loads((package/'private/reference_stage2.json').read_text())
    fixtures=[]
    def case(name,edit=None,expected=False,stage2_expected=False):
        a,b=deepcopy(first),deepcopy(second)
        if edit:edit(a,b)
        fixtures.append((name,a,b,expected,stage2_expected))
    case('correct',expected=True,stage2_expected=True)
    def omit(a,b):
        b['answers']['G1']['value']=a['answers']['G1']['value'];b['answers']['G1']['changed']=False
    case('missed_affected_goal',omit)
    def flip(a,b):b['answers']['G4'].update(value=12,changed=True)
    case('false_change_on_invariant',flip)
    def wrong(a,b):a['answers']['G7']['value']=8;b['answers']['G7']['value']=8
    case('both_wrong_but_self_consistent',wrong)
    def correct_prior(a,b):a['answers']['G4']['value']=999
    case('correct_stage1_error',correct_prior,stage2_expected=True)
    def missing_edit(a,b):b['source_edits']=[]
    case('right_answers_missing_source_edit',missing_edit)
    rows=json.loads((package/'private/before_data.json').read_text())
    schemas=json.loads((package/'private/schemas.json').read_text())
    target=json.loads((package/'private/intervention.json').read_text())
    def unrelated(a,b):
        for table,records in rows.items():
            for i,r in enumerate(records):
                if table==target['table'] and i==target['row_index']:continue
                non_keys=[c['name'] for c in schemas[table]['columns'] if c['name'] not in schemas[table]['primary_key']]
                if non_keys and schemas[table]['primary_key']:
                    b['source_edits'].append({'table':table,'row_key':{k:r[k] for k in schemas[table]['primary_key']},'column':non_keys[0],'value':'UNRELATED CHANGE'})
                    return
        raise ValueError('Control needs a protected non-key field')
    case('collateral_source_edit',unrelated)
    def missinggoal(a,b):del b['answers']['G8']
    case('missing_goal',missinggoal)
    reports={}
    for name,a,b,expected,stage2_expected in fixtures:
        folder=output/name;save(folder/'stage1.json',a);save(folder/'stage2.json',b)
        report=evaluate(package,a,b,folder/'delivered')
        report['expected_pass']=expected;report['expected_stage2_pass']=stage2_expected
        report['expectation_met']=report['pass']==expected and report.get('stage2_pass',False)==stage2_expected
        save(folder/'evaluation.json',report)
        reports[name]={'pass':report['pass'],'stage2_pass':report.get('stage2_pass',False),'expectation_met':report['expectation_met'],
                       'summary':report.get('summary'), 'format_error':report.get('format_error')}
    if not all(r['expectation_met'] for r in reports.values()):raise AssertionError(reports)
    save(output/'summary.json',{'pass':True,'cases':reports,'scope':'Programmatic oracle controls, not model runs'})
    return reports
