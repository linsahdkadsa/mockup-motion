# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow", "scipy"]
# ///
# scribble-pop（临摹「实拍里的 2D 怪物」）：线条甩出聚团 → 砰地弹大 → 悬停 → 跳到桌面压扁 → 蹦几步 → 招手。
# 用法：mm new scribble-pop 场景/ 会把本文件和 params.json 拷进场景文件夹。改好 params.json（坐标按 size 的画面像素），
# 姿势图和底图放进场景文件夹（灰底或透明），然后直接 mm build 场景/（会自动跑本文件），或者手动：
#   uv run 场景/make_scene.py 场景/        → 在场景文件夹里生成 scene.json 和 _pose_*.png，再 mm build / mm render
# 不写文件夹就用本文件所在的文件夹。节奏是逐帧量参考片得到的，不用改；要换位置只改 params.json。
import json, os, sys, numpy as np
from PIL import Image
from scipy import ndimage
HERE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
if not os.path.exists(os.path.join(HERE, 'params.json')):
    raise SystemExit(f'{HERE} 里没有 params.json：先 mm new scribble-pop 场景/，再在场景文件夹里跑')
os.chdir(HERE)                      # 下面的文件（姿势图、scene.json、_pose_*.png）都在场景文件夹里读写
P = json.load(open('params.json', encoding='utf-8'))
_miss = [f for f in P['poses'].values() if not os.path.exists(f)]
if _miss:
    raise SystemExit(f'{HERE} 里缺这些姿势图：{_miss}（文件名要和 params.json 的 poses 对上）'
                     + ('。这是模板文件夹：先 mm new scribble-pop 场景/，再对场景文件夹跑' if os.path.basename(os.path.dirname(HERE)) == 'templates' else ''))
W, H = P['size']; D = P['desk_y']; O = (W // 2, D); K = min(1.0, 1200 / max(W, H))   # 角色图在引擎里最长边缩到 1200（小图不放大）
if not os.path.exists(P['base']):
    print(f'提醒：还没有底图 {P["base"]}，mm build 前放进来')
else:
    _bw, _bh = Image.open(P['base']).size
    if abs(_bw / _bh - W / H) > 0.02 * W / H:
        print(f'提醒：底图 {_bw}×{_bh} 和 size {W}×{H} 比例不一样，底图会被拉伸。把 size 改成同比例（比如 [{W}, {round(W * _bh / _bw)}]），坐标照新尺寸量')
def cut(path):                      # 灰底 / 白底的姿势图抠成透明，返回图和角色框
    im = Image.open(path).convert('RGBA'); a = np.asarray(im).astype(float)
    if a[..., 3].min() > 250:
        rgb = a[..., :3]; band = np.concatenate([rgb[:16].reshape(-1, 3), rgb[-16:].reshape(-1, 3), rgb[:, :16].reshape(-1, 3), rgb[:, -16:].reshape(-1, 3)])
        d = np.sqrt(((rgb - np.median(band, 0)) ** 2).sum(-1)); lab, _ = ndimage.label(d < 26)
        edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])); edge = edge[edge > 0]
        obj = ndimage.binary_opening(ndimage.binary_fill_holes(~np.isin(lab, edge)), iterations=2)
        a[..., 3] = ndimage.gaussian_filter(obj.astype(float), 0.8) * 255
    im = Image.fromarray(a.astype(np.uint8), 'RGBA'); bb = im.getbbox()
    if not bb: raise SystemExit(f'{path} 抠完是空的：要灰底或透明底、角色和底色分得开')
    return im, bb
HT = {'land': P['desk_height'] * 0.73}
for k, f in P['poses'].items():
    im, (x0, y0, x1, y1) = cut(f); s = HT.get(k, P['desk_height']) / (y1 - y0)
    im = im.crop((x0, y0, x1, y1)); im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    px, py = round(O[0] - im.width / 2), round(O[1] - im.height)      # 脚底中点对齐 O；超出画面上沿、左沿的部分裁掉（alpha_composite 不收负坐标）
    c = Image.new('RGBA', (W, H), (0, 0, 0, 0)); c.alpha_composite(im.crop((max(0, -px), max(0, -py), im.width, im.height)), (max(0, px), max(0, py))); c.save(f'_pose_{k}.png')
dh, fh = P['desk_height'], P['float_height']; gx, gy = P['gather']; fx_, fy = P['float']
F = lambda cx, cy, h: (cx, cy + h / 2)
key = [(0.46, *F(gx, gy, fh * .25), fh * .25 / dh), (0.56, *F(gx, gy - 30, fh * .5), fh * .5 / dh), (0.60, *F(fx_ - 10, fy + 10, fh * 1.07), fh * 1.07 / dh),
       (0.64, *F(fx_ - 5, fy + 20, fh * .96), fh * .96 / dh), (0.79, *F(fx_, fy, fh), fh / dh), (0.94, *F(fx_, fy - 15, fh), fh / dh), (0.98, *F(fx_, fy + 15, fh), fh / dh)]
