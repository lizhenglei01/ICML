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

2026-11-25 按研究方向调整，暂停从零构建 HR 初始系统，源码与本地产物保留。转而在 Workspace-Bench Lite 38 原始员工工作簿副本上做单次生效日期调动试验。GPT-6.2-sol 完成调动记录、当前档案及按日期统计；修正月末人数评分口径后，模型提交和标准工作簿均通过 13/13 独立检查。该任务仍是单员工的部门人数变化探针，没有暴露模型能力缺陷，不能代表 SWE-Milestone 级别持续演化。

2026-11-25 在 Lite 38 原始工作簿上进行通用调动功能测试。GPT-6.2-sol 提交了带调动账本和公式的工作簿。首轮修正评分器的年度和期初日期设置后，诊断重算为 44/45，旧功能保护 21/21。首轮提交未接受一条离职员工调动记录。详见 [通用调动功能实验](../experiments/lite38_generic_transfer_m1/analysis.md)。

2026-11-25 冻结修正版评分器并以原题、原工作簿开启新会话复跑。原判 41/45，旧功能保护 21/21。四项扣分来自评分器读入指定的隐藏辅助表。对模型实际交付的年度报表按同一份 GT 复核后为 45/45。此次离职员工调动记录有效，月初人数统计正确。详见 [干净复跑](../experiments/lite38_generic_transfer_clean_m1/analysis.md)。

2026-11-25 两份实际 M1 提交分别继承到临时借调 M2。两组 GPT‑6.2‑sol 均通过冻结后的 133 项检查。第一组修复了一项原有离职员工调动功能，第二组保留了原有功能，两组均未观测到旧功能回归。详见 [M2 双轨演化](../experiments/lite38_temporary_assignment_m2/analysis.md)。

2026-11-25 两条轨迹继续从实际 M2 文件执行重新入职 M3。冻结原分分别为 227/227 和 223/227，累计旧检查均为 133/133。轨迹二四项差异来自隐藏模板内的活动统计，题面所列实际报表全部通过。两份 M3 实际提交均归档。详见 [M3 结果](../experiments/lite38_evolution_m3/analysis.md)。

2026-11-25 两条轨迹分别从实际 M3 文件执行部门组织变更 M4。两份提交的冻结原分均为 307/307，222 项累计旧检查无回归。M5 继续继承两份实际提交。详见 [M4 结果](../experiments/lite38_evolution_m4/analysis.md)。

2026-11-25 两条轨迹分别从实际 M4 文件执行工资分摊 M5，冻结原分均为 367/367，307 项累计旧检查无回归。M6 以两份 M5 实际提交作为起点。详见 [M5 结果](../experiments/lite38_evolution_m5/analysis.md)。

2026-11-25 两条轨迹分别从实际 M5 文件执行编制与工资预算审批 M6，冻结原分均为 451/451，367 项累计旧检查无回归。M7 以这两份实际提交作为起点。详见 [M6 结果](../experiments/lite38_evolution_m6/analysis.md)。

2026-11-25 Lite 38 双轨演化实验完成至 M7。轨迹一为 531/531，轨迹二冻结原分 512/531，台账日期归一化诊断为 515/531。轨迹二改变了入职日人数边界，九项旧检查由通过变为失败，诊断副本重算已定位根因。见 [M7 及五轮累计结果](../experiments/lite38_evolution_m7/analysis.md)。

2026-11-25 重计本地 Navidrome 的九轮原始结果。最后一轮 56 项计分旧测试在前一轮已失败。另一轮的四项计分旧测试从通过变为失败，日志显示在模拟组件异常处中断。Lite 38 的两条轨迹在 M2–M7 确认一轮真实旧功能回归。论文实验日志需授权，整体频率还无法比较。[逐轮复核](../experiments/swe_milestone_regression_audit/analysis.md)

Lite 38 模型对照复用了 M1–M7 的冻结题面、GT 和评分器。DeepSeek V4-Pro 与 Qwen3.5-27B 通过了独立工作簿工具预检。当前 ChatGPT 登录下，Codex CLI 返回 GPT-5.3-Codex 不可用。工具预检未提交正式里程碑分数。[对照入口](../multifile_reconstruction/workspace_bench_lite38/model_comparison.py)

