# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow", "scipy", "scikit-image", "playwright", "imageio-ffmpeg", "opencv-python-headless"]
# ///
"""mockup-motion 命令行。用法（推荐用 uv，会自动装依赖）：uv run mm.py <命令> …

新建
  new <模板> <场景文件夹>                 从模板新建：拷配置，列出要准备的素材和下一步（模板名在 assets/templates、assets/packages）
素材
  palette <底图>                          从底图取色：角色和字只用这几种颜色
  hold 拿着.jpg 空手.jpg                  手里拿着的产品：算位置和压在前面的手指遮罩
  decal 白图.jpg …                        黑底白图转成透明贴纸（窗贴、字标），自动裁掉四周空白
  cutout 角色表.png --names a,b,c         灰底角色表切成一个个透明 PNG
  split <文件夹> a.jpg b.jpg --names l,r   几个角色画在一张图里 → 沿空隙切开
  frame <高清原图> --box x0,y0,x1,y1 --out base.png   取景：裁一个更近的镜头
街景（street-crowd）
  plate 视频.mp4 --out 文件夹/ [--dur 秒] [--start-sec 秒]   真人走动底片 → 逐帧图、base.jpg、天空遮罩、行人遮罩
  sky <街景>                              静态街景的天空遮罩
  occluder 空街景 去近景图 --target base.jpg   近处的楼、树、灯杆 → 遮挡层 near.png
量坐标
  grid <图片> [--box x0,y0,x1,y1] [--keep] 叠坐标网格。两套刻度：src=原图像素，scene=scene.json 用的坐标
  surface <底图> --seed X,Y               从一个点自动找表面四个角
  layers <图形.svg>                       列出 SVG 里带 id 的组（定轴心用）
出片
  build <场景文件夹>                      读 scene.json，拆零件、算透视，出自检图（scribble-pop 会先跑 make_scene.py）
  preview <场景文件夹> [--t 0.5,1,2]      定帧联系表
  render <场景文件夹> [--layout both]     出 MP4（pure=纯成片，compare=竖版对照，both=都出）
  package <文件夹>                        包装镜头（color-panels、street-crowd），读 package.json 出 MP4
  frames 成片.mp4 --t 1,2,3               从视频抽帧自检
  measure 参考片.mp4 [--box x0,y0,x1,y1 --at 秒 | --track X,Y,秒] [--span 起,止]   量参考片：切点、每镜联系表、镜头运动、跟踪+缓动拟合
  beats 音乐 [--cuts "bar:3,drop:1"] [--where 3.16,5.2]   分析音乐：BPM、小节、高潮、突停；节拍写法换算成秒
  pick 图 [--cell C4 | --at C4.b3 | --box P1 P2 | --quad P1 P2 P3 P4]   选格子定位：网格上选格子、放大再选，出两套坐标 + 回看图
  concat <输出.mp4> <a.mp4> <b.mp4> …     几段硬切接成一条
  import-codex <config.json> <文件夹>     把 Codex image-logo-motion 的配置转过来
"""
import argparse, json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _cjk_font(size):
    from mmlib.fonts import cjk_font
    return cjk_font(size)


def _q(p):
    """拼进「下一步」命令里的路径：带空格或特殊符号才加引号（照抄进终端不会被拆成两半），普通中文路径原样"""
    import re, shlex
    p = str(p)
    return shlex.quote(p) if not p or re.search(r'[^\w@%+=:,./-]', p) else p


def _probe(path):
    """用 ffmpeg 看一眼视频：帧率 fps、时长 dur（秒）、尺寸 size、有没有音轨 audio。读不出来的项是 None"""
    import re
    from mmlib.browser import ffmpeg_exe
    err = subprocess.run([ffmpeg_exe(), '-hide_banner', '-i', path], capture_output=True, text=True).stderr
    m = re.search(r'Duration: (\d+):(\d+):([\d.]+)', err)
    vl = re.search(r'Stream #[^\n]*Video:[^\n]*', err); vl = vl[0] if vl else ''
    sz = re.search(r'(\d{2,5})x(\d{2,5})', vl)
    fp = re.search(r'([\d.]+) fps', vl) or re.search(r'([\d.]+) tbr', vl)
    return dict(dur=int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None, fps=float(fp[1]) if fp and float(fp[1]) > 0 else None,
                size=(int(sz[1]), int(sz[2])) if sz else None, audio=' Audio:' in err, video=bool(vl), err=err)


def _shift(arr, dy, dx, edge=False):
    """整体平移 (dy, dx) 像素。挪出去的那条不会从对边绕回来（np.roll 会）：空出来的边 edge=True 用边缘像素补，否则补 0 / False"""
    import numpy as np
    H, W = arr.shape[:2]
    pad = [(max(dy, 0), max(-dy, 0)), (max(dx, 0), max(-dx, 0))] + [(0, 0)] * (arr.ndim - 2)
    p = np.pad(arr, pad, mode='edge' if edge else 'constant')
    y0, x0 = max(0, -dy), max(0, -dx)
    return p[y0:y0 + H, x0:x0 + W]


def _scene_scale(path, W, H):
    """这张图在 scene.json 里的坐标 = 原图像素 × (kx, ky)，和 build 缩底图的规则一样：
    同文件夹有 package.json → 不缩（package.json 用原图像素）；这张图是 scene.json / params.json 的 base 且写了 size → 按 size；
    其余 → 宽过 1080 就缩到 1080 宽"""
    d, bn = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    if os.path.exists(os.path.join(d, 'package.json')):
        return 1.0, 1.0, '同文件夹有 package.json：package.json 用原图像素，不缩', '不缩'
    other = ''
    for f in ('scene.json', 'params.json'):
        try: cfg = json.load(open(os.path.join(d, f), encoding='utf-8'))
        except Exception: continue
        if not isinstance(cfg, dict): continue
        if cfg.get('base') == bn or bn in (cfg.get('bases') or []):
            if cfg.get('size'):
                sw, sh = cfg['size']; return sw / W, sh / H, f'{f} 写了 size {sw}×{sh}，底图按它缩放', f'按 {f} 的 size {sw}×{sh}'
        elif cfg.get('base') and not other:
            other = f'；注意这张不是 {f} 里的底图 {cfg["base"]}：是角色 / 图形图就只看 src'
    if W > 1080:
        return 1080 / W, 1080 / W, '底图缩到 1080 宽（scene.json 没写 size 时 build 就这么缩）' + other, '缩到 1080 宽'
    return 1.0, 1.0, '不到 1080 宽，不缩', '不缩'


def _nice_step(x):
    c = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000]
    import math
    return min(c, key=lambda v: abs(math.log(v) - math.log(max(x, 1))))


