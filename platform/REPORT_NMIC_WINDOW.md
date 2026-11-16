# 国家站资料截止窗口交付报告

2026-11-11，Codex，WSL `/home/chengyang/vt-window`。

实现、离线验证和交付已完成；未登录 Spark、未 push、未部署，未访问气象资料网站或使用账号、密钥。没有修改真实缓存、重新配对或重新计算真实评分，也没有发送企业微信。

## 提交与导入

原分支为 `nmic-window-20261111`，原 HEAD 为任务书提交 `df70025`，业务基线为 `dc7b05b`。实际执行 `git add` 仍报：

```text
Unable to create '/home/chengyang/vt-guifan/.git/worktrees/vt-window/index.lock': Read-only file system
```

按任务书的备用交付方式，中文提交保存在本工作目录的独立 Git 库 `.nmic-window-git/`，同名分支导出为 **`nmic-window.bundle`**。原工作树 HEAD 没有被更新。实现提交：

- `972de85` 按快照国家站占比调整区域总览截止时间。
- `87a7416` 站点实况默认日期和快捷窗口跟随国家站截止日。
- `edf0718` 保留详细分析既有区域取数口径。
- 最后一条提交收录报告、验证证据及完成标记，用下列命令查看。

```bash
git --git-dir=.nmic-window-git --work-tree=. log -4 --oneline
git bundle verify nmic-window.bundle
```

bundle 的先决提交是 `dc7b05b`，包含任务书提交和上述开发提交。在有该先决提交的审核仓库中，可创建独立审核分支：

```bash
git fetch /path/to/nmic-window.bundle nmic-window-20261111
git switch -c review-nmic-window FETCH_HEAD
```

以上导入命令交主管执行，本次未执行任何线上操作。

## 文件与规则

| 文件 | 改动 |
| --- | --- |
| `station_score.py` | 仅增加快照字段 `nmic_end`：从当次已完成 QC、来源选择的 `observations` 中，取 `source == 'nmic'` 的最晚有效 UTC 时间；没有则为 `null`。其他评分、配对代码没有改动。 |
| `station_api.py` | 增加过半判定、有效截止计算；调整总览查询起止，实际调整时才返回 `data_end_reason: 'nmic'`；`meta` 和已有 `grid-datasets` 带出 `nmic_end`、`region_nmic_majority`。数据集同时带出所属快照的 `window_end`，包括地面要素回退快照。 |
| `app.js` | 共用日期控件按有效截止的北京时间日期设默认范围和 3/7/30 天快捷范围；切换区域时，自动范围重新取锚点，手动范围保留；新快照资料日期更新后，自动范围随之更新；在已有 `filter-note` 说明位置换行提示。 |
| `overview.js` | 日期沿用接口实际起止；仅在截止调整后追加「国家站资料到 11 月 9 日，窗口截止到这一天（北京时间）。」 |
| `index.html` | 同步更新修改过的 `app.js`、`overview.js` 的 `?v=`。 |
| `test_nmic_window.py` | 8 项离线后端测试，使用小型临时 SQLite/JSON 夹具。 |
| `test_nmic_window_frontend.js` | 2 项 jsdom 测试，覆盖总览说明和详细分析共用日期/地图。 |
| `tools/check_nmic_window.py` | 给定缓存目录，固定读取一个已发布快照，打印区域规则、资料截止和总览窗口/站数；只读、不联网。 |
| `.gitignore` | 排除独立 Git 库及交付 bundle。 |
| `evidence/nmic-window/` | 测试日志、结构化结果、基线逐值核对、核对工具夹具输出和最终完成标记。 |

区域成员复用 `region_stations` 的现有规则；南方地区的分母按其原有评分范围排除新疆。成员中 `sources` 含 `nmic` 的站数乘 2 **严格大于**该区域站数才成立，混合来源也计为国家站；空区域、恰好一半均不成立，没有硬编码 `hn`/`gba`。有效截止为 `min(window_end, nmic_end)`；旧快照缺字段或为 `null` 时沿用原截止，`nmic_end` 相等或更晚时也不调整、不显示调整原因。

总览使用原来的闭区间 `[截止 − N 天, 截止]`；详细分析仍按原有日历口径选 **N 个北京时间日期**（起始日为结束日减 N−1）。接口请求不会扫描原始文本，也没有新接口或新控件。站点实况图表与分布图共用 `app.js` 日期，因此 `stations.js`、`grid-stations.js` 不需要改动。

详细分析的提示明确说「默认和快捷日期窗口截止到这一天」，避免把手动选到更晚日期误说成实际窗口也已被截断。日期上限不设为国家站截止日；未调整区域的文字和原日期算法保持。个例、台风、月降水、临近降水、质控的实现均未改动。

## 实测验证

运行环境使用已有 venv 和 jsdom，没有安装新环境。主要命令：

```bash
PY=/home/chengyang/.venvs/ec-gfs-server/bin/python
"$PY" -m unittest test_nmic_window -v
"$PY" -m unittest discover -q
NODE_PATH=/tmp/st-jsdom/node_modules node --test test_nmic_window_frontend.js
NODE_PATH=/tmp/st-jsdom/node_modules node --test test_*.js
node --check app.js
node --check overview.js
"$PY" -m py_compile station_api.py station_score.py test_nmic_window.py tools/check_nmic_window.py
git diff --check
```

