"""mm beats：分析音乐 → beats.json（拍子、小节、能量分段、鼓点、高潮、突停）+ 一张给人看的图；
再把 "bar:3"、"drop:1" 这类写法换成秒，让镜头按节拍排，不用手写秒数。

用法
  mm beats 音乐.mp3 [--out beats.json] [--bpm 118] [--first-bar 0.52]
  mm beats 音乐.mp3 --cuts "bar:1,bar:3,drop:1,bar:9"     打印每个写法对应的秒数
  mm beats beats.json --where "0,3.16,5.2"                 反查：这些秒落在第几小节第几拍
  （第一个参数给已经分析好的 beats.json 就不再重新分析，秒出）

只用 numpy / scipy / pillow（和 mm.py 其它命令同一套依赖），不装 librosa：
librosa 会拖进 numba + llvmlite（上百 MB，部分平台要编译），而这里只需要 onset 强度、自相关求 BPM、
动态规划对拍子三步，自己写不到两百行。

------------------------------------------------------------------------------------------
出处与许可（Third-party notice）
  部分分析思路和参数改写自 HyperFrames 的 skills/music-to-video/scripts/analyze-beatgrid.py
  https://github.com/heygen-com/hyperframes （commit f8a1e2d，2026-08-18）
  Copyright 2026 HeyGen, Inc.
  Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
  in compliance with the License. You may obtain a copy of the License at
      http://www.apache.org/licenses/LICENSE-2.0
  Unless required by applicable law or agreed to in writing, software distributed under the License
  is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
  or implied. See the License for the specific language governing permissions and limitations.

  借过来的部分（已改动）：
    - 鼓点分类 _classify_drum：三段频带（<150 Hz 底鼓 / 150–900 Hz 军鼓 / >5 kHz 镲）各自的起音通量谁大算谁，
      阈值 0.06、噪声度 0.30 照原版；
    - 小节头判断 _downbeat_phase：哪一组拍子上的底鼓最重，哪组就是每小节第 1 拍（这里另加了和声变化一项）；
    - 能量分级 VOID/LOW/MEDIUM/HIGH（0.2 / 0.4 / 0.65）和「相邻两格能量差 > 0.12 算猛涨 / 猛跌」；
    - 「信任边界」：节奏不明显的曲子，BPM 和拍子网格不可信，按能量和段落切，不硬卡拍。
  改动：去掉 librosa / soundfile，全部改成 numpy/scipy；拍子跟踪自己实现；能量按小节算（响度 + 鼓点密度），按配器变化切段、对齐小节线；
    原版的 SURGE 在这里叫 drop（EDM 说法：高潮砸下来），原版的 DROP 在这里叫 dip；输出格式改成 beats.json。
  拍子跟踪按 Ellis (2007) "Beat Tracking by Dynamic Programming" 的方法自己写，参数取法参考了 librosa（ISC 许可）。
  详见 skill 根目录 THIRD_PARTY_NOTICES.md。本文件其余部分按 mockup-motion 的 MIT 许可发布。
------------------------------------------------------------------------------------------
"""
import json, os, re, subprocess, sys, tempfile, wave

import numpy as np

SR = 22050
HOP = 512                  # 一帧约 23 ms
NFFT = 2048
FPS = SR / HOP
VERSION = 1

LEVELS = (('VOID', 0.2), ('LOW', 0.4), ('MEDIUM', 0.65), ('HIGH', 9))      # 照 HyperFrames
SURGE = 0.12                                                                # 相邻两格能量差超过这个算猛涨 / 猛跌


# ── 解码 ──────────────────────────────────────────────────────────────────────
def load_audio(path):
    """任何 ffmpeg 读得了的文件（mp3 / wav / m4a / mp4 视频的音轨）→ 单声道 float32 @ 22050"""
    if not os.path.exists(path):
        raise SystemExit(f'找不到这个文件：{path}')
    try:
        from mmlib.browser import ffmpeg_exe
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from mmlib.browser import ffmpeg_exe
    fd, tmp = tempfile.mkstemp(suffix='.wav'); os.close(fd)
    try:
        r = subprocess.run([ffmpeg_exe(), '-y', '-hide_banner', '-i', path, '-vn', '-ac', '1', '-ar', str(SR), '-sample_fmt', 's16', tmp],
                           capture_output=True, text=True)
        if r.returncode != 0 or os.path.getsize(tmp) < 1000:
            if 'does not contain any stream' in r.stderr or 'Output file #0 does not contain' in r.stderr or 'matches no streams' in r.stderr:
                raise SystemExit(f'{os.path.basename(path)} 里没有声音（这个视频没有音轨）')
            raise SystemExit(f'读不了 {os.path.basename(path)}：ffmpeg 说「{(r.stderr.strip().splitlines() or ["没有输出"])[-1]}」。换成 mp3 / wav 再试')
        with wave.open(tmp) as w:
            y = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    finally:
        try: os.unlink(tmp)
        except OSError: pass
    if len(y) < SR * 2:
        raise SystemExit(f'音乐太短（{len(y) / SR:.1f} 秒），至少要 2 秒才分析得出拍子')
    return y


