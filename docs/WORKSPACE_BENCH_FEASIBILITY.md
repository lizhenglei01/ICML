# Workspace-Bench Office 资产可行性核查

2026-11-25 · 核查编号 `workspace_bench_range_20261125`

**样本中已经有可执行、可做功能回归测试的复杂 Excel。** Lite 37 的 KPI 工作簿和 Lite 38 的员工信息系统均通过了实际输入干预。此次核查未发现一组可以直接作为持续演化基座的 Excel、Word、PPT 联动系统。抽到的 Word、PPT 有业务内容，部分 Word 图表有自己的内嵌数据。人员审批历史、跨文件自动同步和长期状态迁移仍未验证。

## 取样与传输

- 英文压缩包大小为 18,721,947,868 字节。首次请求 `Range: bytes=0-1023`，Hugging Face 重定向的 CDN 返回 `206 Partial Content`、`Accept-Ranges: bytes` 和 `Content-Range: bytes 0-1023/18721947868`。返回 1,024 字节，ZIP 文件签名正确。后续使用严格校验 `206`、范围和长度的按需读取器，未启用整包回退。
- 从 ZIP64 末尾定位中央目录，读取到 27,703 个 ZIP 条目，含目录。中央目录大小为 5,017,719 字节。累计 79 次成功的 Range 响应，ZIP 响应正文实际合计 **17,708,149 字节**，约 16.89 MiB、原包的 0.0946%。其中中央目录被三个独立操作各读取一次。
- 另下载 Lite 英文 CSV 和数据集 README，共 540,618 字节。上述可核算响应正文合计 **18,248,767 字节**。该数字不含目录 API 元数据、网页查阅和 HTTP 请求或响应头。22 个解压成员共 3,265,471 字节，解析出 17 份 XLSX、3 份 DOCX、1 份 PPTX，另 1 个标称 XLSX 实为主题包。仅抽查英文包，未下载整包、启动 Docker 或调用 Agent 模型。
- 按 Lite CSV 的 `absolute_id` 定位，完整抽取了任务 15、37、38 共 10 份资产。这三项清单全部是 Excel。补样为 HR 薪酬 329、生日策划 340、固定资产 346 和员工档案邻近目录。生日任务另有两份体积较大的附件未提取。这是目的性取样，不能用于估算全库成熟资产的比例。

[工作区数据集](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Workspaces) · [Lite 英文任务元数据](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/main/task_lite_clean_en_metadata_table.csv)

## Excel 逐文件结果

跨表列统计识别出的显式跨表 A1 公式单元格。深度为显式公式依赖图上的最长链，按经过的公式单元格数计算。结构化表、命名范围、`INDIRECT` 等动态引用未完全展开，因此这个深度是对可识别部分的统计。图表和数据透视表按原生 XML 部件计数。17 份有效工作簿中，未识别到跨工作簿公式或原生透视表。一个固定资产工作簿含外部链接部件，目标名为「会员卡管理信息表1」，未发现公式使用该链接。

| 来源 | XLSX 文件名 | 工作表 | 公式 | 显式跨表 | 深度 | 原生图表 | 透视表 |
|---|---|---:|---:|---:|---:|---:|---:|
| 15 财务 | `4-financial-table-income-expense-dashboard.xlsx` | 3 | 190 | 0 | 42 | 6 | 0 |
| 15 财务 | `6-financial-table-income-and-expense-data-visual-charts.xlsx` | 1 | 45 | 0 | 3 | 7 | 0 |
| 15 财务 | `5-financial-table-income-expense-profit-statement_visual-charts_.xlsx` | 2 | 76 | 0 | 3 | 0 | 0 |
| 37 HR | `5-company-personnel-structure-visual-dashboard-1.xlsx` | 2 | 18 | 0 | 2 | 6 | 0 |
| 37 HR | `6-personnel-mobility-analysis-chart-visual-charts.xlsx` | 3 | 38,723 | 11 | 5 | 3 | 0 |
| 37 HR | `1-human-resources-decision-analysis-dashboard-1.xlsx` | 2 | 0 | 0 | 0 | 6 | 0 |
| 37 HR | `2-KPIkpi-analysis-performance-evaluation-visual-dashboard.xlsx` | 4 | 2,687 | 1,207 | 7 | 10 | 0 |
| 38 HR | `15-annual-onboarding-and-offboarding-statistics.xlsx` | 3 | 13 | 0 | 2 | 4 | 0 |
| 38 HR | `12-human-resources-employee-information-management-system.xlsx` | 14 | 8,947 | 1,556 | 14 | 34 | 0 |
| 38 HR | `10-recruitment-employee-statistics-analysis-table.xlsx` | 1 | 7 | 0 | 1 | 3 | 0 |
| 329 薪酬 | `1-company_annual_human-resources-compensation-analysis-table.xlsx` | 2 | 44 | 0 | 3 | 8 | 0 |
| 329 薪酬 | `3-company_monthly_-analysischarts.xlsx` | 2 | 20 | 0 | 2 | 6 | 0 |
| 329 薪酬 | `4-company_monthly_-analysis.xlsx` | 2 | 20 | 0 | 2 | 6 | 0 |
| 346 运营 | `1-administrative-fixed-assets-management-table.xlsx` | 1 | 62 | 0 | 2 | 2 | 0 |
| 346 运营 | `fixed-assets-ledger_filtered-summary_.xlsx` | 1 | 14,001 | 0 | 6 | 0 | 0 |
| 346 运营 | `register_-_.xlsx` | 1 | 36 | 0 | 2 | 0 | 0 |
| 340 HR | `7-birthday-event-planning-proposal.xlsx` | 3 | 2 | 0 | 1 | 0 | 0 |

## 实际公式与重算

