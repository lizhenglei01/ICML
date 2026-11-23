"""Private dependency diagrams and answer-free mixed-unit public reports."""
from pathlib import Path
import html
import json
import shutil
import subprocess

from .graph_view import COLORS,quote
from .engine import jsonable


def unit_text(unit):
    if not unit:return '比例（小数）'
    labels={'CNY':'元','room_night':'间夜','room':'间','day':'天','item':'件','complaint':'次'}
    up=[];down=[]
    for key,power in unit.items():
        term=labels.get(key,key)+(f'^{abs(power)}' if abs(power)>1 else '')
        (up if power>0 else down).append(term)
    return ('·'.join(up) or '1')+('/'+'·'.join(down) if down else '')


def formula_text(expr,metrics):
    if 'ref' in expr:return metrics[expr['ref']]['zh']
    if 'const' in expr:return str(expr['const'])
    a,b=[formula_text(c,metrics) for c in expr['args']]
    return '('+a+{'ADD':'＋','SUB':'－','MUL':'×','DIV':'÷'}[expr['op']]+b+')'


def export_diagram(directory,title,nodes,edges,note):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    caption='<<B>'+html.escape(title)+'</B><BR/><FONT POINT-SIZE="12" COLOR="#667891">蓝＝基础事实　紫＝计算/判断　绿＝派生指标　黄＝逻辑选择　红＝结果</FONT><BR/><FONT POINT-SIZE="11" COLOR="#995555">构建端私有；'+html.escape(note)+'</FONT>>'
    dot=['digraph Business {',f'graph [rankdir=BT,bgcolor="white",nodesep=.5,ranksep=.65,pad=.3,dpi=110,fontname="Noto Sans CJK SC",fontsize=20,labelloc=t,label={caption}];',
         'node [shape=box,style="rounded,filled",fontname="Noto Sans CJK SC",fontsize=13,color="#91a2bd",margin=".16,.1"];',
         'edge [fontname="Noto Sans CJK SC",fontsize=11,color="#667891",fontcolor="#667891"];']
    for node in nodes:dot.append(quote(node['id'])+' [label='+quote(node['label'])+',fillcolor='+quote(COLORS[node['kind']])+'];')
    for edge in edges:dot.append(quote(edge['source'])+' -> '+quote(edge['target'])+' [label='+quote(edge.get('label',''))+'];')
    dot.append('}')
    path=directory/'dependency_graph.dot';path.write_text('\n'.join(dot),encoding='utf-8')
    command=shutil.which('dot')
    if not command:raise RuntimeError('Graphviz dot required')
    for fmt in ['svg','png']:subprocess.run([command,'-T'+fmt,str(path),'-o',str(directory/('dependency_graph.'+fmt))],check=True,capture_output=True)
    (directory/'dependency_graph.json').write_text(json.dumps({'nodes':nodes,'edges':edges,'note':note},ensure_ascii=False,indent=2),encoding='utf-8')