# ── 频谱 ──────────────────────────────────────────────────────────────────────
def _spectrum(y):
    """功率谱 S[频率, 帧]，帧居中（第 t 帧 = t × HOP / SR 秒）"""
    from scipy.fft import rfft
    yp = np.pad(y, NFFT // 2, mode='reflect')
    n = 1 + (len(yp) - NFFT) // HOP
    frames = np.lib.stride_tricks.as_strided(yp, (n, NFFT), (yp.strides[0] * HOP, yp.strides[0]))
    S = np.abs(rfft(frames * np.hanning(NFFT).astype(np.float32), axis=1)).astype(np.float32) ** 2
    return S.T, np.fft.rfftfreq(NFFT, 1 / SR)


def _mel_fb(freqs, n=64, fmin=30.0, fmax=11000.0):
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    imel = lambda m: 700 * (10 ** (m / 2595) - 1)
    pts = imel(np.linspace(mel(fmin), mel(fmax), n + 2))
    fb = np.zeros((n, len(freqs)), np.float32)
    for i in range(n):
        lo, c, hi = pts[i], pts[i + 1], pts[i + 2]
        fb[i] = np.clip(np.minimum((freqs - lo) / (c - lo), (hi - freqs) / (hi - c)), 0, None)
    return fb


def _onset_env(S, freqs):
    """onset 强度：对数 mel 谱逐帧正向差的平均（和 librosa onset_strength 同一路子）"""
    M = _mel_fb(freqs) @ S
    D = 10 * np.log10(np.maximum(M, 1e-10))
    D = np.maximum(D, D.max() - 80)
    flux = np.maximum(0, np.diff(D, axis=1, prepend=D[:, :1])).mean(axis=0)
    return flux, D


# ── BPM ───────────────────────────────────────────────────────────────────────
def _tempo(env, start_bpm=120.0):
    """自相关 + 以 120 BPM 为中心的对数高斯先验（宽一个八度），峰值做抛物线插值；再看半速 / 倍速哪个更像"""
    x = env - env.mean()
    n = len(x)
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac /= ac[0] + 1e-9
    lags = np.arange(1, n)
    bpm = 60 * FPS / lags
    ok = (bpm >= 50) & (bpm <= 220)
    prior = np.exp(-0.5 * (np.log2(bpm / start_bpm)) ** 2)
    score = np.where(ok, ac[1:] * prior, -1)
    L = int(lags[np.argmax(score)])
    if 1 < L < n - 1:
        a, b, c = ac[L - 1], ac[L], ac[L + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
        Lf = L + float(np.clip(d, -0.5, 0.5))
    else:
        Lf = float(L)
    clarity = float(max(0.0, ac[L]))
    return 60 * FPS / Lf, clarity


# ── 拍子跟踪（动态规划，Ellis 2007）────────────────────────────────────────────
def _track(env, bpm, tightness=100.0):
    period = 60 * FPS / bpm
    e = env / (env.std() + 1e-9)
    w = np.arange(-int(period), int(period) + 1)
    local = np.convolve(e, np.exp(-0.5 * (w * 32.0 / period) ** 2), 'same')
    n = len(local)
    back = np.full(n, -1)
    cum = np.zeros(n)
    win = np.arange(-int(round(2 * period)), -int(round(period / 2)) + 1)
    txwt = -tightness * np.log(-win / period) ** 2
    thresh = 0.01 * local.max()
    started = False
    for i in range(n):
        idx = i + win
        valid = idx >= 0
        if not valid.any():
            cum[i] = local[i]
            continue
        cand = np.where(valid, txwt + cum[np.clip(idx, 0, None)], -np.inf)
        k = int(np.argmax(cand))
        cum[i] = local[i] + cand[k]
        if not started and local[i] < thresh:
            back[i] = -1; cum[i] = local[i]
        else:
            back[i] = idx[k]; started = True
    # 最后一拍：cum 局部极大里最后一个够分量的
    peaks = np.flatnonzero((cum[1:-1] > cum[:-2]) & (cum[1:-1] >= cum[2:])) + 1
    if not len(peaks):
        return np.array([], int), local
    good = peaks[cum[peaks] >= 0.5 * np.median(cum[peaks])]
    i = int(good[-1])
    beats = []
    while i >= 0:
        beats.append(i); i = back[i]
    beats = np.array(beats[::-1])
    # 头尾去掉没劲的拍
    if len(beats):
        sm = np.convolve(local, np.hanning(9), 'same')
        th = 0.5 * np.sqrt((sm[beats] ** 2).mean())
        keep = np.flatnonzero(sm[beats] > th)
        if len(keep):
            beats = beats[keep[0]:keep[-1] + 1]
    return beats, local


def _steady_grid(beat_t, period_s, dur, audible, fixed_period=False):
    """拍子大致匀速（电子乐、代码合成的音乐、按节拍器录的歌）→ 拉成一条直线网格，补齐中间漏掉的拍、去掉抖动。
    返回 (网格 or None, 残差秒)"""
    if len(beat_t) < 8:
        return None, None
    idx = np.round((beat_t - beat_t[0]) / period_s)
    keep = np.ones(len(idx), bool)
    for _ in range(3):                       # 拟合直线 → 去掉跟错的个别拍 → 再拟合（跟踪器偶尔一两拍跑偏，不代表曲子不匀速）
        if fixed_period:
            b = period_s
            a = float(np.median(beat_t[keep] - idx[keep] * b))
        else:
            b, a = np.polyfit(idx[keep], beat_t[keep], 1)
        res = beat_t - (a + b * idx)
        keep = np.abs(res) < max(0.07, 0.1 * b)
    rms = float(np.sqrt((res[keep] ** 2).mean())) if keep.any() else 1.0
    if keep.mean() < 0.75 or rms > 0.035:
        return None, rms
    # 两头往外延：网格点附近有起音才算
    def has_hit(t):                             # 两头往外延到「还听得见」为止
        i = max(0, int(round(t * 100)))
        return i < len(audible) and bool(audible[max(0, i - 3):i + 4].any())
    lo, hi = int(idx[0]), int(idx[-1])
    while a + (lo - 1) * b >= -0.04 and has_hit(a + (lo - 1) * b): lo -= 1
    while a + (hi + 1) * b <= dur - 0.05 and has_hit(a + (hi + 1) * b): hi += 1
    grid = a + b * np.arange(lo, hi + 1)
    return grid, rms


def _refine(beats, y, steady):
    """拍子对到起音的真实位置：频谱帧 23 ms 一格、窗 93 ms，峰会比真正的起音早十几毫秒。
    用 3 ms 一格的响度上升曲线在 ±35 ms 里找最合适的偏移；匀速网格整体挪一个偏移，逐拍跟踪的每拍各自挪。"""
    h = 64
    n = len(y) // h
    e = np.log(np.sqrt((y[:n * h].reshape(n, h) ** 2).mean(1)) + 1e-4)
    rise = np.maximum(0, np.diff(e, prepend=e[0]))
    rise = np.convolve(rise, np.ones(3) / 3, 'same')
    fs = SR / h
    shifts = np.arange(-int(0.035 * fs), int(0.035 * fs) + 1)
    idx = np.round(np.asarray(beats) * fs).astype(int)
    def val(i): return rise[np.clip(i, 0, n - 1)]
    if steady:
        sc = [val(idx + d).sum() for d in shifts]
        g = np.asarray(beats) + shifts[int(np.argmax(sc))] / fs
        return np.where((g < 0) & (g > -0.04), 0.0, g)[g > -0.04]
    out = []
    for i, t in zip(idx, beats):
        w = val(i + shifts)
        out.append(max(0.0, t + shifts[int(np.argmax(w))] / fs) if w.max() > 0 else t)
    return np.array(out)


# ── 频带与鼓点（改写自 HyperFrames）────────────────────────────────────────────
def _bands(S, freqs):
    def flux(e):
        f = np.maximum(0, np.diff(np.sqrt(e), prepend=np.sqrt(e[:1])))
        return f / (f.max() + 1e-9)
    low, mid, high = S[freqs < 150].sum(0), S[(freqs >= 150) & (freqs < 900)].sum(0), S[freqs >= 5000].sum(0)
    tot = S.sum(0) + 1e-9
    mag = np.sqrt(S) + 1e-12
    flat = np.exp(np.log(mag).mean(0)) / mag.mean(0)
    sel = (freqs >= 150) & (freqs < 8000)
    nflat = np.exp(np.log(mag[sel]).mean(0)) / mag[sel].mean(0)        # 中频噪声度：拍手 / 军鼓高，乐器音低
    nflat = np.convolve(nflat, np.ones(3) / 3, 'same')
    return dict(nflat=nflat, low=low, mid=mid, high=high, total=tot, fl=flux(low), fm=flux(mid), fh=flux(high), flat=flat, n=S.shape[1])


def _classify_drum(f, bc):
    w = slice(max(0, f - 1), min(bc['n'], f + 2))
    fx = {'kick': float(bc['fl'][w].max()), 'snare': float(bc['fm'][w].max()), 'hihat': float(bc['fh'][w].max())}
    lead = max(fx, key=fx.get)
    flat = float(bc['flat'][w].mean())
    mr = float(bc['mid'][w].mean() / bc['total'][w].mean())
    if fx[lead] < 0.06:
        return 'glitch' if flat > 0.30 else 'perc'
    if lead == 'snare' and flat > 0.30 and mr < 0.30:
        return 'glitch'
    return lead


def _band_hits(flux, th=0.25, gap=0.09):
    """某个频带的起音通量里找峰：按峰值的 95 分位归一化（不被一个最响的点压扁），超过 th、间隔至少 gap 秒"""
    from scipy.signal import find_peaks
    pk, _ = find_peaks(flux, distance=max(1, int(gap * FPS)))
    if not len(pk): return []
    ref = np.quantile(flux[pk], 0.95) + 1e-9
    good = pk[flux[pk] / ref >= th]
    return [round(float(f / FPS), 3) for f in good]


def _peak_pick(env, delta=0.07, pre_max=0.03, pre_avg=0.1, post_avg=0.1, wait=0.03):
    """起音点：归一化 onset 强度的局部峰，比附近平均高 delta（参数同 librosa onset_detect 默认）"""
    x = (env - env.min()) / (env.max() - env.min() + 1e-9)
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    pm, pa, qa, wt = int(pre_max * FPS) + 1, int(pre_avg * FPS) + 1, int(post_avg * FPS) + 1, int(wait * FPS)
    mx = maximum_filter1d(x, 2 * pm + 1, mode='nearest')
    av = uniform_filter1d(x, pa + qa + 1, mode='nearest')
    cand = np.flatnonzero((x >= mx) & (x >= av + delta))
    out, last = [], -10 ** 9
    for c in cand:
        if c - last > wt:
            out.append(int(c)); last = c
    return np.array(out, int), x


def _downbeat_phase(beat_f, bc, D, bpb):
    """哪一组拍子上底鼓最重 + 和声变化最大，哪组就是小节第 1 拍（前一半照 HyperFrames）"""
    n = bc['n']
    kick = bc['low'] / bc['total']
    def at(arr, f): return float(arr[max(0, f - 2):min(n, f + 3)].max())
    k = np.array([at(kick, f) + at(bc['fl'], f) for f in beat_f])
    # 和声变化：拍子前后各一拍、1 kHz 以下 mel 带的中位谱差（换和弦多发生在小节头；用中位数压掉军鼓、拍手这种瞬间噪声）
    H = D[:36]
    span = max(3, int(np.median(np.diff(beat_f)))) if len(beat_f) > 1 else 10
    nov = np.array([float(np.abs(np.median(H[:, f:f + span], 1) - np.median(H[:, f - span:f], 1)).mean()) if span <= f < n - span else 0.0
                    for f in beat_f])
    z = lambda v: (v - v.mean()) / (v.std() + 1e-9)
    sc = z(k) + 0.5 * z(nov)
    best = max(range(bpb), key=lambda p: sc[p::bpb].mean() if len(sc[p::bpb]) else -9)
    return best


def _level(v):
    return next(name for name, th in LEVELS if v < th)


# ── 主分析 ────────────────────────────────────────────────────────────────────
def analyze(path, bpm=None, beats_per_bar=4, first_bar=None):
    y = load_audio(path)
    dur = len(y) / SR
    S, freqs = _spectrum(y)
    env, D = _onset_env(S, freqs)
    bc = _bands(S, freqs)

    auto_bpm, clarity = _tempo(env)
    use_bpm = float(bpm) if bpm else auto_bpm
    beat_f, local = _track(env, use_bpm, tightness=400.0 if bpm else 100.0)
    beat_t = beat_f / FPS
    period = 60 / use_bpm
    if len(beat_t) > 1 and not bpm:
        period = float(np.median(np.diff(beat_t)))
    on_f, on_n = _peak_pick(env)
    m10 = int(SR * 0.01); n10 = len(y) // m10
    db10 = 20 * np.log10(np.sqrt((y[:n10 * m10].reshape(n10, m10) ** 2).mean(1)) + 1e-7)
    audible = db10 > np.quantile(db10, 0.95) - 35
    grid, res = _steady_grid(beat_t, period, dur, audible, fixed_period=bool(bpm))
    steady = grid is not None
    beats = _refine(grid if steady else beat_t, y, steady)
    if len(beats) < 4:
        raise SystemExit('这段音乐找不出稳定的拍子（可能是没有鼓的氛围音乐、或者太短）。'
                         '可以用 --bpm 手动给速度；或者别卡拍，按 drop / stop / section 切')
    if steady and not bpm:
        final_bpm = 60 / float(np.median(np.diff(beats)))
    elif bpm:
        final_bpm = float(bpm)
    else:
        final_bpm = 60 / float(np.median(np.diff(beats)))
    ibi = np.diff(beats)
    cv = float(ibi.std() / ibi.mean())
    reliable = bool(clarity >= 0.2 and (steady or cv < 0.08))

    # 小节头
    bf = np.clip(np.round(beats * FPS).astype(int), 0, bc['n'] - 1)
    if first_bar is not None:
        i0 = int(np.argmin(np.abs(beats - first_bar)))
        if abs(beats[i0] - first_bar) > 0.25 * period:
            raise SystemExit(f'--first-bar {first_bar} 附近没有拍子（最近的拍在 {beats[i0]:.2f} 秒），看一眼 beats.png 再填')
        phase = i0 % beats_per_bar
    else:
        phase = _downbeat_phase(bf, bc, D, beats_per_bar)
    downbeats = beats[phase::beats_per_bar]

    # 起音点 + 鼓
    hits = []
    for f in on_f:
        d = _classify_drum(int(f), bc)
        hits.append(dict(t=round(f / FPS, 3), drum=d, strength=round(float(on_n[f]), 2)))
    # 三种鼓各自在自己的频带里找峰（一个时刻可以同时有底鼓和镲；hits 里的 drum 只记最突出的那个）
    kicks = _band_hits(bc['fl'])
    from scipy.ndimage import median_filter
    nf_bg = median_filter(bc['nflat'], int(FPS) | 1, mode='nearest')          # 附近 1 秒的底噪（有镲时整体就高）
    def noisy(t):           # 噪声要持续两三帧（军鼓、拍手），镲太短、乐器不够噪
        f = min(bc['n'] - 1, int(round(t * FPS)))
        return float(bc['nflat'][f:f + 3].mean()) > max(0.3, nf_bg[f] + 0.25)
    snares = [t for t in _band_hits(bc['fm']) if noisy(t)]
    hihats = _band_hits(bc['fh'])

    # 重拍：拍子上 onset 强度排前 20% 且明显高于中位
    bs = np.array([float(env[max(0, f - 2):f + 3].max()) for f in bf])
    acc_th = max(np.quantile(bs, 0.8), 1.3 * np.median(bs))
    accents = [round(float(t), 3) for t, s in zip(beats, bs) if s >= acc_th]

    # 能量曲线（0.1 秒一格，RMS / 最大值）
    step = 0.1
    m = int(SR * step)
    nb = len(y) // m
    rms = np.sqrt((y[:nb * m].reshape(nb, m) ** 2).mean(1))
    from scipy.ndimage import uniform_filter1d
    rms_s = uniform_filter1d(rms, 5)
    curve = rms_s / (rms_s.max() + 1e-9)

    def seg_energy(t0, t1):
        a, b = int(t0 / step), max(int(t0 / step) + 1, int(t1 / step))
        seg = rms[a:b]
        return float(np.sqrt((seg ** 2).mean())) if len(seg) else 0.0

    # 分段的格子：节奏可信按小节，不可信按 1 秒
    if reliable and len(downbeats) >= 3:
        bar_len = period * beats_per_bar
        edges = list(downbeats)
        if edges[0] > 0.6 * bar_len: edges = [0.0] + edges      # 弱起不到大半小节：并进第 1 小节
        else: edges[0] = 0.0
        edges.append(dur)
        unit = '小节'
    else:
        edges = list(np.arange(0, dur, 1.0)) + [dur]
        unit = '秒'
    cells = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i + 1] - edges[i] > 0.05]
    # 一格的能量 = 响度 和 鼓点密度（onset 强度平均）各占一半：只看响度的话贝斯一直在，前奏、主歌、副歌差不多响
    ce_r = np.array([seg_energy(a, b) for a, b in cells])
    ce_o = np.array([float(env[int(a * FPS):max(int(a * FPS) + 1, int(b * FPS))].mean()) for a, b in cells])
    ce = 0.5 * ce_r / (ce_r.max() + 1e-9) + 0.5 * ce_o / (ce_o.max() + 1e-9)
    ce = ce / (ce.max() + 1e-9)

    # 猛涨（drop）/ 猛跌（dip）
    drops, dips = [], []
    for i in range(1, len(cells)):
        dlt = float(ce[i] - ce[i - 1])
        if dlt > SURGE: drops.append(dict(t=round(cells[i][0], 3), delta=round(dlt, 2)))
        elif dlt < -SURGE: dips.append(dict(t=round(cells[i][0], 3), delta=round(dlt, 2)))

    groups = np.array_split(np.arange(D.shape[0]), 8)
    def feat(a, b):
        fa, fb = int(a * FPS), max(int(a * FPS) + 1, int(b * FPS))
        return [float(D[g, fa:fb].mean()) for g in groups] + [float(env[fa:fb].mean()) * 3]
    feats = np.array([feat(a, b) for a, b in cells])
    sections = _sections(cells, ce, feats, min_cells=2 if unit == '小节' else 3, short_ends=unit == '小节')

    # 突停 / 静音（0.01 秒一格的 dB）
    stops, silences = _stops(y, dur)

    climaxes = []
    top = [s for s in sections if s['label'] == 'drop']
    for s in top:
        a, b = int(s['start'] / step), max(int(s['start'] / step) + 1, int(s['end'] / step))
        pk = a + int(np.argmax(curve[a:b]))
        climaxes.append(dict(t=s['start'], peak=round(pk * step, 2), section=s['index']))

    hint = ('拍子稳定，可以放心卡拍' if reliable and steady else
            '拍子基本可信，但不是匀速（现场 / 自由速度），卡拍前看一眼 beats.png' if reliable else
            '节奏不明显：BPM 和小节不可信，别硬卡拍，按 section / drop / stop 切')
    out = dict(
        version=VERSION, source=os.path.basename(path), duration=round(dur, 3),
        bpm=round(final_bpm, 2), bpm_source='manual' if bpm else 'auto', beats_per_bar=beats_per_bar,
        grid='steady' if steady else 'tracked', grid_reliable=reliable, pulse_clarity=round(clarity, 3),
        grid_residual_ms=round(res * 1000, 1) if res is not None else None, hint=hint,
        beats=[round(float(t), 3) for t in beats], downbeats=[round(float(t), 3) for t in downbeats],
        pickup_beats=phase, sections=sections, section_unit=unit,
        drops=drops, dips=dips, climaxes=climaxes, stops=stops, silences=silences,
        accents=accents, kicks=kicks, snares=snares, hihats=hihats, hits=hits,
        energy=dict(step=step, values=[round(float(v), 3) for v in curve]),
        notes='bar:N=第 N 小节第 1 拍（从 1 数）；pickup_beats=第 1 小节前的弱起拍数（算 bar:0）；'
              'drop=能量猛涨（高潮砸下来），dip=能量猛跌；section 的 label 是按能量猜的，仅供参考。',
    )
    return out


