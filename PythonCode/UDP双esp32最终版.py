import matplotlib
matplotlib.use("TkAgg")

import socket
import struct
import threading
import time
from collections import deque
import math

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from scipy.signal import butter, sosfiltfilt


# =========================
# UDP 参数
# =========================
UDP_IP = "0.0.0.0"
UDP_PORT = 3333

ESP1_IP = "192.168.1.109"
ESP2_IP = "192.168.1.110"

TARGET_IPS = [ESP1_IP, ESP2_IP]


# =========================
# 绘图 / 数据参数
# =========================
TOP_K = 5

MAX_POINTS = 2500 #最大数据点数
DRAW_POINTS = 2500 #绘图最大数据点数

PLOT_INTERVAL_MS = 120  #画图刷新时间

USE_OFFSET = True
OFFSET_STEP = 8

YLIM_UPDATE_EVERY = 20  #每20数据更新依次y轴
BANDPASS_UPDATE_EVERY = 12  #每12数据计算bandpass数据


# =========================
# Hampel 参数
# =========================
ENABLE_HAMPEL = True
HAMPEL_WIN = 7 #以最近7个点的数据计算中位数
HAMPEL_NSIGMA = 3.0


# =========================
# top5 选择参数
# =========================
SELECT_TOP5_AFTER_POINTS = 200  #200个数据后选出top5
TOP5_SCORE_POINTS = 200  #200个数据后选出top5


# =========================
# Bandpass 参数
# =========================
BP_LOW_HZ = 0.10
BP_HIGH_HZ_REQUEST = 10.0
BP_ORDER = 3

BANDPASS_MIN_SEC = 5.0 #开启滤波至少要5s 数据
BANDPASS_EXTRA_SEC = 10.0

LEFT_TRIM_SEC = 0.35  #把最左边 0.35 秒 最右边 1 秒的数据切掉
RIGHT_TRIM_SEC = 1.00

TOP1_GAIN = 12.0  #对bandpass后的数据放大，gain = 12
FIXED_Y_LIM = 120.0 #y轴默认 -120 ~ 120


# =========================
# 低频周期性 + 周期稳定性 + 幅度选择参数
# =========================
SELECT_WINDOW_SEC = 16.0   #选择最近16秒的数据做自相关

LOW_FREQ_MIN = 0.08
LOW_FREQ_MAX = 0.80

PERIODIC_TIE_MARGIN = 0.06
STABILITY_TIE_MARGIN = 0.06

AMP_RMS_WEIGHT = 0.65
AMP_P2P_WEIGHT = 0.35

SELECTION_UPDATE_EVERY = 40


# =========================
# 全局停止标志
# =========================
stop_flag = False


# =========================
# Run 窗口打印开关
# =========================
PRINT_TO_RUN_WINDOW = False


def log(*args, **kwargs):
    if PRINT_TO_RUN_WINDOW:
        print(*args, **kwargs)


# =========================
# 选择结果缓存
# =========================
selection_state = {  #字典，保存最后数据结果
    "best_ip": "NA",
    "mode": "not_ready",

    "periodic_score": 0.0,
    "stability_score": 0.0,
    "amplitude_score": 0.0,

    "rms": 0.0,
    "p2p": 0.0,

    "period_sec": 0.0,
    "f0": 0.0,

    "acf_score": 0.0,
    "fft_peak_ratio": 0.0,
    "band_power_ratio": 0.0,
    "period_cv": 0.0,
}


# =========================
# 每个 ESP32 状态
# =========================
device_states = {}

for ip in TARGET_IPS:   #循环每个IP，初始化状态
    device_states[ip] = {
        "time_data": deque(maxlen=MAX_POINTS),        # 单位 ms，用来画图横轴
        "real_time_data": deque(maxlen=MAX_POINTS),   # 单位 s，用来滤波和算 Fs

        "startup_raw": None,
        "startup_hampel": None,
        "num_subcarriers": None,

        "selected_indices": [],  #保存选出来的 TOP5 子载波
        "selected_info": [],
        "selected_raw": [], #保存选出来的 TOP5 子载波的原始数据
        "selected_hampel": [], #保存选出来的 TOP5 子载波的 Hampel 数据

        "top5_locked": False,

        "sample_count": 0,
        "packet_count": 0,

        # 现在 ESP32 发送的是 uint64 elapsed_us，单位 us
        "first_elapsed_us": None,
        "last_elapsed_us": None,

        "cached_bp_x": np.array([]),
        "cached_bp_y": np.array([]),
        "cached_bp_fs": None,
        "cached_bp_high": None,
        "cached_bp_top1": None,

        "last_fs": None,
        "last_bp_high_used": None,

        "legend_hampel_created": False,
        "legend_bp_created": False,

        "lock": threading.Lock(),
    }