def cmd_grid(a):
    """坐标网格，两套刻度：
    src   = 原图像素：角色图里量位置（眼睛）、package.json 用这套；
    scene = scene.json 用的坐标（底图缩到 1080 宽，或按 scene.json / params.json 的 size）。
    默认：图缩成 scene 尺寸，实线标 scene，黄色虚线标 src；--keep：图不缩，实线标 src，虚线标 scene；
    --box x0,y0,x1,y1（原图像素）：只看这块并放大，实线标 src，虚线标 scene。两套一样时只画一套"""
    from PIL import Image, ImageDraw
    src = Image.open(a.image)
    if src.mode in ('RGBA', 'LA', 'P'):                # 透明图铺中灰，不然透明区是满屏噪点
        src = src.convert('RGBA'); bg = Image.new('RGBA', src.size, (128, 128, 128, 255)); bg.alpha_composite(src); src = bg
    im = src.convert('RGB'); W, H = im.size
    kx, ky, why, short = _scene_scale(a.image, W, H)
    two = abs(kx - 1) > 0.002 or abs(ky - 1) > 0.002
    ox = oy = 0; zx = zy = 1.0; box = None
    if a.box:                                          # 放大看一块：box 按原图像素写
        try: x0, y0, x1, y1 = [int(round(float(v))) for v in a.box.split(',')]
        except ValueError: raise SystemExit('--box 要写成 x0,y0,x1,y1（原图像素，四个数用逗号隔开）')
        x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
        if x1 - x0 < 4 or y1 - y0 < 4: raise SystemExit('--box 太小了')
        box = [x0, y0, x1, y1]
        im = im.crop((x0, y0, x1, y1)); ox, oy = x0, y0; zx = zy = 1200 / max(im.width, im.height)
        im = im.resize((round(im.width * zx), round(im.height * zy)), Image.LANCZOS)
        primary = 'src'; st = _nice_step(max(x1 - x0, y1 - y0) / 10 * a.step / 100)
    elif a.keep or not two:
        primary = 'src'; st = max(1, a.step)
    else:                                              # 默认：图缩成 scene 尺寸
        zx, zy = kx, ky; im = im.resize((round(W * kx), round(H * ky)), Image.LANCZOS)
        primary = 'scene'; st = max(1, a.step)
    d = ImageDraw.Draw(im)
    fs = max(12, im.width // 90)
    f_of = {'src': (1.0, 1.0), 'scene': (kx, ky)}       # 原图像素 → 这套坐标 的倍数
    second = ('scene' if primary == 'src' else 'src') if two else None
    st2 = _nice_step(st * f_of[second][0] / f_of[primary][0]) if second else None

    def axis(step, n_view, o, z, f):
        """这套坐标在画面上的刻度：[(画面位置, 坐标值)]"""
        res = []
        for v in range(0, 100000, step):
            p = (v / f - o) * z
            if p > n_view: break
            if p >= 0: res.append((p, v))
        return res
    for p, v in axis(st, im.width, ox, zx, f_of[primary][0]):          # 实线：标在上边、左边
        d.line([(p, 0), (p, im.height)], fill=(255, 0, 255), width=1); d.text((p + 3, 3), str(v), fill=(255, 0, 255), font_size=fs, stroke_width=1, stroke_fill=(0, 0, 0))
    for p, v in axis(st, im.height, oy, zy, f_of[primary][1]):
        d.line([(0, p), (im.width, p)], fill=(0, 190, 255), width=1); d.text((3, p + 3), str(v), fill=(0, 190, 255), font_size=fs, stroke_width=1, stroke_fill=(0, 0, 0))
    if second:                                         # 黄色虚线：标在下边、右边
        yl = (255, 215, 0)
        for p, v in axis(st2, im.width, ox, zx, f_of[second][0]):
            for q in range(0, im.height, 12): d.line([(p, q), (p, min(q + 6, im.height))], fill=yl, width=1)
            d.text((p + 3, im.height - fs - 6), str(v), fill=yl, font_size=fs, stroke_width=2, stroke_fill=(0, 0, 0))
        for p, v in axis(st2, im.height, oy, zy, f_of[second][1]):
            for q in range(0, im.width, 12): d.line([(q, p), (min(q + 6, im.width), p)], fill=yl, width=1)
            tw = d.textlength(str(v), font_size=fs)
            d.text((im.width - tw - 6, p + 3), str(v), fill=yl, font_size=fs, stroke_width=2, stroke_fill=(0, 0, 0))
    name = {'src': ('原图像素 src（角色图里量位置、package.json 用）', 'SRC = original image pixels (character PNG, package.json)'),
            'scene': (f'scene 坐标（scene.json 用，{short}）', 'SCENE = scene.json coords (base scaled like mm build)')}
    if second:                                         # 图例：哪套是哪套
        font = _cjk_font(max(14, fs + 2)); zh = font is not None
        lines = [f'实线（数字在上、左）= {name[primary][0]}' if zh else f'solid, labels top/left: {name[primary][1]}',
                 f'黄色虚线（数字在下、右）= {name[second][0]}' if zh else f'dotted yellow, labels bottom/right: {name[second][1]}']
        kw = {'font': font} if zh else {'font_size': max(14, fs + 2)}
        lh = max(14, fs + 2) + 8; wmax = max(d.textlength(t, **kw) for t in lines)
        y0_ = im.height - fs - 14 - lh * len(lines) - 6
        d.rectangle([8, y0_ - 4, 8 + wmax + 16, y0_ + lh * len(lines) + 2], fill=(0, 0, 0))
        for i, t in enumerate(lines):
            d.text((16, y0_ + i * lh), t, fill=(255, 0, 255) if i == 0 else (255, 215, 0), **kw)
    out = a.out or os.path.splitext(a.image)[0] + ('_zoom.png' if a.box else '_grid.png')
    im.save(out)
    sysdesc = {'src': '原图像素 src', 'scene': 'scene 坐标'}
    res = {'grid': out, 'size': list(im.size), '原图尺寸': [W, H],
           '刻度': {'实线（数字在上、左）': sysdesc[primary] + f'，每 {st}'} | ({'黄色虚线（数字在下、右）': sysdesc[second] + f'，每 {st2}'} if second else {}),
           'scene 换算': (f'scene = 原图像素 × {kx:.4f}' + (f'（y × {ky:.4f}）' if abs(kx - ky) > 0.002 else '') + f'；{why}') if two else f'scene 坐标 = 原图像素（{why}），只有一套刻度'}
    if second:
        res['用哪套'] = '写 scene.json（quad / seed / focus）、params.json 的坐标用 scene；量角色图里的位置（眼睛）、写 package.json 用 src'
    if box:
        res['box'] = {'src': box} | ({'scene': [round(box[0] * kx), round(box[1] * ky), round(box[2] * kx), round(box[3] * ky)]} if second else {})
    print(json.dumps(res, ensure_ascii=False))


def cmd_surface(a):
    import numpy as np
    from PIL import Image, ImageDraw
    from mmlib.surface import find_quad
    im = Image.open(a.image).convert('RGB')
    if im.width > 1080:
        im = im.resize((1080, round(im.height * 1080 / im.width)), Image.LANCZOS)
    B = np.asarray(im).astype(np.float32)
    seed = [int(v) for v in a.seed.split(',')]
    quad, mask, rep = find_quad(B, seed, a.tol)
    d = ImageDraw.Draw(im)
    d.polygon([tuple(p) for p in quad], outline=(255, 0, 255), width=4)
    d.ellipse([seed[0] - 8, seed[1] - 8, seed[0] + 8, seed[1] + 8], fill=(0, 200, 255))
    out = os.path.splitext(a.image)[0] + '_surface.png'
    im.save(out)
    rep['check'] = out
    print(json.dumps(rep, ensure_ascii=False))


def _session(build_dir, layout_size=(1080, 1920)):
    from mmlib.browser import Session
    return Session(build_dir, *layout_size)


def _regen_scene(sdir):
    """生成器模板（scribble-pop）：文件夹里有 make_scene.py + params.json 时，scene.json 不存在、
    或者比 params.json / make_scene.py / 姿势图旧，就先跑 make_scene.py 重新生成。返回它的输出（没跑返回 None）"""
    ms, pj, sj = (os.path.join(sdir, f) for f in ('make_scene.py', 'params.json', 'scene.json'))
    if not (os.path.exists(ms) and os.path.exists(pj)):
        return None
    srcs = [ms, pj]
    try: srcs += [os.path.join(sdir, f) for f in json.load(open(pj, encoding='utf-8')).get('poses', {}).values()]
    except Exception: pass
    if os.path.exists(sj) and os.path.getmtime(sj) >= max(os.path.getmtime(f) for f in srcs if os.path.exists(f)):
        return None
    r = subprocess.run([sys.executable, ms, os.path.abspath(sdir)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('make_scene.py 没跑通：' + ((r.stderr or r.stdout).strip().splitlines() or ['（没有输出）'])[-1])
    return r.stdout.strip()


def cmd_build(a):
    from mmlib.build import build
    gen = _regen_scene(a.scene)
    if not os.path.exists(os.path.join(a.scene, 'scene.json')):
        raise SystemExit(f'{a.scene} 里没有 scene.json：先 mm new <模板> {_q(a.scene)}')
    sc = json.load(open(os.path.join(a.scene, 'scene.json'), encoding='utf-8'))      # 素材没齐、TODO 没填：先说清楚，别等报一串英文错
    miss = [f for f in _image_refs(sc) if not os.path.exists(os.path.join(a.scene, f))]
    todo = [f'{path}（{msg}）' for path, msg in _todos(sc)]
    if miss or todo:
        raise SystemExit('还不能 build：' + (f'\n  缺图：{"、".join(miss)}' if miss else '') + ''.join(f'\n  还没填：{t}' for t in todo))
    with _session(None) as s:          # SVG、纯文字、角色分层都要用浏览器来排版/栅格化
        rep = build(a.scene, browser=s)
    with _session(os.path.join(a.scene, 'build')) as s:
        s.open_player()
        err = s.page.evaluate('window.__error || null')
        if err or s.errors:                 # 播放器初始化就出错了：直接把原因报出来，别等到算时间轴才莫名其妙地崩
            raise SystemExit(f'播放器报错：{err or ""} {s.errors}')
        tl = s.page.evaluate('window.__timeline()')
        dur = s.page.evaluate('window.SCENE.duration')
    warns = []
    ends = [g['end'] for G in tl for g in G]
    if ends and max(ends) > dur - 1.0:
        warns.append(f'最后一个动作 {max(ends):.2f} 秒才结束，离片尾不到 1 秒：落款停不够，看不清。把 duration 加长，或者让动作早点结束')
    for f in _image_refs(sc):                          # mm decal 没写 --out、输入又是 png 时会另存成 xxx_decal.png：scene.json 还用着没转的原图
        stem = os.path.splitext(f)[0]; dec = os.path.join(a.scene, stem + '_decal.png')
        if not stem.endswith('_decal') and os.path.exists(dec) and os.path.getmtime(dec) >= os.path.getmtime(os.path.join(a.scene, f)):
            warns.append(f'旁边有 {stem}_decal.png（mm decal 转好的透明版），可 scene.json 用的还是没转的 {f}：要用转好的，就把 scene.json 里的 "{f}" 改成 "{stem}_decal.png"；'
                         f'或者把原图改个名（比如 {stem}_原图.png），再 mm decal {_q(os.path.join(a.scene, stem + "_原图.png"))} --out {_q(os.path.join(a.scene, f))}（--color 照原来的写）')
    res = {'report': rep, 'timeline': tl, '提醒': warns,
           'check': sorted(f for f in os.listdir(os.path.join(a.scene, 'build')) if f.startswith('check'))}
    if gen is not None:
        res['make_scene'] = '按 params.json 重新生成了 scene.json（手改 scene.json 的内容会被盖掉，位置只改 params.json）：' + gen
    print(json.dumps(res, ensure_ascii=False, indent=1))


def cmd_frame(a):
    """取景：从高清原图里按表面裁一个更近的镜头（不是放大小图，所以不糊）"""
    from PIL import Image
    im = Image.open(a.image).convert('RGB')
    W, H = [int(v) for v in a.size.split('x')]
    x0, y0, x1, y1 = [float(v) for v in a.box.split(',')]
    if a.coords == 'out':                    # box 按 1080 宽的网格图坐标写
        k = im.width / 1080
        top = (im.height - round(im.height / k)) / 2 * 0
        x0, y0, x1, y1 = x0 * k, y0 * k, x1 * k, y1 * k
    cx, cy, bw, bh = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    asp = W / H
    if bw / bh > asp: bh = bw / asp
    else: bw = bh * asp
    bw, bh = min(bw, im.width, im.height * asp), min(bh, im.height, im.width / asp)
    cx = min(max(cx, bw / 2), im.width - bw / 2); cy = min(max(cy, bh / 2), im.height - bh / 2)
    box = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
    im.crop(tuple(round(v) for v in box)).resize((W, H), Image.LANCZOS).save(a.out)
    print(json.dumps({'out': a.out, 'crop': [round(v) for v in box], 'zoom': round(im.width / bw, 2)}, ensure_ascii=False))


_IMG_EXT = ('.png', '.jpg', '.jpeg', '.svg', '.webp')


def _image_refs(obj):
    """配置里写到的图片文件名（按出现顺序，去重；TODO 说明不算）"""
    out = []
    def walk(o):
        if isinstance(o, dict): [walk(v) for v in o.values()]
        elif isinstance(o, list): [walk(v) for v in o]
        elif isinstance(o, str) and o.lower().endswith(_IMG_EXT) and not o.startswith('TODO') and o not in out: out.append(o)
    walk(obj)
    return out


def _todos(obj, path=''):
    """配置里还没填的 TODO：[(字段路径, 说明)]"""
    res = []
    if isinstance(obj, dict):
        for k, v in obj.items(): res += _todos(v, f'{path}.{k}' if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj): res += _todos(v, f'{path}[{i}]')
    elif isinstance(obj, str) and obj.startswith('TODO'):
        res.append((path, obj[4:].lstrip(' _：:')))
    return res


def _scene_hints(sc, d, bundled=()):
    """按 scene.json 里实际有什么，写「下一步」"""
    steps, gs = [], sc.get('graphics', [])
    for i, g in enumerate(gs):
        rig = g.get('rig') or {}
        pngs = [p['png'] for p in rig.get('parts', {}).values() if isinstance(p, dict) and p.get('png')]
        if 'text' in g:
            steps.append(f'图形{i} 是系统字：改 text.lines（现在是 {json.dumps(g["text"].get("lines", []), ensure_ascii=False)}）；有自己的字标图就删掉 text，改写 "art": "字标.png"')
        elif pngs and all(f in bundled for f in pngs):
            steps.append(f'图形{i} 用模板自带的 {"、".join(pngs)}，不用准备')
        elif len(pngs) > 1:
            steps.append(f'图形{i} 是角色，要 {len(pngs)} 张姿势图：{"、".join(pngs)}（同一个角色、画布一样大、站在同一个位置；灰底或透明）')
        elif pngs:
            steps.append(f'图形{i} 是会动的图：{pngs[0]}（灰底或透明）')
        elif g.get('art'):
            art = g['art']; low = art.lower()
            kind = '字标 / logo' if any(w in low for w in ('logo', 'word', 'text', 'badge', 'title', 'sign')) else '图形'
            raw, col = ('字标原图.png', '招牌对比色') if kind != '图形' else ('原图.png', '颜色')
            if low.endswith(('.png', '.webp')):            # --out 直接写成 scene.json 要的文件名：不写的话输入是 png 会另存成 xxx_decal.png，build 还读没转的那张
                dec = f'；黑底白字的先转透明：mm decal {raw} --color {col} --out {_q(os.path.join(d, art))}（{col}写成带引号的色号，如 \'#ffcc00\'；原图别和 {art} 同名）'
            elif low.endswith(('.jpg', '.jpeg')):
                png = os.path.splitext(art)[0] + '.png'
                dec = f'；黑底白字的先转透明：mm decal {raw} --color {col} --out {_q(os.path.join(d, png))}，再把 scene.json 里的 "{art}" 改成 "{png}"（{col}写成带引号的色号，如 \'#ffcc00\'）'
            else:
                dec = ''
            steps.append(f'图形{i} 是{kind}：放你的 {art}（原样用，不让 AI 重画）{dec}')
        if isinstance(g.get('carrier'), dict) and g['carrier'].get('open'):
            steps.append(f'图形{i} 跟着载体（卷帘门等）走：还要一张同机位、门打开的底图 {g["carrier"]["open"]}')
    if sc.get('bases'):
        steps.append(f'翻页底图 {len(sc["bases"])} 张：{"、".join(sc["bases"])}（同一机位，每张一个场景）')
    by_msg = {}                                        # 同一句 TODO 的几块图形合成一条
    for path, msg in _todos(sc):
        if path.startswith('graphics['):
            gi = int(path[9:path.index(']')]); field = path[path.index(']') + 2:]
            by_msg.setdefault((field, msg), []).append(gi)
        else:
            by_msg.setdefault((path, msg), []).append(None)
    base = os.path.join(d, sc.get('base', 'base.png'))
    for (field, msg), gis in by_msg.items():
        gis = [g for g in gis if g is not None]
        who = ('全部图形' if len(gis) == len(gs) and len(gs) > 1 else '图形' + '、'.join(map(str, gis))) if gis else ''
        if field.startswith('surface'):
            steps.append(f'{who}的 surface：{msg}。坐标看 mm grid {_q(base)} 的实线（scene 坐标），或 mm surface {_q(base)} --seed X,Y 自动找四角')
        else:
            steps.append(f'{who + "的 " if who else ""}{field} 还没填：{msg}' + ('。拿在手里的东西：mm hold 拿着.jpg 空手.jpg 会算出手指遮罩' if field == 'mask.image' else ''))
    files = _image_refs(sc)
    have = [f for f in files if os.path.exists(os.path.join(d, f))]
    steps.append('照 references/templates.md 这个模板的「素材」一节出图，文件名和上面对上；位置大小调 place')
    steps.append(f'mm build {_q(d)} → 看 build/check_place.png → mm preview {_q(d)} → mm render {_q(d)}')
    return [f for f in files if f not in have], have, steps


def _package_hints(pk, d):
    """按 package.json 里实际有什么，写「下一步」"""
    steps, base = [], os.path.join(d, pk.get('base', 'base.jpg'))
    if 'segments' in pk:
        steps.append('改 palette（4 色）、ink、intro（开场字）、segments（每段 title + icon），见 references/templates.md「color-panels」')
    if pk.get('plate'):
        dur_ = f' --dur {pk["duration"]:g}' if isinstance(pk.get('duration'), (int, float)) else ' --dur 秒数'
        steps.append(f'真人走动底片：mm plate 视频.mp4 --out {_q(d)}{dur_}（--dur 按秒取这一镜要的长度；从视频中间接着取加 --start-sec 秒数。写出 {pk["plate"]}、{pk.get("base", "base.jpg")}、{pk.get("sky", "sky.png")}）')
    elif pk.get('sky'):
        steps.append(f'天空遮罩：mm sky {_q(base)} --out {_q(os.path.join(d, pk["sky"]))}')
    if pk.get('near'):
        steps.append(f'近处遮挡：mm occluder 空街景.png 去近景.jpg --target {_q(base)} --out {_q(os.path.join(d, pk["near"]))}（看 near_check.jpg）'
                     + ('。要等上一步 mm plate 写出底图以后再跑' if pk.get('plate') else ''))
    imgs = _image_refs(pk.get('actors', []))
    if imgs:
        sub = sorted({os.path.dirname(f) for f in imgs if os.path.dirname(f)})
        steps.append(f'角色图 {len(imgs)} 张：{"、".join(imgs)}。灰底角色表用 mm cutout 表.png --names … --out {_q(os.path.join(d, sub[0]) if sub else d)} 切开')
    t = pk.get('title') or {}
    if t.get('img'):
        steps.append(f'落款字标：mm decal 字标原图.png --out {_q(os.path.join(d, t["img"]))}')
    elif t.get('text'):
        steps.append(f'落款现在是系统字 {json.dumps(t["text"], ensure_ascii=False)}；有字标图就改成 "img"')
    if pk.get('actors') or pk.get('plate'):
        steps.append(f'量坐标：mm grid {_q(base)}（package.json 用原图像素）')
    steps.append('照 references/templates.md 这个模板的「素材」「package.json」两节准备素材、改字段')
    steps.append(f'mm package {_q(d)} --name 成片名' + ('（每次都会出自检图和提醒，必看）' if pk.get('actors') else ''))
    return steps


def cmd_new(a):
    """从桥段模板新建场景：复制 scene.json（生成器模板是 params.json + make_scene.py），按模板里实际有什么列出素材和下一步"""
    import shutil
    tdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'templates')
    pdir = os.path.join(os.path.dirname(tdir), 'packages')
    names, pnames = sorted(os.listdir(tdir)), sorted(os.listdir(pdir))
    d = a.dir.rstrip('/') or a.dir
    if a.template in pnames:                           # 包装模板（street-crowd / color-panels）：拷 package.json，用 mm package 渲染
        os.makedirs(a.dir, exist_ok=True); dst = os.path.join(a.dir, 'package.json')
        if os.path.exists(dst) and not a.force:
            print(json.dumps({'error': f'{dst} 已经存在，要覆盖加 --force'}, ensure_ascii=False)); return
        pk = json.load(open(os.path.join(pdir, a.template, 'package.json'), encoding='utf-8')); pk['template'] = a.template
        json.dump(pk, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(json.dumps({'package': dst, 'template': a.template, '下一步': _package_hints(pk, d)}, ensure_ascii=False, indent=1)); return
    if a.template not in names:
        print(json.dumps({'error': f'没有这个模板：{a.template}', 'templates': names, 'packages': pnames}, ensure_ascii=False)); return
    src = os.path.join(tdir, a.template)
    os.makedirs(a.dir, exist_ok=True)
    if not os.path.exists(os.path.join(src, 'scene.json')) and os.path.exists(os.path.join(src, 'make_scene.py')):
        # 生成器模板（scribble-pop）：没有现成的 scene.json，拷 params.json + make_scene.py，由 make_scene.py 按 params.json 生成
        dst = os.path.join(a.dir, 'params.json')
        if os.path.exists(dst) and not a.force:
            print(json.dumps({'error': f'{dst} 已经存在，要覆盖加 --force'}, ensure_ascii=False)); return
        for f in os.listdir(src):
            if not f.startswith(('.', '_')) and os.path.isfile(os.path.join(src, f)) and (a.force or f in ('params.json', 'make_scene.py') or not os.path.exists(os.path.join(a.dir, f))):
                shutil.copy(os.path.join(src, f), a.dir)
        P = json.load(open(dst, encoding='utf-8'))
        poses = list(P.get('poses', {}).values()); files = [P.get('base', 'base.jpg')] + poses
        have = [f for f in files if os.path.exists(os.path.join(a.dir, f))]
        W, H = P.get('size', [1920, 1080]); ms = os.path.join(d, 'make_scene.py')
        keys = [k for k in ('desk_y', 'gather', 'float', 'land_x', 'hops', 'desk_height', 'float_height', 'colors') if k in P]
        print(json.dumps({'params': dst, 'template': a.template, '要准备的素材': [f for f in files if f not in have], '已有': have,
                          '下一步': [f'底图 {files[0]}（比例和 size {W}×{H} 一样）+ 同一个角色的 {len(poses)} 张姿势图 {"、".join(poses)}（灰底或透明），放进 {d}，文件名和 params.json 对上；照 references/templates.md「{a.template}」一节出图',
                                    f'量坐标：mm grid {_q(os.path.join(d, files[0]))}（刻度自动按 params.json 的 size {W}×{H}），改 params.json 的 {"、".join(keys)}',
                                    f'mm build {_q(d)}：会先自动跑 {ms}，按 params.json 生成 scene.json 和 _pose_*.png。之后改了 params.json 或姿势图，再 mm build 会自动重新生成（手动跑：uv run {_q(ms)} {_q(d)}）',
                                    f'看 build/check_place.png → mm preview {_q(d)} → mm render {_q(d)}']}, ensure_ascii=False, indent=1)); return
    dst = os.path.join(a.dir, 'scene.json')
    if os.path.exists(dst) and not a.force:
        print(json.dumps({'error': f'{dst} 已经存在，要覆盖加 --force'}, ensure_ascii=False)); return
    shutil.copy(os.path.join(src, 'scene.json'), dst)
    bundled = []
    for f in os.listdir(src):                          # 模板自带的小素材（比如「…」三个点）一起拷过去
        if f != 'scene.json' and not f.startswith('.') and os.path.isfile(os.path.join(src, f)):
            bundled.append(f)
            if not os.path.exists(os.path.join(a.dir, f)): shutil.copy(os.path.join(src, f), a.dir)
    sc = json.load(open(dst, encoding='utf-8'))
    need, have, steps = _scene_hints(sc, d, bundled)
    print(json.dumps({'scene': dst, 'template': a.template, '要准备的素材': need, '已有': [f + ('（模板自带）' if f in bundled else '') for f in have], '下一步': steps},
                     ensure_ascii=False, indent=1))


def cmd_split(a):
    """把几张「多个角色画在一起」的姿势图，各自切成左右（或更多）单独的角色 PNG"""
    from PIL import Image, ImageDraw
    import numpy as np
    from mmlib import art as A
    names = a.names.split(',') if a.names else [f'p{i}' for i in range(a.n)]
    check = []
    for f in a.images:
        rgba = A.load_rgba(os.path.join(a.dir, f) if not os.path.isabs(f) else f)
        outs, cuts = A.split_by_seam(rgba, a.n, border=None if a.no_border else a.border)
        stem = os.path.splitext(os.path.basename(f))[0]
        for nm, o in zip(names, outs):
            Image.fromarray(np.clip(o, 0, 255).astype(np.uint8), 'RGBA').save(os.path.join(a.dir, f'{nm}_{stem}.png'))
        bgc = Image.new('RGB', (rgba.shape[1], rgba.shape[0]), (90, 90, 90))
        vis = bgc.copy(); vis.paste(Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), 'RGBA'), (0, 0), Image.fromarray(np.clip(rgba[..., 3], 0, 255).astype(np.uint8))); d = ImageDraw.Draw(vis)
        for i, o in enumerate(outs):          # 每个角色染一种颜色，看有没有切错
            col = [(255, 80, 80), (80, 140, 255), (80, 200, 120)][i % 3]
            tint = Image.new('RGB', vis.size, col)
            vis.paste(tint, (0, 0), Image.fromarray((np.clip(o[..., 3], 0, 255) * 0.45).astype(np.uint8)))
        for c in cuts:
            d.line([(int(x), y) for y, x in enumerate(c)], fill=(255, 255, 0), width=5)
        check.append(vis.resize((300, 300)))
    sheet = Image.new('RGB', (300 * len(check), 300))
    for i, c in enumerate(check):
        sheet.paste(c, (i * 300, 0))
    out = os.path.join(a.dir, 'split_check.jpg'); sheet.save(out, quality=85)
    print(json.dumps({'outputs': [f'{nm}_{{图名}}.png' for nm in names], 'check': out}, ensure_ascii=False))


def cmd_package(a):
    """包装镜头：文件夹里放 package.json，按模板渲染成 MP4。
    模板在 assets/packages/<名字>/：color-panels（全屏色块转场）、street-crowd（实拍街景里混进一群平面小怪）。"""
    import shutil, base64, http.server, threading, functools
    from playwright.sync_api import sync_playwright
    from mmlib.browser import find_chrome, ffmpeg_exe
    from mmlib import sfx
    d = os.path.abspath(a.dir)
    if not os.path.exists(os.path.join(d, 'package.json')):
        raise SystemExit(f'{a.dir} 里没有 package.json：先 mm new street-crowd {_q(a.dir)}（或 color-panels）')
    pk = json.load(open(os.path.join(d, 'package.json'), encoding='utf-8'))
    tpl = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'packages', pk.get('template', 'color-panels'))
    if not os.path.isdir(tpl):
        raise SystemExit(f'没有这个包装模板：{pk.get("template")}（可选：{"、".join(sorted(os.listdir(os.path.dirname(tpl))))}）')
    t_ = pk.get('title') if isinstance(pk.get('title'), dict) else {}
    refs = [pk[k] for k in ('base', 'sky', 'near', 'plate') if isinstance(pk.get(k), str)] + _image_refs(pk.get('actors', [])) + ([t_['img']] if t_.get('img') else []) \
        + [s['icon'] for s in pk.get('segments', []) if isinstance(s, dict) and str(s.get('icon', '')).lower().endswith(('.png', '.jpg', '.webp'))]
    bad = [f for f in refs if any(c in f for c in '#?%')]   # 浏览器把 # 当网址片段、? 当参数、% 当转义：文件明明在，播放器也读不到
    if bad:
        raise SystemExit('这些文件（或文件夹）名里有 #、?、% 符号，播放器读不到：' + '、'.join(bad)
                         + '\n  把文件改个名，去掉这几个符号（比如 字标#2.png → 字标2.png），package.json 里的名字也跟着改')
    miss = []                                          # 素材没齐就先说清楚缺什么、怎么做，别等浏览器报错
    plate_hint = f'mm plate 视频.mp4 --out {_q(a.dir)}' + (f' --dur {pk["duration"]:g}' if isinstance(pk.get('duration'), (int, float)) else '')
    if pk.get('plate') and not os.path.isdir(os.path.join(d, pk['plate'])):
        miss.append(f'底片 {pk["plate"]}：{plate_hint}（会一起写出 base.jpg、sky.png）；不用真人底片就删掉 "plate"')
    elif pk.get('plate') and not any(f.startswith('f_') and f.endswith('.jpg') for f in os.listdir(os.path.join(d, pk['plate']))):
        miss.append(f'底片文件夹 {pk["plate"]} 是空的（上次 mm plate 多半没跑成）：重新跑 {plate_hint}')
    for k, how in (('base', '底图'), ('sky', '天空遮罩：mm sky 底图，或 mm plate 会写出'), ('near', '近处遮挡：mm plate 跑完以后，mm occluder 空街景.png 去近景.jpg --target base.jpg')):
        if pk.get(k) and not os.path.exists(os.path.join(d, pk[k])) and not (pk.get('plate') and k in ('base', 'sky') and miss):
            miss.append(f'{pk[k]}（{how}）')
    imgs = [f for f in _image_refs(pk.get('actors', [])) + ([t_['img']] if t_.get('img') else []) if not os.path.exists(os.path.join(d, f))]
    if imgs:
        miss.append(f'角色 / 字标图 {len(imgs)} 张：{"、".join(imgs)}（mm cutout 切角色表，mm decal 做字标）')
    if miss:
        raise SystemExit('素材还没齐，先补上：\n  ' + '\n  '.join(miss))
    if pk.get('base'):                                 # 遮罩、底片帧都按底图像素裁：和底图不一样大就会错位，而且不报错
        from PIL import Image
        def _size(p):
            try:
                with Image.open(p) as im_: return im_.size
            except Exception: return None
        bs = _size(os.path.join(d, pk['base'])); bad = []
        rel = lambda f: _q(os.path.join(a.dir, f))
        chk = []
        if pk.get('sky'): chk.append((pk['sky'], f'重新跑 {plate_hint}（会一起写出 {pk["sky"]}）' if pk.get('plate') else f'mm sky {rel(pk["base"])} --out {rel(pk["sky"])} 重做'))
        if pk.get('near'): chk.append((pk['near'], f'mm occluder 空街景.png 去近景.jpg --target {rel(pk["base"])} --out {rel(pk["near"])} 重做（--target 一定要写）'))
        if pk.get('plate'):
            pdir = pk['plate'].rstrip('/') + '/'
            for f in ('f_000.jpg', 'p_000.png'):
                if os.path.exists(os.path.join(d, pdir, f)):
                    chk.append((pdir + f, f'{pk["base"]} 和底片要一起出：重新跑 {plate_hint}，再按新底图重做 near.png（如果有）'))
        for f, how in chk:
            s_ = _size(os.path.join(d, f))
            if bs and s_ and abs(s_[0] / s_[1] - bs[0] / bs[1]) > 0.01 * bs[0] / bs[1]:   # 只比宽高比例：比例一样、大小不同时模板会按比例自动对齐
                bad.append(f'{f} 是 {s_[0]}×{s_[1]}，底图 {pk["base"]} 是 {bs[0]}×{bs[1]}，宽高比例对不上：{how}')
        if bad:
            raise SystemExit('素材和底图的宽高比例对不上，叠上去会错位：\n  ' + '\n  '.join(bad))
    bd = os.path.join(d, 'build'); shutil.rmtree(bd, ignore_errors=True); os.makedirs(bd)
    shutil.copy(os.path.join(tpl, 'index.html'), bd)
    for f in os.listdir(d):                            # 素材（底图、底片帧、遮罩、角色图）原样拷进去，路径照 package.json 里写的
        if f in ('build', 'package.json') or f.endswith(('.mp4', '.wav')): continue
        src = os.path.join(d, f)
        try: os.symlink(src, os.path.join(bd, f))      # 用链接，不整份复制
        except OSError: (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, os.path.join(bd, f))
    if pk.get('plate'):                                # 底片帧数
        pd_ = os.path.join(d, pk['plate'])
        pk['plateN'] = len([f for f in os.listdir(pd_) if f.startswith('f_') and f.endswith('.jpg')])
        pk['platePeople'] = os.path.exists(os.path.join(pd_, 'p_000.png'))
        if os.path.exists(os.path.join(pd_, 'plate.json')) and 'plateFps' not in pk:
            pk['plateFps'] = json.load(open(os.path.join(pd_, 'plate.json')))['fps']
    for sgm in pk.get('segments', []):                 # 自定义图标（PNG）一起拷进去
        ic = sgm.get('icon', '')
        if ic.lower().endswith(('.png', '.jpg', '.webp')):
            if not os.path.exists(os.path.join(bd, os.path.basename(ic))): shutil.copy(os.path.join(d, ic), os.path.join(bd, os.path.basename(ic)))
            sgm['icon'] = os.path.basename(ic)
    open(os.path.join(bd, 'pkg.js'), 'w', encoding='utf-8').write('window.PKG = ' + json.dumps(pk, ensure_ascii=False) + ';')
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *x): pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=bd))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fps = a.fps or pk.get('fps') or (24 if pk.get('template') == 'street-crowd' else 30); name = a.name or os.path.basename(d)   # 默认帧率和模板里的一致
    out = os.path.join(a.out or d, name + '.mp4'); os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + '.noaudio.mp4'; ff = ffmpeg_exe()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=find_chrome())
        pg = b.new_page(viewport={'width': 1080, 'height': 1920})
        pg.goto(f'http://127.0.0.1:{srv.server_address[1]}/index.html'); pg.wait_for_function('window.__ready === true', timeout=60000)
        if pg.evaluate('window.__error || ""'):
            raise SystemExit('播放器报错：' + pg.evaluate('window.__error'))
        pre = pg.evaluate('window.__precheck ? window.__precheck() : []')      # 渲染前检查（镜头框越界等），有错就不渲染
        if pre: raise SystemExit('渲染前检查没过：' + '；'.join(pre))
        dur = pg.evaluate('window.__duration'); n = int(round(dur * fps)); events = pg.evaluate('window.__events ? window.__events() : []')
        proc = subprocess.Popen([ff, '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', str(fps), '-c:v', 'mjpeg', '-i', '-',
                                 '-vf', 'scale=in_range=full:out_range=tv:in_color_matrix=bt601:out_color_matrix=bt709,format=yuv420p',
                                 '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-c:v', 'libx264', '-crf', '17', tmp], stdin=subprocess.PIPE)
        js = 't => { window.__render(t); return document.getElementById("c").toDataURL("image/jpeg", 0.95); }'
        for i in range(n):
            proc.stdin.write(base64.b64decode(pg.evaluate(js, i / fps).split(',')[1]))
        warns, checks = [], []
        bres = pg.evaluate('window.__beats ? window.__beats() : null')        # 看得见的新事件时刻：包装镜头每 0.8 秒内要有一个（落款出完后的停留不算）
        if bres:
            beats, qf = bres['beats'], bres.get('quietFrom')
            end_ = qf if qf is not None else dur
            bt = sorted(set([0.0] + [b for b in beats if 0 <= b <= end_] + [end_]))
            for x, y in zip(bt, bt[1:]):
                if y - x > 0.8 + 1e-6 and x < dur - 0.4: warns.append(f'{x:.2f}–{y:.2f} 秒没有新事件（{y - x:.1f} 秒），加一个：配角从画外走进来 / 小怪走出画面 / 第二只怪从楼后探出来 / 眨眼')
        if pg.evaluate('typeof window.__check === "function"'):   # 自检图：角色框、落款框、天空范围 + 重叠提醒
            cts = [float(x) for x in a.check.split(',')] if a.check and a.check != 'auto' else [round(dur * f, 2) for f in (0.15, 0.5, 0.92)]
            for ts in cts:
                r = pg.evaluate('t => window.__check(t)', ts)
                cp = os.path.join(os.path.dirname(out), f'{name}_check_t{ts:.2f}.jpg'); open(cp, 'wb').write(base64.b64decode(r['img'].split(',')[1])); checks.append(cp)
                warns += [f'{ts:.2f}s {w}' for w in r['warns']]
        stills = []
        for ts in ([float(x) for x in a.stills.split(',')] if a.stills else []):
            sp = os.path.join(os.path.dirname(out), f'{name}_t{ts:.2f}.jpg'); open(sp, 'wb').write(base64.b64decode(pg.evaluate(js, ts).split(',')[1])); stills.append(sp)
        b.close()
    proc.stdin.close(); proc.wait(); srv.shutdown()
    try: tdef = json.load(open(os.path.join(tpl, 'package.json'), encoding='utf-8')).get('sound')   # 模板自己声明的默认档位
    except Exception: tdef = None
    # 档位：命令行 --sound → 用户 package.json 的 sound → 模板 package.json 的 sound → 兜底（现有两个包装模板都是 full：street-crowd 的 whoosh 是大怪探出 / 缩回的主事件声）
    level = 'mute' if a.mute or not events else a.sound or pk.get('sound') or tdef or {'color-panels': 'full', 'street-crowd': 'full'}.get(pk.get('template', 'color-panels'), 'minimal')
    if level == 'mute':                                # 没有音轨（--sound none 还是留一条静音音轨，mm concat 要用）
        os.replace(tmp, out)
    else:
        wav = out + '.wav'; sfx.write_wav(wav, sfx.render_events(events, dur, 1.0, level))
        # 不用 -shortest：它会把视频最后几帧（约 0.16 秒）一起切掉。音频补静音到视频那么长，按视频时长截
        subprocess.run([ff, '-y', '-loglevel', 'error', '-i', tmp, '-i', wav, '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-af', 'apad', '-c:a', 'aac', '-b:a', '192k', '-t', f'{n / fps:.4f}', out], check=True)
        os.remove(tmp); os.remove(wav)
    print(json.dumps({'output': out, 'frames': n, 'duration': round(dur, 2), 'sound': level, 'segments': len(pk.get('segments', [])), 'stills': stills,
                      '提醒': sorted(set(warns)) or '无', '自检图（必看）': checks}, ensure_ascii=False))


