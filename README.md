# CFBench 第一阶段：复用 OfficeEditBench 资产生成反事实任务

本目录新增一个薄扩展层，不改动四个上游仓库。当前覆盖 OfficeEditBench OBM-036 的真实上游 XLSX + DOCX 文件；不生成替代 Office 模板，不实现通用编辑 Agent。

四个项目的实现边界、可复用代码和与研究目标的关系见 [UPSTREAM_AUDIT.md](UPSTREAM_AUDIT.md)。已读取本目录的 `ICML_2027.odt` 和上级的交接说明。

## 已完成的实验

起点是 OfficeEditBench 已修正单位的原版参考文件。以它建立一致的原始状态，再生成原任务没有覆盖的比较基期干预。原参考文件仅作为 baseline；新真值由公式和规则重新计算。

| 状态 | 本期 USD 百万 | 模拟上期 USD 百万 | 同比 | 文字状态 |
|---|---:|---:|---:|---|
| 上游原始参考 | 2466 | 2300 | 约 +7.2% | 温和增长 |
| flip | 2466 | 2600 | 约 −5.2% | 下降 |
| same_status | 2466 | 2350 | 约 +4.9% | 温和增长，状态保持 |

这是应付账款 `AccountsPayableCurrent` 的单位与模拟比较练习，**不是营收案例或真实公司同比结论**。SEC 原始事实不变；变更的比较基期本来就是上游作者构造的模拟值。

两种干预均要求更新当前批准摘要、Word 执行摘要、同比数值与解释；保留公式、其余数据与段落。Word 末尾原批准记录作为历史保留，新指令明确规定这一点。

## 运行

环境需要 Python、LibreOffice，以及同级四个原始仓库。依赖版本记录在 `requirements.txt`；实际环境记录在 `artifacts/logs/environment.json`。

```bash
cd /mnt/docker_disk/tmp/data/ICM2027
# 首次在新环境使用时：python -m pip install -r requirements.txt
bash run_stage1.sh
```

一键命令执行构建、原版验证器检查、正负例、新 Oracle、真实 WTM 评分器调用及 pytest，日志写入 `artifacts/logs/`。pytest 禁用自动加载外部插件，是为了避开宿主 ROS pytest 插件缺少 lark 的环境问题。

单独构建或评分：

```bash
python -m cfbench.build
python -m cfbench.evaluate artifacts/flip/private/oracle.json /path/to/submission \
  --report /path/to/report.json
```

评测 PASS 退出码为 0，FAIL 为 1。评分端需要隐藏 Oracle；求解端只接收公开压缩包。**不能把整个本目录或上游仓库挂载给正式被测 Agent**，因为其中包含评测答案。

## 输出布局

```text
artifacts/
  CFBench_OBM036_flip_public.zip
  CFBench_OBM036_same_status_public.zip
  flip/                         # same_status/ 同构
    public/
      input/unit_review.xlsx
      input/management_discussion.docx
      instruction.md
      ATTRIBUTION.txt
      UPSTREAM_LICENSE.txt
    private/
      truth.json                # 前后事实、来源核查过的依赖图、新文字参考
      oracle.json               # 新干预独立评分契约
      source_manifest.json      # repo commit、原文件 SHA-256、git blob 比对
      reference/                # 新正确终态
      negative_submissions/
      reports/
  logs/
    smoke.log
    summary.json
    pytest.log
    environment.json
    upstream_original_reference.json
    upstream_verifier_on_new_counterfactual.json
```

`cfbench/` 下全是我们的新增代码。上游函数动态导入，未复制改写其实现；来源 manifest 对实际使用的源码、原输入、原参考、gold 和许可证逐一比对 pinned git blob。

## 实际来源和复用

OfficeEditBench commit：`3b4f567970e25d5340571fb1e41b3a6a28e93ecb`。

实际起始文件：

- `OfficeEditBench/medium/cohorts/cross_media/private/reference/OBM-036/unit_review.xlsx`
- `OfficeEditBench/medium/cohorts/cross_media/private/reference/OBM-036/management_discussion.docx`