def _sections(cells, ce, feats, min_cells=2, short_ends=True):
    """分段：看相邻几格的「配器」变了多少（8 个频带的响度 + 鼓点密度），变化最大的地方切开；
    这样鼓进来、镲进来、旋律进来都能切出来，哪怕总响度差不多。段名只按能量猜。"""
    n = len(cells)
    med = np.median(feats, 0)
    mad = np.median(np.abs(feats - med), 0) * 1.4826
    F = (feats - med) / (np.maximum(mad, 0.1 * feats.std(0)) + 1e-6)      # 稳健缩放：别让一小节前奏把别的差别压扁
    F = np.clip(F, -6, 6)
    w = min_cells
    nov = np.zeros(n + 1)
    for i in range(1, n):
        a, b = F[max(0, i - w):i].mean(0), F[i:min(n, i + w)].mean(0)
        nov[i] = float(np.sqrt(((a - b) ** 2).mean()))
    inner = nov[1:n]
    th = max(0.6, 1.5 * float(np.median(inner))) if n > 2 else 9          # 比「平常每格的变化」大一半以上才算换段
    cand = sorted((i for i in range(1, n) if nov[i] >= th and nov[i] >= nov[i - 1] and nov[i] >= nov[i + 1]), key=lambda i: -nov[i])
    bounds = [0, n]
    for i in cand:                       # 强的先切；离已有边界太近的不切（开头、结尾除外：一小节前奏 / 尾巴可以）
        ok = all(abs(i - x) >= min_cells or (short_ends and x in (0, n)) for x in bounds)
        ok = ok and all(abs(i - x) >= 1 for x in bounds)
        if ok: bounds.append(i)
    bounds = sorted(bounds)
    secs = []
    for k in range(len(bounds) - 1):
        a, b = bounds[k], bounds[k + 1]
        e = float(ce[a:b].mean())
        secs.append(dict(index=k + 1, start=round(cells[a][0], 3), end=round(cells[b - 1][1], 3),
                         level=_level(e), energy=round(e, 2), cells=[a + 1, b], change=round(float(nov[a]), 2)))
    if secs:
        topE = max(x['energy'] for x in secs)
        for x in secs:
            x['label'] = 'drop' if x['energy'] >= 0.95 * topE else 'main'
        if len(secs) > 1:
            if secs[0]['label'] != 'drop' and secs[0]['energy'] < 0.85 * topE: secs[0]['label'] = 'intro'
            if secs[-1]['label'] != 'drop' and secs[-1]['energy'] < secs[-2]['energy']: secs[-1]['label'] = 'outro'
        seen_drop = False
        for k, x in enumerate(secs):
            if x['label'] == 'drop': seen_drop = True; continue
            if x['label'] != 'main': continue
            if not seen_drop and k + 1 < len(secs) and secs[k + 1]['label'] == 'drop': x['label'] = 'build'
            elif seen_drop: x['label'] = 'break'
    return secs


