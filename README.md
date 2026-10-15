# 气象检验平台

当前多模块平台的源码位于 [`platform/`](platform/)，服务入口为 [`platform/server.py`](platform/server.py)。支持降水、气温、地面、环流、个例、台风、月降水检验，以及模式接入和资料质控；包含 Open-Meteo EC 接入模块。

安装环境、外部项目依赖和部署步骤见 **[平台运行说明](platform/README.md)**。源码整理范围见 [迁移说明](docs/platform-migration.md)。

## 项目结构

| 目录或文件 | 用途 |
| --- | --- |
| [`platform/`](platform/) | Python 后端、页面、检验算法和现有测试 |
| [`data/`](data/) | 公共深圳格网和街道边界资料，与当前平台使用的基础资料一致 |
| [`deploy/`](deploy/) | systemd 用户服务配置模板 |
| [`docs/`](docs/) | 迁移说明及源文件校验清单 |
| [`requirements.txt`](requirements.txt) | 转向当前平台的 Python 依赖清单 |

## 在现有 WSL 环境预览

当前平台复用预报平台、三维地球的公共业务代码和本地气象归档。满足运行说明中的外部依赖后，使用空闲端口预览：

```bash
cd platform
/home/chengyang/.venvs/ec-gfs-server/bin/python server.py --port 8514
```

访问 `http://127.0.0.1:8514/Verification/test/`。正式服务的目录切换、配置和回退步骤见运行说明。

## 版本与数据

旧版 Streamlit 应用入口和专用配置已从当前文件树移除，过往版本可通过 Git 提交历史查看。当前平台使用 Python 后端；GitHub 用于管理源码，运行网站需要 Linux/WSL、相应业务代码和气象资料。

缓存、评分结果、密钥和临时文件由 `.gitignore` 排除。请通过环境变量或本地配置提供接口凭据。
