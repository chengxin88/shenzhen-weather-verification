# Spark 预览（仅操作独立目录，端口 8533）

本说明未在 Spark 执行。生产仓库为 `/home/yjzx/verification-platform/verification-test`，服务 `server.py --port 8513`。下面不停止服务、不操作 systemd、不向生产缓存写文件。所有重算只写 `previews/guifan-20261109/state`，输入场只读引用生产目录。耗时/空间为估计，须以 Spark 的 `du`、`df` 和 `/usr/bin/time -v` 为准。

本次 WSL 会话的 `.git` 只读，提交在独立 Git 元数据中完成，交付 `guifan-scores.bundle`（分支 `guifan-scores`，依赖 Spark 现有提交 `44e7726`，包含任务说明提交及3个实现提交）。先把 bundle 放到 Spark 的 `/tmp/guifan-scores.bundle`。若通过普通 Git 已取得全部提交，可直接从 `guifan-scores` 建 worktree，跳过 fetch。

## 1. 环境与 worktree（约 1 分钟，代码不足 20 MB）

在 Spark 的 bash 中逐段运行；以下变量需保留在同一 shell：

```bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export PROD=/home/yjzx/verification-platform/verification-test
export PREVIEW=/home/yjzx/verification-platform/previews/guifan-20261109
export PY=/home/yjzx/verification-platform/venv/bin/python
set -a
source /home/yjzx/verification-platform/deploy/env
set +a
# 仅记录路径，不打印 env 全文（其中可能有凭据）。
export PROD_STATIONS=${STATIONS_CACHE_DIR:-/home/yjzx/verification-platform/cache/stations}
export PROD_NATIVE=${NATIVE_SCORES_PATH:-$PROD/native-scores.json}
export PROD_OM=$(cd "$PROD" && "$PY" -c 'import openmeteo_ecmwf as ec; print(ec.ROOT)')
export PROD_OM_SCORE=${OPENMETEO_SCORE_ROOT:-$PROD_OM}
export VERIFICATION_APP_ROOT=${VERIFICATION_APP_ROOT:-/home/yjzx/verification-platform/ec-gfs-server}
export NATIVE_WEATHER_ROOT=${NATIVE_WEATHER_ROOT:-/home/yjzx/verification-platform/gods-eye-weather/weather-data}
# 新目录必须不存在；避免覆盖上一次预览。
test ! -e "$PREVIEW"
mkdir -p "$PREVIEW/old" "$PREVIEW/state/native" "$PREVIEW/state/openmeteo"
git -C "$PROD" bundle verify /tmp/guifan-scores.bundle
git -C "$PROD" fetch /tmp/guifan-scores.bundle guifan-scores:refs/heads/guifan-preview-source-20261109
git -C "$PROD" worktree add -b guifan-preview-20261109 "$PREVIEW/code" guifan-preview-source-20261109
cd "$PREVIEW/code"
# 两个既有只读适配器的符号链接在 Spark 应当可用。
test -f openmeteo_ecmwf.py
test -f openmeteo_regional.py
"$PY" -c 'import native_archive,openmeteo_verification; print("adapters OK")'
```

`openmeteo_verification.py` 已纳入版本控制；原来是绝对路径 symlink。本次基于 WSL 的 `openmeteo-ecmwf` 同项目实现，保留原评分运算。上线前应复核 Spark 原适配器与该文件的其他逻辑差异；不覆盖外部 `openmeteo-ecmwf` 项目。

## 2. 空间检查和一致的站点基线（通常 1–5 分钟）

不复制 forecasts、NPZ station samples、weather-data 等大型数据目录。只复制当前已发布快照、观测档案和必要评分 memo。站点预计每份 0.5–3 GB；old/new 加上迁移保留的两代快照，峰值约为当前快照的 4–6 倍加两份 obs。native/Open-Meteo 评分 memo 和 catalog 通常几十到数百 MB，原始场零复制。先根据下面实际尺寸核算；预计总写入超过 5 GB 时，先确认磁盘可用空间足够，再执行复制。

