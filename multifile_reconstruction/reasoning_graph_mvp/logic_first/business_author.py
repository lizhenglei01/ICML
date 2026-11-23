"""Propose mixed-unit metrics and formulas without exposing the private oracle."""
import argparse
import json
from pathlib import Path

from .topic_author import parse_json


def call_business(request,directory,effort='medium'):
    """Reuse the inference transport; leave global configuration unchanged."""
    from alignment_probe.codex_transport import configured_route,call_codex
    import time
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
    route=configured_route();started=time.perf_counter()
    raw,info=call_codex(request,route['model'],effort,directory/'transport')
    info['wall_seconds']=time.perf_counter()-started
    (directory/'response.txt').write_text(raw,encoding='utf-8')
    (directory/'call.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    return parse_json(raw),info


def proposal_request(topic):
    return {'messages':[
        {'role':'system','content':'Propose executable business metric dependencies using established definitions. Return JSON only; no tools, browsing or file access.'},
        {'role':'user','content':json.dumps({
            'topic':topic,
            'request':'Independently choose useful basic and derived metrics for this topic and propose their computational dependencies. No observations or target answers exist yet.',
            'schema':{
                'topic_zh':'topic in Chinese','topic_en':'topic in English',
                'period':'2025-Q4','entity_type_zh':'entity kind','entity_type_en':'entity kind',
                'entities':['six realistic fictional names in ASCII English'],
                'metrics':[{'id':'your own lowercase_ascii_metric_identifier','concept':'your own standard English business concept identifier',
                            'kind':'base or derived','zh':'Chinese metric name','en':'English metric name',
                            'meaning':'precise business definition and measurement scope',
                            'unit':{'CNY':1},
                            'formula':'derived only: expression object; omit for a base metric'}]},
            'expression_language':{
                'reference':{'ref':'declared metric id'},
                'operator':{'op':'ADD or SUB or MUL or DIV','args':['two expression objects, ordered']},
                'constant':{'const':1}},
            'constraints':[
                'Choose exactly 3 base metrics and exactly 3 derived metrics. Each derived metric must have an explicit executable formula. Keep meaning under 30 Chinese characters or 20 English words.',
                'Use standard defensible business definitions. Do not invent arbitrary scores, weights, certification policies, or formulas just to increase depth.',
                'When a legitimate intermediate derived metric is useful, reference it instead of repeating its definition. Do not add pointless arithmetic.',
                'Different metrics may have different units. A unit is a dimension-exponent dictionary; ratio is {}. Use CNY for currency, room_night for period accommodation counts, item for merchandise counts, complaint for complaint counts, day for days, room for physical rooms.',
                'Only nonzero integer unit exponents are allowed; do not call every unit dimensionless. Per-unit prices must retain denominator units.',
                'Keep ratio formulas as decimal fractions, not numbers multiplied by 100. The only allowed literal constant is dimensionless 1.',
                'All current-period metrics refer to the same entity and 2025-Q4. If historical comparisons are proposed, identify the corresponding comparable prior period explicitly.',
                'For accommodation occupancy, distinguish period room-nights from a static physical-room count. Do not mix periods, tax bases, or currencies.',
                'Do not include numeric metric observations, task thresholds, sample answers or a handwritten data table.',
                'Do not claim that your formulas have already been verified. Unsupported definitions may be rejected by an independent checker.']},ensure_ascii=False)}]}


def propose(topic,directory):
    directory=Path(directory)
    data,info=call_business(proposal_request(topic),directory)
    (directory/'proposal.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return data,info


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--topic',required=True);p.add_argument('--out',required=True,type=Path)
    args=p.parse_args();data,info=propose(args.topic,args.out)
    print(json.dumps({'topic':data.get('topic_zh'),'metrics':[(m.get('zh'),m.get('kind')) for m in data.get('metrics',[])],
                      'model':info['reported_model'],'seconds':info['wall_seconds']},ensure_ascii=False),flush=True)