同时核查并记录 `tasks/OBM-036/input/` 原任务输入和 `private/gold/OBM-036.json`，没有把原静态 gold 当新答案。

实际调用上游代码：

- OfficeEditBench `hard/patch_office.py`：`patch_xlsx`、`patch_docx`、包重写和 sheet 定位工具。
- OfficeEditBench `medium/cohorts/cross_media/verify_task.py`：`paragraphs`、`workbook_diff`、`office_recalc`；原版 CLI 只用于原任务自检和说明旧 verifier 对新干预不适用。
- WTM-Bench `grader/scorer_v2.py`：实际执行 `compute_reward(generated_path, ground_truth_path, initial_path, mode)`，仅作为补充诊断。

OfficeEditBench 原创代码/叙述/布局为 CC BY 4.0；SEC 原始数据来源保留。WTM-Bench 为 MIT。公开包带署名、变更说明和上游许可；完整冻结来源清单的哈希也记录在私有 manifest。

## Oracle 与验证范围

私有图登记了公式、数值引用和文字解释的已确认关系。数值由确定性规则计算；前后事实比较产生 changed / unchanged 集合。Office 对象定位、渲染模板和保护遮罩是本任务适配器手工登记的，**当前没有通用的图到任意 Office Oracle 编译器**。

每组包含五项业务必改检查，另有一项独立重算可用性检查；三项跨文件一致性检查。保护检查覆盖公式、注入事实、SEC 数据及目标文本以外的 OOXML 内容/结构/格式。same_status 另外检查定性状态保持不变。

正确参考应 PASS；以下八类反例均应 FAIL：仅更新 Excel、破坏无关数据、用同值常量公式替代依赖公式、错误批准数值、保留旧解释、仅翻错方向、伪造公式缓存、提交原静态答案。详细失败项见逐例 JSON。

重算先在临时副本上清除公式缓存，再调用上游 LibreOffice 工具。直接格式转换可能保留伪造缓存，这一步由反例验证得到。提交文件本身不会被评分器修改。

## 已知局限和研究含义

- 只有一个上游任务、两个干预；尚无 PPT、复杂业务逻辑、多层语义推理或外部 Agent 实验。工程闭环不能证明论文新颖性或现有 Agent 的系统性缺陷。
- 依赖图由源码/公式/原文证据逐项确认并以脚本断言验证，没有声称人工专家已签字审定；正式数据发布前仍需人工审查。
- 当前传播是场景专用确定性函数，不能自动发现任意文件的隐含依赖。
- 文字 Oracle 是受控中文数字/方向/关键词检查，支持一定措辞变化，不是完整语义等价或矛盾检测；仍可能被构造性措辞欺骗。
- 格式保护采用严格包部件指纹，目标 XML 局部遮罩后比较；可能拒绝 Office/LibreOffice/openpyxl 常规另存产生的无害重序列化。它适合本轮严格编辑实验，正式 Agent benchmark 应迁移为更宽容的语义与结构保护检查。尚未做视觉渲染一致性评测。
- WTM 目标差异评分对本例的无关修改及同值常量公式也给 1.0，不能替代完整 Oracle。它依据差异选权重，本例自动落到 values_only，不能据此宣称公式保留已获 WTM 验证。
- EditCoherenceBench 已执行无 API 的生成器；未移植其评分代码、调用模型或复现论文结果。其 README 声称 MIT、pyproject 声称 Apache-2.0，当前 checkout 没有 LICENSE 文件；源码直接移植前需厘清。本轮只参考设计。
- SWE-Milestone 已检查代码边界，未运行 Docker/模型评测，也未移植其环境。
- 交接提及的 `CFBench_MVP_2026-11-18.zip`、`CFBench_public_flip.zip` 没有出现在当前提供的文件中；旧 MVP 的五项测试未复现。本目录是上游资产扩展，不冒充旧 MVP 的继续修改。
