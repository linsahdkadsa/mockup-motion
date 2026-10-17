"""找表面：从底图上的一个点长出整块表面（招牌/海报/橱窗），拟合成四边形，算透视矩阵。"""
import numpy as np
from scipy import ndimage as ndi


def _cdist(a, b):
    return np.sqrt(((a - b) ** 2).sum(-1))


def grow_surface(img, seed, tol=40):
    """img: HxWx3 float。从 seed 往外长出颜色相近的一片；光照有渐变时按局部颜色再长两轮。"""
    H, W, _ = img.shape
    blur = np.stack([ndi.gaussian_filter(img[..., c], 2.0) for c in range(3)], -1)
    sx, sy = int(seed[0]), int(seed[1])
    ref = blur[max(sy - 5, 0):sy + 6, max(sx - 5, 0):sx + 6].reshape(-1, 3).mean(0)
    mask = _cdist(blur, ref) < tol
    for _ in range(2):
        lab, _ = ndi.label(mask)
        if lab[sy, sx] == 0:
            break
        mask = lab == lab[sy, sx]
        w = mask.astype(np.float32)
        den = ndi.gaussian_filter(w, 30)
        local = np.stack([ndi.gaussian_filter(blur[..., c] * w, 30) for c in range(3)], -1) / np.maximum(den, 1e-4)[..., None]
        local = np.where((den > 1e-3)[..., None], local, ref)
        mask = _cdist(blur, local) < tol
    lab, _ = ndi.label(mask)
    mask = lab == lab[sy, sx]
    mask = ndi.binary_closing(mask, iterations=3)
    mask = ndi.binary_fill_holes(mask)
    mask = ndi.binary_opening(mask, iterations=2)
    lab, _ = ndi.label(mask)
    if lab[sy, sx]:
        mask = lab == lab[sy, sx]
    return mask


def fit_quad(mask):
    """把一块区域拟合成四边形，返回 [[x,y]*4]，顺序 左上、右上、右下、左下。"""
    from scipy.spatial import ConvexHull
    ys, xs = np.where(mask & ~ndi.binary_erosion(mask))
    pts = np.stack([xs, ys], 1).astype(np.float64)
    hull = pts[ConvexHull(pts).vertices]
    poly = list(hull)
    # 每次去掉「去掉后面积损失最小」的顶点，直到剩 4 个
    def tri(a, b, c):
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
    while len(poly) > 4:
        n = len(poly)
        losses = [tri(poly[i - 1], poly[i], poly[(i + 1) % n]) for i in range(n)]
        poly.pop(int(np.argmin(losses)))
    q = np.array(poly)
    c = q.mean(0)
    ang = np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0])
    q = q[np.argsort(ang)]                      # 顺时针（y 向下）：从左上开始
    k = int(np.argmin(q[:, 0] + q[:, 1]))
    q = np.roll(q, -k, 0)
    return [[round(float(x), 1), round(float(y), 1)] for x, y in q]


def homography(src, dst):
    """src/dst: 4 个点 [[x,y]...]，返回 3x3 矩阵 H，dst ~ H @ src"""
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    b = np.array(dst, np.float64).reshape(-1)
    h = np.linalg.solve(np.array(A, np.float64), b)
    return np.append(h, 1).reshape(3, 3)


def quad_size(q):
    """四边形摊平后的大致宽高"""
    q = np.array(q, np.float64)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return float(w), float(h)


def warp_to_flat(img, quad, size):
    """把四边形区域摊平成 size=(w,h) 的正面图（双线性采样）。img 可以是 HxW 或 HxWxC"""
    w, h = size
    H = homography([[0, 0], [w, 0], [w, h], [0, h]], quad)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    P = H @ np.stack([xx.ravel() + 0.5, yy.ravel() + 0.5, np.ones(xx.size)])
    u, v = P[0] / P[2] - 0.5, P[1] / P[2] - 0.5
    chans = img[..., None] if img.ndim == 2 else img
    out = np.stack([ndi.map_coordinates(chans[..., c], [v, u], order=1, mode='constant') for c in range(chans.shape[2])], -1)
    out = out.reshape(h, w, -1)
    return out[..., 0] if img.ndim == 2 else out


def quad_area(q):
    q = np.array(q, np.float64)
    x, y = q[:, 0], q[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def find_quad(img, seed, tol=None):
    """自动找表面四边形。没给 tol 就从宽到严试几档，挑「长出来的区域最像四边形」的那档：
    表面和周围颜色太近时，宽容差会漏到墙上（区域不再像四边形），收紧后就对了。
    返回 (quad, mask, report)"""
    H, W, _ = img.shape
    tries = [tol] if tol else [45, 36, 28, 22, 16, 12]
    best = None
    for t in tries:
        m = grow_surface(img, seed, t)
        if m.sum() < 500:
            continue
        q = fit_quad(m)
        fill = float(m.sum()) / max(quad_area(q), 1)
        edge = m[0].mean() + m[-1].mean() + m[:, 0].mean() + m[:, -1].mean()
        score = -abs(fill - 1) - 0.5 * edge     # 区域面积和四边形面积越接近越好（>1 说明区域不止一块四边形）
        rec = dict(tol=t, fill=round(fill, 3), touches_border=round(float(edge), 3), quad=q)
        if best is None or score > best[0] + 0.01:
            best = (score, q, m, rec)
        if abs(fill - 1) < 0.03 and edge < 0.02:
            break
    if best is None:
        raise ValueError('从 seed 点没长出表面：换一个落在表面空白处的点，或直接给 quad')
    return best[1], best[2], best[3]
