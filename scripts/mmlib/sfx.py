"""音效：全部用代码合成（没有版权问题）。播放器给出动作事件 → 在对应时间放对应的声音 → 混成一条 WAV。"""
import wave
import numpy as np

SR = 44100
_rng = np.random.default_rng(11)


def _env(n, a=0.004, d=0.08):
    t = np.arange(n) / SR
    return np.minimum(t / max(a, 1e-4), 1) * np.exp(-t / max(d, 1e-4))


def _bandnoise(n, lo, hi):
    x = _rng.normal(0, 1, n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(X, n)
    return y / (np.abs(y).max() + 1e-9)


def pop(pitch=1.0):
    n = int(0.12 * SR); t = np.arange(n) / SR
    f = 420 * pitch * (1 + 0.8 * np.exp(-t * 50))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.005, 0.04)
    y += 0.05 * _bandnoise(n, 1500, 4000) * _env(n, 0.001, 0.006)
    return y * 0.35


def tick():
    n = int(0.03 * SR)
    return _bandnoise(n, 1800, 4500) * _env(n, 0.001, 0.005) * 0.12


def scratch(dur):
    n = int(dur * SR); t = np.arange(n) / SR
    y = _bandnoise(n, 900, 3800)
    wob = 0.55 + 0.45 * np.abs(np.sin(2 * np.pi * 7.3 * t + 2 * np.sin(2 * np.pi * 1.7 * t)))
    fade = np.minimum(1, np.minimum(t / 0.08, (dur - t) / 0.12).clip(0))
    return y * wob * fade * 0.05


def thud():
    n = int(0.35 * SR); t = np.arange(n) / SR
    f = 60 + 70 * np.exp(-t * 25)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.004, 0.1)
    y += 0.12 * _bandnoise(n, 150, 900) * _env(n, 0.002, 0.03)
    return y * 0.55


def slam():
    y = thud()
    n = int(0.05 * SR)
    y[:n] += _bandnoise(n, 800, 3500) * _env(n, 0.001, 0.012) * 0.15
    return y * 0.9


def whoosh(dur=0.35):
    n = int(dur * SR); t = np.arange(n) / SR
    x = _rng.normal(0, 1, n)
    # 扫频带通：简单用分段 FFT 滤波近似
    y = np.zeros(n); seg = 2048
    for i in range(0, n, seg // 2):
        j = min(i + seg, n); k = j - i
        if k < 64:
            break
        fc = 300 + 2500 * (i / n)
        X = np.fft.rfft(x[i:j] * np.hanning(k)); f = np.fft.rfftfreq(k, 1 / SR)
        X *= np.exp(-((f - fc) / (fc * 0.6)) ** 2)
        y[i:j] += np.fft.irfft(X, k)
    y /= np.abs(y).max() + 1e-9
    shape = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 1.5
    return y * shape * 0.16


def peel(dur=0.8):
    n = int(dur * SR); t = np.arange(n) / SR
    y = np.zeros(n)
    k = _rng.random(n) < (900 * (0.4 + 0.6 * np.sin(np.pi * t / dur)) / SR)
    imp = np.where(k, _rng.normal(0, 1, n), 0)
    y = np.convolve(imp, _bandnoise(220, 1500, 7000) * np.exp(-np.arange(220) / 30), 'same')
    y /= np.abs(y).max() + 1e-9
    return y * 0.14


def boing():
    n = int(0.3 * SR); t = np.arange(n) / SR
    f = 260 + 120 * np.sin(2 * np.pi * 9 * t) * np.exp(-t * 8)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.006, 0.1) * 0.3


def zap():
    """霓虹通电：短促的电流嗡一声"""
    n = int(0.16 * SR); t = np.arange(n) / SR
    hum = np.sign(np.sin(2 * np.pi * 100 * t)) * 0.5 + np.sin(2 * np.pi * 200 * t) * 0.5
    y = (0.6 * hum + 0.4 * _bandnoise(n, 300, 2500)) * _env(n, 0.002, 0.05)
    return _lowpass(y, 2500) * 0.18


def knock(pitch=1.0):
    """立体字落位、盖章：闷闷的一下"""
    n = int(0.18 * SR); t = np.arange(n) / SR
    f = 140 * pitch * (1 + 0.6 * np.exp(-t * 40))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.045)
    y += 0.2 * _bandnoise(n, 300, 1600) * _env(n, 0.001, 0.015)
    return y * 0.45


