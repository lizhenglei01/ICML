"""Builder-only diagrams generated from registered rules and executed values."""
from pathlib import Path
import html
import json
import shutil
import subprocess
import textwrap

from .chart_rules import execute,answers
from .topic_synthesis import chart_coordinates


COLORS={'evidence':'#eaf1fc','rule':'#efe5fb','logic':'#fff0c9','set':'#e4f3e8','answer':'#fce6e6'}


def quote(text):
    return json.dumps(str(text),ensure_ascii=False)


def atom_label(task,node):
    meta=task['metadata'];p=node['params'];kind=node['template']
    metrics={m['id']:m['zh'] for m in meta['metrics']};groups={g['id']:g['zh'] for g in meta['groups']}
    metric=metrics[p['metric']];group=groups[p['group']]
    sign='≥' if p.get('comparison')=='GE' else '≤'
    window=f"{p['window'][0]}—{p['window'][-1]}"
    base=[p['timestamp'],group]
    if kind in {'timestamp_threshold','metric_threshold','same_group_value_threshold'}:
        detail=base+[f"{metric} {sign} {p['threshold']} {meta['unit']['zh']}"]
    elif kind=='timestamp_vs_across_entity_agg':
        detail=base+[metric,sign+' 当前候选公司该指标的平均值']
    elif kind=='timestamp_vs_within_entity_time_agg':
        detail=base+[metric,sign+' 本公司在'+window+'的平均值']
    elif kind in {'two_point_change_threshold','two_point_percentage_change'}:
        detail=[group,metric,p['end']+' 相对 '+p['start'],
                ('变化率' if kind=='two_point_percentage_change' else '绝对差值')+f" {sign} {p['threshold']}"+('%' if kind=='two_point_percentage_change' else ' '+meta['unit']['zh'])]
    elif kind in {'window_monotone','window_slope_strict_monotone'}:
        detail=[window,group,metric,
                ('季度数值' if kind=='window_monotone' else '相邻季度差值')+('连续严格递增' if p['direction']=='up' else '连续严格递减')]
    elif kind in {'timestamp_extreme','single_metric_cross_entity_extreme','same_group_value_extreme','window_sum_extreme'}:
        detail=[window if kind=='window_sum_extreme' else p['timestamp'],group,
                metric+('的期间合计' if kind=='window_sum_extreme' else ''),
                '在当前候选范围内唯一'+('最高' if p['direction']=='up' else '最低')+'（排除并列）']
    elif kind in {'within_entity_metric_compare','metric_difference_threshold'}:
        detail=base+([metric,sign+' 本公司'+metrics[p['other_metric']]] if kind=='within_entity_metric_compare'
                     else [metric+' 减去',metrics[p['other_metric']],f"差值 {sign} {p['threshold']} {meta['unit']['zh']}"])
    elif kind in {'within_entity_group_compare','group_difference_threshold'}:
        detail=[p['timestamp'],metric,group+' 与 '+groups[p['other_group']],
                ('前者 '+sign+' 后者（同一公司）' if kind=='within_entity_group_compare'
                 else f"前者减后者 {sign} {p['threshold']} {meta['unit']['zh']}")]
    else:raise ValueError('Unsupported diagram template:'+kind)
    wrapped=[]
    for line in detail:wrapped.extend(textwrap.wrap(line,width=24,break_long_words=True,break_on_hyphens=False) or [''])
    return '\n'.join(wrapped)


