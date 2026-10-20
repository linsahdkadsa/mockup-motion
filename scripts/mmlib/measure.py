"""mm measure：半自动量参考片，代替手工逐帧量。

做五件事，结果都放进「参考片名_measure/」：
  1. 切点：自动找硬切，出每个镜头的起止秒和时长（cuts.csv）
  2. 联系表：每个镜头一张（默认每 0.08 秒一帧，带时间戳），外加全片总表
  3. 运动曲线（可选 --track / --box）：跟踪画面里一块区域，出位置、大小随时间的曲线（csv + png），
     拟合成常见缓动（linear / ease-in / ease-out / ease-in-out / spring 带过冲），误差 > 0.08 标「不可信」
  4. 镜头运动：每个镜头整体平移 / 缩放（推拉摇移），每秒多少像素
  5. measure.md：照拆解.md 的表排好，人只要填「画面」「图形」两列

坐标一律按参考片原图像素写（可以先 mm frames 抽一帧、mm grid 叠网格读坐标）。
"""
import csv, json, os, re, subprocess, sys

import numpy as np

STEP = 0.08          # 联系表默认每 0.08 秒一帧
UNTRUSTED = 0.08     # 拟合误差超过这个数就不可信（照 mblode/ui-animation 的经验值）
ANALYZE_W = 480      # 找切点、算镜头运动用的分析宽度
THUMB_W = 360        # 联系表里每格的宽度
TRACK_W = 960        # 跟踪时把画面缩到多宽（坐标会换算回原图像素）
MAX_TRACK_FRAMES = 900


# ---------------------------------------------------------------- 命令行参数

def add_parser(sp):
    """给 mm.py 的 argparse 子命令注册 measure：sp 是 add_subparsers() 的返回值"""
    m = sp.add_parser('measure', help='半自动量参考片：切点、每镜联系表、镜头运动、跟踪曲线和缓动拟合',
                      description='半自动量参考片：切点、每镜联系表、镜头运动，可选跟踪一块区域并拟合缓动。结果放在「参考片名_measure/」')
    m.add_argument('video', help='参考片（mp4 / mov 等）')
    m.add_argument('--out', help='输出文件夹，默认是参考片旁边的「参考片名_measure/」')
    m.add_argument('--step', type=float, default=STEP, help='联系表每隔几秒取一帧，默认 0.08')
    m.add_argument('--cols', type=int, default=6, help='联系表每行几格，默认 6')
    m.add_argument('--sens', type=float, default=1.0, help='切点灵敏度：漏了切点就调大（1.5），多出假切点就调小（0.7）')
    m.add_argument('--min-shot', type=float, default=0.3, help='镜头最短几秒，比这更近的两个切点只留一个，默认 0.3')
    m.add_argument('--cuts', help='自动找得不对时，手动给切点（秒，逗号分隔，如 3.16,5.2），会跳过自动找')
    m.add_argument('--track', help='跟踪一个点周围的区域：X,Y,T（原图像素坐标 + 在第几秒时它在那里）')
    m.add_argument('--box', help='跟踪一个框：x0,y0,x1,y1（原图像素），配合 --at 说是第几秒的框')
    m.add_argument('--at', type=float, help='--box 是第几秒的画面（全片时间，秒）；不写就用 --shot 那个镜头的第一帧')
    m.add_argument('--shot', type=int, help='在第几个镜头里跟踪（从 1 数）；不写就按 --track / --at 的时间自动判断')
    m.add_argument('--size', type=int, help='--track 时跟踪框的边长（原图像素），默认画面高度的 15%%')
    m.add_argument('--span', help='只拿镜内这一段来拟合缓动：起,止（镜内秒，如 0.9,1.2）。一个镜头里有好几段动作时必须用它，不然几段混在一起拟合不准')
    m.add_argument('--no-sheets', action='store_true', help='不出联系表（只要数字时更快）')
    m.set_defaults(func=run)
    return m


def _fail(msg):
    raise SystemExit('【mm measure】' + msg)


def _nums(s, n, name):
    try:
        v = [float(x) for x in s.replace('，', ',').split(',')]
    except ValueError:
        _fail(f'{name} 要写成 {n} 个数字，用逗号隔开，比如 --{name} ' + ','.join(['100'] * n))
    if len(v) != n:
        _fail(f'{name} 要正好 {n} 个数字（现在是 {len(v)} 个），用逗号隔开')
    return v


# ---------------------------------------------------------------- 读视频

def _ffmpeg():
    try:
        from mmlib.browser import ffmpeg_exe
        return ffmpeg_exe()
    except Exception:
        _fail('找不到 ffmpeg。请用 uv run mm.py measure … 来运行（会自动装好 imageio-ffmpeg）')


def _cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        _fail('缺少 OpenCV。请用 uv run mm.py measure … 运行（会自动装 opencv-python-headless），'
              '或者手动装：uv pip install opencv-python-headless')


def probe(video):
    """读宽高、帧率、时长（从 ffmpeg -i 的输出里抠）"""
    r = subprocess.run([_ffmpeg(), '-hide_banner', '-i', video], capture_output=True, text=True, errors='replace')
    txt = r.stderr
    m = re.search(r'Stream #.*?Video:.*?(\d{2,5})x(\d{2,5})', txt)
    if not m:
        _fail(f'读不出这个视频的画面：{video}\n（文件坏了，或者不是视频？）')
    w, h = int(m.group(1)), int(m.group(2))
    line = txt[m.start():txt.find('\n', m.start())]
    fm = re.search(r'([\d.]+) fps', line) or re.search(r'([\d.]+) tbr', line)
    fps = float(fm.group(1)) if fm else 25.0
    rot = re.search(r'rotate\s*:\s*(-?\d+)', txt) or re.search(r'rotation of (-?[\d.]+)', txt)
    if rot and abs(round(float(rot.group(1)))) % 180 == 90:   # 手机竖拍：ffmpeg 解出来是转正后的宽高
        w, h = h, w
    dm = re.search(r'Duration: (\d+):(\d+):([\d.]+)', txt)
    dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3)) if dm else None
    return {'w': w, 'h': h, 'fps': fps, 'duration': dur}