# =========================
# UDP
# 新 ESP32 格式：
# [uint64_t elapsed_us][uint16_t pair_count][uint32_t p0][uint32_t p1]...
# 前 8 字节：elapsed_us，单位 us
# 第 8~9 字节：pair_count
# 第 10 字节开始：power 数据
# =========================
def parse_udp_packet(data: bytes):
    try:
        # uint64 elapsed_us = 8 bytes
        # uint16 pair_count = 2 bytes
        # 最小包头 = 10 bytes, 小于10表示数据不完整
        if len(data) < 10:
            return None, None

        # 前 8 字节读取 uint64 elapsed_us，小端
        elapsed_us = struct.unpack_from("<Q", data, 0)[0] #从data 的0 byte位置，读取8个字节，赋值给 elapsed_us

        # 第 8~9 字节读取 uint16 pair_count，小端
        pair_count = struct.unpack_from("<H", data, 8)[0] #从data 的8 byte位置，读取2个字节，赋值给 pair_count

        if pair_count <= 0 or pair_count > 512: #如果pair_count(子载波数)不合法，则返回None
            return None, None

        # 8 字节 elapsed_us + 2 字节 pair_count + pair_count 个 uint32 power
        expected_len = 8 + 2 + pair_count * 4 #单data里总byte数

        if len(data) < expected_len: #总byte数不够，说明数据不完整，返回None
            return None, None

        # power 数据从第 10 字节开始
        power_values = struct.unpack_from("<" + "I" * pair_count, data, 10)#读取所有power数据，从第10个byte开始

        # ESP32 发来的是 power = I^2 + Q^2
        # Python 这里转成 amplitude = sqrt(power)
        amplitudes = [math.sqrt(p) for p in power_values] #将power数据转成amplitude(list)

        return elapsed_us, amplitudes

    except Exception:
        return None, None


# =========================
# Hampel 单点
# history_deque 是deque类型，可以快速地删除头部元素，list不行
# new_value 是当前最新的一个数据点
# =========================
def hampel_one_value(history_deque, new_value):
    if len(history_deque) < HAMPEL_WIN: #如果历史数据小于hampel窗口大小，返回原值
        return new_value

    recent = list(history_deque)[-HAMPEL_WIN:] #从history_deque取最近7点数据，赋值recent

    med = float(np.median(recent)) #算出recent的中位数
    abs_dev = [abs(v - med) for v in recent] #计算每个点离中位数多远
    mad = float(np.median(abs_dev)) #算出每个点离中位数的差的中位数，赋值mad

    if mad < 1e-6: #如果mad太小，会让后面hampel判断过于敏感
        return new_value #所以如果mad太小，返回原值，不做hampel处理

    sigma_est = 1.4826 * mad

    if abs(new_value - med) > HAMPEL_NSIGMA * sigma_est: #如果新值离中位数的距离大于判断阈值
        return med #判断为异常，返回过去七点的中位数

    return new_value #否则值正常，返回原值


# =========================
# 启动阶段：全部子载波 Hampel
# =========================
def hampel_all_startup(state, amplitudes): 
    if state["startup_raw"] is None:
        return amplitudes

    cleaned = list(amplitudes)

    for i in range(len(amplitudes)):
        cleaned[i] = hampel_one_value(state["startup_raw"][i], amplitudes[i]) #startup_raw里保存了每个子载波最近的所有数据

    return cleaned


# =========================
# top5 锁定后：只处理 selected top5
# hampel 处理top5
# =========================
def process_selected_top5_only(state, amplitudes):
    for rank, sc in enumerate(state["selected_indices"]):
        if sc >= len(amplitudes):  #如果子载波编号大于总子载波数，异常跳过
            continue

        val = amplitudes[sc]  #把选中的子载波的幅度值拿出来，赋值给val

        filtered_val = hampel_one_value( 
            state["selected_raw"][rank], 
            val
        ) #hampel处理

        state["selected_raw"][rank].append(val) #保存新的一个原始数据点
        state["selected_hampel"][rank].append(filtered_val) #保存新的一个hampel处理后的数据


# =========================
# 用 Hampel 后数据的 std 选 top5
# =========================
def try_select_top5_by_std_locked(ip, state):
    if state["top5_locked"]:
        return False

    if state["startup_hampel"] is None:
        return False

    if state["num_subcarriers"] is None:
        return False

    if state["sample_count"] < SELECT_TOP5_AFTER_POINTS:
        return False
    #数据点不够，没有数据都返回false，什么都不做

    candidates = []

    for sc in range(state["num_subcarriers"]):
        y = list(state["startup_hampel"][sc]) #依次读取每个子载波的hampel数据，赋值给y

        if len(y) < TOP5_SCORE_POINTS: #如果数据点不够，什么都不做
            continue

        y_recent = np.array(y[-TOP5_SCORE_POINTS:], dtype=float) #提取最新200个数据点，赋值给y_recent
        score = float(np.std(y_recent)) #计算这200个数据点的标准差，赋值给score

        candidates.append((sc, score)) #把子载波编号和对应的分数作为数据，添加到candidates列表里

    if not candidates:
        return False

    candidates.sort(key=lambda x: x[1], reverse=True) #按照分数从大到小排序
    best = candidates[:TOP_K] #截取前5个，赋值给best

    state["selected_indices"] = [x[0] for x in best] #将子载波编号列表赋值给state["selected_indices"]
    state["selected_info"] = best #将子载波编号和分数列表赋值给state["selected_info"]

    state["selected_raw"] = [
        deque(maxlen=MAX_POINTS) for _ in range(TOP_K) #给 TOP5 创建新的 raw数据队列
    ]

    state["selected_hampel"] = [
        deque(maxlen=MAX_POINTS) for _ in range(TOP_K) #给 TOP5 创建新的 hampel 队列
    ]

    for rank, sc in enumerate(state["selected_indices"]):
        raw_list = list(state["startup_raw"][sc])  #把选定的子载波的startup阶段的数据放入到 selected_raw 里
        hampel_list = list(state["startup_hampel"][sc]) #把选定的子载波的startup阶段的hampel数据放入到 selected_hampel 里

        for v in raw_list:
            state["selected_raw"][rank].append(v)

        for v in hampel_list:
            state["selected_hampel"][rank].append(v)

    state["startup_raw"] = None #清空 startup 阶段的数据
    state["startup_hampel"] = None

    state["top5_locked"] = True  #设置 top5 锁定

    log(f"\n[{ip}] TOP5 locked by Hampel STD. From now on only TOP5 will be processed.")
    for rank, (sc, score) in enumerate(best, start=1):
        log(f"  #{rank}: SC {sc}, std={score:.4f}")

    return True


