# 酒店净收入的直接证据与间接证据

从 `ICM2027/` 运行一条命令生成两套原生 Office 任务并完成验证。

```bash
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_revenue_evidence
```

默认产物在 [hotel_revenue_evidence](business_artifacts/hotel_revenue_evidence/)。用 `--output <目录>` 可指定输出目录。每版的 `public/` 含任务说明和三份原生文件，`private/reference/` 含更正后的参考文件。共享依赖图和退款反例位于总目录的 `private/`。

## 两版如何配对

两份输入的任务说明逐字节一致，程序检查 Excel 和 Word 的原生对象状态相同。它们都公开同一份已确认退款记录和同一套口径。直接版在 PPT 中公开净收入，间接版将相同的图表位置用于公开客房入账。这个设计保持 Excel 观测行数、政策说明和全部修改目标一致。

以 Willowbank 为例，程序从间接版 PPT 原生图表和 Excel 源工作表分别读回 1,125,000 和 45,000，计算净客房收入 1,080,000。与已售间夜 8,000 结合，算出平均房价 135 元。两版使用同一事件和业务真值，入选名单都从仅 Harborstone 变为 Harborstone、Silverbrook 和 Willowbank，三家均价为 140 元。

## 核验产物

- `summary.json` 记录两版的目标深度、文件必要性、参考评分和被拒绝的负例。
- `private/graph.json` 记录全部节点与前后真值，包含净收入等于入账减退款的可执行边。
- 每版的 `private/revenue_evidence.json` 记录原始和修订资产的读取与恢复证据。间接版另有以相同入账、相同更正和相同历史结论构造的退款反例。
- 每版的 `private/visibility.json` 检查八个文件子集。`private/shortcuts.json` 保存普通重算、历史均价差量更新及 Excel 操作文法下的可行路径。
- 每版的 `private/evidence_map.json`、`private/propagation.json` 和 `private/oracle.json` 保存原生位置、节点状态及评分规则。

净收入的最短恢复深度从 0 增至 1。资格名单、入选家数和平均房价这三个评测目标的最短并行深度仍为 5、6、6。难度说明以登记的计算规则和已核验的文件内容为准。

泄漏扫描覆盖任务文字和 OOXML 字面值，包括隐藏工作表、备注、公式和缓存、嵌入工作簿。扫描与可执行证明分别验证数值直接出现及规则范围内的推导捷径。收入分项来自程序构造的合成账本，Word 使用确定性双语口径。构建阶段的 Agent 调用次数为零。

## 跨业务验证与连续更正

从 `ICM2027/` 运行：

```bash
python -m multifile_reconstruction.evidence_domain_assets
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_revenue_sequence
```

`evidence_domain_assets` 从 Doc2DB-Bench 原始医疗和赛事表生成六个状态。三类资产提供原始总数、排除数和复核条件。验证器从 Excel、Word、PPT 读回，使用同一个差值变换恢复净计数，再与原表独立筛选后的统计比较。医疗通过任职外键统计主要任职医生，赛事通过场馆外键统计胜负记录。样例数据来源及文件哈希保存在 `multifile_reconstruction/artifacts/evidence_transfer/`。业务字段映射和计数口径由各域适配器登记。

`hotel_revenue_sequence` 构建两次连续更正，默认产物在 `business_artifacts/hotel_revenue_sequence/`。第二轮可以从第一轮标准结果开始，也能从实际提交继续。`prepare_stage_two` 只更正批准的新来源单元格，并保留上轮的已发布结果和其他原生对象。两组第二轮使用同一题面和同一份 Oracle。报告分别记录本轮遗漏、旧错保留或修复、无关内容误改与历史不可用。

## 三轮功能递进

```bash
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_revenue_milestones --output /tmp/hotel_milestone_repro
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_revenue_milestones_agent --source /tmp/hotel_milestone_repro --preflight-only
```

第一轮建可重算的入住率和平均房价。第二轮用这两项公式建立可重算的资格判断，并签发 Word 历史名单。第三轮保留历史名单，用当前资格与历史名单的交集更新续约报告。`business_artifacts/hotel_revenue_milestones/` 下三轮分别保存题目、标准文件、Oracle 和原生文件哈希。`summary.json` 保存审批名单替换对照和误改负例，`task_dependencies.json` 保存任务依赖和计算规则。

