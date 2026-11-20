# 任务：站点检验扩到 CMA-GD(MT) 全域（第 3 步：代码 + 报告，只做到分支，不碰线上）

派发：Claude 主管，2026-11-16。用户 11-15 已同意扩范围，方案见下面「已定的事」。全程用中文写报告和提交说明。

## 代码基线

本目录 `~/vt-cmagd` 是独立克隆（.git 在本目录内，可以正常提交），分支 `cmagd-20261116`，起点 `602f40e` =
Spark 正式站 2026-11-16 11:50 的完整快照（含正式站上还没提交 git 的改动：汇总提速 station_aggregate*.py、近 15 天、
配对只留 16 天、站表变动只重评有变化的配对、深圳站雨量修正）。**以这个快照为准，不要拿别的工作树的代码覆盖。**

## 已定的事（用户 11-15 同意，不要改）

1. 范围：内网数值预报综合分析平台 CMA-GD(MT) 的范围 **93.64～126.34°E、16.60～42.88°N**。范围内国家站 1917 个
   = 核心 260 站（粤 44、桂 45、琼 46、湘 43 四省，平台已在用）+ 外圈 1657 站（新加）。
2. **新站只评 4 个模式**：`ecmwf`（EC 0.25°）、`gfs`、`aifs`（ECAI）、`aigfs`（GFSAI）。这 4 个在 Spark 上的区域帧
   （gods-eye-weather/weather-data/region-v4）是 60～150°E、0～60°N，盖得住全域。EC 9 km（ec01，按站点向 Open-Meteo
   取点）、EC 集合平均、GEFS、WN3、WN3 订正等**不评新站**，以后裁剪范围改好再说。
3. 区域：原「南方地区」（id `all`）**保持原样**——原范围框、原站，结果不能变；**新加区域「CMA-GD 全域」**（id 建议 `cmagd`），
   包含范围内所有站（老站 + 新站）。其他区域（华南、湖南、大湾区、香港、深圳、新疆巴里坤）也都不能变。
4. 配对保留 16 天（已上线，常量 `PAIR_DAYS` 等在 station_common.py），本任务不改。

## 已查清的情况（主管查的，供参考，以代码为准）

- `station_common.BOX = (97, 18, 123, 33)` 不只管国家站：OGIMET、GHCNh、IEM 的选站也用它。**不能直接改 BOX**（会多出很多外部请求），要给国家站单独一个 CMA-GD 框。
- `nmic_obs.add_stations` 用 `in_box`（即 BOX）过滤，框外站跳过；遇到元数据里没有的站会直接报错，整轮失败。元数据
  `raw/nmic/A0012_stations_api.json` 有 2169 站，1917 站都在。
- **平台会把 `raw/nmic/*.txt` 里出现的站自动加进站表**（add_stations）。所以现在 Spark 上只放了核心 260 站的文件；
  外圈站文件只在笔记本 E 盘。
- 集合平均 ECENS/GEFS 的帧是 97～123/18～33，框外取样为 NaN、`pair_run` 用 `finite()` 跳过；WN3 裁到 35°N，框内的新站会被
  WN3、EC 0.1°、wn3ml 照评——这不符合第 2 条，所以**要给站加标记，评分和取样阶段对 4 个模式以外的模式直接跳过新站**（不要靠 NaN 跳）。
- 区域定义在 `station_api.py`：`OVERVIEW_REGIONS`、`region_stations()`、`REGION_BOUNDS`、`nmic_majority()`（国家站过半的区域，
  评分窗口截止到国家站资料最新时刻；`all` 被排除在这条规则外）。
- 站表常因 IEM 机场 METAR 时有时无而变；11-16 起站表变动只重评有变化的配对（`station_score.score_job` 的 full 分支逐行比较）。

## 要做的

1. **国家站范围**：国家站（nmic 来源）用单独的 CMA-GD 框选站；元数据缺的站跳过并记进诊断，不能让整轮失败。BOX 和其他来源的选站不变。
2. **新站标记**：确定「新站（扩展站）」的判定并写进站表，要确定、可重复。主管建议：**只有国家站来源、且省码不在 43/44/45/46 的站**
   算新站；站表里已有其他来源（OGIMET 等）的站，即使在外圈省份，也照旧评全部模式。如果你认为别的判定更稳，报告里说明理由。
3. **评分只评 4 个模式**：新站只对 ecmwf/gfs/aifs/aigfs 取样和配对，其他模式在取样前就跳过（省时间，也不出 NaN 配对）。
   EC 9 km 的按站取点（station_forecasts / nmic_ec01 等）**不能**因为新站多出任何 Open-Meteo 请求。
4. **外部请求不能增加**：OGIMET、GHCNh、IEM、Open-Meteo 及其他任何外部请求，加了新站前后的请求数要一样。报告里给出前后对比的数法和结果。
5. **区域**：加「CMA-GD 全域」（考核总览和详细分析的区域栏、站点分布图都要有）；`all` 和其他老区域排除新站，结果与现在逐值相同。
   「CMA-GD 全域」**只显示这 4 个模式**（其他模式在这个区域里不出现），页面上用一句完整的话说明「这个区域里新加的国家站只评 EC 0.25°、GFS、ECAI、GFSAI 四个模式」。
   评分窗口按现有 `nmic_majority` 规则走（报告里写明 CMA-GD 全域是否判为国家站过半、窗口截止到哪）。
