# 多文件证据重建与增量维护：最小实验

## 有状态酒店审批实验

有状态版本用 Excel 保存申请、签发快照和名额释放记录。评测逐步调用 Agent 提交的 Python 命令，每一步重新打开同一份工作簿，核对返回值、状态和保留内容。

```bash
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_stateful_bench
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_stateful_agent
```

在 `ICM2027` 根目录运行。构建器需要 `openpyxl` 和 `bubblewrap`。模型实测还需要 Codex CLI 与已授权的 OpenAI 登录。生成资产在 `multifile_reconstruction/reasoning_graph_mvp/logic_first/business_artifacts/hotel_stateful_actions/`。新建一组实验时，为生成器传入 `--output`，为运行器传入同一路径的 `--source`。

连续模式继承每轮实际提交的可读工作簿和程序，包括业务检查失败的提交。独立模式每轮使用标准前序版本。两种模式共享第一轮起点和测试。下文记录的是原有的证据重建与公式评测实验。

## 第三轮业务升级范围对照

两臂共用第一、二轮的实际成果、Excel 账本和 Word/PPT 报告模板。第二轮开始，每次生效的业务操作同步当前报告，签发保存独立 Word/PPT 快照。第三轮窄版新增退款。扩大版另外接入退款冲销、跨月更正、审批撤回和报告修订。评分比较共同测试和扩大版独有的交互测试。

```bash
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_upgrade_bench
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_upgrade_agent
```

构建过程使用三种酒店数与名额规模，生成连续操作序列。通过标准、未编辑及故障对照后冻结资产、评分源码和 Oracle 哈希。运行器先验证哈希，分别报告共享和新增测试，并归档实际输入输出。两臂同时改变了新增功能数量和交互范围，结果用于衡量升级范围的影响。

**输入协议已修正**：不再向模型提供 JSON schema、字段清单、活动数量或按位置定义的子结构。只挂载三份原始 Office 文件和业务目标，要求模型自行组织状态。标准结构仅存在评分端。旧 `ferry/`、`ferry_revision/` 的 100/100、89/89 均属于“提供 schema 的旧实验”，不能作为无框架重建结果。后续默认输出使用 `ferry_no_schema/`、`ferry_revision_no_schema/`。旧结果完整保留。最新无框架单轮／两轮对照见下节。

自由组织的JSON尚无可靠语义对齐评分器，因此其状态标为 `NOT_SCORED`，不能用标准字段路径不匹配判错，也不能报100/100。原生Office修改继续单独由隐藏Oracle评分。模型输出目录在重新运行前会归档到私有目录，避免上次包含标准答案的评分文件通过输出挂载被模型看到。

## 无框架单轮／两轮对照

```bash
cd /mnt/docker_disk/tmp/data/ICM2027
PYTHONDONTWRITEBYTECODE=1 python -m multifile_reconstruction.ablation
```

输入使用同一份 `ferry_revision_no_schema` 包，复制到互相独立的两个工作区；Office输入哈希、业务修改要求和冻结Oracle完全一致。两轮组先自行组织JSON，再续接原会话修改文件；单轮组在全新会话直接收到同一个修改要求。两个组都需实际保存原生Office文件和自行组织的新JSON；不提供字段清单、标准答案或评分反馈。每组可自行使用多次工具调用，“轮”指用户任务轮次，不代表只有一次模型API推理。

实验包保存 `plan.json`（推理前冻结的输入/Oracle哈希）、每组提示词、真实工具轨迹、用时、token记录和逐项文件评分。最新结果导出为本目录的 `单轮与两轮对照.json`；资产用 `两轮6.1结果_`、`单轮6.1结果_` 区分，第一轮笔记为 `两轮6.1原始重建_状态.json`。旧 `模型6.1结果_` 是此前有schema实验的历史文件。

一次成对实验只能说明这一个任务的观察结果。两轮组同时多了预先阅读、推理预算、JSON笔记和会话历史，不能把差异仅归因于JSON这种格式。初始自由JSON如有事后核对，单独标记其人工设计的一次性字段映射、核对范围与时间，不能将其冒充通用自动语义评分器。