# =========================
# 估计采样率
# =========================
def estimate_fs_from_time(t_sec):
    # 
    # 用 ESP32 发来的 us 时间戳换算出来的 t_sec 计算平均采样率。

    # 公式：
    #     fs = (N - 1) / (t_last - t_first)

    # 其中：
    #     N = 当前窗口中的 CSI 点数
    #     N - 1 = 采样间隔数量
    #     t_last - t_first = 这些采样点真实经过的时间，单位秒

    

    t_sec = np.asarray(t_sec, dtype=float) #把时间点转化为数组

    if len(t_sec) < 10: #如果时间点小于10，无法估计采样率，返回none
        return None

    duration = t_sec[-1] - t_sec[0]  #时间长度为最后一个时间点减去第一个时间点

    if duration <= 1e-6:  #如果时间长度小于1e-6，无法估计采样率，返回none
        return None

    fs = (len(t_sec) - 1) / duration  #采样率 = (点数 - 1) / 时间长度

    if fs <= 0:
        return None

    return float(fs) #返回采样率


# =========================
# Bandpass 滤波
# =========================
def bandpass_signal_with_margin_and_trim(
    t_sec,
    y,
    display_x_min_ms,
    display_x_max_ms,
    extra_sec,
    left_trim_sec,
    right_trim_sec,
    low_hz,
    high_hz_request,
    order=3
):
    if len(t_sec) < 30 or len(y) < 30:  #如果数据点小于30，返回None
        return None, None, None, None

    t_sec = np.asarray(t_sec, dtype=float)
    y = np.asarray(y, dtype=float)  #数据转换成数组

    display_min_sec = display_x_min_ms / 1000.0
    display_max_sec = display_x_max_ms / 1000.0

    calc_min_sec = max(t_sec[0], display_min_sec - extra_sec)
    calc_max_sec = display_max_sec

    mask_calc = (t_sec >= calc_min_sec) & (t_sec <= calc_max_sec)

    t_calc = t_sec[mask_calc]  #得到用于滤波的时间和信号
    y_calc = y[mask_calc]

    if len(t_calc) < 30:  #如果用于滤波的数据点小于30，返回None
        return None, None, None, None

    valid = ~np.isnan(y_calc)  #去掉非数字的点，valid是一个布尔数组，表示哪些点是有效的

    t_calc = t_calc[valid] #只保留有效点
    y_calc = y_calc[valid]

    if len(t_calc) < 30: #如果有效点小于30，返回None
        return None, None, None, None

    duration = t_calc[-1] - t_calc[0]   #时间长度为最后一个时间点减去第一个时间点

    if duration < BANDPASS_MIN_SEC: #如果时间长度小于bandpass最小秒数，返回None
        return None, None, None, None

    fs = estimate_fs_from_time(t_calc) #用时间点计算采样率

    if fs is None:
        return None, None, None, None

    nyq = 0.5 * fs  #奈奎斯特频率为采样率的一半
    high_hz_used = min(high_hz_request, 0.45 * fs)  #实际bandpass的高频截止不能超过采样率的0.45倍

    if high_hz_used <= low_hz * 1.2: #如果高频截止太低，无法形成有效的通带，返回None
        return None, None, None, None

    low = low_hz / nyq #低频截止除以奈奎斯特频率，得到数字滤波器的低频截止
    high = high_hz_used / nyq #算出归一化频率

    if low <= 0 or high >= 1 or low >= high: #如果数字滤波器的截止频率不合法，返回None
        return None, None, None, None

    try:
        sos = butter(order, [low, high], btype="bandpass", output="sos") #设计一个巴特沃斯带通滤波器，返回二阶节系数
        y_bp_calc = sosfiltfilt(sos, y_calc) #用 filtfilt 函数进行前向和反向滤波，得到滤波后的信号，长度和 y_calc 一样
    except Exception:
        return None, None, None, None

    trusted_min_sec = display_min_sec + left_trim_sec 
    trusted_max_sec = display_max_sec - right_trim_sec

    if trusted_max_sec <= trusted_min_sec:
        return None, None, None, None

    mask_show = (t_calc >= trusted_min_sec) & (t_calc <= trusted_max_sec)

    t_show = t_calc[mask_show] #得到滤波后的时间点
    y_show = y_bp_calc[mask_show] #得到滤波后的信号

    if len(t_show) < 5:
        return None, None, None, None

    return t_show, y_show, fs, high_hz_used #返回滤波后的时间点，滤波后的信号，采样率，高频截止


# ============================================================
# 用 FFT 判断周期性
# ============================================================

