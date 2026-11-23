# 项目进度

项目研究 Agent 能否持续扩展和维护带依赖的业务工作空间。当前主要原型是酒店审批。Excel 保存业务状态，Agent 交付执行申请、签发、退款和递补的程序。

2026-11-23 的首个有状态实验中，GPT‑6.2‑sol 的连续组和独立组都通过了三轮测试。原型验证了跨调用状态保存、操作顺序和名额释放依赖。该结果来自一个人工构造的酒店案例。

<!-- archive-start -->

| 实验 | 最新归档结果 |
|---|---|
| [酒店政策路由对照](../experiments/hotel_policy_routing/analysis.md) | 原始评分 3/4 次运行通过 |
| [酒店公式工作流](../experiments/hotel_workflow_001/analysis.md) | 原始评分 4/4 次运行通过 |
| [有状态酒店审批](../experiments/hotel_workflow_002/analysis.md) | 原始评分 5/5 次运行通过 |
| [SWE-Milestone Navidrome 九轮对照](../experiments/swe_milestone_navidrome/analysis.md) | 原始评分 3 轮通过，6 轮失败 |

<!-- archive-end -->

后续研究重点是扩展可重放的业务状态与旧功能回归覆盖，再检验能否从已验证的任务结构生成更多案例。

参考 [研究设计](RESEARCH_DESIGN.md)、[实验索引](EXPERIMENT_REGISTRY.md) 和 [已知问题](KNOWN_ISSUES.md)。