Qwen3.5-27B 已完成 Lite 38 M1，冻结评分 21/45。模型提交的调动台账列布局与题面约定不符，旧功能检查 21/21。M2 免费额度用尽，未评分。北京工作空间接口已验证 `qwen4-max` 登录可用，另起轨迹。[Qwen3.5-27B 结果](../experiments/lite38_model_comparison_qwen/analysis.md)

Lite 38 M1 对照中，DeepSeek V4-Pro 为 35/45、Qwen4-Max 为 33/45。两者有可复核的历史人员统计错误。Qwen4-Max 另有两条旧离职报表检查从通过变为失败，诊断副本恢复原公式后通过。两条连续轨迹按用户要求在 M5 截止。[M1 对照与复核](../experiments/lite38_model_comparison_m1_diagnostic/analysis.md)

两组分别继承实际 M1 交付完成 M2。统一月报日期的诊断中，DeepSeek V4-Pro 借调重点检查为 5/12，Qwen4-Max 为 12/12。四条原始 Qwen4-Max 候选回归在日期复核中消失。两组原始评分保留。[M2 同口径结果](../experiments/lite38_model_comparison_deepseek_m2_diagnostic/analysis.md)

两组已完成 M3。归一化报告日期后，DeepSeek V4-Pro 为 158/227、重点 16/27，Qwen4-Max 为 178/227、重点 17/27。两组均观察到前轮错误延续，本轮旧测试没有新出现的通过→失败。Qwen4-Max 在 M4 遇到百炼访问拒绝，保留 M3 提交作为续跑起点。[DeepSeek 结果](../experiments/lite38_model_comparison_deepseek/analysis.md) · [Qwen4 结果](../experiments/lite38_model_comparison_qwen4/analysis.md)

DeepSeek V4-Pro 已完成 M4，冻结分 135/307，日期复核为 198/307，部门合并重点检查 12/36。69 条旧检查在相同日期复核中延续了旧失败，未观测到新的通过→失败。M5 使用实际 M4 工作簿。[DeepSeek 连续对照](../experiments/lite38_model_comparison_deepseek/analysis.md)

模型对照评测已归档到各自可执行的最后一轮。DeepSeek 比较到 M4，M5 因余额不足中断。中断时保存的工作簿已完成离线复核，冻结方法为 112/367，日期复核 188/367。Qwen4-Max 比较到 M3，M4 因百炼访问拒绝中断。Qwen3.5-27B 比较到 M1，M2 因免费额度用尽中断。三条轨迹都已验证续跑起点，M6 和 M7 没有启动。[DeepSeek](../experiments/lite38_model_comparison_deepseek/analysis.md) · [Qwen4-Max](../experiments/lite38_model_comparison_qwen4/analysis.md) · [Qwen3.5-27B](../experiments/lite38_model_comparison_qwen/analysis.md)

2026-11-26 从 EPA 规范追到两套 WeeWX 插件的实际版本和 HTML 报表，复现 PM2.5 AQI 高浓度换算变更。只更新一套插件时，另一套报表仍用旧值。以旧插件组成的分散版与人为集中规则的对照，两版 GPT‑6.2‑sol 各跑两次，均为 128/128。本例验证了检测漏改的能力，四次提交均未漏改。[真实版本重放与对照](../experiments/aqi_version_history/analysis.md)

2026-11-26 补齐 Lite 38 两个旧功能回归的公式因果链。四份 M6/M7 工作簿和两种针对性复核确认，轨迹二同日边界造成 9 条旧检查和 2 条本轮次要检查失分，签发效果丢失造成 5 条失分，其中 2 条属于 M7 重点检查。[回归复核](../experiments/lite38_regression_audit/analysis.md)

2026-11-26 核查康州地区迁移的真实发布资产。2025 Excel 与官网快照都保留 Fairfield 的当期失业率，并使用新区发布 Greater Bridgeport 人口。ArcGIS 的 2025 县级图层只收录新规划区。公开简版应用的 SQL 可查到 2025 Fairfield，但选区目录缺少该旧县。官网实际页面与数据已离线重放，模型未启动。[迁移核查](../experiments/connecticut_geography_migration/analysis.md)

参考 [研究设计](RESEARCH_DESIGN.md)、[实验索引](EXPERIMENT_REGISTRY.md) 和 [已知问题](KNOWN_ISSUES.md)。