本次实际对照 `artifacts/json_ablation_20261119_195714/`：两轮约282.5秒，单轮约207.1秒；两组原生文件均通过18/18必改、155/155保护、5/5一致性和3/3布局边界，输入和Oracle哈希保持不变。这个单例没有观察到最终文件正确率优势。第一轮自由JSON另经事后一次性字段对照核验：原始100项事实均有对应且值相同，额外的355分钟活动、90分钟间隔和445分钟总跨度也正确；未完整认证所有额外叙述与来源定位。此项核对记录在两轮组 `private/initial_json_review.json`，没有反馈给被测模型。

当前证据难度有明显限制：按生成端主绑定登记，100项事实中99项可直接读取，1项需组合Excel开始时间与PPT时长；Excel没有公式，PPT同时给出时长表与可编辑图表。文件分散不等于推理困难。下一步应固定事实与干预，单独改变证据计划，控制必须跨文件才能恢复的比例、计算/条件层数和冗余程度；公开证据必须足够支持正确答案。此项设计讨论不等于已经实现更复杂生成器或确认论文新颖性。

最新一组查看文件直接并排放在本目录，不使用 before/after 子目录。文件按前缀区分：`原始事实_`、`反事实标准答案_`、`模型6.1结果_`，每组包含日程.xlsx、说明.docx、简报.pptx、状态.json，共9个Office文件与3个JSON。原始JSON来自原始真值，反事实标准JSON来自隐藏新真值，模型JSON来自其实际保存的 updated_state.json，不能混用。`task.md` 保存修改要求。后续正常完成模型两阶段运行会自动更新这三套查看文件；`--skip-model` 不会拿参考答案或旧提交冒充新模型结果。查看副本不作为被测模型的公开输入。详细私有真值、日志和历史仍留在实验包内。

以 RevPropBench 已审定的 `travel_itinerary_L100_06` 为隐藏真值，将100个叶子事实分配到三份原生 Office 文件。第一轮模型自行组织原始状态 JSON；第二轮**续接同一模型会话**，交给它原文件的可写副本和它自己的上一轮回答，再要求取消渡轮、局部更新文件。中途不反馈标准答案，也不纠正它的旧回答。

新增的间接证据只覆盖一个受控时间规则：Word 不再直接给出咖啡活动的结束时间，而要求结合 Excel 中的开始时间和 PPT 原生图表中的活动时长计算。Doc2DB Refiner 从两条经过登记的等价句式中选择表达；程序拒绝范围外的表达，并独立读取两个原生数据源核算。**这不是任意隐含证据的自动生成与正确性证明**。上游 Refiner 的 `_generate_with_llm` 在检查前直接 `return texts`，其后恢复性验证代码不可达；不能声称调用上游就已经完成了可靠验证。

反事实真值复用原任务的人工审定补丁，经原版 `benchmark.jsonptr.apply_patch` 执行；另用 `datetime` 从当天起点、时长和间隔逐项重算，核对所有新时间与汇总。删除四项渡轮专用准备事项的业务政策仍来自原作者审核记录，并非自动发现。Office 参考文件通过复制原文件后局部编辑生成，不重新调用模型写整篇文章。

新增 Oracle 的接受范围是**保留无关措辞与结构的最小修改**：比较原生单元格、段落、样式、幻灯片对象、图表缓存与内嵌可编辑数据，并独立检查时间、人数汇总和图表。Word 连续空白差异可接受；任意自然改写、重新排版不在当前接受范围内。原文件没有原生 Excel 公式，测试中的“公式注入被拒绝”不能算作已经验证复杂原始公式保护。

## 实际执行的上游组件

