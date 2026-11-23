# 两阶段、多业务目标反事实评测

这版把评测目标改为：**同一组业务目标先计算一次；锁定提交后揭示一个事实变更；再计算一次并区分哪些目标变化、哪些不变。**输入不包含任一阶段的统计答案。

## 一键复现

从 `/mnt/docker_disk/tmp/data` 执行：

```bash
bash ICM2027/run_staged_cf.sh
```

该命令依次构建赛事包、运行正反例、验证提交锁定、用医疗包验证复用、真实运行两次同一会话的模型调用，最后运行本轮及已有回归测试。每次生成新的 `artifacts/runs/<时间_随机后缀>/`，不会覆盖旧会话。`artifacts/latest.json` 指向最新成功跑完的实验。模型的业务答案做错会如实记分，不会用真值修补其回答。

依赖沿用现有 `schema_cf`／`medical_cf` 环境和 `codex` CLI 登录。本实验没有新增系统依赖，没有安装软件包，也不需要填写新的 API key。模型读取输入采用已有 Codex Relay：只把公开工作簿的原始表头和单元格值按 CSV 原样传入，不允许主机工具访问。**这验证的是两阶段分析能力，不是 GUI 或 Office 文件读取能力。**

不调用模型，仅构建和验证工程闭环：

```bash
cd ICM2027
python -m staged_cf.run --out staged_cf/artifacts_offline --skip-model --medical-check
```

`--skip-model` 会明确记录未实测，不能把它当作模型实验。

## 阶段一给 Agent 什么

只给选定包的 `stage1/`：

- `source.xlsx`：四张原始表，使用 `event`、`record`、`stadium`、`swimmer` 原表名。仅整理表头、列宽、冻结窗格、筛选和空值单元格表示，数据值不变。
- `instruction.md`：业务背景、冻结的业务目标、必要的计数/去重/并列口径和结果格式。
- `goals.json`：目标 ID、自然语言问题和答案类型，没有查询计划、答案或变化标签。

没有 Metrics 工作表、统计清单 Word、排名结果、Oracle、私有事实 ID 或第二阶段变更。无需为多文件而把答案重复生成到 Word。现有 Doc2DB LLM Refiner/Writer 没有在这版执行，不宣称接通了原有自然语言资产生成流水线。

题目明确业务问题，让模型自行连接原始表、选择计算步骤。当前业务目标和统计口径由作者在 JSON profile 中定义，**不声称程序已经自动理解任意业务目标**。

## 阶段二怎样提供

第一阶段只要提交格式完整，就可以进入下一阶段；答案错误不会阻止揭示，也不会在中间反馈分数。

`Session.commit(1, ...)` 使用排他创建保存提交和 SHA-256。`reveal()` 必须在第一阶段锁定后调用，检查原始输入及提交哈希，再发布阶段二指令。本次赛事指令是“赛事3从场馆2迁至场馆1，其他源记录不变”。阶段二完全沿用原目标，每项额外提交 `changed` 和依据。

提交不能通过接口覆盖；直接修改已锁定文件会被哈希检查拒绝。这是可信评测进程下的防误改和审计机制，不是对拥有主机管理员权限者的防篡改系统。给外部 Agent 部署时只挂载释放后的目录，不能把包含 `private/` 的完整实验根目录提供给它。

真实模型实验直接复用原 `CodexRelayBackend`，两次调用同一角色、同一线程；核对第二次 `thread_reused=true`、线程 ID 一致、没有加载其他指令文件、没有工具事件。后台重新建线程不会静默算作连续实验。

## 业务目标及隐藏答案

以下只供构建者核对，**不能复制到求解端**：

| ID | 目标 | 原始真值 | 干预后真值 | 变化？ |
|---|---|---:|---:|---|
| G1 | 场馆1成绩记录数 | 4 | 9 | 是 |
| G2 | 场馆2成绩记录数 | 5 | 0 | 是 |
| G3 | 成绩记录最多的全部场馆 | [2] | [1] | 是 |
| G4 | 全部成绩记录数 | 13 | 13 | 否 |
| G5 | 全部赛事数 | 6 | 6 | 否 |
| G6 | 场馆1记录中的不同运动员数 | 4 | 6 | 是 |
| G7 | 场馆5成绩记录数 | 0 | 0 | 否 |
| G8 | 场馆1承办赛事数 | 1 | 2 | 是 |

共 5 个变化、3 个不变。额外参数化测试将一条成绩记录从赛事3更正为赛事2，得到场馆1和2并列最多，证明同一评分器可检查不同干预和并列结果。

医疗复用只更换数据与 `medical.json`，不改评分器：John 的主要科室1→2，主要任职人数5/3/1→4/4/1，最多科室从[1]变为[1,2]；其余医生总数、患者数、任职关系总数和已有主管医生关系等保持不变，形成3个变化、5个不变。医疗口径显式要求只统计主要任职、每名医生一个主要科室；不会把次要任职随意翻成主要任职后当作业务正确。

## 真值与评分

复用 `schema_cf` 的 schema 分析、已知关联查询、反事实应用和事实来源。每个目标绑定可执行查询计划或明确的全表行计数；先用现有 Python 计算，再由独立 SQLite JOIN/COUNT/COUNT DISTINCT 查询核对。没有使用模型第一阶段回答作为真值。单元测试额外断言关键固定数字，并故意污染生成器真值，确认 SQL 检查会拒绝。

