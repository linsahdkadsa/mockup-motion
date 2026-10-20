"""选格子定位：不让 agent 读像素坐标，而是在编号网格上选格子 → 放大再选 → 画回看图确认。

做法来自 HeyGen HyperFrames 的「grounding」协议（skills/motion-graphics/grounding/PROTOCOL.md、locate.mjs，
https://github.com/heygen-com/hyperframes ，Apache-2.0 许可）：模型猜像素坐标误差大，但「目标在哪个编号格」选得准；
先粗格、裁出来放大再细格、最后画框回看。他们实测中心误差 6.5% → 2.3%。
这里按 mockup-motion 的需要用 Python 重写（没有照搬代码）：格子按「列字母 + 行数字」编号（C4），
放大可以一层层往下（C4.b3.e2），回看图带每个点的局部放大，坐标同时给原图像素 src 和 scene.json 用的 scene。

用法（接进 mm.py：from mmlib import pick; pick.add_parser(sp)；分发表里加 'pick': pick.run）
  mm pick 图.png                           出第一层编号网格（列 A、B、C…，行 1、2、3…）
  mm pick 图.png --cell C4                 把 C4 放大成第二层网格（子格 a1…f6）；--cell C4:D5 放大一片；--cell C4.b3 再往下放大一层
  mm pick 图.png --at C4.b3                取这一格的中心点，出 src / scene 坐标和回看图
  mm pick 图.png --box C4.b3 F6.d5         两个点当矩形的左上、右下角
  mm pick 图.png --quad A B C D            四个点当四边形（surface.quad），自动排成 左上、右上、右下、左下
  点落在两格交界上：写一片 b3:c4，取的是这一片的中心（也就是交叉点）
"""
import json, math, os, re, sys

PAD = 0.25          # 放大时在选中的格子四周多裁 0.25 格，目标贴着格子边也看得见（HyperFrames 第二层用的就是 0.25）
SUB = 6             # 第二层起每层的格数（短边 6 格，长边按比例，最多 12）
ZOOM_LONG = 1000    # 放大图长边像素
VIEW_LONG = 1400    # 第一层网格图长边最多多少像素


# ---------- 坐标换算：和 mm grid 用同一个 _scene_scale ----------
def _scene_scale(path, W, H):
    m = sys.modules.get('__main__')
    f = getattr(m, '_scene_scale', None)
    if f is None:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from mm import _scene_scale as f
    return f(path, W, H)


# ---------- 格子路径 ----------
_TOK = re.compile(r'^([A-Za-z])(\d+)(?::([A-Za-z])(\d+))?$')


def _dims(w, h, level, cols0):
    """这一层分几列几行：第一层固定 cols0 列、行数让格子接近正方形；之后短边 SUB 格"""
    if level == 0:
        c = max(2, min(26, cols0)); return c, max(1, round(c * h / w))
    if w >= h:
        return max(1, min(2 * SUB, 26, round(SUB * w / h))), SUB
    return SUB, max(1, min(2 * SUB, round(SUB * h / w)))


def _label(level, ci, ri):
    ch = chr(65 + ci)
    return (ch if level == 0 else ch.lower()) + str(ri + 1)


def _resolve(path, W, H, cols0):
    """'C4.b3' → 每一层的区域和网格；最后一层选中的矩形（原图像素，浮点）"""
    toks = [t for t in path.strip().split('.') if t]
    if not toks: raise SystemExit('格子写成 C4、C4.b3 或 C4:D5 这样')
    region = (0.0, 0.0, float(W), float(H)); levels = []
    for i, t in enumerate(toks):
        m = _TOK.match(t.strip())
        if not m: raise SystemExit(f'看不懂「{t}」：每层写 字母+数字（C4），框一片写 C4:D5，层之间用点隔开（C4.b3）')
        x0, y0, x1, y1 = region; rw, rh = x1 - x0, y1 - y0
        cols, rows = _dims(rw, rh, i, cols0)
        c0, r0 = ord(m[1].upper()) - 65, int(m[2]) - 1
        c1, r1 = (ord(m[3].upper()) - 65, int(m[4]) - 1) if m[3] else (c0, r0)
        c0, c1 = sorted((c0, c1)); r0, r1 = sorted((r0, r1))
        if c1 >= cols or r1 >= rows or c0 < 0 or r0 < 0:
            last = _label(i, cols - 1, rows - 1)
            raise SystemExit(f'「{t}」超出第 {i + 1} 层网格：这一层是 {cols} 列 × {rows} 行（{_label(i, 0, 0)} 到 {last}）')
        cw, chh = rw / cols, rh / rows
        sel = (x0 + c0 * cw, y0 + r0 * chh, x0 + (c1 + 1) * cw, y0 + (r1 + 1) * chh)
        levels.append(dict(region=region, cols=cols, rows=rows, cell=(cw, chh), sel=sel, tok=t))
        region = (max(0.0, sel[0] - PAD * cw), max(0.0, sel[1] - PAD * chh), min(float(W), sel[2] + PAD * cw), min(float(H), sel[3] + PAD * chh))
    return levels, region