def _stops(y, dur):
    m = int(SR * 0.01)
    nb = len(y) // m
    db = 20 * np.log10(np.sqrt((y[:nb * m].reshape(nb, m) ** 2).mean(1)) + 1e-7)
    ref = np.quantile(db, 0.95)
    rel = db - ref
    quiet = rel < -40
    silences = []
    i = 0
    while i < nb:
        if quiet[i]:
            j = i
            while j < nb and quiet[j]: j += 1
            if (j - i) * 0.01 >= 0.3:
                silences.append(dict(start=round(i * 0.01, 2), end=round(j * 0.01, 2)))
            i = j
        else:
            i += 1
    stops = []
    for k in range(30, nb - 25):
        before = rel[k - 30:k].mean()
        after = rel[k:k + 25]
        if before > -18 and after.max() < before - 18 and after.mean() < -30 and rel[k - 1] - rel[k] > 3:
            # 落点：从 k 往前找到掉下来那一格
            t = round(k * 0.01, 2)
            if not stops or t - stops[-1]['t'] > 0.5:
                end = k
                while end < nb and rel[end] < before - 12: end += 1
                stops.append(dict(t=t, quiet_until=round(end * 0.01, 2)))
    return stops, silences


# ── 时间表达式 ─────────────────────────────────────────────────────────────────
_KINDS = {'drop': 'drops', 'dip': 'dips', 'stop': 'stops', 'silence': 'silences', 'section': 'sections',
          'kick': 'kicks', 'snare': 'snares', 'hihat': 'hihats', 'accent': 'accents', 'climax': 'climaxes', 'hit': 'hits'}
