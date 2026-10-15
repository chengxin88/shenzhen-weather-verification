# WN3 格点接口演示

用户已确认用 WN3 测试新模式接口，并明确标为演示资料。

11 个短临、短期格点入口共用一批经过现有 native_archive 验证的 WeatherNext 3 集合均值资料，选取 +6h 气温与同一起报最后三个完整小时的降水，配对对应北京时间深圳格点实况。保留原生 0.1° 网格、小时资料间隔，不按目标模式分辨率或分钟时效伪造资料。台风入口不生成路径样例。

## 接口

- 接入清单：`/Verification/test/api/model-interfaces`，含 `demo` 元数据以及每个入口的 `demo_available` 和 `demo_endpoint`。
- 演示格点：`/Verification/test/api/model-interfaces/{product_id}/demo-field`，参数与正式 `field` 相同，必须使用演示清单中的真实起报、360 分钟时效。气温为 `temperature_2m`；降水为 `precipitation`，`accumulation_minutes=180`。
- 演示配对和评分：`/Verification/test/api/model-interfaces/demo/detail`。

响应明确携带 `is_demo=true`、`source_model=Google WeatherNext 3（集合均值）`、`excluded_from_official_scores=true`、`target_product_id`、`native_grid_degrees`。下载文件名包含 WN3-DEMO。正式 field 路由仍返回 reserved，未注册新模式适配器。正式数据验证拒绝演示响应。

演示分开写入 `cache/model-interface-demo`，不写正式评分档案、排名或自动下载配置。SHA256 校验不可变字段及配对文件，公开接口不返回本地缓存路径或源签名。源路径仅存于内部 provenance.json。页面的气温线性插值与降水最近邻配对沿用现有检验方式。

## 更新样例

在科学计算虚拟环境运行 `build_wn3_demo.py --destination <演示缓存目录>`，只读已有缓存，不下载资料。生成器选择最近的 +6h 批次，要求完整气温、三个同起报小时降水和已到报实况；冻结样例后用原子写入更新 current.json。该演示是单批检查，不代表目标模式能力或长期 WN3 评分。

测试：`python -m unittest -v test_model_interfaces test_wn3_demo`；生成真实样例后执行。