# ---------- 画图 ----------
def _load(path):
    from PIL import Image
    src = Image.open(path)
    if src.mode in ('RGBA', 'LA', 'P'):            # 透明图铺中灰
        src = src.convert('RGBA'); bg = Image.new('RGBA', src.size, (128, 128, 128, 255)); bg.alpha_composite(src); src = bg
    return src.convert('RGB')


def _font(size):
    from mmlib.fonts import cjk_font
    f = cjk_font(size)
    if f is None:
        from PIL import ImageFont
        try: f = ImageFont.load_default(size)
        except TypeError: f = ImageFont.load_default()
    return f


def _view(im, region, long_side, upscale=True):
    """把原图的 region（浮点）精确映射成一张图；返回 图, 缩放倍数"""
    from PIL import Image
    x0, y0, x1, y1 = region
    z = long_side / max(x1 - x0, y1 - y0)
    if not upscale: z = min(z, 1.0)
    size = (max(1, round((x1 - x0) * z)), max(1, round((y1 - y0) * z)))
    return im.transform(size, Image.EXTENT, (x0, y0, x1, y1), Image.BICUBIC), z


def _draw_grid(view, cols, rows, level, mark=None):
    """半透明网格 + 每格左上角编号 + 四边列号行号。mark：画面坐标的矩形，画黄色虚线（上一层选中的格子）"""
    from PIL import Image, ImageDraw
    w, h = view.size; cw, ch = w / cols, h / rows
    ov = Image.new('RGBA', view.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    for i in range(1, cols):
        x = round(i * cw); d.line([(x + 1, 0), (x + 1, h)], fill=(0, 0, 0, 120), width=1); d.line([(x, 0), (x, h)], fill=(255, 255, 255, 170), width=1)
    for j in range(1, rows):
        y = round(j * ch); d.line([(0, y + 1), (w, y + 1)], fill=(0, 0, 0, 120), width=1); d.line([(0, y), (w, y)], fill=(255, 255, 255, 170), width=1)
    fs = max(11, min(30, int(min(cw, ch) / 4.2))); f = _font(fs)
    for ci in range(cols):
        for ri in range(rows):
            t = _label(level, ci, ri); x, y = ci * cw + 3, ri * ch + 3
            bb = d.textbbox((x, y), t, font=f)
            d.rectangle([bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1], fill=(0, 0, 0, 110))
            d.text((x, y), t, font=f, fill=(255, 255, 255, 215))
    if mark:
        x0, y0, x1, y1 = mark
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            L = math.hypot(b[0] - a[0], b[1] - a[1]); n = max(1, int(L / 10))
            for k in range(0, n, 2):
                p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n); q = (a[0] + (b[0] - a[0]) * min(k + 1, n) / n, a[1] + (b[1] - a[1]) * min(k + 1, n) / n)
                d.line([p, q], fill=(255, 215, 0, 230), width=2)
    out = view.convert('RGBA'); out.alpha_composite(ov)
    return out.convert('RGB')


def _cross(d, x, y, r, col, w=2):
    d.line([(x - r, y), (x - 3, y)], fill=col, width=w); d.line([(x + 3, y), (x + r, y)], fill=col, width=w)
    d.line([(x, y - r), (x, y - 3)], fill=col, width=w); d.line([(x, y + 3), (x, y + r)], fill=col, width=w)