_NAMES = {'drop': '高潮砸下来（能量猛涨）的点', 'dip': '能量猛跌的点', 'stop': '突停', 'silence': '静音段', 'section': '段落',
          'kick': '底鼓', 'snare': '军鼓', 'hihat': '镲', 'accent': '重拍', 'climax': '高潮', 'hit': '起音点'}
EXPR_HELP = ('写法：3.5（秒）、bar:3（第 3 小节头）、bar:3.2（第 3 小节第 2 拍）、beat:17、phrase:2（第 2 个四小节乐句）、'
             'drop:1、dip:1、stop:1、silence:1、section:2、climax、kick:5、snare:3、accent:4、end；'
             '后面可以加偏移：bar:3-0.1（提前 0.1 秒）、bar:3+1b（晚一拍）、drop:1-0.5b')


def load(beats):
    if isinstance(beats, dict): return beats
    try:
        with open(beats, encoding='utf-8') as f: return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f'找不到 {beats}：先跑 mm beats 音乐.mp3 生成它')
    except json.JSONDecodeError:
        raise SystemExit(f'{beats} 不是 beats.json（格式坏了），重新跑一次 mm beats')


def _beat_len(b):
    return 60.0 / b['bpm']


def _bar_time(b, n, k=1):
    bpb = b.get('beats_per_bar', 4)
    if not 1 <= k <= bpb:
        raise SystemExit(f'每小节只有 {bpb} 拍，"第 {k} 拍" 不存在')
    db, bt = b['downbeats'], b['beats']
    if n == 0:                                   # 弱起：第 1 小节前那几拍
        p = b.get('pickup_beats', 0)
        idx = (k - 1) - (bpb - p)
        if idx < 0:
            return db[0] - (bpb - (k - 1)) * _beat_len(b)
        return bt[idx]
    if n < 0:
        raise SystemExit('小节从 1 数（bar:0 是第 1 小节前的弱起拍）')
    i0 = b.get('pickup_beats', 0) + (n - 1) * bpb + (k - 1)
    if i0 < len(bt):
        return bt[i0]
    return bt[-1] + (i0 - len(bt) + 1) * _beat_len(b)        # 超出检测到的拍子：按 BPM 往后推


