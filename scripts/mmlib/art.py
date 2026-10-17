"""拆图形：用户的 logo / 角色 / 海报 → 连通块 → 分组 → 每组按动法导出精灵图或「到达时间图」。"""
import base64
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

WORK_MAX = 1200          # 图形统一缩到最长边 1200 再处理，分组坐标都按这个尺寸


def load_rgba(path_or_img):
    """读 PNG（有透明通道直接用；没有就把背景色抠掉），缩到工作尺寸。返回 HxWx4 float(0-255)"""
    im = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    im = im.convert('RGBA')
    s = WORK_MAX / max(im.size)
    if s < 1:
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32)
    if a[..., 3].min() > 250:                      # 没有透明：用四角的颜色当背景
        rgb = a[..., :3]
        # 背景色从一圈边带里取（不只四个角）：AI 改图常在最外圈留一道发灰的边，只看角会取错
        bw = max(4, round(min(rgb.shape[:2]) * 0.02))
        band = np.zeros(rgb.shape[:2], bool); band[:bw] = band[-bw:] = True; band[:, :bw] = band[:, -bw:] = True
        bg = np.median(rgb[band], 0)
        d = np.sqrt(((rgb - bg) ** 2).sum(-1))
        # 贴边画了一道细框（AI 产品图常见）：四边各往里扫 8%，整条都和底色不同的行/列就是框线，连同外面一起当背景
        Hh0, Ww0 = d.shape
        for axis in (0, 1):
            n = Hh0 if axis == 0 else Ww0
            for side in (0, 1):
                last = -1
                for k in range(int(n * 0.08)):
                    idx = k if side == 0 else n - 1 - k
                    line = d[idx] if axis == 0 else d[:, idx]
                    if (line > 15).mean() > 0.6:
                        last = k
                if last >= 0:
                    sl = slice(0, last + 3) if side == 0 else slice(n - last - 3, n)
                    if axis == 0: d[sl] = 0
                    else: d[:, sl] = 0
        alpha = np.clip((d - 22) / 55, 0, 1)          # 阈值高一点：AI 图的底色常带纸纹/噪点，不能算进图形
        rim = max(2, round(min(rgb.shape[:2]) * 0.006))
        alpha[:rim] = alpha[-rim:] = 0; alpha[:, :rim] = alpha[:, -rim:] = 0     # 最外圈那道灰边直接清掉
        # 只去掉和图片边缘连着的背景：角色身上和背景同色的填充（白脸、白杯子）要保留
        near = d < 40
        lab, _ = ndi.label(near)
        edge_ids = np.unique(lab[band])
        outside = np.isin(lab, edge_ids[edge_ids > 0])
        inside_fill = near & ~outside
        # AI 图常在四周画一道浅灰细框：框里的整片底色会被当成「角色身上的填充」留下来。
        # 被包住的同色区域如果几乎撑满整张图（外接框超过 90%），其实是背景；那道细框本身也去掉
        Hh, Ww = near.shape
        flab, fn = ndi.label(inside_fill)
        if fn:
            for k, sl in enumerate(ndi.find_objects(flab)):
                if (sl[0].stop - sl[0].start) > 0.9 * Hh and (sl[1].stop - sl[1].start) > 0.9 * Ww:
                    reg = flab == k + 1
                    inside_fill &= ~reg; outside |= reg
            rlab, rn = ndi.label(~outside & (d >= 40))
            for k, sl in enumerate(ndi.find_objects(rlab)):
                bh, bw_ = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
                if bh > 0.9 * Hh and bw_ > 0.9 * Ww and (rlab[sl] == k + 1).sum() < 0.03 * bh * bw_:
                    outside |= rlab == k + 1          # 撑满整图、却很稀疏的一圈：就是那道框
        alpha = np.where(inside_fill, 1.0, alpha)
        alpha = np.where(outside, 0.0, alpha)          # 和边缘连成一片的背景（带渐变、纸纹）整块清掉
        # 去掉孤立的小斑点（纸纹噪点），面积按图片大小算；溅出来的小水珠比它们大，会保留
        lab2, n2 = ndi.label(alpha > 0.3)
        if n2:
            sizes = ndi.sum(np.ones_like(alpha), lab2, range(1, n2 + 1))
            tiny = np.isin(lab2, np.where(sizes < alpha.size * 4e-5)[0] + 1)
            alpha = np.where(ndi.binary_dilation(tiny, iterations=1) & ~np.isin(lab2, np.where(sizes >= alpha.size * 4e-5)[0] + 1), 0.0, alpha)
        safe = np.maximum(alpha, 1e-3)[..., None]
        a[..., :3] = np.where(inside_fill[..., None], rgb, np.clip(bg + (rgb - bg) / safe, 0, 255))
        a[..., 3] = alpha * 255
    # 四周留点边，免得零件贴边
    pad = 6
    a = np.pad(a, ((pad, pad), (pad, pad), (0, 0)))
    return a


