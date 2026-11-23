# 医疗多表反事实最小实验

这是 Doc2DB-Bench 外部的实验扩展。没有修改任何上游源码或原始数据。第一阶段使用 Excel + Word；未实现 PPT、通用关系发现或模型训练。

## 一键复现

在 `/mnt/docker_disk/tmp/data` 执行：

```bash
bash ICM2027/run_medical_cf.sh
```

依赖：Python、`medical_cf/requirements.txt` 中的包，以及 `libreoffice`。当前环境已执行此命令；它会构建三组任务、生成和评分正反例、运行测试，并保存日志。没有 API 请求，不需要 API key。

自定义干预，只修改 JSON，不改验证器：

```json
{"interventions": [{"id": "molly_to_surgery", "physician": 9, "department": 2}]}
```

```bash
bash ICM2027/run_medical_cf.sh /absolute/path/to/params.json medical_cf/artifacts_custom
```

从其他同结构数据构建：

```bash
cd ICM2027
python -m medical_cf.run --case /absolute/path/to/case --config /absolute/path/to/params.json --out medical_cf/artifacts_other
```

case 必须含原医疗结构的 `schema.json` 和 `tables/*.json`。当前不是任意 schema 自动适配器。

## 已执行的数值与语义结果

原始主要任职人数：普通内科 5、外科 3、精神科 1。

| 参数 | 新人数（内科／外科／精神科） | 人数最多的科室 |
|---|---|---|
| John（1）→ 外科（2） | 4／4／1 | 内科、外科并列 |
| John（1）→ 精神科（3） | 4／3／2 | 仍为内科，原“最多”结论须保留 |
| Elliot（2）→ 外科（2） | 4／4／1 | 内科、外科并列 |

测试还覆盖：Molly（9）→ 外科，得到 5／4／0；新增医生、新增空科室、重排记录并更名的同结构实例；全部 16 个受支持的医生转科组合。另 11 种组合因无实际变化或目标已有该医生的任职记录而明确拒绝。

排名采用竞赛排名：4／4／1 对应第 1／1／3 名。只统计 `PrimaryAffiliation=1`，不把次要任职重复计入。

## 产物位置

```text
artifacts/
  source_manifest.json              上游 commit、原始数据/代码/文档 SHA-256
  upstream_reuse.json               实际上游调用、入口阻碍、未集成组件
  parameters.json                   本次干预参数
  summary.json                      汇总及已知局限
  baseline/
    input/hospital.xlsx             六张原始表 + Primary、Summary 工作表
    input/review.docx               自动生成的科室、统计、排名及判断表述
    private/{data,facts,bindings,rules,evidence,independent}.json
    private/native_independent.json  独立读取 Office 产物的检查
    private/native_recalculation.json  LibreOffice 临时副本重算结果
  tasks/<任务名>/
    public/
      instruction.md
      input/{hospital.xlsx,review.docx}
    private/
      parameters.json
      before_data.json / after_data.json
      truth.json                    干预前后事实真值
      dependency_graph.json         来源、规则、条件及依赖边
      bindings.json                 自动生成的字段—文件位置
      training_labels.jsonl         字段、来源、规则、依赖、位置标签
      oracle.json                   Required / Protected / Consistency / Structure
      reference/{hospital.xlsx,review.docx}
      independent.json              SQLite 独立计算逐条结果
      native_independent.json        最终文件内容独立断言
      native_recalculation.json      临时副本原生公式重算
      submissions/                  正反例文件
      reports/*.json                各对象 expected / actual / pass
  logs/{build.log,pytest.log,pytest.xml}
```

只向 Agent 提供 `tasks/<任务名>/public/`。不得提供 baseline/private、任务 private、整个生成目录或生成器源码。公共输入保留旧状态，由任务说明指定新基础事实。

独立评分，不重新生成答案：

```bash
cd ICM2027
python -m medical_cf.evaluate \
  --submission medical_cf/artifacts/tasks/john_to_surgery/private/reference \
  --oracle medical_cf/artifacts/tasks/john_to_surgery/private/oracle.json \
  --report /tmp/medical_cf_evaluation.json
```

## 什么已经自动化，什么仍是人工定义

人工定义一次：医疗字段的业务含义；只统计主要任职；排名及并列规则；允许的转科方式；Word 句式、工作表布局及保护策略。

