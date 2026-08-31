from datetime import datetime, timedelta
import io
import math
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from netCDF4 import Dataset
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point
import streamlit as st
import urllib3


API_URL = "https://opendata.sz.gov.cn/api/29200_00903509/1/service.xhtml"
ROWS_PER_PAGE = 10000

st.set_page_config(page_title="深圳格点预报检验平台", page_icon="🌧️", layout="wide")
st.markdown(
    """
    <style>
      .stApp {background: linear-gradient(180deg,#eef7fb 0,#f8fbfd 35%);}
      [data-testid="stMetric"] {background:#ffffff;border:1px solid #d7e8f2;border-radius:10px;padding:12px;}
      h1,h2,h3 {color:#0d4e78;}
    </style>
    """,
    unsafe_allow_html=True,
)


def page_request(session, app_key, date_text, page):
    response = session.post(
        API_URL,
        data={"appKey": app_key, "page": page, "rows": ROWS_PER_PAGE,
              "startDate": date_text, "endDate": date_text},
        timeout=60,
        verify=False,
    )
    response.raise_for_status()
    result = response.json()
    if "errorCode" in result:
        raise RuntimeError(f"接口错误 {result.get('errorCode')}：{result.get('message')}")
    return result


@st.cache_data(show_spinner=False, ttl=600)
def fetch_observation(app_key, valid_time_text, rain_field):
    valid_time = pd.Timestamp(valid_time_text)
    date_text = valid_time.strftime("%Y%m%d")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://opendata.sz.gov.cn/"})
    first = page_request(session, app_key, date_text, 1)
    total = int(first.get("total", 0))
    pages = math.ceil(total / ROWS_PER_PAGE)
    records, found = [], False
    target = valid_time.strftime("%Y-%m-%d %H:%M:%S")
    for page in range(pages, 0, -1):
        result = first if page == 1 else page_request(session, app_key, date_text, page)
        matched = [row for row in result.get("data", []) if row.get("DDATETIME") == target]
        if matched:
            records.extend(matched)
            found = True
        elif found:
            break
    if not records:
        raise ValueError(f"实况接口没有 {target} 的数据")
    frame = pd.DataFrame(records)
    for column in ["GRIDID", rain_field]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.loc[frame[rain_field] < 0, rain_field] = np.nan
    frame = frame.dropna(subset=["GRIDID"]).drop_duplicates(subset=["GRIDID"])
    frame["GRIDID"] = frame["GRIDID"].astype(int)
    return frame[["GRIDID", rain_field]].rename(columns={rain_field: "observed"})


@st.cache_data(show_spinner=False)
def read_grid(grid_path):
    grid = pd.read_excel(grid_path, sheet_name="数据集1").rename(columns={
        "格网相对X坐标": "grid_x", "格网相对Y坐标": "grid_y",
        "格网左下角经度（度）": "lon_left", "格网右上角经度（度）": "lon_right",
        "格网左下角纬度（度）": "lat_bottom", "格网右上角纬度（度）": "lat_top",
        "格网ID（唯一）": "GRIDID",
    })
    columns = ["GRIDID", "grid_x", "grid_y", "lon_left", "lon_right", "lat_bottom", "lat_top"]
    for column in columns:
        grid[column] = pd.to_numeric(grid[column], errors="coerce")
    grid = grid.dropna(subset=columns)
    grid[["GRIDID", "grid_x", "grid_y"]] = grid[["GRIDID", "grid_x", "grid_y"]].astype(int)
    grid["lon"] = (grid["lon_left"] + grid["lon_right"]) / 2
    grid["lat"] = (grid["lat_bottom"] + grid["lat_top"]) / 2
    return grid


@st.cache_data(show_spinner=False)
def read_boundary(boundary_path):
    streets = gpd.read_file(boundary_path).to_crs("EPSG:4326")
    if (~streets.geometry.is_valid).any():
        streets["geometry"] = streets.geometry.buffer(0)
    return streets


@st.cache_data(show_spinner=False)
def read_forecast(data_dir, lead_hour, accumulation):
    current_path = Path(data_dir) / f"postvar{lead_hour:03d}.nc"
    previous_hour = lead_hour - accumulation
    previous_path = Path(data_dir) / f"postvar{previous_hour:03d}.nc"
    if previous_hour < 0:
        raise ValueError("预报时效必须大于或等于累计时长")
    for path in [current_path, previous_path]:
        if not path.exists():
            raise FileNotFoundError(f"找不到模式文件：{path}")
    with Dataset(current_path) as current, Dataset(previous_path) as previous:
        lon = np.asarray(current.variables["lon"][:], dtype=float)
        lat = np.asarray(current.variables["lat"][:], dtype=float)
        rain_now = np.ma.filled(current.variables["rain"][0, 0], np.nan).astype(float)
        rain_before = np.ma.filled(previous.variables["rain"][0, 0], np.nan).astype(float)
    rain = rain_now - rain_before
    rain[rain < -0.01] = np.nan
    rain[(rain >= -0.01) & (rain < 0)] = 0
    return lon, lat, rain