def iter_frames(video, width, height, gray=False, select=None):
    """一帧一帧读出来（numpy 数组）。select=(a,b) 只要第 a 到 b 帧（含）"""
    vf = f'scale={width}:{height}:flags=area'
    if select:
        vf = f"select='between(n\\,{select[0]}\\,{select[1]})'," + vf
    pix = 'gray' if gray else 'rgb24'
    cmd = [_ffmpeg(), '-loglevel', 'error', '-i', video, '-vf', vf, '-vsync', '0', '-f', 'rawvideo', '-pix_fmt', pix, '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = width * height * (1 if gray else 3)
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            a = np.frombuffer(buf, np.uint8)
            yield a.reshape(height, width) if gray else a.reshape(height, width, 3)
    finally:
        p.stdout.close(); p.wait()


def _even(x):
    return max(2, int(round(x / 2)) * 2)


# ---------------------------------------------------------------- 第一遍：切点特征 + 镜头运动 + 缩略图

def analyze(video, info, want_thumbs=True):
    cv2 = _cv2()
    from PIL import Image
    import io
    aw = min(ANALYZE_W, info['w']); ah = _even(aw * info['h'] / info['w']); aw = _even(aw)
    sw = 96; sh = max(2, int(round(sw * ah / aw)))
    tw = min(THUMB_W, info['w']); th = _even(tw * info['h'] / info['w'])
    pix, hist, motion, thumbs = [], [], [], []
    prev_small = prev_hist = prev_gray = None
    for i, f in enumerate(iter_frames(video, aw, ah)):
        gray = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
        small = cv2.resize(gray, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
        hsv = cv2.cvtColor(cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2HSV)
        hh = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
        hh /= hh.sum() + 1e-9
        if prev_small is None:
            pix.append(0.0); hist.append(0.0); motion.append((0.0, 0.0, 1.0, False))
        else:
            pix.append(float(np.abs(small - prev_small).mean() / 255))
            hist.append(float(0.5 * np.abs(hh - prev_hist).sum()))
            motion.append(_global_motion(cv2, prev_gray, gray))
        prev_small, prev_hist, prev_gray = small, hh, gray
        if want_thumbs:
            b = io.BytesIO(); Image.fromarray(f).resize((tw, th), Image.LANCZOS).save(b, 'JPEG', quality=86)
            thumbs.append(b.getvalue())
    if not pix:
        _fail('一帧都没读出来：视频是不是坏了？')
    return {'pix': np.array(pix), 'hist': np.array(hist), 'motion': motion, 'thumbs': thumbs,
            'scale': info['w'] / aw, 'n': len(pix), 'thumb_size': (tw, th)}


def _global_motion(cv2, a, b):
    """相邻两帧整体怎么动：返回 (dx, dy, 缩放, 是否可信)，dx/dy 按分析分辨率的像素"""
    pts = cv2.goodFeaturesToTrack(a, maxCorners=300, qualityLevel=0.01, minDistance=8, blockSize=7)
    if pts is not None and len(pts) >= 12:
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None, winSize=(21, 21), maxLevel=3)
        good = st.ravel() == 1
        if good.sum() >= 10:
            M, inl = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=1.5)
            if M is not None and inl is not None and inl.sum() >= max(8, 0.3 * good.sum()):
                s = float(np.hypot(M[0, 0], M[1, 0]))
                h, w = a.shape
                c = np.array([w / 2, h / 2, 1.0])          # 以画面中心为基准量平移（缩放不带出假平移）
                d = M @ c - c[:2]
                return (float(d[0]), float(d[1]), s, True)
    # 特征点太少（纯色、模糊）：退回到相位相关，只量平移
    try:
        (dx, dy), resp = cv2.phaseCorrelate(np.float32(a), np.float32(b))
        return (float(dx), float(dy), 1.0, resp > 0.2)
    except cv2.error:
        return (0.0, 0.0, 1.0, False)


# ---------------------------------------------------------------- 切点