def diagram_model(task,document):
    """No guessed relations: every rule/scope edge comes from task.json."""
    state=execute(task);truth=answers(task,state)
    names={s['node_id']:s['name_zh'] for s in document['sections']}
    nodes=[];edges=[]
    def add(key,kind,label,source=None):
        nodes.append({'id':key,'kind':kind,'label':label,'source':source})
    def edge(a,b,label):edges.append({'source':a,'target':b,'label':label})
    used={n['chart_id'] for n in task['nodes'] if n['op']=='ATOM'}
    q=task['average_query'];needed={ '|'.join([e,q['metric'],q['group'],q['timestamp']]) for e in task['metadata']['entities']}
    alternatives=[c['id'] for c in task['charts'] if needed<=set(chart_coordinates(task['metadata'],c))]
    if not alternatives:raise ValueError('No chart provides average evidence')
    average_chart=alternatives[-1];used.add(average_chart)
    metric_names={m['id']:m['zh'] for m in task['metadata']['metrics']}
    group_names={g['id']:g['zh'] for g in task['metadata']['groups']}
    for chart in task['charts']:
        if chart['id'] not in used:continue
        number=chart['id'].split('_')[-1]
        label=[f"数据图 {number}｜"+{'line':'时序图','multi_metric_bar':'多指标柱状图','group_bar':'分组柱状图'}[chart['kind']],
               *[metric_names[m] for m in chart['metrics']],*[group_names[g] for g in chart['groups']],
               '—'.join([chart['timestamps'][0],chart['timestamps'][-1]]) if len(chart['timestamps'])>1 else chart['timestamps'][0]]
        add(chart['id'],'evidence','\n'.join(label),'public/'+chart['id']+'.svg')
    for n in task['nodes']:
        rule='rule_'+n['id'];result='set_'+n['id']
        if n['op']=='ATOM':
            head='筛选规则 '+n['id'].split('_')[-1]
            if not n['inputs']:head+='｜全部'+str(len(task['metadata']['entities']))+'个对象'
            add(rule,'rule',head+'\n'+atom_label(task,n),n['id'])
            edge(n['chart_id'],rule,'读取指标数据')
            for previous in n['inputs']:edge('set_'+previous,rule,'仅在此组继续筛选')
        else:
            add(rule,'logic',('AND｜同时满足\n取交集' if n['op']=='AND' else 'OR｜至少满足一组\n取并集，含同时满足'),n['id'])
            for previous in n['inputs']:edge('set_'+previous,rule,'候选组输入')
        add(result,'set',names[n['id']]+'\n计算结果：'+str(len(state[n['id']]))+'个对象',n['id'])
        edge(rule,result,'得到候选集合')
    selected='set_'+task['selection']
    entity_lines=truth['entities'][:4]
    if len(truth['entities'])>4:entity_lines+=['另有'+str(len(truth['entities'])-4)+'个，详见 truth.json']
    add('answer_entities','answer','问题①｜最终名单\n'+'\n'.join(entity_lines),'truth.entities')
    add('answer_count','answer',f"问题②｜成员数量\n{truth['count']} 个",'truth.count')
    add('average_rule','rule','问题③｜对最终名单求平均\n'+q['timestamp']+'｜'+group_names[q['group']]+'\n'+metric_names[q['metric']],'average_query')
    add('answer_average','answer','问题③｜平均值\n'+truth['average_exact']+' '+task['metadata']['unit']['zh'],'truth.average_exact')
    edge(selected,'answer_entities','输出名单');edge(selected,'answer_count','COUNT｜计数')
    edge(selected,'average_rule','限定统计对象');edge(average_chart,'average_rule','读取同一批对象的指标')
    edge('average_rule','answer_average','AVG｜求平均')
    return {'title':document['title_zh']+'｜证据与计算关系图','nodes':nodes,'edges':edges,
            'private':True,'average_alternative_sources':alternatives,
            'scope':'Registered source bindings and executed selection dependencies; not a proof of minimum evidence or real-world causality.'}


