# 实验索引

Run ID 对应一次模型运行。公开清单提供已记录的版本、私有档案相对位置及 SHA‑256。早于 Git 初始化的实验注明源码审查版本，实际运行版本未知时保留为空。

<!-- archive-start -->

| 实验 | Run ID | 证据 |
|---|---|---|
| 酒店政策路由对照 | `gpt62_scope_20261123T124429168003Z` | [公开清单](../experiments/hotel_policy_routing/manifest.json) |
| 酒店公式工作流 | `gpt62_workflow_20261123T134800813601Z` | [公开清单](../experiments/hotel_workflow_001/manifest.json) |
| 有状态酒店审批 | `gpt62_stateful_20261123T150141302533Z` | [公开清单](../experiments/hotel_workflow_002/manifest.json) |
| SWE-Milestone Navidrome 九轮对照 | `codex_gpt-6.2-sol_navidrome_001` | [公开清单](../experiments/swe_milestone_navidrome/manifest.json) |

<!-- archive-end -->

## 阅读顺序

1. 从各实验的清单定位任务、运行版本和私有证据。
2. 用 `execution_summary.json` 确认实际起点和提交范围。
3. 对照 `test_summary.json`、`regression_report.json` 和 `dependency_audit.json`，查分数口径、未验证项目和流程诊断。

## 重建与核验

在项目根目录执行 `python -m research_archive backfill` 可从该工作站保存的原始实验重建索引。用 `python -m research_archive verify <实验目录名>` 核对私有文件、清单和 SHA‑256。完整追溯需要这台工作站的本地档案。
