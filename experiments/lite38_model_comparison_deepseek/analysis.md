# Lite 38 DeepSeek V4-Pro 连续对照

DeepSeek 使用官方 Responses 接口和相同的 Codex 文件编辑环境。M1 使用与 GPT‑6.2‑sol 基线相同的原始工作簿。每轮带入本模型实际交回的工作簿，所用题面、隐藏输入、GT 和评分器均未更改。本次按用户调整在 M5 截止。[运行清单](manifest.json)

| 轮次 | 冻结原分 | 日期复核 | 复核后的重点检查 |
|---|---:|---:|---:|
| M1 永久调动 | 35/45 | — | 14/24（冻结） |
| M2 临时借调 | 78/133 | 103/133 | 5/12 |
| M3 重新入职 | 114/227 | 158/227 | 16/27 |

M1 的旧功能检查 **21/21**，历史报表仍出现实际人数误差。M2 在标准日期下仍有七项借调重点检查未通过。M3 标准日期重算后，133 项旧检查中有 30 项原有失败继续出现，无新增的通过→失败。M3 新功能仍有十一项重点检查未通过。Qwen4-Max M3 复核总分为 178/227、重点检查 17/27，之后因 M4 接口访问中断。两条连续轨迹的前序模型实现各不相同。[M1 对照](../lite38_model_comparison_m1_diagnostic/analysis.md) · [M2 诊断](../lite38_model_comparison_deepseek_m2_diagnostic/analysis.md) · [M3 诊断](../lite38_model_comparison_m3_m5_diagnostics/manifest.json) · [Qwen4 轨迹](../lite38_model_comparison_qwen4/analysis.md)
