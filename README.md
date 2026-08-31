# 深圳格点预报检验平台

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
