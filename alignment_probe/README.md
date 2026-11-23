# OBM-036 文字与表格证据定位小实验

输入来自 OfficeEditBench 原版参考资产，不修改上游。人工选定 Review 表和 Word
第 19 段（从零计数）；模型不知道 gold 中的单元格绑定。第二句话仅删除原句中的
百分数，用于区分数字匹配和定性表述定位，不冒充原始资产。

在工作区根目录运行准备和公式追踪：

```bash
PYTHONDONTWRITEBYTECODE=1 python ICM2027/alignment_probe/probe.py --prepare-only
```

配置 `OPENAI_API_KEY`，可选 `OPENAI_BASE_URL`，然后实际调用模型：

```bash
PYTHONDONTWRITEBYTECODE=1 python ICM2027/alignment_probe/probe.py --model YOUR_MODEL
```

兼容 Chat Completions 的接口加 `--wire-api chat`。不自动使用 Codex 会话凭据。
如需保留多轮实验，使用不同的 `--out` 目录。

也可显式使用已有 Codex Relay 和 Codex 当前配置的模型、推理强度：

```bash
PYTHONDONTWRITEBYTECODE=1 python ICM2027/alignment_probe/probe.py --backend codex-relay --out ICM2027/alignment_probe/results_relay
```

这个入口复用 `OSWorld-V2/experiments/longhorizon_osworld/codex_relay_backend.py`，
不修改该文件。每个样例新建进程、临时会话和空目录，关闭宿主工具、Apps 和网页搜索；
只允许用户消息、推理和最终回答事件，出现工具事件则拒绝该次结果。原 Relay 的 GUI
角色指令被替换为 SciTabAlign 的 system 提示，实际 thread/turn 请求及事件类型保存在
`relay/*/transport_audit.json`，可检查模型名、会话独立性及是否加载额外指令文件。
若 Codex 已选官方 ChatGPT 登录，仅在子进程移除会覆盖该登录的 `CODEX_API_KEY`。
这是 Codex Relay 适配实验，不宣称与论文原模型或裸 API 完全等价。

```bash
cd ICM2027
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -o addopts= -p no:cacheprovider alignment_probe/test_probe.py alignment_probe/test_codex_transport.py
```

实际复用：执行 SciTabAlign 原版 `build_zero_shot_prompt` 的函数 AST、
`utils.format_caption_and_table`、`utils.extract_answer`；调用 linexcel `analyze`。
原提示词文件在导入时会无条件打开未提供的 few-shot 数据，因此只加载原版零样本
函数，不伪造示例数据。Office 读取、坐标转换、API 调用、独立断言和评分为新增代码。

`results/public/requests.json` 是完整模型输入；`private/` 保存独立答案、坐标映射、
公式图和逐项检查；`responses/` 保存真实模型原文、模型名和用量；`summary.json`
记录结果。准备成功或单元测试成功均不代表模型定位成功。
`PASS_WITH_EXTRA_EVIDENCE` 表示覆盖正确证据但未达到已登记的最小数值证据集合；
本例没有无关数值干扰项，不能仅凭覆盖通过声称能精确筛选关联。

这个例子有明确文档提示；没有验证跨文件检索、完整隐含规则恢复、图片图表定位，
也没有开展人工耗时对照。依赖 openpyxl、python-docx、openai 和 uv；公式引擎在
uv 临时环境运行，固定 formualizer 0.14.0，不修改上游依赖锁。

## 改数字后是否自动改文字

`counterfactual_compare.py` 在原版资产副本中修改模拟上期数，调用上游
`patch_office.patch_xlsx` 和 `verify_task.office_recalc`（LibreOffice），并以独立
Fraction 运算核对结果。给模型的是更新后的 Review 表和 Word 全部 55 段，不给
应改段落编号、预期同比或方向。模型返回段落替换提案，程序通过上游
`patch_office.patch_docx` 写回原版 Word 的副本。

```bash
PYTHONDONTWRITEBYTECODE=1 python ICM2027/alignment_probe/counterfactual_compare.py --prior 2500 --backends codex-relay --out ICM2027/alignment_probe/new_edit_run
```

用 `--prior 2350` 测试数值变化但增长判断仍成立的情况。可同时指定
`--backends codex-relay third-party-api --api-base-url YOUR_URL --api-model YOUR_MODEL`，
通过 `OPENAI_API_KEY` 或本机 `--api-key-file` 指定密钥。不要把密钥写进代码或命令行。
两条通道收到相同的 system/user 输入；模型不同，因此耗时不是对传输开销的独立测量。
单次延迟比较不能推出稳定速度排名。Responses 请求明确设置 `store=False`。

实际模型回答、逐段差异、检查结果与推理配置分别保存在各后端子目录。公式计算、
全文读取和写回是程序执行，文字修改由模型提出。这是模型文本修改实验，未测试
Agent 自己打开 Office 或从任意文件中恢复全部隐含规则。工作簿只注入 Review 的
数值并重算；Approval 文字说明的完整更新不属于这个文本传播实验的评分范围。

本机 openai 3.8 的 httpx2/Brotli 解压有兼容问题，第三方请求使用
`Accept-Encoding: identity` 绕过压缩，不修改共享依赖。初次连接/余额失败保留原日志，
不把失败请求的耗时当作模型速度。所有实验目录分开保存。
