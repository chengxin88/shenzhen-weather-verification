# 移除时效分组检验报告

已完成，仅修改工作区，未执行 git commit；未使用 SSH 或外网，未修改 Python 后端和生产缓存。

改动文件：

- `index.html`、`app.js`：删除分组切换、说明、覆盖提示、分组快捷按钮及汇总逻辑；保留单时效按钮与图表旁快捷切换。全部时效视图隐藏整个“预报时效”栏，不留空标签。加载时清理全局和各数据集的旧 `leadMode` / `leadGroup` 偏好，普通视图首次站点请求也只带当前时效；CSV 删除分组字段。
- `grid-stations.js`：删除单站详情分组按钮，预报实况曲线跟随主图当前所选时效，保留多模式曲线、图例和地图交互。
- `stations.js`：独立站点页删除时效范围分组选项及详情分组按钮；逐时效对比展示全部时效，详情通过现有接口展示本站全部可用时效的预报均值与实况。
- `core.js`、`style.css`、`stations.css`：删除无人使用的时效分组工具和样式，保留现有评分函数。
- `test_grid_stations_frontend.js`、`test_station_selection.js`、`test_stations_frontend.js`、`test_surface_stations.js`：替换旧分组用例，验证无分组按钮、旧偏好只请求所选时效、单站出图、全部时效视图及 CSV；同步修正旧测试中的地图按钮位置、资料说明位置和异步等待。

界面由“单个时效 / 分组检验”改为直接选择单个时效；“时效对比 / 时效 × 阈值 / 时间序列·多时效”继续展示全部时效，没有增加控件。对前端源码检索“分组 / leadGroup / lead-group / 临近预报”，仅剩清理旧偏好的两行 `delete ...leadGroup`；界面无相关按钮或文字。

前端测试逐个执行，合计 **11 个文件通过、0 个失败**（表中按文件计数；独立站点测试还会调用站点卡片测试）：

| 测试命令 | 通过 / 失败 |
|---|---|
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_available_models.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_demo_core.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_grid_stations_frontend.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_guifan_frontend.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_initial_kind_switch.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_overview_frontend.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_slim_core.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_station_selection.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_station_systems.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_stations_frontend.js` | 1 / 0 |
| `NODE_PATH=/tmp/guifan-jsdom/node_modules node test_surface_stations.js` | 1 / 0 |

Python 测试命令：`python3 -m pytest -q test_guifan_scores.py test_station_overview.py`，结果 **19 通过、4 失败**。4 项均在导入时因当前 `python3` 缺少 `shapely` 失败，未到评分断言；未安装依赖或改动后端：

- `test_guifan_scores.py::StationProducts::test_native_original_scores_are_exact`
- `test_guifan_scores.py::CacheAndPreview::test_native_pruned_rows_survive_signature_bump`
- `test_guifan_scores.py::CacheAndPreview::test_openmeteo_bins_and_pruned_memo_survive_revision`
- `test_guifan_scores.py::CacheAndPreview::test_openmeteo_recomputes_counts_and_keeps_legacy_thresholds`

另执行 `node --check app.js`、`node --check core.js`、`node --check grid-stations.js`、`node --check stations.js` 和 `git diff --check`，均通过。无保留的大型实验副本或临时产物。
