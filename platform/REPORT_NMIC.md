# 国家站三小时资料接入报告

2026-11-11，WSL `/home/chengyang/vt-nmic`。仅开发、验证和交接；没有登录 Spark，没有 push，没有修改线上服务、定时器或线上缓存。

## 交付状态

代码、测试、证据和预览步骤已完成。**原工作树的 Git 元数据被环境挂载为只读，不能更新原分支 HEAD**：实际 `git add` 报 `index.lock: Read-only file system`，路径为 `/home/chengyang/vt-guifan/.git/worktrees/vt-nmic/`。没有绕过该限制或修改外部目录。

提交保存在本目录的独立 Git 数据库 `.nmic-git/`，分支仍叫 `nmic-stations`，导出为 `nmic-review.bundle`。前三个实现提交：

- `caa07db` 接入国家站三小时实况并保持现有来源优先。
- `4075b28` 补齐 EC9km 新站点预报并增加严格等价核对。
- `5dcf8f0` 保持 EC 原生降水时段，禁止拆分长时段评分。
- 最后一条提交包含本报告、可复现回放工具及验证证据；用下面的 `git log` 查看完整 ID。

```bash
cd ~/vt-nmic
git --git-dir=.nmic-git --work-tree=. log -4 --oneline
git bundle verify nmic-review.bundle
```

bundle 包含任务书提交 `814a117`，先决提交为 `1e3c717`，便于当前 Spark 基线导入。它没有包含 `_nmic_data/` 原始资料、完整全国元数据或任何账号材料。测试夹具仅 `tests/fixtures/nmic_sample.txt`，17 行（表头加 16 条观测）。

在这个工作树的只读限制解除后，可用下列命令将原分支指向交付提交，保留工作区文件。执行前先核对当前分支为 `nmic-stations`，并确认没有另行暂存的修改；此命令会重置暂存区。

```bash
git fetch ./nmic-review.bundle nmic-stations
git reset --mixed FETCH_HEAD
```

## 改动文件及处理口径

| 文件 | 用途 |
| --- | --- |
| `nmic_obs.py` | 无网络导入目录内所有资料文本，UTC 时刻、去重、字段转换、完整雨量窗口、站点合并及首次入库活跃判断 |
| `nmic_provinces.json` | 176 个国家站的省码索引；来自 WSL 已有的 `/home/chengyang/nmic-radar/probe/old_stations.json`（440/450/460 的完整选择清单），没有访问数据网站 |
| `station_obs.py` | 读取 NMIC，加入归档、QC 和来源统计；在全部旧来源及其 rain12 派生完成后才填空 |
| `build_stations.py`、`station_score.py` | 建表及 `--score-only` 两条路径都能自动加入新站；详情/共用资料来源文字 |
| `station_api.py` | 华南按省码 44/45/46 自动加入新站，兼容旧快照名单；深圳新增 NMIC 站按现有经纬度范围加入 |
| `stations.js` | 来源名称「国家站三小时资料（2 分钟平均风）」及深圳地图站点筛选；未增加控件 |
| `nmic_ec01.py` | 匿名 Single Runs 点预报补取；默认只打印计划，明确 `--fetch` 才请求；持久请求限额、原子写入、坐标/单位/时间轴验证 |
| `station_forecasts.py` | EC 网格站表扩展时保留旧点的认证双精度值；扩展期点值重排；读取 NMIC 点补充，网格已有有效值优先 |
| `test_nmic.py`、`tests/fixtures/nmic_sample.txt` | 15 项离线测试及小样例 |
| `tools/check_nmic_equivalence.py` | 两个冻结结果的老站配对和逐站评分**精确**比较 |
| `tools/replay_nmic.py` | 真 NMIC 数据、模拟旧源与模拟预报的全评分链路回放，无网络，临时缓存自动删除 |
| `evidence/nmic/*.json` | 导入、测试、请求计划/日志、EC 实测、受控回放证据 |
| `.gitignore` | 排除全部原始资料、执行日志、独立 Git 库和交接 bundle |

默认目录是 `cache/stations/raw/nmic/*.txt`。覆盖变量为 `STATIONS_NMIC_DIR`；元数据默认同目录 `A0012_stations_api.json`，可用 `STATIONS_NMIC_METADATA` 指定。`STATIONS_CACHE_DIR` 仍按平台原规则控制缓存。