def resolve(expr, beats):
    """把时间写法换成秒。beats 是 beats.json 的路径或已读进来的 dict。写错了抛 SystemExit（中文说明）"""
    b = load(beats)
    s = str(expr).strip().replace('：', ':')
    if not s:
        raise SystemExit('时间写法是空的。' + EXPR_HELP)
    try:
        return float(s.rstrip('s'))
    except ValueError:
        pass
    m = re.fullmatch(r'(.+?)([+-]\d+(?:\.\d+)?)(s|b|beat|拍|bar|小节)?', s)
    off = 0.0
    if m and (':' in m[1] or m[1] in ('end', 'climax', 'start')):
        s = m[1]
        unit = m[3] or 's'
        v = float(m[2])
        off = v * _beat_len(b) if unit in ('b', 'beat', '拍') else v * _beat_len(b) * b.get('beats_per_bar', 4) if unit in ('bar', '小节') else v
    t = _resolve_base(s, b)
    t += off
    dur = b.get('duration', 1e9)
    if t < -1e-6 or t > dur + 1e-3:
        raise SystemExit(f'"{expr}" 算出来是 {t:.2f} 秒，超出了音乐范围（0–{dur:.2f} 秒）')
    return round(max(0.0, t), 3)


def _resolve_base(s, b):
    if s == 'start': return 0.0
    if s == 'end': return float(b['duration'])
    if s == 'climax': s = 'climax:1'
    if ':' not in s:
        raise SystemExit(f'看不懂 "{s}"。' + EXPR_HELP)
    kind, arg = s.split(':', 1)
    kind = kind.strip().lower()
    if kind == 'bar':
        parts = arg.split('.')
        try:
            n = int(parts[0]); k = int(parts[1]) if len(parts) > 1 and parts[1] else 1
        except ValueError:
            raise SystemExit(f'"{s}" 写错了：bar 后面写整数，如 bar:3 或 bar:3.2（第 3 小节第 2 拍）')
        if len(parts) > 2: raise SystemExit(f'"{s}" 写错了：只能写到第几拍（bar:3.2），更细用偏移 bar:3.2+0.5b')
        return _bar_time(b, n, k)
    if kind == 'phrase':
        try: n = int(arg)
        except ValueError: raise SystemExit(f'"{s}" 写错了：phrase 后面写整数')
        if n < 1: raise SystemExit('phrase 从 1 数')
        return _bar_time(b, 1 + 4 * (n - 1))
    try:
        n = int(arg)
    except ValueError:
        raise SystemExit(f'"{s}" 写错了：冒号后面要写第几个（整数），如 {kind}:1')
    if kind == 'beat':
        bt = b['beats']
        if n < 1: raise SystemExit('beat 从 1 数')
        return bt[n - 1] if n <= len(bt) else bt[-1] + (n - len(bt)) * _beat_len(b)
    if kind not in _KINDS:
        raise SystemExit(f'不认识 "{kind}"。' + EXPR_HELP)
    lst = b.get(_KINDS[kind]) or []
    if n < 1:
        raise SystemExit(f'{kind} 从 1 数')
    if n > len(lst):
        have = f'这首只找到 {len(lst)} 个' if lst else '这首一个都没找到'
        tip = '（看 beats.png 上的标记，或换成 bar:N）'
        raise SystemExit(f'没有第 {n} 个{_NAMES[kind]}（{kind}:{n}），{have}{tip}')
    it = lst[n - 1]
    if isinstance(it, dict):
        return float(it.get('t', it.get('start')))
    return float(it)