def export_graph(directory,task,document):
    directory=Path(directory);private=directory/'private';private.mkdir(parents=True,exist_ok=True)
    graph=diagram_model(task,document)
    caption='<<B>'+html.escape(graph['title'])+'</B><BR/><FONT POINT-SIZE="13" COLOR="#667891">蓝＝数据证据　紫＝规则　黄＝AND／OR　绿＝候选集合　红＝答案；从下往上阅读</FONT><BR/><FONT POINT-SIZE="12" COLOR="#995555">构建端私有：包含隐藏结果，不交给被测模型；连线为已登记的程序依赖，不代表真实业务因果。</FONT>>'
    lines=['digraph Evidence {','graph [rankdir=BT, bgcolor="white", nodesep=0.55, ranksep=0.7, pad=0.35, splines=polyline, dpi=115, fontname="Noto Sans CJK SC", fontsize=22, labelloc=t, label='+caption+'];',
           'node [shape=box, style="rounded,filled", color="#91a2bd", penwidth=1.1, fontname="Noto Sans CJK SC", fontsize=14, margin="0.18,0.12"];',
           'edge [color="#6c7d99", arrowsize=0.7, fontname="Noto Sans CJK SC", fontsize=11, fontcolor="#596d8b"];']
    for node in graph['nodes']:
        lines.append(quote(node['id'])+' [fillcolor='+quote(COLORS[node['kind']])+', label='+quote(node['label'])+'];')
    sources=[quote(n['id']) for n in graph['nodes'] if n['kind']=='evidence']
    lines.append('{rank=same; '+'; '.join(sources)+';}')
    lines.append('{rank=same; "answer_entities"; "answer_count"; "answer_average";}')
    for e in graph['edges']:lines.append(quote(e['source'])+' -> '+quote(e['target'])+' [label='+quote(e['label'])+'];')
    lines.append('}')
    dot=private/'dependency_graph.dot';dot.write_text('\n'.join(lines),encoding='utf-8')
    (private/'dependency_graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    dot_command=shutil.which('dot')
    if not dot_command:raise RuntimeError('Graphviz dot is required for dependency diagrams')
    for format in ['svg','png']:
        subprocess.run([dot_command,'-T'+format,str(dot),'-o',str(private/('dependency_graph.'+format))],check=True,capture_output=True,text=True)
    legend='蓝色：数据证据　紫色：筛选或统计规则　黄色：AND／OR组合　绿色：候选集合　红色：最终答案。箭头按数据→规则→结果阅读。'
    note='仅供构建端查看，包含隐藏结果，不交给被测模型。实线表示已登记并执行的程序依赖；不表示真实业务因果关系或最短必要证据链。'
    page='<!doctype html><meta charset="utf-8"><title>'+html.escape(graph['title'])+'</title><style>body{font:17px sans-serif;margin:2em;line-height:1.6}img{max-width:100%}a{margin-right:2em}</style><h1>'+html.escape(graph['title'])+'</h1><p>'+legend+'</p><p>'+note+'</p><p><a href="dependency_graph.svg">打开可缩放SVG</a><a href="dependency_graph.png">打开PNG</a><a href="dependency_graph.json">查看图结构JSON</a></p><img src="dependency_graph.svg">'
    (private/'dependency_graph.html').write_text(page,encoding='utf-8')
    return {'nodes':len(graph['nodes']),'edges':len(graph['edges']),'viewer':'private/dependency_graph.html',
            'png':'private/dependency_graph.png','svg':'private/dependency_graph.svg','private':True}


def gallery(root):
    root=Path(root);cards=[]
    for taskfile in sorted(root.glob('*/task_*/private/task.json')):
        private=taskfile.parent
        if not (private/'dependency_graph.svg').exists():continue
        doc=json.loads((private/'document.json').read_text());relative=private.relative_to(root).as_posix()
        cards.append('<article><h2>'+html.escape(doc['title_zh'])+'</h2><a href="'+relative+'/dependency_graph.html"><img loading="lazy" src="'+relative+'/dependency_graph.png"></a></article>')
    page='<!doctype html><meta charset="utf-8"><title>依赖图总览</title><style>body{font:17px sans-serif;margin:2em}article{border:1px solid #ddd;padding:1em;margin:2em 0}img{max-width:100%;max-height:600px}</style><h1>依赖图总览｜构建端私有</h1><p>点击任一图查看大图、SVG和结构JSON。包含标准答案，不作为模型公开输入。</p>'+''.join(cards)
    path=root/'依赖图总览.html';path.write_text(page,encoding='utf-8');return path


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent/'topic_artifacts')
    args=parser.parse_args()
    for path in sorted(args.root.glob('*/task_*/private/task.json')):
        task=json.loads(path.read_text());doc=json.loads((path.parent/'document.json').read_text())
        export_graph(path.parent.parent,task,doc)
    print(gallery(args.root))
