# 个例检验页改版第一阶段报告

日期：2026-11-11。起始分支：`case-page-20261111`，起始提交：`8062746`（任务书提交，其父提交为正式站 `1e3c717`）。

功能、验证和截图已完成。**当前仓库的 Git 提交被会话文件系统限制阻挡**：`.git` 被挂载为只读，`git add` 返回 `Unable to create .../.git/index.lock: Read-only file system`。没有改写或绕过该目录权限。使用 `/tmp` 中独立的 Git 元数据准备同名分支上的中文提交，并交付根目录 `case-page-20261111.bundle`；当前工作区保留全部修改，原仓库分支指针仍为 `8062746`。`complete.json` 因此报告 `blocked`，阻挡项仅为无法更新原仓库分支。未 push、未合并、未部署，未在 Spark 改文件或起服务。

## 实现内容

- `case_api.py` 顶部 `CASES` 登记个例，目录增加 `cases`、当前 `case`；前端从目录读取个例和默认起报，不再写死起报 ID。请求中的可选 `case` 会校验。
- 新增 `POST api/case/fields`，沿用评分条件并增加 UTC 有效时间 `valid`。始终返回所选第一个起报、市界内目标格点、实况和各模式配对值，以及仓库已有的市界轮廓；缺测和未纳入配对的模式格点为 `null`。共同样本时实况也应用同一掩码。多段累计、插值、目标格点分别复用原引擎的 `get_frame`、`combined_values`、`city_points`，不另写插值算法。
- 仅降水显示落区对比：实况加已选模式，按原模式顺序和颜色排列，时段来自 `run_coverage`。显示当前起报、累计时长、当前阈值下逐时次 TS、面雨量，以及实况最大值；2/6/12/24 小时使用对应降水分级图例。
- 评分增加“各模式对比／随时效”。逐时次 POD、FAR、BIAS 从 H/M/F/C 现算；零分母和缺测断线。悬停一个时效能查看各模式／起报数值；多层廓线按层次分线，避免混接不同气压层。
- 降水汇总改为模式×阈值矩阵，多起报显示“模式 · 起报”；随指标切换，保留 N 和有效时次数。其他类别保持原指标长表，单一要素／层次／单位移到表头说明，逐时次表也去掉相应重复列。
- 评分显示三位小数；TS、ETS、POD、FAR 坐标固定 0～1，间隔 0.2，其余用 1/2/5 整刻度。汇总 CSV 仍输出完整长表及原始统计量，没有减少内容。
- 保留默认阈值 `rainMix` / `defaultThreshold`、“没有区分度”提示、会话评分／图表缓存、WN3 演示切换。图表缓存键包含视图名；原始落区仅作有限内存缓存。只新增任务允许的三类控件，没有新增前端库。更新 `index.html` 中 `case.js`、`case.css` 的版本号。
- 没有改动外部 `case_verification_core.py`、`case_verification.py`，也没有提交任何部署路径改动。

## 验证和证据

使用本机 `127.0.0.1:8505` 的真实历史回放和已保存实况，未伪造产品数据。先确认 `/status` 在线，再在修改前通过 `api/case/score` 保存基线。

1. `score-before.json`、`score-after.json`、`score-comparison.json`：默认条件 32 条汇总、128 条逐时次评分逐值相同；记录行数和 SHA-256。
2. `test_case_page.py`：真实 HTTP 接口回归，覆盖默认 6 小时、12 小时、24 小时，以及 0.01° 独立样本条件。将落区返回值逐格按阈值重算，**288 组 N/H/M/F/C 全部与同一时刻的评分行相等**；输出为 `fields-score-check.json` 和 `backend-tests.txt`。另检验非法个例／起报／有效时刻、非降水请求拒绝、双起报只返回第一个起报。
3. `test_case_page_frontend.js`：基于真实 HTTP 结果检查个例下拉、默认阈值（本机这份回放自动选到 50 mm）、四个时段、九张 SVG、市界、矩阵、三位小数、完整 32 行汇总 CSV、POD/FAR/BIAS 零分母断线、视图缓存区分、双起报 16 行矩阵、气温表头及落区隐藏。输出为 `frontend-tests.txt`、`frontend-requests.json`。原有 `test_demo_core.js` 也通过，见 `demo-regression.txt`。
4. `tools/case_page_browser.js`：本机无头 Chrome，1600 px 宽；`default.png`、`lead.png`、`threshold-0.1.png` 分别为默认、随时效、点击 0.1 mm 后。实际检查九图同排、390 px 自动换为多行且页面宽度仍为 390、WN3 演示图可显示、切回业务资料恢复九图。见 `browser-check.txt`。
5. `scientific-sources.sha256` 记录未改动的两份科学计算文件。Python 编译、JS 语法和 `git diff --check` 均通过。

复测命令（仓库根目录，先启动 8543 服务）：

```bash
CASE_TEST_URL=http://127.0.0.1:8543/Verification/test/api/case/ /home/chengyang/miniforge3/bin/python -m unittest -v test_case_page
NODE_PATH=/tmp/guifan-jsdom/node_modules node test_case_page_frontend.js
NODE_PATH=/tmp/guifan-jsdom/node_modules node test_demo_core.js
node tools/case_page_browser.js
```

`jsdom` 仅使用本机已有的测试依赖，不引入产品页面。截图脚本将 Chrome 配置置于 `/tmp`，并关闭沙箱和 D-Bus 调用以适应当前受限测试环境；这些参数不会进入产品页面或上线服务。

## 范围、已知限制和上线注意

- 第二阶段多个例核心改造未做，符合任务范围。当前只登记 2026-07-25；增加登记项只改 `CASES`，真正计算其他日期仍需第二阶段扩展外部核心及数据源。
- 本轮数值回归覆盖上述 6/12/24 小时条件；没有穷举所有 2 小时、实况来源、高空层次及预报窗口组合。2 小时取数与累计仍调用原引擎，其他类别评分算法没有改动。
- 原有个例资料中，默认第一个时段实况全为零；因此落区实况为空雨区、部分 TS 为缺测是实际结果。默认阈值继续由原逻辑选取，没有人为改分或固定阈值。
- 本机一般目录构建的后台 ERA5 更新缺少 `eccodes`，启动时会报告模块缺失。这是现有本地环境问题；个例回放接口、页面和本轮验证正常，未修改其他页面来规避。
- 上线须重启 Python 服务以加载新接口和目录；静态资源版本号已更新。沿用 Spark 原有 `VERIFICATION_APP_ROOT` 和实况缓存／凭据配置；APP_KEY 仅由既有代码读取，证据、日志、请求及报告不含密钥。
- 本地测试只监听 `127.0.0.1:8543`，交付前已关闭；Spark 仅通过既有隧道读取。没有生产发布动作。
- Chrome 临时配置、临时实况副本和临时 Git 元数据在交付前清理；没有保留实验数据副本。保留交付物为 `evidence/case-page/`（约 3.4 MB，核对结果和截图）和 `case-page-20261111.bundle`（约 1.2 MB，供只读 Git 限制解除后恢复提交）；以文件实际大小为准。