def estimate_periodicity_by_fft(t_sec, y, fs):
    y = np.asarray(y, dtype=float)

    if len(y) < 50:
        return None

    y = y - np.mean(y)  #去直流分量

    if np.std(y) < 1e-9:
        return None

    n = len(y)  #数据点数

    win = np.hanning(n)  #加窗，减少频谱泄漏
    y_win = y * win

    freqs = np.fft.rfftfreq(n, d=1.0 / fs) #计算FFT对应的频率，rfftfreq返回非负频率，d是采样间隔
    spec = np.abs(np.fft.rfft(y_win)) ** 2 #计算FFT的幅度谱，rfft返回非负频率的FFT结果，取绝对值平方得到功率

    mask = (freqs >= LOW_FREQ_MIN) & (freqs <= LOW_FREQ_MAX) #只关注低频范围的频率，得到一个布尔数组，表示哪些频率在这个范围内

    if np.sum(mask) < 2: 
        return None

    band_freqs = freqs[mask] #得到低频范围内的频率范围
    band_spec = spec[mask] #得到低频范围内的功率

    peak_idx = int(np.argmax(band_spec)) #找到低频范围内功率最大的频率的index，赋值给peak_idx
    f0 = float(band_freqs[peak_idx]) #找到低频范围内功率最大的频率，赋值给f0

    total_power = float(np.sum(spec) + 1e-9) #总功率
    band_power = float(np.sum(band_spec)) #频率范围内总功率

    band_power_ratio = band_power / total_power #频率范围内功率占总功率的比例

    left = max(0, peak_idx - 1) 
    right = min(len(band_spec), peak_idx + 2)

    peak_near_power = float(np.sum(band_spec[left:right])) #峰值附近的功率，包含峰值和左右各一个频点
    fft_peak_ratio = peak_near_power / (band_power + 1e-9) #峰值附近的功率占频率范围内总功率的比例

    period_sec = 1.0 / f0 if f0 > 1e-9 else 0.0 #用主峰频率算出period

    return {
        "f0": f0,
        "period_sec": period_sec,
        "fft_peak_ratio": fft_peak_ratio,
        "band_power_ratio": band_power_ratio,
    }


def estimate_periodicity_by_autocorr(t_sec, y, fs):
    y = np.asarray(y, dtype=float)  #把数据点转成数组

    if len(y) < 50: #如果数据点小于50，无法估计周期性，
        return None

    y = y - np.mean(y)  #去直流分量

    std_y = np.std(y)  #计算标准差

    if std_y < 1e-9: #如果标准差太小，说明信号过于平，无法估计周期，返回None
        return None

    y = y / std_y  #将标准差归一化

    n = len(y)  #数据点数

    corr = np.correlate(y, y, mode="full")  #计算相关系数，全部数据点的相关系数，长度为 2*n-1
    corr = corr[n - 1:] #只保留正的部分，长度为 n

    if abs(corr[0]) < 1e-9:
        return None

    corr = corr / corr[0] #归一化corr数组，完全重合没有移动的点corr为1(corr[0]/corr[0])，其他点为0-1之间的值

    min_period = 1.0 / LOW_FREQ_MAX
    max_period = 1.0 / LOW_FREQ_MIN

    min_lag = int(min_period * fs)
    max_lag = int(max_period * fs)

    min_lag = max(min_lag, 1)
    max_lag = min(max_lag, len(corr) - 1)

    if max_lag <= min_lag:
        return None

    search_corr = corr[min_lag:max_lag + 1] #要搜索的相关系数范围，要在频率范围内

    if len(search_corr) < 3:
        return None

    peak_local_idx = int(np.argmax(search_corr))  #找到相关系数最大的点的index，赋值给peak_local_idx
    best_lag = min_lag + peak_local_idx  #找到相关系数最大的点对应的lag，加上被截去的min_lag，赋值给best_lag

    acf_score = float(corr[best_lag])  #相关系数最大的点对应的相关系数，赋值给acf_score
    acf_score = max(0.0, acf_score)   

    # ACF 周期计算：
    # best_lag = 最像自己的延迟点数
    # fs = 每秒采样点数
    # period = best_lag / fs
    f0 = fs / best_lag if best_lag > 0 else 0.0
    period_sec = 1.0 / f0 if f0 > 1e-9 else 0.0

    return {
        "acf_score": acf_score,
        "f0": f0,
        "period_sec": period_sec,
    }


def estimate_period_stability_from_peaks(t_sec, y):
    t_sec = np.asarray(t_sec, dtype=float)
    y = np.asarray(y, dtype=float)

    if len(y) < 80:
        return None

    y = y - np.mean(y)  #去直流分量

    std_y = np.std(y)  #计算标准差

    if std_y < 1e-9:
        return None

    z = np.abs(y) / std_y  #z-score 标准差

    threshold = np.percentile(z, 70) # 找到第70百分位数作为门槛

    peak_times = []

    for i in range(1, len(z) - 1):
        if z[i] >= threshold and z[i] >= z[i - 1] and z[i] >= z[i + 1]: #如果一个点大于阈值并比左右两点高，就是一个峰值
            peak_times.append(t_sec[i]) #把这个峰值的时间点添加到peak_times列表里

    if len(peak_times) < 3:
        return {
            "stability_score": 0.0,
            "period_cv": 999.0,
        }

    peak_times = np.asarray(peak_times, dtype=float)

    intervals = np.diff(peak_times) #计算峰值间隔时间

    min_period = 1.0 / LOW_FREQ_MAX
    max_period = 1.0 / LOW_FREQ_MIN

    intervals = intervals[   
        (intervals >= min_period * 0.5)
        & (intervals <= max_period * 1.5)
    ]  #只保留在频率范围内的峰值间隔时间

    if len(intervals) < 2:
        return {
            "stability_score": 0.0,
            "period_cv": 999.0,
        }

    mean_period = float(np.mean(intervals))  #计算平均峰值间隔时间
    std_period = float(np.std(intervals))

    if mean_period < 1e-9:
        return {
            "stability_score": 0.0,
            "period_cv": 999.0,
        }

    period_cv = std_period / mean_period  #标准差和平均值的比，越大说明间隔不稳定

    stability_score = 1.0 / (1.0 + 2.0 * period_cv) #用一个函数把period_cv转成0-1之间的稳定性分数，period_cv越小，越稳定

    return {
        "stability_score": float(stability_score),
        "period_cv": float(period_cv),
    }