用仓库已有的 LibreOffice Calc 隔离重算工具在临时副本中操作，禁用网络。预期值由公式业务逻辑和原始输入独立计算。以下通过仅涵盖实际检查的路径。

| 样本 | 可核实功能和干预 | 实测 |
|---|---|---|
| Lite 37 KPI | 目标和权重从 `KPIZhiBiaoJiWeightSheJiBiao!F5/G5` 进入 `YuanGongYeJi!M5/N5`。该表 `P5` 用 `=E5/$M$5*100*0.7*$N$5` 计算一项 KPI 得分，汇总表 `'Kao He Hui Zong Biao'!C4` 读取它。仅将 `YuanGongYeJi!E5` 从 1,800 改为 2,000。 | `P5` 和 `C4` 都从 **20.16 → 22.4**，与预期一致。 |
| Lite 38 员工系统 | `WorkforceStructureAnalysis!I9` 用 `=COUNTIF(Table1[Education],H9)` 统计学历类别。只改 `ActiveEmployees!K6` 的学历类别；按 Excel 表覆盖范围独立计数。 | `I9` 从 **44 → 43**，与预期一致。这条结构化跨表边不在显式跨表列的 1,556 个计数内。 |
| Lite 15 财务仪表板 | `'Income Expense Dashboard'!K7` 为 `=SUMIF(Income Details!$B$5:$B$20000,J7,Income Details!$E$5:$E$20000)`，带空格的表名未加引号。全书 108 个公式文本含 `!`。 | 原文件该格无缓存，Calc 重算为 **#VALUE!**。 |
| Lite 15 财务损益表 | `Sheet1!C5` 为 `=IncomeExpense Details!K19`，实际工作表名是 `Income Expense Details`。全书 4 个公式文本含 `!`。 | 原文件该格无缓存，Calc 重算为 **#VALUE!**。 |

- Lite 38 还包含独立的员工档案查询、组织结构分析、人力成本查询、月度异动和提醒。例子包括 `EmployeeRecordQuery!C9` 的 `VLOOKUP`、`Home!Q10` 的 `HLOOKUP` 以及 `ActiveEmployees!AA6` 的合同有效期提醒。部分公式使用 `INDIRECT` 等难以静态展开的函数，日期提醒的回归测试需要固定基准日期。尚未重算验证这些分支。
- 同属 Lite 37 的人员流动表，`Sheet1!N11` 读取 `EmployeeInfoRegister!Z5`，后者用 `X5/W5` 计算比例，`W5` 使用 `COUNTIF`，`Y5` 使用 `COUNTIFS`。38,723 个公式中绝大部分来自同一登记表的重复填充。另外，`1-human-resources-decision-analysis-dashboard-1.xlsx` 虽有 6 个原生图表，整份文件没有公式。
- 固定资产台账的 `'模板页'!X6` 为 `=SUBTOTAL(109,P9:P20000)`，同表还有按日期计算的月折旧、累计折旧和净值。另一本固定资产管理表的 `J14` 使用 `RANDBETWEEN`。财务仪表板的 42 层主要来自日期逐格推进。公式总数、图表数量和最长链，需要结合具体业务行为来解释。

## Word、PPT 与工作簿关系

| 文件 | 实际核查 |
|---|---|
| 329 `5-analysis-report.docx` | 87 个正文段落，4 个原生图表和 4 个包内 XLSX，分别提供薪酬构成、职级薪酬缺口与离职率、薪酬区间分布和目标指标数据。相邻独立工作簿主要是按部门统计人数、总薪酬和人均工资。可确认主题相关及 Word 图表使用包内数据，未找到对这些外部 XLSX 的链接，也未对齐同一业务记录的数值。 |
| 346 `fixed-assets-scrap-and-damage-application-approval-form.docx` | 表单要求提供编号、原值、净值、已提折旧及审批签章。可与固定资产台账的 `K8/P8/Y8/X8` 列按资产编号对应业务字段。唯一外部关系指向本地 WPS Word 模板。未验证有已填记录能和台账逐行对齐，未发现 Excel 绑定。 |
| 340 `2-employee-birthday-greeting-card.docx` | 有 Word 模板关联，未发现对生日策划 XLSX 的链接。 |
| 员工档案目录 `2024_employee_turnover_rate_analysis_report.pptx` | 15 页，4 个原生表格、0 张原生图表，无外部关系或嵌入工作簿。同目录 `employee_separation_archive_record_2024.xlsx` 的包入口是 `theme/theme/themeManager.xml`，没有工作簿，无法核对报告与之逐条一致。 |

## 选材建议

1. **Lite 37 KPI 工作簿，最适合先做。** 已验证从绩效输入到明细评分和汇总的链条。可以在同一工作簿上依次扩展不同岗位权重、按政策生效期汇总，再用旧权重和输入重测旧评分路径。其他仪表板可作业务背景。整组自动联动未获证实。
2. **Lite 38 员工信息系统，功能面最广。** 可沿员工表、查询、组织结构统计、合同提醒的共用数据结构设计递进修改。用新员工、离职、合同日期边界测试新增行为，同时重测历史档案查询、现有统计和提醒。当前只有学历统计通过干预，其他分支以及动态公式兼容性仍待重算。
3. **346 固定资产台账与审批表，适合跨格式试点。** 台账有可重算的价值和折旧数据，Word 表单提供业务字段和审批位置。可在既有台账上扩展报废申请、入账与已签发记录保护。试点需要建立按资产编号取数和回写历史的流程，现有文件未证明已有这些功能。

实际原件、成员路径、提取日志、解析统计和重算明细保存在本机忽略目录 `artifacts_research_archive/workspace_bench_range_20261125/`，可按 `SHA256SUMS` 核对。没有对未抽样的成员、完整的 Excel/WPS 兼容性或跨文件自动化作结论。