def _sky_mask(rgb, tol=-3, smooth=6.0):
    """天空 = 连到画面上沿、又平又亮、不偏红的区域（地平线附近发白的天也算）。
    先找「明显的蓝天」当种子，再把和它连着的「浅色、平滑、b ≥ r + tol」的区域并进来；最后往外长 4px 盖住楼顶、树梢边的天光。
    返回带透明度的白图（canvas 遮罩只认透明度）和天空占比"""
    import numpy as np
    from scipy import ndimage
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    m1 = ndimage.uniform_filter(lum, 7); m2 = ndimage.uniform_filter(lum * lum, 7)
    flat = np.sqrt(np.maximum(m2 - m1 * m1, 0)) < smooth
    strong = (b > r + 25) & (b > 120)
    pale = (b >= r + tol) & (lum > 120) & flat
    cand = strong | pale
    lab, n = ndimage.label(cand); keep = np.zeros_like(cand)
    for i in range(1, n + 1):
        m = lab == i
        if m[:5].any() and m.sum() > 2000 and (strong & m).sum() > 0.05 * m.sum(): keep |= m
    if not keep.any():                                 # 整片天都发白、没有一块明显的蓝：退一步，取连到上沿、又亮又平的最大一块
        best = 0
        for i in range(1, n + 1):
            m = lab == i
            if m[:5].any() and m.sum() > max(best, 0.02 * m.size) and (pale & m).sum() > 0.6 * m.sum(): keep = m; best = m.sum()
    keep = ndimage.binary_fill_holes(ndimage.binary_closing(keep, iterations=3))
    keep = ndimage.binary_dilation(keep, iterations=4)          # 往外长 4px：盖住楼顶、树梢边上那圈发亮的天光，不然大怪边上有一圈白边
    a = ndimage.gaussian_filter(keep.astype(float), 1.0) * 255
    return np.dstack([np.full(keep.shape + (3,), 255), a]).astype(np.uint8), float(keep.mean())