def render_hybrid(directory,task):
    from .topic_run import save
    from .business_hybrid import negative_submissions
    directory=Path(directory);public=directory/'public';private=directory/'private'
    public.mkdir(parents=True,exist_ok=True);private.mkdir(parents=True,exist_ok=True)
    profile=task['profile'];metrics={m['id']:m for m in profile['metrics']};graph=task['graph']
    base=[m for m in profile['metrics'] if m['kind']=='base']
    pieces=['<!doctype html><meta charset="utf-8"><style>body{max-width:1100px;margin:2em auto;font:17px sans-serif;line-height:1.7}td,th{border:1px solid #ccc;padding:.6em}table{border-collapse:collapse}</style>',
            '<h1>'+html.escape(profile['topic_zh'])+'</h1><p>'+html.escape(profile['topic_en'])+'</p>',
            '<p>统计期间 / Reporting period：'+profile['period']+'。本题为合成内部审阅任务；比例按小数计算。</p>',
            '<h2>基础资料 / Base observations</h2><table><tr><th>对象 / Entity</th>']
    pieces += ['<th>'+html.escape(m['zh']+' / '+m['en']+'（'+unit_text(m['unit'])+'）')+'</th>' for m in base]
    pieces.append('</tr>')
    mapping=[]
    for i,entity in enumerate(profile['entities']):
        pieces.append('<tr><td>'+html.escape(entity)+'</td>')
        for m in base:
            anchor=f'data_{i}_{m["id"]}';value=graph['facts'][m['id']][entity]
            pieces.append(f'<td id="{anchor}">'+str(value)+'</td>')
            mapping.append({'metric':m['id'],'entity':entity,'location':'report.html#'+anchor,'value':value})
        pieces.append('</tr>')
    pieces.append('</table><h2>指标定义 / Metric definitions</h2>')
    for metric in profile['metrics']:
        if metric['kind']=='derived':
            pieces.append('<p><b>'+html.escape(metric['zh']+' / '+metric['en'])+'</b> = '+html.escape(formula_text(metric['formula'],metrics))+'；单位：'+html.escape(unit_text(metric['unit']))+'。</p>')
    descriptions=[]
    for policy in task['policies']:
        metric=metrics[policy['metric']];sign='≥' if policy['op']=='GE' else '≤'
        descriptions.append(metric['zh']+' '+sign+' '+str(policy['threshold'])+' '+unit_text(metric['unit']))
    conjunction='同时满足 / satisfy both' if task['logic']=='AND' else '至少满足一项，含两项都满足 / satisfy at least one (inclusive OR)'
    pieces.append('<h2>本题审阅规则 / Synthetic review policy</h2><p>'+conjunction+'：</p><ul>'+''.join('<li>'+html.escape(d)+'</li>' for d in descriptions)+'</ul>')
    avg=metrics[task['truth']['average_metric']]['zh']
    pieces.append('<h2>问题 / Questions</h2><ol><li>哪些对象满足规则？ / Which entities qualify?</li><li>共有多少个？ / How many qualify?</li><li>这些对象的'+html.escape(avg)+'平均值是多少？ / What is their mean of that metric?</li></ol>')
    (public/'report.html').write_text('\n'.join(pieces),encoding='utf-8')
    nodes=[];edges=[]
    def node(key,kind,label):nodes.append({'id':key,'kind':kind,'label':label})
    def edge(a,b,label='输入'):edges.append({'source':a,'target':b,'label':label})
    actual_nodes={n['id']:n for n in graph['nodes']}
    out={}
    for n in graph['nodes']:
        key=n['id'];op=n['op']
        if op=='FACT':
            label=metrics[key]['zh']+'\n'+unit_text(metrics[key]['unit']) if key in metrics else '常数1'
            node(key,'evidence',label);out[key]=key
        elif op in {'ADD','SUB','MUL','DIV'}:
            compute='compute_'+key;node(compute,'rule',op+'｜全部输入齐备')
            for i,incoming in enumerate(n['inputs']):
                label=(['分子','分母'][i] if op=='DIV' else (['被减数','减数'][i] if op=='SUB' else '输入'))
                edge(out[incoming],compute,label)
            if key in metrics:
                node(key,'set',metrics[key]['zh']+'\n'+unit_text(metrics[key]['unit']));edge(compute,key,'得到指标');out[key]=key
            else:out[key]=compute
        elif op in {'GE','LE'}:
            policy=task['policies'][int(key[-1])];node(key,'rule',descriptions[int(key[-1])])
            edge(out[n['inputs'][0]],key,'判断阈值');out[key]=key
        elif op in {'AND','OR'}:
            node(key,'logic',op+('｜同时满足' if op=='AND' else '｜至少一项满足'))
            for incoming in n['inputs']:edge(out[incoming],key,'候选集合')
            out[key]=key
        elif op=='COUNT':
            node(key,'answer','入选数量\n'+str(task['truth']['count'])+' 个');edge('selection',key,'COUNT')
        elif op=='AVG':
            node(key,'answer','入选对象的'+avg+'平均值\n'+task['truth']['average_exact'])
            edge('selection',key,'限定对象');edge(out[n['inputs'][1]],key,'AVG')
    node('selected_entities','answer','入选对象\n'+'\n'.join(task['truth']['entities']))
    edge('selection','selected_entities','名单')
    export_diagram(private,profile['topic_zh']+'｜数值与逻辑依赖',nodes,edges,'公式先独立验证，再生成基础数据；不代表最短推理路径。')
    checks={'passed':all(c['passed'] for c in task['independent_numeric_checks']),
            'numeric_checks':task['independent_numeric_checks'],'negative_submissions':negative_submissions(task),
            'policy_scope':task['policy_scope'],'depth_scope':task['depth_scope']}
    checks['passed'] &= checks['negative_submissions']['passed']
    save(private/'task.json',jsonable(task));save(private/'truth.json',task['truth']);save(private/'evidence_map.json',mapping);save(private/'checks.json',checks)
    return {'passed':checks['passed'],'public':'public/report.html','diagram':'private/dependency_graph.png','graph_depth':task['graph_depth']}


def render_goal(directory,result):
    directory=Path(directory);nodes=[];edges=[]
    target_quantity=result.get('target',{'id':'revpar','zh':'RevPAR'})
    target_id=target_quantity['id']
    accepted=[p for p in result['paths'] if p['accepted']]
    quants={q['id']:q for q in result['quantities']}
    production_ids={p for route in accepted for p in route['production_ids']}
    productions=[p for p in result['productions'] if p['id'] in production_ids]
    used={m['id'] for route in accepted for m in route['proposal']['metrics']}
    for key in sorted(used):
        q=quants[key]
        nodes.append({'id':key,'kind':'answer' if key==target_id else ('evidence' if q['kind']=='base' else 'set'),
                      'label':q['zh']+'\n'+unit_text(q['unit'])})
    nodes.append({'id':'choose_target','kind':'logic','label':'OR｜任选一种合法计算方案\n不是集合并集'})
    edges.append({'source':'choose_target','target':target_id,'label':'同一目标指标'})
    for production in productions:
        serial=[0]
        def draw(expr):
            if 'ref' in expr:return expr['ref']
            serial[0]+=1;key=production['id']+'__'+str(serial[0])
            if 'const' in expr:nodes.append({'id':key,'kind':'evidence','label':str(expr['const'])});return key
            nodes.append({'id':key,'kind':'rule','label':expr['op']+'\nAND：全部输入必需'})
            for i,child in enumerate(expr['args']):
                label=(['分子','分母'][i] if expr['op']=='DIV' else (['被减数','减数'][i] if expr['op']=='SUB' else '输入'))
                edges.append({'source':draw(child),'target':key,'label':label})
            return key
        source=draw(production['formula']);target='choose_target' if production['output']==target_id else production['output']
        routes=[p for p in accepted if production['id'] in p['production_ids']]
        label='\n'.join(f"{p['operator_count']}算子 / 深度{p['dependency_depth']}" for p in routes) if target=='choose_target' else '派生指标'
        edges.append({'source':source,'target':target,'label':label})
    export_diagram(directory,'目标驱动反向构图｜'+target_quantity['zh']+'的合法路径',nodes,edges,'图示候选由模型提出并通过独立校验；算子数和深度分别标注。')
