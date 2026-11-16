# 规范评分交付报告

日期：2026-11-09。范围为本仓库代码、离线测试和预览说明；未 SSH、未访问生产缓存、未操作 systemd，也未访问外网（仅按任务许可通过指定代理安装 jsdom）。生产数据的数值核对和页面预览仍须由 Claude 在 Spark 执行，确切命令见 [GUIFAN_PREVIEW.md](GUIFAN_PREVIEW.md)。

## 实现与适用范围

- `guifan_scores.py` 集中定义附件7的可加计数和分级边界。气温存 N1/N2，比较使用 `k+1e-6`；前端以 ΣN1/Σn、ΣN2/Σn 计算 ACC1/ACC2。只对站点 t、hk/Open-Meteo 2米气温开放，未给露点、日最高/最低气温增加这些指标。
- 晴雨保留原 0.1 mm H/M/F/n，增加 T。PC=(n−M−F+T)/n。站点微量依据 trace 且 ob<0.1；格点微量依据 0<ob<0.1。微量修正只用于 PC，既有累加阈值和分级计数都不受影响。
- 3小时边界为 0.1/3/10/20/50/70；24小时为 0.1/10/25/50/100/250，左闭右开，实数不先四舍五入。因此 2.95 是3小时小雨，9.99 是24小时小雨，250 是特大暴雨。
- 分级结果单独成行，`grade=1..6`、`threshold=null`，H=A、F=B、M=C、n=全部配对数。每对最多记一次，空报落预报级，漏报落实况级；grade 纳入 core 分组、catalog 去重、共同时间键和 Spark 冻结历史 overlay 键。分级按规范公式提供 ETS（随机命中 R=(A+B)(A+C)/N，N 为全部配对数；审核时按用户要求补上）；performance 用相同分级 A/B/C 计算 POD、SR、TS、BIAS，可以正常使用。
- hk 1小时阈值不变；3小时增加70。站点 rain24 增加250。Open-Meteo 新行按1/3/6/12/24小时分别取任务规定档位。对旧 memo 已发表的额外阈值取并集继续计算，保证原来的24小时1/3/20/30等行不会因为调整档位消失；元数据给出实际并集。
- 三个生产者及站点 scores/station-scores、detail_stats、grid-rows、grid-map.sqlite、区域/时次地图、原始单站配对、单站卡都接入新计数。前端 app/core/stations/grid-stations 增加能力控制、规范说明、准确率百分数、分级切换、PC固定0.1阈值和禁用控件。地图准确率用“越深越好”的蓝色梯度。
- 结果表和既有 CSV 导出包含新指标；CSV 百分率列标题注明 (%)，值为百分数。分级导出 grade；图表、单站卡、地图用同一计数口径。独立站点页原来没有导出按钮，本次未另造导出系统。
- lead/weekly 不声明新能力，独立通道不出现新指标；从新指标/分级切过去会退回默认指标并提示“该数据源暂不提供规范准确率/分级检验”。混合统一通道取可用来源能力，但新指标只使用确有计数的行。没有更改 case.js、monthly.js、demo-models.js 或外部旧生成程序。

## 表3-1与任务中核对等式的冲突

任务要求每对只记一次，同时要求 Σ(A+C)=实况≥0.1、Σ(A+B)=预报≥0.1；这两个条件一般不能同时成立。例如实况小雨、预报中雨只记 B₂，但实况有雨数为1，Σ(A+C)为0。

实现严格遵守任务列出的表3-1逐格规则。为核对增加两个独立计数（只放对应0.1累加行，站点 daily 也保存）：

- GO = #(f>o≥1)，即跨级报大且双方都有雨；GU = #(o>f≥1)，即跨级报小且双方都有雨。
- 正确恒等式为 `Σ(A+C)+GO=H₀.₁+M₀.₁`、`Σ(A+B)+GU=H₀.₁+F₀.₁`。
- `GO+GU≤H₀.₁`，所有级别的 A/B/C 总和≤n；最高级的 A/B/C 分别等于最高阈值累加 H/F/M。

只读核对工具检查这些关系，不把正确表3-1实现误判为失败。中间阈值的完整累加 H/M/F 一般不能仅由每级 A/B/C 唯一恢复，因此没有编造中间档位等式。

## trace调查和迁移

`pairs` 原本就有 trace，`pair_run` 已写入，日 CSV 也有该字段，无需改 pairs 表结构。实际代码的 SYNOP RRR=990 和 GHCNh 的 measurement='T' 会产生微量标志；本仓库 IEM 只读气温、露点、风，未采集 IEM 降水，不存在可直接补入的 IEM 降水 T 列，不能虚构该数据源。