lx = P['land_x']; key += [(1.03, (fx_ + lx) / 2, min(fy, D) + fh * 0.18, (fh + dh) / 2 / dh), (1.08, lx, D, 1.0)]
t, x = 1.20, lx
for x1, hh in P['hops']:
    key += [(t, x, D, 1.0), (t + 0.09, (x + x1) / 2, D - hh, 1.0), (t + 0.18, x1, D, 1.0)]; x = x1; t += 0.24
key.sort(); end_hops = t
move = [[round(a, 3), round((x - O[0]) * K, 1), round((y - O[1]) * K, 1)] for a, x, y, _ in key]
eases = ['ease-out' if key[i + 1][2] < key[i][2] else 'ease-in' if key[i + 1][2] > key[i][2] else 'smooth' for i in range(len(key) - 1)]
scale = sorted([[a, round(s, 3), round(s, 3)] for a, _, _, s in key if a <= 1.08] + [[0.96, fh / dh * 1.12, fh / dh * 0.82]])
scale += [[1.12, 1.07, 0.88], [1.16, 0.96, 1.05], [1.20, 1, 1], [end_hops + 0.1, 1, 1], [end_hops + 0.14, 1.03, 0.96], [end_hops + 0.2, 1, 1]]
hops_t = [(1.20 + i * 0.24, 1.38 + i * 0.24) for i in range(len(P['hops']))]
spans = {'burst': [(0.46, 0.64)], 'float': [(0.64, 0.98)], 'hop': [(0.98, 1.08)] + hops_t, 'land': [(1.08, 1.20)] + [(b, b + 0.06) for _, b in hops_t],
         'stand': [(end_hops - 0.06 + 0.06, end_hops + 0.04)], 'wave': [(end_hops + 0.04, 99)]}
def show(sp):
    k = [[0, 0]]
    for a, b in sp: k += [[round(a - 0.001, 3), 0], [round(a, 3), 1], [round(b - 0.001, 3), 1], [round(b, 3), 0]]
    return k
c1, c2 = P['colors']; fr = lambda q: [round(q[0] / W, 4), round(q[1] / H, 4)]
lines = [([[gx - 140, 60], [gx + 300, 30], [gx + 310, gy + 110], [gx - 40, gy + 90]], 0.06, 0.34, c1, 0.024, 0.6),
         ([[gx + 300, 80], [gx + 60, -20], [gx - 220, gy - 110], [gx, gy + 130]], 0.14, 0.30, c2, 0.017, 0.6),
         ([[gx + 360, gy - 230], [gx + 240, gy - 130], [gx + 120, gy - 50], [gx + 10, gy]], 0.30, 0.18, c1, 0.014, 0.7),
         ([[gx - 280, gy - 170], [gx - 180, gy - 100], [gx - 90, gy - 40], [gx - 10, gy]], 0.34, 0.16, c2, 0.010, 0.7)]
scene = {"base": P['base'], "size": [W, H], "duration": round(end_hops + 0.8, 2), "fps": 24, "camera": {"push": 0.0, "drift": 0.3, "punch": False}, "grain": 3,
 "graphics": [{"art": "_pose_stand.png", "surface": {"quad": [[0, 0], [W, 0], [W, H], [0, H]]}, "place": {"width": 1, "height": 1, "anchor": [0.5, 0.5], "fit": "art"},
  "look": {"shade": 0.0, "soften": 0.0, "boil": 0.0, "clip": False},
  "rig": {"travel": True, "shadow": {"ground": D / H, "size": dh * 0.37 / W, "opacity": 0.55, "lift": 0.25},
   "enter": {"motion": "static", "at": 0, "idle": "none"},
   "parts": {"root": {"virtual": True, "pivot_frac": [O[0] / W, O[1] / H]}, **{k: {"png": f"_pose_{k}.png", "parent": "root"} for k in P['poses']}},
   "acts": [{"do": "show", "part": k, "keys": show(v)} for k, v in spans.items()] +
           [{"do": "move", "part": "root", "keys": move, "eases": eases}, {"do": "scale", "part": "root", "keys": scale},
            {"do": "rotate", "part": "root", "keys": [[0.64, 0], [0.72, 4], [0.80, -4], [0.88, 3], [0.94, 0], [0.98, 0], [1.0, 14], [1.08, 0], [end_hops + 0.04, 0], [end_hops + 0.12, 3], [end_hops + 0.22, -3], [end_hops + 0.32, 2], [end_hops + 0.42, 0]]},
            {"do": "impact", "at": 1.08, "sfx": "knock"}]},
  "accents": [{"kind": "strokes", "at": 0, "lines": [{"pts": [fr(q) for q in p], "at": a, "dur": d, "color": c, "width": w, "trail": tr} for p, a, d, c, w, tr in lines],
               "splat": {"pos": fr((gx, gy + 130)), "at": 0.40, "dur": 0.12, "color": c1, "size": 0.036}},
              {"kind": "burst", "at": 0.56, "pos": [(fx_ - 5) * K, (fy + 20) * K], "size": fh * 0.36 * K, "color": c1, "n": 8, "dur": 0.22}]}]}
json.dump(scene, open('scene.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'{os.path.join(HERE, "scene.json")} ok · duration {scene["duration"]}')
