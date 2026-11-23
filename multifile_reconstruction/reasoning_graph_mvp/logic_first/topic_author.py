"""Inference-only authors: topic metadata and answer-blind bilingual narratives."""
import json
from pathlib import Path
import re
import time


def metadata_request(topic):
    return {"messages":[
        {"role":"system","content":"You create synthetic document benchmark metadata. Output JSON only. Do not use tools or external services."},
        {"role":"user","content":json.dumps({
            "topic":topic,
            "task":"Create coherent fictional business metadata, not a question, data table, answer or dependency graph.",
            "schema":{
                "topic_zh":"Chinese business topic", "topic_en":"English business topic",
                "entity_type_zh":"Chinese entity kind", "entity_type_en":"English entity kind",
                "entities":["10 distinct realistic fictional names; ASCII English, not Entity A or a real company"],
                "unit":{"id":"lowercase_ascii_unit","zh":"Chinese common summable unit","en":"English common summable unit"},
                "metrics":[{"id":"lowercase_ascii_identifier","zh":"Chinese name","en":"English name","meaning":"plain business meaning"}],
                "groups":[{"id":"lowercase_ascii_identifier","zh":"Chinese subgroup","en":"English subgroup"}],
                "timestamps":["2025-Q1","2025-Q2","2025-Q3","2025-Q4"]},
            "constraints":[
                "Exactly 10 entities, exactly 8 metrics and exactly 3 groups. Four ordered timestamps.",
                "All metrics must share the one common summable unit and be meaningful for every group and timestamp.",
                "Metrics are independent nonnegative counts/amounts, not scores, rates, percentages or ratios.",
                "Do not include totals together with their components, or variables related by a fixed formula.",
                "Groups must be mutually exclusive business categories, valid across all metrics.",
                "Use realistic fictional entity names only. Do not declare real regulatory or certification policies.",
                "Do not supply numerical metric observations, thresholds, formulas or a ready-made reasoning tree."]},ensure_ascii=False)}]}


