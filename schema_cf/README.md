# 基于表格结构的反事实生成实验

本扩展接受表格输入，自动提出结构有效的反事实候选、组合查询规则、试算变化，并生成 Excel＋Word、私有真值、位置映射及 Oracle。当前输出是**数值已独立验证、业务含义待审定的任务草案**。没有修改上游仓库，没有调用模型或训练模型。

## 已运行的跨输入实验

```bash
# 从 /mnt/docker_disk/tmp/data 执行
bash ICM2027/run_schema_cf.sh
```

同一个生成器、不添加案例专属计算代码，运行三种输入：

| 输入 | 原始表／记录 | 自动查询视图 | 生成草案 |
|---|---:|---:|---:|
| Doc2DB medical/case1 | 6／39 | 26 | 3 |
| Doc2DB sports/case1 | 4／37 | 10 | 3 |
| 仅含赛事表头、数据的普通 XLSX | 4／37 | 3 | 3 |

最后一项是将上游赛事数据原值导出成普通工作表后作为输入，不含 schema、隐藏映射或答案。它验证输入格式适配；独立领域泛化证据是前两项不同结构的数据，不应把这个格式变体算成第三个独立领域。

运行日志及逐对象报告在 `artifacts/`。自动测试包括新增通用层测试和原医疗层回归测试，共 **40 项通过**。

## 输入自己的表格

```bash
cd /mnt/docker_disk/tmp/data/ICM2027
python -m schema_cf.run --input /absolute/path/to/tables.xlsx --out /absolute/path/to/output
```

也接受单个 CSV、JSON 数组、`{rows:[...]}` JSON、包含这些文件的目录，或 Doc2DB 格式的 `schema.json + tables/*.json` 目录。XLSX 每个工作表是一张表，第一行为唯一的文本列名。只支持原生标量数据；含公式的输入会明确拒绝，避免把公式文字或过期缓存误作事实。

参数只有输入、输出和搜索预算，不需要填写医生、科室、目标字段或文件位置：

```bash
python -m schema_cf.run --input /path/to/case --out /path/to/output \
  --tasks 3 --max-candidates 120 --max-views 48
```

没有适用规则时，输出空候选／空任务列表，不补造业务关系。原输入结构有问题时明确报错。输出目录必须位于输入目录之外。

## 如何自动选择任务

1. 读取已声明的主键、外键和类型。普通表格则分析唯一 ID 列、重复类别、布尔值；推测的跨表关联只记录为待确认，不悄悄当成真关系。
2. 从显式外键自动枚举最多两跳关联路径；组合行计数、关联端点去重计数、布尔条件过滤、竞赛排名、最大值集合和是否有记录的判断。自动排除明显的数值／时间格式字符串，避免把泳道用时误作普通类别。
3. 提出外键改指向、已有类别值替换。逐一模拟，检查空值、主键冲突、外键存在性，排除没有派生影响的修改；在预算内优先选择影响深、多处语义变化的候选，并兼顾不同字段、操作。
4. 对选定查询用 Python 计算事实，用独立 SQLite JOIN/COUNT/COUNT DISTINCT 和窗口 RANK 核对。查询定义、条件和来源全部保存。
5. 生成受控 Office 文件时自动登记位置。参考终态由原始文件副本局部编辑产生；固定评分器读取新生成的 Oracle。

例如赛事数据自动选中了 `event.Stadium_ID: 2 → 1`。这会改变各场馆关联的赛事数量；`record.Event_ID → event.Stadium_ID → stadium.ID` 的两层查询又改变各场馆关联的成绩记录数量、排名和最大值判断。这条路径来自源 schema，不是手工登记赛事案例。

普通 XLSX 没有外键声明时，目前仅生成有证据支持的类别分组任务；不会自动完成上述跨表传播。对应候选关联见 `analysis.json` 的 `inferred_relationships_not_used`，补充确认后的 schema 才能进入关联查询。

## 实际复用与新增部分