| 来源 | 实际调用 | 本轮作用 |
|---|---|---|
| RevPropBench | 读取原始任务 `current_json` | 已有事实与来源；不重新猜测真值 |
| Doc2DB-Bench | `WriterAgent.run`、`WriterInput`、`TaskBlock`、`TaskItem` | 将带来源标签和事实占位符的片段组织成自然段落 |
| Doc2DB-Bench（`--indirect`） | `RefinerAgent.run`、`RefinerInput`、`CapabilityMatrix` | 一条受控 RI-TC 时间证据；另由扩展层进行语法白名单与数值检查 |
| OrgSmith | `Fact`、`resolve_text` | 模型写完后再确定性填入事实值，保留原值 |
| Worldloom | `ArtifactIR`、`Table`、`Chart`、XLSX/DOCX/PPTX的`render` | 原生 Office 布局、文件和可编辑 PPT 条形图 |
| DRBench | 未调用生成器 | 参考将证据分配到不同文件的设计；当前公开仓库未找到完整通用生成入口 |
| SynthDocQA | 本轮未调用 | 已下载作为后续图表、PDF布局参考，未冒充实际集成 |

四个新仓库位于 `../三方依赖代码/{Worldloom,OrgSmith,SynthDocQA,DRBench}`。上游源码保持不变。来源 SHA、原始样本和许可证文件哈希由每个实验包的 `private/sources.json` 记录。派生材料继续受 RevPropBench 原始许可证约束。

通过原模块接口接入 Writer 和可选 Refiner；不运行含 `Order_Items` 过滤的完整 pipeline。新增内容为独立实验目录中的旅行布局适配、占位符校验、文件定位、受控时间语法、局部修改、最小修改 Oracle 和模型运行器。没有接入 OfficeEditBench 的 Office 修改器；本实验不能将它列为实际调用。

## 一条命令

在 `/mnt/docker_disk/tmp/data` 下：

```bash
bash ICM2027/run_multifile_reconstruction.sh --revision --indirect
```

完成：缓存的原始写作 → Refiner 时间片段 → 三文件 → 100项原生事实审计 → 原作者补丁与独立重算 → 局部参考文件与 Oracle → 8个正反例 → 模型重建 → **原会话继续修改文件** → 原生文件和新 JSON 分别评分。模型推理使用现有 Codex 登录，不向它开放 `private/`。`--skip-model` 跳过两个被测模型阶段；首次 Refiner 写作仍需模型。完全离线工程验证用：

```bash
bash ICM2027/run_multifile_reconstruction.sh --revision --indirect --literal-author --skip-model --out /tmp/ferry-revision-offline
```

仅重建实验原命令仍可用：

```bash
bash ICM2027/run_multifile_reconstruction.sh
```

需要本机现有 Python Office依赖、bubblewrap和已登录的Codex。首次调用现有 Codex Relay 生成带占位符的说明，再通过 Codex CLI 让模型自行读取原生文件并重建 JSON。使用当前配置的模型。已接受的说明保存下来；同一输出包重跑会复用，不重复生成说明，但默认会重新执行重建。

仅生成材料并审计，不运行重建：

```bash
bash ICM2027/run_multifile_reconstruction.sh --skip-model
```

完全离线工程验证（段落为原模块的字面回退，不是模型生成文章）：

```bash
bash ICM2027/run_multifile_reconstruction.sh --skip-model --literal-author --out /tmp/ferry-offline
```

不要在同一个输出目录混用自然语言和离线回退作者。不同实验请使用不同 `--out`。运行记录明确区分生成审计、程序构造的正反例与真实模型结果。

## 给被测模型的内容

只挂载 `public/`：

- `day_schedule.xlsx`：活动顺序、名称和开始时间。
- `visitor_notes.docx`：活动结束时间、预约、交通、天气、设备及联系等说明。
- `travel_brief.pptx`：旅行概况、同行人员、住宿、预算、汇总和活动时长；包含原生条形图和图表底层数据。
- `instruction.md`：理解现有行程的业务目标，没有隐藏字段清单或取消渡轮的干预。

原始100项事实、标准schema、位置绑定、原任务修改要求及标准补丁都留在 `private/`。生成端仍是一套**已登记的旅行场景布局**，不是任意JSON的自动业务布局发现；被测模型不能获得这份组织结构。

说明生成使用 Doc2DB 原始 Writer；模型只改写占位符周围的语言。每个标签和占位符必须保留一次且对应不变，再由 OrgSmith 填入原值。标签在渲染前移除。该过程能检查值与出处，不等于证明整篇自然语言完全没有歧义；正式扩展前仍需语义审阅。

