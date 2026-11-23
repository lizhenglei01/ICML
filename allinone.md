# 2026 年文献核查：由依赖图生成间接办公证据

核查日期为 2026 年 11 月 22 日。关注证据如何加工、如何落到文档对象，以及如何确认目标可从公开证据推出。

## 检索记录

Paper Search 查询为 `multi-hop reasoning logic-first document generation chart evidence synthesis` 和 `procedural benchmark indirect evidence reasoning chain document table`，年份为 2026。各来源请求每个查询至多 3 项，相关性下限为 3。arXiv 返回 6 项，Crossref 返回 6 项，合并 2 条重复记录，共 10 项。OpenAlex、Semantic Scholar 和 OpenReview 返回 HTTP 429，DBLP 返回内容未能解析为 JSON。会议归属与下文机制通过会议官网、论文原文及作者仓库另行核对。

| 排名 | 初筛命中 | 年份和来源 | 检索得分 |
|---:|---|---|---:|
| 1 | [DocHop: Benchmarking Out-of-domain Multi-hop Reasoning in Information-Dense Documents](https://arxiv.org/abs/2609.02059) | 2026，arXiv。另核实 ICML 2026 正式发表 | 9 |
| 2 | [BRIDGE: Benchmark for multi-hop Reasoning In long multimodal Documents with Grounded Evidence](https://arxiv.org/abs/2603.07931) | 2026，arXiv | 8 |
| 3 | [GDP.pdf: Benchmarking Grounded Multimodal Reasoning over Professional PDF Documents](https://arxiv.org/abs/2607.11192) | 2026，arXiv | 7 |
| 4 | [Kinship Data Benchmark for Multi-hop Reasoning](https://arxiv.org/abs/2601.07794) | 2026，arXiv | 6 |
| 5 | [When Truth is Retrieved but Ignored: Evidence-Present Indirect Injection in Multi-document RAG](https://doi.org/10.2139/ssrn.7248360) | Crossref，年份缺失 | 6 |
| 6 | [LongCoT: Benchmarking Long-Horizon Chain-of-Thought Reasoning](https://arxiv.org/abs/2604.14140) | 2026，arXiv | 4 |
| 7 | [ProgramTab: Boosting Table Reasoning of LLMs via Programmatic Paradigm](https://arxiv.org/abs/2607.11207) | 2026，arXiv | 4 |
| 8 | [ClinAuthBench: A Synthetic Benchmark for Evidence-grounded Inpatient Psychiatric Authorization Reasoning](https://doi.org/10.2139/ssrn.7447863) | Crossref，年份缺失 | 4 |
| 9 | [Document in Criminal Procedural Proof](https://doi.org/10.32850/lb2414-4207.2026.43.33) | 2026，Law Bulletin，Crossref | 3 |
| 10 | [Review for TRELLIS](https://doi.org/10.1088/2631-8695/ae66a1/v2/review1) | 2026，Crossref，扫描标为 survey | 3 |

初扫最相关的命中为 DocHop、BRIDGE 和 GDP.pdf。随后按三条方法线核对了更多原始文献，包括逆向证据合成、资产内的可执行变换，以及单份证据必要性。用户本轮关心构建机制，因此 NLP、检索与推理综述只保留在初扫表中。初扫记录的引文计数均为 0，加上覆盖不完整，不能据此比较论文或作者影响。

初扫标题词频按英文独立词、大小写不敏感计数。下表中的截短标题按原始检索标题计数。

| 标题词 | 次数 |
|---|---:|
| Reasoning | 8 |
| Benchmark / Benchmarking | 6 |
| Evidence | 4 |
| Multi-hop | 4 |
| Document / Documents | 5 |

## 核心机制

### DTBench（KDD 2026）

从已有表格的单元格值出发，标注要考的能力。Refiner 将直接事实改写成可恢复该值的证据片段。Planner 分配片段，Writer 按章节撰写，Verifier 检查原值能否恢复、是否真需要指定能力、是否生成未登记的事实。适合复用按证据撰写的方式。论文中的语言检查由模型承担，本项目的计算恢复仍宜直接执行程序。 [会议](https://kdd2026.kdd.org/papers/) · [论文 §3.3](https://arxiv.org/html/2602.13812#S3.SS3) · [Refiner 源码](https://github.com/ZJU-DAILY/DTBench/blob/main/dataset_generation/refiner_agent.py)

### LongChart（2026 年 8 月 arXiv）

显式区分原始数据、运算结果及图表节点。求和、差值、比例、时间偏移等是确定性变换。有两种公开设置，一种直接公开中间结果图表，另一种给足以求解的较小图表集合。适合参考面向同一真值的证据公开策略。 [论文 §3、§4.2](https://arxiv.org/html/2608.01328v1#S3)

### DocHop（ICML 2026）

先生成筛选逻辑，按图表汇总需满足的约束，生成数字，并运行符号推理树验证。正文生成只得到规则，因而不会从生成输入复制最终名单。官方仓库主要提供评测入口。 [会议](https://proceedings.mlr.press/v306/yu26al.html) · [论文 §3.2](https://arxiv.org/html/2609.02059v1#S3.SS2) · [仓库](https://github.com/ZhuoranYu/dochop-vlmevalkit)

### TopoGraphRAG-Bench（NeurIPS 2026 名录，10 月 arXiv）

从原有 PDF 的独立正文、表格、图像块构作单步问题，隐藏跨证据桥接实体。构建时分别测试单份证据、纯文本加 OCR、综合题逐项删减支持证据。这是模型驱动的过滤方法。对 Office 可在已知的计算规则下另外做反例式必要性证明。 [会议名录](https://neurips.cc/Downloads/2026) · [论文 §3.2](https://arxiv.org/html/2610.09360v1#S3.SS2) · [构建代码](https://github.com/RichardLRC/TopoGraphRAG-Bench/tree/main/construction)

### MMEnterprise-Bench（2026 年 10 月 arXiv）

先从种子文档提取关键事实，规划协作事件，再生成会随时间累积的团队记录。可保留一次误报、后续更正和不同渠道的旧记录。验证事件顺序和消息引用，人工检查证据可推导性。可借鉴的是历史快照与权威口径的区分。 [论文 §3 与附录 C](https://arxiv.org/html/2610.27911v1#S3) · [仓库发布说明](https://github.com/mlvlab/MMEnterprise-Bench)

### SynthCorp（2026 年 11 月 arXiv）

将业务事件编译为带显式输入输出的操作，按虚拟时间生成数据库、政策备忘录、目录和快照。从日志生成 SQL 真值，再检查答案能否从公开资产重现。适合参考事件、证据、权威时间点到真值的追踪。论文评估数据查询。 [论文 §3.3–3.5](https://arxiv.org/html/2611.14765v1#S3.SS5)

## 辅助对照

- ChartCF（ACL 2026）有反事实图表生成源码。更改答案相关的绘图数据或语义标记后重绘，尽量保持其他视觉属性，适合参考成对渲染。 [论文](https://aclanthology.org/2026.acl-long.1280/) · [生成代码](https://github.com/jianzhubao/ChartCF/blob/main/scripts/run_data_syn.sh)
- SynthDocBench 将 D3 图表与带数据、单位、图表源信息的结构化元数据绑定，生成长篇 PDF／HTML，适合参考图表的来源记录。 [论文附录 L](https://arxiv.org/html/2607.10400v1#A12) · [生成代码](https://github.com/ServiceNow/SynthDocBench)
- HopWeaver（ACL 2026）将共享实体上的单步问答组合，过滤直接连通不相邻节点的文档。 [论文](https://aclanthology.org/2026.acl-long.1295/) · [源码](https://github.com/Zh1yuShen/HopWeaver)
- FondueQA（11 月 6 日 arXiv）检查每项事实是否存在替代文档、题面是否泄露、推理中是否有冗余步骤。 [论文](https://arxiv.org/html/2611.09274v1#S3)
- ChainCheck（11 月 9 日 arXiv）证明它所测试的一组构造能留下足以被模型利用的表面编辑痕迹。适合参考用同一排版流程和安慰剂编辑控制表面差异。 [论文](https://arxiv.org/html/2611.11965v1#S3)
- MM-ReportBench（11 月 17 日 arXiv）用树搜索组织图表和分析洞见后由人审定参考报告。 [论文](https://arxiv.org/html/2611.22514v1)
- MKA（11 月 18 日 arXiv）由专家构建需要读取多种视觉资料并交付原生文件的任务，适合参考真实工作资产设计。 [论文](https://arxiv.org/html/2611.25228v1)
- WorkWorlds（11 月 18 日更新）比较同一道题下任务定制材料和真实岗位全量可见材料，对理解证据可达性有帮助。 [论文](https://arxiv.org/abs/2609.23806v3)

## 酒店场景的首个可验证对照

现有深版将已售和可售间夜写入 Excel、客房收入写入 PPT、筛选口径写入 Word。它隐藏了入住率与平均房价等中间结果，并已经检查在已登记计算关系下需要三份文件。客房收入本身仍可直接读取。 [现有布局](/mnt/docker_disk/tmp/data/ICM2027/multifile_reconstruction/reasoning_graph_mvp/logic_first/hotel_deep_case.py:251)

保留事件、规则语义和新旧终态，选一个事实做配对。直接版在 PPT 公开净客房收入 1,080,000 元。间接版公开当期入账 1,125,000 元和同口径退款 45,000 元，可分别放在 PPT 与 Excel。Word 给出扣除退款的财务口径。依赖图执行 `1,125,000 − 45,000 = 1,080,000`，再执行 `1,080,000 ÷ 8,000 = 135`，最终入选结果与直接版一致。这里的入账和退款是拟构造的示例数值。

程序先用预定义的变换生成分项，保证单位、时间和范围一致，再写入世界状态。文本模型可以改写标注过的口径说明。生成后应从 Office 对象回读分项并独立计算，检测正文、题面、公式缓存和图表内嵌数据是否直接泄露净额。必要性验证逐个移去关键公开证据，检查是否存在同样可见的信息却对应不同答案的合法状态。两版的变更事件、评测语义目标和生成排版流程保持一致。

阅读顺序可从 DTBench 的反向证据合成开始，再看 LongChart 的计算图和可见性设置，随后看 TopoGraph 的必要性过滤和 SynthCorp 的日志验证。

