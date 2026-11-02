# 站点检验模块 v1

降水/气温页的「格点 | 站点」现在选择参考资料，共用原来的控件、图表、评分明细及 SVG/CSV 导出；深链接为 `/Verification/test/#grid&ref=station`。站点范围为华南104–121°E、18–27°N，使用近31天有效时间、0–168小时预报时效；降水只有6/24小时累计。站点默认比较EC 0.1°与EC 0.25°，其余模式可在原模式按钮中选择。原独立页 `/Verification/test/#stations` 保留，提供近1/3/7/30天汇总及露点、气压、风速等原要素。时间在配对库中存为UTC，主图行及日期筛选使用北京时间。

DOM流程检查使用可选的jsdom开发依赖：`NODE_PATH=/tmp/st-jsdom/node_modules node test_stations_frontend.js`。本次依赖仅安装在`/tmp/st-jsdom`，不是页面或后端运行依赖；测试响应不写入服务数据。当前受限环境的Chrome渲染器启动失败，DOM测试不替代正式浏览器的排版复核。

网页只读取已发布快照。下载、解码、质控、配对、评分和汇总均由`refresh_stations.py`离线完成，网页刷新仅重新读取结果。未配置任何系统服务，`deploy/`中仅有服务及定时器草稿。

## 观测与站点