def validate_metadata(data):
    required={"topic_zh","topic_en","entity_type_zh","entity_type_en","entities","unit","metrics","groups","timestamps"}
    if set(data)!=required: raise ValueError("METADATA_KEYS")
    for key in ["topic_zh","topic_en","entity_type_zh","entity_type_en"]:
        if not isinstance(data[key],str) or not data[key].strip(): raise ValueError("METADATA_TEXT")
    entities=data['entities']
    if len(entities)!=10 or len(set(entities))!=10 or any(not isinstance(e,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 &.,\x27-]{2,70}',e) for e in entities):
        raise ValueError("METADATA_ENTITIES")
    for key,count in [('metrics',8),('groups',3)]:
        items=data[key]
        if len(items)!=count or len({i['id'] for i in items})!=count: raise ValueError("METADATA_CARDINALITY")
        for item in items:
            if not re.fullmatch('[a-z][a-z0-9_]*',item['id']): raise ValueError("METADATA_ID")
            if not all(isinstance(item.get(k),str) and item[k].strip() for k in ['zh','en']):raise ValueError("METADATA_LABEL")
            if key=='metrics' and not isinstance(item.get('meaning'),str):raise ValueError("METADATA_MEANING")
        if any(len({i[lang] for i in items})!=count for lang in ['zh','en']):raise ValueError("METADATA_LABEL_COLLISION")
    if data['timestamps']!=['2025-Q1','2025-Q2','2025-Q3','2025-Q4']:raise ValueError("METADATA_TIME")
    if set(data['unit'])!={'id','zh','en'} or not re.fullmatch('[a-z][a-z0-9_]*',data['unit']['id']):raise ValueError("METADATA_UNIT")
    return data


def parse_json(raw):
    raw=raw.strip()
    if raw.startswith('```'):
        raw=raw[raw.index('\n')+1:raw.rfind('```')].strip()
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError('Expected object')
    return data


def call_author(request,directory):
    from alignment_probe.codex_transport import call_codex,configured_route
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
    route=configured_route();started=time.perf_counter()
    raw,info=call_codex(request,route['model'],route['effort'] or 'high',directory/'transport')
    (directory/'response.txt').write_text(raw,encoding='utf-8')
    info['wall_seconds']=time.perf_counter()-started
    (directory/'call.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    return parse_json(raw),info


def generate_metadata(topic,directory):
    """No fixture or hand-authored metric fallback is substituted for model output."""
    directory=Path(directory)
    data,info=call_author(metadata_request(topic),directory)
    validate_metadata(data)
    (directory/'metadata.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return data,info


def writer_spec(task,task_id):
    from .chart_rules import semantic_ir
    # No entity names, observed metric values, desired memberships or answers.
    metadata={k:v for k,v in task['metadata'].items() if k!='entities'}
    return {'task_id':task_id,'metadata':metadata,'rules':semantic_ir(task),
            'charts':task['charts'],'average_query':task['average_query']}


def narrative_request(specs):
    from .chart_rules import DESCRIPTIONS
    return {'messages':[
        {'role':'system','content':'Write faithful bilingual synthetic business review documents from supplied selection logic. JSON only; no tools.'},
        {'role':'user','content':json.dumps({
            'task':'Turn each supplied logic graph into coherent policy-style Chinese and English report prose. This is a synthetic internal review policy, not a real certification.',
            'atom_semantics':DESCRIPTIONS,'tasks':specs,
            'output':{'tasks':[{'task_id':'copy input task_id','title_zh':'Chinese title','title_en':'English title',
                      'intro_zh':'One short natural introductory paragraph','intro_en':'One short natural introductory paragraph',
                      'sections':[{'node_id':'copy an input node id','name_zh':'distinct meaningful short name for the resulting candidate group',
                                   'name_en':'distinct meaningful short name for the resulting candidate group',
                                   'text_zh':'one natural Chinese paragraph faithfully defining this candidate group',
                                   'text_en':'one natural English paragraph faithfully defining this candidate group'}]}]},
            'requirements':[
                'Output exactly one section for EVERY graph node in topological order. The final section is the designated target group.',
                'Use natural business names and refer to preceding groups by those names. Do not print stage_1, R1 or a JSON-like formula list in report text.',
                'Preserve each metric, subgroup, timestamp/window, direction, numerical threshold, operand order and inclusive AND/OR relation exactly.',
                'For ATOM with an input, filter ONLY the named preceding group. For ATOM without input, screen all candidates.',
                'For cross-entity means or extrema, use the incoming candidate pool as comparator scope, not the entire chart if a pool is provided.',
                'GE/LE include equality; OR includes candidates meeting both; strict monotone means every consecutive interval; unique extrema exclude ties.',
                'Absolute change and percentage change are different. Slope trend compares consecutive differences, not the level or a generic improving trend.',
                'Use the supplied exact metric/group labels and timestamp strings for auditability. Explain all conditions explicitly.',
                'Do not invent business formulas, extra criteria, metric values, qualifying entity names, final counts or final numeric answers.',
                'Do not add negative judgements to high/low indicators. This is a designated review group, not necessarily the best performers.',
                'Keep each section under 65 English words and 110 Chinese characters. No Markdown/code blocks.']},ensure_ascii=False)}]}


def extraction_request(specs,documents):
    from .chart_rules import DESCRIPTIONS,PARAMETERS
    tasks=[]
    for spec,doc in zip(specs,documents):
        tasks.append({'task_id':spec['task_id'],'metadata':spec['metadata'],
                      'title_en':doc['title_en'],'intro_en':doc['intro_en'],
                      'sections':doc['sections']})
    return {'messages':[
        {'role':'system','content':'Independently recover selection logic from report prose. JSON only. Do not use tools.'},
        {'role':'user','content':json.dumps({
            'task':'Read the English report clauses and recover their exact typed selection graph. Also compare Chinese clauses against English for semantic differences. Original graph and answers are NOT supplied.',
            'template_definitions':DESCRIPTIONS,'active_parameter_keys':PARAMETERS,'tasks':tasks,
            'output':{'tasks':[{'task_id':'copy task id','ir':{'nodes':[{'id':'section node_id','op':'ATOM or AND or OR','inputs':['preceding section node_id'],
                           'template':'only for ATOM; choose supplied template name','params':'only for ATOM; dictionary of all active keys'}],
                           'selection':'last section node_id'},'bilingual_consistent':True,'issues':['specific ambiguities, missing conditions or Chinese/English differences']}]},
            'rules':[
                'Do not guess missing rules; describe missing/ambiguous details in issues.',
                'Use canonical metric/group ids from metadata; timestamps exactly as supplied; comparisons GE/LE; directions up/down.',
                'inputs is [] for an ATOM over all entities; for a scoped filter name its preceding group id. For AND/OR give both group ids.',
                'For equivalent threshold/extreme templates use timestamp_threshold/timestamp_extreme for line-chart descriptions; metric_threshold/single_metric_cross_entity_extreme for multi-metric descriptions; same_group_value_threshold/same_group_value_extreme for group-chart descriptions.',
                'If English does not identify chart kind, choose any semantically identical alias from those three families; this is accepted by the comparator.',
                'Return no chart id, extra fields or inactive atom parameters. AND/OR inputs may be reordered.',
                'Do not invent current metric values, target memberships, standard answers or business definitions.']},ensure_ascii=False)}]}


def validate_document(spec,document):
    if document.get('task_id')!=spec['task_id']:raise ValueError('DOCUMENT_TASK_ID')
    expected=[n['id'] for n in spec['rules']['nodes']]
    if [s.get('node_id') for s in document.get('sections',[])]!=expected:raise ValueError('DOCUMENT_NODE_COVERAGE')
    for key in ['title_zh','title_en','intro_zh','intro_en']:
        if not isinstance(document.get(key),str) or not document[key].strip():raise ValueError('DOCUMENT_TEXT')
    for section in document['sections']:
        if set(section)!={'node_id','name_zh','name_en','text_zh','text_en'}:raise ValueError('DOCUMENT_SECTION_KEYS')
        if any(not isinstance(v,str) or not v.strip() for v in section.values()):raise ValueError('DOCUMENT_SECTION_TEXT')
        if any(re.search(r'\bstage_\d+\b|\bR\d+\b',section[k]) for k in ['name_en','text_en','name_zh','text_zh']):raise ValueError('TECHNICAL_ID_IN_PROSE')
    for lang in ['zh','en']:
        if len({s['name_'+lang] for s in document['sections']})!=len(expected):raise ValueError('DUPLICATE_GROUP_NAME')
    return document


def compare_extracted(expected,extracted):
    """Compare typed IRs. Equivalent template aliases do not count as a failure."""
    from .chart_rules import PARAMETERS
    aliases={n:family[0] for family in [
        ['timestamp_threshold','metric_threshold','same_group_value_threshold'],
        ['timestamp_extreme','single_metric_cross_entity_extreme','same_group_value_extreme']]
        for n in family}
    def normalize(ir):
        result=[]
        for node in ir['nodes']:
            keys={'id','op','inputs'} | ({'template','params'} if node['op']=='ATOM' else set())
            if set(node)!=keys:raise KeyError('EXTRA_EXTRACTED_FIELDS')
            n={k:node[k] for k in ['id','op','inputs']}
            if n['op'] in {'AND','OR'}:n['inputs']=sorted(n['inputs'])
            else:
                template=node['template']
                if set(node['params'])!=set(PARAMETERS[template]):raise KeyError('EXTRA_OR_MISSING_PARAMETERS')
                n.update(template=aliases.get(template,template),params={k:node['params'][k] for k in PARAMETERS[template]})
            result.append(n)
        return {'nodes':sorted(result,key=lambda n:n['id']),'selection':ir['selection']}
    try:
        a,b=normalize(expected),normalize(extracted)
        checks=[{'node_id':n['id'],'passed':n in b['nodes']} for n in a['nodes']]
        passed=a==b
        return {'passed':passed,'checks':checks,'expected':a,'extracted':b}
    except (KeyError,TypeError):return {'passed':False,'error':'INVALID_EXTRACTED_IR'}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--topic',required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();data,info=generate_metadata(args.topic,args.out)
    print(json.dumps({'topic':data['topic_zh'],'metrics':[m['zh'] for m in data['metrics']],
                      'model':info['reported_model'],'seconds':info['wall_seconds']},ensure_ascii=False),flush=True)