def components(rgba, min_px=4):
    """实心块(>40%)分块，柔边分给最近的块。返回 (lab, comps)"""
    alpha = rgba[..., 3] / 255
    core, n = ndi.label(alpha > 0.4, structure=np.ones((3, 3)))
    if n == 0:
        return np.zeros(alpha.shape, int), []
    d, (iy, ix) = ndi.distance_transform_edt(core == 0, return_indices=True)
    lab = np.where((alpha > 0.01) & (d <= 3.0), core[iy, ix], 0)
    sizes = ndi.sum(np.ones_like(alpha), core, range(1, n + 1))
    comps = []
    for i, sl in enumerate(ndi.find_objects(lab)):
        if sl is None or sizes[i] < min_px:
            continue
        m = lab[sl] == i + 1
        cy, cx = ndi.center_of_mass(m)
        comps.append(dict(id=i + 1, x0=sl[1].start, x1=sl[1].stop, y0=sl[0].start, y1=sl[0].stop,
                          cx=float(cx + sl[1].start), cy=float(cy + sl[0].start), area=float(sizes[i])))
    return lab, comps


def lines_of(cs):
    """把字母串成词、词并成行：每个字母连到右边最近、间距小、高度接近的那个（弯曲排版也适用）。返回 [[comp...]...] 从上到下"""
    def best_right(a):
        h = a['y1'] - a['y0']
        hb = lambda b: b['y1'] - b['y0']
        # 下一个字母：中心在右边、离得近、高度接近；弧形排版时可以明显偏上或偏下
        cand = [b for b in cs if b is not a and b['cx'] > a['cx'] + 0.2 * h
                and np.hypot(b['cx'] - a['cx'], b['cy'] - a['cy']) < 1.5 * max(h, hb(b))
                and abs(b['cy'] - a['cy']) < 0.95 * max(h, hb(b)) and 0.5 < hb(b) / max(h, 1) < 2.0]
        return min(cand, key=lambda b: (b['cx'] - a['cx']) ** 2 + (b['cy'] - a['cy']) ** 2) if cand else None
    nxt = {id(a): best_right(a) for a in cs}
    for b in cs:
        lefts = [a for a in cs if nxt[id(a)] is b]
        for a in sorted(lefts, key=lambda a: (b['cx'] - a['cx']) ** 2 + (b['cy'] - a['cy']) ** 2)[1:]:
            nxt[id(a)] = None
    has_left = {id(b) for b in nxt.values() if b is not None}
    words = []
    for a in sorted(cs, key=lambda c: c['cy']):
        if id(a) in has_left:
            continue
        w, cur = [], a
        while cur is not None and cur not in w:
            w.append(cur)
            cur = nxt[id(cur)]
        words.append(w)
    words.sort(key=lambda w: np.mean([c['cy'] for c in w]))
    lines = []
    for w in words:
        hh = np.mean([c['y1'] - c['y0'] for c in w])
        if lines and abs(np.mean([c['cy'] for c in w]) - np.mean([c['cy'] for c in lines[-1]])) < 0.6 * hh:
            lines[-1] += w
        else:
            lines.append(list(w))
    return [sorted(l, key=lambda c: c['x0']) for l in lines]


def _is_text_line(l):
    """一行字：至少 3 个块，大部分块的高度接近（插画零件高矮差很多）"""
    if len(l) < 3:
        return False
    hs = np.array([c['y1'] - c['y0'] for c in l], float)
    med = np.median(hs)
    if np.mean((hs > 0.6 * med) & (hs < 1.5 * med)) < 0.75:
        return False
    # 字母左右排开、基本不重叠；插画零件常常上下叠着（宽度加起来远超整行宽度）
    span = max(c['x1'] for c in l) - min(c['x0'] for c in l)
    return sum(c['x1'] - c['x0'] for c in l) / max(span, 1) < 1.35


