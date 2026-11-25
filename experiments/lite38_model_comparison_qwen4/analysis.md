# Lite 38 Qwen4-Max 连续对照

模型通过百炼北京工作空间的 Responses 接口运行，开启思考模式。M1 从相同原始工作簿开始，M2 与 M3 分别继承该模型上一次实际提交，文件哈希逐轮核对。题面、隐藏测试、GT 和原评分器均沿用冻结版本。[运行清单](manifest.json)

| 轮次 | 冻结评分 | 事后复核 |
|---|---:|---|
| M1 永久调动 | 33/45 | 旧离职统计两项真实回归，恢复原公式后两项均通过。 |
| M2 临时借调 | 66/133 | 同业务日期重算 118/133，借调重点 12/12，四条候选回归消失。 |
| M3 重新入职 | 99/227 | 同业务日期重算 178/227，重入职重点 17/27。旧检查 4 条修复、11 条继续失败、0 条由通过变为失败。 |
| M4 部门合并 | 无评分 | 百炼返回访问拒绝，未完成模型提交。 |
| M5 工资分摊 | 未启动 | 等待 M4 的实际交付。 |

M4 前段调用成功，随后返回 `403 AccessDenied.Unpurchased`。内置重试以及事后最小请求均得到访问拒绝。现有记录确认接口访问中断，账户原因仍需工作台核实。续跑入口已核对 M3 工作簿的 SHA‑256，恢复模型访问后可在 M4 重开会话。[百炼错误码](https://help.aliyun.com/zh/model-studio/error-code) · [M1 回归](../lite38_model_comparison_m1_diagnostic/analysis.md) · [M2 诊断](../lite38_model_comparison_qwen4_m2_diagnostic/analysis.md) · [M3 诊断](../lite38_model_comparison_m3_m5_diagnostics/manifest.json)
