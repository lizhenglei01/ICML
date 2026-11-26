"""Read-only inspection of the frozen hotel and procurement examples.

This is an independent review of TWO known themes, not an arbitrary-domain
business validator. Arithmetic checks implement the published four-decimal
half-even convention without importing the builder or its execution engine.
Qualitative concerns are observations to review, not model grading criteria.
Only the output directory is written. It must be ignored by this repository.
No fixed business answers, network calls, or model calls are embedded here.

Example:
    python automatic_business_review.py --out artifacts/business_review_repeat
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "artifacts/automatic_business_20261126T140730686Z/verified"
DEFAULT_OUTPUT = ROOT / "artifacts/automatic_business_review_20261126"
SOURCE_COMMIT = "8c898ebbde95240e08e85f99ffa0fc137285197b"
CASES = ("hotel", "procurement")


def frac(x):
    return Fraction(x)


def rounded(x):
    with localcontext() as c:
        c.prec = 60
        return Fraction(
            (Decimal(x.numerator) / Decimal(x.denominator)).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_EVEN
            )
        )


def fmt(x):
    if isinstance(x, dict):
        return "；".join(f"{k}={fmt(v)}" for k, v in x.items())
    if isinstance(x, list):
        return "、".join(x) or "空集"
    if isinstance(x, (int, float)):
        return str(x)
    try:
        q = Fraction(x)
        return f"{float(q):.4f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return str(x)


def normal(x):
    if isinstance(x, dict):
        return {k: normal(v) for k, v in x.items()}
    if isinstance(x, list):
        return sorted(x)
    try:
        return frac(x)
    except (ValueError, TypeError):
        return x


def audit(g, t):
    checks = []
    for phase in ("before", "after"):
        known = {k: normal(t["nodes"][k][phase]) for k in g["base"]}
        for k, v in g["base"].items():
            for e, x in known[k].items():
                checks.append(
                    {
                        "type": "base_range",
                        "phase": phase,
                        "node": k,
                        "entity": e,
                        "pass": frac(v["min"]) <= x <= frac(v["max"])
                        and ((x - frac(v["min"])) / frac(v["step"])).denominator == 1,
                    }
                )
        for constraint in g["constraints"]:
            for e in g["entities"]:
                checks.append(
                    {
                        "type": "subset_constraint",
                        "phase": phase,
                        "node": constraint["left"],
                        "entity": e,
                        "pass": known[constraint["left"]][e]
                        <= known[constraint["right"]][e],
                    }
                )
        known.update(
            {
                f"threshold_{k}": frac(v["threshold"])
                for k, v in g["policy"]["conditions"].items()
            }
        )
        for p in g["productions"]:
            args = [known[k] for k in p["inputs"]]
            op = p["op"]
            if op in {"ADD", "SUB", "MUL", "DIV"}:
                f = {
                    "ADD": lambda x, y: x + y,
                    "SUB": lambda x, y: x - y,
                    "MUL": lambda x, y: x * y,
                    "DIV": lambda x, y: x / y,
                }[op]
                value = {e: rounded(f(args[0][e], args[1][e])) for e in g["entities"]}
            elif op == "COMPARE":
                compare = {"GE": lambda x, y: x >= y, "LE": lambda x, y: x <= y}[
                    p["comparison"]
                ]
                value = sorted(e for e in g["entities"] if compare(args[0][e], args[1]))
            elif op == "AND":
                value = sorted(set(args[0]) & set(args[1]))
            elif op == "COUNT":
                value = len(args[0])
            elif op == "AVG":
                value = rounded(sum(args[1][e] for e in args[0]) / len(args[0]))
            else:
                raise ValueError(op)
            known[p["output"]] = value
            checks.append(
                {
                    "type": "production",
                    "phase": phase,
                    "node": p["output"],
                    "pass": normal(value) == normal(t["nodes"][p["output"]][phase]),
                }
            )
    return checks


def docx_text(path):
    with ZipFile(path) as package:
        tree = ET.fromstring(package.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return ["".join(p.itertext()) for p in tree.findall(".//w:p", ns)]


def quality_findings(cases, source):
    """Record known concerns, deriving every example value from the actual files."""
    hotel, ht = cases["hotel"]
    procurement, pt = cases["procurement"]
    who = ht["event"]["entity"]
    hn = ht["nodes"]
    revenue = frac(hn["room_revenue"]["before"][who])
    expense = frac(hn["room_operating_expense"]["before"][who])
    paid = frac(hn["paid_room_nights"]["before"][who])
    pn = pt["nodes"]
    failure_rows = {}
    for entity in procurement["entities"]:
        failed = frac(pn["failed_items"]["before"][entity])
        inspected = frac(pn["inspected_items"]["before"][entity])
        failure_rows[entity] = {
            "failed": int(failed),
            "inspected": int(inspected),
            "share": float(failed / inspected),
        }
    # Ranking is descriptive, not an industry-valid acceptance threshold.
    high_failure = dict(
        sorted(failure_rows.items(), key=lambda x: x[1]["share"], reverse=True)[:2]
    )
    event_values = {}
    for case, (graph, truth) in cases.items():
        event = truth["event"]
        before, after = frac(event["before"]), frac(event["after"])
        event_values[case] = {
            "before": event["before"],
            "after": event["after"],
            "relative_reduction": float((before - after) / before),
        }
    texts = {
        case: docx_text(
            source / case / "case/layouts/deep_cross_file/public/policy.docx"
        )
        for case in CASES
    }
    graph_sizes = {
        f"{case}_{kind}": len(graph[kind])
        for case, (graph, _) in cases.items()
        for kind in ("nodes", "productions")
    }
    graph_sizes["backward_additional_paths"] = sum(
        len(g["backward"]["additional_paths"]) for g, _ in cases.values()
    )
    return [
        {
            "id": "BR01",
            "case": "hotel",
            "severity": "quality",
            "summary": f"{who}营业费用与收入的比例需结合业务规模审查",
            "evidence": {
                "room_revenue": float(revenue),
                "room_operating_expense": float(expense),
                "expense_income_share": float(expense / revenue),
                "expense_per_paid_night": float(expense / paid),
            },
            "interpretation": "费用定义包括人工、布草、清洁、能耗和分摊费用。程序只验证金额范围及间夜总分关系，没有验证费用能否支撑相应运营规模；本核查不据此断言业务不可能。",
        },
        {
            "id": "BR02",
            "case": "procurement",
            "severity": "quality",
            "summary": "采购样本最高两项质检失败比例及其业务背景待审查",
            "evidence": high_failure,
            "interpretation": "原冻结样本两个比例接近100%，满足计数约束，未提供供应商持续经营或特殊事故背景。不能把这些样本视为具有行业代表性。",
        },
        {
            "id": "BR03",
            "case": "both",
            "severity": "quality",
            "summary": "自动事件通过减少分母改变资格，需要解释账本更正理由",
            "evidence": event_values,
            "interpretation": "原冻结题面明确为获批准的账本更正，因此逻辑上可以成立；但没有维修台账或合同明细来验证更正理由，不应描述为真实经营绩效提升。",
        },
        {
            "id": "BR04",
            "case": "both",
            "severity": "expression",
            "summary": "部分指标名和单位不符合常见业务表述习惯",
            "evidence": [
                "每付费入住间夜客房收入缺少显式的平均二字",
                "酒店间夜被渲染为间·天",
                "采购批次数单位为batch，入选数单位为entity",
                "Word长段将全部指标定义串联，查阅负担较大",
            ],
            "actual_word_paragraphs": texts,
            "interpretation": "公式和范围仍可恢复。以上为本次冻结样本的审查笔记，实际文本一并保留。此次只记录问题，不改冻结构建器、题面或评分器。",
        },
        {
            "id": "BR05",
            "case": "both",
            "severity": "scope",
            "summary": "业务依据目前是明示合成定义及合成政策",
            "evidence": {
                case: {
                    "base_concepts": {
                        k: v.get("concept") for k, v in g["base"].items()
                    },
                    "derived_authorities": sorted(
                        {
                            v["meaning"]["authority"]
                            for v in g["nodes"].values()
                            if v.get("meaning")
                        }
                    ),
                    "policy_origin": g["policy"]["origin"],
                }
                for case, (g, _) in cases.items()
            },
            "interpretation": "独立数值复算能证明给定定义下的一致性，不能独立证实模型给出的名称和定义是行业标准；政策门槛来自合成提案，资产有合成政策标记。",
        },
        {
            "id": "BR06",
            "case": "both",
            "severity": "scope",
            "summary": "原冻结两个图均为两条件AND及COUNT/AVG，反向路径未增加替代路线",
            "evidence": graph_sizes,
            "interpretation": "验证了两主题相同构建接口，可支持本轮比较，但不能代表复杂逻辑树、多路径或广泛业务分布。",
        },
    ]


def read_cases(source):
    cases = {}
    expected = {
        "hotel": {
            "available_room_nights",
            "paid_room_nights",
            "room_revenue",
            "room_operating_expense",
        },
        "procurement": {
            "due_batches",
            "ontime_batches",
            "inspected_items",
            "failed_items",
        },
    }
    for case in CASES:
        folder = source / case / "case/private"
        graph = json.loads((folder / "graph.json").read_text(encoding="utf-8"))
        truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
        if set(graph["base"]) != expected[case]:
            raise ValueError(
                f"{case}: this review supports the two frozen schemas only"
            )
        if any(x["op"] != "LE" for x in graph["constraints"]):
            raise ValueError("Unsupported constraint in this scoped review")
        if any(
            p.get("decimal_places") != 4
            for p in graph["productions"]
            if p["op"] in {"ADD", "SUB", "MUL", "DIV", "AVG"}
        ):
            raise ValueError(
                "This review requires the published four-decimal convention"
            )
        cases[case] = (graph, truth)
    return cases


def validate_output(source, output):
    if (
        output == source
        or output.is_relative_to(source)
        or source.is_relative_to(output)
    ):
        raise ValueError("Review output must be separate from the source tree")
    if not output.is_relative_to(ROOT):
        raise ValueError(
            "Place private review output inside an ignored repository directory"
        )
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", str(output / "business_review.json")],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Output is not Git-ignored; refusing to write private GT")


def run_review(source, output, source_commit):
    validate_output(source, output)
    cases = read_cases(source)
    issues = quality_findings(cases, source)
    output.mkdir(parents=True, exist_ok=True)
    report = [
        "# 冻结案例业务合理性与完整 GT 核查",
        "",
        f"审查对象为构建器提交 `{source_commit}` 生成并归档的酒店和采购两个案例。未修改构建器、原始 Office 文件、题面、GT 或评分器，未调用 Agent。本报告包含私有答案，仅保存在被 Git 忽略的本地目录。",
        "",
        "## 核查结论",
        "",
        "数值复算的通过情况见下方逐例结果和JSON汇总，出现不一致时进程返回非零。生成质量尚不足以宣称真实业务代表性，审查关注酒店费用、质检失败比例和分母更正。它们是质量与覆盖范围问题，需结合实际数值判断，不能直接等同于算术错误。",
        "",
        "## 核查方法与边界",
        "",
        "本脚本是针对两个既有主题的独立审查，不是任意领域验证器。直接读取原始 `graph.json`、`truth.json`、生成模型的结构化提案及实际 Word 文本。另写一个独立于项目执行器的有理数计算脚本，从基础记录重算已生成的 SUB、DIV、COMPARE、AND、COUNT、AVG；每步按已公开的四位小数半值取偶规则处理。此复算没有重新证明行业定义或真实政策有效性。",
        "",
        "所有数值表直接来自冻结私有 GT。百分比表述将比例乘100，仅用于阅读；源 GT 保留有理数。公式图包含全部派生指标，包括本次目标没有使用的平均客房收入分支。",
        "",
    ]
    summary = {
        "scope": "read-only local review of frozen actual artifacts",
        "source_commit": source_commit,
        "cases": {},
        "issues": issues,
    }
    rows = []
    all_checks = []
    for case in CASES:
        folder = source / case
        g, t = cases[case]
        checks = audit(g, t)
        all_checks += checks
        sources = {
            (
                str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
            ): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                folder / "case/private/graph.json",
                folder / "case/private/truth.json",
                folder / "case/layouts/deep_cross_file/public/instruction.md",
                folder / "case/layouts/deep_cross_file/public/policy.docx",
            ]
        }
        summary["cases"][case] = {
            "sources_sha256": sources,
            "graph_path": str(folder / "case/private/graph.json"),
            "graph_image": str(folder / "case/private/dependency_graph.png"),
            "graph_svg": str(folder / "case/private/dependency_graph.svg"),
            "truth_path": str(folder / "case/private/truth.json"),
            "node_count": len(g["nodes"]),
            "productions": g["productions"],
            "base_definitions": g["base"],
            "event": t["event"],
            "policy": g["policy"],
            "targets": {"before": t["before"], "after": t["after"]},
            "node_truth": t["nodes"],
            "independent_checks": checks,
            "passed": all(x["pass"] for x in checks),
        }
        report += [
            f"## {'酒店' if case=='hotel' else '供应商采购'}案例",
            "",
            f"主题标题：{g['title']}。统计期间：{g['period']}。对象：{'、'.join(g['entities'])}。",
            "",
            f"完整图：[{case} PNG]({folder/'case/private/dependency_graph.png'}) · [SVG]({folder/'case/private/dependency_graph.svg'}) · [结构化图]({folder/'case/private/graph.json'})。",
            "",
            f"依赖图包含 {len(g['nodes'])} 个节点、{len(g['productions'])} 条计算/判断/汇总规则。反向确认 {len(g['backward']['verified_paths'])} 条路径，新增替代路径 {len(g['backward']['additional_paths'])} 条。",
            "",
            "### 基础指标的原始定义",
            "",
            "| 节点 | 业务含义 | 单位 | 允许范围 |",
            "|---|---|---|---|",
        ]
        for key, v in g["base"].items():
            report.append(
                f"| `{key}` | {v['label']} | `{json.dumps(v['unit'],ensure_ascii=False)}` | {v['min']}—{v['max']}，步长{v['step']} |"
            )
        report += [
            "",
            "### 完整计算关系",
            "",
            "| 输出节点 | 业务名称 | 运算 | 输入节点 |",
            "|---|---|---|---|",
        ]
        for p in g["productions"]:
            report.append(
                f"| `{p['output']}` | {g['nodes'][p['output']]['label']} | {p['op']} {p.get('comparison','')} | {' + '.join('`'+x+'`' for x in p['inputs'])} |"
            )
        report += [
            "",
            f"政策原文：{g['policy']['selection']['label']}。",
            "",
            "政策阈值属于模型合成的内部资格规则，不是引用的行业标准。两个输出汇总均为 COUNT 与等权 AVG，等权平均不是按间夜、件数或批次加权。",
            "",
            "### 实际全部业务数据",
            "",
        ]
        metric_keys = list(g["base"]) + list(g["metrics"])
        for phase, phlabel in [("before", "更正前"), ("after", "更正后")]:
            report += [
                f"**{phlabel}**",
                "",
                "| 对象 | "
                + " | ".join(g["nodes"][k]["label"] for k in metric_keys)
                + " |",
                "|---|" + "---:|" * len(metric_keys),
            ]
            for e in g["entities"]:
                report.append(
                    "| "
                    + e
                    + " | "
                    + " | ".join(fmt(t["nodes"][k][phase][e]) for k in metric_keys)
                    + " |"
                )
            report.append("")
        ev = t["event"]
        report += [
            "### 自动反事实与最终 GT",
            "",
            f"更正 {ev['entity']} 的 `{ev['node_id']}`：{ev['before']} → {ev['after']}。该事件在公开题面中明确为“已批准更正”，其余基础记录保持不变。",
            "",
            "| 目标 | 修改前 | 修改后 |",
            "|---|---|---|",
        ]
        for k in g["targets"]:
            report.append(
                f"| {g['nodes'][k]['label']} | {fmt(t['before'][k])} | {fmt(t['after'][k])} |"
            )
        report += [
            "",
            "### 每个节点的前后 GT 与修改边界",
            "",
            "节点状态中的 related 按图可达性标注，preserved 表示实际值未改变。表中的向量按对象逐一列出。",
            "",
            "| 节点 | 类型 | 前 GT | 后 GT | 状态 | 图上相关 |",
            "|---|---|---|---|---|---|",
        ]
        for k, v in t["nodes"].items():
            report.append(
                f"| `{k}` | {g['nodes'][k]['kind']} | {fmt(v['before'])} | {fmt(v['after'])} | {v['status']} | {v['related']} |"
            )
            es = g["entities"] if isinstance(v["before"], dict) else [None]
            for e in es:
                b = v["before"][e] if e else v["before"]
                a = v["after"][e] if e else v["after"]
                rows.append(
                    {
                        "case": case,
                        "node_id": k,
                        "label": g["nodes"][k]["label"],
                        "kind": g["nodes"][k]["kind"],
                        "entity": e or "",
                        "before_exact": json.dumps(b, ensure_ascii=False),
                        "after_exact": json.dumps(a, ensure_ascii=False),
                        "before_display": fmt(b),
                        "after_display": fmt(a),
                        "node_status": v["status"],
                        "node_related": v["related"],
                        "entry_changed": b != a,
                    }
                )
        report += [
            "",
            f"独立复算检查 {sum(x['pass'] for x in checks)}/{len(checks)} 通过，其中节点计算 {sum(x['type']=='production' for x in checks)} 项，其他是基础范围及总分关系检查。这些检查数不代表独立业务功能数。",
            "",
        ]
    report += ["## 需要人工关注的质量问题", ""]
    for x in issues:
        report += [
            f"### {x['id']} {x['summary']}",
            "",
            f"范围：{x['case']}。分类：{x['severity']}。",
            "",
            json.dumps(x["evidence"], ensure_ascii=False),
            "",
            x["interpretation"],
            "",
        ]
    report += [
        "## 本轮推荐判读",
        "",
        "保留当前任务作为冻结的探索性样本。模型成绩只评价能否按明示的合成业务口径修订文件。先分别报告业务结果、文件修改、图表一致性和受保护对象；质量疑点和评分争议另列，不将事后新增的现实性要求追溯为模型失败。",
        "原冻结酒店有一个“图上相关但实际无需改动”的条件节点，采购质量条件则与本次更正无图上依赖。具体状态以本报告逐节点GT表为准，不可把两者混称为同一类保护测试。",
        "两主题只有一个案例、每例四种布局；每个布局一次模型提交只足以观察首轮差异，不能推断稳定区分能力或因果难度效果。",
        "",
        "完整逐节点表另存 `node_gt.csv`，机器可读审查 `business_review.json`，全部源文件哈希也在 JSON 内。",
    ]
    summary["independent_check_totals"] = {
        "passed": sum(x["pass"] for x in all_checks),
        "total": len(all_checks),
        "by_type": dict(Counter(x["type"] for x in all_checks)),
        "failed": [x for x in all_checks if not x["pass"]],
    }
    summary["node_gt_csv_rows"] = len(rows)
    (output / "business_review.md").write_text("\n".join(report) + "\n")
    (output / "business_review.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    with (output / "node_gt.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(summary["independent_check_totals"], ensure_ascii=False))
    print("CSV rows", len(rows))
    print("REPORT", output / "business_review.md")

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Root containing the frozen hotel/ and procurement/ cases",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Git-ignored local directory for private reports and node GT",
    )
    parser.add_argument(
        "--source-commit",
        default=SOURCE_COMMIT,
        help="Builder commit provenance, not inferred from current HEAD",
    )
    args = parser.parse_args()
    summary = run_review(args.source.resolve(), args.out.resolve(), args.source_commit)
    return 0 if not summary["independent_check_totals"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