def compute_period_stability_amp_score(x_ms, y, fs):
    if x_ms is None or y is None or fs is None:
        return None

    x_ms = np.asarray(x_ms, dtype=float)
    y = np.asarray(y, dtype=float)

    if len(x_ms) < 80 or len(y) < 80:
        return None

    t_sec = x_ms / 1000.0

    t_end = t_sec[-1]  #最后一个时间点(最新)
    mask = t_sec >= (t_end - SELECT_WINDOW_SEC) #得到boolean数组，表示时间点是否在SELECT_WINDOW_SEC秒内

    t_sec = t_sec[mask]  #得到在SELECT_WINDOW_SEC秒内的时间点
    y = y[mask] #得到在SELECT_WINDOW_SEC秒内的信号数据

    if len(y) < 80:
        return None

    y = y - np.mean(y)  #去直流分量

    rms = float(np.sqrt(np.mean(y ** 2)))  
    p2p = float(np.percentile(y, 95) - np.percentile(y, 5))

    if rms < 1e-9:
        return None

    fft_info = estimate_periodicity_by_fft(t_sec, y, fs)  #返回FFT分析函数的结果
    acf_info = estimate_periodicity_by_autocorr(t_sec, y, fs) #返回ACF分析函数的结果
    stable_info = estimate_period_stability_from_peaks(t_sec, y) #返回峰值分析函数的结果

    if fft_info is None and acf_info is None:
        return None

    acf_score = 0.0
    fft_peak_ratio = 0.0
    band_power_ratio = 0.0

    f0 = 0.0
    period_sec = 0.0

    # 优先使用 ACF 的周期
    if acf_info is not None:
        acf_score = acf_info["acf_score"]
        f0 = acf_info["f0"]
        period_sec = acf_info["period_sec"]

    if fft_info is not None:
        fft_peak_ratio = fft_info["fft_peak_ratio"]
        band_power_ratio = fft_info["band_power_ratio"]

        # 只在 ACF 没有给出有效周期时，才用 FFT 的周期
        # 这样可以避免尖峰型周期信号被 FFT 二次谐波识别成一半周期
        if f0 <= 1e-9:
            f0 = fft_info["f0"]
            period_sec = fft_info["period_sec"]

    if stable_info is None:
        stability_score = 0.0
        period_cv = 999.0
    else:
        stability_score = stable_info["stability_score"]  #得到稳定性评分
        period_cv = stable_info["period_cv"]

    periodic_score = ( 
        0.50 * acf_score
        + 0.35 * fft_peak_ratio
        + 0.15 * band_power_ratio
    ) #加权得到周期评分

    amplitude_score = (
        AMP_RMS_WEIGHT * rms
        + AMP_P2P_WEIGHT * p2p
    )

    return {
        "periodic_score": float(periodic_score),
        "stability_score": float(stability_score),
        "amplitude_score": float(amplitude_score),

        "rms": rms,
        "p2p": p2p,

        "f0": float(f0),
        "period_sec": float(period_sec),

        "acf_score": float(acf_score),
        "fft_peak_ratio": float(fft_peak_ratio),
        "band_power_ratio": float(band_power_ratio),
        "period_cv": float(period_cv),
    }



# =========================
# 根据评分选择最佳ESP32接收端
# =========================
def choose_best_ip_by_period_stability_amplitude():
    results = {}

    for ip in TARGET_IPS:
        state = device_states[ip]

        with state["lock"]:
            x = np.array(state["cached_bp_x"], dtype=float)  #bandpass最终结果的时间点
            y = np.array(state["cached_bp_y"], dtype=float)  #bandpass最终结果的信号数据
            fs = state["cached_bp_fs"]  #bandpass最终结果的采样率

        info = compute_period_stability_amp_score(x, y, fs)  #计算综合评分

        results[ip] = {
            "info": info
        }

    info_113 = results[ESP1_IP]["info"]  #ESP1的评分结果
    info_110 = results[ESP2_IP]["info"]  #ESP2的评分结果

    if info_113 is None and info_110 is None:
        return "NA", results

    if info_113 is not None and info_110 is None:
        return ESP1_IP, results

    if info_110 is not None and info_113 is None:
        return ESP2_IP, results

    p113 = info_113["periodic_score"]  #ESP1的周期性评分
    p110 = info_110["periodic_score"]

    s113 = info_113["stability_score"]  #ESP1的稳定性评分
    s110 = info_110["stability_score"]

    a113 = info_113["amplitude_score"]  #ESP1的幅度评分
    a110 = info_110["amplitude_score"]

    if abs(p113 - p110) > PERIODIC_TIE_MARGIN:  #如果周期性评分差值大于阈值，则返回周期性评分更高的那个IP
        if p113 > p110:
            return ESP1_IP, results
        else:
            return ESP2_IP, results

    if abs(s113 - s110) > STABILITY_TIE_MARGIN:  #如果周期性评分差不大，稳定性评分差值大于阈值，则返回稳定性评分更高的那个IP
        if s113 > s110:
            return ESP1_IP, results
        else:
            return ESP2_IP, results

    if a113 >= a110:                 #如果周期稳定评分都接近，则返回幅度评分更高的那个IP
        return ESP1_IP, results
    else:
        return ESP2_IP, results


