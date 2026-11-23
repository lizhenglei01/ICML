# 四个上游项目的代码边界与 CFBench 的关系

本报告依据 2026-11-18 本地实际 checkout，不重新做文献检索，也不将 README 宣传等同于完整实现。四个仓库初始 git status 均为空。

| 项目 | 核查 commit | 角色 |
|---|---|---|
| OfficeEditBench | `3b4f567970e25d5340571fb1e41b3a6a28e93ecb` | 第一阶段资产、局部文件编辑与底层验证基础 |
| WTM-Bench | `83d45ae601f3241ac7246613c67b765e48f93c06` | XLSX 对象差异评分组件；后续显式依赖解析参考 |
| EditCoherenceBench / EditPropBench | `4cfd20c8cd94324cca48bead9362806c764269f7` | 已知事实驱动的文本传播、标签与诊断设计参考 |
| SWE-Milestone | `17a8f1593e172e26b36cea15e2b30fb9536c93f5` | 后续连续变更的任务调度/检查点参考 |

## 1. OfficeEditBench：给我们现成材料与编辑/检查工具

源码位置：

- [`hard/materialize_task.py`](../OfficeEditBench/hard/materialize_task.py)：读 task.json，复制对应 fixtures，再调用 `seed_input_files` 调整已知初始状态。没有推断未知业务关系或生成任意新任务。
- [`hard/patch_office.py`](../OfficeEditBench/hard/patch_office.py)：按已指定 cell、shape、text 等操作局部改 OOXML。**可直接调用**，前提是我们自己提供正确位置和新值。
- [`hard/verifier_core.py`](../OfficeEditBench/hard/verifier_core.py) 与 [`hard/SCHEMA.md`](../OfficeEditBench/hard/SCHEMA.md)：已登记的 Excel/PPT/Word 原子检查和保护合同，具备很多可复用的对象级处理能力。
- [`medium/cohorts/cross_media/verify_task.py`](../OfficeEditBench/medium/cohorts/cross_media/verify_task.py)：`workbook_diff` 找源/参考/提交的变化，`check_workbook` 按原参考定义合法范围；`check_doc` 按任务编号取静态 metrics 并查特定段落；`office_recalc` 调 LibreOffice，另有公式灵敏度测试。

这意味着它已经覆盖了“跨文件编辑是否完成、无关内容是否保留”的一部分。我们不能把这些作为完全空白。缺少的是将一个**已确认依赖的资产状态**变成多个新干预，并确定性地产生各自新真值和评分合同的可复现机制。

原 verifier 内部绑定 task 编号、gold 和参考文件。应复用其底层工具，新增自己的干预 gold/oracle，而不是直接将原任务 verifier 运行结果作为新任务成绩。

### OBM-031～036 选择

| 任务 | 现成资产/关系 | 对当前实验的适合度 |
|---|---|---|
| 031 | C150_4 完成率引用、定义修正，XLSX+DOCX | 没有原生阈值/方向判断。适合数值引用，若要状态翻转还需新增规则。 |
| 032 | 州电力数据、展示数值和会议图表，XLSX+PPTX | 很适合下一步验证原生图表传播，但当前任务本身主要是数字修正。 |
| 033 | 合同编号到批准续期日期，再到备忘录清单 | 适合键匹配和多项独立变更，数值/语义派生较少。 |
| 034 | 批准成员→区域均值→PPT | 适合集合变更、多源聚合和图表扩展；需处理成员选择与重算。 |
| 035 | 取消/有效航班→分母→准点率→PPT | 有明确过滤和计算规则，适合下一轮多跳数值任务。 |
| **036** | USD→百万美元→同比→Word 数字与定性解释 | **本轮选择**。比较基期明确为模拟值，可做状态翻转且保护公开事实。 |

来源性质必须表述准确：这些是公开冻结记录加上游作者的任务、说明、批准与布局，不能称作未经加工的真实企业材料。

本轮实际用的是 `private/reference/OBM-036/` 原版资产作为一致 baseline，同时核对原 `tasks/OBM-036/input/` 与原 gold。对新比较基期重新计算真值，不使用原同比作为新答案。

## 2. WTM-Bench：有显式依赖与 Excel 差异评分，没有跨文件反事实系统

源码位置：

- [`data_generation/METHODOLOGY.md`](../WTM-Bench/data_generation/METHODOLOGY.md)：从已有完整工作簿提取对象，移除部分对象，形成 raw/final/intermediate 状态与恢复轨迹。终态本来已知。
- [`dependency/all_dependencies.py`](../WTM-Bench/data_generation/data_gen/scripts/dependency/all_dependencies.py)、[`cell_dependency.py`](../WTM-Bench/data_generation/data_gen/scripts/dependency/cell_dependency.py)：在内部工作簿 JSON 中记录公式引用、表、透视、图表、条件格式的 dependencies/used_in。不是读取 Word 隐含判断的模块。
- [`paths/dependency_struct.py`](../WTM-Bench/data_generation/data_gen/scripts/paths/dependency_struct.py)：把上述对象依赖组织成对象/计算组图，为逆向删减与轨迹生成服务。
- [`grader/scorer_v2.py`](../WTM-Bench/grader/scorer_v2.py)：接受 generated/ground_truth/initial；以初态到终态的差异评分，覆盖数值、公式以及图表/透视/条件格式/表/新工作表特征。用 formulas 库计算数值，失败时可回退缓存。

可复用的是单工作簿对象与公式分析、差异评分。我们必须提供**自己计算的新 ground_truth**；WTM 不会替我们推导 Word 的新结论。