- [NOAA GHCNh站表](https://noaa-ghcnh-pds.s3.amazonaws.com/hourly/doc/ghcnh-station-list.txt)：评分框内99个CHM站为基表；与OGIMET块56/57/58/59出现的站点取并集。补充站点通过GHCNh其他中国编号匹配，例如59493列为CHU00059493。`stations.json`冻结站号、名称、经纬度、海拔、来源与坐标出处。CHU中的历史站址可能与当前站址不同，v1没有自行更换坐标或推算海拔。缺少坐标的OGIMET编号单独记录，不猜测其位置。
- [OGIMET SYNOP](https://www.ogimet.com/getsynop_help.phtml)：近实时主源。按块、固定时间窗下载，31天内的原始报文保留。一次30天补采拆成10个三天窗口×4个块，共40次请求，前后窗口不重叠。请求开始时间间隔至少20.5秒；先写请求日志，再发送请求；相同窗口不自动重发。回补累计上限120次。HTTP错误、HTML或限流响应立即设置停止标记，保留原因与响应，不再访问OGIMET。超时、连接被拒或中断这类没有收到响应的网络故障只暂停2.5小时（`retry_after`），由下一轮定时刷新重试；连续3次网络故障才设置停止标记。
- [GHCNh v1.1说明](https://noaa-ghcnh-pds.s3.amazonaws.com/hourly/doc/ghcnh_DOCUMENTATION.pdf)：使用2026年PSV站年文件，按日条件GET与ETag缓存。只填补OGIMET缺测或未通过质控的同站、同时间、同要素，另生成独立来源一致性报告。质控码按来源区分；220等来源仅采用`1=Good`，不误用`5=Removed`。313等来源采用明确通过检查的0/1/4/5/9。未接受的码保留在审计文件中。没有使用已停更的ISD/global-hourly观测。
- [IEM CN__ASOS](https://mesonet.agron.iastate.edu/geojson/network/CN__ASOS.geojson)：框内14个机场，下载时每批合并全部机场。初始30天最多3批，正常刷新1批最近26小时。只检验气温、露点、风速，华氏温度转换为摄氏度，节转换为m/s；不把METAR降水或气压纳入本版评分。站表列出站点不表示该站当前有资料。

所有数据请求创建`trust_env=False`的会话，并显式设置HTTP/HTTPS代理`http://127.0.0.1:17908`。不会继承登录环境的7897代理。原始文件、解码记录、质控标记、来源响应元信息和下载日志均留在忽略目录`cache/stations/`。

## SYNOP解码与质控

`synop_decode.py`独立解码FM-12的AAXX头、iRiXhVV、Nddff与00fff、温度、露点或相对湿度、站压、海平面气压、气压倾向、天气、云和333节。高山站`4a3hhh`不当作海平面气压。iw=0/1为m/s，3/4为节；露点用湿度推导时记录出处。NIL不生成观测，更正报按CCA/CCB等级及接收顺序保留最新值。原始组和未解释的333组保留以供复查。

降水严格使用报文自身的累计时段：

| 编码 | 规则 |
| --- | --- |
| 6RRRtR | RRR=000–988为整毫米；989为≥989mm下界，保留但不作精确评分；990为微量；991–999为0.1–0.9mm |
| tR | 1/2/3/4/5/6/7/8/9分别为6/12/18/24/1/2/3/9/15小时；0或缺测为未知，不评分 |
| 333节7RRRR | 过去24小时，单位0.1mm；9999为微量 |
| iR=3且无组 | 记录零、累计期未知，不当作6或24小时零降水 |
| iR=4 | 未观测，全部降水保持缺测；若报文仍有雨量组，仅留在原始编码供复核 |

微量记录为0并保留trace标记。相同时段多个组冲突时标记并排除。未知时段保留在解码审计资料中，不从观测时次反推累计期。即使GHCNh同时间降水为零，也不足以证明省略组代表哪个时段；v1始终不放宽iR=3规则。

质控使用[DATA_QUALITY.md](DATA_QUALITY.md)的华南季节气温与6/24小时降水上限、露点≤气温+0.5°C、650–1150hPa海平面气压、0–60m/s地面风速。跨季降水取覆盖月份中最宽的上限。保守时序复核为气温20°C/h、露点25°C/h、风速40m/s/h、气压30hPa/h（间隔不足一小时按一小时容差，超过6小时不检查）。不同值的重复报告标记冲突；完全相同的重传合并，更正报替换旧报。所有标记值保留但不计分，不修正原测量。

## 预报及配对

EC 0.1°是`ECMWF-IFS-9km-OpenMeteo`本地固定起报区域缓存，直接导入现有`openmeteo_regional.py`的manifest/field/sample读取与校验逻辑，未向Open-Meteo请求新预报。0–90小时为逐小时，之后至144小时为3小时，之后为6小时；06/18 UTC起报只到144小时。

降水时间语义核对了[Open-Meteo开放数据说明](https://github.com/open-meteo/open-data#updates-to-real-time-weather-forecasts)、[IFS原生步长说明](https://open-meteo.com/en/docs/ecmwf-api)以及本地`precipitation.json`的source_times：`data_run`保持原生时间分辨率，降水是向后累计量且不含起报时刻。因而lead=93覆盖(90,93]，lead=150覆盖(144,150]，不是前一小时雨量，也不乘3/6或再作累计差分。实现先最近邻采样各原生雨量，再严格拼接完整时段；与原有`rain()`使用相同的原生步长规则，避免整幅区域场相加。

EC/GFS/ECAI/GFSAI/GEFS复用`native_archive.py`身份、时间、网格、存储校验及有界加载。优先原归档，再用原平台`cache/native-backfill/`填补；GEFS实际位于其中。慢盘目录优先复用`native-scores.json`已发现路径及各模式`catalog.json`，发现结果持久化，只在无索引时做一次目录枚举。各字段使用只读mmap，仅访问站点邻点，不物化全球场。每批站点样本持久化为NPZ，后续观测变化不必重复采样归档。

0.25°归档实际存在3小时和6小时雨量。仅采用rain_start与有效时间匹配的rain3h/rain6h，6小时可由两段3小时或一段6小时组成，24小时同理，全部同一起报且连续、不重叠，缺任一段即缺测。气温和RH在四个原生邻点计算Magnus露点后再双线性插值。风先插值u/v，再计算风速。仅有气温/降水的GEFS或其他产品不补造风、露点和气压。

WN3使用现有已验证的原生**0.1°集合均值**清单和NPZ读取器，仅气温及rain1h，不误称0.25°，不使用显示JSON、演示别名或不存在的地面要素。逐小时雨量连续累加。SYNOP只在UTC 00/03/06…/21整点配对；METAR在模型有效时刻±10分钟内选最近的一整份报告，报告内缺测字段不另换较远报告填补，等距取较早报告。0.25°仅在实际三小时时次配对，EC 0.1°可逐小时配METAR；缺预报时刻不作时间插值。超出覆盖范围不外推。

气温、露点、海平面气压、u/v双线性；降水最近邻。本版没有海拔订正。站高来自站表；归档有明确以米为单位的海拔字段时才记录模式格点海拔，差值超过300m在图上标▲。当前本地产品未提供可确认的地形字段时，页面显示未知，不能据此断言海拔相符。

## 统计与发布

独立 `#stations` 页的连续量保持原口径：合并全部站点配对计算n、ME、MAE、RMSE、CC，ME为预报－实况，零方差CC为缺测。共用图表则先对每个模式×起报×时效×有效时间跨站计算n、ME、MAE、RMSE、空间CC（少于3站或零方差为null）；期间/时效组各份预报等权，RMSE先平均平方再开方，CC为平均空间相关，沿用 `core.js`。共同有效时次只匹配时效/有效时间，不保证各模式站点集合相同。

降水保存H、M、F及n（从而可得正确否定C），事件为累计降水≥阈值。6小时阈值0.1/1/5/10/25/50mm，24小时阈值0.1/10/25/50/100mm。TS=H/(H+M+F)，POD=H/(H+M)，FAR=F/(H+F)，ETS使用随机命中(H+M)(H+F)/n，分母无效时为null。微量小于0.1mm。

工作SQLite保存紧凑逐配对记录和每个模式起报的来源签名；每个run只因预报签名或相关有效时间的实况签名变化而重新评分。逐日`pairs/YYYY-MM-DD.csv.gz`为可复查配对文件。离线准备模式×要素×时效、站点×模式×要素及固定时效的近7/30天汇总；独立页API只读取已准备的评分；日期范围地图仅合并小型日统计表，不扫描全部配对。每轮完成后发布独立不可变SQLite和JSON快照，原子替换`current.json`，失败时继续提供上一轮已完成结果。

API只有GET：

- `api/stations/meta`：站点、观测源最新时间、模式实际配对范围、资料数和海岸线。
- `api/stations/scores?var=t&models=ec01,ecmwf&days=30&range=0-168`：逐时效及站图所需汇总。days支持1/3/7/30，range支持0-168、0-24、25-72、73-168；变量为t/td/msl/wind/rain6/rain24。模式ID为ec01/ecmwf/gfs/aifs/aigfs/gefs/wn3。
- `api/stations/station?id=59493&var=t&model=ec01&lead=24&days=30`：固定时效单站统计及至多1000个时序点；不向浏览器发送全配对库。

共用图表新增GET接口，站点行不写入 `api/catalog`：

- `api/stations/grid-datasets`：站点资料集元数据，ID为 `station-气温-2m`、`station-降水-6`、`station-降水-24`。
- `api/stations/grid-rows?dataset=station-气温-2m`：离线JSON行；按 `Accept-Encoding` 直接流式传输gzip文件，或有界解压流式返回。后端不缓存整份行JSON。
- `api/stations/map?model=ec01&var=t&start=2026-10-20&end=2026-10-25&leads=25,48,72`：BJT起止日期均包含，可传 `lead=24` 或逗号分隔 `leads=`；降水另传合法 `threshold=`。模式支持站点ID和主图完整模式名。仅合并 `grid-map.sqlite` 中按模式×要素×BJT日×时效×站点保存的充分统计量。
- `api/stations/series?station=59493&model=ec01&var=t&lead=24&start=2026-10-20&end=2026-10-25`：复用原单站查询，按BJT整日转换为UTC边界；最多1000对。原 `station` 接口亦支持 `start`/`end`。

主图站点行在首次切换时才读入浏览器内存，原格点来源与站点来源分别选择。日期含自定义，钳制到当前站点资料集范围；阈值、时效组、视图、共同时次、表格与导出共用原评分逻辑。主图下方地图跟随首个/当前单模式、日期、时效/时效组、指标与阈值。地图按全部配对合并，单站CC为时间相关；主图的共同有效时次/起报时次/单时刻筛选不再过滤地图。地图说明区明确区分口径。地图点击或键盘选择站点后显示固定时效序列；时效组内可选择该组的单个时效。共享SVG地图和序列绘图函数在 `stations.js` 的 `StationVisuals` 中，独立页保留原功能。

每轮 `aggregates()` 同时调用 `grid_aggregates()`：一次SQL分组生成逐次空间行，另一次分组生成日统计，各降水阈值共享扫描。SQLite请求连接均为 `mode=ro&immutable=1`，地图连接缓存限制约2MiB。新文件先写临时文件再替换；正常定时任务仍在全部文件完成后原子发布快照指针。只补建新产品、不下载/配对/运行旧4窗口汇总的命令为：

```bash
STATIONS_CACHE_DIR="$PWD/cache/stations" nice -n 10 ionice -c3 \
  /home/chengyang/.venvs/ec-gfs-server/bin/python station_score.py --grid-only
```

此命令写所选快照的新产品，只能对本工作树的副本执行；预览不得指向生产站点缓存。行数、压缩大小、日统计表大小及耗时写入快照 `grid-status.json`。

站图复用平台现有华南海岸线，沿用SVG图表与现有CSS变量，支持键盘选择站点、深浅色、模式对比与单站曲线。各模式默认使用各自有效配对，样本数可不同，不能把此表当作严格共同样本排名。维护报告另按run/lead/station/var/实况来源完全相同的EC 0.1°、EC 0.25°、GFS共同配对给出D1–D5比较。

## 刷新、验证与回滚

在本工作树使用科学虚拟环境，不安装新依赖：

```bash
export PYTHONDONTWRITEBYTECODE=1
PY=/home/chengyang/.venvs/ec-gfs-server/bin/python
$PY refresh_stations.py --backfill 30  # 初始回补，必须遵守OGIMET停止标记
$PY refresh_stations.py                # 常规一次性刷新
$PY refresh_stations.py --score-only   # 完全离线重新配对/发布
$PY refresh_stations.py --fetch-only   # 仅准备观测
$PY build_stations.py                  # 显式重新冻结待评审站表
$PY -m unittest -v test_stations
```

站点缓存可由`STATIONS_CACHE_DIR`配置，默认本工作树`cache/stations`。脚本持有flock，第二个刷新直接退出。完整首次采样较慢，进度和每轮资源统计写日志及`refresh-status.json`。OGIMET出现停止标记时，先检查`ogimet-journal.json`与`.error`响应、等待并联系数据维护者确认；脚本不自动清除标记。不要用反复执行回补来绕过限制。

定时服务设置`STATIONS_ALERTS=1`：每轮刷新结束后由`station_alerts.py`检查，OGIMET停止标记或OGIMET实况超过12小时未更新时，通过`~/wecom-claude/wecom-send`发企业微信；同一问题24小时内不重复，恢复时发一条“已恢复”。提醒状态记在缓存目录`alert-state.json`，发送失败下一轮重发。手工运行和测试默认不发。

`agreement.json`和`agreement-mismatches.csv.gz`保存跨源统计与完整差异列表；`synop-decoded.jsonl.gz`和逐日obs JSONL保存原始编码与QC。`validation-report.json`、`common-report.json`与`shenzhen-sanity.json`记录共同配对及59493与深圳最近格点T的同时间检查。数据存在稀疏、较长时延，尤其没有明确6小时GHCNh累计量时，一致性样本为0，不能写成已验证一致。风速整m/s编码与GHCNh小数精度可产生较低严格相等率，报告保留实际差异。

预览启动：

```bash
setsid nohup ./run_preview.sh > /tmp/preview-8524.log 2>&1 < /dev/null &
# http://127.0.0.1:8524/Verification/test/#grid&ref=station
```

预览仅绑定127.0.0.1:8524；`VERIFICATION_READ_ONLY=1`阻止既有POST和其他可能启动工作的API，格点目录仅可直接读取原平台已发布JSON。不会跟随原来的目录重建路径，也不把站点缓存链接到生产。普通server.py默认行为维持；新增站点路由本身始终只读。

回滚站点数据时，把`current.json`原子指回已完成的旧`snapshots/<generation>`，不要改配对库内容。代码经评审后可回退本模块提交；当前不改生产端口、服务、计时器或生产原始数据。若Git元数据被沙箱挂为只读，无法更新原worktree的stations分支；本次评审提交保存在`cache/stations/review.git`及`stations-review.bundle`中，恢复正常Git写权限后再应用，详见交接报告。

### 逐有效时间增量评分（station-incremental）

`refresh_stations.py --score-only --end 2026-10-27T03:00:00Z` 可固定离线评分终点，禁止与抓取同时使用。每轮日志输出观测加载、发现、各模式源检查与配对、各天数窗口汇总、grid、共同样本报告、日导出、覆盖统计、SQLite backup 和发布耗时；`score-status.json` 保存汇总耗时、全量/局部 run 数及变更有效时间数。`--score-only` 的抓取耗时明确为 0。

`jobs` 保留旧签名；新增 `job_state(model,run,version,source)` 和 `job_valid(model,run,valid,signature)`。源签名与评分终点解耦，覆盖已提交的预报时效，终点前移不会因为多出有效时间而改变预报身份。新 run、源签名变化或 `VERSION` 变化整 run 重配；其余只替换观测摘要变化（新增、订正、删除）的有效时间，使用主键的 model/run/lead 前缀删除。窗口退出单独 DELETE，不会触发其他时效重算。旧 `jobs` 签名与当前输入完全相等时直接建立新元数据；无法证明相等的旧 run 首轮保守全算，因此迁移轮可能比后续轮慢。

`forecast_runs(..., lazy=True)` 先检查源身份，评分器比较摘要后才载入站点数组；无变化的 run 不解压站点数组、不打开原始预报场。需要配对时最多额外载入前 24 小时用于原有降水精确拼接。缓存记录各 lead 的源身份，只对确实缺失/改变的 lead 重新采样；现存原始字段的 mtime/size 每轮仍检查，以检测独立替换；缺字段仅在目录身份/mtime/ctime 未变且上次检查已越过 2 秒粗时间戳保护期时复用缺失结果。NPZ ZIP 注释内嵌采样证书（旧读取器忽略注释，代码回退仍可读取数组）与数组一起原子替换，旧 NPZ/证书仍可读取。新写数组使用 float64，避免新采样值先评分、随后存为 float32 而在下一次全算时发生舍入变化。对已证明预报源未变的旧缓存，一次性用已发布 pairs 的 fc 恢复被 float32 舍弃的低位（必须舍入到完全相同的 float32 值，否则报错）；pairs 行本身不变，完成标记使后续未变轮不再载入数组。这是正常按需载入之外的一次性格式迁移。

旧原生降水区间由 `station_precision.py` 单独恢复：候选只能来自原始元数据或旧区域 JSON 的三位小数精度，且必须与旧区间的 float32 编码完全相同；再用已有全部 6/24 小时 fc 总量约束区间选择，逐项精确复核后才保存 `pair_precision=2`。已精确的值保留，无法由舍入解释的差异直接报错；不修改 pairs，不使用观测值选择预报精度。

旧 1/3/7/30 天评分对每个变量、阈值、时效范围反复扫描 pairs。现为每窗口 6 次 SQL 分组（原来 90 次）：一次同时产生全部详情矩和降水阈值计数，另 5 次共享连续要素的原始 SUM 次序，避免低方差气压 CC 的消减误差被重组放大；降水合并的列联计数是精确整数。逐站/逐时效详情流式写入 `detail_stats`，只在内存中保留较小的合并计数。不能平均各站的分数。旧评分 CC 的 n>1 与 grid CC 的 n>=3 分别保留，列联阈值和分母规则不变。grid 两个有界内存扫描仍保留。SQLite 全量 backup 仍保留并单独计时，避免对 immutable 读者采用可变硬链接。

每个 run 的 pairs、观测摘要和 `dirty_days` 在一个 SQLite 事务内提交。导出日清单持久化，配对后、发布前被杀也会在下轮重试导出。新产品先写 `snapshots/.incomplete-<generation>`；全部完成且 SQLite writer 关闭、文件 fsync 后，目录原子改名，再持久化原子更新 `current.json`。旧代读者继续读取不可变文件。清理只保留 current 和另一个最新完成代；未完成目录不占名额，回滚到较旧 current 时也绝不删除 current。`common-report.json`/`shenzhen-sanity.json` 同时保存在完整代内，根目录副本供旧离线消费者使用。

深圳 sanity 缓存格网定位和按文件 mtime/size 验证的逐时格点值；观测值始终取当前轮输入重新计算指标，观测订正不复用旧分数。

验证工具：`tools/benchmark_station_run.py` 支持固定终点、保留旧代码入口和离线预报样本录制/重放（pickle 只读取自己生成的本地文件）；`tools/freeze_station_cache.py` 将已完成轮的认证站点样本冻结为完整 baseline 配对的相同输入；`tools/compare_station_runs.py A B` 流式逐行精确比较全部 pairs 列，并比较全部评分、grid 行、grid-map 统计、datasets 及共同报告。允许的浮点误差为相对 1e-9、近零绝对 1e-12；仅忽略发布时间和压缩文件字节数。真实私有副本耗时及完整等价结果见 `STATION_INCREMENTAL_REPORT.md`。

冻结输入 A/B1 的 5,740,355 条 pairs 与降水精度修复后连续 B2 的 5,742,260 条 pairs 均逐列精确相等，全部派生产品零差异，最大相对误差 0。该检查独立重做配对与汇总，预报站点数组是共享冻结输入，因此不构成对原始场插值的独立复核。最终 Python 站点/格点测试 52 项全通过（含 HTTP），两个前端测试文件全通过。

私有副本实测：main baseline 54.67 分钟 / 453.0 MiB；首次实时源迁移轮 36.21 分钟 / 446.7 MiB；含精度迁移的连续 B2 28.39 分钟 / 443.6 MiB。推进 3 小时终点的 B3 成功完成，GNU time 为 27:20.26 / 487.9 MiB（499,644 KiB），9 个全量和 112 个局部 run，插入 19,091 条 pairs，总计 5,744,053 条。**B3 未达到约 20 分钟目标**：仅预报源身份检查就用时 20.41 分钟，采样/配对/SQL 另用 4.10 分钟；保留现有 timeout，不能据此宣称 15–20 分钟达标。冻结输入 B1 的 5.48 分钟不含真实源发现/读取，不作为日常运行耗时。同终点重复轮及各阶段明细见报告。

首次同终点重复轮为 24:00.90 / 476.1 MiB，但期间 GFS、EC0.1 与两个 WN3 run 的源发生变化，重算 4 个 run / 插入 551 条 pairs，因此不能当作无变化轮。B3 与首次重复轮的 supervisor 和子进程各调用一次 `nice -n 10`，叠加后实际 nice=19；后续无变化验证改为单层 nice=10，全部使用 `ionice -c3`。耗时比较需保留这一调度差异，不能把 B3 的实测超时目标直接外推到所有生产运行。