def _check_image(im, pts, names, shape, kx, ky, out):
    """回看图：上面整张图（点 / 框 / 四边形），下面每个点一张局部放大（±60 scene 像素，放大 3 倍以上）"""
    from PIL import Image, ImageDraw
    W, H = im.size
    full, z = _view(im, (0, 0, W, H), VIEW_LONG, upscale=False)
    d = ImageDraw.Draw(full); col = (255, 40, 200)
    sp = [(x * z, y * z) for x, y in pts]
    if shape == 'quad' or shape == 'box':
        poly = sp if shape == 'quad' else [sp[0], (sp[1][0], sp[0][1]), sp[1], (sp[0][0], sp[1][1])]
        d.line(poly + [poly[0]], fill=col, width=3)
    f = _font(max(14, full.width // 60))
    for (x, y), n, (ox, oy) in zip(sp, names, pts):
        _cross(d, x, y, 18, (0, 230, 255), 2)
        t = f'{n} ({round(ox * kx)},{round(oy * ky)})'
        tw = d.textlength(t, font=f); tx = x + 8 if x + 8 + tw + 4 < full.width else x - 8 - tw   # 靠右边就写到点的左边
        ty = y + 8 if y + 8 + f.size + 6 < full.height else y - 8 - f.size - 4
        bb = d.textbbox((tx, ty), t, font=f); d.rectangle([bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2], fill=(0, 0, 0))
        d.text((tx, ty), t, font=f, fill=(255, 255, 255))
    # 局部放大
    R = 60 / max(kx, 1e-6)                                    # 原图像素半径
    tile = 320; insets = []
    for (ox, oy), n in zip(pts, names):
        reg = (ox - R, oy - R, ox + R, oy + R)
        v, zz = _view(im, reg, tile)
        dv = ImageDraw.Draw(v)
        if shape in ('quad', 'box'):
            poly = pts if shape == 'quad' else [pts[0], (pts[1][0], pts[0][1]), pts[1], (pts[0][0], pts[1][1])]
            pp = [((px - reg[0]) * zz, (py - reg[1]) * zz) for px, py in poly]
            dv.line(pp + [pp[0]], fill=col, width=2)
        c = tile / 2; _cross(dv, c, c, 40, (0, 230, 255), 2)
        fi = _font(18); dv.rectangle([0, 0, tile, 26], fill=(0, 0, 0)); dv.text((6, 3), f'{n}  放大×{zz * max(kx, 1e-6):.1f}', font=fi, fill=(255, 255, 255))
        insets.append(v)
    per = max(1, full.width // tile)
    rows_ = math.ceil(len(insets) / per)
    canvas = Image.new('RGB', (full.width, full.height + rows_ * (tile + 8) + 8), (20, 20, 20))
    canvas.paste(full, (0, 0))
    for i, v in enumerate(insets):
        canvas.paste(v, (8 + (i % per) * (tile + 8), full.height + 8 + (i // per) * (tile + 8)))
    canvas.save(out)


# ---------- 命令 ----------
def add_parser(sp):
    p = sp.add_parser('pick', help='选格子定位：编号网格选格 → 放大再选 → 回看确认，出 src / scene 坐标')
    p.add_argument('image')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--cell', help='放大这一格（或一片 C4:D5），出下一层网格。可多层：C4.b3')
    g.add_argument('--at', help='取这一格的中心点，如 C4.b3；也能写一片 C4.b3:c4，取那片的中心')
    g.add_argument('--box', nargs='+', metavar='点', help='矩形的两个对角点，空格隔开：--box C4.b3 F6.d5（点里不框片时也可写成 C4.b3:F6.d5）')
    g.add_argument('--quad', nargs='+', metavar='点', help='四边形的四个角，空格隔开：--quad B2.c4 G2.d4 G8.d5 B8.b5（surface.quad 用，会排成 左上、右上、右下、左下）')
    p.add_argument('--cols', type=int, default=8, help='第一层分几列（默认 8，行数自动让格子接近正方形）。换了列数，之前选的格子名就不对了')
    p.add_argument('--out', help='输出图片路径（默认放在图旁边：_pick.png / _pick_C4.png / _pick_check.png）')
    return p


def _pt(path, W, H, cols):
    levels, _ = _resolve(path, W, H, cols)
    s = levels[-1]['sel']; cw, ch = levels[-1]['cell']
    return ((s[0] + s[2]) / 2, (s[1] + s[3]) / 2), (cw / 2, ch / 2)


def _sc(x, y, kx, ky):
    return [round(x * kx), round(y * ky)]


def _order_quad(pts):
    """[(点, 名字)] 排成 左上、右上、右下、左下（先按 y 分上下两对，再各自按 x）"""
    s = sorted(pts, key=lambda p: p[0][1]); top = sorted(s[:2], key=lambda p: p[0][0]); bot = sorted(s[2:], key=lambda p: p[0][0])
    return [top[0], top[1], bot[1], bot[0]]


def run(a):
    im = _load(a.image); W, H = im.size
    kx, ky, why, short = _scene_scale(a.image, W, H)
    two = abs(kx - 1) > 0.002 or abs(ky - 1) > 0.002
    stem = os.path.splitext(a.image)[0]
    conv = (f'scene = 原图像素 × {kx:.4f}' + (f'（y × {ky:.4f}）' if abs(kx - ky) > 0.002 else '') + f'；{why}') if two else f'scene 坐标 = 原图像素（{why}）'
    res = {'原图尺寸': [W, H], 'scene 换算': conv}

    if not (a.at or a.box or a.quad):                       # 网格图：第一层，或 --cell 放大
        if a.cell:
            levels, region = _resolve(a.cell, W, H, a.cols)
            lv = len(levels)
            view, z = _view(im, region, ZOOM_LONG)
            cols, rows = _dims(region[2] - region[0], region[3] - region[1], lv, a.cols)
            s = levels[-1]['sel']
            mark = ((s[0] - region[0]) * z, (s[1] - region[1]) * z, (s[2] - region[0]) * z, (s[3] - region[1]) * z)
            img = _draw_grid(view, cols, rows, lv, mark)
            out = a.out or f'{stem}_pick_{a.cell.replace(":", "-").replace(".", "_")}.png'
            ex = a.cell + '.' + _label(lv, min(1, cols - 1), min(2, rows - 1))
            sub_w, sub_h = (region[2] - region[0]) / cols, (region[3] - region[1]) / rows
            res |= {'grid': out, '层': lv + 1, '网格': f'{cols} 列 × {rows} 行（{_label(lv, 0, 0)} 到 {_label(lv, cols - 1, rows - 1)}）',
                    '放大区域 src': [round(v) for v in region], '一格多大 src': [round(sub_w, 1), round(sub_h, 1)],
                    '提示': f'黄色虚线是你上一层选的格子，四周多裁了 {PAD} 格。看图找到目标落在哪一格，在格子名后面接上去：'
                            f'取点 --at {ex}；还嫌粗就再放大 --cell {ex}；选错了就回上一层重选。'
                            f'取点精度约 ±{max(sub_w, sub_h) / 2 * max(kx, ky):.0f} scene 像素'}
        else:
            view, z = _view(im, (0, 0, W, H), VIEW_LONG, upscale=False)
            cols, rows = _dims(W, H, 0, a.cols)
            img = _draw_grid(view, cols, rows, 0)
            out = a.out or f'{stem}_pick.png'
            res |= {'grid': out, '层': 1, '网格': f'{cols} 列 × {rows} 行（A1 到 {_label(0, cols - 1, rows - 1)}，列是字母、行是数字）',
                    '一格多大 src': [round(W / cols, 1), round(H / rows, 1)],
                    '提示': '看图找到目标在哪一格（格子左上角写着名字），然后放大：--cell C4（目标跨两格就框一片 --cell C4:D5）。'
                            '找四个角就对每个角各放大一次。不要凭感觉估数字，只选格子'}
        img.save(out)
        print(json.dumps(res, ensure_ascii=False)); return

    if a.at:
        levels, _ = _resolve(a.at, W, H, a.cols)
        s = levels[-1]['sel']; cw, ch = levels[-1]['cell']
        p = ((s[0] + s[2]) / 2, (s[1] + s[3]) / 2)
        pts, names, shape = [p], [a.at], 'point'
        err = max(s[2] - s[0], s[3] - s[1]) / 2
        res |= {'点 src': [round(p[0]), round(p[1])], '点 scene': _sc(*p, kx, ky)}
    else:
        parts = list(a.box or a.quad)
        need = 2 if a.box else 4
        if len(parts) == 1 and len([t for t in parts[0].split(':') if t]) == need:   # 老写法 A:B:C:D（点里没有框片）
            parts = [t for t in parts[0].split(':') if t]
        if len(parts) != need:
            raise SystemExit(f'{"--box" if a.box else "--quad"} 要 {need} 个点，空格隔开（每个点写到最细那层，如 C4.b3；点本身是一片的中心就写 C4.b3:c4）；现在是 {len(parts)} 个')
        got = [_pt(t, W, H, a.cols) for t in parts]
        pts = [g[0] for g in got]; names = parts
        err = max(max(g[1]) for g in got)
        if a.box:
            (x0, y0), (x1, y1) = pts
            x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
            pts = [(x0, y0), (x1, y1)]; shape = 'box'
            q = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            res |= {'box src': [round(x0), round(y0), round(x1), round(y1)], 'box scene': _sc(x0, y0, kx, ky) + _sc(x1, y1, kx, ky),
                    'quad src': [[round(x), round(y)] for x, y in q], 'quad scene': [_sc(x, y, kx, ky) for x, y in q]}
        else:
            order = _order_quad(list(zip(pts, names)))
            if [o[1] for o in order] != names:
                res['顺序'] = '已按 左上、右上、右下、左下 重排：' + ' → '.join(o[1] for o in order)
            pts = [o[0] for o in order]; names = [o[1] for o in order]; shape = 'quad'
            res |= {'quad src': [[round(x), round(y)] for x, y in pts], 'quad scene': [_sc(x, y, kx, ky) for x, y in pts]}
    out = a.out or f'{stem}_pick_check.png'
    _check_image(im, pts, names, shape, kx, ky, out)
    res |= {'check': out, '精度': f'约 ±{err * max(kx, ky):.0f} scene 像素（最后一层半格）',
            '提示': '打开回看图看下面的局部放大：十字要正压在目标上（角点就压在角上）。'
                    '偏了就回到那一层，换相邻的格子；差半格以上说明上一层选错了，回上一层重选。'
                    + ('写 scene.json 用 scene 那套；package.json、角色图里的位置用 src' if two else '')}
    print(json.dumps(res, ensure_ascii=False))
