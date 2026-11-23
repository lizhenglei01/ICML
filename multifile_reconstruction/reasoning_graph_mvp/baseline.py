"""Deterministic typed enumeration, explicitly distinct from LLM generation."""
from itertools import product
from .operators import OPERATORS, result_unit, InvalidGraph
from .verifier import verify


def to_graph(tree):
    quantities=[{"id":"gross_profit_margin","concept":"gross_profit_margin","unit":"ratio"}]
    productions=[]
    def visit(node,root=False):
        if "fact" in node:return node["fact"]
        args=[visit(x) for x in node["args"]]
        name="gross_profit_margin" if root else "aux_"+str(len(productions))
        if not root:
            power=node["unit"]
            quantities.append({"id":name,"concept":"auxiliary","unit":"ratio" if power==0 else "money^"+str(power)})
        productions.append({"id":"op_"+str(len(productions)),"output":name,"op":node["op"],"inputs":args})
        return name
    visit(tree,True)
    return {"target":"gross_profit_margin","quantities":quantities,"productions":productions}


def search(task,max_operators=2):
    from fractions import Fraction
    by_size={0:[{"fact":k,"unit":Fraction(1)} for k in task["known_facts"]]}
    attempted=0; accepted=[]; seen_sets=set()
    for size in range(1,max_operators+1):
        terms=[]
        for op,spec in OPERATORS.items():
            splits=[(size-1,)] if spec.arity==1 else [(left,size-1-left) for left in range(size)]
            for split in splits:
                for args in product(*(by_size.get(n,[]) for n in split)):
                    try: power=result_unit(op,[a["unit"] for a in args])
                    except InvalidGraph:continue
                    term={"op":op,"args":list(args),"unit":power};terms.append(term)
                    if power!=0:continue
                    graph=to_graph(term);attempted+=1; report=verify(graph,task)
                    if report["accepted"]:
                        key=tuple(report["minimal_input_sets"][0])
                        if key not in seen_sets:
                            accepted.append({"graph":graph,"validation":report});seen_sets.add(key)
        by_size[size]=terms
    return {"origin":"deterministic_program_search", "llm_generated":False,"candidate_count":attempted,
            "accepted":accepted,"minimal_input_sets":sorted([list(s) for s in seen_sets])}