# =========================
# 更新选择的ip的数据
# =========================
def update_selection_state(best_ip, results):
    global selection_state

    empty_state = {
        "best_ip": "NA",
        "mode": "not_ready",

        "periodic_score": 0.0,
        "stability_score": 0.0,
        "amplitude_score": 0.0,

        "rms": 0.0,
        "p2p": 0.0,

        "period_sec": 0.0,
        "f0": 0.0,

        "acf_score": 0.0,
        "fft_peak_ratio": 0.0,
        "band_power_ratio": 0.0,
        "period_cv": 0.0,
    }

    if best_ip == "NA":
        selection_state = empty_state
        return

    info = results[best_ip]["info"]

    if info is None:
        selection_state = empty_state
        return

    selection_state = {
        "best_ip": best_ip,
        "mode": "period_stability_amplitude",

        "periodic_score": info["periodic_score"],
        "stability_score": info["stability_score"],
        "amplitude_score": info["amplitude_score"],

        "rms": info["rms"],
        "p2p": info["p2p"],

        "period_sec": info["period_sec"],
        "f0": info["f0"],

        "acf_score": info["acf_score"],
        "fft_peak_ratio": info["fft_peak_ratio"],
        "band_power_ratio": info["band_power_ratio"],
        "period_cv": info["period_cv"],
    }


# =========================
# UDP 接收线程
# =========================
def udp_receiver_thread():
    global stop_flag

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)

    try:
        sock.bind((UDP_IP, UDP_PORT))
    except Exception as e:
        log("UDP bind error:", e)
        return

    sock.settimeout(0.2)

    log(f"UDP receiver thread started: {UDP_IP}:{UDP_PORT}")
    log(f"Processing IPs: {TARGET_IPS}")

    last_debug_time = 0

    while not stop_flag:
        try:
            data, addr = sock.recvfrom(4096)
            ip = addr[0]

            if ip not in device_states:
                continue

            # 现在读取的是 elapsed_us，不是 elapsed_ms
            elapsed_us, amplitudes_raw = parse_udp_packet(data)

            if amplitudes_raw is None or len(amplitudes_raw) == 0:
                continue

            state = device_states[ip]

            with state["lock"]:
                state["packet_count"] += 1

                if state["num_subcarriers"] is None:
                    state["num_subcarriers"] = len(amplitudes_raw)

                    state["startup_raw"] = [
                        deque(maxlen=SELECT_TOP5_AFTER_POINTS)
                        for _ in range(state["num_subcarriers"])
                    ]

                    state["startup_hampel"] = [
                        deque(maxlen=SELECT_TOP5_AFTER_POINTS)
                        for _ in range(state["num_subcarriers"])
                    ]

                    log(f"[{ip}] detected subcarriers: {state['num_subcarriers']}")

                if len(amplitudes_raw) != state["num_subcarriers"]:
                    continue

                state["sample_count"] += 1

                # 第一个包的 elapsed_us 作为本机相对时间 0 点
                if state["first_elapsed_us"] is None:
                    state["first_elapsed_us"] = elapsed_us

                state["last_elapsed_us"] = elapsed_us

                # 相对时间，单位 us
                t_rel_us = float(elapsed_us - state["first_elapsed_us"])

                # 绘图横轴用 ms，方便看
                t_rel_ms = t_rel_us / 1000.0

                # 滤波、Fs、ACF 用 s
                # 这里就是用 ESP32 发来的 us 时间戳计算出来的秒
                t_rel_s = t_rel_us / 1_000_000.0

                state["time_data"].append(t_rel_ms)
                state["real_time_data"].append(t_rel_s)

                if not state["top5_locked"]:
                    if ENABLE_HAMPEL:
                        amplitudes_hampel = hampel_all_startup(state, amplitudes_raw)
                    else:
                        amplitudes_hampel = amplitudes_raw

                    for i in range(state["num_subcarriers"]):
                        state["startup_raw"][i].append(amplitudes_raw[i])
                        state["startup_hampel"][i].append(amplitudes_hampel[i])

                    try_select_top5_by_std_locked(ip, state)

                else:
                    process_selected_top5_only(state, amplitudes_raw)

            now = time.time()

            if now - last_debug_time > 2.0:
                s1 = device_states[ESP1_IP]
                s2 = device_states[ESP2_IP]

                log(
                    f"Packets: "
                    f"{ESP1_IP}={s1['packet_count']}, "
                    f"{ESP2_IP}={s2['packet_count']} | "
                    f"Top5 locked: "
                    f"{ESP1_IP}={s1['top5_locked']}, "
                    f"{ESP2_IP}={s2['top5_locked']}"
                )

                last_debug_time = now

        except socket.timeout:
            continue

        except Exception as e:
            log("UDP receiver error:", e)

    sock.close()
    log("UDP receiver stopped.")


# =========================
# 绘图初始化：四张图
# =========================
fig, axes = plt.subplots(4, 1, figsize=(12, 8))
fig.subplots_adjust(hspace=0.4, top=0.92)

selection_text = fig.text(
    0.01,
    0.985,
    "Selected IP: waiting...",
    fontsize=10,
    ha="left",
    va="top"
)

ax_113_hampel = axes[0]
ax_113_bp = axes[1]
ax_110_hampel = axes[2]
ax_110_bp = axes[3]


def setup_hampel_ax(ax, ip):
    ax.set_title(f"CSI Realtime Hampel - {ip}")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Amplitude after Hampel")
    ax.grid(True)

    lines = []

    for _ in range(TOP_K):
        line, = ax.plot([], [], linewidth=1.3)
        lines.append(line)

    return lines


def setup_bp_ax(ax, ip):
    ax.set_title(f"CSI Realtime Bandpass + Gain - {ip}")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Bandpassed Value")
    ax.grid(True)

    line, = ax.plot([], [], linewidth=1.6, label="Top1 Bandpass * Gain")

    return line


lines_113_hampel = setup_hampel_ax(ax_113_hampel, ESP1_IP)
line_113_bp = setup_bp_ax(ax_113_bp, ESP1_IP)

lines_110_hampel = setup_hampel_ax(ax_110_hampel, ESP2_IP)
line_110_bp = setup_bp_ax(ax_110_bp, ESP2_IP)