边界尤其明确：`score_values_diff` 遍历 gt_diff 目标项；公式分数仅在 gt_diff 发生公式变化时纳入。本轮公式本应保留，因此即便提交把它换成数值相同的常量公式，也可能得满分。该行为已通过本轮真实调用复现；所以 WTM 成绩只作补充诊断。

`data_generation` 通过 `utils.py -> libraries.py` 引入 Excel/COM 相关依赖，且有缓存目录写入等导入副作用。完整管线要求 Windows/MS Excel，不应为了当前 Linux 最小实验强行移植。当前只实际调用独立 scorer，没有声称依赖提取管线已经跑通。

## 3. EditCoherenceBench：与反事实构造思路最接近，且生成器确实存在

源码位置：

- [`generator/fact_sampler.py`](../EditCoherenceBench/editpropbench/generator/fact_sampler.py)：生成受控事实；已实现两个重要定性级联：文档数 `<100 / <500 / 其他` 对应规模类别，模型数 `<=4 / <=7 / 其他` 对应覆盖类别。
- [`generator/surface_realizer.py`](../EditCoherenceBench/editpropbench/generator/surface_realizer.py)：将事实实现为文稿与 `fact_to_units` 映射。
- [`generator/edit_sampler.py`](../EditCoherenceBench/editpropbench/generator/edit_sampler.py)：选干预；级联时修改基础与派生事实，合并相关 unit 到 required 集；其他事实相关 unit 扣除 required/deletion 后成为 protected；还支持结论失效时删除句子。
- [`scoring/required_scorer.py`](../EditCoherenceBench/editpropbench/scoring/required_scorer.py)、[`protected_scorer.py`](../EditCoherenceBench/editpropbench/scoring/protected_scorer.py)、[`contradiction_scorer.py`](../EditCoherenceBench/editpropbench/scoring/contradiction_scorer.py)：数值/别名/旧值残留及标签规则，部分阶段辅以 LLM judge。并非对任意自然语言都能确定性保证完整语义正确。
- [`protocols/oracle_graph.py`](../EditCoherenceBench/editpropbench/protocols/oracle_graph.py)：给被测模型正确修改位置作为消融，以区分找不到位置与知道位置仍改错。这一点也值得我们的 Agent 评测沿用。

实际执行：

```bash
PYTHONPATH=EditCoherenceBench python EditCoherenceBench/scripts/generate_benchmark.py \
  --phase 1c --strict --out-dir ICM2027/upstream_checks/editprop
```

结果：10 篇 manuscripts、50 项 edits、50 项 benchmark items，strict 运行退出 0，无 API。产物保留在 `upstream_checks/editprop/phase_1c_prototype/`。未下载 HF 数据、未调用模型、未复现论文性能。

对我们的含义：已知事实、受控派生规则、隐含定性描述、Required/Protected 及 Oracle-Graph 消融这些思想已有直接代码先例，不能作为我们的独有贡献。我们需要验证的增量是**原生多文件对象上的反事实构造、动态正确终态、计算与语义联合传播、可编辑性和无关状态保护**，并进一步做规模和能力诊断实验。

目前没有直接移植该仓库源码。README 标 MIT，pyproject 标 Apache-2.0，checkout 中没有 LICENSE 文件；这里记录元数据不一致，不据此宣称确定的再分发许可。

## 4. SWE-Milestone：任务之间的依赖，不是事实之间的计算关系

- [`harness/e2e/dag.py`](../SWE-Milestone/harness/e2e/dag.py)：从 CSV 加载里程碑 DAG，处理强/弱依赖、可运行集合、完成/失败/跳过/已提交状态。
- [`harness/e2e/orchestrator.py`](../SWE-Milestone/harness/e2e/orchestrator.py)：运行和调度任务。
- [`harness/e2e/evaluator.py`](../SWE-Milestone/harness/e2e/evaluator.py)、[`harness/utils/snapshot.py`](../SWE-Milestone/harness/utils/snapshot.py)：检查点、独立测试评估和结果管理。执行测试的真值依赖既有测试及版本数据；不是根据任意新事实生成新测试真值。

可参考将来的 A→B→C 连续办公修订：错误是否累积、后续变更是否依赖前一步输出、如何隔离阶段评分。但不能拿 milestone DAG 替代我们的“单元格→公式→文字结论”事实图。

本轮只核查源码。完整数据和容器依赖不是已经本地运行过的成果；未启动它的 Agent 环境。

## 5. 我们该如何继续

```text
OfficeEditBench 原始文件 + 现有文件工具
                ↓
逐任务确认的私有事实/规则/对象位置映射
                ↓
我们新增：干预采样 + 确定性状态传播
                ↓
新参考文件 + 必改/保留标签 + 新独立 Oracle
                ↓
Office 底层检查 + WTM 补充分数 + 跨文件一致性/语义检查
                ↓
外部 Agent 实验与 Oracle-Graph 消融；后续才考虑连续任务
```

第一阶段 `cfbench/` 已采用这一结构，且明确只是一个场景适配器。下一步先完善对普通 Office 另存的宽容保护，再扩到 OBM-035/034/032，逐步增加筛选、聚合和原生 PPT 图表。用不同基础资产划分训练/测试，不能把同一模板生成的两个数字实例当作跨域泛化证据。

发表层面的定位还需要更多实验支持：**单靠将三个项目功能串接，或多造几个 if 分支，不能证明 ICML 级贡献。** 当前产物的价值是把“能否可靠复用资产构造新任务”从方案变成了可检验的工程事实；依赖复杂度、任务覆盖、Oracle 的独立可靠性和 Agent 失败规律仍待验证。