def _sky_check(rgb, mask, out):
    """天空遮罩检查图：天空染青、边界描红。大怪只会出现在青色里"""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    al = mask[..., 3] / 255.0; edge = ndimage.binary_dilation(al > 0.5, iterations=2) & ~(al > 0.5)
    v = rgb.copy(); v = v * (1 - 0.45 * al[..., None]) + np.array([0, 220, 255]) * 0.45 * al[..., None]; v[edge] = [255, 30, 30]
    im = Image.fromarray(np.clip(v, 0, 255).astype(np.uint8)); im.thumbnail((1400, 1400)); im.save(out, quality=85)


def cmd_sky(a):
    """从静态街景算天空遮罩（street-crowd 里 behind 的角色只在天空里露出来 = 藏在楼后）"""
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(a.image).convert('RGB')).astype(float)
    m, frac = _sky_mask(im, a.tol, a.smooth); out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.image)), 'sky.png')
    Image.fromarray(m).save(out); chk = os.path.splitext(out)[0] + '_check.jpg'; _sky_check(im, m, chk)
    print(json.dumps({'sky': out, 'check': chk, 'sky_fraction': round(frac, 3), 'note': '天空占比太小（<3%）说明没找到蓝天，换一张有天空缺口的街景' if frac < 0.03 else ''}, ensure_ascii=False))


def _vision_people(pdir, fs, movs, notes):
    """macOS：用系统人像分割（mmlib/segpeople.swift，第一次自动编译到缓存）在「有人在动」的区域里抠出完整的人。
    返回每帧的布尔遮罩列表；不是 macOS、没有 swiftc 或出错时返回 None（只用帧间差分）"""
    import platform, shutil as sh, hashlib, numpy as np
    from PIL import Image
    from scipy import ndimage
    if platform.system() != 'Darwin' or not sh.which('swiftc'):
        notes.append('不是 macOS（或没有 swiftc）：行人遮罩只用帧间差分，衣服和背景颜色接近时大怪可能叠到人身上，看自检图')
        return None
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mmlib', 'segpeople.swift')
    tag = hashlib.md5(open(src, 'rb').read()).hexdigest()[:10]
    exe = os.path.join(os.path.expanduser('~/.cache/mockup-motion'), f'segpeople_{tag}')
    if not os.path.exists(exe):
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        r = subprocess.run(['swiftc', '-O', src, '-o', exe], capture_output=True, text=True)
        if r.returncode:
            notes.append('人像分割编译失败，只用帧间差分：' + r.stderr.strip()[-200:]); return None
    jobs = []
    for i, (f, mov) in enumerate(zip(fs, movs)):
        lab, n = ndimage.label(ndimage.binary_dilation(mov, iterations=8)); rects = []
        H, W = mov.shape
        for sl in ndimage.find_objects(lab):
            if sl is None: continue
            y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
            if (y1 - y0) * (x1 - x0) < 900: continue
            h_, w_ = y1 - y0, x1 - x0                  # 人的上半身常常漏在差分外：框往上、往两边多放一些
            rects.append([max(0, x0 - 0.4 * w_), max(0, y0 - 0.6 * h_), min(W, x1 + 0.4 * w_) - max(0, x0 - 0.4 * w_), min(H, y1 + 0.15 * h_) - max(0, y0 - 0.6 * h_)])
        if rects: jobs.append({'in': os.path.join(pdir, f), 'out': os.path.join(pdir, f'.v_{i:03d}.png'), 'rects': rects, 'i': i})
    if not jobs: return None
    jf = os.path.join(pdir, '.vision_jobs.json'); json.dump(jobs, open(jf, 'w'))
    r = subprocess.run([exe, jf], capture_output=True, text=True)
    out = [None] * len(fs)
    for j in jobs:
        if os.path.exists(j['out']):
            v = np.asarray(Image.open(j['out']).convert('L')) > 110
            near_motion = ndimage.binary_dilation(movs[j['i']], iterations=40)   # 只留和走动的人连着的部分（远处站着不动的人不算）
            lab, n = ndimage.label(v); keep = np.zeros_like(v)
            for k in range(1, n + 1):
                c = lab == k
                if (c & near_motion).any(): keep |= c
            out[j['i']] = keep; os.remove(j['out'])
    os.remove(jf)
    if r.returncode: notes.append('人像分割中途出错，部分帧只用帧间差分：' + r.stderr.strip()[-200:])
    return out


