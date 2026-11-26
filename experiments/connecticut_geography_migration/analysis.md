# 康涅狄格州地区编码迁移核查

**结论。** CHR&R 的 Excel 和官网健康快照在 2025 发布版同时提供规划区人口和旧县失业率。居住人口已经迁移，Fairfield 旧县失业率仍是当期数据。ArcGIS 县级要素在 2025 只收录九个规划区。发布方后来公开的简版 RShiny 应用另有一个可定位的断点：数据文件含旧县当期数据，选区目录却从入口排除了旧县。

## 实际查询

| 发布版与对象 | CHR&R Excel | CHR&R 官网快照 | 原始 ArcGIS 县级图层查询 |
|---|---|---|---|
| 2024 Fairfield `09001` | `Select Measure Data!FZ4` 失业率 4.1409957347%，`Additional Measure Data!LK4` 人口 959,768 | 显示 4.1% 和 959,768 | 两项均返回 |
| 2024 Greater Bridgeport `09120` | 地区列表未收录 | 2024 API 无该区快照数据 | 无要素 |
| 2025 Fairfield `09001` | `Select Measure Data!FX4` 失业率 3.8658799335%，`Additional Measure Data!OE4` 人口空白 | 显示 3.9%，当前人口面板不显示 | 无要素 |
| 2025 Greater Bridgeport `09120` | `Additional Measure Data!OE13` 人口 327,651，`Select Measure Data!FX13` 失业率空白 | 人口面板显示 327,651，失业率本地值空白 | 人口 327,651，失业率字段为 null |

