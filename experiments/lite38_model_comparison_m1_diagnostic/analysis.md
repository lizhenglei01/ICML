# Lite 38 模型对照 M1 与已确认回归

四组使用同一原始员工工作簿、M1 题面、隐藏测试、GT 和冻结评分器。M1 输出路径按隔离工作目录调整。API 模型使用 Codex 的兼容工具配置，服务端推理设置和模型元数据与 GPT‑6.2‑sol 路线存在差异。当前登录下 GPT‑5.3‑Codex 不可用。[对照入口](../../multifile_reconstruction/workspace_bench_lite38/model_comparison.py)

| 模型 | 冻结总分 | 本轮调动相关检查 | 旧功能保护 |
|---|---:|---:|---:|
| GPT‑6.2‑sol 干净复跑 | 41/45 | 20/24 | 21/21 |
| DeepSeek V4‑Pro | 35/45 | 14/24 | 21/21 |
| Qwen4‑Max | 33/45 | 14/24 | 19/21 |
| Qwen3.5‑27B | 21/45 | 0/24 | 21/21 |

- GPT‑6.2‑sol 的四项扣分来自年度报表位置，按实际位置读取后为 45/45。[GPT 基线复核](../lite38_generic_transfer_clean_m1/analysis.md)
- DeepSeek 和 Qwen4‑Max 的月末公式以当前在职名单为计数范围，未按报告日期还原在岗人群。在同一个离职档案场景中，两者都把工程部的历史月末人数算成 14，冻结 GT 为 3。该差异可在实际月报读到。[DeepSeek 轨迹](../lite38_model_comparison_deepseek/manifest.json) · [Qwen4-Max 轨迹](../lite38_model_comparison_qwen4/manifest.json)
- Qwen4‑Max 把两处月度、年度离职统计的部门来源改成新辅助表。在有离职档案调动的隐藏场景中，财务部离职数从原工作簿的 0 变为 1。仅在诊断副本恢复两处报表的 14 个原离职统计公式，使用相同隐藏输入重算后，两条旧检查均恢复，其余成绩一致。原提交仍为 33/45。两条失败由同一类公式改动造成。[回归复核清单](manifest.json)
- Qwen3.5‑27B 的台账前四列与题面契约不同，M1 有真实新增功能错误。M2 在接口报免费额度用尽后中断，该轮没有评分。[Qwen3.5-27B 报告](../lite38_model_comparison_qwen/analysis.md)

这些结果显示 M1 已能区分本次模型与工具配置组合。每组仅一次正式运行，无法估计各模型的稳定失败率。M1 的回归发生在原始工作簿到首次模型交付之间。M2 起各轮继承本模型自己的提交，DeepSeek 与 Qwen4‑Max 的本次对照按用户调整截止到 M5。
