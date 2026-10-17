"""scene.json → build/：底图、图形零件精灵、到达时间图、透视矩阵，写成 build/scene.js 给播放器用。"""
import json, os, shutil
import numpy as np
from PIL import Image, ImageDraw

from .surface import find_quad, quad_size, homography
from . import art as A
from .fonts import cjk_font as _cjk_font

PER_ITEM = {'pop', 'slam', 'type', 'flicker', 'bounce'}            # 逐个零件动
WHOLE = {'drop', 'grow', 'peek', 'peel', 'slide', 'fade', 'stamp', 'pixels', 'static', 'popin', 'splat', 'drip', 'melt', 'slidein', 'roll', 'flutter'}   # 整组一起动
FLOOD = {'draw', 'write', 'outline'}          # 沿笔画画出来
MOTIONS = PER_ITEM | WHOLE | FLOOD


def _base(cfg, sdir):
    im = Image.open(os.path.join(sdir, cfg['base'])).convert('RGB')
    if 'size' in cfg:
        W, H = cfg['size']
        im = im.resize((W, H), Image.LANCZOS)
    elif im.width > 1080:
        im = im.resize((1080, round(im.height * 1080 / im.width)), Image.LANCZOS)
    if im.width % 2 or im.height % 2:          # 视频编码要求宽高是偶数（横图缩到 1080 宽常会是奇数高）
        im = im.crop((0, 0, im.width - im.width % 2, im.height - im.height % 2))
    return im


def load_art(path, browser=None):
    if path.lower().endswith('.svg'):
        if browser is None:
            raise ValueError('SVG 需要浏览器来转成图片')
        return A.load_rgba(browser.rasterize_svg(path))
    return A.load_rgba(path)


def _rig_png(g, sdir, out, gi):
    """角色分层的另一种来源：几张对齐好的 PNG（比如 AI 出的几个关键姿势），每张当一个零件。
    parts 里写 {"png": "pose_a.png"}；也可以写 {"virtual": true} 当一个没图的父零件（整体转动用）。
    轴心写 pivot_frac: [x比例, y比例]（按图片宽高）。"""
    rig = g['rig']
    imgs, parts = {}, []
    size = None
    for pid, opt in rig['parts'].items():
        if opt.get('png'):
            im = Image.open(os.path.join(sdir, opt['png'])).convert('RGBA')
            if size is None:
                size = im.size
            elif im.size != size:
                im = im.resize(size, Image.LANCZOS)
            imgs[pid] = A.load_rgba(im)
    first = next(iter(imgs.values()))
    H, W = first.shape[:2]
    to_work = lambda f: [f[0] * (W - 12) + 6, f[1] * (H - 12) + 6]
    for pid, opt in rig['parts'].items():
        pv = to_work(opt.get('pivot_frac', [0.5, 0.5]))
        if pid not in imgs:          # 虚拟父零件 / 代码画的橡皮管腿（没有 png 的零件）
            p = dict(id=pid, png=None, x=0, y=0, w=1, h=1, pivot=pv, parent=opt.get('parent'), hidden=False, virtual=True)
            if 'hose' in opt:
                h = dict(opt['hose'])
                h['hips'] = [to_work(f) for f in h['hips']]
                h['ground'] = to_work([0, h.get('ground', 1.0)])[1]
                sc = (W - 12)                     # 长度类参数按图片宽度的比例写
                for k in ('stride', 'lift', 'width', 'shoe', 'bend'):
                    if k in h:
                        h[k] = h[k] * sc
                p['hose'] = h
            parts.append(p)
            continue
        im = imgs[pid]
        ys, xs = np.where(im[..., 3] > 30)
        x0, y0, x1, y1 = xs.min() - 2, ys.min() - 2, xs.max() + 3, ys.max() + 3
        Image.fromarray(np.clip(im[y0:y1, x0:x1], 0, 255).astype(np.uint8), 'RGBA').save(f'{out}/parts/rig{gi}_{pid}.png')
        parts.append(dict(id=pid, png=f'parts/rig{gi}_{pid}.png', x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0),
                          pivot=[round(pv[0], 1), round(pv[1], 1)], parent=opt.get('parent'), hidden=bool(opt.get('hidden'))))
    vis = [p for p in parts if not p['hidden'] and not p.get('virtual')]
    char = np.zeros_like(first)
    for p in vis:          # 一开始看得见的零件叠起来（画出来入场用）
        im = imgs[p['id']]; a = im[..., 3:4] / 255
        char[..., :3] = char[..., :3] * (1 - a) + im[..., :3] * a
        char[..., 3:4] = np.maximum(char[..., 3:4], im[..., 3:4])
    base = np.zeros_like(first)
    acts = [dict(a) for a in rig.get('acts', [])]
    return base, char, parts, acts