Excel 源与年份页表明，2024 发布版使用 2022 年人口与失业率，2025 发布版使用 2023 年数据。2025 Fairfield 的失业人数为 18,535，劳动力人数为 479,451，按原始 API 的分子分母重算得到约 3.86588%。以下同时是核查源和第三方基准。 [2024 工作簿](https://www.countyhealthrankings.org/sites/default/files/media/document/2024%20County%20Health%20Rankings%20Connecticut%20Data%20-%20v2.xlsx) · [2025 工作簿](https://www.countyhealthrankings.org/sites/default/files/media/document/2025%20County%20Health%20Rankings%20Connecticut%20Data%20-%20v3.xlsx) · [康州卫生部门 2023 年人口估算](https://portal.ct.gov/dph/-/media/departments-and-agencies/dph/population/certified-town-pop/pop_towns2023pdf.pdf?hash=A6D920391624A5FDD31F66F38C284E09&rev=744c08b66df343248b7f309a16a226b5) · [FRED 转载的 BLS 年度失业率](https://fred.stlouisfed.org/series/LAUCN090010000000003A)

## 可重放的取数链

**官网快照。** 原网页容器向 CHR&R 的浏览器应用传入 `data-state-fips="09"` 与 `data-county-fips="001"` 或 `"120"`。浏览器请求 `/measures/{发布年}/09/{县代码}` 获取指标定义，并请求 `/county/{发布年}/09/{地区名}` 获取该快照及地区目录。2025 官网目录为 Fairfield 保留 2011–2025 年，为 Greater Bridgeport 从 2025 年开始收录。Playwright 捕获两条 2025 查询和一条 2024 查询，随后从同一批响应离线执行原网页脚本，再验收实际表格和人口面板。此处的 2024 是当前官网提供的历史发布数据，页面脚本为核查时官网下发的版本。 [发布方双轨说明](https://www.countyhealthrankings.org/health-data/how-to-use-your-county-health-snapshot/changes-to-county-equivalents-for-connecticut) · [Fairfield 2025 快照 API](https://api.countyhealthrankings.org/county/2025/09/fairfield) · [Greater Bridgeport 2025 快照 API](https://api.countyhealthrankings.org/county/2025/09/greater-bridgeport)

**ArcGIS。** 原始项目的第 2 层用 `fipscode` 标识地区，`v023_rawvalue` 和 `v051_rawvalue` 分别为失业率和人口。两版已发布的弹窗配置都绑定这两个字段，失业率保留一位小数，人口保留整数。对 CHR&R 原图层与接管后 Esri 图层执行同一查询，核对了选定字段的全部康州要素。2024 有八个旧县，2025 有九个规划区，九个规划区的失业率都为空。这个差异已经发生在要素层，调整弹窗无法取到缺失的 Fairfield 要素。报告 StoryMap 的可读配置指向教育与住房等专题图层，未提供人口与失业率这两项的报表验收口径。现有证据支持 ArcGIS 与官网的覆盖不同，发布方没有在所查材料中承诺 ArcGIS 与官网采用相同覆盖。 [2024 原始 ArcGIS 项目](https://www.arcgis.com/home/item.html?id=76246cfea2f346b99f39c038d39967a8) · [2025 原始 ArcGIS 项目](https://www.arcgis.com/home/item.html?id=227b2537c79e42f69a31d83e8e0d9322) · [2025 报告地图](https://www.arcgis.com/home/item.html?id=503deb5b7ce94ff0a25519da16ac3559)

**公开简版应用。** 在固定的 RShiny 提交 `3c456c6` 中，`app.R:178–179` 读取 `data/county_fips.rds`，`app.R:391–408` 用它生成地区下拉框，`app.R:545–587` 先查目录、再按代码读年度 Parquet。目录中的康州选项只有九个规划区。用 DuckDB 执行源码中的 SQL，直接传入 `09/001` 可读到 2024 的 85 个指标行和 2025 的 90 个指标行。2025 失业率原始比例约为 0.038658799335。主动绕过目录的这次查询定位了断点，实际界面按照源码的目录判断无法进入 Fairfield 的查询。这里重放了目录和 SQL，未运行整个 Shiny 服务。 [公开应用源码](https://github.com/countyhealthrankings/snapshot/blob/3c456c67fd34dbe49ebb270b25bc842a7cbd4c06/app.R#L391-L408) · [县级取数逻辑](https://github.com/countyhealthrankings/snapshot/blob/3c456c67fd34dbe49ebb270b25bc842a7cbd4c06/app.R#L545-L592)

## 证据范围与模型试验条件

- 已取得可离线运行的官网浏览器脚本及响应、两版 GIS 弹窗配置和原始查询结果、公开简版的查询源码和同一提交的输入数据。
- 两份发布工作簿是静态导出，核查时未见公式、外部链接或数据连接。官网后台、Excel 导出和 ArcGIS 要素发布的完整生产流程及迁移提交仍未找到。公开的失业率计算笔记描述了如何保留旧县并标记新区无值，但它是后续公开的计算资产。 [公开计算归档](https://github.com/countyhealthrankings/county_health_measure_calculations) · [失业率笔记](https://github.com/countyhealthrankings/county_health_measure_calculations/blob/d39acde42804c272ebbcff4946f7865b933b8084/calculations/v023_unemployment.Rmd)
- 验收应分别检查 2024 旧县历史查询、2025 旧县当期失业率、2025 规划区人口，以及错误地区下指标能否继续保持缺失。如果将简版地区目录或地图扩展作为维护任务，需要明确这是基于真实资产的实验改造。本轮只重放公开行为，未启动模型。

## 复验

本地归档标识为 `chr_connecticut_geography_migration_20261126`。需要 Python、OpenPyXL、Playwright、Chrome、DuckDB 和 PyReadR。将归档根作为下列命令的输入即可离线重放：

```bash
python experiments/connecticut_geography_migration/replay.py \
  artifacts_research_archive/chr_connecticut_geography_migration_20261126
```

脚本的 `--capture` 用于在包含原始 Excel、API、GIS 和简版输入的独立工作目录中重新记录官网响应。原始文件和请求记录保存在本地证据归档中。
