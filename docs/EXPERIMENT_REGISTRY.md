# 实验索引

Run ID 对应一次模型运行或事后复核。公开清单提供已记录的版本、私有档案相对位置及 SHA‑256。早于 Git 初始化的实验注明源码审查版本，实际运行版本未知时保留为空。

<!-- archive-start -->

| 实验 | Run ID | 证据 |
|---|---|---|
| 酒店业务升级范围对照 | `gpt62_upgrade_20261123T161601107239Z` | [公开清单](../experiments/gpt62_upgrade_20261123T161601107239Z/manifest.json) |
| 酒店政策路由对照 | `gpt62_scope_20261123T124429168003Z` | [公开清单](../experiments/hotel_policy_routing/manifest.json) |
| 酒店公式工作流 | `gpt62_workflow_20261123T134800813601Z` | [公开清单](../experiments/hotel_workflow_001/manifest.json) |
| 有状态酒店审批 | `gpt62_stateful_20261123T150141302533Z` | [公开清单](../experiments/hotel_workflow_002/manifest.json) |
| SWE-Milestone Navidrome 九轮对照 | `codex_gpt-6.2-sol_navidrome_001` | [公开清单](../experiments/swe_milestone_navidrome/manifest.json) |
| Navidrome 九轮失败来源复核 | `swe_nav_lite38_regression_audit_20261125` | [报告](../experiments/swe_milestone_regression_audit/analysis.md) · [公开清单](../experiments/swe_milestone_regression_audit/manifest.json) |
| Workspace-Bench Lite 38 员工调动 M1 | `gpt62_lite38_transfer_20261125T051108Z` | [公开清单](../experiments/lite38_effective_transfer_m1/manifest.json) |
| Workspace-Bench Lite 38 通用调动功能 | `gpt62_lite38_generic_transfer_20261125T073431Z` | [公开清单](../experiments/lite38_generic_transfer_m1/manifest.json) |
| Workspace-Bench Lite 38 通用调动功能干净复跑 | `gpt62_lite38_generic_transfer_clean_20261125T082839Z` | [公开清单](../experiments/lite38_generic_transfer_clean_m1/manifest.json) |
| Workspace-Bench Lite 38 临时借调 M2 双轨演化 | `gpt62_lite38_m2_20261125T092133Z` | [公开清单](../experiments/lite38_temporary_assignment_m2/manifest.json) |
| Workspace-Bench Lite 38 重新入职 M3 | `gpt62_lite38_evolution_m3_20261125T115236Z` | [公开清单](../experiments/lite38_evolution_m3/manifest.json) |
| Workspace-Bench Lite 38 部门组织变更 M4 | `gpt62_lite38_evolution_m4_after_20261125T115236Z` | [公开清单](../experiments/lite38_evolution_m4/manifest.json) |
| Workspace-Bench Lite 38 工资分摊 M5 | `gpt62_lite38_evolution_m5_after_20261125T115236Z` | [公开清单](../experiments/lite38_evolution_m5/manifest.json) |
| Workspace-Bench Lite 38 编制和预算审批 M6 | `gpt62_lite38_evolution_m6_after_20261125T115236Z` | [公开清单](../experiments/lite38_evolution_m6/manifest.json) |
| Workspace-Bench Lite 38 历史更正与签发保护 M7 | `gpt62_lite38_evolution_m7_after_20261125T115236Z` | [公开清单](../experiments/lite38_evolution_m7/manifest.json) |
| Lite 38 Qwen3.5-27B 模型对照 | `lite38_model_compare_qwen_20261125T143703Z` | [报告](../experiments/lite38_model_comparison_qwen/analysis.md) · [公开清单](../experiments/lite38_model_comparison_qwen/manifest.json) |
| Lite 38 DeepSeek V4-Pro 模型对照 | `lite38_model_compare_deepseek_20261125T143703Z` | [公开清单](../experiments/lite38_model_comparison_deepseek/manifest.json) |
| Lite 38 Qwen4-Max 模型对照 | `lite38_model_compare_qwen4_20261125T144745Z` | [报告](../experiments/lite38_model_comparison_qwen4/analysis.md) · [公开清单](../experiments/lite38_model_comparison_qwen4/manifest.json) |
| Lite 38 M1 模型对照与旧离职报表复核 | `lite38_qwen4_m1_regression_probe_20261125` | [报告](../experiments/lite38_model_comparison_m1_diagnostic/analysis.md) · [公开清单](../experiments/lite38_model_comparison_m1_diagnostic/manifest.json) |
| Lite 38 Qwen4-Max M2 日期重算复核 | `lite38_qwen4_m2_date_probe_20261125` | [报告](../experiments/lite38_model_comparison_qwen4_m2_diagnostic/analysis.md) · [公开清单](../experiments/lite38_model_comparison_qwen4_m2_diagnostic/manifest.json) |
| Lite 38 DeepSeek V4-Pro M2 日期重算复核 | `lite38_deepseek_m2_date_probe_20261125` | [报告](../experiments/lite38_model_comparison_deepseek_m2_diagnostic/analysis.md) · [公开清单](../experiments/lite38_model_comparison_deepseek_m2_diagnostic/manifest.json) |
| Lite 38 M3–M5 月报日期重算复核 | `lite38_model_m3_m5_date_probes_20261125` | [公开清单](../experiments/lite38_model_comparison_m3_m5_diagnostics/manifest.json) |

<!-- archive-end -->

## 阅读顺序

1. 从各实验的清单定位任务、运行版本和私有证据。
2. 用 `execution_summary.json` 确认实际起点和提交范围。
3. 对照 `test_summary.json`、`regression_report.json` 和 `dependency_audit.json`，查分数口径、未验证项目和流程诊断。

## 重建与核验

在项目根目录执行 `python -m research_archive backfill` 可从该工作站保存的原始实验重建索引。用 `python -m research_archive verify <实验目录名>` 核对私有文件、清单和 SHA‑256。完整追溯需要这台工作站的本地档案。

## 数据源可行性核查

- 2026-11-25 · `workspace_bench_range_20261125` · [Workspace-Bench Office 样本、公式重算与选材](WORKSPACE_BENCH_FEASIBILITY.md)

## 设计规格

- 2026-11-25 · 非模型实验 · [初始成熟 Office HR 系统设计草案](INITIAL_MATURE_OFFICE_SYSTEM.md)。借鉴 Lite 38 工作簿模块并提出含政策、审批、归档和报告发布的基线系统。待讨论，不是已经生成或验收的任务资产。
- 2026-11-25 · [Lite 38 生效日期调动 M1](../experiments/lite38_effective_transfer_m1/analysis.md)。模型在原始工作簿副本上完成单次调动，最终独立核验 13/13；初始本地评分口径错误已留痕并修正。该开发性案例已公开题目与标准，不再作为盲测样本。
- 2026-11-25 · [Lite 38 通用调动功能](../experiments/lite38_generic_transfer_m1/analysis.md)。GPT‑6.2‑sol 交付修改后工作簿，校正评分器后的诊断结果为 44/45，旧功能 21/21。评分器修正发生于模型运行后。
- 2026-11-25 · [Lite 38 通用调动功能干净复跑](../experiments/lite38_generic_transfer_clean_m1/analysis.md)。冻结评分 41/45，按提交中的年度报表复核 45/45。旧功能 21/21，离职员工调动遗漏未复现。
- 2026-11-25 · [Lite 38 临时借调 M2 双轨演化](../experiments/lite38_temporary_assignment_m2/analysis.md)。两组继承各自实际 M1 提交，均为 133/133。一组修复原有离职调动缺陷，两组均未观测到旧功能回归。