- **Doc2DB-Bench**：原有 medical/case1、sports/case1 的原始 schema 与数据；实际调用 `load_database_from_directory` 和其结构化模型。没有宣称执行上游完整 LLM 文档流水线。
- **OfficeEditBench**：实际调用 `hard/patch_office.py` 的 `patch_xlsx`、`patch_docx`、`_xlsx_sheet_paths`、`_rewrite_zip`；调用 `medium/cohorts/cross_media/verify_task.py` 的 `workbook_diff`。各任务的 `private/upstream_calls.json` 记录操作数和差异结果；`upstream_reuse.json` 保存源码 SHA、commit、许可。
- **上一阶段自写模块**：复用 `medical_cf/oracle.py` 的规格生成、Required/Protected/Consistency、掩蔽值后的原生 OOXML 结构检查及独立评分入口。本轮只增加可配置文件名和数字值比较选项，原医疗测试保持通过。反例注入还复用自写 XML 编辑工具。
- **本轮新增**：普通表格输入适配；schema 分析；通用查询计划和候选组合、模拟筛选；SQL 独立执行；通用受控布局；连接现有评分器的适配与测试。
- 没有集成 WTM-Bench 或 WordEval 的评分器；此前只是核对了它们可提供的组件。没有把静态上游 gold 当成新答案。

直接复用上游编辑函数需要两项有测试依据的适配：新生成的 worksheet XML 先统一为带 `x:` 前缀，以兼容上游单元格正则定位；只修改本实验生成资产，不改输入或上游。上游 `workbook_diff` 对所有数字采用近似比较，可能漏报 `100000001 → 100000002` 这类编号变化，因此保留其结果，再补精确差异检查。这些是接口／可靠性适配，不声称为研究贡献。

## 产物与评分

```text
artifacts/
  cross_input_results.json          三种输入的实际结果
  inputs/sports_tables_only.xlsx    格式验证用普通表格
  {medical,sports,plain_xlsx}/
    input_manifest.json            输入来源、SHA、约束范围
    upstream_reuse.json             实际复用模块、版本及许可
    analysis.json                  结构、可改字段、查询计划、待确认关系
    candidates.json                候选、约束拒绝原因、影响范围与评分
    selection.json                 自动选中的干预，无手写字段配置
    baseline/input/{data.xlsx,report.docx}
    baseline/private/              数据、事实、位置映射、独立验证
    tasks/<自动任务ID>/
      status.json                  draft_needs_business_review
      public/instruction.md
      public/input/{data.xlsx,report.docx}
      private/
        parameters.json
        before_data.json / after_data.json
        truth.json / dependency_graph.json / bindings.json
        training_labels.jsonl
        oracle.json
        independent_sql.json / native.json / upstream_calls.json
        reference/{data.xlsx,report.docx}
        submissions/               正反例产物
        reports/                   每个检查的 expected / actual / pass
  logs/{build.log,pytest.log,pytest.xml}
```

只把选定任务的 `public/` 提供给求解端。公共文件不含私有事实 ID 或依赖标签。当前受控布局仍直接展示查询定义，尚不是隐含关系难度已经充分验证的最终 benchmark。

固定的已有评分器也能独立读取本轮 Oracle，无需重新生成：

```bash
python -m medical_cf.evaluate \
  --submission /path/to/task/private/reference \
  --oracle /path/to/task/private/oracle.json \
  --report /tmp/schema_cf_score.json
```

每组参考产物 PASS；只改 Excel、漏改语义、错误数值、误改无关内容、公式替换为常量、损坏文件等均 FAIL。受影响依赖的某个布尔结论仍成立时，它会进入 Protected；保留它通过，不必要翻转失败。独立 SQL 错误注入测试确认不会仅靠生成器和 Oracle 自洽而通过。

## 尚未自动化的关键边界

**结构有效不等于业务有效。**所有自动产物的 `status.json` 都明确标记为草案，没有自动批准业务含义。

尤其是本轮医疗候选可能包含 `PrimaryAffiliation: 0 → 1`：它满足上游 schema 的字段、主键与外键约束，但可能违反上一阶段人工登记的“一位医生恰有一个主要科室”。这个约束没有写在原 schema 中，通用层目前不能从表格自动证明它。因此该候选不能直接作为业务正确的正式 benchmark 任务发布；需要接入经过确认的额外约束后重新筛选。数值真值只表示“执行该数据修改后，明确查询的结果是什么”。

当前实现解决的是跨 schema 的候选提议和可执行计算，不是完全自动发现业务规则。没有任意数值公式推断、自动容量阈值、增删记录、复合外键、PPT、自由语言改写评分或训练。结构保护严格，其他编辑器的重存行为尚需独立校准。零引用分组、并列、缺失键、原始数据异常均有明确处理或保留策略。

数据仍保留 Doc2DB／Spider 来源记录；OfficeEditBench 工具按其 CC BY 4.0 记录署名。Doc2DB 数据的再分发授权须单独核对。以上本地实验不修改或重新许可原资产。