程序自动完成每个实例：加载六张原表；约束检查；计算主要科室、人数、排名和领先状态；证据编排；生成原始 Office 文件并登记位置；修改数据库副本；重算；仅修改参考副本的相关对象；对比生成 Required/Protected/Consistency；导出标签；运行正反例与独立检查。改变医生或目标科室参数不需要手工增加文件位置或修改评分器。

绑定位置是在插入单元格/段落时获得的。没有事后手工写“John 对应 Word 第几段”。派生事实 ID 使用实体 ID；原始单元格事实 ID 使用本实例的表名/行号/列名，并同时记录主键，不应把其行号当成跨实例稳定身份。依赖图对任职记录使用保守依赖范围，不声称每条边都是最小因果依赖。

## 实际上游复用与边界

源仓库：`../三方依赖代码/Doc2DB-Bench`；commit `2c8158baca23185f3b5dac71de39779ec53e92b4`。

数据直接读取 `dataset/medical/case1/schema.json` 及 `tables/{Physician,Department,Affiliated_With,Patient,Nurse,Appointment}.json`，共 39 条原始记录。原有五份 Markdown 被记录来源和哈希，没有冒充为新 Office 原版文件；本实验的 XLSX/DOCX 是新增渲染产物。

实际调用原 `load_database_from_directory`、结构化 models、`SerializerAgent.run`、`DocumentSynthesisPipeline._assemble_document` 和 `ValidatorAgent.run`。原 serializer 的排序结果决定 Word 派生表述的编排；原 validator 在私有证据层检查来源标签覆盖。其检查不等同于独立验证最终 Office 语义。

原 `run_demo.py` 导入不存在的 `agents.refiner_multi`；原 `run_database` 的 `Order_Items` 过滤会跳过全部医疗表。本实验对原循环作了无网络、无写入的受限运行验证，实际处理表数为 0，见 `upstream_reuse.json`。扩展不修改该文件，不宣称原完整 LLM 流水线已跑通。

本轮新写的是业务派生、受控文本、Office 渲染和局部编辑、绑定记录、Oracle、独立验证。原 LLM Labeling/Refiner/Writer、soft verifier 均未调用；EditCoherenceBench 等其他仓库没有集成到本轮。无需训练模型或依赖自由生成推理。

## Oracle 与独立性验证

每组保存具体检查结果：正确参考通过；仅更新 Excel、遗漏语义、误改患者姓名、错误人数、破坏公式、损坏 Word 结构、破坏主要任职约束均失败。数值变化但领先判断不变时，保留旧判断通过，错误翻转判断失败。

生成器用 Python 计算；独立检查用 SQLite 的 FK、LEFT JOIN/COUNT 和窗口 RANK。另有不读取 bindings/Oracle 的原生文件内容断言。LibreOffice 在临时副本重算总人数公式，不改原参考文件。测试故意篡改计算真值及参考文字，验证独立检查确实会拒绝。

Oracle 是固定评测器读取每次新生成的 JSON 检查规格；不是自动编写任意验证程序。跨文件检查要求同一事实的各处表示都与对应真值一致。

## 局限

- 当前仅医疗 schema 和已登记业务规则，尚不是通用关系/规则发现器。
- 采用短、受控英文句式，没有复现上游长文 LLM 生成或证明真实文档匹配泛化。
- Word 采用规定模板精确检查，不能给任意同义改写判分。结构检查严格比较掩蔽值后的 OOXML；其他编辑器重存引起的序列化/元数据变化也可能失败，尚未作为开放编辑环境的通用判分器校准。
- 人数与排名为确定性物化值，不是 Excel 派生公式；保留并独立重算的是总人数 SUM 公式。
- 转入已有次要任职的科室可能导致复合主键冲突，当前明确拒绝，没有擅自合并任职记录。
- 原预约表含一条结束时间早于开始时间的异常；本实验原样保护，未将其当成转科引发的变化。
- 本地未找到上游根 LICENSE；已记录 Doc2DB/Spider 来源，公开再分发前仍须核对授权。没有据此停止本地实验。
- 本闭环证明受控构造可运行，不证明论文新颖性、外部 Agent 能力或训练标签在真实文件上的泛化效果。