def locate(t, beats):
    """反查：t 秒落在第几小节第几拍、离最近的拍差多少。返回 dict"""
    b = load(beats)
    bt = np.array(b['beats']); bl = _beat_len(b); bpb = b.get('beats_per_bar', 4); p = b.get('pickup_beats', 0)
    if t < bt[0] - bl / 2:
        i, frac = (t - bt[0]) / bl, None
        i = int(np.floor(i)); fi = (t - (bt[0] + i * bl)) / bl
    elif t > bt[-1] + bl / 2:
        i = len(bt) - 1 + int(np.floor((t - bt[-1]) / bl)); fi = (t - (bt[-1] + (i - len(bt) + 1) * bl)) / bl
    else:
        i = int(np.searchsorted(bt, t, 'right') - 1); i = max(0, min(i, len(bt) - 2))
        fi = (t - bt[i]) / (bt[i + 1] - bt[i])
    if fi > 0.5:                                  # 用最近的拍子表示（偏移在 ±半拍内）
        i += 1; fi -= 1
    bar = (i - p) // bpb + 1
    k = (i - p) % bpb + 1
    off_b = float(fi)
    grid_t = t - off_b * bl
    # 八分 / 十六分位置
    q = round(off_b * 4) / 4
    sub = {0: '正拍', 0.5: '半拍（八分反拍）', -0.5: '半拍（八分反拍）', 0.25: '十六分后', -0.25: '十六分前'}.get(q, '')
    secs = b.get('sections', [])
    sec = next((s for s in secs if s['start'] - 1e-6 <= t < s['end'] - 1e-6), secs[-1] if secs and t >= secs[-1]['start'] else None)
    hits = b.get('hits') or []
    near = min(hits, key=lambda h: abs(h['t'] - t)) if hits else None
    if near and abs(near['t'] - t) > 0.12: near = None
    return dict(t=round(t, 3), bar=int(bar), beat=int(k), beat_index=int(i) + 1, offset_beats=round(off_b, 2),
                nearest_hit=dict(t=near['t'], drum=near['drum'], strength=near['strength'], offset_ms=round((t - near['t']) * 1000)) if near else None,
                offset_ms=round(off_b * bl * 1000), label=f'bar:{bar}.{k}' + (f'{off_b * bl:+.2f}' if abs(off_b * bl) >= 0.005 else ''),
                grid_t=round(grid_t, 3), subdivision=sub, section=sec['index'] if sec else None,
                section_label=sec['label'] if sec else None)


# ── 可视化 ────────────────────────────────────────────────────────────────────
_SEC_COLORS = {'intro': (120, 170, 230), 'build': (240, 190, 80), 'drop': (235, 90, 90), 'break': (140, 200, 150),
               'main': (170, 150, 220), 'outro': (150, 150, 150)}


def draw(b, path, out_png, y=None):
    from PIL import Image, ImageDraw, ImageFont
    try:
        from mmlib.fonts import cjk_font
        f13, f16 = cjk_font(13), cjk_font(18)
    except Exception:
        f13 = f16 = None
    f13 = f13 or ImageFont.load_default(); f16 = f16 or ImageFont.load_default()
    dur = b['duration']
    W = int(min(4000, max(1400, dur * 60)))
    L, R = 50, 20
    pw = W - L - R
    Y0, SH = 44, 30                 # 段落色带
    WY, WH = Y0 + SH + 22, 230      # 波形
    DY = WY + WH + 10               # 鼓点三行
    EY = DY + 3 * 16 + 6            # 事件行
    H = EY + 50
    im = Image.new('RGB', (W, H), (250, 250, 248))
    d = ImageDraw.Draw(im)
    X = lambda t: L + t / dur * pw
    d.text((L, 10), f"{b['source']}   {b['bpm']:.1f} BPM · {len(b['downbeats'])} 小节 · {b['duration']:.1f} 秒 · {b['hint']}", fill=(30, 30, 30), font=f16)
    for s in b['sections']:
        c = _SEC_COLORS.get(s['label'], (180, 180, 180))
        d.rectangle([X(s['start']), Y0, X(s['end']) - 1, Y0 + SH], fill=c)
        d.text((X(s['start']) + 4, Y0 + 7), f"{s['index']} {s['label']}", fill=(20, 20, 20), font=f13)
    # 波形
    if y is None:
        y = load_audio(path)
    n = len(y)
    cols = np.array_split(y[: n - n % pw] if n >= pw else y, pw)
    mid = WY + WH / 2
    d.rectangle([L, WY, L + pw, WY + WH], fill=(240, 240, 236))
    for i, c in enumerate(cols):
        if not len(c): continue
        a, z = float(c.max()), float(c.min())
        d.line([(L + i, mid - a * WH / 2.1), (L + i, mid - z * WH / 2.1)], fill=(150, 150, 150))
    # 能量
    ev = b['energy']['values']; st = b['energy']['step']
    pts = [(X(i * st), WY + WH - v * (WH - 6) - 3) for i, v in enumerate(ev)]
    if len(pts) > 1: d.line(pts, fill=(60, 110, 200), width=2)
    # 拍线 / 小节线
    bl = 60 / b['bpm']
    if bl / dur * pw >= 4:
        for t in b['beats']:
            d.line([(X(t), WY), (X(t), WY + WH)], fill=(205, 205, 200))
    every = max(1, int(np.ceil(28 / (bl * b['beats_per_bar'] / dur * pw))))
    for i, t in enumerate(b['downbeats']):
        d.line([(X(t), WY - 4), (X(t), WY + WH)], fill=(70, 70, 70), width=1)
        if i % every == 0:
            d.text((X(t) + 2, WY - 18), str(i + 1), fill=(40, 40, 40), font=f13)
    # 鼓
    for row, (key, col, name) in enumerate((('kicks', (200, 60, 60), '底鼓'), ('snares', (60, 140, 70), '军鼓'), ('hihats', (90, 90, 200), '镲'))):
        yy = DY + row * 16 + 8
        d.text((4, yy - 8), name, fill=col, font=f13)
        for t in b[key]:
            d.ellipse([X(t) - 2.5, yy - 2.5, X(t) + 2.5, yy + 2.5], fill=col)
    # 事件
    yy = EY + 12
    d.text((4, yy - 8), '事件', fill=(30, 30, 30), font=f13)
    for i, e in enumerate(b['drops']):
        x = X(e['t']); d.polygon([(x - 6, yy + 6), (x + 6, yy + 6), (x, yy - 6)], fill=(235, 90, 90)); d.text((x + 7, yy - 8), f'drop:{i + 1}', fill=(180, 40, 40), font=f13)
    for i, e in enumerate(b['stops']):
        x = X(e['t']); d.rectangle([x - 5, yy + 14, x + 5, yy + 24], fill=(20, 20, 20)); d.text((x + 7, yy + 10), f'stop:{i + 1}', fill=(20, 20, 20), font=f13)
        d.line([(x, WY), (x, WY + WH)], fill=(20, 20, 20), width=2)
    for c in b['climaxes']:
        x = X(c['peak']); d.text((x - 5, yy + 8), '★', fill=(220, 150, 0), font=f16)
    for s in b['silences']:
        d.rectangle([X(s['start']), WY + WH - 6, X(s['end']), WY + WH], fill=(30, 30, 30))
    # 时间轴
    tick = 1 if dur <= 40 else 5 if dur <= 200 else 15
    for t in np.arange(0, dur + 1e-6, tick):
        d.line([(X(t), H - 14), (X(t), H - 8)], fill=(80, 80, 80))
        if (t / tick) % (2 if dur <= 40 else 1) == 0:
            d.text((X(t) + 2, H - 22), f'{t:g}s', fill=(80, 80, 80), font=f13)
    im.save(out_png)
    return out_png


