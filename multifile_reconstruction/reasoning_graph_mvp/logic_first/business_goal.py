"""Goal-driven backward expansion with genuine LLM alternatives and path audits."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import sympy as sp

from .business_author import call_business
from .business_verify import verify,units,expression
from .engine import units_after


def known_revpar():
    # User-specified known fields and target meaning; NO supplied target formula.
    return [
        {'id':'room_revenue','concept':'room_revenue','kind':'base','zh':'客房收入','en':'Room revenue',
         'meaning':'同一季度、同一酒店的客房收入，人民币','unit':{'CNY':1}},
        {'id':'available_room_nights','concept':'available_room_nights','kind':'base','zh':'可售间夜数','en':'Available room nights',
         'meaning':'同一季度、同一酒店可售的间夜数，正数','unit':{'room_night':1}},
        {'id':'sold_room_nights','concept':'sold_room_nights','kind':'base','zh':'已售间夜数','en':'Sold room nights',
         'meaning':'同一季度、同一酒店已售的付费间夜数，正数','unit':{'room_night':1}},
    ]


def goal_quantity():
    return {'id':'revpar','concept':'revpar','kind':'derived','zh':'每间可售客房收入','en':'RevPAR',
            'meaning':'同一季度每可售间夜的客房收入','unit':{'CNY':1,'room_night':-1}}


def backward_request(quantities,productions,goals,feedback):
    return {'messages':[
        {'role':'system','content':'Search executable business dependencies backward from requested goals. JSON only; no tools.'},
        {'role':'user','content':json.dumps({
            'known_and_discovered_quantities':quantities,'current_productions':productions,
            'expand_goals':goals,'feedback':feedback,
            'request':'For only these goals, propose legitimate alternative operator expressions. Independently introduce meaningful intermediate business metrics where useful, then request their backward expansion. Stop branches at given base quantities.',
            'operators':['ADD','SUB','MUL','DIV'],
            'expression_schema':{'reference':{'ref':'quantity id'},'operation':{'op':'allowed operator','args':['two expression objects']},'constant':{'const':1}},
            'output':{'new_quantities':[{'id':'new id','concept':'standard business concept','kind':'derived','zh':'Chinese name','en':'English name','meaning':'short definition','unit':{'CNY':1}}],
                      'productions':[{'id':'unique production id','output':'one requested goal id','formula':'expression object'}],
                      'expand_next':['new unresolved intermediate ids']},
            'constraints':[
                'Return all quantities needed by proposed expressions. Only base quantities are given leaves; introduced intermediates need later derivations.',
                'Search for more than one meaningful path when available, including direct and intermediate-based derivations. Do not pad with algebraic identities.',
                'Do not expand outputs other than expand_goals. Retain existing goal alternatives when adding alternatives.',
                'AND: all operands of an expression are required. OR: choose one legitimate production for a quantity.',
                'No metric observations, expected formulas or standard answers are supplied. Use your business knowledge, subject to independent verification.',
                'Use dimension-exponent units, ratios {}, CNY currency and room_night. Keep ratios as decimals. Only literal 1 is allowed.',
                'Same quarter and entity throughout. Operands of DIV must have nonzero denominator; current known room-nights are positive.',
                'Keep definitions concise. Do not invent score formulas.']},ensure_ascii=False)}]}


def refs(tree):
    if 'ref' in tree:return {tree['ref']}
    return set().union(*(refs(t) for t in tree.get('args',[])))


def operation_count(tree):
    return (1+sum(operation_count(t) for t in tree['args'])) if 'op' in tree else 0


def paths(quantities,productions,target='revpar',limit=64):
    q={m['id']:m for m in quantities};by={k:[p for p in productions if p['output']==k] for k in q if q[k]['kind']=='derived'}
    def closed_plans(key,active=frozenset()):
        if key in active or key not in q:return []
        if q[key]['kind']=='base':return [{}]
        plans=[]
        for production in by.get(key,[]):
            alternatives=[closed_plans(child,active|{key}) for child in sorted(refs(production['formula']))]
            if any(not options for options in alternatives):continue
            for parts in itertools.product(*alternatives):
                merged={key:production};valid=True
                for part in parts:
                    for quantity,rule in part.items():
                        if quantity in merged and merged[quantity]['id']!=rule['id']:valid=False
                        merged[quantity]=rule
                if valid:plans.append(merged)
                if len(plans)>limit:raise ValueError('PATH_ENUMERATION_LIMIT')
        return plans
    result=[];seen=set()
    for chosen in closed_plans(target):
        used=set();active=set();depths={};leafset=set()
        def walk(key):
            if key in active:raise ValueError('CYCLE')
            if key in depths:return depths[key]
            if key not in q:raise ValueError('UNKNOWN_INPUT')
            if q[key]['kind']=='base':leafset.add(key);depths[key]=0;return 0
            active.add(key);used.add(key)
            def depth(t):
                if 'ref' in t:return walk(t['ref'])
                if 'const' in t:return 0
                return 1+max(depth(c) for c in t['args'])
            value=depth(chosen[key]['formula']);active.remove(key);depths[key]=value;return value
        try:d=walk(target)
        except (KeyError,ValueError):continue
        signature=tuple(sorted(chosen[k]['id'] for k in used))
        if signature in seen:continue
        seen.add(signature)
        metrics=[deepcopy(m) for m in quantities if m['kind']=='base' or m['id'] in used]
        for m in metrics:
            if m['kind']=='derived':m['formula']=chosen[m['id']]['formula']
        proposal={'period':'2025-Q4','entities':[f'Goal world {i}' for i in range(6)],'metrics':metrics}
        verification=verify(proposal)
        accepted=bool(verification.get('accepted_derived')) and all(r['accepted'] for r in verification['metrics'])
        # Algebraic support in independent RAW inputs, not private semantic symbols.
        symbols={m['id']:sp.Symbol(m['id'],positive=True) for m in metrics if m['kind']=='base'}
        cache={}
        def raw(key):
            if key in symbols:return symbols[key]
            if key in cache:return cache[key]
            def expr(t):
                if 'ref' in t:return raw(t['ref'])
                if 'const' in t:return sp.Integer(t['const'])
                a,b=[expr(c) for c in t['args']]
                return {'ADD':lambda:a+b,'SUB':lambda:a-b,'MUL':lambda:a*b,'DIV':lambda:a/b}[t['op']]()
            cache[key]=expr(chosen[key]['formula']);return cache[key]
        simplified=sp.cancel(raw(target))
        support=sorted(str(s) for s in simplified.free_symbols)
        result.append({'accepted':accepted,'production_ids':list(signature),'proposal':proposal,
                       'verification':verification,'operator_count':sum(operation_count(chosen[k]['formula']) for k in used),
                       'dependency_depth':d,'syntactic_inputs':sorted(leafset),'simplified_sufficient_inputs':support,
                       'algebraically_cancelled_inputs':sorted(leafset-set(support)),
                       'simplified_expression':str(simplified),
                       'domain_note':'Actual unsimplified execution still needs its denominators nonzero, including any subsequently cancelled quantity.'})
    return result


def run_backward(directory,reuse=True,max_steps=4,goal_spec=None):
    from .topic_author import parse_json
    from .topic_run import save
    goal_spec=deepcopy(goal_spec or {'known':known_revpar(),'target':goal_quantity(),'desired_paths':2})
    target=goal_spec['target'];target_id=target['id'];desired_paths=goal_spec.get('desired_paths',1)
    if any(q['kind']!='base' for q in goal_spec['known']) or target['kind']!='derived':raise ValueError('GOAL_INPUT_KIND')
    directory=Path(directory);quantities=goal_spec['known']+[target];productions=[];goals=[target_id];trace=[]
    save(directory/'input.json',goal_spec)
    feedback={'status':'START'}
    for step in range(max_steps):
        request=backward_request(quantities,productions,goals,feedback);folder=directory/f'step_{step:02d}'
        if reuse and (folder/'response.txt').exists() and json.loads((folder/'request.json').read_text())==request:
            delta=parse_json((folder/'response.txt').read_text());info=json.loads((folder/'call.json').read_text())
        else:delta,info=call_business(request,folder)
        try:
            if set(delta)!={'new_quantities','productions','expand_next'}:raise ValueError('EXPANSION_SCHEMA')
            known={m['id']:m for m in quantities}
            for metric in delta['new_quantities']:
                if metric['id'] in known and metric!=known[metric['id']]:raise ValueError('QUANTITY_REDEFINITION')
                known[metric['id']]=metric
            if any(p['output'] not in goals for p in delta['productions']):raise ValueError('WRONG_EXPANSION_OUTPUT')
            candidates=[p for p in productions if p['output'] not in goals]+delta['productions']
            if len({p['id'] for p in candidates})!=len(candidates):raise ValueError('DUPLICATE_PRODUCTION')
            referenced={key for p in candidates for key in refs(p['formula'])}
            if not referenced<=set(known):raise ValueError('UNDECLARED_REFERENCE')
            quantities=list(known.values());productions=candidates
            unresolved=[m['id'] for m in quantities if m['kind']=='derived' and not any(p['output']==m['id'] for p in productions)]
            candidates_report=paths(quantities,productions,target=target_id)
            accepted=[p for p in candidates_report if p['accepted']]
            feedback={'status':'INCOMPLETE' if unresolved else 'VALIDATED','valid_path_count':len(accepted),'unresolved':unresolved}
            trace.append({'step':step,'goals':goals,'feedback':feedback,'call':info})
            save(folder/'state.json',{'quantities':quantities,'productions':productions,'feedback':feedback})
            if len(accepted)>=desired_paths:break
            goals=unresolved or [target_id]
            if not unresolved:feedback['search_request']='Find additional meaningful legitimate routes, not algebraic padding.'
        except (KeyError,TypeError,ValueError) as exc:
            feedback={'status':'REJECTED','error_code':str(exc)};trace.append({'step':step,'goals':goals,'feedback':feedback,'call':info})
    candidates_report=paths(quantities,productions,target=target_id)
    accepted=[p for p in candidates_report if p['accepted']]
    minimum=min((p['dependency_depth'] for p in accepted),default=None)
    sets={frozenset(p['simplified_sufficient_inputs']) for p in accepted}
    minimal=[sorted(s) for s in sets if not any(t<s for t in sets)]
    result={'passed':len(accepted)>=desired_paths,'origin':'LLM goal-driven backward expansion','trace':trace,'target':target,
            'quantities':quantities,'productions':productions,'paths':candidates_report,
            'minimum_observed_depth':minimum,'minimal_sufficient_inputs':minimal,
            'scope':'Alternative paths are LLM-generated and independently checked. Enumeration is bounded to proposed productions, not all arithmetic programs.'}
    save(directory/'result.json',result);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'business_artifacts/goal_revpar');p.add_argument('--reuse',action='store_true');p.add_argument('--goal-spec',type=Path)
    args=p.parse_args();spec=json.loads(args.goal_spec.read_text()) if args.goal_spec else None
    result=run_backward(args.out,args.reuse,goal_spec=spec)
    print(json.dumps({'passed':result['passed'],'paths':[(p['operator_count'],p['dependency_depth'],p['syntactic_inputs'],p['accepted']) for p in result['paths']]},ensure_ascii=False),flush=True)
