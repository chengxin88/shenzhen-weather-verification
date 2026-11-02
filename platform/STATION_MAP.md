# 站点地图底图

底图、站点采集和评分共同使用 `station_common.BOX`：104–121°E / 18–27°N。
北界按用户要求固定为 27°N；站表与评分同步扩展，旧站历史配对保留。
源网格范围不足或缺少实况的新增站点不外推、不补零，地图显示灰色。

旧实现引用的 `satellite_south_china_coastline.geojson` 在 118°E 截断，
并且只含海岸线，缺少省界。现在使用随源码保存的 `station-basemap.json`，
包括 WGS84 海岸线、陆地国界和省界。海岸线/国界为实线，省界为虚线；
香港和澳门与内地的边界归入内部行政边界。

数据从本机已有的 Natural Earth 1:10m 数据离线生成：

- `physical/ne_10m_coastline.shp`
- `cultural/ne_10m_admin_0_boundary_lines_land.shp`
- `cultural/ne_10m_admin_1_states_provinces_lines.shp`

更新底图时运行：

```sh
python build_station_basemap.py --natural-earth /path/to/natural_earth
```

生成器只裁剪线几何，避免先裁剪多边形再取外环产生虚假的矩形边界。
运行接口只读打包的 JSON，不加载 Cartopy/Shapely，也不下载地图数据。
`station_api.meta()` 为旧评分快照叠加当前底图，不改写快照；
`station_score.refresh()` 也会把当前底图写入后续新快照。

验证：

```sh
python -m unittest test_station_basemap
NODE_PATH=/path/to/jsdom/node_modules node test_stations_frontend.js
```

站点范围扩展时，新站表会使预报缓存重新校验。已删除源文件的历史缓存按旧站号与原坐标重排，保留原站值；新站对应位置为缺测，待真实源文件可用后再采样。IEM 下载缓存键包含站号集合，避免扩站后误用旧站列表的响应。