# ── 命令行 ────────────────────────────────────────────────────────────────────
def add_parser(sp):
    p = sp.add_parser('beats', help='分析音乐：拍子、小节、段落、鼓点、高潮、突停 → beats.json + beats.png；--cuts 把 bar:3 这类写法换成秒')
    p.add_argument('audio', help='音乐（mp3 / wav / m4a，视频也行，取音轨）；给已经生成的 beats.json 就不再分析')
    p.add_argument('--out', help='beats.json 存哪；默认和音乐同文件夹的 beats.json。图存成同名 .png')
    p.add_argument('--bpm', type=float, help='手动指定速度（自动测的不对时，比如测成一半 / 两倍）')
    p.add_argument('--beats-per-bar', type=int, default=4, help='每小节几拍，默认 4')
    p.add_argument('--first-bar', type=float, help='第 1 小节从第几秒开始（自动判断的小节头错了时填，看 beats.png）')
    p.add_argument('--cuts', help='逗号分隔的时间写法，打印对应秒数，如 "bar:1,bar:3,drop:1,bar:9"')
    p.add_argument('--where', help='逗号分隔的秒数，反查落在第几小节第几拍，如 "0,3.16,5.2"')
    p.add_argument('--no-png', action='store_true', help='不出图')
    return p


def _print_summary(b, out, png):
    print(f"{b['source']}：{b['bpm']:.1f} BPM（{'手动' if b['bpm_source'] == 'manual' else '自动'}），{len(b['beats'])} 拍 / {len(b['downbeats'])} 小节，{b['duration']:.1f} 秒")
    print(f"  {b['hint']}（节奏清晰度 {b['pulse_clarity']}，网格={'匀速直线' if b['grid'] == 'steady' else '逐拍跟踪'}）")
    print(f"  第 1 小节从 {b['downbeats'][0]:.2f} 秒开始" + (f"，前面有 {b['pickup_beats']} 拍弱起（bar:0）" if b['pickup_beats'] else ''))
    print('  段落：' + '  '.join(f"{s['index']}.{s['label']} {s['start']:.2f}–{s['end']:.2f}" for s in b['sections']))
    if b['drops']: print('  drop（能量猛涨）：' + '  '.join(f"{i + 1}:{e['t']:.2f}" for i, e in enumerate(b['drops'])))
    if b['stops']: print('  stop（突停）：' + '  '.join(f"{i + 1}:{e['t']:.2f}" for i, e in enumerate(b['stops'])))
    if b['climaxes']: print('  高潮：' + '  '.join(f"{c['t']:.2f}（最响 {c['peak']:.2f}）" for c in b['climaxes']))
    print(f"  底鼓 {len(b['kicks'])} · 军鼓 {len(b['snares'])} · 镲 {len(b['hihats'])} · 重拍 {len(b['accents'])}")
    if out: print(f'  → {out}')
    if png: print(f'  → {png}（波形 + 拍线 + 小节号 + 段落色带，先看一眼小节头对不对）')


def run(args):
    src = args.audio
    if src.lower().endswith('.json'):
        b = load(src); out = png = None
    else:
        b = analyze(src, bpm=args.bpm, beats_per_bar=args.beats_per_bar, first_bar=args.first_bar)
        out = args.out or os.path.join(os.path.dirname(os.path.abspath(src)), 'beats.json')
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(b, f, ensure_ascii=False, indent=1)
        png = None
        if not args.no_png:
            png = os.path.splitext(out)[0] + '.png'
            draw(b, src, png)
        _print_summary(b, out, png)
    if args.cuts:
        print('时间写法 → 秒')
        for e in [x for x in args.cuts.split(',') if x.strip()]:
            t = resolve(e, b)
            loc = locate(t, b)
            print(f'  {e.strip():<14} {t:8.3f}   ({loc["label"]}，第 {loc["section"]} 段 {loc["section_label"]})')
    if args.where:
        print('秒 → 拍子位置（偏移 = 比最近的拍早(-) / 晚(+) 多少）')
        for e in [x for x in args.where.split(',') if x.strip()]:
            try: t = float(e)
            except ValueError: raise SystemExit(f'--where 里 "{e}" 不是秒数')
            loc = locate(t, b)
            print(f'  {t:7.2f}s  第 {loc["bar"]:>2} 小节第 {loc["beat"]} 拍  偏 {loc["offset_ms"]:+5d} ms（{loc["offset_beats"]:+.2f} 拍）'
                  f'{"  " + loc["subdivision"] if loc["subdivision"] else ""}  第 {loc["section"]} 段 {loc["section_label"]}'
                  + (f'  最近的起音 {loc["nearest_hit"]["t"]:.2f}（{loc["nearest_hit"]["drum"]}，强度 {loc["nearest_hit"]["strength"]}，差 {loc["nearest_hit"]["offset_ms"]:+d} ms）' if loc['nearest_hit'] else '  附近 120 ms 内没有起音'))
    return b


if __name__ == '__main__':          # 单独测试：python mmlib/beats.py 音乐.mp3 …
    import argparse
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(prog='beats')
    add_parser(ap.add_subparsers(dest='cmd', required=True))
    argv = sys.argv[1:]
    run(ap.parse_args(argv if argv[:1] == ['beats'] else ['beats'] + argv))
