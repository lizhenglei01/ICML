# Lite 38 两项已确认的旧功能回归

## Qwen4-Max M1 旧离职报表

M1 要给原始工作簿增加可复用的部门调动，并保留原有入离职统计。Qwen4‑Max 引入 `TransferHelper`。在两处报表的 `MonthlyChangeQuery!C6:I6` 和 `AnnualHeadcountHelper!C6:I6`，它把离职统计的部门来源从 `SeparatedEmployeeArchive!O` 改成新辅助表的 `F` 列。后者用 `LOOKUP` 找截至离职日的最近调动部门。同一隐藏输入下，财务部离职人数从原工作簿的 0 变成 1。只在诊断副本中把这两行共 14 个公式恢复原样，两条旧检查均恢复，其他检查结果相同。[Qwen4 M1 复核](../lite38_model_comparison_m1_diagnostic/analysis.md)

## GPT-6.2-sol M6 → M7 日期边界

M7 新增历史更正，两个轨迹都让既有报表读取更正后的任职记录。已核对四份实际工作簿，以及每条轨迹 M6 提交到 M7 输入的哈希。

| 实际路径 | M6 → M7 的公式行为 | 下游输出 |
|---|---|---|
| 轨迹一 | `AssignmentCalculations!E53` 改为从 `CorrectedEmployment` 取入职日。`H53`、`J53` 的守门条件仍用 `入职日 >= 统计日` 排除该任职。`DepartmentHistory!B255`、`B257` 的计数继续使用严格小于。 | `MonthlyChangeQuery!C8:I8` 的月初人数和命名范围 `AnnualOpeningCounts` 均维持口径。 |
| 轨迹二 | `TransferHeadcountHelper` 接入更正。F、H 两列首个入职条件从 `$C2<F$1`、`$C2<H$1` 变为 `<=`。 | 月初人数仍从 F 列汇总，年初命名范围仍从 H 列汇总。同日入职的员工被多计。 |

只在轨迹二诊断副本把 F、H 两列中对应的 970 个复制公式恢复为严格小于。重新计算全部受影响的旧场景，**9 条旧检查全部恢复**。另有两条 M7 新场景的月初人数检查同样恢复。两条轨迹的原交付均保留原样。[诊断清单](manifest.json) · [原冻结结果](../lite38_evolution_m7/analysis.md)

## M7 剩余两项重点失分

这两项是工资分摊和后续审批。在签发场景中，轨迹二的 `RequestCalculation!L2` 保留已签发的 `APPROVED`，同时 `M2` 因更正后预算超限变为 `BUDGET`。`N2` 要求决策为已批准且理由为空，导致 `ApprovedEffects` 丢掉已签发借调的效果。`RequestCalculation!Z3` 无法计入这笔已有的预算占用，让随后的一笔业务审批由预期的拒绝变成批准。原签发值和 `REVIEW` 标记在该提交中都正确。

诊断仅改 `RequestCalculation!N2:N21` 的显示／生效条件，让已经签发的批准继续计入占用。按原测试重放后，工资分摊和审批这两项重点检查恢复。当前部门分布、年末人数和员工查询也一并恢复。结论采用冻结 Oracle 的业务口径，即保留既有批准的占用并另行标出需要复核。[公式与重放清单](manifest.json)

轨迹二冻结分中的 19 个失分可追到同日边界（9 条旧检查和 2 条本轮检查）、签发效果（5 条）和 Calc 二次导出日期序号（3 条）。日期序号的 3 条已在既有诊断中归一化确认。M1 的年度报表位置和 M2 的区域日期解析也有独立诊断结果。[M7 冻结与日期诊断](../lite38_evolution_m7/analysis.md) · [M1 报表位置](../lite38_generic_transfer_clean_m1/analysis.md) · [M2 日期解析](../lite38_model_comparison_qwen4_m2_diagnostic/analysis.md)
