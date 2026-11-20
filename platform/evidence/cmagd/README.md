# CMA-GD 核验证据

- `acceptance-tests.txt`：最终 73 项 Python 测试全部通过。
- `frontend-tests.txt`：无第三方依赖的真实地图渲染及选项/说明检查。
- `stations-tests.txt`：原有 6 项失败，未增加。
- `baseline-extra-tests.txt`：扩查时两个失败在 602f40e 基线上的重现。
- `real-equivalence.json`：真实 34 帧/两日实况 A/B，旧站、旧区域全部产品零差异。
- `final-pairs.json`：最终实现重新生成全部 317470 配对，与真实重放库逐列精确相同。
- `real-*-measurement.json`：WSL E 盘冷跑计时与峰值内存；不是 Spark 亿级验收。
- `requests.json`：请求计划和原站点参数完全一致；审计不联网。
- `coverage.csv/json`、`import-summary.json`：原 ZIP 整理、逐时次覆盖、修订与散列。
- `regions.json`：真实站表的扩展标记和区域成员数量。
- `scale.json`：完整 16 天、26 天起报范围的配对器枚举上界。
- `field-read.json`：单场 mmap/顺序读取耗时，数值精确相同。
- `harness-*.json`：完整副本工具的首轮及三小时常规轮小样本冒烟。
- `spark-*.json`：只读取得的正式轮阶段计时。
- `prior-2100-benchmark.json`：既有 2100 站块刷新基准，只读引用，未冒充本次实测。
- `scratch-retained.json`：清理时保留项、大小和用途。