## 文件及评分

```text
artifacts/ferry/
  public/                   # 仅此目录交给模型
  private/
    truth.json              # 原始current_json，不来自模型回答
    source_sample.json
    bindings.json           # JSON字段 → 单元格/段落跨度/幻灯片对象/图表数据点
    *.artifact_ir.json
    sources.json
    native_audit.json        # 独立读回100项原生事实；检查隐藏表与残留构建标签
    controls.json
    controls/*.json         # 正确、漏Word证据、错图值、错对象、错布尔
    authoring/              # 作者模型输入、原文、已接受文章与传输记录
  model/
    prompt.txt
    events.jsonl            # 真实读取/命令轨迹
    stderr.log
    reconstruction.json
    evaluation.json         # 每字段 expected/observed/status/来源
    run.json                # 模型配置、用时、隔离和输入哈希
  build_summary.json
  summary.json
```

旧版提供schema时的JSON评分和私有评分器正反例按原始叶子逐项比较并严格检查类型；字符串仅容忍大小写、空格和末尾句点。这套逐路径评分不再用于无框架输入的自由JSON输出。当前绑定记录生成端已知的主要证据位置，不宣称已枚举所有重复提及。

模型通过 Bubblewrap 独立工作区运行：宿主 `/mnt` 被隐藏，仅公开资产和模型自身输出目录挂载进去；它看不到私有真值或上游样例。临时 Codex home 仅携带现有登录信息，不加载用户配置和项目说明。未声称这是完整恶意Agent安全沙箱；事件轨迹仍需检查是否只使用公开材料。

原生对象读取允许通过 Python 查看图表底层数据，**不等于视觉读图或GUI能力评测**。后续若测图片模式，需要单独对照，不应混记为本轮结果。

## 验证

```bash
cd /mnt/docker_disk/tmp/data/ICM2027
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -o addopts= -p no:cacheprovider multifile_reconstruction/test_reconstruction.py multifile_reconstruction/test_revision.py
```

新增结果位置：`private/refiner/` 保留真实 Refiner 输入、输出和检查；`private/revision/` 保存新真值、已登记依赖规则、独立断言、参考文件、Oracle 和逐项正反例；`phase2/instruction.md` 是第二轮要求，第一轮不挂载；`model/revision/working/` 是模型实际修改后的文件，`native_evaluation.json` 和 `state_evaluation.json` 分别检查 Office 产物与新 JSON。`fact_changes.json` 区分“计算结果变化”与“文件表述必须变化”：咖啡结束时间会改变，但其通用计算语句无需修改。

本次实际运行：第一轮 100/100；续接同一会话后新状态 89/89；Office 必改18/18、保护155/155、一致性5/5、布局边界3/3。原生参考正例通过，七种错误提交均失败。13项自动测试通过。首版判分误将空白、Excel 行压缩和原生表格高度变化视为错误；后续发现参考生成器重写图表时应保留原始数字格式。修正后用正反例回归，再对**未修改的原模型产物**重新评分。早期 Oracle 和判分保留在 `private/revision/oracle_audit_v1/` 与 `oracle_audit_v2/`，最终为版本3，不能把本轮说成预先冻结 Oracle 的正式排行榜实验。

独立给一份提交判分（在 `ICM2027/` 目录下）：

```bash
python -m multifile_reconstruction.revision --submission multifile_reconstruction/artifacts/ferry_revision/model/revision/working --oracle multifile_reconstruction/artifacts/ferry_revision/private/revision/oracle.json --out /tmp/ferry-submission-evaluation.json
```

8个对照：完整正确、只改 Excel、漏清理语义内容、误改无关内容、时间错误、只篡改图表缓存、公式注入、破坏原生结构。报告包含每个对象的预期值、观察值、必改/保护/一致性分组与失败原因。计数是文件对象检查数，不能当作业务事实数量。

本阶段验证受控证据整合及同一产物的维护闭环。没有证明新的反事实生成算法、跨领域泛化或现有研究未覆盖的创新。