Oracle 用本地 LibreOffice Calc 在临时副本里改变台账输入和资格门槛，并单独替换前一轮的两个公式结果，核对 Excel 的变化。历史名单覆盖和公式固化仅用于离线验证。去掉 `--preflight-only` 会启动 GPT-6.2-sol 正式运行，包含一条继承实际提交的三轮链和第二、三轮的标准起点对照。每轮只暴露题目和三份原生文件，第三轮的唯一来源变更是已批准的间夜数更正。首轮实测的输入和评分规则已冻结。第二轮分文件题面的修订及扩展后的公式测试用于新生成的任务包。

## 共享审批入口与累积回归

从 `ICM2027/` 用新目录构建和预检：

```bash
pilot_dir=$(mktemp -d)
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_evolution --output "$pilot_dir"
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_evolution_agent --source "$pilot_dir" --preflight-only
```

第二轮建立 `审批办理!B8`，用 `B3` 选择酒店并调用第一轮的指标。第三轮把同一个入口升级成支持 `B6` 的「新签」和「续约」。以房价 120 元且曾签约的 Willowbank 为例，新签返回 0，续约返回 1。输入输出位置保留，公式写法可以改变，也可以在计算表内添加辅助单元格。

每轮都用提交的工作簿重跑此前的功能测试。Calc 在隔离副本里改变间夜、入住率、房价线、酒店名称、模式和历史签约标记。第三轮分别报告经营指标、新签和续约的功能检查。`summary.json` 保存参考结果、交接记录和两类负例，其中一类续约全对，但旧的新签规则退化。正式模型运行沿用实际提交，不向模型文件中注入负例。默认实测包在 `business_artifacts/hotel_evolution/`。

首轮实测保留原始的文件结构评分。第三轮的两个提交都将 Excel 发布格改为可重算公式，旧读取器按静态值读取时判错。另用 `hotel_evolution_audit --run <运行目录>` 从重算后的临时副本读取发布值，并把功能扰动放在公开输入及上游指标，不直接覆盖展示格。`hotel_evolution_replay --run <运行目录>` 使用最新版文件恢复旧的 8,000 间夜输入，再运行第二轮留存的测试。两份报告均保留原始得分和提交文件。

新生成的题目明确区分本轮发布值和固定历史记录。评分入口直接支持本轮 Excel 资格标记和续约家数中的公式，并在隔离副本重算。历史签约名单和家数继续按原内容检查。`hotel_evolution_rescore --run <运行目录>` 对原提交和标准文件复评，再验证续约人数错误、历史名单被删改，以及后续业务变化时历史记录保持不变。新报告为 `publication_rescore.json`。

## 医疗预约迁移

从 `ICM2027/` 构建任务和运行无模型预检。

```bash
clinic_dir=$(mktemp -d)
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.medical_evolution --output "$clinic_dir"
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.medical_evolution_agent --source "$clinic_dir" --preflight-only
```

第二轮的 Excel 预约入口使用第一轮的出诊判断，第三轮复用同一入口支持改期。Word 提供冲突规则，PPT 提供诊室开放信息。当前台账在改期时更新，历史台账保留最初的确认时间和诊室。默认实测资产和验证结果在 `business_artifacts/medical_evolution/`。医生编号和诊室名取自公开医疗案例，班次、预约过程和测试输入为构造数据。去掉 `--preflight-only` 会启动一次三轮接续和两次标准输入对照。

## 酒店八轮扩展

从 `ICM2027/` 运行：

```bash
eight_round_dir=$(mktemp -d)
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_long_evolution --output "$eight_round_dir"
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_long_evolution_agent --source "$eight_round_dir" --comparison-model gpt-6.1-sol
```

前 3 轮复用已冻结的酒店实测。第 4–8 轮在原审批入口上加入分级门槛、特批与撤销、退款更正、批量名额和历史政策回放。单家审批和批量排名使用同一列资格结果。第 8 轮按所选政策与数据版本重算，并保留历史档案与已发布结果。评分包含此前仍适用的业务条件，并在临时副本恢复旧输入时测试旧版功能。每一轮均有标准起点和实际文件接续两个实验。可选次模型只做第 8 轮标准起点对照。

