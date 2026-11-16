# 临近降水页（QPE + 短时 QPF 检验）实施报告

分支：`nowcast-rain`（从正式站 `20bcf29` 拉出）
执行日期：2026-11-10

## 1. 改动文件

| 文件 | 改动 |
|---|---|
| `nowcast.js`（新增） | 「临近降水」页面。只有两个控件：指标（TS 评分 / 命中率 / 空报率，默认 TS）和日期（近3天 / 近7天 / 近30天，默认近30天）。日期控件放在第二张表卡片标题旁。两张表、计算、加粗、说明文字均按要求实现。 |
| `nowcast_api.py`（新增） | 不依赖 HTTP 的聚合逻辑：`qpe()` 只读雷达估测 JSON；`qpf(days)` 从现有 1 小时降水评分行聚合。模块导入时不读写任何 cache。 |
| `server.py` | 注册 `api/nowcast/qpe`、`api/nowcast/qpf`，放在 `VERIFICATION_READ_ONLY` 的 503 判断之前；静态文件表登记 `nowcast.js`。 |
| `index.html` | 侧栏「考核总览」下面加「临近降水」；新增 `#nowcast-workspace`；加载 `nowcast.js`。 |
| `modules.js` | 模块列表加入 `nowcast`；切换时显示/隐藏工作区；离开时关闭；顶栏「刷新资料」在临近降水页刷新本页。 |
| `demo-models.js` | `openModule('nowcast')` 直接打开本页，不请求 WN3 演示目录。 |
| `style.css` | 少量布局样式：日期控件、卡片内横向滚动、跨行方法名左对齐。沿用现有 `overview-*` 卡片/表格类。 |
| `guifan_scores.py` | 1 小时降水阈值加 5 mm：`[.1,1,3,5,10,20,30,50]`；`REVISION` 从 `guifan-2017-v1` 调到 `guifan-2017-v2`。 |
| `native_archive.py` | 第 50 行 `THRESHOLDS` 加 5 mm（与时序 1 小时评分实际使用的 `guifan.THRESHOLDS[1]` 保持一致，测试按它断言）。 |
| `openmeteo_verification.py` | 模块顶部 `THRESHOLDS` 加 5 mm；1 小时评分实际取 `guifan.THRESHOLDS[hours]`，因此同步生效。 |
| `test_nowcast_api.py`（新增） | 12 个聚合/缓存/QPE 文件单元测试。 |
| `test_nowcast_frontend.js`（新增） | jsdom 前端测试，覆盖路由、两个控件、三种指标、加粗规则、缺失雷达文件、页面无英文缩写。 |
| `NOWCAST_REPORT.md`（本文件） | 报告。 |

3 小时及以上累计阈值、站点评分阈值（`station_common.THRESHOLDS`）未改。

## 2. 页面与接口口径

### 页面

- 路由 `#nowcast`；无 hash 仍进考核总览。
- 标题「临近降水（深圳，1 小时雨量）」。
- 第一张表：雷达估测降水（历史回算）。只画 `show: true` 的方法，顺序按文件；列头小字「实况 N 格点次」取第一个方法的 `H+M`，千分位。
- 第二张表：短时预报。方法名跨行合并，第二列「第1小时」～「第6小时」；接口返回几个方法就画几个。`observed` 直接用于列头。
- 三种指标与「—」：分母为 0 或接口返回 null 均显示「—」；TS 两位小数，命中率/空报率一位小数百分数。
- 加粗：按显示精度比较；同一列（QPF 为同一时效内）只有一个有效值、或全部相同时不加粗；「—」不参与。
- 页面可见文字不含 POD、FAR、CSI、QPE、QPF（测试断言）。
- 雷达文件缺失/坏 JSON/缺 `methods`/`thresholds` 时，第一张卡只显示「雷达估测评分暂未生成」，第二张表照常。

### `api/nowcast/qpe`

- 读取 `NOWCAST_QPE_FILE`；未设置时读仓库下 `cache/nowcast/qpe-history.json`。
- 正常返回 `{"available": true, "data": 原文件}`；异常返回 `{"available": false}`，HTTP 200。
- 按 `(路径, mtime_ns, size)` 缓存；文件更新后重读。

### `api/nowcast/qpf`

