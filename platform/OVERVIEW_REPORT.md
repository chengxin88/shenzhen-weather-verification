# 考核总览交付报告

2026-11-09。基于 `b43527a`，新增默认首页 `#overview` 和左侧首个导航入口；保留原页面路由。总览只有要素、时效、日期三个选择，默认降水、第1天、近7天。采用现有样式，手机宽表在卡片内滚动。

降水显示晴雨准确率及分级 TS；大暴雨/特大暴雨仅在窗口中实况或预报出现时显示。无分级事件显示「—」，只有空报显示 `0.00`。气温按计数合并准确率及绝对误差；各列最佳与并列最佳加粗。详细分析隐藏“检验方式”，强制累加口径，分级行不会进入图表和表格；原准确率指标选项保留。未修改任何评分生产者或现有指标算法。

## 只读接口

`GET /Verification/test/api/stations/overview?element=rain|t&day=1|2|3&days=3|7|30`

默认 `rain / 1 / 7`；未知、重复、空值参数返回 400。沿用 server 的站点接口路由，`VERIFICATION_READ_ONLY=1` 下可用。

返回主要字段：

- `state`、`generation`、`element`、`day`、`days`。
- `window_start/window_end`：UTC 快照窗口；`start/end`：对应北京时；`coverage_start/coverage_end`：实际配对覆盖的 UTC 时间。
- `station_count`：此窗口、天次、要素中有配对的华南站点并集，排除新疆3站。
- `grades`：显示的量级列表，含 `id/label/range/observed`；`observed` 为跨模式去重后的实况站次（站点×有效时刻），不由分级 A+C 推测。
- `rows`：按平台模式顺序排列，无配对模式省略。降水含 `model/label/n/H/M/F/T/PC` 及六级 `grades[{grade,A,B,C,TS}]`；无分母 `TS=null`。气温含 `model/label/n/N1/N2/absolute/ACC1/ACC2/MAE`；准确率均为 0–1。

仅打开 `STATIONS_CACHE_DIR/current.json` 指向的同一代快照。指标优先读取 `scores-{days}.json`；缺失或旧计数不完整时，SQLite 只读汇总已有 pairs，不重算配对。实际站数、实况站次和极端量级出现情况查询同代 `pairs.sqlite`。缓存键包含快照绝对路径（含 generation）、要素、天次、天数，最多54项；不会混用其他缓存根或旧 generation。

降水取 lead=24/48/72 的 rain24；气温分别取 `(0,24] / (24,48] / (48,72]`。日期边界沿用离线窗口的 `[window_end−days, window_end]`。气温用 ΣN2/Σn、ΣN1/Σn 和 Σ(MAE×n)/Σn，SQLite 回退直接汇总绝对误差。

## 验证

机器可读比较见 [tools/overview_test_results.json](tools/overview_test_results.json)，基线为原 [tools/guifan_test_results.json](tools/guifan_test_results.json)。

| 检查 | 本次结果 | 相对基线新增失败 |
|---|---|---|
| Python 顶层全量 | 205项：191通过、4失败、10错误 | 0（失败ID集合一致） |
| Node 全部 test_*.js | 11文件：9通过、2失败 | 0（失败文件一致） |
| incremental-qc | 6项通过 | 0 |
| 新接口测试 | 10项通过 | 0 |
| 总览与规范 jsdom 专项 | 2文件通过 | 0 |

新增测试覆盖天次边界、按计数合并、空值与零分、微量、大暴雨/特大暴雨显隐（含跨级低报）、排除新疆、模式顺序与缺样本省略、快照缓存失效、SQLite 回退等价、只读 HTTP 和静态脚本。前端实际驱动默认路由、三个控件参数、最佳/并列/全空值、详情跳转、过期响应丢弃、空资料与错误恢复；原规范测试按新要求改为确认详细页不提供分级控件，保留核心分级计算和准确率验证。

本机 Chrome 用合成资料检查 320/390/1440px：页面宽度分别等于视口；320/390px 卡片内宽270/340px、表格宽820px，溢出只在表格容器中。缓存命中测试连续50次请求合计小于1秒，并断言不再打开 SQLite。未访问生产数据，未测生产快照首次查询耗时；真实资料覆盖与冷请求性能待部署环境核实。

复现：

```bash
/home/chengyang/.venvs/ec-gfs-server/bin/python -m unittest discover
/home/chengyang/.venvs/ec-gfs-server/bin/python -m unittest discover -s incremental-qc
NODE_PATH=/tmp/guifan-jsdom/node_modules node --test test_*.js
```

## 交付与限制

环境将仓库 `.git` 设为只读，提交保存在 `/tmp/guifan-overview-git` 的 `guifan-overview` 分支，并打包为仓库根目录的 `guifan-overview.bundle`，唯一前置提交为 `b43527a`；原只读 HEAD 未移动。按接口、界面、验证收尾分块提交，提交信息均为英文。导入示例：

```bash
git fetch ./guifan-overview.bundle guifan-overview:guifan-overview
```

若目标已存在同名分支，先检查并选用其他本地分支名，避免覆盖已有提交。

详情链接带要素、站点实况模式和天数；降水另带24小时累计和对应单时效。气温详情原控件不支持第2/3天这种独立24小时时效组合，因此不新增控件、不声称详情与总览的合并范围完全相同；详情日期继续使用原有北京时日历窗口，总览使用离线评分的精确时间窗口。

没有 SSH、外网访问或服务操作；只使用合成快照和本机临时测试端口。未复制生产缓存。保留 `/tmp/guifan-overview-git` 约1.2MB用于提交审查、`/tmp/guifan-overview-{python,node,qc}.log` 合计不足50KB用于复查；浏览器临时资料已清理。原有 `/tmp/guifan-jsdom` 为任务指定测试依赖，继续保留。