输出约定见每个包的公开说明。阶段一：`stage`、全部目标的 `value` 和 `evidence`。阶段二：同一目标集合，另含布尔 `changed` 和 `source_edits`。计数要求 JSON 整数；最多名单按集合比较，允许顺序不同，禁止重复编号。

逐目标报告记录：

- 两阶段各自的绝对正确性。
- 真实变化／不变识别准确率。
- 受影响目标的新结果正确率。
- 第一阶段正确且真实不变的目标保持率。
- 第一阶段错误被纠正、错误答案被继承的情况。

例如 G4 的真值两阶段都是13，模型先答999、后答13且标记“不变”，后阶段应得分并记为纠错。它不进入“不变且原来正确”的保持率分母，不能被当成误改。不过第一阶段错误仍保留，因此总体严格通过率不会冒充两阶段全对。

阶段二工作簿由 **模型自己提交的编辑列表** 通过 OfficeEditBench `patch_xlsx` 执行；宿主不会补上模型遗漏的改动。检查原始记录中必须变动的一格，以及所有受保护原始值和掩蔽值后的 OOXML 结构。业务结果全对但没有提交源数据编辑，仍会在原生文件检查失败。

`evidence` 目前只检查存在性，尚未自动判定自由文本推理是否正确。自然报告 prose 可以后续作为输出扩展，目前不提供自由 Word/PPT 语义评分，不把结构化数值评分冒充报告质量评估。

## 正反例

`oracle_controls/` 保存以下程序构造的测试，与真实模型结果分开：

1. 前后都正确且源编辑正确：PASS。
2. 遗漏受影响目标：FAIL。
3. 错误翻转不变目标：FAIL。
4. 前后都错但自洽：FAIL，并记录错误继承。
5. 阶段一错、阶段二纠正：总体 FAIL、阶段二 PASS，正确记录自我纠错。
6. 答案正确但漏改源记录：FAIL。
7. 误改无关源字段：FAIL。
8. 漏交目标：格式 FAIL。

另测：第一阶段缺失时不揭示第二阶段；锁定后不能覆盖；哈希发现直接篡改；第一阶段错误仍允许提交；阶段目标冻结；公开包没有答案／后续干预；并列、空值单元格和跨医疗结构复用。

## 产物

```text
artifacts/
  latest.json
  runs/<时间_后缀>/
    summary.json
    package/
      stage1/{source.xlsx,instruction.md,goals.json}
      build_summary.json
      private/
        profile.json / intervention.json
        before_data.json / after_data.json / schemas.json
        oracle.json / dependencies.json
        independent_before.json / independent_after.json
        query_check_before.json / query_check_after.json
        input_manifest.json / upstream_reuse.json / reuse_inventory.json
        public_audit.json
        reference_stage1.json / reference_stage2.json
        stage2_instruction.md
    oracle_controls/<案例>/{stage1.json,stage2.json,evaluation.json,delivered/}
    reference_session/             正确参考的两阶段提交、事件和结果
    medical_package/              医疗复用包（启用 --medical-check）
    medical_controls/ / medical_reference/
    model/
      prompt_stage1.txt / prompt_stage2.txt
      response_stage1.txt / response_stage2.txt
      call_stage1.json / call_stage2.json
      transport_audit.json
      session/{stage1/,stage2/,submission_stage1.json,submission_stage2.json,state.json,events.jsonl}
      delivered/source.xlsx
      evaluation.json
  logs/{latest_build.log,pytest.log,pytest.xml}
```

## 接其他 Agent

```bash
cd ICM2027
python -m staged_cf.session start --package /path/to/package --session /path/to/session
# 此时只发送 session/stage1 给 Agent。
python -m staged_cf.session submit --package /path/to/package --session /path/to/session --stage 1 --response /path/to/first.json
python -m staged_cf.session reveal --package /path/to/package --session /path/to/session
# 此时才发送 session/stage2/instruction.md。
python -m staged_cf.session submit --package /path/to/package --session /path/to/session --stage 2 --response /path/to/second.json
python -m staged_cf.session evaluate --package /path/to/package --session /path/to/session
```

评测进程保存 `private/` 并执行这些命令；Agent 不获得它们的目录权限。参考提交仅用于本地验收，不能作为模型第一阶段回答输入。

## 复用边界

- Doc2DB-Bench：原始赛事／医疗表格、schema、加载与结构化模型。原 LLM 文章生成器未执行。
- OfficeEditBench：原 `patch_xlsx`、ZIP/工作表定位工具，原源码保持不变；许可和 SHA 记录在私有 manifest。
- 本项目此前代码：schema 分析、查询组合、反事实应用、SQL 校验、原始工作簿导出、原生结构检查。
- OSWorld 原 Codex Relay：实际协议、进程和事件循环；本轮仅加同线程两阶段适配。
- 本轮新增：业务目标 profile、隐藏前后真值投影、发布及提交锁定、逐目标评测、对照测试、复现入口。

本轮没有建立通用业务规则发现器，也没有证明模型在复杂长文或未知真实 Office 材料上的泛化。源数据为上游 benchmark 公开快照，不应称为未经加工的真实企业资料。
