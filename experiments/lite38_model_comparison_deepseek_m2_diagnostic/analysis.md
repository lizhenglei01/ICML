# Lite 38 DeepSeek V4-Pro M2 日期口径诊断

DeepSeek 继承了自己交付的 M1 文件。按冻结 M2 评分器重算，得到 **78/133**，借调重点检查 **4/12**。原工作簿月报日期公式在 LibreOffice 中同样出现 `#VALUE!`。将同一报告日期以日期类型写入重算副本后，得到 **103/133**，借调重点检查 **5/12**。同样的方法在 Qwen4-Max 的提交上得到 **118/133** 和 **12/12**。[DeepSeek 运行清单](../lite38_model_comparison_deepseek/manifest.json) · [Qwen4-Max M2 复核](../lite38_model_comparison_qwen4_m2_diagnostic/analysis.md)

DeepSeek 的剩余错误包括跨年相邻借调、离职前借调和借调期间永久调动等场景下的报表人数差异。标准日期介入后，仍有七项借调重点检查失败。两条轨迹的原始总分和同口径诊断分排序不同，研究比较须保留两组数字。原提交与 GT 均未修改。[复核清单](manifest.json)