def cmd_plate(a):
    """真人走动底片（AI 视频，固定机位）→ 逐帧图 plate/f_###.jpg + 第一帧 base.jpg + 天空遮罩 sky.png（用逐帧中值算，走动的人不进遮罩）"""
    import math, shutil
    import numpy as np
    from PIL import Image
    from mmlib.browser import ffmpeg_exe
    if not os.path.isfile(a.video):
        raise SystemExit(f'找不到视频 {a.video}：检查文件名和路径有没有写错（路径里有空格就整个用引号括起来）')
    info = _probe(a.video); notes = []
    if not info['video']:
        raise SystemExit(f'{a.video} 读不出画面：它可能不是视频文件，或者文件没下载完整。换一个能正常播放的 mp4 / mov')
    fps_ = info['fps'] or 24.0
    if not info['fps']:
        notes.append('读不到视频帧率，按每秒 24 帧算' + ('：--dur / --start-sec 换算出的帧数可能不准，用 --frames / --start 直接写帧数更稳' if a.dur or a.start_sec is not None else ''))
    start = int(round(a.start_sec * fps_)) if a.start_sec is not None else a.start      # 秒数优先：按这条视频的帧率换算成第几帧
    want = math.ceil(a.dur * fps_ - 1e-6) if a.dur else a.frames                         # 2.2 秒 × 24 帧 = 53 帧；× 30 帧 = 66 帧
    if start < 0 or want < 0 or (a.dur is not None and a.dur <= 0):
        raise SystemExit('--start / --start-sec / --frames / --dur 都不能是负数，--dur 要大于 0')
    total = int(round(info['dur'] * fps_)) if info['dur'] else None
    d = os.path.abspath(a.out); pd = os.path.join(d, 'plate'); os.makedirs(d, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='.plate_new_', dir=d)  # 先抽到临时文件夹：成功了才替换旧底片，失败时旧的原样留着
    os.chmod(tmp, 0o755)                               # mkdtemp 默认只有自己能读，换成普通文件夹权限
    try:
        sel = f"select='between(n\\,{start}\\,{start + want - 1})'," if want else (f"select='gte(n\\,{start})'," if start else '')
        r = subprocess.run([ffmpeg_exe(), '-loglevel', 'error', '-y', '-i', a.video, '-vf', f"{sel}scale={a.width}:-2", '-fps_mode', 'passthrough',
                            '-start_number', '0', '-q:v', '2', os.path.join(tmp, 'f_%03d.jpg')], capture_output=True, text=True)
        fs = sorted(f for f in os.listdir(tmp) if f.startswith('f_') and f.endswith('.jpg'))
        if not fs:
            if start and (total is None or start >= total - 1):
                raise SystemExit(f'一帧都没抽到：起点第 {start} 帧（{start / fps_:.2f} 秒）已经到了视频结尾或更后面'
                                 + (f'（这条视频约 {info["dur"]:.2f} 秒、{total} 帧，每秒 {fps_:g} 帧）' if total else '') + '。把 --start-sec / --start 改小，或者换一条更长的视频。旧底片没动')
            last = ((r.stderr or '').strip().splitlines() or ['（没有输出）'])[-1]
            raise SystemExit(f'一帧都没抽到：ffmpeg 读不了 {a.video}（{last}）。确认它是能正常播放的视频；旧底片没动')
        if r.returncode:
            notes.append(f'ffmpeg 中途报错，只抽到 {len(fs)} 帧：视频可能有损坏，最好重新下载一次')
        if want and len(fs) < want:
            notes.append(f'要 {want} 帧（{want / fps_:.2f} 秒），可从起点往后视频只剩 {len(fs)} 帧（{len(fs) / fps_:.2f} 秒）：镜头比这长的话，后面的人会定住。'
                         '换一条更长的视频，或者把 --start-sec 往前挪、--dur 改短')
        stack = np.stack([np.asarray(Image.open(os.path.join(tmp, f))).astype(np.float32) for f in fs[::max(1, len(fs) // 12)][:24]])   # float32、最多 24 帧：省内存
        med = np.median(stack, 0); m, frac = _sky_mask(med, a.tol, a.smooth)
        from scipy import ndimage                      # 走动的人（任何位置）→ 逐帧行人遮罩 plate/p_###.png（白=人）。大怪画在人后面：天空模式、近处遮挡模式都会把它扣掉
        sky = m[..., 3] > 127; hit = 0; moved = 0; movs = []
        for i, f in enumerate(fs):                     # ① 帧间差分：哪里有人在动（衣服和背景颜色接近时会漏一块）
            fr = np.asarray(Image.open(os.path.join(tmp, f))).astype(float)
            movs.append(ndimage.binary_dilation(ndimage.binary_opening(np.abs(fr - med).sum(-1) > 60, iterations=2), iterations=2))
        vis = _vision_people(tmp, fs, movs, notes)     # ② macOS 人像分割：在动的地方抠出完整的人（不是 Mac 就跳过）
        for i, mov in enumerate(movs):
            if vis is not None and vis[i] is not None: mov = mov | ndimage.binary_dilation(vis[i], iterations=2)
            mov = ndimage.binary_fill_holes(ndimage.binary_closing(mov, iterations=4))   # 衣服上的小洞补上（大怪不会从人身上透出来）
            if (mov & sky).sum() > 200: hit += 1
            if mov.sum() > 500: moved += 1
            a_ = ndimage.gaussian_filter(mov.astype(float), 1.0) * 255
            Image.fromarray(np.dstack([np.full(mov.shape + (3,), 255), a_]).astype(np.uint8)).save(os.path.join(tmp, f'p_{i:03d}.png'))
        if not moved:                                  # 没人走动就不要逐帧遮罩
            for i in range(len(fs)): os.remove(os.path.join(tmp, f'p_{i:03d}.png'))
        json.dump({'fps': fps_, 'frames': len(fs), 'start': start, 'people_masks': bool(moved)}, open(os.path.join(tmp, 'plate.json'), 'w'))
        if os.path.islink(pd) or os.path.isfile(pd): os.remove(pd)       # 新底片齐了：换掉旧的
        elif os.path.isdir(pd): shutil.rmtree(pd)
        os.rename(tmp, pd)
    finally:
        if os.path.isdir(tmp): shutil.rmtree(tmp, ignore_errors=True)
    Image.fromarray(m).save(os.path.join(d, 'sky.png')); _sky_check(med, m, os.path.join(d, 'sky_check.jpg'))
    Image.open(os.path.join(pd, fs[0])).save(os.path.join(d, 'base.jpg'), quality=93)
    w, h = Image.open(os.path.join(pd, fs[0])).size
    drift = float(np.abs(np.asarray(Image.open(os.path.join(pd, fs[0]))).astype(np.float32) - np.asarray(Image.open(os.path.join(pd, fs[-1]))).astype(np.float32)).mean())   # 首帧和末帧比
    if drift > 25: notes.append('首尾差 > 25 多半是镜头在动，遮罩会对不上，重出固定机位')
    if frac < 0.03: notes.append(f'天空只认出 {frac:.0%}（天偏灰、偏绿时认不出）：楼后的大怪请用近处遮挡层 mm occluder（推荐），或加 --tol -8 重跑；看 sky_check.jpg')
    try:
        with Image.open(os.path.join(d, 'near.png')) as nm_: nsz = nm_.size
    except Exception: nsz = None
    if nsz and nsz != (w, h):
        notes.append(f'near.png 是 {nsz[0]}×{nsz[1]}，新底图 base.jpg 是 {w}×{h}：重新跑 mm occluder 空街景.png 去近景.jpg --target {_q(os.path.join(a.out, "base.jpg"))} --out {_q(os.path.join(a.out, "near.png"))}；角色坐标也要在新 base.jpg 上重新量')
    print(json.dumps({'plate': pd, 'frames': len(fs), 'seconds': round(len(fs) / fps_, 2), 'start_frame': start, 'fps': fps_, 'size': [w, h],
                      'base': os.path.join(d, 'base.jpg'), 'sky': os.path.join(d, 'sky.png'), 'check': os.path.join(d, 'sky_check.jpg'), 'sky_fraction': round(frac, 3),
                      'first_last_diff': round(drift, 1), 'frames_with_people': moved, 'frames_people_in_sky': hit, '提醒': notes or '无'}, ensure_ascii=False))


def cmd_cutout(a):
    """纯色底（灰底最好）的角色表 → 一个个透明 PNG。按行（上到下）、行内从左到右的顺序命名；顺便去掉底色边"""
    import re
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    im = np.asarray(Image.open(a.sheet).convert('RGB')).astype(float)
    bg = np.median(np.concatenate([im[:12].reshape(-1, 3), im[-12:].reshape(-1, 3), im[:, :12].reshape(-1, 3), im[:, -12:].reshape(-1, 3)]), 0)
    dist = np.sqrt(((im - bg) ** 2).sum(-1)); al = np.clip((dist - a.tol) / 26, 0, 1)
    lab, n = ndimage.label(ndimage.binary_dilation(al > 0.5, iterations=6))
    objs = [(s, i + 1) for i, s in enumerate(ndimage.find_objects(lab)) if (s[0].stop - s[0].start) * (s[1].stop - s[1].start) > a.min_area]
    objs.sort(key=lambda o: o[0][0].start)
    hmed = float(np.median([o[0][0].stop - o[0][0].start for o in objs])) if objs else 1
    rows = []
    for o in objs:                                     # 按「顶边」分行：顶边差不到半个（中位）身高的算同一行，大角色跨几行也只算它顶边那一行
        top = o[0][0].start
        if rows and abs(top - rows[-1][0]) < hmed * 0.5: rows[-1][1].append(o)
        else: rows.append([top, [o]])
    order = [o for _, r in rows for o in sorted(r, key=lambda o: o[0][1].start)]
    stem = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]+', '_', os.path.splitext(os.path.basename(a.sheet))[0])[:12].strip('_') or 'cut'
    names = [x.strip() for x in a.names.split(',') if x.strip()] if a.names else [f'{stem}_{i}' for i in range(len(order))]   # 默认名带表名前缀，两张表切到同一个文件夹不会互相覆盖
    out = a.out or os.path.dirname(os.path.abspath(a.sheet)); os.makedirs(out, exist_ok=True); res = []
    for nm, ((sy, sx), idx) in zip(names, order):
        m = (lab[sy, sx] == idx) * al[sy, sx]; mm_ = np.maximum(m, 1e-3)[..., None]
        c = np.clip((im[sy, sx] - (1 - mm_) * bg) / mm_, 0, 255)
        Image.fromarray(np.dstack([c, m * 255]).astype(np.uint8)).save(os.path.join(out, nm + '.png'))
        res.append({'name': nm, 'size': [int(sx.stop - sx.start), int(sy.stop - sy.start)]})
    from PIL import ImageDraw                          # 对照图：每个切出来的框标上名字，看有没有对错号
    vis = Image.open(a.sheet).convert('RGB'); dr = ImageDraw.Draw(vis); fsz = max(28, vis.width // 40)
    f_ = _cjk_font(fsz); fk = {'font': f_} if f_ else {'font_size': fsz}      # 中文名要中文字体，不然是方框
    for i, ((sy, sx), _) in enumerate(order):          # 没分到名字的标黄色 #序号：一眼看出名字少写了
        nm, col = (names[i], (255, 40, 40)) if i < len(names) else (f'#{i} 没名字' if 'font' in fk else f'#{i} ?', (255, 210, 0))
        dr.rectangle([sx.start, sy.start, sx.stop, sy.stop], outline=col, width=6); dr.text((sx.start + 10, sy.start + 8), nm, fill=col, **fk)
    vis.thumbnail((1200, 1200)); chk = os.path.join(out, os.path.splitext(os.path.basename(a.sheet))[0][:40] + '_check.jpg'); vis.save(chk, quality=85)
    print(json.dumps({'found': len(order), 'named': len(res), 'check': chk, 'rows': [len(r) for _, r in rows], 'outputs': res,
                      'note': '找到的个数和名字个数不一样，检查 --names 或 --min-area' if a.names and len(order) != len(names) else ''}, ensure_ascii=False))


def cmd_occluder(a):
    """近处遮挡层：同一画面「原图」和「AI 去掉近处楼、树、灯杆的图」相减 → near.png（白=近处的东西，楼后的大怪会被它挡住）。
    原图用**没加行人之前的空街景**（行人不会被算进来）；--target 底片第一帧，遮罩自动缩放、对齐到底片上"""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    for f, what in ((a.original, '原图（没加行人的空街景）'), (a.removed, '去掉近处楼、树的图')):
        if not os.path.isfile(f): raise SystemExit(f'找不到{what} {f}：检查文件名和路径有没有写错')
    if a.target and not os.path.isfile(a.target):      # 先查，别等遮罩算完了才报英文错
        tdir = os.path.dirname(a.target) or '.'
        raise SystemExit(f'--target 的 {a.target} 还不存在：它是 mm plate 写出的底片第一帧。先跑 mm plate 视频.mp4 --out {_q(tdir)} --dur 秒数，再跑这条 mm occluder'
                         '（不要去掉 --target：不带它算出的 near.png 和底图不一样大，会挡错位置）')
    A = Image.open(a.original).convert('RGB'); W, H = A.size
    B = Image.open(a.removed).convert('RGB').resize((W, H), Image.LANCZOS)
    a_ = np.asarray(A).astype(float); b_ = np.asarray(B).astype(float)
    ga, gb = a_.mean(-1), b_.mean(-1)                  # 相位相关找小平移（AI 改图偶尔整体偏几个像素）
    Fa, Fb = np.fft.fft2(ga - ga.mean()), np.fft.fft2(gb - gb.mean()); R = Fa * np.conj(Fb); R /= np.abs(R) + 1e-9
    r = np.abs(np.fft.ifft2(R)); dy, dx = np.unravel_index(np.argmax(r), r.shape)
    dy = dy - H if dy > H // 2 else dy; dx = dx - W if dx > W // 2 else dx
    if abs(dx) < 40 and abs(dy) < 40: b_ = _shift(b_, int(dy), int(dx), edge=True)     # 不用 np.roll：挪出去的边会从对边绕回来，多出一条假遮挡
    diff = np.abs(ndimage.gaussian_filter(a_, (2, 2, 0)) - ndimage.gaussian_filter(b_, (2, 2, 0))).sum(-1)
    m = diff > a.diff
    m = ndimage.binary_closing(ndimage.binary_opening(m, iterations=2), iterations=4)
    lab, n = ndimage.label(m); keep = np.zeros_like(m)
    for i in range(1, n + 1):
        mm_ = lab == i
        if mm_.sum() > a.min_area: keep |= mm_
    keep = ndimage.binary_fill_holes(keep)
    keep = ndimage.binary_closing(keep, iterations=6)
    if a.target:                                       # 对齐到底片：原图（没人的空街景）和底片第一帧之间找缩放后的平移，遮罩跟着挪
        T = Image.open(a.target).convert('RGB'); TW, TH = T.size
        km = Image.fromarray((keep * 255).astype(np.uint8)).resize((TW, TH), Image.BILINEAR)
        ga2 = np.asarray(A.resize((TW, TH), Image.LANCZOS).convert('L')).astype(float); gt = np.asarray(T.convert('L')).astype(float)
        F1, F2 = np.fft.fft2(gt - gt.mean()), np.fft.fft2(ga2 - ga2.mean()); R2 = F1 * np.conj(F2); R2 /= np.abs(R2) + 1e-9
        r2 = np.abs(np.fft.ifft2(R2)); ty, tx = np.unravel_index(np.argmax(r2), r2.shape)
        ty = ty - TH if ty > TH // 2 else ty; tx = tx - TW if tx > TW // 2 else tx
        keep = np.asarray(km).astype(float) / 255 > 0.5
        if abs(tx) < 60 and abs(ty) < 60: keep = _shift(keep, int(ty), int(tx), edge=True)   # 空出来的边用边缘补：贴着画面边的近楼不会漏一条
        a_ = np.asarray(T).astype(float); dx, dy = int(tx), int(ty)
    al = ndimage.gaussian_filter(keep.astype(float), 1.2) * 255
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.target or a.original)), 'near.png')
    Image.fromarray(np.dstack([np.full(keep.shape + (3,), 255), al]).astype(np.uint8)).save(out)
    v = a_.copy(); v[keep] = v[keep] * 0.45 + np.array([255, 60, 200]) * 0.55
    chk = os.path.splitext(out)[0] + '_check.jpg'; im = Image.fromarray(np.clip(v, 0, 255).astype(np.uint8)); im.thumbnail((1400, 1400)); im.save(chk, quality=85)
    print(json.dumps({'near': out, 'check': chk, 'shift': [int(dx), int(dy)], 'near_fraction': round(float(keep.mean()), 3),
                      'note': '粉色 = 近处遮挡（楼后的大怪会被它挡住）。漏了一块就把 --diff 调小，背景被算进来就调大'}, ensure_ascii=False))


def cmd_frames(a):
    """从视频抽帧做自检：--t 1,2.5,4 指定时刻；--from/--to/--n 在一段里均匀抽 n 帧。出单帧 jpg + 一张联系表"""
    from PIL import Image, ImageDraw
    from mmlib.browser import ffmpeg_exe
    out = a.out or os.path.splitext(a.video)[0] + '_frames'
    sheet_path = None
    if out.lower().endswith(('.jpg', '.jpeg', '.png')):   # --out 写成图片名：联系表存成这个名字，单帧放旁边的同名文件夹
        sheet_path = out; out = os.path.splitext(out)[0] + '_帧'
    os.makedirs(out, exist_ok=True)
    ts = [float(x) for x in a.t.split(',')] if a.t else [a.start + (a.end - a.start) * i / max(1, a.n - 1) for i in range(a.n)]
    ims = []
    for t in ts:
        f = os.path.join(out, f't{t:06.3f}.jpg')
        if os.path.exists(f): os.remove(f)             # 上次抽的同名帧删掉，不然超出片长时会拿旧图冒充
        subprocess.run([ffmpeg_exe(), '-loglevel', 'quiet', '-y', '-ss', f'{t:.3f}', '-i', a.video, '-frames:v', '1', '-vf', 'scale=out_range=full,format=yuvj420p', '-q:v', '2', f])   # 超出片长的时刻会抽不到，跳过
        if os.path.exists(f): im = Image.open(f).convert('RGB'); im.thumbnail((480, 480)); ImageDraw.Draw(im).text((8, 6), f'{t:.2f}s', fill=(255, 255, 0), font_size=22); ims.append(im)
    if not ims: raise SystemExit('一帧都没抽到：时刻超出了片长？')
    cols = min(len(ims), a.cols); rows = (len(ims) + cols - 1) // cols; w, h = ims[0].size
    sheet = Image.new('RGB', (w * cols, h * rows))
    for i, im in enumerate(ims): sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    sp_ = sheet_path or os.path.join(out, 'sheet.jpg'); sheet.save(sp_, quality=85)
    print(json.dumps({'frames': len(ims), 'dir': out, 'sheet': sp_}, ensure_ascii=False))


def cmd_hold(a):
    """手里拿着东西、东西要跳起来的场景：给两张同角度的图——「拿着产品」和「同姿势空手」（AI 从前一张去掉产品），
    算出产品该放的位置（place）和压在产品前面的手指遮罩（mask，白=手指）。"""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    from mmlib.build import _base
    base = _base({'base': os.path.basename(a.empty)}, os.path.dirname(os.path.abspath(a.empty)))
    W, H = base.size
    ld = lambda p: np.asarray(Image.open(p).convert('RGB').resize((W, H), Image.LANCZOS)).astype(int)
    hold, hol = ld(a.holding), ld(a.empty)
    bag = ndimage.binary_opening(np.abs(hold - hol).sum(-1) > a.diff, iterations=2)
    lab, n = ndimage.label(bag)
    if not n:
        print(json.dumps({'error': '两张图几乎一样，找不到产品'}, ensure_ascii=False)); return
    sizes = ndimage.sum(bag, lab, range(1, n + 1))
    bag = lab == (np.argmax(sizes) + 1)
    ys, xs = np.where(bag)
    L, R = int(np.percentile(xs, 1)), int(np.percentile(xs, 99))
    y0, y1 = int(ys.min()), int(ys.max())
    sil = np.zeros_like(bag); sil[y0:y1 + 1, max(L - 6, 0):R + 7] = True
    skin = lambda c: (c[..., 0] > 95) & (c[..., 0] > c[..., 1] + 10) & (c[..., 1] > c[..., 2]) & (c[..., 0] - c[..., 2] > 25)
    front = sil & skin(hold) & skin(hol)          # 两张里都是肤色（手指一直在）、又落在产品范围里：压在产品前面的手指
    front = ndimage.binary_closing(ndimage.binary_opening(front, iterations=1), iterations=3)
    lab, n = ndimage.label(front)
    if n:
        sizes = ndimage.sum(front, lab, range(1, n + 1))
        front = np.isin(lab, np.where(sizes > 150)[0] + 1)
    Image.fromarray((front * 255).astype(np.uint8)).save(a.out)
    chk = hold.copy().astype(np.uint8)
    chk[front] = (chk[front] * 0.3 + np.array([0, 255, 0]) * 0.7).astype(np.uint8)
    ck = os.path.splitext(a.out)[0] + '_check.jpg'
    Image.fromarray(chk).save(ck, quality=88)
    print(json.dumps({'mask': a.out, 'check': ck,
                      'place': {'width': round((R - L) / W, 3), 'height': round((y1 - y0) / H, 3), 'anchor': [round((L + R) / 2 / W, 3), round((y0 + y1) / 2 / H, 3)]},
                      'mask_json': {'image': os.path.basename(a.out), 'invert': True, 'feather': 1.2},
                      '下一步': '底图用「同姿势空手」那张；产品图（最好黑底出）放进 rig，place 和 mask 照上面写'}, ensure_ascii=False, indent=1))


def cmd_decal(a):
    """白色贴纸类素材（AI 在黑底上出的白图）转成透明底：白的不透明、黑的透明。
    窗贴、玻璃上的白线稿用它；普通抠图会把被白色包住的黑洞（o 的中间、黑色的脸）当填充留下来。"""
    import numpy as np
    from PIL import Image
    outs, notes = [], []
    ext = os.path.splitext(a.out)[1].lower() if a.out else ''
    if a.out and ext in ('.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.svg') and not os.path.isdir(a.out):
        raise SystemExit(f'--out {a.out}：贴纸是透明底，要存成 .png（比如 --out {_q(os.path.splitext(a.out)[0] + ".png")}），或者写一个文件夹')
    if a.out and len(a.images) > 1 and ext in ('.png', '.webp') and not os.path.isdir(a.out):
        raise SystemExit(f'一次转 {len(a.images)} 张图时，--out 要写文件夹（比如 --out sp），每张按原名存进去；要指定文件名就一张一张转')
    out_is_dir = bool(a.out) and (len(a.images) > 1 or a.out.endswith(('/', os.sep)) or os.path.isdir(a.out) or ext not in ('.png', '.webp'))   # 没写 .png 结尾的一律当文件夹，不存在就建
    for src in a.images:
        if not os.path.isfile(src): raise SystemExit(f'找不到图片 {src}：检查文件名和路径有没有写错')
        im = np.asarray(Image.open(src).convert('RGB')).astype(np.float32)
        lum = im.mean(-1)
        bw = max(4, round(min(lum.shape) * 0.02))         # 底色从四周一圈量：AI 的「纯黑」常是 40–60 的深灰，阈值要跟着抬
        band = np.concatenate([lum[:bw].ravel(), lum[-bw:].ravel(), lum[:, :bw].ravel(), lum[:, -bw:].ravel()])
        bgl = float(np.median(band))
        lo, hi = a.low, a.high                      # 每张图各算各的阈值（几张一起转时不互相带）
        if a.ink: hi = max(min(hi, bgl - 25), lo + 10)
        else: lo = min(max(lo, bgl + 25), hi - 10)
        if a.ink:                                   # 反过来：深色墨线变成白线（粗黑线稿的彩色角色 → 白色线稿窗贴，设计不变）
            alpha = np.clip((hi - lum) / (hi - lo), 0, 1)
        else:
            alpha = np.clip((lum - lo) / (hi - lo), 0, 1)
        if a.bold:                                  # 细线稿放到画面里会糊掉：把线条加粗几像素
            from scipy import ndimage
            alpha = ndimage.grey_dilation(alpha, size=(a.bold * 2 + 1, a.bold * 2 + 1))
        rim = max(2, round(min(alpha.shape) * 0.006))      # 最外圈常有一道 AI 画的细边，清掉
        alpha[:rim] = 0; alpha[-rim:] = 0; alpha[:, :rim] = 0; alpha[:, -rim:] = 0
        from scipy import ndimage as _nd                  # 去掉零星小噪点（纸纹、压缩块），不然会把内容范围撑大
        lab, n = _nd.label(alpha > 0.3)
        if n:
            sizes = _nd.sum(np.ones_like(alpha), lab, range(1, n + 1))
            tiny = np.isin(lab, np.where(sizes < alpha.size * 2e-5)[0] + 1)
            alpha = np.where(_nd.binary_dilation(tiny, iterations=1) & ~np.isin(lab, np.where(sizes >= alpha.size * 2e-5)[0] + 1), 0, alpha)
            alpha = np.where(alpha > 0.06, alpha, 0)
        rgba = np.zeros(im.shape[:2] + (4,), np.uint8)
        rgba[..., :3] = np.array([int(a.color[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)
        rgba[..., 3] = (alpha * 255).astype(np.uint8)
        if out_is_dir:                                  # --out 是文件夹：几张都放进去
            out = os.path.join(a.out, os.path.splitext(os.path.basename(src))[0] + '.png')
        else:
            out = a.out or os.path.splitext(src)[0] + '.png'
        if os.path.abspath(out) == os.path.abspath(src):        # 输入本身是 png：别把原图覆盖掉
            out = os.path.splitext(src)[0] + '_decal.png'
            bn = os.path.basename(src); st = os.path.splitext(bn)[0]
            notes.append(f'没覆盖原图 {bn}，转好的存成了 {os.path.basename(out)}。场景里用的要是 {bn}：先把原图改名（比如 {st}_原图.png），'
                         f'再 mm decal {_q(os.path.join(os.path.dirname(src), st + "_原图.png"))} --out {_q(src)}（--color 等照原来的写）')
        if not a.keep_margin:                          # 裁掉四周空白（留 2%），落款按 size 缩放时字才是真的那么大
            ys_, xs_ = np.nonzero(rgba[..., 3] > 20)
            if len(ys_):
                pad = int(0.02 * max(rgba.shape[:2])); y0_, y1_ = max(0, ys_.min() - pad), min(rgba.shape[0], ys_.max() + pad + 1); x0_, x1_ = max(0, xs_.min() - pad), min(rgba.shape[1], xs_.max() + pad + 1)
                rgba = np.ascontiguousarray(rgba[y0_:y1_, x0_:x1_])
        if os.path.dirname(out): os.makedirs(os.path.dirname(out), exist_ok=True)
        Image.fromarray(rgba, 'RGBA').save(out)
        outs.append(out)
    print(json.dumps({'decals': outs} | ({'提醒': notes} if notes else {}), ensure_ascii=False))


def cmd_palette(a):
    """从底图取主要颜色：角色和字只用这几种颜色，画面才和场景是一套"""
    import numpy as np
    from PIL import Image, ImageDraw
    im = Image.open(a.image).convert('RGB')
    if a.box:
        x0, y0, x1, y1 = [int(float(v)) for v in a.box.split(',')]; im = im.crop((x0, y0, x1, y1))
    im.thumbnail((240, 240))
    px = np.asarray(im).reshape(-1, 3).astype(float)
    rng = np.random.default_rng(0); C = px[rng.choice(len(px), a.n, replace=False)]
    for _ in range(25):
        lab = np.argmin(((px[:, None] - C[None]) ** 2).sum(-1), 1)
        C = np.array([px[lab == k].mean(0) if (lab == k).any() else C[k] for k in range(a.n)])
    share = np.bincount(lab, minlength=a.n) / len(lab)
    order = np.argsort(-share)
    cols = []
    for k in order:
        r, g, b = C[k]; lum = 0.299 * r + 0.587 * g + 0.114 * b
        mx, mn = max(r, g, b), min(r, g, b); sat = (mx - mn) / max(mx, 1)
        cols.append(dict(hex='#%02x%02x%02x' % tuple(int(v) for v in C[k]), share=round(float(share[k]), 3), lum=int(lum), sat=round(sat, 2)))
    # 建议：一个深色当线条/主色，一个最有颜色的当强调色，一个浅色当填充
    dark = min(cols, key=lambda c: c['lum']); light = max(cols, key=lambda c: c['lum'])
    accent = max([c for c in cols if c not in (dark, light)] or cols, key=lambda c: c['sat'])
    sw = Image.new('RGB', (100 * len(cols), 130), (255, 255, 255)); d = ImageDraw.Draw(sw)
    for i, c in enumerate(cols):
        d.rectangle([i * 100, 0, i * 100 + 99, 99], fill=c['hex']); d.text((i * 100 + 4, 104), f"{c['hex']} {int(c['share'] * 100)}%", fill=(0, 0, 0))
    out = os.path.splitext(a.image)[0] + '_palette.png'; sw.save(out)
    print(json.dumps({'colors': cols, '建议': {'线条/主色': dark['hex'], '强调色': accent['hex'], '填充/浅色': light['hex']}, 'swatch': out}, ensure_ascii=False, indent=1))


def cmd_import_codex(a):
    """把 Codex image-logo-motion 的 config.json（背景 + SVG 图层 + 关键帧 + 四角）转成 mockup-motion 的场景"""
    from PIL import Image, ImageOps
    import shutil
    cfg = json.load(open(a.config)); src = os.path.dirname(os.path.abspath(a.config))
    W, H = int(cfg.get('width', 1080)), int(cfg.get('height', 1440))
    os.makedirs(a.out, exist_ok=True)
    bg = Image.open(os.path.join(src, cfg['background'])).convert('RGB')
    ImageOps.fit(bg, (W, H), Image.LANCZOS).save(os.path.join(a.out, 'base.png'))      # 和 Codex 一样：居中等比铺满再裁
    shutil.copy(os.path.join(src, cfg['logo']), os.path.join(a.out, 'art.svg'))
    quad = [[p[0] * W, p[1] * H] for p in cfg['quad']]
    parts, acts = {}, []
    keymap = {'x': 0, 'y': 0}
    for L in cfg['layers']:
        pid = L['id']; parts[pid] = {}
        if 'pivot' in L:
            parts[pid]['pivot'] = L['pivot']
        kf = sorted(L['keyframes'], key=lambda k: k['time'])
        full = lambda k, f, d: k.get(f, d)
        eases = [k.get('ease', 'linear') for k in kf[:-1]]
        acts.append({'do': 'move', 'part': pid, 'keys': [[k['time'], full(k, 'x', 0), full(k, 'y', 0)] for k in kf], 'eases': eases})
        acts.append({'do': 'scale', 'part': pid, 'keys': [[k['time'], full(k, 'scale', 1), full(k, 'scale', 1)] for k in kf], 'eases': eases})
        acts.append({'do': 'rotate', 'part': pid, 'keys': [[k['time'], full(k, 'rotation', 0)] for k in kf], 'eases': eases})
        acts.append({'do': 'fade', 'part': pid, 'keys': [[k['time'], full(k, 'opacity', 1)] for k in kf], 'eases': eases})
        if any(k.get('reveal', 1) != 1 for k in kf):
            acts.append({'do': 'reveal', 'part': pid, 'keys': [[k['time'], full(k, 'reveal', 1)] for k in kf], 'eases': eases})
    scene = {'base': 'base.png', 'duration': cfg.get('duration', 5), 'fps': cfg.get('fps', 30), 'sound': 'minimal',
             'camera': {'push': 0, 'drift': 0, 'punch': False}, 'grain': 0,
             'graphics': [{'art': 'art.svg', 'surface': {'quad': quad}, 'place': {'width': 1, 'height': 1, 'fit': 'art'},
                           'look': {'blend': cfg.get('blend', 'multiply'), 'shade': 0.6, 'soften': 0.5, 'boil': 0},
                           'rig': {'only_parts': True, 'enter': {'motion': 'static', 'idle': 'none'}, 'parts': parts, 'acts': acts}}]}
    json.dump(scene, open(os.path.join(a.out, 'scene.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({'scene': os.path.join(a.out, 'scene.json'), 'layers': [L['id'] for L in cfg['layers']],
                      '说明': '已按原配置换算；镜头晃动、颗粒、冲击都关掉了，和原片一致。想加 mockup-motion 的节奏和效果，直接改这份 scene.json'}, ensure_ascii=False, indent=1))


def cmd_layers(a):
    with _session(None) as s:
        info = s.svg_layers(a.svg)
    print(json.dumps(info, ensure_ascii=False, indent=1))


def cmd_preview(a):
    from PIL import Image, ImageDraw
    from mmlib.browser import decode_dataurl
    import io
    bdir = os.path.join(a.scene, 'build')
    with _session(bdir) as s:
        s.open_player()
        dur = s.page.evaluate('window.SCENE.duration')
        ts = [float(x) for x in a.t.split(',')] if a.t else [round(dur * k / 7, 2) for k in range(8)]
        frames = []
        for t in ts:
            r = s.page.evaluate('([t]) => window.__frame(t, ["pure"], 0.92)', [t])
            frames.append(Image.open(io.BytesIO(decode_dataurl(r['pure']))).convert('RGB'))
        if s.errors:
            print('播放器报错：', s.errors)
    cw = 360
    ch = round(frames[0].height * cw / frames[0].width)
    cols = min(4, len(frames)); rows = (len(frames) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cw, rows * (ch + 24)), (40, 40, 44))
    d = ImageDraw.Draw(sheet)
    for i, (t, f) in enumerate(zip(ts, frames)):
        x, y = (i % cols) * cw, (i // cols) * (ch + 24)
        sheet.paste(f.resize((cw, ch), Image.LANCZOS), (x, y + 24))
        d.text((x + 6, y + 6), f't={t:.2f}s', fill=(255, 230, 120))
    out = os.path.join(a.scene, 'preview.jpg')
    sheet.save(out, quality=90)
    print(json.dumps({'preview': out, 'times': ts}, ensure_ascii=False))


def cmd_render(a):
    from mmlib.browser import decode_dataurl, ffmpeg_exe
    from mmlib import sfx
    bdir = os.path.join(a.scene, 'build')
    name = a.name or os.path.basename(os.path.abspath(a.scene))
    outdir = a.out or a.scene
    os.makedirs(outdir, exist_ok=True)
    want = ['pure', 'compare'] if a.layout == 'both' else [a.layout]
    ff = ffmpeg_exe()
    with _session(bdir) as s:
        s.open_player()
        scene = s.page.evaluate('({d: window.SCENE.duration, f: window.SCENE.fps})')
        dur, fps = scene['d'], a.fps or scene['f']
        n = int(round(dur * fps))
        procs, tmp = {}, {}
        for w in want:
            tmp[w] = os.path.join(outdir, f'.{name}_{w}_noaudio.mp4')
            procs[w] = subprocess.Popen([ff, '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', str(fps), '-c:v', 'mjpeg',
                                         '-i', '-', '-vf', 'scale=in_range=full:out_range=tv:in_color_matrix=bt601:out_color_matrix=bt709,format=yuv420p',
                                         '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                                         '-c:v', 'libx264', '-crf', str(a.crf), '-preset', 'medium',
                                         '-movflags', '+faststart', tmp[w]], stdin=subprocess.PIPE)
        for i in range(n):
            r = s.page.evaluate('([t, w]) => window.__frame(t, w, 0.95)', [i / fps, want])
            for w in want:
                procs[w].stdin.write(decode_dataurl(r[w]))
            if i % max(1, n // 10) == 0:
                print(f'  帧 {i}/{n}', flush=True)
        events = s.page.evaluate('window.__events()')
        level = s.page.evaluate('window.SCENE.sound || "minimal"')
        timeline = s.page.evaluate('window.__timeline()')
        stills = []
        if a.stills:
            from mmlib.browser import decode_dataurl as _d
            sd = os.path.join(outdir, f'{name}_stills'); os.makedirs(sd, exist_ok=True)
            for ts in [float(x) for x in a.stills.split(',')]:
                fi = min(n - 1, max(0, int(round(ts * fps)))); tt = fi / fps
                r2 = s.page.evaluate('([t]) => window.__frame(t, ["pure"], 0.95)', [tt])
                pth = os.path.join(sd, f'frame_{fi:06d}_t{tt:.3f}.jpg'); open(pth, 'wb').write(_d(r2['pure'])); stills.append(pth)
        if s.errors:
            print('播放器报错：', s.errors)
    for w in want:
        procs[w].stdin.close(); procs[w].wait()
    outs = []
    wav = None
    if not a.mute and events:
        wav = os.path.join(outdir, f'.{name}_sfx.wav')
        sfx.write_wav(wav, sfx.render_events(events, dur, a.volume, a.sound or level))
    for w in want:
        final = os.path.join(outdir, f'{name}{"" if w == "pure" else "_对照"}.mp4')
        if wav:
            subprocess.run([ff, '-y', '-loglevel', 'error', '-i', tmp[w], '-i', wav, '-map', '0:v:0', '-map', '1:a:0',
                            '-c:v', 'copy', '-af', 'apad', '-c:a', 'aac', '-b:a', '192k', '-t', f'{n / fps:.4f}', final], check=True)   # 不用 -shortest（会切掉最后几帧）
            os.remove(tmp[w])
        else:
            os.replace(tmp[w], final)
        outs.append(final)
    if wav:
        os.remove(wav)
    meta = dict(scene=os.path.abspath(a.scene), outputs=outs, frames=n, fps=fps, duration=dur, sound=a.sound or level,
                timeline=timeline, sound_events=events, stills=stills)
    meta_path = os.path.join(outdir, f'{name}.metadata.json')
    json.dump(meta, open(meta_path, 'w'), ensure_ascii=False, indent=1)
    dense = len([e for e in events if e['kind'] not in ('tick',)]) / max(dur, 0.1)
    tips = ['声音偏满：每秒超过 3 个声音，考虑给不重要的组加 "sfx": false'] if dense > 3 else []
    print(json.dumps({'outputs': outs, 'metadata': meta_path, 'frames': n, 'fps': fps, 'sfx_events': len(events), 'stills': stills, '提醒': tips}, ensure_ascii=False))


def cmd_concat(a):
    from mmlib.browser import ffmpeg_exe
    ff = ffmpeg_exe()
    info = []
    for p in a.inputs:                                 # 先看每段：有没有音轨、多长、多大、帧率
        if not os.path.exists(p): raise SystemExit(f'找不到 {p}：检查文件名和路径有没有写错')
        x = _probe(p)
        if not x['video']: raise SystemExit(f'{p} 读不出画面：它可能不是视频文件，或者没渲染完整')
        x['dur'] = x['dur'] or 0; info.append(x)
    sizes = {x['size'] for x in info}
    if len(sizes) > 1:
        raise SystemExit('几段画面尺寸不一样，接不起来：' + '、'.join(f'{os.path.basename(p)} {x["size"][0]}×{x["size"][1]}' for p, x in zip(a.inputs, info) if x['size']))
    fps0 = info[0]['fps']                              # 帧率不一样（场景模板 30、street-crowd 24）：都统一成第一段的，不然成片是忽快忽慢的可变帧率
    mixed = fps0 is not None and any(x['fps'] is not None and abs(x['fps'] - fps0) > 0.01 for x in info[1:])
    inputs, k, src_a = [], len(a.inputs), []
    for p in a.inputs:
        inputs += ['-i', p]
    for x in info:                                     # 没有音轨的段（--mute 出的）补一段静音，不然接不起来
        if x['audio']: src_a.append(None)
        else: inputs += ['-f', 'lavfi', '-t', f'{x["dur"]:.3f}', '-i', 'anullsrc=r=44100:cl=mono']; src_a.append(k); k += 1
    n = len(a.inputs)
    # fps 滤镜会在每段末尾多补一帧（刚好落在结束时刻）：按这段原长截掉半帧以后的，总长和原来一样
    pre = ''.join(f'[{i}:v:0]fps={fps0:g}' + (f',trim=duration={info[i]["dur"] - 0.5 / fps0:.4f}' if info[i]['dur'] > 1 / fps0 else '') + f'[v{i}];' for i in range(n)) if mixed else ''
    fl = pre + ''.join(f'[{f"v{i}" if mixed else f"{i}:v:0"}][{i if src_a[i] is None else src_a[i]}:a:0]' for i in range(n)) + f'concat=n={n}:v=1:a=1[v][a]'
    r = subprocess.run([ff, '-y', '-loglevel', 'error', *inputs, '-filter_complex', fl, '-map', '[v]', '-map', '[a]',
                        *(['-r', f'{fps0:g}'] if mixed else []), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-c:a', 'aac', '-b:a', '192k',
                        '-movflags', '+faststart', a.output], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('拼接没成功：' + ((r.stderr or '').strip().splitlines() or ['（ffmpeg 没有输出）'])[-1])
    res = {'output': a.output, 'segments': n, 'duration': round(sum(x['dur'] for x in info), 2), 'fps': fps0,
           '补了静音': [os.path.basename(p) for p, x in zip(a.inputs, info) if not x['audio']]}
    if mixed:
        res['统一了帧率'] = ('几段帧率不一样（' +'、'.join(f'{os.path.basename(p)} {x["fps"]:g}' for p, x in zip(a.inputs, info) if x['fps'])
                         + f'），都按第一段统一成每秒 {fps0:g} 帧（帧率高的段会均匀丢几帧、低的会重复几帧，看着基本没区别）。想统一成别的帧率，就把那个帧率的片段放在第一个')
    print(json.dumps(res, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest='cmd', required=True)
    g = sp.add_parser('grid'); g.add_argument('image'); g.add_argument('--step', type=int, default=100, help='网格间距；加 --box 时自动选，改它按比例变疏密'); g.add_argument('--out'); g.add_argument('--keep', action='store_true', help='图不缩放，实线标原图像素 src（package.json、角色图用）'); g.add_argument('--box', help='只看这块并放大：x0,y0,x1,y1（按原图像素写）；图上和输出里两套刻度都有')
    s = sp.add_parser('surface'); s.add_argument('image'); s.add_argument('--seed', required=True); s.add_argument('--tol', type=float)
    b = sp.add_parser('build'); b.add_argument('scene')
    l = sp.add_parser('layers'); l.add_argument('svg')
    pkp = sp.add_parser('package'); pkp.add_argument('dir'); pkp.add_argument('--out'); pkp.add_argument('--name'); pkp.add_argument('--fps', type=int); pkp.add_argument('--stills'); pkp.add_argument('--mute', action='store_true'); pkp.add_argument('--check', nargs='?', const='auto', default='auto', help='自检图时刻，逗号分隔；默认开场/中间/结尾 3 张'); pkp.add_argument('--sound', choices=['minimal', 'full', 'none'], help='不写就按 package.json 的 sound，再按模板 package.json 里声明的默认档位（street-crowd、color-panels 都是 full）')
    sk = sp.add_parser('sky'); sk.add_argument('image'); sk.add_argument('--out'); sk.add_argument('--tol', type=float, default=-3, help='浅色天空的 蓝−红 下限，天发灰白漏掉就调小（-8），楼面被算进来就调大（5）'); sk.add_argument('--smooth', type=float, default=6, help='平滑度阈值，越大越宽松')
    pl = sp.add_parser('plate'); pl.add_argument('video'); pl.add_argument('--out', required=True, help='场景文件夹（写 plate/、base.jpg、sky.png）'); pl.add_argument('--dur', type=float, help='取几秒（按视频帧率换算成帧数，比 --frames 优先），如 --dur 2.2'); pl.add_argument('--start-sec', type=float, help='从第几秒开始（比 --start 优先），如 --start-sec 2.2')
    pl.add_argument('--start', type=int, default=0, help='从第几帧开始（用 --start-sec 按秒写更省事）'); pl.add_argument('--frames', type=int, default=0, help='取多少帧，0=到结尾（用 --dur 按秒写更省事）'); pl.add_argument('--width', type=int, default=1990, help='缩到多宽（比画面宽一点，留给镜头横移）'); pl.add_argument('--tol', type=float, default=-3); pl.add_argument('--smooth', type=float, default=6)
    co = sp.add_parser('cutout'); co.add_argument('sheet'); co.add_argument('--names', help='逗号分隔，按行从上到下、行内从左到右'); co.add_argument('--out'); co.add_argument('--tol', type=float, default=14); co.add_argument('--min-area', type=int, default=6000)
    oc_ = sp.add_parser('occluder'); oc_.add_argument('original', help='原图：没加行人的空街景（行人会走开，不能算进遮挡）'); oc_.add_argument('removed', help='AI 去掉近处楼、树、灯杆后的图'); oc_.add_argument('--out'); oc_.add_argument('--target', help='底片第一帧 base.jpg：遮罩对齐、缩放到它上面'); oc_.add_argument('--diff', type=float, default=110); oc_.add_argument('--min-area', type=int, default=3000)
    from mmlib import measure as _measure, beats as _beats, pick as _pick
    _measure.add_parser(sp); _beats.add_parser(sp); _pick.add_parser(sp)
    fr = sp.add_parser('frames'); fr.add_argument('video'); fr.add_argument('--t', help='时刻，逗号分隔'); fr.add_argument('--from', dest='start', type=float, default=0); fr.add_argument('--to', dest='end', type=float, default=3); fr.add_argument('--n', type=int, default=12); fr.add_argument('--cols', type=int, default=4); fr.add_argument('--out')
    ho = sp.add_parser('hold'); ho.add_argument('holding', help='拿着产品的图'); ho.add_argument('empty', help='同姿势空手的图（当底图）'); ho.add_argument('--out', default='front.png'); ho.add_argument('--diff', type=int, default=90)
    de = sp.add_parser('decal'); de.add_argument('images', nargs='+'); de.add_argument('--out'); de.add_argument('--color', default='#ffffff', help='贴纸颜色'); de.add_argument('--keep-margin', action='store_true', help='不裁四周空白（要和底图逐像素对齐的贴纸用）')
    de.add_argument('--low', type=float, default=40); de.add_argument('--high', type=float, default=200); de.add_argument('--bold', type=int, default=0, help='线条加粗几像素（原图像素）'); de.add_argument('--ink', action='store_true', help='取深色墨线当贴纸（线稿角色转白色窗贴），配合 --low 60 --high 130 这类阈值')
    pa = sp.add_parser('palette'); pa.add_argument('image'); pa.add_argument('--n', type=int, default=5); pa.add_argument('--box', help='只看这块区域 x0,y0,x1,y1')
    ic = sp.add_parser('import-codex'); ic.add_argument('config'); ic.add_argument('out')
    nw = sp.add_parser('new'); nw.add_argument('template'); nw.add_argument('dir'); nw.add_argument('--force', action='store_true')
    sp_ = sp.add_parser('split'); sp_.add_argument('dir'); sp_.add_argument('images', nargs='+'); sp_.add_argument('--n', type=int, default=2); sp_.add_argument('--names', help='切出来每个角色的名字，逗号分隔，如 left,right')
    sp_.add_argument('--border', type=float, default=0.011, help='给每个角色重新描的白边宽度（图片宽度的比例）'); sp_.add_argument('--no-border', action='store_true', help='不重画白边，直接切')
    f = sp.add_parser('frame'); f.add_argument('image'); f.add_argument('--box', required=True, help='x0,y0,x1,y1')
    f.add_argument('--coords', choices=['src', 'out'], default='out', help='out=按 1080 宽网格图的坐标；src=原图像素'); f.add_argument('--size', default='1080x1440'); f.add_argument('--out', required=True)
    v = sp.add_parser('preview'); v.add_argument('scene'); v.add_argument('--t')
    r = sp.add_parser('render'); r.add_argument('scene'); r.add_argument('--layout', choices=['pure', 'compare', 'both'], default='both')
    r.add_argument('--fps', type=int); r.add_argument('--crf', type=int, default=17); r.add_argument('--out'); r.add_argument('--name')
    r.add_argument('--mute', action='store_true'); r.add_argument('--volume', type=float, default=1.0)
    r.add_argument('--sound', choices=['minimal', 'full', 'none'], help='音效多少；默认用 scene.json 的 sound（不写是 minimal）')
    r.add_argument('--stills', help='再导出这些时间点的静帧（秒，逗号分隔），质检用')
    c = sp.add_parser('concat'); c.add_argument('output'); c.add_argument('inputs', nargs='+')
    a = p.parse_args()
    {'grid': cmd_grid, 'surface': cmd_surface, 'build': cmd_build, 'preview': cmd_preview, 'layers': cmd_layers, 'frame': cmd_frame, 'split': cmd_split, 'new': cmd_new, 'palette': cmd_palette, 'decal': cmd_decal, 'hold': cmd_hold, 'package': cmd_package, 'sky': cmd_sky, 'plate': cmd_plate, 'cutout': cmd_cutout, 'frames': cmd_frames, 'measure': _measure.run, 'beats': _beats.run, 'pick': _pick.run, 'occluder': cmd_occluder, 'import-codex': cmd_import_codex,
     'render': cmd_render, 'concat': cmd_concat}[a.cmd](a)


if __name__ == '__main__':
    main()