```bash
df -h "$PREVIEW"
du -sh "$PROD_STATIONS/snapshots" "$PROD_STATIONS/obs" "$PROD_NATIVE" "$PROD_OM_SCORE/score-memo.json" "$PROD_OM_SCORE/verification-catalog.json"
# 锁文件用只读 FD 打开；拿不到锁时本段失败，等现有 worker 完成再重试本段。
(
  flock -s -n 9
  generation=$($PY -c 'import json,os; from pathlib import Path; print(json.loads((Path(os.environ["PROD_STATIONS"])/"current.json").read_text())["generation"])')
  mkdir -p "$PREVIEW/old/stations/snapshots"
  cp -a --reflink=auto "$PROD_STATIONS/snapshots/$generation" "$PREVIEW/old/stations/snapshots/"
  cp -a "$PROD_STATIONS/current.json" "$PREVIEW/old/stations/"
  cp -a --reflink=auto "$PROD_STATIONS/obs" "$PREVIEW/old/stations/"
) 9<"$PROD_STATIONS/refresh.lock"
cp -a --reflink=auto "$PREVIEW/old/stations" "$PREVIEW/state/stations"
# 使用同一已发布 pairs 作为 working，避免复制到生产尚未发布的增量事务。
generation=$($PY -c 'import json,os; from pathlib import Path; print(json.loads((Path(os.environ["PREVIEW"])/"state/stations/current.json").read_text())["generation"])')
cp -a --reflink=auto "$PREVIEW/state/stations/snapshots/$generation/pairs.sqlite" "$PREVIEW/state/stations/working.sqlite"
cp -a "$PROD_NATIVE" "$PREVIEW/state/native/native-scores.json"
cp -a "$(dirname "$PROD_NATIVE")/native-catalog.json" "$PREVIEW/old/native-catalog.json"
cp -a "$PROD_OM_SCORE/score-memo.json" "$PREVIEW/state/openmeteo/"
cp -a "$PROD_OM_SCORE/verification-catalog.json" "$PREVIEW/old/openmeteo-catalog.json"
```

从 8513 读取所有格点目录行，保存实际生产基线；仅本机 GET，不触发 refresh。rows 请求绑定同一 snapshot，409 则重做导出，不混用不同代。

```bash
"$PY" - <<'PY'
import json, os, urllib.parse, urllib.request
from pathlib import Path
base='http://127.0.0.1:8513/Verification/test/api/'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open(base+path,timeout=120) as r:return json.load(r)
value=get('catalog')
if 'rows' not in value:
    value['rows']=[]
    for dataset in value['datasets']:
        query=urllib.parse.urlencode(dict(dataset=dataset['id'],snapshot=value['snapshot_id']))
        value['rows'].extend(get('rows?'+query)['rows'])
(Path(os.environ['PREVIEW'])/'old/catalog.json').write_text(json.dumps(value,ensure_ascii=False,allow_nan=False))
print('exported',len(value['rows']),'rows')
PY
```

## 3. 在预览缓存重算（站点约 3–15 分钟；格点约 5–30 分钟）

站点迁移从保存的 pairs 回算全部仍保留的历史；从 obs 档案修正同值、同来源观测的 trace。它不下载、不读取 forecast samples、不重新插值，不改变 fc/ob。使用 worker 同一个 flock、dirty_days 和原子快照发布方式。中断后重复同一命令即可；不要手动替换 current.json。

```bash
export STATIONS_CACHE_DIR="$PREVIEW/state/stations"
export NATIVE_SCORES_PATH="$PREVIEW/state/native/native-scores.json"
export NATIVE_AUX_ROOT="$PROD"
export VERIFICATION_AUX_ROOT="$PROD"
# 原始 Open-Meteo 缓存只读引用；所有评分 memo/catalog 单独写。
export OPENMETEO_EC_ROOT="$PROD_OM"
export OPENMETEO_SCORE_ROOT="$PREVIEW/state/openmeteo"
export GUIFAN_SCORE_END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
unset VERIFICATION_FROZEN_CATALOG
/usr/bin/time -v "$PY" tools/migrate_guifan.py > "$PREVIEW/migration.log" 2>&1
/usr/bin/time -v "$PY" native_archive.py > "$PREVIEW/native.log" 2>&1
/usr/bin/time -v "$PY" openmeteo_verification.py > "$PREVIEW/openmeteo.log" 2>&1
"$PY" tools/merge_guifan_catalog.py \
  --base "$PREVIEW/old/catalog.json" \
  --native "$PREVIEW/state/native/native-catalog.json" \
  --openmeteo "$PREVIEW/state/openmeteo/verification-catalog.json" \
  --output "$PREVIEW/state/native-catalog.json"
```

`GUIFAN_SCORE_END` 固定两个格点评分的窗口终点，不改变旧行数值。站点迁移保持原 snapshot 的 window_end。新增档位和分级增加评分行数；daily SQLite 新增 23 个整数计数列（当前表 affinity 仍为 REAL，值保持精确整数），预计比分级前体积增加 20–50%，以实测为准。迁移峰值包含 `.incomplete-*`，完成后只保留两代完整快照。

## 4. 只读核对（约 1–5 分钟）