`station_obs.derive_rain12` 确实在把连续两个6小时量相加，原来 `trace and trace` 错误。已改为“合计<0.1，且任一子段 trace”；若合计达到0.1则不再是微量。没有改变累加降水量。

`tools/migrate_guifan.py` 是专用的一次性离线迁移：

1. 获取原 refresh.lock，读取持久 pairs 和 obs 档案，用原 QC/来源优先选择逻辑重建降水 trace。只修复站点/时间/变量/来源/观测值完全匹配的既有 pair，不改 fc/ob，也不重新插值。
2. trace修复、dirty_days和独立 score_migrations 标记同一事务提交，失败回滚。既有观测档案不在的配对保留已存 trace。
3. 从全量保存的 pairs 重建所有窗口、detail、grid rows/map及元数据，在 `.incomplete-*` 构建；导出日 CSV 后 fsync、rename、原子替换 current，再清 dirty_days；保持两代完整快照。中断后重跑即可。

日常全量评分也会调用一次 trace修复并生成全部新计数。新增 `score_revision=guifan-2017-v1`，刻意保留用于 forecast pairing/精度证书的旧 VERSION，避免为了增加计数而强制重采样历史 fc/ob。评分快照每次本就全量重建，因此新计数不会误用旧汇总缓存。

native/Open-Meteo 把规范 revision 加入源签名，源文件仍在时重算；旧 memo 不因顶层版本不符整批删除。源文件或观测已清理的、仍在保留窗口内的历史评分原样保留，没有伪造新计数。Open-Meteo 旧 revision catalog 也可继续读取。

## 混合历史与路径

新指标先筛选有对应 N1/N2/T 的行，再求共同有效时次交集；grade只使用 grade 行。计数缺失不补零；一个有效新计数也没有时显示 — 及原因。说明区给出当前筛选/共同时间实际保留行的起止时间。旧指标继续用原算法和原始样本；选择新指标时，表内其他指标随该新指标的有效样本范围展示。

- 站点所有新产物仍遵守 STATIONS_CACHE_DIR。
- native 增加 NATIVE_SCORES_PATH（默认本仓库 native-scores.json）；snapshot/lock与它同目录，NATIVE_AUX_ROOT控制只读backfill根，默认本仓库；VERIFICATION_APP_ROOT、NATIVE_WEATHER_ROOT控制输入。
- Open-Meteo增加 OPENMETEO_SCORE_ROOT（默认原 ec.ROOT），只改变 score-memo/verification-catalog 写入位置；OPENMETEO_EC_ROOT仍是已有 forecast 缓存根。VERIFICATION_AUX_ROOT为只读obs-backfill/analysis参考根，VERIFICATION_APP_ROOT为主平台输入根。
- GUIFAN_SCORE_END可固定两个格点评分的UTC终点，便于受控核对。
- 预览 server 用 VERIFICATION_READ_ONLY=1 和独立 VERIFICATION_PREVIEW_DATA_ROOT，并清除继承的 VERIFICATION_FROZEN_CATALOG，防止走生产刷新分支。额外提供 tools/merge_guifan_catalog.py，保留旧lead/weekly/分析历史并叠加新格点产物。

`openmeteo_verification.py` 在原仓库是指向 Spark 绝对路径的 symlink，本机已断开。本次读取 WSL `/home/chengyang/openmeteo-ecmwf/openmeteo_verification.py`，将评分实现纳入本仓库；两个数据读取 adapter 保持原 symlink。未修改外部项目。Spark 上需要检查原评分实现与此版本的差异，且既有调度必须调用本仓库 scorer 才会持续产出新指标。

## 验证结果

环境：`/home/chengyang/.venvs/ec-gfs-server/bin/python`、Node v25.8.1、jsdom安装在任务指定的 `/tmp/guifan-jsdom`（npm cache也放/tmp）。先跑基线，再改代码；Python无`python`命令，采用项目已有venv。缺jsdom时的首次前端运行另有日志，比较采用补装jsdom后、前端修改前的基线。

| 测试 | 基线 | 最终 | 新增失败 |
|---|---:|---:|---:|
| Python顶层discover | 182项，168通过、4失败、10错误 | 195项，181通过、4失败、10错误 | 0 |
| Node全部 test_*.js | 9文件，7通过、2失败 | 10文件，8通过、2失败 | 0 |
| incremental-qc独立discover（未修改代码） | 补充检查 | 6项通过 | 0 |

失败测试ID已按集合精确比较，机器可读记录见 [tools/guifan_test_results.json](tools/guifan_test_results.json)。基线完整清单：

