# 项目进度

项目研究 Agent 能否持续扩展和维护带依赖的业务工作空间。当前主要原型是酒店审批。Excel 保存业务状态，Agent 交付执行申请、签发、退款和递补的程序。

2026-11-23 的首个有状态实验中，GPT‑6.2‑sol 的连续组和独立组都通过了三轮测试。原型验证了跨调用状态保存、操作顺序和名额释放依赖。该结果来自一个人工构造的酒店案例。

2026-11-24 的升级范围对照把实时 Word/PPT 状态和不可变签发快照纳入同一审批流程。窄版和扩大版都通过了 21 条共同测试。扩大版原判 30/36，字段口径复核后为 36/36。两版数据及原因均保留在实验索引中。

<!-- archive-start -->

| 实验 | 最新归档结果 |
|---|---|
| [酒店业务升级范围对照](../experiments/gpt62_upgrade_20261123T161601107239Z/analysis.md) | 原始评分 3/4 次运行通过 |
| [酒店政策路由对照](../experiments/hotel_policy_routing/analysis.md) | 原始评分 3/4 次运行通过 |
| [酒店公式工作流](../experiments/hotel_workflow_001/analysis.md) | 原始评分 4/4 次运行通过 |
| [有状态酒店审批](../experiments/hotel_workflow_002/analysis.md) | 原始评分 5/5 次运行通过 |
| [SWE-Milestone Navidrome 九轮对照](../experiments/swe_milestone_navidrome/analysis.md) | 原始评分 3 轮通过，6 轮失败 |

<!-- archive-end -->

后续研究重点是扩展可重放的业务状态与旧功能回归覆盖，再检验能否从已验证的任务结构生成更多案例。

2026-11-25 依据 Workspace-Bench Lite 38 的 14 表 HR 工作簿完成了初始成熟 Office 系统规格草案。方案把既有查询、统计、提醒、成本分析与新设计的审批、状态更新、归档和报告快照连接起来，并列出 M1 前基线验收。当前仅为待讨论设计，尚未生成工作簿、Word、PPT 或脚本，也未运行模型。

参考 [研究设计](RESEARCH_DESIGN.md)、[实验索引](EXPERIMENT_REGISTRY.md) 和 [已知问题](KNOWN_ISSUES.md)。