def shimmer(dur=0.6):
    """闪光、像素拼出：一串很轻的高音叮"""
    n = int(dur * SR); y = np.zeros(n)
    k = max(4, int(dur * 14))
    for i in range(k):
        st = int(i / k * (n - 2000)); m = int(0.08 * SR); t = np.arange(m) / SR
        f = 1700 + 900 * _rng.random()
        blip = np.sin(2 * np.pi * f * t) * _env(m, 0.002, 0.03)
        y[st:st + m] += blip[:max(0, min(m, n - st))] * (0.6 + 0.4 * _rng.random())
    return y * 0.07


def step():
    """软底脚步：很短的闷响"""
    n = int(0.07 * SR); t = np.arange(n) / SR
    y = np.sin(2 * np.pi * (90 + 60 * np.exp(-t * 60)) * t) * _env(n, 0.002, 0.02)
    y += 0.25 * _bandnoise(n, 300, 1400) * _env(n, 0.001, 0.008)
    return y * 0.35


def roll(dur=2.0):
    """卷帘门：薄金属滚轴连续摩擦 + 一节节帘片经过滚轴的小颤动"""
    n = int(dur * SR); t = np.arange(n) / SR
    y = _bandnoise(n, 180, 1600) * (0.6 + 0.4 * np.sin(2 * np.pi * 3.1 * t) ** 2)
    clicks = np.zeros(n)
    for k in np.arange(0.05, dur, 0.1):
        i = int(k * SR); m = min(int(0.012 * SR), n - i)
        if m > 0:
            clicks[i:i + m] += _bandnoise(m, 1200, 4000) * _env(m, 0.0005, 0.003)
    fade = np.minimum(1, np.minimum(t / 0.15, (dur - t) / 0.1).clip(0))
    return (y * 0.06 + clicks * 0.12) * fade


def _lowpass(y, fc):
    X = np.fft.rfft(y); f = np.fft.rfftfreq(len(y), 1 / SR)
    X *= 1 / (1 + (f / fc) ** 4)
    return np.fft.irfft(X, len(y))


# minimal（默认）：只留看得见的接触声（弹出、落地、砸、敲、脚步、门）；书写声减半；嗖、闪光、打字只留开头几下
MINIMAL_DROP = {'whoosh', 'shimmer'}


def render_events(events, duration, volume=1.0, level='minimal'):
    out = np.zeros(int((duration + 1) * SR))
    if level == 'none':
        return out[:int(duration * SR)]
    ticks = 0
    for e in events:
        k = e['kind']; g = e.get('gain', 1.0)
        if level == 'minimal':
            if k in MINIMAL_DROP:
                continue
            if k == 'tick':
                ticks += 1
                if ticks > 3:
                    continue
            if k == 'scratch':
                g *= 0.5
        if k == 'pop':
            y = pop(e.get('pitch', 1.0))
        elif k == 'tick':
            y = tick()
        elif k == 'scratch':
            y = scratch(max(0.1, e.get('dur', 1.0)))
        elif k == 'thud':
            y = thud()
        elif k == 'slam':
            y = slam()
        elif k == 'whoosh':
            y = whoosh(e.get('dur', 0.35))
        elif k == 'peel':
            y = peel(e.get('dur', 0.8))
        elif k == 'boing':
            y = boing()
        elif k == 'zap':
            y = zap()
        elif k == 'knock':
            y = knock(e.get('pitch', 1.0))
        elif k == 'shimmer':
            y = shimmer(e.get('dur', 0.6))
        elif k == 'step':
            y = step()
        elif k == 'roll':
            y = roll(e.get('dur', 2.0))
        else:
            continue
        # 同类声音挤在一起时（逐字弹出）每个调小，不然叠起来很冲
        near = sum(1 for o in events if o is not e and o['kind'] == k and abs(o['t'] - e['t']) < 0.2)
        g /= (1 + 0.35 * near)
        i = int(max(0, e['t']) * SR)
        j = min(len(out), i + len(y))
        out[i:j] += y[:j - i] * g
    out = out[:int(duration * SR)]
    out = _lowpass(out, 7000)                 # 去掉刺耳的高频
    peak = np.abs(out).max()
    if peak > 0.5:                            # 峰值压到 -6dB，给用户自己的音乐留空间
        out *= 0.5 / peak
    return out * volume


def write_wav(path, y):
    data = (np.clip(y, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(data.tobytes())