- `test_module_demo.Metadata`：test_month_integrity_and_private_provenance、test_month_scientific_counts_from_same_run_hourly_fields、test_month_time_identity_and_missing_days，缺 cache/wn3-module-demo/2026-10.json。
- `test_module_demo.HTTP.test_routes_and_static_assets`：缺生产预览资料，HTTP503。
- `test_wn3_demo.HTTPDemo.test_all_demo_routes_and_formal_routes_remain_pending`，以及 `RealSample` 的 test_all_grid_interfaces_preserve_wn3_identity_and_original_values、test_corrupt_sample_rejected_and_missing_sample_graceful、test_no_fabricated_minute_fields_different_batch_or_window、test_real_source_and_same_run_trailing_rain_sum、test_scores_match_real_paired_arrays：缺 cache/model-interface-demo/current.json。
- `test_station_xinjiang.WN3Speed.test_download_and_scoring_agree_on_the_speed_field`：既有 fixture 缺 surface_ws。
- `test_station_basemap.StationBasemapTests.test_full_map_coverage_and_real_linework`：旧断言北界27，与当前33不符。
- `test_stations.DownloadAndIncrementalTests.test_explicit_proxy_does_not_inherit_metered_link`：旧断言显式代理17908，与当前空代理不符。
- `test_stations.StationHTTPTests.test_preview_refresh_cannot_rebuild_any_catalog`：缺固定预览目录，HTTP503。
- Node `test_grid_stations_frontend.js`：既有旧控件查询返回null；`test_stations_frontend.js`：既有“省界”说明断言不符。两项未通过掩盖或跳过。

新增13项Python测试覆盖：1/2℃及浮点容差、0.1及实数量级边界、两种微量口径、rain12微量合成、两个时段全部7×7组合、站点离线/地图/分时次/单站计数、trace迁移、revision后native（含WN3）及Open-Meteo清理源行保留、Open-Meteo分时段档位/旧阈值保留、Spark overlay分级不被折叠、核对工具反例/只读、迁移发布前中断重试。

另从 Git 中 `44e7726:station_score.py` 载入原生产者，对同一份覆盖全部站点变量、含低方差气压的合成 pairs 生成旧产物，用新工具逐值无容差比较新产物；覆盖窗口scores/station-scores、detail_stats、grid-rows、grid-map和原pair字段。没有修改已有数值运算公式。相关专项49项全部通过；既有增量/SIGKILL/精度证书测试继续通过。三处已有测试仅扩展了分级行数量断言，原累加行检查全部保留。

新增jsdom文件同时实际驱动格点页与独立站点页：能力开关、lead无新指标、PC固定阈值及禁用按钮、分级选项/禁用ETS、分级performance、按计数合并与逐时平均的区别、混合新旧行共同时间、无新计数说明、真实覆盖日期、百分比地图/表格/单站UI。没有把生产缓存复制到WSL，真实数据精确等价尚待Spark核对。

复现命令：

```bash
/home/chengyang/.venvs/ec-gfs-server/bin/python -m unittest discover
/home/chengyang/.venvs/ec-gfs-server/bin/python -m unittest discover -s incremental-qc
NODE_PATH=/tmp/guifan-jsdom/node_modules node --test test_*.js
/home/chengyang/.venvs/ec-gfs-server/bin/python -m unittest \
  test_guifan_scores test_station_grid test_station_incremental test_native_rain1h test_station_selection
```

## 只读工具和交付限制

`tools/check_guifan.py OLD NEW [--pairs PATH] [--sample 32]` 接受站点缓存/快照目录或catalog/rows JSON（支持gzip）。它核对所有旧行已有字段，JSON float按binary64比较；流式核对pairs和旧daily列；检查N1/N2/T/分级恒等式和最高档；按完整站点forecast group独立重算抽样计数。返回码0通过、1失败，最多打印20条差异。旧trace的预期修复不混入fc/ob等旧字段比较。没有覆盖到的真实group不能据此宣称已全量独立验证；可增大--sample。

环境给本仓库 `.git` 只读权限，普通 `git add/commit` 报 `index.lock: Read-only file system`。已在 `/tmp/guifan-git` 的独立元数据、同名分支分块提交，bundle保存这些提交；当前工作目录保留全部可审查改动，原只读HEAD未移动。Spark导入/建worktree命令在预览说明中。这是环境限制，不能声称原只读分支已经更新。

未处理后续：lead/weekly外部旧评分程序、日最高/最低气温、风规范准确率、技巧评分、新疆3站降级档位。新疆此次rain24仍使用通用表2-1档位，界面不声称满足降级检验。实际Spark耗时、磁盘增量和外部Open-Meteo版本差异也必须在上线前实测/审查。

本机没有保留大型实验数据副本。保留 `/tmp/guifan-jsdom` 约28 MB用于复跑前端，`/tmp/guifan-*.log` 共不足1 MB作为测试原始日志；交付根目录的bundle不足1 MB。安装缓存与临时编辑脚本在收尾删除。生产/预览缓存大小只给估计，未杜撰测量值。