def auto_groups(comps, H, W):
    """没给分组时的猜测：高度相近、成行排列的块是字（最高的那几行=标题，其余=小字），剩下的是图形。
    返回的组带 ids（直接记零件编号），box 只是给人看的范围。"""
    if not comps:
        return []
    cand = [c for c in comps if c['area'] < 0.25 * H * W]
    lines = [l for l in lines_of(cand) if _is_text_line(l)]
    text_ids = {c['id'] for l in lines for c in l}
    graphic = [c for c in comps if c['id'] not in text_ids]
    groups = []
    def box(cs, pad=4):
        return [int(min(c['x0'] for c in cs) - pad), int(min(c['y0'] for c in cs) - pad),
                int(max(c['x1'] for c in cs) + pad), int(max(c['y1'] for c in cs) + pad)]
    if graphic:
        groups.append(dict(name='graphic', box=box(graphic), motion='draw', ids=[c['id'] for c in graphic]))
    if lines:
        hs = [np.median([c['y1'] - c['y0'] for c in l]) for l in lines]
        top = max(hs)
        title = sum([l for l, h in zip(lines, hs) if h > 0.7 * top], [])
        rest = sum([l for l, h in zip(lines, hs) if h <= 0.7 * top], [])
        groups.append(dict(name='title', box=box(title), motion='pop', ids=[c['id'] for c in title]))
        if rest:
            groups.append(dict(name='small', box=box(rest), motion='type', ids=[c['id'] for c in rest]))
    return groups


def assign(comps, groups):
    """每个块按中心点落在哪个框，归到哪组（先写的优先）；box 写 "rest" 的组收剩下所有块。"""
    for c in comps:
        c['g'] = None
        for gi, g in enumerate(groups):
            if c['id'] in g.get('ids', ()):
                c['g'] = gi
                break
        if c['g'] is not None:
            continue
        for gi, g in enumerate(groups):
            b = g.get('box')
            if b == 'rest' or b is None:
                continue
            if b[0] <= c['cx'] <= b[2] and b[1] <= c['cy'] <= b[3]:
                c['g'] = gi
                break
    rest = [gi for gi, g in enumerate(groups) if g.get('box') in ('rest', None)]
    if rest:
        for c in comps:
            if c['g'] is None:
                c['g'] = rest[0]


def _b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode()


def crop_sprite(rgba, lab, cs, pad=3):
    H, W = lab.shape
    x0 = max(min(c['x0'] for c in cs) - pad, 0); y0 = max(min(c['y0'] for c in cs) - pad, 0)
    x1 = min(max(c['x1'] for c in cs) + pad, W); y1 = min(max(c['y1'] for c in cs) + pad, H)
    m = np.isin(lab[y0:y1, x0:x1], [c['id'] for c in cs])
    out = rgba[y0:y1, x0:x1].copy()
    out[..., 3] = np.where(m, out[..., 3], 0)
    a = out[..., 3] / 255
    yy, xx = np.mgrid[y0:y1, x0:x1]
    tot = max(a.sum(), 1e-6)
    info = dict(x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0),
                cx=round(float((a * xx).sum() / tot), 2), cy=round(float((a * yy).sum() / tot), 2))
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), 'RGBA'), info


def _geodesic(sub_alpha, seeds):
    """墨沿线条走便宜、跨空白贵：返回归一化到 0-1 的到达时间"""
    from skimage.graph import MCP_Geometric
    cost = np.where(sub_alpha > 0.3, 1.0, np.where(sub_alpha > 0.02, 1.6, 9.0))
    T, _ = MCP_Geometric(cost).find_costs(seeds)
    ink = sub_alpha > 0.02
    mx = T[ink].max() if ink.any() else 1
    Tn = np.clip(T / max(mx, 1e-6), 0, 1)
    Tn[~ink] = 1
    return Tn


def _seed_point(sub, rule, ox, oy):
    yy, xx = np.where(sub > 0.5)
    if not len(yy):
        yy, xx = np.where(sub > 0.02)
    if isinstance(rule, (list, tuple)):
        k = np.argmin((xx - (rule[0] - ox)) ** 2 + (yy - (rule[1] - oy)) ** 2)
    else:
        k = {'left': np.argmin(xx), 'right': np.argmax(xx), 'top': np.argmin(yy),
             'bottom': np.argmax(yy)}.get(rule, np.argmin(xx))
    return (int(yy[k]), int(xx[k]))


