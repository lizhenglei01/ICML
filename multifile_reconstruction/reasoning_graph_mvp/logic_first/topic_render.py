"""Render natural authored reports with actual multi-series chart evidence."""
import hashlib
import html
import json
from pathlib import Path
import textwrap
import xml.etree.ElementTree as ET

from .chart_rules import coordinate
from .topic_synthesis import chart_coordinates
from .topic_verify import audit_task,negative_controls
from .topic_author import validate_document,writer_spec


def render_chart(path,task,chart,preview=False):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.font_manager import FontProperties
    meta=task['metadata'];entities=meta['entities']
    metrics={m['id']:m for m in meta['metrics']};groups={g['id']:g for g in meta['groups']}
    font=FontProperties(fname='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    def value(e,m,g,t):return task['facts'][coordinate(e,m,g,t)]
    with plt.rc_context({'svg.fonttype':'none','svg.hashsalt':chart['id']}):
        if chart['kind']=='line':
            fig,axes=plt.subplots(5,2,figsize=(12,13),sharey=True)
            metric=chart['metrics'][0];group=chart['groups'][0];times=chart['timestamps']
            for entity,ax in zip(entities,axes.flat):
                values=[value(entity,metric,group,t) for t in times]
                ax.plot(times,values,marker='o',color='#346e9a')
                for i,v in enumerate(values):ax.annotate(str(v),(i,v),xytext=(0,6),textcoords='offset points',ha='center',fontsize=8)
                ax.set_title(entity,fontsize=9);ax.tick_params(labelsize=7);ax.set_ylim(0,280)
            title=metrics[metric]['zh']+' / '+metrics[metric]['en']+'\n'+groups[group]['zh']+' / '+groups[group]['en']
            fig.suptitle(title,fontproperties=font,fontsize=12)
            fig.tight_layout(rect=[0,0,1,.94])
        else:
            fig,ax=plt.subplots(figsize=(13,5))
            x=np.arange(len(entities));t=chart['timestamps'][0]
            series=chart['metrics'] if chart['kind']=='multi_metric_bar' else chart['groups']
            width=.8/len(series)
            for i,series_id in enumerate(series):
                m=series_id if chart['kind']=='multi_metric_bar' else chart['metrics'][0]
                g=chart['groups'][0] if chart['kind']=='multi_metric_bar' else series_id
                item=metrics[m] if chart['kind']=='multi_metric_bar' else groups[g]
                vals=[value(e,m,g,t) for e in entities]
                bars=ax.bar(x+(i-(len(series)-1)/2)*width,vals,width,label=item['zh']+' / '+item['en'])
                ax.bar_label(bars,labels=[str(v) for v in vals],fontsize=6)
            ax.set_xticks(x,['\n'.join(textwrap.wrap(e,16)) for e in entities],fontsize=7)
            if chart['kind']=='multi_metric_bar':subtitle=groups[chart['groups'][0]]
            else:subtitle=metrics[chart['metrics'][0]]
            ax.set_title(subtitle['zh']+' / '+subtitle['en']+' — '+t,fontproperties=font,fontsize=11)
            ax.legend(prop=font,fontsize=7,loc='upper left',bbox_to_anchor=(0,-.2));ax.set_ylim(0,290)
            fig.tight_layout()
        fig.text(.01,.005,meta['unit']['zh']+' / '+meta['unit']['en'],fontproperties=font,fontsize=9)
        fig.savefig(path,format='svg',metadata={'Date':None,'Description':chart['id']})
        if preview:fig.savefig(Path(path).with_suffix('.png'),dpi=110)
        plt.close(fig)


def render_topic_bundle(directory,task,document,preview=False):
    from .topic_run import save
    directory=Path(directory);public=directory/'public';public.mkdir(parents=True,exist_ok=True)
    validate_document(writer_spec(task,document['task_id']),document)
    chunks=["<!doctype html><meta charset='utf-8'><title>"+html.escape(document['title_en'])+'</title>',
            '<style>body{max-width:1100px;margin:2em auto;font:17px sans-serif;line-height:1.65}img{width:100%}.en{color:#455}section{margin:2em 0}</style>',
            '<h1>'+html.escape(document['title_zh'])+'</h1><h2>'+html.escape(document['title_en'])+'</h2>',
            '<p>'+html.escape(document['intro_zh'])+'</p><p class="en">'+html.escape(document['intro_en'])+'</p>']
    mapping=[];full_text=[]
    nodes={n['id']:n for n in task['nodes']};shown=set();chartmap={c['id']:c for c in task['charts']}
    for index,section in enumerate(document['sections']):
        anchor=f'policy_{index+1}'
        chunks.append(f'<section id="{anchor}"><h2>'+html.escape(section['name_zh'])+' / '+html.escape(section['name_en'])+'</h2><p>'+html.escape(section['text_zh'])+'</p><p class="en">'+html.escape(section['text_en'])+'</p></section>')
        full_text.extend([section['name_zh'],section['name_en'],section['text_zh'],section['text_en']])
        mapping.append({'node_id':section['node_id'],'evidence_id':anchor,'location':'report.html#'+anchor})
        chart_id=nodes[section['node_id']].get('chart_id')
        if chart_id and chart_id not in shown:
            chunks.append(f'<figure><img src="{chart_id}.svg" alt="{chart_id}"><figcaption>数据图 / Evidence chart {len(shown)+1}</figcaption></figure>')
            shown.add(chart_id)
    for chart_id in sorted(chartmap.keys()-shown):
        chunks.append(f'<figure><img src="{chart_id}.svg" alt="{chart_id}"></figure>')
    last=document['sections'][-1];q=task['average_query'];meta=task['metadata']
    metric=next(m for m in meta['metrics'] if m['id']==q['metric']);group=next(g for g in meta['groups'] if g['id']==q['group'])
    questions=[f"哪些{meta['entity_type_zh']}进入“{last['name_zh']}”？ / Which {meta['entity_type_en']} belong to {last['name_en']}?",
               f"“{last['name_zh']}”有多少个成员？ / How many members belong to {last['name_en']}?",
               f"这些成员在{q['timestamp']}、{group['zh']}的{metric['zh']}平均是多少？ / What is their mean {metric['en']} for {group['en']} in {q['timestamp']}?"]
    chunks+=['<h2>问题 / Questions</h2>']+['<p>'+html.escape(q)+'</p>' for q in questions]
    page='\n'.join(chunks)
    (public/'report.html').write_text(page,encoding='utf-8')
    (public/'report.txt').write_text('\n\n'.join([document['title_zh'],document['title_en'],document['intro_zh'],document['intro_en'],*full_text,*questions]),encoding='utf-8')
    checks=[]
    for chart in task['charts']:
        path=public/(chart['id']+'.svg')
        render_chart(path,task,chart,preview=preview and chart==task['charts'][0])
        root=ET.parse(path).getroot();ns='{http://www.w3.org/2000/svg}'
        texts=[''.join(e.itertext()) for e in root.iter(ns+'text')]
        textgroups=[''.join(''.join(e.itertext()) for e in group.iter(ns+'text'))
                    for group in root.iter(ns+'g') if group.get('id','').startswith('text_')]
        chart_facts={c:task['facts'][c] for c in chart_coordinates(meta,chart)}
        present=all(str(v) in texts for v in chart_facts.values())
        # Line panels use exact entity titles; bar charts wrap long names.
        present &= all(any(''.join(e.split())==''.join(t.split()) for t in textgroups) for e in meta['entities'])
        checks.append({'name':'chart_numeric_and_entity_readback:'+chart['id'],'passed':bool(present),
                       'facts_count':len(chart_facts),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        mapping.append({'chart_id':chart['id'],'location':path.name,'kind':chart['kind'],
                        'source_coordinates':chart_facts})
    rendered=(public/'report.html').read_text()
    checks.append({'name':'authored_prose_present','passed':all(html.escape(s[k]) in rendered for s in document['sections'] for k in ['text_zh','text_en'])})
    # No private node tables, desired sets or truth JSON are placed in public.
    allowed={'report.html','report.txt'}|{c['id']+'.svg' for c in task['charts']}
    if preview:allowed.add(task['charts'][0]['id']+'.png')
    checks.append({'name':'public_file_allowlist','passed':all(p.name in allowed for p in public.iterdir())})
    author_input=writer_spec(task,document['task_id'])
    checks.append({'name':'writer_answer_blind','passed':'entities' not in author_input['metadata'] and not ({'facts','truth','answers','intended_sets'} & set(author_input))})
    policy_text='\n'.join(full_text+[document['intro_zh'],document['intro_en']])
    checks.append({'name':'no_qualifying_entity_names_in_policy','passed':not any(e in policy_text for e in meta['entities'])})
    graph=audit_task(task);negative=negative_controls(task)
    language=json.loads((directory/'private'/'language_audit.json').read_text())['audit']
    result={'passed':all(c['passed'] for c in checks) and graph['passed'] and negative['passed'] and language['passed'],
            'artifact_checks':checks,'graph_checks':graph,'negative_controls':negative,'model_assisted_language_audit':language,
            'visual_review':'Not automatically certified; SVG text readback is not a full layout/geometry audit.'}
    save(directory/'private'/'evidence_map.json',mapping);save(directory/'private'/'checks.json',result)
    from .graph_view import export_graph
    result['dependency_diagram']=export_graph(directory,task,document)
    save(directory/'private'/'checks.json',result)
    return result