- 新增后端 **8/8 通过**：动态区域过半/恰好一半/混合源/空区域、有效截止四种情况、雨量和气温窗口配对边界、未调整区域逐值一致、旧快照兼容、不同数据集快照的元数据、手动日期、发布时的最新有效 NMIC 时刻及空值。
- 全量 Python **252 项，4 failures + 10 errors，3 skipped**。失败名称与 `evidence/nmic/tests.json` 的原 14 项逐项一致，**新增失败为 0**。见 `python-tests.json`、`python-tests.log`。本基线在本次新增 8 项前为 244 项，前一接入报告中的 240 项属于更早基线。
- jsdom 新增 **2/2 通过**；覆盖截止 UTC 11 月 8 日 18 时对应北京时间 11 月 9 日，3/7/30 天快捷范围、区域切换恢复、手动日期、地图及序列请求日期、新快照推进日期、缺失/更晚的 NMIC 截止、不增日期控件。
- 全部 **15 个离线前端文件通过**，Node 汇总为 **16 项通过、0 失败**，包括本次新增文件。见 `frontend-tests.json`、`frontend-tests.log`。
- **通配符前端命令不能宣称全绿**：还有一个既有 `test_case_page_frontend.js`，需要真实的 `http://127.0.0.1:8543/Verification/test/` 个例回放服务；本机连接被拒绝，完整通配符运行等待该服务，已停止。它只加载未改动的 `case_cache.js`、`case.js`，不加载本次修改的 `app.js`/`overview.js`。未为通过该测试而修改个例代码或伪造其真实回放资料。离线全量命令为：

```bash
NODE_PATH=/tmp/st-jsdom/node_modules node --test \
  $(rg --files -g 'test_*.js' | rg -v '^test_case_page_frontend\.js$' | sort)
```

与 **`git show dc7b05b:station_api.py` 的原实现**另外做了 **90 组完整返回值及 JSON 序列化字节相等核对**：`all/hk/sz/xj` 的 SQLite 路径各 18 组，以及默认 `all` 的已有 `scores-N.json` 路径 18 组（气温/降水 × 第 1/2/3 天 × 3/7/30 天）。使用合成快照，其中这些区域均不满足国家站过半；所有结果完全相同。见 `equivalence.json`，不是仅比较分数的近似相等。

另跑了本次 8 项和既有 `test_station_grid`，结果在 `targeted-tests.log`；语法和空白检查通过。小夹具的完整评分发布仅用于测试 `nmic_end` 写入，不涉及真实缓存。

## 主管可直接运行的核对命令

在本分支代码目录，用生产环境已有 Python，将参数替换成**含 `current.json` 的缓存目录**：

```bash
python tools/check_nmic_window.py /path/to/cache/stations --days 3 7 30
```

输出同一代快照的 `nmic_end`、`window_end`、六个区域是否国家站过半及有效截止，以及 `hn`/`gba`/`all` 的 3/7/30 天总览 UTC 起止、北京时间起止和实际有配对的站数。默认降水、第 1 天；气温可加 `--element t`，第 2/3 天用 `--day 2` / `--day 3`。只读取已发布产品和配对，既不发布新快照，也不调用评分生产函数。`check-fixture.json` 是该工具在离线夹具上的实测输出。

**推断/待生产确认**：任务书给定的实际活跃站表应当只有 `hn`、`gba` 满足过半，`all/sz/hk/xj` 不满足。本机没有 Spark 生产缓存，因此没有实测其区域站数和真实总览分数，应由主管用上述命令在预览核对。新字段会在以后正常评分发布时产生；本次不改写旧快照。旧快照没有 `nmic_end` 时保持原行为，不能据此误判新规则失效。

## 已知限制与清理

1. 快照仍只保留原有 31 天配对；国家站滞后时，30 天窗口前移后的早期日期可能超出已保留资料。日期显示完整前移范围，但早期资料不会补造，按实际配对计分。
2. 总览按精确 UTC 时刻截止；详细分析仍按整北京时间日期筛选，截止当天晚于国家站最新时刻的旧来源配对仍可能存在。这是任务要求的「结束日」口径，未改成精确时刻截断，也保留手动选晚日期。
3. 原 14 项 Python 失败、缺少本机真实个例回放服务、原工作树 Git 元数据只读，均如实记录；前两项不因本次实现新增，Git 以允许的 bundle 方式交付。
4. 临时测试缓存均自动删除，未复制任何完整数据/缓存目录，未保留 E: 实验副本。保留 `.nmic-window-git/` 约 3.2 MB（中文提交及后续导出所需）、`nmic-window.bundle`（约几十 KB，审核导入所需）和约 0.1 MB 的小型证据；没有大型 scratch 输出。

所有代码、报告、证据和 bundle 校验完成后，最后落盘 `evidence/nmic-window/complete.json`，内容为 `{"status":"done"}`。