- 只读 `current_http_snapshot()` 的两份资料集文件（`openmeteo-降水-1`、`hk-降水-1`），不走 `api/catalog` 全量接口。
- 日期窗口：只用配置中 model、时效在 `9..26` 小时（= `k+delay` 的最小/最大可用范围）的行；`end` 为其中最晚 `valid`，`start = end - days`，严格 `start < valid <= end`。
- 对每个（方法，V，k）：取满足 `lead >= k + delay_hours` 的最小 lead；`L > k + delay + 12` 跳过；同一 (V,L) 有多个 run 时取最新 run；该 run 缺某阈值则只跳过该阈值。
- `hours` 按 0.1 mm 档计数；没有任何累加行的（方法，k，阈值）返回 `null` 而不是 0。
- `observed` 按阈值取第一个有数据的方法在 k=1 时的 `H+M`。
- 全部 k、全部阈值都为 null 的方法不返回。
- `days` 只接受 3/7/30，否则抛 `ValueError`，由 server 转 400。
- 按 `(snapshot_id, days)` 缓存；`snapshot_id` 变化（目录重建）后失效。

## 3. 1 小时降水补 5 mm：重算条件与处理

### 两条链路什么时候重算老行

1. **Open-Meteo（`openmeteo_verification.py`）**
   - 缓存文件：`SCORE_ROOT/score-memo.json`，每条 memo 的 `signature` 为
     `[guifan.REVISION, 源 manifest 各段 sha256, 参考场签名, 各实况文件签名]`（第 149 行）。
   - 包级 `REVISION = 'openmeteo-verification-v2+' + guifan.REVISION`（第 22 行），写进 memo 和 `verification-catalog.json`。
   - 因此改 `guifan.REVISION` 会让所有 memo 的 `signature` 失配，下一次 `refresh()` 全量重算；新算出的 1 小时行会带上 `guifan.THRESHOLDS[1]` 里的 5 mm。
   - 另有一条兜底：源起报或实况被裁剪时，旧行会原样保留（第 217–221 行）。这些历史行可能没有 5 mm 档；`qpf` 按要求把缺失阈值当作 null，不补 0。

2. **本地原生归档（`native_archive.py`）**
   - 缓存文件：`NATIVE_SCORES_PATH`（默认仓库下 `native-scores.json`），`files` 下每条有 `signature`。
   - EC/GFS/AI 帧：`source_signature()` 末尾带 `guifan.REVISION`（第 86 行）；AI 累计降水窗口的签名第一项也是 `guifan.REVISION`（第 302 行）；WN3 的 `wn3_source_signature()` 返回 `{'guifan': guifan.REVISION, ...}`（第 148 行）。
   - 顶层 `version` 字段（第 321 行）只写不校验，真正决定是否命中缓存的是每条 `signature`。
   - 因此改 `guifan.REVISION` 后，下一次运行 `native_archive.py`（或调用 `build_native` 的评分任务）会重算 31 天窗口内的全部原生帧；WN3 1 小时评分改用含 5 mm 的新阈值。

3. **站点评分**
   - `station_score.migrate_trace()` 用 `guifan.REVISION` 作为迁移记录键（第 590 行），`meta.score_revision` 也会变。
   - 但站点评分本身用的阈值来自 `station_common.THRESHOLDS`（未改），`VERSION`（`stations-v1.0`）未变；`guifan.sql_expressions()` 也不读 `THRESHOLDS`。所以**不会触发站点评分重算**，只会多跑一次幂等的 trace 标记迁移、更新 meta 里的 revision 字符串。

### 是否改了版本号

改。`guifan_scores.REVISION` 从 `guifan-2017-v1` 改为 `guifan-2017-v2`。这是两条格点链路唯一实际生效的失效开关；只改阈值列表不会让已缓存的旧行重算。

### 上线后第一次运行的量级估计

- 改 `guifan.REVISION` 会同时使原生归档与 Open-Meteo 的**全部格点 memo** 失效，不只 1 小时降水：还包括 3/6/12/24 小时累计降水和 2 米气温。原因如上，签名是共用的。
- 窗口都是 31 天滚动窗口。按任务书的描述，两个 1 小时资料集平时各有约 6–7 万行；新增 5 mm 档后，1 小时评分行数约增加 1/7（+14%）。
- 本机没有正式 cache（`NATIVE_SCORES_PATH` 无文件，`/home/yjzx/...` 不可读），无法给出精确行数与秒表值。按仓库里 `GUIFAN_PREVIEW.md` 对整轮格点重算的估计（格点约 5–30 分钟），我估计两条链路首次合计在**十几分钟到半小时**量级；站点链路只多一次轻量迁移。
- 按要求**没有运行**任何会写正式 cache 的评分程序，也没有改版本号以外的触发手段。

## 4. 测试

### 新增测试