已先读本地 `SURF_CHN_MUL_HOR_readme.txt`。它列出要素与单位，但「特征值/说明」两列全空，也没有时制说明。因此，**不能声称已从该 readme 确认 UTC 或微量编码**。补充依据为气象局公开的 [GB/T 37301—2019 附录 E](https://www.cma.gov.cn/zfxxgk/gknr/flfgbz/bz/202209/P020220921704337679062.pdf)：999999 缺测、999998 不观测/无资料、999990 微量。未访问 `nmic.cn` 或 `data.cma.cn`。

- UTC：采用任务书中主管已核对的 19 站×48 时次证据，未加 8 小时；页面继续按北京时间显示。**这是给定交叉核对支持的口径，本次未独立重跑生产 SYNOP 对照。**
- `TEM→t`、`PRS_Sea→msl`、`WIN_S_Avg_2mi→wind`；保持原单位，不把 2 分钟风替换为 10 分钟风。露点复用 `sz_district_obs._dewpoint`；缺温、无效湿度不产生露点。
- 999999、999998、非有限值及其他保留数值不能成为测量值。999990 只在降水中作为微量：累计按 0 mm，累计值小于 0.1 mm 且含微量时保留 `trace=True`，与平台既有计分语义一致；不人为给 0.05 或 0.1 mm。
- rain6/rain12/rain24 分别要求连续 2/4/8 个有效 PRE_3h，窗口为 `(t-hours,t]`。任一缺时、缺测、未观测或非法值则整个窗口缺值；不跨缺口、不插值。读取截止时间之前的原始段以支持窗口边界累计。
- 去重先于累计，键为站号和 UTC 时刻；文件名升序、文件内行序，最后一条完整记录胜出。相同/冲突次数记录在导入诊断。文件修改时间不参与选择。文件名应保留下载订单名，后来的纠正版使用排序更后的名称。
- 所有既有来源的选择和 QC 先照原逻辑完成，rain12 也先派生；NMIC 仅对空键 `setdefault`。这样不会用新 rain6 改写旧 rain12，也不会把旧源和 NMIC 的两个半段拼成新 rain12。
- 新站 type 为 `synop`，来源 `nmic`，坐标/海拔来自给定 API 元数据。旧站名称、坐标、海拔保持；仅补来源及省码。最近 45 天的原始 NMIC 记录也能证明活跃，故初次尚无 obs 日文件的新站不会被剔除；过期 raw 文件不提供永久豁免。

## 实测数据与预报

开发时广西文件已到齐。两个文件共有 **8,448 条、176 站、48 个 UTC 时次**，范围 2026-11-03 03 至 11-09 00；无重复、无冲突。广东 86、广西 74、海南 16。相对本工作树自带的 **137 站**静态表，重合 35、新增 141，合并后 278。主管实际 Spark 活跃表还包括其他站，**不能把 278 当作线上合并后站数**。

NMIC 产生 **57,198** 行，QC 标记为 0：t/td/wind 各 8,448，msl 8,446，rain6 8,272，rain12 7,920，rain24 7,216。两个海平面气压缺测没有变成大数。当前批次 PRE_3h 没有微量/缺测码；这些边界由单元测试覆盖。见 `evidence/nmic/import.json`。

代码核读结果：EC 0.25°、EC 集合平均、GFS、ECAI、GFSAI、GEFS 和 WN3 都按传入站点坐标采样，站表增加后自然取值；原始场覆盖范围内才有值，已经裁掉或删除的历史场不能凭空补出新站。现有西部/站点扩展测试仍通过。

**EC 9 km 与任务书假设有差别**：本分支 `regional_series` 的主缓存是 0.1°、60–150°E/0–60°N 的网格，覆盖粤桂琼，可直接采新站，不需外部请求；旧 `.extended.npz` 则是按站序的点数组。新增补取工具解决扩展期和主网格已滚动删除时的点值缺口，最多到 240 h。00/12 UTC 周期补到 240 h；06/18 UTC 仍使用平台原有的最高 144 h 网格路径。本次没有扩展 06/18 的独立点下载。

读取补充点时，精确按站号+经纬度对齐，已有有效网格/旧缓存值不被覆盖。已有较长降水间隔保留，补充量先累加为同长度再填；EC 输出按原有 0–90 h 每小时、93–144 h 每 3 小时、150–240 h 每 6 小时组织，不把远期 6 h 降水拆成 3 h 评分。较旧的无证书 extended 文件通过上一已发布站表恢复站序，不能凭新数组长度猜测重排。

`wn3ml` 是**仅针对新疆指定站训练的风订正点产品**，不是可对任意新站采样的格点模式；这次不伪造其粤桂琼预报，也不扩训练。常规 8 个模式能按上述路径接入；9 个配置模式参与的模拟回放仅证明评分管线兼容，不能证明 WN3ml 的新站业务能力。

Open-Meteo [Single Runs 官方文档](https://open-meteo.com/en/docs/single-runs-api)声明 EC IFS 9 km 单起报存档从 2024-03-14 起提供。由此**推断**可回补本次约 6 天观测窗口对应的历史起报，不受 NMIC 最近 7 天限制；实际可取期仍以 API 返回为准，不承诺每一历史起报都成功。

覆盖本批次全部 240 h 验证时效的 00/12 起报计划为 **2026-10-24 12 至 11-09 00，共 32 个起报，一批 176 站，一起报一 GET，共 32 次请求**。未来每 5 天若补取滚动 10 天起报，已完成的同站表缓存会跳过。请求日志在所用缓存的 `nmic-ec01-requests.json`，失败也计数；不自动重试，不自动重置 300 次上限。站表改变会重新生成相应起报点缓存。

**本次实际预报请求 2 次**：首次直连超时，第二次经 `http://127.0.0.1:17908` 成功，均无账号/密钥。成功响应为 2026-11-03 00 起报，176 站全部取得预报，按原生降水时段处理后得到 57,198 个 NMIC 配对（各要素数量如上）。见 `evidence/nmic/ec01.json`、`ec01-requests.json`。没有执行 32 次完整回补。官方文档检索也远低于总计 300 次外部请求限制。

## 测试和等价性

本机使用已存在的 venv 和 jsdom，未安装或改动外部环境：

```bash
cd ~/vt-nmic
PY=/home/chengyang/.venvs/ec-gfs-server/bin/python
"$PY" -m unittest test_nmic -v
NODE_PATH=/tmp/st-jsdom/node_modules node --test test_*.js
"$PY" -m unittest discover -q
node --check stations.js
"$PY" -m py_compile nmic_obs.py nmic_ec01.py tools/check_nmic_equivalence.py tools/replay_nmic.py
```

- 新增 **15/15 通过**：解析、UTC、单位、缺测/未观测/保留码、微量、完整雨窗/缺口/截止边界、露点、去重、所有旧源优先、旧 rain12 不变、站点合并/首次活跃/过期、区域、旧配对评分、EC 响应验证/点重排/网格迁移/原生时段及比较器反例。
- 前端 **14/14 个测试文件通过**，jsdom 可用。
- **全量 Python 并非全绿。** 未改动时 225 项，4 failures+10 errors；加入后 240 项，仍是**完全相同的 14 项**。清单在 `evidence/nmic/tests.json`，没有新增失败。
- 原有失败原因：缺失 WN3 演示月数据/样例导致读文件或 HTTP 503；新疆 WN3 外部缓存模块没有 `surface_ws`；旧地图测试期望北界 27°而当前数据已是 33°；旧代理断言期望 17908 而当前 PROXY 为空；预览 catalog 夹具缺失。没有为让测试变绿而改动这些无关功能或跳过原测试。

实跑全链路回放命令：

```bash
# WSL 重新运行请用有写权限的 E: scratch，过程约需 2–3 GB，结束自动删除缓存。
mkdir -p /mnt/e/scratch/vt-nmic-20261111-replay
"$PY" tools/replay_nmic.py --data-dir _nmic_data \
  --scratch /mnt/e/scratch/vt-nmic-20261111-replay --output /tmp/nmic-replay.json
```

`evidence/nmic/replay.json` 的**实测受控结果**：真实 176 站坐标和观测；模拟旧源在旧站观测键上完整覆盖，风值刻意不同；9 个配置模式用确定性模拟预报，真实运行 `station_score.refresh`、SQLite 配对、评分及发布。旧站 **102,357/102,357 对全列精确相同**，修改/删除/新增旧站配对均为 0；1/3/7/30 天逐站评分分别 18,270/27,405/36,540/36,540 条，前后全部精确相同。候选总配对 514,782。临时结果已自动删除。

**未完成生产缓存等价实测**：WSL 没有 Spark 那份生产缓存，未登录 Spark。这份受控证据不替代生产对照。并且“给老站补原来没有的要素”本身可能增加老站配对、改变汇总评分；“已有配对保持”不等于“所有老站汇总绝不变化”。比较器会把这种新增单独报出，严格等价返回失败，不隐瞒它。若主管要求老站所有汇总绝对冻结，应在预览对照后另定是否禁止老站补空；本实现遵照任务的逐要素填空要求。

在**同一份冻结输入、同一 end**的 Spark 基线/候选缓存上，一条核对命令：

```bash
python tools/check_nmic_equivalence.py /path/to/nmic-baseline-cache /path/to/nmic-preview-cache \
  --output /tmp/nmic-equivalence.json
```

退出 0 要求所有基线老站的配对数、配对每列和逐站评分都相同，没有浮点容差；新增站不计入老站比较。退出 1 同时报告旧对是否被改写/删除、老站是否新增对以及评分差异。全区域汇总包含新站后本来就会变化，不作为“不变”承诺。

## Spark 预览部署与回退（交主管执行，本次未执行）

以下仅建独立预览工作树、私有缓存和 loopback 8545，不重启/修改正式站服务，不设置任何定时器。先将 bundle、两份 S*.txt 和元数据放到 Spark 的 `/tmp/nmic-inbox/`，不要把 WSL 生成的模拟缓存搬过去。

```bash
set -euo pipefail
BASE=/home/yjzx/verification-platform/verification-test
PREVIEW=/home/yjzx/verification-platform/verification-test-nmic-preview
OLD_CODE=/home/yjzx/verification-platform/verification-test-nmic-baseline
INBOX=/tmp/nmic-inbox
PY=/home/yjzx/verification-platform/venv/bin/python
git -C "$BASE" fetch "$INBOX/nmic-review.bundle" nmic-stations
git -C "$BASE" worktree add -b nmic-preview-20261111 "$PREVIEW" FETCH_HEAD
git -C "$BASE" worktree add --detach "$OLD_CODE" 1e3c717

# 私有缓存复制可能超过 5 GB：先看大小和空闲空间，确认容得下后再执行复制。
du -sh "$BASE/cache/stations"
df -h "$PREVIEW"
mkdir -p "$PREVIEW/cache/stations"
# 等待现有 station worker 释放锁；不停止定时器，不与它同时复制可变 DB。
flock -x "$BASE/cache/stations/refresh.lock" \
  cp -a --reflink=auto "$BASE/cache/stations/." "$PREVIEW/cache/stations/"
cd "$PREVIEW"
export STATIONS_CACHE_DIR="$PREVIEW/cache/stations"
export STATIONS_NATIVE_ROOT="$BASE"
export STATIONS_WEATHER_ROOT=/home/yjzx/verification-platform/gods-eye-weather/weather-data
export STATIONS_GRID_ROOT=/home/yjzx/verification-platform/ec-gfs-server
export STATIONS_GRID_EXTRA="$BASE/cache/obs-backfill/grid"
export SZ59493_OFFLINE=1
END=$("$PY" -c 'import json,os,pathlib;p=pathlib.Path(os.environ["STATIONS_CACHE_DIR"]);g=json.loads((p/"current.json").read_text())["generation"];print(json.loads((p/"snapshots"/g/"meta.json").read_text())["window_end"])')

# 原代码先按相同 end 重评分；NMIC 指向空目录，以免混入新资料。
mkdir -p "$PREVIEW/cache/nmic-empty"
STATIONS_NMIC_DIR="$PREVIEW/cache/nmic-empty" \
  "$PY" "$OLD_CODE/refresh_stations.py" --score-only --end "$END"
FROZEN="$PREVIEW/cache/nmic-baseline"
GEN=$("$PY" -c 'import json,os,pathlib;print(json.loads((pathlib.Path(os.environ["STATIONS_CACHE_DIR"])/"current.json").read_text())["generation"])')
mkdir -p "$FROZEN/snapshots"
cp "$STATIONS_CACHE_DIR/current.json" "$FROZEN/current.json"
# 已发布快照不可变，可安全硬链接保存基线，不复制第二份大 SQLite。
cp -al "$STATIONS_CACHE_DIR/snapshots/$GEN" "$FROZEN/snapshots/$GEN"

mkdir -p "$STATIONS_CACHE_DIR/raw/nmic"
cp "$INBOX"/S20*.txt "$INBOX/A0012_stations_api.json" "$STATIONS_CACHE_DIR/raw/nmic/"
export STATIONS_NMIC_DIR="$STATIONS_CACHE_DIR/raw/nmic"
"$PY" refresh_stations.py --score-only --end "$END"
"$PY" tools/check_nmic_equivalence.py "$FROZEN" "$STATIONS_CACHE_DIR" \
  --output "$PREVIEW/cache/nmic-equivalence.json"
```

对照期间需保持共享原始格点输入不变；如果其它下载器补入/修订历史场，应冻结相关源文件后重跑两侧。严格核对不通过时停在预览，先看是旧键修改、老站补空还是外部输入变化。之后可按需执行 EC 回补计划/明确 fetch，再重评分和重核对。

核对后启动只读预览：

```bash
cd "$PREVIEW"
export VERIFICATION_READ_ONLY=1 VERIFICATION_PREVIEW_DATA_ROOT="$BASE"
nohup "$PY" server.py --port 8545 >cache/nmic-preview-server.log 2>&1 &
echo $! >cache/nmic-preview-server.pid
curl -fsS http://127.0.0.1:8545/Verification/test/api/stations/meta >/tmp/nmic-preview-meta.json
```

回退只针对此次预览；正式站无需回退，因为未变更。先停止预览进程（先核对 PID 对应本预览），随后可移除独立工作树及其私有缓存。下列工作树移除包含大缓存删除，执行前确认 PREVIEW 值。

```bash
ps -fp "$(cat "$PREVIEW/cache/nmic-preview-server.pid")"
kill "$(cat "$PREVIEW/cache/nmic-preview-server.pid")"
git -C "$BASE" worktree remove --force "$PREVIEW"
git -C "$BASE" worktree remove "$OLD_CODE"
git -C "$BASE" branch -D nmic-preview-20261111
```

## 以后每 5 天更新

由主管沿现有半自动流程获取文件，保留以往订单文件，不覆盖不同时间范围。把新文件先放目录外，复制完后再原子移入配置的 NMIC 目录；元数据同目录保留。无需改站表、控件、HUANAN_IDS 或定时器。

```bash
cd "$PREVIEW"
export STATIONS_CACHE_DIR="$PREVIEW/cache/stations"
export STATIONS_NMIC_DIR="$STATIONS_CACHE_DIR/raw/nmic"
export SZ59493_OFFLINE=1
# 只取新观测评分，不触发常规观测下载。
"$PY" refresh_stations.py --score-only

# 可选补点；先查看数量，再显式取数。日期换成需要回补的 00/12 起报窗口。
"$PY" nmic_ec01.py --start 2026-10-24T12:00:00Z --end 2026-11-09T00:00:00Z
OPENMETEO_EC_PROXY=http://127.0.0.1:17908 \
  "$PY" nmic_ec01.py --start 2026-10-24T12:00:00Z --end 2026-11-09T00:00:00Z --fetch
"$PY" refresh_stations.py --score-only
```

请求 cap 是同一缓存累计 300 次；到限额先评估计划，不要为了继续下载删日志。普通评分完全离线读取 NMIC，EC fetch 与 station worker 共用 refresh.lock 防止同时写缓存。平台仍只有 31 天评分窗口，原始 NMIC 文件可长期留存，若要展示更早评分需另行设计历史回算，不在本次扩展范围。

## 遗留与清理

需主管实跑生产缓存严格等价核对、确认新增老站空缺对汇总的影响，并审核 14 项原有测试失败；06/18 独立点补取及 WN3ml 新站订正未做。样例 readme 不含时制，若后续正式产品说明与当前 UTC 证据矛盾，应先复核再改口径。省码索引覆盖当前三省完整 176 站；若将来增加其它省或新设国家站，需要随元数据更新索引，不能按经纬度粗框猜省份。

本次没有保留实验缓存或大备份：全链路临时缓存结束自动删除，EC 下载临时 NPZ 验证后删除。`evidence/nmic/` 仅小型 JSON；`.nmic-git/` 约 1.5 MB 和 `nmic-review.bundle` 为交付提交所需，保留。没有在 E: 留下任务副本，也没有复制任何生产数据目录到 WSL home。