# =========================
# 更新 Hampel 图
# =========================
def update_hampel_plot(ip, ax, lines, frame_id):
    state = device_states[ip]

    with state["lock"]:
        sample_count = state["sample_count"]
        packet_count = state["packet_count"]
        top5_locked = state["top5_locked"]
        selected_indices = list(state["selected_indices"])
        selected_info = list(state["selected_info"])
        legend_created = state["legend_hampel_created"]

        t_plot = list(state["time_data"])[-DRAW_POINTS:]

        if top5_locked and len(state["selected_hampel"]) > 0:
            y_lists = [
                list(state["selected_hampel"][rank])[-DRAW_POINTS:]
                for rank in range(len(selected_indices))
            ]
            show_indices = selected_indices
        else:
            y_lists = []
            show_indices = []

    if sample_count == 0:
        ax.set_title(f"CSI Realtime Hampel - {ip} | no data")
        return

    if not top5_locked:
        ax.set_title(
            f"CSI Realtime Hampel - {ip} | selecting TOP5 by STD... "
            f"samples={sample_count} | packets={packet_count}"
        )
        return

    if len(t_plot) == 0 or len(y_lists) == 0:
        ax.set_title(f"CSI Realtime Hampel - {ip} | waiting data")
        return

    y_min_total = float("inf")
    y_max_total = float("-inf")

    active_line_count = 0

    for rank, y_data in enumerate(y_lists):
        if rank >= len(lines):
            continue

        min_len = min(len(t_plot), len(y_data))

        if min_len <= 1:
            continue

        t_use = np.array(t_plot[-min_len:], dtype=float)
        y_use = np.array(y_data[-min_len:], dtype=float)

        if USE_OFFSET:
            y_display = y_use + rank * OFFSET_STEP
        else:
            y_display = y_use

        lines[rank].set_data(t_use, y_display)
        active_line_count += 1

        if not legend_created:
            if rank < len(selected_info):
                sc, score = selected_info[rank]
                lines[rank].set_label(f"SC {sc} | std={score:.3f}")
            else:
                lines[rank].set_label(f"SC {show_indices[rank]}")

        y_min_total = min(y_min_total, float(np.min(y_display)))
        y_max_total = max(y_max_total, float(np.max(y_display)))

    for rank in range(active_line_count, len(lines)):
        lines[rank].set_data([], [])

    if len(t_plot) > 1:
        ax.set_xlim(t_plot[0], t_plot[-1])

    if frame_id % YLIM_UPDATE_EVERY == 0:
        if y_min_total != float("inf"):
            margin = (y_max_total - y_min_total) * 0.1

            if margin == 0:
                margin = 1

            ax.set_ylim(y_min_total - margin, y_max_total + margin)

    if not legend_created:
        ax.legend(loc="upper right", fontsize="small", ncol=2)

        with state["lock"]:
            state["legend_hampel_created"] = True

    selected_text = ", ".join(str(x) for x in selected_indices)

    ax.set_title(
        f"CSI Realtime Hampel - {ip} | "
        f"TOP5 fixed by STD: {selected_text} | "
        f"samples={sample_count} | packets={packet_count}"
    )


# =========================
# 重算 bandpass 缓存
# =========================
def recompute_bandpass_cache(ip, linked_hampel_ax):
    state = device_states[ip]

    with state["lock"]:
        top5_locked = state["top5_locked"]
        selected_indices = list(state["selected_indices"])

        # real_time_data 是由 elapsed_us / 1_000_000 得到的秒
        t_arr_sec = np.array(state["real_time_data"], dtype=float)

        if (
            not top5_locked
            or len(selected_indices) == 0
            or len(state["selected_hampel"]) == 0
            or len(t_arr_sec) < 30
        ):
            return False

        top1_sc = selected_indices[0]

        y_top1_hampel = np.array(
            state["selected_hampel"][0],
            dtype=float
        )

    x1_min, x1_max = linked_hampel_ax.get_xlim()
    
    #使用bandpass处理函数得到bandpass后的时间点和信号，以及采样率和实际使用的高频截止
    t_bp, y_bp, fs, high_used = bandpass_signal_with_margin_and_trim(
        t_arr_sec,
        y_top1_hampel,
        x1_min,
        x1_max,
        BANDPASS_EXTRA_SEC,
        LEFT_TRIM_SEC,
        RIGHT_TRIM_SEC,
        BP_LOW_HZ,
        BP_HIGH_HZ_REQUEST,
        order=BP_ORDER
    )

    if t_bp is None or y_bp is None:
        return False

    x_ms = t_bp * 1000.0  #把bandpass后的时间点转成ms
    y_final = y_bp * TOP1_GAIN  #对bandpass结果乘一个增益

    with state["lock"]:
        state["cached_bp_x"] = x_ms
        state["cached_bp_y"] = y_final
        state["cached_bp_fs"] = fs
        state["cached_bp_high"] = high_used
        state["cached_bp_top1"] = top1_sc
        state["last_fs"] = fs
        state["last_bp_high_used"] = high_used

    return True