def prepare_pairs(grid, observation, streets, model_lon, model_lat, forecast):
    data = grid.merge(observation, on="GRIDID", how="inner")
    city = streets.dissolve().geometry.iloc[0]
    inside = [city.covers(Point(x, y)) for x, y in zip(data["lon"], data["lat"])]
    data = data.loc[inside].copy()
    x_index = np.abs(model_lon[None, :] - data["lon"].to_numpy()[:, None]).argmin(axis=1)
    y_index = np.abs(model_lat[None, :] - data["lat"].to_numpy()[:, None]).argmin(axis=1)
    data["forecast"] = forecast[y_index, x_index]
    return data.dropna(subset=["forecast", "observed"])


def scores(data, thresholds):
    rows = []
    for threshold in thresholds:
        f_event = data["forecast"].to_numpy() >= threshold
        o_event = data["observed"].to_numpy() >= threshold
        h = int(np.sum(f_event & o_event))
        m = int(np.sum(~f_event & o_event))
        f = int(np.sum(f_event & ~o_event))
        ts = h / (h + m + f) if h + m + f else np.nan
        pod = h / (h + m) if h + m else np.nan
        far = f / (h + f) if h + f else np.nan
        bias = (h + f) / (h + m) if h + m else np.nan
        rows.append([threshold, h, m, f, ts, pod, far, bias])
    return pd.DataFrame(rows, columns=["阈值(mm)", "命中H", "漏报M", "空报F", "TS", "POD", "FAR", "BIAS"])


def rain_map(data, streets, column, title, levels):
    colors = ["#ffffff", "#d9f0ff", "#91d7ff", "#45b8ff", "#35c96f",
              "#f5e642", "#ff9d2e", "#ef3b2c", "#8e2de2"]
    cmap = ListedColormap(colors[: len(levels) - 1])
    norm = BoundaryNorm(levels, cmap.N, clip=True)
    fig, ax = plt.subplots(figsize=(8, 5.5))
    image = ax.scatter(data["lon"], data["lat"], c=data[column], s=30, marker="s", cmap=cmap, norm=norm)
    streets.boundary.plot(ax=ax, color="#555", linewidth=0.3)
    streets.dissolve().boundary.plot(ax=ax, color="black", linewidth=1.0)
    fig.colorbar(image, ax=ax, boundaries=levels, ticks=levels, spacing="uniform", label="mm")
    ax.set_title(title)
    ax.set_aspect(1 / np.cos(np.deg2rad(data["lat"].mean())))
    ax.set_xlabel("经度"); ax.set_ylabel("纬度"); fig.tight_layout()
    return fig


def contingency_map(data, streets, threshold):
    f_event, o_event = data["forecast"] >= threshold, data["observed"] >= threshold
    category = np.where(f_event & o_event, 1, np.where(~f_event & o_event, 2, np.where(f_event & ~o_event, 3, 0)))
    fig, ax = plt.subplots(figsize=(8, 5.5))
    cmap = ListedColormap(["#eeeeee", "#2ca25f", "#3182bd", "#de2d26"])
    ax.scatter(data["lon"], data["lat"], c=category, s=30, marker="s", cmap=cmap, vmin=0, vmax=3)
    streets.boundary.plot(ax=ax, color="#555", linewidth=0.3)
    streets.dissolve().boundary.plot(ax=ax, color="black", linewidth=1.0)
    ax.set_title(f"空间列联分布（阈值 ≥ {threshold:g} mm）\n绿色命中 / 蓝色漏报 / 红色空报")
    ax.set_aspect(1 / np.cos(np.deg2rad(data["lat"].mean())))
    ax.set_xlabel("经度"); ax.set_ylabel("纬度"); fig.tight_layout()
    return fig


st.title("深圳格点预报检验平台 · MVP")
st.caption("NetCDF模式预报 × 深圳开放数据API实况｜北京时间｜深圳街道边界")