def _rig(g, sdir, browser, out, gi):
    """IP 角色分层：SVG 里命名的组逐个导出成零件，算好轴心；其余内容（字等）照常拆零件分组"""
    rig = g['rig']
    if not g.get('art', '').lower().endswith('.svg'):
        return _rig_png(g, sdir, out, gi)
    svg = os.path.join(sdir, g['art'])
    info = browser.svg_layers(svg)
    sc, (vx, vy, _, _) = info['scale'], info['viewBox']
    order = [l['id'] for l in info['layers']]
    pids = [p for p in order if p in rig['parts']]
    missing = [p for p in rig['parts'] if p not in order]
    if missing:
        raise ValueError(f'SVG 里找不到这些组：{missing}（用 mm layers 看有哪些 id）')
    to_work = lambda x, y: [(x - vx) * sc + 6, (y - vy) * sc + 6]
    base = A.load_rgba(browser.rasterize_svg_layers(svg, show=None, hide=pids))
    if rig.get('only_parts'):                 # 只画列出的图层（Codex 的约定：没列出的元素不保留）
        base[..., 3] = 0
    parts = []
    for pid in pids:
        opt = rig['parts'][pid]
        im = A.load_rgba(browser.rasterize_svg_layers(svg, show=[pid]))
        a = im[..., 3]
        ys, xs = np.where(a > 2)
        if not len(xs):
            continue
        x0, y0, x1, y1 = xs.min() - 2, ys.min() - 2, xs.max() + 3, ys.max() + 3
        Image.fromarray(np.clip(im[y0:y1, x0:x1], 0, 255).astype(np.uint8), 'RGBA').save(f'{out}/parts/rig{gi}_{pid}.png')
        pv = to_work(*opt['pivot']) if 'pivot' in opt else [float((x0 + x1) / 2), float((y0 + y1) / 2)]
        parts.append(dict(id=pid, png=f'parts/rig{gi}_{pid}.png', x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0),
                          pivot=[round(pv[0], 1), round(pv[1], 1)], parent=opt.get('parent'), hidden=bool(opt.get('hidden'))))
    vis = [p['id'] for p in parts if not p['hidden']]
    char = A.load_rgba(browser.rasterize_svg_layers(svg, show=vis))
    acts = []
    for act in rig.get('acts', []):
        act = dict(act)
        if act.get('do') == 'move':
            act['keys'] = [[k[0], k[1] * sc, k[2] * sc] for k in act['keys']]
        for key in ('height', 'rise', 'amp_px'):
            if key in act:
                act[key] = act[key] * sc
        acts.append(act)
    return base, char, parts, acts