- `python3 -m pytest test_nowcast_api.py -q` → **12 passed**。覆盖：
  - lead 3/9/15、delay 8 时 k=1 用 lead 9、k=2 用 lead 15；
  - 起报过旧（k=1 只用 lead 22）跳过；lead 3 与 27 不进入可用范围；
  - 缺 5 mm 行时该阈值为 null、其它阈值正常；
  - `observed` 按阈值回退到下一个方法；
  - `start` 不含、`end` 含；
  - 无数据方法不返回、非法 `days` 抛 `ValueError`；
  - 缓存随 snapshot 版本失效；
  - QPE 文件缺失/坏 JSON/缺字段/正常/更新后重读。
- `NODE_PATH=/tmp/st-jsdom/node_modules node test_nowcast_frontend.js` → **PASS**。覆盖导航与默认路由、两个控件、指标切换不重发请求、日期切换只重发 qpf、三种指标算法与「—」、并列/整列相同/单值加粗规则、第一张表 unavailable 时第二张表照常、页面无 POD/FAR/CSI/QPE/QPF。

### 现有测试

前端（全部用 `NODE_PATH=/tmp/st-jsdom/node_modules` 运行）：13 个 `test_*.js` 全部 PASS，包括 `test_guifan_frontend.js`、`test_overview_frontend.js`、`test_detail_redesign.js`、`test_stations_frontend.js`。

Python：

- `python3 -m pytest test_nowcast_api.py test_guifan_scores.py -q` → 21 passed，4 failed，均为 `ModuleNotFoundError: No module named 'shapely'`（`native_archive.py` / `openmeteo_verification.py` 导入阶段失败），属于任务书说明的环境性失败。
- `test_native_rain1h.py`、`test_station_basemap.py` 因缺 shapely 无法收集。
- 全量 `pytest -q --ignore=test_native_rain1h.py --ignore=test_station_basemap.py` → **192 passed，29 failed**。抽查失败原因均为缺 shapely 或缺演示/缓存文件（例如 `cache/model-interface-demo/current.json`、预览 `native-catalog.json`），未发现由本次阈值或页面的改动引起的新失败。
- 额外做了一次本地 HTTP 冒烟（临时目录 + `VERIFICATION_READ_ONLY=1`，非 systemd）：`api/nowcast/qpe` 200、`qpf?days=3` 200、`qpf?days=5` 400、`nowcast.js` 200，证明只读预览下路由位于 503 判断之前且可用。

### 性能

用合成数据（每个资料集 100,800 行，8 个阈值）测 `_qpf_for`：首次 **0.85 s**，命中缓存 **约 8 µs**。任务书要求首次 <3 s、命中 <0.1 s。正式数据行数更少（约 6–7 万行/集），预计更快；首次不含目录重建时间（生产服务目录已在内存）。

## 5. 没做完 / 不确定的地方

1. **没有在正式数据上验证。** 本机没有 `native-scores.json`，`/home/yjzx/verification-platform` 不可读；QPF 只用合成行和合成快照验证，QPE 只读了真实样例的结构（未复制进仓库）。真实数据下的 `hours`、`observed`、首次耗时需上线后核对。
2. **重算量是估计。** 未运行评分程序；行数和耗时按历史文档与 31 天窗口估算，不是实测。
3. **`end` 的“可用时效范围”我取 9–26 小时**（`min(k)+delay` 到 `max(k)+delay+MAX_STALE`）。任务书只说“时效在可用范围内”，未给具体上下界；若希望用别的范围，需要调整 `compute()` 里的 `lowest/highest`。
4. **`observed` 在 k=1 没有数据时返回 null**，即使 k=6 有数据也不回填；这是按任务书“取第一个有数据的方法在 k=1 时的 H+M”实现的。
5. **旧行可能仍缺 5 mm。** 两条链路都有“源文件被裁剪后保留旧评分行”的兜底；这些行不会因版本号变化而补出 5 mm。QPF 对它们按缺失阈值返回 null，正是任务书要求的“没有评分行显示 —”。
6. **站点评分受 revision 的轻微影响**：不会重算分数，但 `station_score.meta.score_revision` 会变、trace 迁移会多跑一次。若要求站点评分元数据完全不变，需要把 5 mm 的失效开关从 `guifan.REVISION` 拆成单独的常量（这会让格点两条链路仍然全量重算，收益有限）。
7. **没有加任何额外控件**；详情页 1 小时降水阈值按钮会因此多出「5」，按要求保留。

## 上线口径补记（Claude，2026-11-10）

用户选「不重算」：`guifan_scores.REVISION` 保持 `guifan-2017-v1`，上文关于升到 v2 触发重算的描述不再适用。5 mm 档只出现在上线后新算的行里，约 30 天后补满；期间接口对缺 5 mm 的行返回 null，页面显示「—」。
