# 深圳格点预报检验平台（MVP）

## 启动

```powershell
cd C:\Users\chengyang\Documents\Codex\2026-08-31\sh\outputs\verification_platform
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

页面中填写：模式目录、格点信息表、深圳街道边界和开放数据 appKey。平台不会把 appKey 写入文件。

当前支持：

- 1小时、3小时、6小时、24小时累计降水；
- NetCDF 累计降水差分；
- API 实况倒序查询；
- 最近邻格点匹配、深圳边界裁剪；
- TS、POD、FAR、BIAS；
- 预报、实况、误差、命中/漏报/空报空间图；
- CSV 结果下载。