```bash
"$PY" tools/check_guifan.py "$PREVIEW/old/stations" "$PREVIEW/state/stations" --sample 64 \
  > "$PREVIEW/check-stations.log"
"$PY" tools/check_guifan.py "$PREVIEW/old/native-catalog.json" "$PREVIEW/state/native/native-catalog.json" \
  > "$PREVIEW/check-native.log"
"$PY" tools/check_guifan.py "$PREVIEW/old/openmeteo-catalog.json" "$PREVIEW/state/openmeteo/verification-catalog.json" \
  > "$PREVIEW/check-openmeteo.log"
"$PY" tools/check_guifan.py "$PREVIEW/old/catalog.json" "$PREVIEW/state/native-catalog.json" \
  > "$PREVIEW/check-catalog.log"
cat "$PREVIEW"/check-*.log
```

必须都是 PASS。旧指标比较无浮点容差，pairs 的 fc/ob 等旧字段逐值核对（trace 的预期修复单独处理）。抽样按完整 forecast group 独立遍历 pairs，比较 N1/N2/T 和分级。`--sample 0` 仅关闭独立抽样，不关闭旧值比较/自洽检查。

表3-1 每对只记一个 A/B/C，故未加跨级项的 `Σ(A+C)=实况有雨`、`Σ(A+B)=预报有雨` 不成立；工具检查带 GO/GU 补项的正确恒等式以及最高档累加计数与第6级一致。参见报告。

原始输入仍可能被生产任务更新或清理；这类变动会让格点旧值比较失败。不得放宽比较容差；用固定的输入副本在预览目录重做两份产物并查明差异后再评审。源文件已清理的 memo 行会原样保留，所以它们的新指标显示 —，不会假装已补齐。

## 5. 启动 8533（前台，无 systemd）

```bash
export VERIFICATION_READ_ONLY=1
export VERIFICATION_PREVIEW_DATA_ROOT="$PREVIEW/state"
unset VERIFICATION_FROZEN_CATALOG
# 当前目录必须是 worktree，HTTP 临时 catalog 只写这里的 cache/catalog-http。
cd "$PREVIEW/code"
"$PY" server.py --port 8533
```

浏览 `http://127.0.0.1:8533/Verification/test/`，按已有访问方式转发 8533。服务默认监听地址沿用原 server.py。另一个终端只读检查：

```bash
curl -fsS http://127.0.0.1:8533/Verification/test/api/catalog >/dev/null
curl -fsS http://127.0.0.1:8533/Verification/test/api/stations/grid-datasets >/dev/null
```

人工复核气温准确率、PC 阈值禁用、3/24小时分级、分级综合图、站点与格点模式、地图、单站卡、CSV，以及旧 lead/weekly 通道的能力回退提示。预览服务使用冻结目录，不自动刷新格点评分；要更新需重复第3–4步，之后重启该前台预览进程。生产 8513 不受影响。

## 生产上线（仅步骤，本次不执行）

1. 审核提交、真实数据核对日志和 8533 页面；确认 Spark 外部 Open-Meteo 适配器差异已审查。不要把预览合成数据部署到生产。
2. 等现有 station/native/Open-Meteo 评分任务空闲，在已有部署流程中串行迁移；先备份现有产物、current 指针和 working SQLite。生产备份另选有空间的目录，核算 4–6 倍当前快照的峰值。
3. 将 `guifan-scores` 提交合入当时的生产分支；保持外部两个 Open-Meteo 只读 adapter symlink。外部评分调度如果仍执行 `openmeteo-ecmwf/openmeteo_verification.py`，需改为执行本仓库的 `openmeteo_verification.py`（否则该路新计数不会持续生成）。保留原 OPENMETEO_EC_ROOT，评分默认也写该 root；可显式配置 OPENMETEO_SCORE_ROOT。
4. 在生产环境运行站点一次性迁移；运行 native 与本仓库 Open-Meteo scorer；再次按同一输入核对旧值和新计数。常规 `refresh_stations.py --score-only` 也会修复 trace 并重建新快照，但一次性迁移更易证明 fc/ob 未变化。
5. 按既有部署流程加载新前端/后端，检查生产目录。若使用 VERIFICATION_FROZEN_CATALOG，新 spark_overlay 已将 grade 纳入键，不能只部署评分脚本。回退时回退代码并恢复完整旧快照/current/working，不把新旧半代产物拼起来。
6. 评审完成后停止 8533；确认无需保留预览基线再删除整个 PREVIEW，最后用 `git worktree remove` 清理 worktree。原始生产缓存从未复制到 WSL。