6. **页面**：不加新控件，只在已有的区域栏里多一个选项；文字写全（不要「第1天」「08时」这种省略说法）；风格照现有页面。
   用户嫌平台复杂，别堆东西。
7. **资料**：笔记本 E 盘 `/mnt/e/sz-local-ai-data/nmic/SURF_CHN_MUL_HOR/*.zip`（只读）是全部国家站下载，里面有核心站的包也有外圈站的包，
   时段从 11-03 起，外圈站从 11-04 06 时（世界时）起。写一个小工具，把这些包整理成能放进 `raw/nmic/` 的 txt（核对表头、站号、时次、重复），
   并报告外圈站各时次的覆盖情况。**上线时由主管放文件**，你不要往 Spark 放任何东西。
8. **规模和耗时**：按 1917 站 + 现有站估算 16 天稳定后的配对数、库大小、常规一轮各阶段耗时，以及**加站后第一轮**（要给新站补齐 26 天内
   所有起报的 4 个模式配对）的耗时；检查汇总提速代码（station_aggregate*.py）在多一个区域、多 1657 站时会不会超内存或退回慢路径
   （`GROUP_LIMIT`、`AGGFAST_PARTITION` 等）。不达标的地方要改或给出办法。参考：11-15 上线后常规一轮约 26 分钟，2 亿条合成库常规轮约 9 分钟。
9. **等价核对**（最重要）：在你能拿到的数据上证明「老站、老区域」的配对和汇总与改动前逐值相同（计数逐项相等，连续量相对差 ≤1e-9），
   新站只多出 4 个模式的配对。另外**写好一条在 Spark 副本上跑真实规模对照的命令/脚本**（只读正式快照、输出到指定目录、不发布），
   主管第 4 步用它在 Spark 上计时和核对。可参考已有的 `tools/compare_station_runs.py`、`tools/compare_aggregates.py`、`tools/check_nmic_equivalence.py`。
10. **测试**：新功能加测试。已有测试：`test_stations.py` 现在就有 6 项失败（`test_explicit_proxy_does_not_inherit_metered_link`、
    `test_source_or_observation_change_alone_invalidates_run`、`test_assets_and_module_order`、`test_day_windows_follow_published_files`、
    `test_preview_refresh_cannot_rebuild_any_catalog`、`test_routes_only_read_persisted_results`，是之前改近 15 天等留下的，和本任务无关），
    不能新增失败；`test_nmic.py`、`test_region_hunan.py`、`test_station_return.py`、`test_aggregate_fast.py`、`test_rescore_diff.py` 要全过。
    测试用 `python -m unittest`（Spark 正式环境没有 pytest）。

## 能用的数据和环境

- Spark 只读：`ssh -o ProxyCommand=none spark`（直连地址从略）。正式目录 `~/verification-platform`：代码
  `verification-test/`，缓存 `cache/stations/`（stations.json、obs/*.jsonl.gz、raw/nmic/、snapshots/、pairs.sqlite 等），预报帧
  `gods-eye-weather/weather-data/region-v4/`，Python 环境 `venv/`，环境变量 `deploy/env`。
  **只许读**：`cat`、`ls`、`scp` 拉到本机、`sqlite3 -readonly`、只读打开的 Python。**不许**在 Spark 上写文件、改代码、起停服务、跑评分。
- 大文件放 `/mnt/e/vt-cmagd-scratch/`（已建，E 盘剩约 87 GB；别整个拷 pairs.sqlite，约 27 GB，确实需要先在报告里说明）。
  上次提速留下的 `/mnt/e/vt-aggfast-scratch/`（9.3 GB）可以读，别删。
- 笔记本 HOME 和 /tmp 都在 C 盘的虚拟盘上，**不要往 /tmp 放大文件**；SQLite 临时文件设 `TMPDIR`、`SQLITE_TMPDIR` 到 E 盘目录。
- 本机 Python：`~/.venvs/ec-gfs-server/bin/python` 或系统 python3；缺包就在报告里说，**不要装包**。

## 交付

- 在本分支上分几次中文提交（实现、测试、工具、报告），最后一次提交包含 `REPORT_CMAGD.md` 和空文件 `DONE_CMAGD`。
- `REPORT_CMAGD.md`（中文）：改了哪些文件、为什么；新站判定；区域和页面的变化（附截图或页面文字）；外部请求前后对比；规模和耗时估算；
  等价核对的做法和结果；Spark 真实规模对照命令；上线步骤和回退步骤（要放哪些文件、重启哪些服务、第一轮大约多久）；没做完或有风险的地方。
- 不部署、不往 Spark 写、不发企业微信、不打印任何账号密码或密钥。
- Codex 额度有限：别用长时间轮询循环；跑长任务用 `setsid nohup … &` 放后台、隔一段时间看日志。额度快用完时先提交已完成的部分，
  并在 `PROGRESS_CMAGD.md` 写清做到哪、下一步是什么。