def time_map(rgba, lab, cs, motion, seed='left'):
    """draw：整组从一个起笔点流过去；write：逐个字母按阅读顺序各自从左边写；outline：额外给轮廓带和剪影"""
    m = np.isin(lab, [c['id'] for c in cs])
    ys, xs = np.where(m)
    H, W = lab.shape
    x0, y0 = max(xs.min() - 4, 0), max(ys.min() - 4, 0)
    x1, y1 = min(xs.max() + 5, W), min(ys.max() + 5, H)
    alpha = rgba[y0:y1, x0:x1, 3] / 255 * m[y0:y1, x0:x1]
    out = dict(x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0))
    if motion == 'write':
        order = [c for l in lines_of(cs) for c in l]
        n = max(len(order), 1)
        T = np.ones_like(alpha)
        sublab = lab[y0:y1, x0:x1]
        overlap = 0.8
        for k, c in enumerate(order):
            mk = (sublab == c['id'])
            a = alpha * mk
            if a.max() <= 0:
                continue
            Tk = _geodesic(a, [_seed_point(a, 'left', 0, 0)])
            g = (k * overlap + Tk) / ((n - 1) * overlap + 1)
            T = np.where(mk & (a > 0.01), g, T)
    else:
        T = _geodesic(alpha, [_seed_point(alpha, seed, x0, y0)])
    out['time'] = _b64((T * 65535).astype('<u2'))
    if motion == 'outline':
        sil = ndi.binary_fill_holes(ndi.binary_closing(alpha > 0.3, iterations=2))
        sil = ndi.binary_dilation(sil, iterations=1)
        lw = max(3, int(round(min(out['w'], out['h']) * 0.022)))
        band = sil & ~ndi.binary_erosion(sil, iterations=lw)
        bandf = ndi.gaussian_filter(band.astype(np.float32), 0.6)
        Tb = _geodesic(bandf, [_seed_point(bandf, seed, x0, y0)])
        silf = ndi.gaussian_filter(sil.astype(np.float32), 0.7)
        out['band'] = _b64(np.clip(bandf * 255, 0, 255).astype(np.uint8))
        out['bandTime'] = _b64((Tb * 65535).astype('<u2'))
        out['sil'] = _b64(np.clip(silf * 255, 0, 255).astype(np.uint8))
        # 剪影填什么颜色：图形里最多的那种颜色
        px = rgba[y0:y1, x0:x1][alpha > 0.8][:, :3]
        if len(px):
            q = (px // 32).astype(int)
            keys, cnt = np.unique(q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2], return_counts=True)
            kk = keys[np.argmax(cnt)]
            sel = px[(q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]) == kk]
            out['fill'] = [int(v) for v in sel.mean(0)]
        else:
            out['fill'] = [255, 255, 255]
    return out


def front_path(tm, steps=64):
    """画出来的「笔头」轨迹：每个进度上，刚画到的那一圈像素的中心。给发光笔头用（图形坐标）"""
    T = np.frombuffer(base64.b64decode(tm['time']), '<u2').reshape(tm['h'], tm['w']) / 65535
    ys, xs = np.where(T < 0.999)
    tv = T[ys, xs]
    out, last = [], [float(xs.mean() + tm['x']), float(ys.mean() + tm['y'])] if len(xs) else [0, 0]
    for p in np.linspace(0, 1, steps):
        m = np.abs(tv - p) < 0.015
        if m.sum() >= 3:
            last = [float(xs[m].mean() + tm['x']), float(ys[m].mean() + tm['y'])]
        out.append([round(last[0], 1), round(last[1], 1)])
    return out


