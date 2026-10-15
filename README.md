# 深圳格点预报检验平台

本仓库同时保留早期 Streamlit 应用，以及 2026-10-15 从现有 WSL 服务整理的多模块平台源码。

| 版本 | 入口 | 说明 |
| --- | --- | --- |
| 当前多模块平台 | [`platform/server.py`](platform/server.py) | 降水、气温、地面、环流、个例、台风、月降水、模式接入及资料质控；包含 Open-Meteo EC 接入模块 |
| 早期 Streamlit 版 | [`app.py`](app.py) | 上传 NetCDF、进行深圳格点降水检验，原入口保留 |

当前平台的安装环境、外部项目依赖和部署步骤见 [平台运行说明](platform/README.md)。版本整理范围见 [迁移说明](docs/platform-migration.md)。

当前平台使用 Python 后端和本地气象归档。GitHub 用于管理源码；运行服务需要 Linux/WSL、参考资料和现有公共代码，详见运行说明。

以下内容为早期 Streamlit 版的使用说明。

## 启动

```bash
pip install -r requirements.txt
streamlit run app.py
```

格点信息表和深圳街道边界位于项目的 `data` 目录。页面中填写模式起报时间并上传计算时段首尾两个 NetCDF 文件。平台不会把手动输入的 appKey 写入文件。

部署到 Streamlit Community Cloud 时，在 Advanced settings → Secrets 中配置：

```toml
APP_KEY = "你的密钥"
```

当前支持：

- 1小时、3小时、6小时、24小时累计降水；
- NetCDF 累计降水差分；
- API 实况倒序查询；
- 最近邻格点匹配、深圳边界裁剪；
- TS、POD、FAR、BIAS；
- 预报、实况、误差、命中/漏报/空报空间图；
- CSV 结果下载。
