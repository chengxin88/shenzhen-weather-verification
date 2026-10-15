# 当前气象检验平台

这是 2026-10-15 从 WSL Ubuntu 的 `verification-test` 整理出的源码快照。现有 Python、HTML、JavaScript、CSS 文件保持原有相对位置，避免因移动目录改变导入和页面资源路径。

## 环境与依赖

- Linux 或 WSL Ubuntu；Python 3.12。部分缓存锁使用 Linux 的 `fcntl`。
- Python 依赖见 [`requirements.txt`](requirements.txt)。版本取自本次运行中的虚拟环境；不包含另外两个项目的完整依赖。
- 当前平台复用预报平台及三维地球的业务代码和气象归档，仍是集成部署版本。单独克隆本仓库、安装 pip 依赖不足以运行全部业务。

需要保留或另行迁移的外部组件如下。

| 组件 | 当前 WSL 位置 | 用途 |
| --- | --- | --- |
| 预报平台 | `/home/chengyang/ec-gfs-server` | `case_verification.py`、`case_verification_core.py`、`seven_day_verification.py`、台风函数、格网与深圳边界、原检验缓存 |
| 三维地球后端 | `/home/chengyang/gods-eye-weather/weather_backend` | `global_store.py` 及其依赖、归档读取 |
| 三维地球数据 | `/home/chengyang/gods-eye-weather/weather-data` | EC/GFS/AI/WN3 模式归档、ERA5 参考资料 |
| Open-Meteo 缓存及更新服务 | `/home/chengyang/openmeteo-ecmwf` | 已接入的 EC 9 km 地面场缓存及周期更新 |
| 历史月预报 | `/home/chengyang/hist-forecast` | 月降水模块使用的历史资料 |
| 本地资料接口 | `http://127.0.0.1:8505` | 个例检验调用的既有资料服务 |

Open-Meteo 的三个 Python 文件已从原有符号链接复制为真实源码文件。本仓库中的副本可独立查看，后续更新共享实现时需要同步这些文件。相关来源与文件校验值见 [`../docs/platform-source-manifest.json`](../docs/platform-source-manifest.json)。

## 在现有 WSL 环境预览

现有线上服务继续使用原目录。先使用另外一个空闲端口预览仓库中的版本：

```bash
cd /path/to/shenzhen-weather-verification/platform
/home/chengyang/.venvs/ec-gfs-server/bin/python server.py --port 8514
```

浏览器访问 `http://127.0.0.1:8514/Verification/test/`。服务默认仅监听本机；对外访问由原有反向代理和登录网关提供。

新的代码目录没有携带运行缓存。第一次打开检验目录时会读取既有业务资料并生成评分，耗时取决于本地数据量。演示产品需要原有的生成结果，或另外运行对应的生成脚本。

如需新建虚拟环境：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

仍需满足上面的外部项目和资料要求。新服务器还需安装匹配的 ecCodes 运行环境，并迁移或重建所需气象归档。

## 配置

[`.env.example`](.env.example) 列出源码已经支持的环境变量。程序不会自动加载 `.env` 文件，应通过 shell 或 systemd 的 `EnvironmentFile` 注入。

- `APP_KEY`：深圳开放数据接口密钥。也可以沿用预报平台本地 `.streamlit/secrets.toml`。
- `VERIFICATION_APP_ROOT`：预报平台位置。
- `QC_APP_ROOT`、`QC_WEATHER_ROOT`、`QC_CACHE_ROOT`：资料质控的项目、资料与结果目录。
- `OPENMETEO_EC_ROOT`：已下载的 Open-Meteo 缓存目录。
- `OPENMETEO_EC_PROXY`、`ANALYSIS_REFERENCE_PROXY`、`BF_PROXY`：相应请求使用的代理。
- `MONTHLY_FC_ROOT`：历史月预报资料位置。

部分模块仍保留 `/home/chengyang/...` 路径和本地端口，环境变量尚未覆盖全部外部依赖。换用户或服务器时，需要先统一这些路径；本次整理没有改变检验算法或运行服务。

## 部署到当前服务

现有用户服务为 `weather-verification-test.service`，工作目录为 `/home/chengyang/verification-test`，使用 `/home/chengyang/.venvs/ec-gfs-server/bin/python`，端口为 8513。

部署流程：

1. 审核并合并源码分支，记录准备上线的提交。
2. 在现有 WSL 上获取该提交，按上面的命令用空闲端口预览。
3. 验证页面、资料读取、评分与质控，并备份原服务配置。
4. 将现有服务的工作目录切换到该提交中的 `platform/`，并设置所需环境变量。
5. 执行 `systemctl --user daemon-reload` 和 `systemctl --user restart weather-verification-test.service`，通过原有域名验证。
6. 若需回退，将服务工作目录恢复到原目录或前一个已验证版本后重启。

用户服务配置模板见 [`../deploy/weather-verification-test.service.example`](../deploy/weather-verification-test.service.example)。本仓库目前没有连接线上服务的自动部署工作流。

## 校验

在 `platform/` 目录中，可以先运行使用临时资料或模拟请求的测试：

```bash
NO_PROXY=127.0.0.1,localhost python -m unittest test_model_interfaces test_quality_control test_quality_http
node --test test_demo_core.js
```

`test_wn3_demo.py` 还要求本地存在真实 WN3 演示资料。在原 WSL 环境可通过以下变量指向既有样本，生成资料仍放在仓库外：

```bash
MODEL_INTERFACE_DEMO_ROOT=/home/chengyang/verification-test/cache/model-interface-demo \
NO_PROXY=127.0.0.1,localhost python -m unittest test_wn3_demo
```

这些测试不能代替真实资料下的部署验证。新增集成校验时，需明确数据目录，避免在 CI 中自动下载完整气象归档。

## 仓库与运行数据

源码、页面配置、测试和说明文档纳入版本控制。缓存、评分目录、天气数据、密钥、日志和临时部署包由 `.gitignore` 排除。`model_ui_reference.json` 属于页面配置，保留在源码中。

仓库根目录 `data/` 保留公共深圳格网及街道边界，与当前预报平台使用的文件一致。集成部署时，现有代码继续从 `VERIFICATION_APP_ROOT/data/` 等业务目录读取这些资料，具体路径见上面的依赖清单。旧版 Streamlit 应用入口和专用配置已移除，过往源码可通过提交历史查看。

ERA5、GDAS、ECMWF/Open-Meteo 等资料的使用与再分发应遵循对应来源的许可和署名要求。气象数据的许可与本仓库源码的许可分别管理；本次整理没有新增源码许可声明。