with st.sidebar:
    st.header("检验设置")
    data_dir = st.text_input("模式目录", r"C:\Users\chengyang\Downloads\2026083023\2026083023")
    grid_path = st.text_input("格点信息表", r"C:\Users\chengyang\Downloads\深圳范围自动站实况格点信息表_2920000903510.xlsx")
    boundary_path = st.text_input("深圳街道边界", r"C:\Users\chengyang\Downloads\深圳街道\深圳街道\深圳街道.shp")
    app_key = st.text_input("开放数据 appKey", type="password")
    lead_hour = st.number_input("结束预报时效（小时）", min_value=1, max_value=240, value=7)
    accumulation = st.selectbox("累计降水", [1, 3, 6, 24], index=0)
    default_thresholds = "0.1,1,2,5,10,20,30" if accumulation == 1 else "0.1,1,5,10,25,50,100"
    threshold_text = st.text_input("检验阈值（mm）", default_thresholds)
    run_button = st.button("开始检验", type="primary", use_container_width=True)

if run_button:
    try:
        if not app_key:
            raise ValueError("请输入 appKey")
        run_time_utc = datetime.strptime(Path(data_dir).name, "%Y%m%d%H")
        valid_time_bjt = run_time_utc + timedelta(hours=lead_hour + 8)
        rain_field = {1: "RAIN01H", 3: "RAIN03H", 6: "RAIN06H", 24: "RAIN24H"}[accumulation]
        thresholds = [float(value.strip()) for value in threshold_text.split(",") if value.strip()]
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        with st.spinner("读取模式、查询实况并匹配格点……"):
            grid = read_grid(grid_path)
            streets = read_boundary(boundary_path)
            model_lon, model_lat, forecast = read_forecast(data_dir, int(lead_hour), accumulation)
            observation = fetch_observation(app_key, valid_time_bjt.strftime("%Y-%m-%d %H:%M:%S"), rain_field)
            paired = prepare_pairs(grid, observation, streets, model_lon, model_lat, forecast)
            result = scores(paired, thresholds)

        st.success(f"检验完成：北京时间 {valid_time_bjt:%Y-%m-%d %H:%M}，有效配对 {len(paired):,} 个格点")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("模式最大降水", f"{paired['forecast'].max():.1f} mm")
        c2.metric("实况最大降水", f"{paired['observed'].max():.1f} mm")
        c3.metric("平均绝对误差", f"{np.mean(np.abs(paired['forecast']-paired['observed'])):.2f} mm")
        c4.metric("均方根误差", f"{np.sqrt(np.mean((paired['forecast']-paired['observed'])**2)):.2f} mm")

        tab1, tab2, tab3, tab4 = st.tabs(["评分总览", "预报/实况", "空间列联", "数据表"])
        with tab1:
            st.subheader(f"{accumulation}小时累计降水评分")
            st.dataframe(result.style.format({"TS":"{:.3f}", "POD":"{:.3f}", "FAR":"{:.3f}", "BIAS":"{:.3f}"}), use_container_width=True)
            fig, ax = plt.subplots(figsize=(9, 4))
            x = np.arange(len(result)); width = 0.25
            ax.bar(x-width, result["TS"], width, label="TS")
            ax.bar(x, result["POD"], width, label="POD")
            ax.bar(x+width, result["FAR"], width, label="FAR")
            ax.set_xticks(x, [f"≥{v:g}" for v in result["阈值(mm)"]]); ax.set_ylim(0, 1)
            ax.set_ylabel("评分"); ax.set_xlabel("降水阈值 (mm)"); ax.legend(); ax.grid(axis="y", alpha=.25)
            st.pyplot(fig)
        with tab2:
            levels = sorted(set([0] + thresholds + [max(100, max(thresholds)*2)]))
            left, right = st.columns(2)
            left.pyplot(rain_map(paired, streets, "forecast", "模式预报", levels))
            right.pyplot(rain_map(paired, streets, "observed", "API实况", levels))
        with tab3:
            selected_threshold = st.selectbox("空间检验阈值", thresholds)
            st.pyplot(contingency_map(paired, streets, selected_threshold))
        with tab4:
            st.dataframe(paired[["GRIDID", "lon", "lat", "forecast", "observed"]], use_container_width=True)
            buffer = io.StringIO(); result.to_csv(buffer, index=False)
            st.download_button("下载评分CSV", buffer.getvalue().encode("utf-8-sig"), "verification_scores.csv", "text/csv")
    except Exception as error:
        st.error(str(error))
        st.exception(error)
else:
    st.info("请在左侧填写 appKey 和检验设置，然后点击“开始检验”。")
