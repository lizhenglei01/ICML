# 酒店 Office 修改试验

在 `ICM2027/` 下执行以下命令。预检验证原生参考文件和执行环境，正式运行还要求四种证据布局的构建报告全部通过。

```bash
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_agent --preflight-only
python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_agent
```

每种布局在独立会话中运行一次。模型工作区包含三个原生 Office 文件的可写副本和自然语言任务。视图、节点关系、来源映射、参考文件和 Oracle 由主进程保管。Bubblewrap 隔离项目目录，Codex 工具沙盒允许在工作区写文件。预检会验证私有目录不可见和原生文件库可用。

结果默认保存到 `business_artifacts/hotel_evidence/agent_runs/<timestamp>/`。`plan.json` 记录模型、超时、资产与评分规则哈希。每个布局保存 Agent 提交的原生文件、事件日志、修改文件清单、耗时与评分。`results.json` 汇总有效得分和基础设施异常。默认每个布局最多 300 秒。

同一场景每种布局只测一次，结果反映本轮试验的原生编辑表现。`native_pass` 由主进程对照文件对象和证据值评分。基础设施错误有单独状态，完成的会话则保留实际原生评分。