def split_by_seam(rgba, n=2, band=(0.25, 0.75), border=0.011, border_color=(255, 255, 255)):
    """一张图里画了几个角色（比如两个人站一起）→ 沿着人和人之间最空的地方找一条从上到下的切缝，切成 n 张。
    切缝避开线条（深色）、尽量走背景和白边；每张保持原图大小和坐标，所以多张姿势切出来依然对齐。"""
    H, W = rgba.shape[:2]
    a = rgba[..., 3] / 255
    lum = rgba[..., :3] @ np.array([0.299, 0.587, 0.114])
    rgb_ = rgba[..., :3]; mx_ = rgb_.max(-1); sat = (mx_ - rgb_.min(-1)) / np.maximum(mx_, 1)
    line = ndi.binary_dilation((a > 0.5) & ((sat > 0.18) | (lum < 120)), iterations=2)   # 线条：有颜色或很黑（白边、灰底都不算）
    lab, _ = ndi.label(~line)
    edge_ids = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    outside = np.isin(lab, edge_ids[edge_ids > 0])                            # 从图边走得到、不用跨线的地方：背景 + 贴纸白边 + 人和人之间的缝
    inside = ~outside & ~line                                                 # 线条围起来的人物内部
    cost = np.where(outside, 0.0, np.where(line, 2.0, 8.0)) + 0.02            # 走外面免费，跨线要钱，穿过人物内部很贵
    # 往「人和人之间」靠：按人物的横向分布找出 n 团的中心，取相邻两团之间最空的那一列当目标
    mass = ndi.gaussian_filter1d((line | inside).sum(0).astype(float), W * 0.02)
    xs_ = np.arange(W); wts = mass / max(mass.sum(), 1e-6)
    centers = np.quantile(np.repeat(xs_, np.maximum((wts * 20000).astype(int), 0)), [(i + 0.5) / n for i in range(n)]) if wts.sum() > 0 else np.linspace(0, W, n + 2)[1:-1]
    targets = [int(centers[k] + np.argmin(mass[int(centers[k]):int(centers[k + 1]) + 1])) for k in range(n - 1)]
    cuts = []
    for k in range(1, n):
        c0 = int(W * (band[0] + (band[1] - band[0]) * (k - 1) / max(n - 1, 1))) if n > 2 else int(W * band[0])
        c1 = int(W * (band[0] + (band[1] - band[0]) * k / max(n - 1, 1))) if n > 2 else int(W * band[1])
        tgt = targets[k - 1]
        c0, c1 = max(0, tgt - int(W * 0.18)), min(W, tgt + int(W * 0.18))
        sub = cost[:, c0:c1] + 0.05 * np.abs(np.arange(c0, c1)[None, :] - tgt) / (0.1 * W)
        acc = sub.copy()
        for y in range(1, H):
            prev = acc[y - 1]
            left = np.r_[np.inf, prev[:-1]]; right = np.r_[prev[1:], np.inf]
            acc[y] += np.minimum(np.minimum(left, prev), right)
        seam = np.zeros(H, int); seam[-1] = int(np.argmin(acc[-1]))
        for y in range(H - 2, -1, -1):
            x = seam[y + 1]; lo, hi = max(x - 1, 0), min(x + 2, sub.shape[1])
            seam[y] = lo + int(np.argmin(acc[y, lo:hi]))
        cuts.append(seam + c0)
    xs = np.arange(W)[None, :]
    body = (line | inside) & (a > 0.3)                     # 角色本身（线条 + 线条围起来的部分），不含外面那圈共用的白边
    bw = max(3, int(round(W * border))) if border else 0
    outs, prev = [], np.zeros(H, int)
    for k in range(n):
        right = cuts[k] if k < len(cuts) else np.full(H, W)
        m = (xs >= prev[:, None]) & (xs < right[:, None])
        o = rgba.copy()
        if border is None:                                 # 不重画白边：直接按切缝切
            o[..., 3] = np.where(m, o[..., 3], 0)
        else:                                              # 去掉共用白边，给这个角色单独描一圈（两个人不再粘成一张贴纸）
            own = body & m
            own = ndi.binary_fill_holes(ndi.binary_closing(own, iterations=5)) & (a > 0.3)   # 线条小缺口闭合，脸、身体里面填满
            ring = ndi.binary_dilation(own, iterations=bw) & ~own if bw else np.zeros_like(own)
            soft = np.clip(ndi.gaussian_filter((own | ring).astype(float), 0.8) * 1.4, 0, 1)
            o[..., :3] = np.where(ring[..., None], np.array(border_color, float), o[..., :3])
            o[..., 3] = np.where(own, o[..., 3], soft * 255)
        outs.append(o)
        prev = right
    return outs, cuts