def build(sdir, browser=None, log=print):
    cfg = json.load(open(os.path.join(sdir, 'scene.json')))
    out = os.path.join(sdir, 'build')
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(f'{out}/parts')
    base = _base(cfg, sdir)
    base.save(f'{out}/base.png')
    # 底图序列（翻页书 / 定格跑酷）：bases 里的每张都裁成和第一张一样大，播放时每 baseHold 帧换一张
    seq = []
    for k, b in enumerate(cfg.get('bases', [])):
        im = Image.open(os.path.join(sdir, b)).convert('RGB')
        s_ = max(base.width / im.width, base.height / im.height)
        im = im.resize((max(base.width, round(im.width * s_)), max(base.height, round(im.height * s_))), Image.LANCZOS)
        x0, y0 = (im.width - base.width) // 2, (im.height - base.height) // 2
        im.crop((x0, y0, x0 + base.width, y0 + base.height)).save(f'{out}/base_{k}.jpg', quality=92)
        seq.append(f'base_{k}.jpg')
    B = np.asarray(base).astype(np.float32)
    H, W = B.shape[:2]
    lum = B @ np.array([0.299, 0.587, 0.114], np.float32)

    graphics = []
    report = []
    for gi, g in enumerate(cfg['graphics']):
        # ---------- 表面 ----------
        surf = g.get('surface', {})
        if 'quad' in surf:
            quad = surf['quad']; srep = {'quad': quad, 'from': 'given'}
        else:
            quad, _, srep = find_quad(B, surf['seed'], surf.get('tol'))
        Sw, Sh = quad_size(quad)

        # ---------- 图形 ----------
        rig_parts = None
        if 'text' in g:                              # 纯文字：一段文案直接排出来
            rgba = A.load_rgba(browser.rasterize_text(g['text']))
        elif 'rig' in g:
            rgba, char_rgba, rig_parts, rig_acts = _rig(g, sdir, browser, out, gi)
        else:
            rgba = load_art(os.path.join(sdir, g['art']), browser)
        look = g.get('look', {})
        if look.get('color'):                       # 整体换色（比如深色 logo 放到黑板/霓虹上要变白、变亮色）
            hx = look['color'].lstrip('#')
            rgba[..., :3] = [int(hx[i:i + 2], 16) for i in (0, 2, 4)]
        aH, aW = rgba.shape[:2]
        lab, comps = A.components(rgba)
        groups = g.get('groups')
        auto = not groups
        if auto:
            groups = A.auto_groups(comps, aH, aW)
            # 自动分组后按组名改动法：{"graphic": {"motion": "drop"}, "title": {"motion": "slam"}}
            for grp in groups:
                grp.update(g.get('auto', {}).get(grp['name'], {}))
            # 没写配置的组（比如字里的 @ / . 这类没归进行的小符号）跟着第一个写了时间的组走，不然没有开始时间会报错
            ref = next((x for x in groups if 'at' in x), None)
            for grp in groups:
                if 'at' not in grp:
                    if ref:
                        grp.update({k: ref[k] for k in ('at', 'motion', 'step', 'stagger') if k in ref})
                    else:
                        grp['at'] = 0.3
        A.assign(comps, groups)

        out_groups = []
        for k, grp in enumerate(groups):
            cs = [c for c in comps if c['g'] == k]
            motion = grp.get('motion', 'pop')
            if motion not in MOTIONS:
                raise ValueError(f'不认识的动法 {motion}，可选：{sorted(MOTIONS)}')
            og = {kk: vv for kk, vv in grp.items() if kk != 'box'}
            og['motion'] = motion
            if not cs:
                log(f'  !! 图形{gi} 的组「{grp.get("name", k)}」没有零件，跳过')
                continue
            tag = f'g{gi}_{k}'
            if motion in PER_ITEM:
                items = []
                # 被另一块整个包住的小块（描边字的内芯、O 里面的点）算同一个字母，一起弹
                kids = {c['id']: [] for c in cs}
                top = []
                for c in cs:
                    host = next((p for p in cs if p is not c and (p['x1'] - p['x0']) * (p['y1'] - p['y0']) > (c['x1'] - c['x0']) * (c['y1'] - c['y0']) and p['x0'] - 2 <= c['x0'] and c['x1'] <= p['x1'] + 2
                                 and p['y0'] - 2 <= c['y0'] and c['y1'] <= p['y1'] + 2), None)
                    if host: kids[host['id']].append(c)
                    else: top.append(c)
                for li, line in enumerate(A.lines_of(top)):
                    for ci, c in enumerate(line):
                        im, info = A.crop_sprite(rgba, lab, [c] + kids[c['id']])
                        name = f'parts/{tag}_{li}_{ci}.png'; im.save(f'{out}/{name}')
                        info.update(png=name, line=li); items.append(info)
                og['items'] = items
            else:
                tm = A.time_map(rgba, lab, cs, motion, grp.get('seed', 'left')) if motion in FLOOD else None
                im, info = A.crop_sprite(rgba, lab, cs, pad=4)
                if tm and grp.get('tip'):
                    og['tipPath'] = A.front_path(tm)
                if tm:     # 精灵和时间图用同一个框
                    full = np.zeros((tm['h'], tm['w'], 4), np.uint8)
                    src = np.asarray(im)
                    ox, oy = info['x'] - tm['x'], info['y'] - tm['y']
                    sx0, sy0 = max(-ox, 0), max(-oy, 0)
                    dx0, dy0 = max(ox, 0), max(oy, 0)
                    hh = min(src.shape[0] - sy0, tm['h'] - dy0); ww = min(src.shape[1] - sx0, tm['w'] - dx0)
                    full[dy0:dy0 + hh, dx0:dx0 + ww] = src[sy0:sy0 + hh, sx0:sx0 + ww]
                    im = Image.fromarray(full, 'RGBA')
                    info.update(x=tm['x'], y=tm['y'], w=tm['w'], h=tm['h'])
                    og['flood'] = tm
                name = f'parts/{tag}.png'; im.save(f'{out}/{name}')
                info['png'] = name
                og['sprite'] = info
            og['n'] = len(cs)
            out_groups.append(og)

        if rig_parts:
            enter = dict(g['rig'].get('enter', {'motion': 'popin'}))
            em = enter.get('motion', 'popin')
            vis = [p for p in rig_parts if not p['hidden'] and not p.get('virtual')]
            bx0, by0 = min(p['x'] for p in vis), min(p['y'] for p in vis)
            bx1, by1 = max(p['x'] + p['w'] for p in vis), max(p['y'] + p['h'] for p in vis)
            m = int(0.35 * max(bx1 - bx0, by1 - by0))        # 留边：表演时零件会动出原来的范围
            cy_ = (by0 + by1) / 2
            og = dict(enter, name='rig', motion=em, rig=True, parts=rig_parts, acts=rig_acts, n=len(vis),
                      sprite=dict(x=int(bx0 - m), y=int(by0 - m), w=int(bx1 - bx0 + 2 * m), h=int(by1 - by0 + 2 * m),
                                  cx=float((bx0 + bx1) / 2), cy=float(cy_), bottom=int(by1)),
                      body=[int(bx0), int(by0), int(bx1), int(by1)])
            rig = g['rig']
            if rig.get('travel'):        # 角色要飞过整个画面：画布用整张图，不按角色范围裁
                cH, cW = char_rgba.shape[:2]
                og['sprite'] = dict(og['sprite'], x=0, y=0, w=int(cW), h=int(cH))
            if rig.get('shadow'):        # 接触影子：地面线（图片高度比例）、影子宽（图片宽度比例）
                sh = dict(rig['shadow']); cH, cW = char_rgba.shape[:2]
                og['shadow'] = dict(ground=sh.get('ground', 0.9) * (cH - 12) + 6, size=sh.get('size', 0.08) * (cW - 12),
                                    opacity=sh.get('opacity', 0.5), lift=sh.get('lift', 0.25) * (cH - 12), color=sh.get('color', '#28190f'))
            if em in FLOOD:
                lab2, comps2 = A.components(char_rgba)
                og['flood'] = A.time_map(char_rgba, lab2, comps2, em, enter.get('seed', 'top'))
            out_groups.insert(0, og) if enter.get('first', True) else out_groups.append(og)

        # ---------- 摆放：按图形的「实际内容范围」缩放和定位（AI 图四周的空白不算） ----------
        if rig_parts:
            vis = [p for p in rig_parts if not p['hidden'] and not p.get('virtual')]
            cb = [min(p['x'] for p in vis), min(p['y'] for p in vis), max(p['x'] + p['w'] for p in vis), max(p['y'] + p['h'] for p in vis)]
            for p in rig_parts:
                if p.get('hose'):
                    hh = p['hose']; ext = hh.get('stride', 40) + hh.get('shoe', 40)
                    cb = [min(cb[0], min(h[0] for h in hh['hips']) - ext), cb[1], max(cb[2], max(h[0] for h in hh['hips']) + ext), max(cb[3], hh['ground'])]
        else:
            ys_, xs_ = np.where(rgba[..., 3] > 8)
            cb = [int(xs_.min()), int(ys_.min()), int(xs_.max()) + 1, int(ys_.max()) + 1] if len(xs_) else [0, 0, aW, aH]
        pl = g.get('place', {})
        if pl.get('fit') == 'art':                 # 按整张图缩放：几个角色从同一张图切出来时，用同样的 place 就能保持原来的相对位置
            cb = [0, 0, aW, aH]
        cw_, ch_ = max(cb[2] - cb[0], 1), max(cb[3] - cb[1], 1)
        fw, fh = pl.get('width', 0.78), pl.get('height', 0.8)
        s = min(fw * Sw / cw_, fh * Sh / ch_)
        ax_, ay_ = pl.get('anchor', [0.5, 0.5])
        ox, oy = pl.get('offset', [0, 0])                  # 以表面宽高为单位的偏移
        ax = ax_ * Sw - s * (cb[0] + cw_ / 2) + ox * Sw
        ay = ay_ * Sh - s * (cb[1] + ch_ / 2) + oy * Sh
        # 平面画布：表面摊平后再往外留边（掉落要从表面上方进来）
        mx, mt, mb = 0.15 * Sw, 1.0 * Sh, 0.2 * Sh
        r = min(1.5, 2400 / max(Sw + 2 * mx, Sh + mt + mb))
        PW, PH = int((Sw + 2 * mx) * r), int((Sh + mt + mb) * r)
        T = np.array([[1 / r, 0, -mx], [0, 1 / r, -mt], [0, 0, 1]])           # 平面像素 → 摊平坐标
        Hf = homography([[0, 0], [Sw, 0], [Sw, Sh], [0, Sh]], quad)            # 摊平 → 底图
        Hp = Hf @ T
        Hinv = np.linalg.inv(Hp)
        Hinv /= Hinv[2, 2]
        art2plane = [r * s, r * (ax + mx), r * (ay + mt)]                     # 缩放, 平移x, 平移y

        # ---------- 遮罩：圆窗、拱门、任意形状（底图坐标），图形只在遮罩里显示 ----------
        mask_png = None
        if g.get('mask'):
            mk = g['mask']
            m = Image.new('L', (W, H), 0); dm = ImageDraw.Draw(m)
            if 'ellipse' in mk:
                cx, cy, rx, ry = mk['ellipse']; dm.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
            elif 'rect' in mk:
                dm.rounded_rectangle(mk['rect'], radius=mk.get('radius', 0), fill=255)
            elif 'polygon' in mk:
                dm.polygon([tuple(p) for p in mk['polygon']], fill=255)
            elif 'image' in mk:
                m = Image.open(os.path.join(sdir, mk['image'])).convert('L').resize((W, H), Image.LANCZOS)
            if mk.get('invert'):
                from PIL import ImageOps
                m = ImageOps.invert(m)
            from PIL import ImageFilter
            m = m.filter(ImageFilter.GaussianBlur(mk.get('feather', 1.2)))
            mask_png = f'mask_{gi}.png'; m.save(f'{out}/{mask_png}')
        carrier = g.get('carrier')
        if carrier:                                   # 载体联动：比如卷帘门拉下来，门上的图跟着门走
            op = Image.open(os.path.join(sdir, carrier['open'])).convert('RGB').resize((W, H), Image.LANCZOS)
            op.save(f'{out}/open_{gi}.png')
            carrier = dict(carrier, open=f'open_{gi}.png')
        accents = []
        for ac in g.get('accents', []):
            ac = dict(ac)
            if 'group' in ac:
                if ac['group'] == 'rig' and rig_parts:
                    vis = [p for p in rig_parts if not p['hidden'] and not p.get('virtual')]
                    bb = [min(p['x'] for p in vis), min(p['y'] for p in vis), max(p['x'] + p['w'] for p in vis), max(p['y'] + p['h'] for p in vis)]
                else:
                    k = next((i for i, gg in enumerate(groups) if gg.get('name') == ac['group']), None)
                    cs = [c for c in comps if c['g'] == k]
                    bb = [min(c['x0'] for c in cs), min(c['y0'] for c in cs), max(c['x1'] for c in cs), max(c['y1'] for c in cs)] if cs else None
                if bb:
                    x0, y0, x1, y1 = bb
                    ac.setdefault('box', [int(x0), int(y0), int(x1), int(y1)])
                    fx, fy = ac.get('anchor', [0.5, 0.5])          # 放在组的哪里：[0.5, 0] = 正上方
                    ac.setdefault('pos', [float(x0 + fx * (x1 - x0)), float(y0 + fy * (y1 - y0))])
            ac.setdefault('box', [0, 0, aW, aH])
            ac.setdefault('pos', [aW / 2, aH / 2])
            accents.append(ac)
        a_ = rgba[..., 3] > 200
        ink = [int(v) for v in np.median(rgba[a_][:, :3], 0)] if a_.any() else [30, 30, 30]

        poly = np.array(quad)
        from skimage.draw import polygon as pf
        rr, cc = pf(poly[:, 1], poly[:, 0], (H, W))
        mean_lum = float(lum[rr, cc].mean()) if len(rr) else 128.0
        graphics.append(dict(
            plane=[PW, PH], hinv=[float(v) for v in Hinv.T.ravel()],     # 列主序给 WebGL
            art=[aW, aH], art2plane=art2plane, quad=quad, meanLum=mean_lum,
            shade=look.get('shade', 0.8), blend=look.get('blend', 'normal'),
            soften=look.get('soften', 0.6), opacity=look.get('opacity', 1.0),
            style=look.get('style', 'print'), look=look, ink=ink, accents=accents,
            h=[float(v) for v in (Hp / Hp[2, 2]).T.ravel()], carrier=carrier, mask=mask_png,
            surfRect=[mx * r, mt * r, Sw * r, Sh * r],
            groups=out_groups))
        report.append(dict(graphic=gi, surface=srep, art_size=[aW, aH], auto_groups=auto,
                           groups=[dict(name=gg.get('name'), motion=gg['motion'], parts=gg['n']) for gg in out_groups]))
        _check_art(rgba, lab, comps, groups, f'{out}/check_art_{gi}.png')

    # 摆放自检图：每块图形的表面四边形画回底图上 —— 出片前必须看一眼（框有没有跨过窗框、有没有跟着透视）
    chk = base.copy(); dr = ImageDraw.Draw(chk)
    cols = [(255, 0, 200), (0, 200, 255), (255, 200, 0), (0, 230, 120)]
    fsz = max(20, W // 36); font = _cjk_font(fsz)            # 标签要看得清：默认字体太小、也写不了中文
    for gi, G in enumerate(graphics):
        q = [tuple(p) for p in G['quad']]
        dr.line(q + [q[0]], fill=cols[gi % 4], width=4)
        dr.text((q[0][0] + 8, q[0][1] + 6), f'图形{gi}' if font else f'graphic {gi}', fill=cols[gi % 4], stroke_width=2, stroke_fill=(0, 0, 0),
                **({'font': font} if font else {'font_size': fsz}))
    for gi, gcfg in enumerate(cfg['graphics']):          # 遮罩也画出来
        if gcfg.get('mask') and os.path.exists(f'{out}/mask_{gi}.png'):
            mm_ = Image.open(f'{out}/mask_{gi}.png').point(lambda v: 255 if v > 128 else 0)
            edge = mm_.filter(__import__('PIL.ImageFilter', fromlist=['FIND_EDGES']).FIND_EDGES)
            chk.paste((255, 60, 60), (0, 0), edge)
    chk.save(f'{out}/check_place.png')

    scene = dict(W=W, H=H, duration=cfg.get('duration', 5), fps=cfg.get('fps', 30), sound=cfg.get('sound', 'minimal'),
                 camera=cfg.get('camera', {'push': 0.04}), grain=cfg.get('grain', 4),
                 layout=cfg.get('layout', {}), graphics=graphics,
                 bases=seq, baseHold=cfg.get('baseHold', 6))
    with open(f'{out}/scene.js', 'w') as f:
        f.write('window.SCENE = ' + json.dumps(scene) + ';\n')
    with open(f'{out}/report.json', 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def _check_art(rgba, lab, comps, groups, path):
    """自检图：每组一种颜色，框和组名画上去，没归组的灰色"""
    cols = [(230, 60, 60), (40, 120, 230), (40, 170, 80), (240, 150, 0), (170, 70, 220), (0, 170, 170)]
    H, W = lab.shape
    img = np.full((H, W, 3), 245, np.float32)
    a = rgba[..., 3:4] / 255
    for c in comps:
        col = np.array(cols[c['g'] % 6] if c['g'] is not None else (150, 150, 150), np.float32)
        m = lab == c['id']
        img[m] = img[m] * (1 - a[m]) + col * a[m]
    im = Image.fromarray(img.astype(np.uint8))
    d = ImageDraw.Draw(im)
    for k, g in enumerate(groups):
        b = g.get('box')
        if isinstance(b, list):
            d.rectangle(b, outline=cols[k % 6], width=2)
            d.text((b[0] + 3, b[1] + 2), f"{g.get('name', k)}:{g.get('motion', '?')}", fill=cols[k % 6])
    for x in range(0, W, 100):
        d.text((x + 2, H - 12), str(x), fill=(120, 120, 120))
    for y in range(0, H, 100):
        d.text((2, y + 2), str(y), fill=(120, 120, 120))
    im.save(path)