def find_cuts(a, fps, sens=1.0, min_shot=0.3):
    """返回新镜头第一帧的帧号列表（不含 0）。
    分数 = 像素差 + 颜色直方图差；阈值随附近的平均活跃度自适应（运动多的镜头阈值跟着抬高）。"""
    s = (a['pix'] * 2.0 + a['hist']) / 2
    n = len(s)
    win = max(4, int(round(fps * 0.5)))
    cands = []
    for i in range(1, n):
        lo, hi = max(1, i - win), min(n, i + win + 1)
        around = np.concatenate([s[lo:max(lo, i - 1)], s[min(hi, i + 2):hi]])
        base = float(np.median(around)) if len(around) else 0.0
        spread = float(np.percentile(around, 90)) if len(around) else 0.0
        thr = max(0.10, base * 3.0 + 0.04, spread * 1.6) / sens
        if s[i] > thr and s[i] >= s[max(1, i - 1)] and (i + 1 >= n or s[i] >= s[i + 1]):
            cands.append((i, float(s[i] / thr)))
    # 太近的只留最强的
    gap = max(1, int(round(min_shot * fps)))
    keep = []
    for i, r in sorted(cands, key=lambda x: -x[1]):
        if all(abs(i - j) >= gap for j, _ in keep) and i >= gap and n - i >= max(2, gap // 2):
            keep.append((i, r))
    return sorted(i for i, _ in keep), s


def make_shots(cut_frames, n, fps):
    edges = [0] + list(cut_frames) + [n]
    return [{'no': k + 1, 'f0': edges[k], 'f1': edges[k + 1] - 1, 't0': edges[k] / fps, 't1': edges[k + 1] / fps,
             'dur': (edges[k + 1] - edges[k]) / fps} for k in range(len(edges) - 1)]


# ---------------------------------------------------------------- 镜头运动

def shot_motion(shot, a, fps):
    """把相邻帧的整体位移累加：每秒平移多少像素（原图像素）、每秒缩放多少"""
    ms = [a['motion'][i] for i in range(shot['f0'] + 1, shot['f1'] + 1)]   # 第一帧跨了切点，不算
    if not ms:
        return {'dx': 0, 'dy': 0, 'zoom': 0, 'ok': 0.0, 'desc': '太短，量不了'}
    ok = [m for m in ms if m[3]]
    okr = len(ok) / len(ms)
    if not ok:
        return {'dx': 0, 'dy': 0, 'zoom': 0, 'ok': 0.0, 'desc': '画面里能对上的纹理太少，量不出来'}
    dxs = np.array([m[0] for m in ok]); dys = np.array([m[1] for m in ok]); ss = np.array([m[2] for m in ok])
    k = a['scale']
    dx = float(np.median(dxs)) * k * fps; dy = float(np.median(dys)) * k * fps
    zoom = (float(np.exp(np.median(np.log(ss)) * fps)) - 1) * 100       # 每秒放大百分之几
    # 整段走了多少：用总和（不是中位数），给人直观感觉
    tot_x = dx * shot['dur']; tot_y = dy * shot['dur']     # 用中位数乘时长：个别帧被前景图形带偏不影响
    tot_z = (float(np.exp(np.median(np.log(ss)) * len(ms))) - 1) * 100
    W = a['scale'] * ANALYZE_W
    parts = []
    if abs(dx) > max(8, 0.005 * W):
        parts.append(f'画面内容往{"左" if dx < 0 else "右"}走 {abs(dx):.0f} px/秒（镜头往{"右" if dx < 0 else "左"}摇/移）')
    if abs(dy) > max(8, 0.005 * W):
        parts.append(f'画面内容往{"上" if dy < 0 else "下"}走 {abs(dy):.0f} px/秒（镜头往{"下" if dy < 0 else "上"}摇/移）')
    if abs(zoom) > 1.0:
        z = f'{"推近（放大）" if zoom > 0 else "拉远（缩小）"} 每秒 {abs(zoom):.1f}%'
        if abs(zoom) > 30:
            z += '（数太大，多半是贴地跟拍往前走、近处地面透视在变，不是真变焦，看联系表判断）'
        parts.append(z)
    desc = '；'.join(parts) if parts else '基本固定'
    mad = lambda x: 1.4826 * float(np.median(np.abs(x - np.median(x))))
    shake = (mad(dxs) + mad(dys)) * k
    if shake > 3 and parts:
        desc += '（有晃动）'
    elif shake > 3:
        desc = '基本固定，但在晃（手持感）'
    if okr < 0.6:
        desc += f'（只有 {okr * 100:.0f}% 的帧量得准，参考用）'
    return {'dx': round(dx, 1), 'dy': round(dy, 1), 'zoom': round(zoom, 2), 'total_dx': round(tot_x), 'total_dy': round(tot_y),
            'total_zoom': round(tot_z, 1), 'shake': round(shake, 1), 'ok': round(okr, 2), 'desc': desc}


# ---------------------------------------------------------------- 联系表

def _font(size):
    try:
        from mmlib.fonts import cjk_font
        f = cjk_font(size)
        if f:
            return f
    except Exception:
        pass
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _label(draw, xy, text, font, fill=(255, 235, 0)):
    x, y = xy
    bb = draw.textbbox((x, y), text, font=font)
    draw.rectangle((bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2), fill=(0, 0, 0))
    draw.text((x, y), text, fill=fill, font=font)


def make_sheet(cells, cols, path, title):
    """cells: [(jpeg bytes, 标签)]"""
    from PIL import Image, ImageDraw
    import io
    ims = [Image.open(io.BytesIO(b)).convert('RGB') for b, _ in cells]
    w, h = ims[0].size
    cols = max(1, min(cols, len(ims))); rows = (len(ims) + cols - 1) // cols
    pad, top = 4, 40
    sheet = Image.new('RGB', (cols * (w + pad) + pad, top + rows * (h + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    d.text((10, 8), title, fill=(255, 255, 255), font=_font(22))
    f = _font(max(14, w // 16))
    for k, (im, (_, lab)) in enumerate(zip(ims, cells)):
        x = pad + (k % cols) * (w + pad); y = top + pad + (k // cols) * (h + pad)
        sheet.paste(im, (x, y)); _label(d, (x + 5, y + 4), lab, f)
    sheet.save(path, quality=88)
    return path


def shot_sheets(shots, a, fps, step, cols, out):
    paths = []
    for s in shots:
        cells, t = [], 0.0
        while True:
            i = s['f0'] + int(round(t * fps))
            if i > s['f1']:
                break
            cells.append((a['thumbs'][i], f'{(i - s["f0"]) / fps:.2f}  ({i / fps:.2f})'))   # 标真实帧时间（23.976 帧这类不整除时不会差半帧）
            t = round(t + step, 6)
        if s['f1'] - s['f0'] > 1 and int(round((t - step) * fps)) + s['f0'] < s['f1']:
            cells.append((a['thumbs'][s['f1']], f'{(s["f1"] - s["f0"]) / fps:.2f} 末帧'))
        p = os.path.join(out, f'镜{s["no"]:02d}_联系表.jpg')
        make_sheet(cells, cols, p, f'镜 {s["no"]}  {s["t0"]:.2f}–{s["t1"]:.2f} 秒（{s["dur"]:.2f} 秒）  格子标签：镜内秒（全片秒）  每 {step:g} 秒一格')
        paths.append(p)
    # 全片总表：每镜一行，取 首帧 / 1/4 / 中 / 3/4 / 末帧
    cells = []
    for s in shots:
        for q, name in ((0, '首'), (0.25, '1/4'), (0.5, '中'), (0.75, '3/4'), (1, '末')):
            i = s['f0'] + int(round(q * (s['f1'] - s['f0'])))
            cells.append((a['thumbs'][i], f'镜{s["no"]} {name} {i / fps:.2f}s'))
    p = os.path.join(out, '全片总表.jpg')
    make_sheet(cells, 5, p, f'全片总表：{len(shots)} 个镜头，每行一个镜头（首 / 1/4 / 中 / 3/4 / 末）')
    return paths, p


# ---------------------------------------------------------------- 跟踪

def track(video, info, shot, box, t_at, fps):
    """在 shot 里跟踪 box（原图像素 x0,y0,x1,y1，第 t_at 秒时的位置），往前往后都跟。
    返回每帧 [帧号, cx, cy, w, h, 可信]（原图像素）"""
    cv2 = _cv2()
    f0, f1 = shot['f0'], shot['f1']
    if f1 - f0 + 1 > MAX_TRACK_FRAMES:
        _fail(f'这个镜头有 {f1 - f0 + 1} 帧，太长了（最多跟 {MAX_TRACK_FRAMES} 帧）。先用 ffmpeg 剪出要量的那一段再量')
    k = min(1.0, TRACK_W / info['w'])
    tw, th = _even(info['w'] * k), _even(info['h'] * k)
    kx, ky = tw / info['w'], th / info['h']
    frames = list(iter_frames(video, tw, th, gray=True, select=(f0, f1)))
    if not frames:
        _fail('跟踪时一帧都没读出来')
    start = min(max(int(round(t_at * fps)) - f0, 0), len(frames) - 1)
    x0, y0, x1, y1 = box
    b0 = np.array([(x0 + x1) / 2 * kx, (y0 + y1) / 2 * ky, abs(x1 - x0) * kx, abs(y1 - y0) * ky])
    if b0[2] < 8 or b0[3] < 8:
        _fail('跟踪框太小了，至少要十几个像素宽高')
    res = {start: (b0.copy(), True)}
    T0 = _crop(frames[start], b0)                       # 起始帧的样子：每帧都拿它校正，跟久了也不漂
    H_, W_ = frames[0].shape
    for direction in (1, -1):
        b = b0.copy(); tmpl = T0
        i = start
        while 0 <= i + direction < len(frames):
            j = i + direction
            prev = b.copy()
            b, lk_ok, tmpl = _track_step(cv2, frames[i], frames[j], b, tmpl)
            ok, agree = lk_ok, False
            if T0 is not None:
                rb, score = _refine(cv2, frames[j], b, T0, b0)
                far = np.hypot(rb[0] - b[0], rb[1] - b[1]) > 0.35 * b[2]
                agree = score >= 0.55 and not far
                if score >= 0.55 and not (lk_ok and far):     # 光流跟得好、模板却跳去别处：信光流
                    b, ok = rb, True
                elif score < 0.3 and not lk_ok:
                    ok = False
            # 一帧跳了大半个框：光流和模板都认同才算（真的很快的下落），否则多半跟丢，退回上一帧的位置
            if np.hypot(b[0] - prev[0], b[1] - prev[1]) > 0.6 * max(b[2], b[3]) and not (lk_ok and agree):
                b, ok = prev, False
            # 框一大半跑出画面：后面的都不算了
            vis_w = min(W_, b[0] + b[2] / 2) - max(0, b[0] - b[2] / 2)
            vis_h = min(H_, b[1] + b[3] / 2) - max(0, b[1] - b[3] / 2)
            if vis_w < 0.5 * b[2] or vis_h < 0.5 * b[3]:
                break
            res[j] = (b.copy(), ok); i = j
    rows = []
    for idx in sorted(res):
        bb, ok = res[idx]
        rows.append([f0 + idx, bb[0] / kx, bb[1] / ky, bb[2] / kx, bb[3] / ky, ok])
    return rows


def _crop(img, b):
    h, w = img.shape
    x0, y0 = int(max(0, b[0] - b[2] / 2)), int(max(0, b[1] - b[3] / 2))
    x1, y1 = int(min(w, b[0] + b[2] / 2)), int(min(h, b[1] + b[3] / 2))
    return img[y0:y1, x0:x1].copy() if x1 - x0 > 4 and y1 - y0 > 4 else None


def _track_step(cv2, a, b_img, box, tmpl):
    """一帧：先用框里的特征点光流 + 相似变换（能量出缩放），点不够再退回模板匹配（只量平移）"""
    h, w = a.shape
    cx, cy, bw, bh = box
    mask = np.zeros_like(a)
    mx0, my0 = int(max(0, cx - bw / 2)), int(max(0, cy - bh / 2))
    mx1, my1 = int(min(w, cx + bw / 2)), int(min(h, cy + bh / 2))
    if mx1 - mx0 < 4 or my1 - my0 < 4:
        return box, False, tmpl
    mask[my0:my1, mx0:mx1] = 255
    pts = cv2.goodFeaturesToTrack(a, maxCorners=200, qualityLevel=0.005, minDistance=3, blockSize=5, mask=mask)
    if pts is not None and len(pts) >= 8:
        win = int(np.clip(min(bw, bh) / 4, 11, 41)) | 1
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b_img, pts, None, winSize=(win, win), maxLevel=4)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(b_img, a, nxt, None, winSize=(win, win), maxLevel=4)
        fb = np.linalg.norm((back - pts).reshape(-1, 2), axis=1)
        good = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < 1.0)
        if good.sum() >= 6:
            M, inl = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=1.5)
            if M is not None and inl.sum() >= max(5, 0.4 * good.sum()):
                s = float(np.clip(np.hypot(M[0, 0], M[1, 0]), 0.92, 1.08))
                if abs(s - 1) < 0.004:                       # 小于 0.4% 当成没变，免得误差一帧帧累积
                    s = 1.0
                c = M @ np.array([cx, cy, 1.0])
                nb = np.array([c[0], c[1], bw * s, bh * s])
                return nb, True, _crop(b_img, nb)
    # 模板匹配兜底
    if tmpl is None or tmpl.size == 0:
        return box, False, tmpl
    th_, tw_ = tmpl.shape
    sx0, sy0 = int(max(0, cx - bw * 1.5)), int(max(0, cy - bh * 1.5))
    sx1, sy1 = int(min(w, cx + bw * 1.5)), int(min(h, cy + bh * 1.5))
    region = b_img[sy0:sy1, sx0:sx1]
    if region.shape[0] <= th_ or region.shape[1] <= tw_:
        return box, False, tmpl
    r = cv2.matchTemplate(region, tmpl, cv2.TM_CCOEFF_NORMED)
    _, mv, _, ml = cv2.minMaxLoc(r)
    nb = np.array([sx0 + ml[0] + tw_ / 2, sy0 + ml[1] + th_ / 2, bw, bh])
    ok = mv > 0.6
    return (nb if ok else box), ok, (_crop(b_img, nb) if ok else tmpl)


def _refine(cv2, img, box, T0, b0):
    """拿起始帧的样子在预测位置附近找（试三种大小），返回 (校正后的框, 相似度 0–1)"""
    h, w = img.shape
    cx, cy, bw, bh = box
    s_now = bw / b0[2]
    best = (box, -1.0)
    for s in (s_now * 0.96, s_now, s_now * 1.04):
        tw_, th_ = int(round(T0.shape[1] * s)), int(round(T0.shape[0] * s))
        if tw_ < 8 or th_ < 8:
            continue
        t = cv2.resize(T0, (tw_, th_), interpolation=cv2.INTER_AREA)
        sx0, sy0 = int(max(0, cx - bw)), int(max(0, cy - bh))
        sx1, sy1 = int(min(w, cx + bw)), int(min(h, cy + bh))
        region = img[sy0:sy1, sx0:sx1]
        if region.shape[0] <= th_ or region.shape[1] <= tw_:
            continue
        r = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv > best[1]:
            best = (np.array([sx0 + ml[0] + tw_ / 2, sy0 + ml[1] + th_ / 2, b0[2] * s, b0[3] * s]), float(mv))
    return best


# ---------------------------------------------------------------- 缓动拟合

BEZIER = {'linear': 'linear', 'ease-in': 'cubic-bezier(0.32, 0, 0.67, 0)',
          'ease-out': 'cubic-bezier(0.33, 1, 0.68, 1)', 'ease-in-out': 'cubic-bezier(0.65, 0, 0.35, 1)'}


def _ease(name, u):
    u = np.clip(u, 0, 1)
    if name == 'linear':
        return u
    if name == 'ease-in':
        return u ** 3
    if name == 'ease-out':
        return 1 - (1 - u) ** 3
    if name == 'ease-in-out':
        return np.where(u < 0.5, 4 * u ** 3, 1 - (-2 * u + 2) ** 3 / 2)
    raise ValueError(name)


def _spring(tau, zeta, omega):
    tau = np.maximum(tau, 0)
    wd = omega * np.sqrt(max(1 - zeta ** 2, 1e-6))
    return 1 - np.exp(-zeta * omega * tau) * (np.cos(wd * tau) + zeta * omega / wd * np.sin(wd * tau))


def fit_easings(t, v):
    """把一条曲线（t 秒、v 值）拟合成几种缓动。误差 = 均方根误差 / 这条曲线的总变化量"""
    from scipy.optimize import least_squares
    t = np.asarray(t, float); v = np.asarray(v, float)
    rng = float(v.max() - v.min())
    span = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    if len(t) < 5 or rng < 1e-9 or span <= 0:
        return None
    dt = span / max(1, len(t) - 1)
    a, b = float(np.median(v[:3])), float(np.median(v[-3:]))
    if abs(b - a) < 0.15 * rng:        # 起点终点差不多：来回运动（抖动、晃），不是一次缓动
        a, b = v[0], v[np.argmax(np.abs(v - v[0]))]
    p = (v - a) / (b - a if b != a else 1)
    def cross(q):
        idx = np.where(p >= q)[0]
        return t[idx[0]] if len(idx) else t[-1]
    t05, t95 = cross(0.05), cross(0.95)
    out = {}
    for name in ('linear', 'ease-in', 'ease-out', 'ease-in-out'):
        best = None
        for t0g, dg in ((t05, max(t95 - t05, 2 * dt)), (t[0], span), (t05 - 0.1 * span, (t95 - t05) * 1.3 + dt)):
            x0 = [t0g, max(dg, 2 * dt), a, b]
            lb = [t[0] - 2 * span, dt, -np.inf, -np.inf]; ub = [t[-1], 4 * span + dt, np.inf, np.inf]
            x0 = np.clip(x0, np.array(lb) + 1e-9, np.array(ub) - 1e-9)
            f = lambda x, name=name: x[2] + (x[3] - x[2]) * _ease(name, (t - x[0]) / x[1]) - v
            try:
                r = least_squares(f, x0, bounds=(lb, ub))
            except ValueError:
                continue
            if best is None or r.cost < best.cost:
                best = r
        if best is None:
            continue
        t0, d, v0, v1 = best.x
        err = float(np.sqrt(np.mean(best.fun ** 2)) / rng)
        info = {'误差': round(err, 3), '开始秒': round(t0, 3), '时长秒': round(d, 3), '起值': round(v0, 1), '终值': round(v1, 1),
                'css': BEZIER[name]}
        if name == 'linear':
            info['速度每秒'] = round((v1 - v0) / d, 1)
        out[name] = (err, info, lambda tt, x=best.x, name=name: x[2] + (x[3] - x[2]) * _ease(name, (tt - x[0]) / x[1]))
    best = None
    for zeta0 in (0.25, 0.5, 0.8):
        for om0 in (8.0, 15.0, 30.0):
            x0 = [t05 - dt, a, b, zeta0, om0]
            lb = [t[0] - span, -np.inf, -np.inf, 0.1, 1.0]; ub = [t[-1], np.inf, np.inf, 0.99, 120.0]
            x0 = np.clip(x0, np.array(lb) + 1e-9, np.array(ub) - 1e-9)
            f = lambda x: x[1] + (x[2] - x[1]) * _spring(t - x[0], x[3], x[4]) - v
            try:
                r = least_squares(f, x0, bounds=(lb, ub))
            except ValueError:
                continue
            if best is None or r.cost < best.cost:
                best = r
    if best is not None:
        t0, v0, v1, zeta, om = best.x
        err = float(np.sqrt(np.mean(best.fun ** 2)) / rng)
        overshoot = float(np.exp(-np.pi * zeta / np.sqrt(1 - zeta ** 2))) * 100
        settle = 4 / (zeta * om)
        out['spring'] = (err, {'误差': round(err, 3), '开始秒': round(t0, 3), '起值': round(v0, 1), '终值': round(v1, 1),
                               '阻尼比': round(zeta, 3), '角频率': round(om, 2), '过冲%': round(overshoot, 1),
                               '稳定用时秒': round(settle, 3), 'stiffness(质量1)': round(om ** 2, 1), 'damping(质量1)': round(2 * zeta * om, 2)},
                         lambda tt, x=best.x: x[1] + (x[2] - x[1]) * _spring(tt - x[0], x[3], x[4]))
    if not out:
        return None
    order = sorted(out, key=lambda k: out[k][0])
    pick = order[0]
    # 误差差不多时选简单的（spring 多两个参数，不能光凭小一点点就赢）
    simple = ['linear', 'ease-out', 'ease-in-out', 'ease-in', 'spring']
    for k in simple:
        if k in out and out[k][0] <= out[pick][0] + 0.005:
            pick = k; break
    if pick == 'spring' and out['spring'][1]['过冲%'] < 1.0:
        pick = order[0] if order[0] != 'spring' else order[1] if len(order) > 1 else 'spring'
    return {'best': pick, 'trusted': out[pick][0] <= UNTRUSTED, 'all': {k: out[k][1] for k in order},
            'curve': out[pick][2], 'range': rng}


# ---------------------------------------------------------------- 曲线图

def plot_track(rows, shot, fits, path, fps, span=None):
    from PIL import Image, ImageDraw
    props = [('x', '横向位置 x（px）', 1), ('y', '纵向位置 y（px）', 2), ('size', '大小（相对第一帧，%）', 3)]
    W, PH, L, R, T = 1200, 300, 90, 30, 50
    im = Image.new('RGB', (W, T + PH * 3 + 40), (255, 255, 255))
    d = ImageDraw.Draw(im)
    f = _font(18); fs = _font(14)
    d.text((L, 12), f'镜 {shot["no"]} 跟踪曲线（横轴：镜内秒）  点=量到的，线=最接近的缓动，灰点=那一帧没跟准' + ('，黄底=拟合用的那段' if span else ''), fill=(0, 0, 0), font=f)
    ts = np.array([(r[0] - shot['f0']) / fps for r in rows])
    for k, (key, title, col) in enumerate(props):
        y0 = T + k * PH; y1 = y0 + PH - 50
        vals = np.array([r[col] for r in rows], float)
        if key == 'size':
            vals = vals / vals[0] * 100
        oks = np.array([r[5] for r in rows])
        lo, hi = float(vals.min()), float(vals.max())
        if hi - lo < 1e-6:
            hi, lo = hi + 1, lo - 1
        pad_ = (hi - lo) * 0.1; lo -= pad_; hi += pad_
        tmax = max(ts.max(), 1e-3)
        X = lambda tt: L + (W - L - R) * tt / tmax
        Y = lambda vv: y1 - (y1 - y0 - 20) * (vv - lo) / (hi - lo)
        d.rectangle((L, y0 + 20, W - R, y1), outline=(180, 180, 180))
        fit = fits.get(key)
        lab = title
        if fit:
            lab += f'   → {fit["best"]}，误差 {fit["all"][fit["best"]]["误差"]:.3f}' + ('' if fit['trusted'] else '  【不可信】')
        elif fit is None:
            lab += '   （基本没变，不拟合）'
        d.text((L, y0), lab, fill=(0, 0, 0) if not fit or fit['trusted'] else (200, 0, 0), font=fs)
        for q in range(5):
            vv = lo + (hi - lo) * q / 4
            d.text((8, Y(vv) - 8), f'{vv:.0f}', fill=(90, 90, 90), font=fs)
        for q in range(int(tmax / 0.2) + 1):
            xx = X(q * 0.2); d.line((xx, y1, xx, y1 + 4), fill=(120, 120, 120))
            if q % 2 == 0:
                d.text((xx - 10, y1 + 6), f'{q * 0.2:.1f}', fill=(90, 90, 90), font=fs)
        if span:
            d.rectangle((X(span[0]), y0 + 21, X(span[1]), y1 - 1), fill=(255, 244, 214))
        if fit:
            tt = np.linspace(span[0], span[1], 400) if span else np.linspace(ts.min(), ts.max(), 400)
            cv = fit['curve'](tt)
            if key == 'size':
                cv = cv / float(np.array([r[3] for r in rows])[0]) * 100
            d.line(list(zip(X(tt), Y(cv))), fill=(220, 60, 60), width=2)
        for tt, vv, ok in zip(ts, vals, oks):
            c = (30, 90, 220) if ok else (170, 170, 170)
            d.ellipse((X(tt) - 3, Y(vv) - 3, X(tt) + 3, Y(vv) + 3), fill=c)
    im.save(path)
    return path


# ---------------------------------------------------------------- 主流程

def run(args):
    video = os.path.abspath(os.path.expanduser(args.video))
    if not os.path.exists(video):
        _fail(f'找不到这个视频：{args.video}')
    # 参数先检查一遍，免得分析完一整条片子才报错
    if args.track and args.box:
        _fail('--track 和 --box 二选一就行')
    if args.track: _nums(args.track, 3, 'track')
    if args.box: _nums(args.box, 4, 'box')
    if args.span:
        a0, a1 = _nums(args.span, 2, 'span')
        if a1 <= a0: _fail('--span 要写成 起,止，止要比起大（镜内秒），比如 --span 0.9,1.2')
        if not (args.track or args.box): _fail('--span 是给跟踪拟合用的，要和 --track 或 --box 一起写')
    if (args.at is not None or args.size) and not (args.track or args.box):
        _fail('--at / --size 是给跟踪用的，要和 --box / --track 一起写')
    if args.step <= 0: _fail('--step 要大于 0，比如 0.08')
    out = args.out or os.path.splitext(video)[0] + '_measure'
    os.makedirs(out, exist_ok=True)
    info = probe(video)
    print(f'读视频：{info["w"]}×{info["h"]}，每秒 {info["fps"]:g} 帧，约 {info["duration"] or 0:.2f} 秒。开始分析（一遍解码）…', file=sys.stderr)
    a = analyze(video, info, want_thumbs=not args.no_sheets)
    fps, n = info['fps'], a['n']

    # 1. 切点
    if args.cuts:
        cut_frames = sorted({int(round(float(x) * fps)) for x in args.cuts.replace('，', ',').split(',') if x.strip()})
        cut_frames = [c for c in cut_frames if 0 < c < n]
        cut_src = '手动给的'
    else:
        cut_frames, _ = find_cuts(a, fps, args.sens, args.min_shot)
        cut_src = '自动找的'
    shots = make_shots(cut_frames, n, fps)
    with open(os.path.join(out, 'cuts.csv'), 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh); w.writerow(['镜', '开始秒', '结束秒', '时长秒', '开始帧', '结束帧'])
        for s in shots:
            w.writerow([s['no'], f'{s["t0"]:.2f}', f'{s["t1"]:.2f}', f'{s["dur"]:.2f}', s['f0'], s['f1']])

    # 4. 镜头运动
    for s in shots:
        s['motion'] = shot_motion(s, a, fps)

    # 2. 联系表
    sheets, overview = ([], None)
    if not args.no_sheets:
        sheets, overview = shot_sheets(shots, a, fps, args.step, args.cols, out)

    # 3. 跟踪
    tr = None
    if args.track or args.box:
        tr = _do_track(args, video, info, shots, fps, out)

    md = write_md(out, video, info, shots, n, fps, cut_src, tr, args.step)
    res = {'输出文件夹': out, '镜头数': len(shots), '切点秒': [round(s['t0'], 2) for s in shots] + [round(n / fps, 2)],
           'measure.md': md, '切点表': os.path.join(out, 'cuts.csv')}
    if overview:
        res['全片总表'] = overview; res['每镜联系表'] = f'{len(sheets)} 张（镜01_联系表.jpg …）'
    if tr:
        res['跟踪'] = {k: tr[k] for k in ('csv', 'png', '结论')}
    res['下一步'] = '打开 measure.md，对着联系表填「画面」「图形」两列；切点不对就用 --sens 调灵敏度，或 --cuts 手动给'
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def _do_track(args, video, info, shots, fps, out):
    if args.track and args.box:
        _fail('--track 和 --box 二选一就行')
    if args.track:
        x, y, t = _nums(args.track, 3, 'track')
        half = (args.size or info['h'] * 0.15) / 2
        box = [x - half, y - half, x + half, y + half]
    else:
        box = _nums(args.box, 4, 'box')
        t = args.at
    if args.shot:
        if not 1 <= args.shot <= len(shots):
            _fail(f'--shot 要在 1 到 {len(shots)} 之间（这条片子一共 {len(shots)} 个镜头）')
        shot = shots[args.shot - 1]
        if t is None:
            t = shot['t0']
        if not shot['t0'] - 1e-6 <= t < shot['t1'] + 1e-6:
            _fail(f'第 {t:g} 秒不在镜 {shot["no"]}（{shot["t0"]:.2f}–{shot["t1"]:.2f} 秒）里，时间和镜头对不上')
    else:
        if t is None:
            t = 0.0
        shot = next((s for s in shots if s['t0'] - 1e-6 <= t < s['t1']), shots[-1])
    W, H = info['w'], info['h']
    if not (0 <= (box[0] + box[2]) / 2 <= W and 0 <= (box[1] + box[3]) / 2 <= H):
        _fail(f'跟踪框的中心跑到画面外了（画面是 {W}×{H}，坐标按原图像素写）')
    print(f'跟踪：镜 {shot["no"]}（{shot["t0"]:.2f}–{shot["t1"]:.2f} 秒），从第 {t:.2f} 秒的框 {[round(v) for v in box]} 往前后两头跟…', file=sys.stderr)
    rows = track(video, info, shot, box, t, fps)
    path_csv = os.path.join(out, f'跟踪_镜{shot["no"]:02d}.csv')
    with open(path_csv, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh); w.writerow(['全片秒', '镜内秒', '中心x', '中心y', '宽', '高', '大小%', '跟准了'])
        for r in rows:
            w.writerow([f'{r[0] / fps:.3f}', f'{(r[0] - shot["f0"]) / fps:.3f}', f'{r[1]:.1f}', f'{r[2]:.1f}', f'{r[3]:.1f}', f'{r[4]:.1f}',
                        f'{r[3] / rows[0][3] * 100:.1f}', '是' if r[5] else '否'])
    ok_rate = float(np.mean([r[5] for r in rows]))
    good = [r for r in rows if r[5]]
    span = None
    if args.span:
        span = _nums(args.span, 2, 'span')
        good = [r for r in good if span[0] - 1e-6 <= (r[0] - shot['f0']) / fps <= span[1] + 1e-6]
        if len(good) < 5:
            _fail(f'--span {args.span} 这一段里跟准的帧不到 5 帧，拟合不了：把这段放宽一点，或看曲线图换一段')
    ts = np.array([(r[0] - shot['f0']) / fps for r in good])
    fits = {}
    if len(good) >= 5:
        for key, col, thr in (('x', 1, 0.02 * W), ('y', 2, 0.02 * H), ('size', 3, 0.05)):
            vals = np.array([r[col] for r in good], float)
            if key == 'size':
                rel = vals / vals[0]
                if rel.max() - rel.min() < thr:
                    fits[key] = None; continue
            elif vals.max() - vals.min() < thr:
                fits[key] = None; continue
            fits[key] = fit_easings(ts, vals)
    path_png = plot_track(rows, shot, fits, os.path.join(out, f'跟踪_镜{shot["no"]:02d}.png'), fps, span)
    names = {'x': '横向位置', 'y': '纵向位置', 'size': '大小'}
    concl = []
    for key in ('x', 'y', 'size'):
        fit = fits.get(key)
        if fit is None:
            concl.append(f'{names[key]}：基本没变'); continue
        b = fit['all'][fit['best']]
        s = f'{names[key]}：最像 {fit["best"]}，误差 {b["误差"]:.3f}' + ('' if fit['trusted'] else '【不可信，看曲线图自己判断】')
        if fit['best'] == 'spring':
            s += f'，从 {b["开始秒"]:.2f} 秒开始，过冲 {b["过冲%"]:.0f}%，约 {b["稳定用时秒"]:.2f} 秒稳住（阻尼比 {b["阻尼比"]}，stiffness {b["stiffness(质量1)"]} / damping {b["damping(质量1)"]}）'
        else:
            s += f'，{b["开始秒"]:.2f} 秒开始、用时 {b["时长秒"]:.2f} 秒，{b["起值"]:.0f} → {b["终值"]:.0f}'
            if key == 'size':
                s = s.replace(f'{b["起值"]:.0f} → {b["终值"]:.0f}', f'{b["起值"]:.0f}px → {b["终值"]:.0f}px 宽')
            if fit['best'] == 'linear':
                s += f'（每秒 {abs(b["速度每秒"]):.0f} px）'
            s += f'；CSS 写 {b["css"]}'
        concl.append(s)
    # 一拍二检测：画面在动，但隔一帧就有一帧原地不动 → 动画是每 2 帧换一张画（12/12.5 张每秒）
    if len(good) >= 10:
        key_col = max((1, 2), key=lambda c: np.ptp([r[c] for r in good]))
        fr = [r[0] for r in good]; vv = [r[key_col] for r in good]
        steps = [abs(vv[q + 1] - vv[q]) for q in range(len(vv) - 1) if fr[q + 1] - fr[q] == 1]
        if len(steps) >= 8:
            steps = np.array(steps)
            m_even, m_odd = float(np.median(steps[0::2])), float(np.median(steps[1::2]))
            big, small = max(m_even, m_odd), min(m_even, m_odd)
            if big > 3 and small < 0.3 * big:
                concl.append(f'节奏：一帧走一大步、一帧几乎不动，交替出现——动画是「一拍二」（每 2 帧换一张画，相当于每秒 {fps / 2:g} 张），自己做时也按 2 帧一步走')
    if ok_rate < 0.8:
        concl.append(f'注意：只有 {ok_rate * 100:.0f}% 的帧跟准了（灰点），框选大一点、选纹理清楚的地方再跟一次')
    with open(os.path.join(out, f'跟踪_镜{shot["no"]:02d}_拟合.json'), 'w', encoding='utf-8') as fh:
        json.dump({k: (None if v is None else {'最接近': v['best'], '可信': v['trusted'], '各种缓动': v['all']}) for k, v in fits.items()},
                  fh, ensure_ascii=False, indent=1)
    if span:
        concl.insert(0, f'只拟合了镜内 {span[0]:g}–{span[1]:g} 秒这一段（--span），时间都是镜内秒')
    else:
        concl.append('拟合用的是整个镜头；镜头里有好几段动作（先飞、再落、再走）时，看曲线图挑一段，加 --span 起,止 重跑')
    return {'shot': shot, 'box': box, 't': t, 'csv': path_csv, 'png': path_png, '结论': concl, 'ok_rate': ok_rate, 'fits': fits}


def write_md(out, video, info, shots, n, fps, cut_src, tr, step):
    name = os.path.splitext(os.path.basename(video))[0]
    total = n / fps
    L = [f'# {name} 测量（{total:.2f} 秒，{info["w"]}×{info["h"]}，{fps:g}fps）', '',
         '> `mm measure` 自动量的。数字是机器量的，「画面」「图形」两列留给人对着联系表填。', '',
         '## 总体',
         f'- {len(shots)} 个镜头硬切（{cut_src}）：' + ' / '.join(f'{s["t0"]:.2f}'.rstrip('0').rstrip('.') if s['t0'] else '0' for s in shots)
         + f' / {total:.2f}。每镜 {min(s["dur"] for s in shots):.2f}–{max(s["dur"] for s in shots):.2f} 秒。',
         f'- 联系表：`镜NN_联系表.jpg`（每 {step:g} 秒一格，标签是「镜内秒（全片秒）」），全片总表 `全片总表.jpg`。', '',
         '## 逐镜（时间是镜内秒）',
         '| 镜 | 画面 | 图形 | 时间 | 镜头运动 |', '|---|---|---|---|---|']
    for s in shots:
        L.append(f'| {s["no"]}（{s["t0"]:.2f}–{s["t1"]:.2f}，{s["dur"]:.2f} 秒） |  |  |  | {s["motion"]["desc"]} |')
    L += ['', '## 切点表', '| 镜 | 开始秒 | 结束秒 | 时长 | 帧（开始–结束） |', '|---|---|---|---|---|']
    for s in shots:
        L.append(f'| {s["no"]} | {s["t0"]:.2f} | {s["t1"]:.2f} | {s["dur"]:.2f} | {s["f0"]}–{s["f1"]} |')
    L += ['', '## 镜头运动（整体画面，原图像素）',
          '| 镜 | 横向 px/秒 | 纵向 px/秒 | 缩放 %/秒 | 整镜累计（横 / 纵 / 缩放） | 晃动 | 量得准的帧 |', '|---|---|---|---|---|---|---|']
    for s in shots:
        m = s['motion']
        if 'total_dx' in m:
            L.append(f'| {s["no"]} | {m["dx"]:+.0f} | {m["dy"]:+.0f} | {m["zoom"]:+.1f} | {m["total_dx"]:+d} / {m["total_dy"]:+d} / {m["total_zoom"]:+.1f}% | {m["shake"]:.1f} | {m["ok"] * 100:.0f}% |')
        else:
            L.append(f'| {s["no"]} | – | – | – | – | – | {m["desc"]} |')
    L += ['', '正数 = 画面内容往右 / 往下走、放大；负数 = 往左 / 往上走、缩小。前景大物体（字、人）在动时会带偏，以联系表为准。']
    if tr:
        s = tr['shot']
        L += ['', f'## 跟踪（镜 {s["no"]}，第 {tr["t"]:.2f} 秒的框 {[round(v) for v in tr["box"]]}）',
              f'- 曲线：`{os.path.basename(tr["png"])}`，逐帧数据：`{os.path.basename(tr["csv"])}`，各缓动参数：`{os.path.basename(tr["csv"])[:-4]}_拟合.json`',
              f'- 跟准的帧：{tr["ok_rate"] * 100:.0f}%']
        L += [f'- {c}' for c in tr['结论']]
        L += [f'- 误差 = 拟合曲线和量到的点差多少（按总变化量算），超过 {UNTRUSTED} 不可信。']
    p = os.path.join(out, 'measure.md')
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    return p
