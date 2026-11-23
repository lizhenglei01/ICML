# 酒店经营复核对照实验

从 `ICM2027/` 执行下面两条命令，用已保存的模型提案生成四组原生 Office 材料并核验，再启动四个隔离的编辑会话。

```bash
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_evidence --output /tmp/hotel_evidence_trial
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_agent --source /tmp/hotel_evidence_trial
```

重新核对正向探索使用 `python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.business_forward --reuse`。代码保留主题一次生成和目标反向搜索的既有流程，与两轮正向发现共用独立定义和单位验证。构建器读取这三个已验证实验的归档输出。`hotel_evidence` 本身是确定性的。图表渲染需要 Graphviz `dot`，原生材料使用已接入项目的 Worldloom、openpyxl、python-docx 和 python-pptx。

## 场景

六家虚构酒店使用同季度、同一币种的记录。内部复核规定入住率至少为 75%，平均房价至少为 140 元／间夜，两项同时满足才入选。门槛是人工设定的合成任务政策，计算定义来自已登记的 STR 指标口径。

Willowbank 的已售间夜数由 6,000 更正为 8,000。可售间夜和客房收入保持固定。其入住率由 0.60 升至 0.80，平均房价由 200 降至 150，RevPAR 仍为 120。原先只有 Harborstone Grand Hotel 入选。更正后新增 Willowbank，达标总数为 2，两家平均房价等权平均为 155 元／间夜。

每个初始工作包包括最新的依据和上一版待修订的复核结果。会话从自然语言获知同一更正，读取文件中的最新依据，更新三份文件中已发布的旧结果。把已核准的新依据和旧报表同时放进工作包是控制设计的一部分。浅版用复核方已计算的入住率、平均房价作为最新依据。深版按同样口径发布基础记录。

| 布局 | 公开依据 | 名单最短深度 | 全部目标的最短并行深度 | 最少文件 |
|---|---|---:|---:|---:|
| 浅层集中 | 入住率、平均房价、政策均在 Excel | 2 | 3 | 1 |
| 浅层跨文件 | Excel 入住率，PPT 平均房价，Word 政策 | 2 | 3 | 3 |
| 深层集中 | 三项基础数据及政策均在 Excel | 3 | 4 | 1 |
| 深层跨文件 | Excel 间夜数，PPT 客房收入，Word 政策 | 3 | 4 | 3 |

推导深度以最长依赖链中的算子层数计，可以同时进行的判断处于同一层。程序对从原生文件读回的公开值逐一验证全部八个文件子集。每个材料不足的子集都有一个符合仍可见数值、更正说明及旧报表的反例世界，在缺失材料中改变一项事实即可改变全部三个答案。跨文件必要性由这些反例支撑。深度结论限于登记的算子、替代公式和短路规则，自然语言可能支持更多捷径。门槛的交叉相乘等价形式也已加入搜索。

完整图保留 RevPAR 的两条合法路径。客房收入除以可售间夜数仅需一层。经 ADR 与入住率的路径使用三个算子，深度为二。两者是同一数值的备选计算关系。业务政策中的 AND 则对候选集合取交集。四组任务选择的输出是入选名单、家数和入选酒店的等权平均房价。

## 资产与 Oracle

`business_artifacts/hotel_evidence/index.html` 链接四组初始和参考文件、完整图与验证报告。各组 `public/` 包含 Excel、Word、PPT 和同一任务说明。`private/` 包含对应的 `reference/`、`oracle.json`、`visibility.json`、`evidence_map.json` 和 `native_semantic_delta.json`。私有图还保存了每个节点的稳定 ID、业务定义、计算和来源，以及前后值。

每组的评分覆盖更正单元格、Excel 资格标记、Word 结论和 PPT 资格表及原生图表。Excel 使用原生 COUNTIF，由 Oracle 保护公式并按标记独立重算。PPT 核对可见表格、图表缓存及内嵌数据表。参考通过，未修订、漏改 Word、用常量替代公式和仅篡改图表缓存的提交应被拒绝。

人定义业务口径、复核政策、纠正事件及文件可用位置。模型提出指标和公式候选。程序核验公式、汇编图、选择并回读公开证据、验证捷径、执行反事实和生成 Oracle。一个情景只提供一次四格原生编辑观察，模型表现按这一小样本解释。

实际编辑结果保存在 `business_artifacts/hotel_evidence/agent_runs/20261120T084457191702Z/`。四份提交使用相同的业务答案。原评分要求 Word 名单按字母顺序排列，而任务只规定分隔符。这一要求造成四个假阴性。`results.json` 保留原评分，`results_v2.json` 记录允许名单无损换序后的复核。新评分仍检查全部成员是否准确、是否有重复及段落格式。复核验证原始输入、提交及评分文件的哈希，沿用同一批原生提交。现有实验的修正版 Oracle 放在 `oracle_v2.json`，新构建直接生成修正版。