# =========================
# 更新 bandpass 图
# =========================
def update_bandpass_plot(ip, ax, line, linked_hampel_ax, frame_id):
    state = device_states[ip]

    with state["lock"]:
        sample_count = state["sample_count"]
        packet_count = state["packet_count"]
        top5_locked = state["top5_locked"]
        selected_indices = list(state["selected_indices"])

    if sample_count == 0:
        line.set_data([], [])
        ax.set_title(f"CSI Realtime Bandpass + Gain - {ip} | no data")
        return

    if not top5_locked or len(selected_indices) == 0:
        line.set_data([], [])
        ax.set_title(
            f"CSI Realtime Bandpass + Gain - {ip} | "
            f"waiting TOP5 selection | samples={sample_count}"
        )
        return

    if frame_id % BANDPASS_UPDATE_EVERY == 0:
        recompute_bandpass_cache(ip, linked_hampel_ax)

    with state["lock"]:
        x_cached = np.array(state["cached_bp_x"], dtype=float)
        y_cached = np.array(state["cached_bp_y"], dtype=float)
        fs = state["cached_bp_fs"]
        high_used = state["cached_bp_high"]
        top1_sc = state["cached_bp_top1"]

    if len(x_cached) == 0 or len(y_cached) == 0:
        line.set_data([], [])
        ax.set_title(
            f"CSI Realtime Bandpass + Gain - {ip} | "
            f"bandpass not ready | samples={sample_count}"
        )
        return

    line.set_data(x_cached, y_cached)

    x1_min, x1_max = linked_hampel_ax.get_xlim()
    ax.set_xlim(x1_min, x1_max)

    if frame_id % YLIM_UPDATE_EVERY == 0:
        valid_y = y_cached[~np.isnan(y_cached)]

        if len(valid_y) > 0:
            abs_max = np.max(np.abs(valid_y))

            if abs_max <= FIXED_Y_LIM:
                ax.set_ylim(-FIXED_Y_LIM, FIXED_Y_LIM)
            else:
                ymax = abs_max * 1.08
                ax.set_ylim(-ymax, ymax)

    if not state["legend_bp_created"]:
        line.set_label("Top1 Bandpass * Gain")
        ax.legend(loc="upper right", fontsize="small")

        with state["lock"]:
            state["legend_bp_created"] = True

    fs_txt = "NA" if fs is None else f"{fs:.2f}"
    high_txt = "NA" if high_used is None else f"{high_used:.2f}"
    top1_txt = "NA" if top1_sc is None else str(top1_sc)

    ax.set_title(
        f"CSI Realtime Bandpass+Gain - {ip} | "
        f"top1 SC {top1_txt} | "
        f"gain={TOP1_GAIN:.1f} | "
        f"BP={BP_LOW_HZ:.2f}-{high_txt}Hz | "
        f"fs={fs_txt}Hz | "
        f"samples={sample_count} | packets={packet_count}"
    )


# =========================
# 更新顶部选择小字
# =========================
def update_selection_text():
    selection_text.set_text(
        f"Selected IP: {selection_state['best_ip']} | "
        f"mode={selection_state['mode']} | "
        f"periodic={selection_state['periodic_score']:.3f} | "
        f"stability={selection_state['stability_score']:.3f} | "
        f"amp={selection_state['amplitude_score']:.3f} | "
        f"period={selection_state['period_sec']:.2f}s | "
        f"f0={selection_state['f0']:.3f}Hz | "
        f"rms={selection_state['rms']:.3f} | "
        f"p2p={selection_state['p2p']:.3f} | "
        f"acf={selection_state['acf_score']:.3f} | "
        f"fft_peak={selection_state['fft_peak_ratio']:.3f} | "
        f"band_power={selection_state['band_power_ratio']:.3f} | "
        f"period_cv={selection_state['period_cv']:.3f}"
    )


# =========================
# 动画更新：四张图
# =========================
def update_plot(frame_id):
    update_hampel_plot(
        ESP1_IP,
        ax_113_hampel,
        lines_113_hampel,
        frame_id
    )

    update_bandpass_plot(
        ESP1_IP,
        ax_113_bp,
        line_113_bp,
        ax_113_hampel,
        frame_id
    )

    update_hampel_plot(
        ESP2_IP,
        ax_110_hampel,
        lines_110_hampel,
        frame_id
    )

    update_bandpass_plot(
        ESP2_IP,
        ax_110_bp,
        line_110_bp,
        ax_110_hampel,
        frame_id
    )

    if frame_id % SELECTION_UPDATE_EVERY == 0:
        best_ip, results = choose_best_ip_by_period_stability_amplitude()
        update_selection_state(best_ip, results)

        log("\n========== Period > Stability > Amplitude Selection ==========")
        log(f"Best IP: {selection_state['best_ip']}")

        for ip in TARGET_IPS:
            info = results[ip]["info"]

            if info is None:
                log(f"{ip}: not ready")
                continue

            log(
                f"{ip}: "
                f"periodic={info['periodic_score']:.3f}, "
                f"stability={info['stability_score']:.3f}, "
                f"amp={info['amplitude_score']:.3f}, "
                f"period={info['period_sec']:.2f}s, "
                f"f0={info['f0']:.3f}Hz, "
                f"rms={info['rms']:.3f}, "
                f"p2p={info['p2p']:.3f}, "
                f"acf={info['acf_score']:.3f}, "
                f"fft_peak={info['fft_peak_ratio']:.3f}, "
                f"band_power={info['band_power_ratio']:.3f}, "
                f"period_cv={info['period_cv']:.3f}"
            )

    update_selection_text()

    return (
        lines_113_hampel
        + [line_113_bp]
        + lines_110_hampel
        + [line_110_bp]
        + [selection_text]
    )


# =========================
# 启动
# =========================
receiver = threading.Thread(target=udp_receiver_thread, daemon=True) #创建并启动 UDP 接收线程
receiver.start()

ani = FuncAnimation(
    fig,
    update_plot,
    interval=PLOT_INTERVAL_MS,
    blit=False,
    cache_frame_data=False
)

try:
    plt.show()

finally:
    stop_flag = True
    time.sleep(0.5)
    plt.close("all")

    if receiver.is_alive():
        receiver.join(timeout=1.0)