"""浏览器：起一个本地小服务器放播放器和 build/，用无头 Chrome 逐帧截图。"""
import base64, glob, os, platform, shutil, subprocess, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PLAYER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'player')


def find_chrome():
    """找本机的 Chrome / Edge / Chromium；找不到就用 Playwright 自带的 Chromium（需先 playwright install chromium）"""
    env = os.environ.get('MM_CHROME')
    if env and os.path.exists(env):
        return env
    sysname = platform.system()
    cands = []
    if sysname == 'Darwin':
        for root in ['/Applications', os.path.expanduser('~/Applications'), os.path.expanduser('~/Desktop')]:
            for app, exe in [('Google Chrome.app', 'Google Chrome'), ('Chromium.app', 'Chromium'),
                             ('Microsoft Edge.app', 'Microsoft Edge'), ('Brave Browser.app', 'Brave Browser')]:
                cands.append(f'{root}/{app}/Contents/MacOS/{exe}')
        try:   # Spotlight 兜底：Chrome 被放在别的位置时
            r = subprocess.run(['mdfind', "kMDItemCFBundleIdentifier == 'com.google.Chrome'"],
                               capture_output=True, text=True, timeout=5)
            cands += [p + '/Contents/MacOS/Google Chrome' for p in r.stdout.split('\n') if p]
        except Exception:
            pass
    elif sysname == 'Windows':
        for base in [os.environ.get('PROGRAMFILES', ''), os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('LOCALAPPDATA', '')]:
            cands += [os.path.join(base, 'Google/Chrome/Application/chrome.exe'),
                      os.path.join(base, 'Microsoft/Edge/Application/msedge.exe')]
    else:
        for n in ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge']:
            p = shutil.which(n)
            if p:
                cands.append(p)
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


class _Handler(SimpleHTTPRequestHandler):
    roots = {}

    def translate_path(self, path):
        path = path.split('?', 1)[0].split('#', 1)[0]
        from urllib.parse import unquote
        path = unquote(path)
        for prefix, root in self.roots.items():
            if path.startswith(prefix):
                return os.path.join(root, path[len(prefix):].lstrip('/'))
        return os.path.join(PLAYER, path.lstrip('/'))

    def log_message(self, *a):
        pass


class Session:
    """with Session(build_dir) as s: s.page.evaluate(...)"""

    def __init__(self, build_dir=None, width=1080, height=1920):
        self.build_dir = build_dir
        self.size = (width, height)

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        roots = {'/player/': PLAYER}
        if self.build_dir:
            roots['/build/'] = os.path.abspath(self.build_dir)
        handler = type('H', (_Handler,), {'roots': roots})
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.pw = sync_playwright().start()
        exe = find_chrome()
        args = ['--enable-webgl', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']
        try:
            self.browser = self.pw.chromium.launch(executable_path=exe, headless=True, args=args) if exe \
                else self.pw.chromium.launch(headless=True, args=args)
        except Exception as e:
            raise RuntimeError('没找到可用的 Chrome。装一个 Chrome，或设置环境变量 MM_CHROME 指向它，'
                               '或运行：uv run --with playwright playwright install chromium') from e
        self.page = self.browser.new_page(viewport={'width': self.size[0], 'height': self.size[1]})
        self.errors = []
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.on('console', lambda m: self.errors.append(m.text) if m.type == 'error' else None)
        return self

    def open_player(self):
        self.page.goto(f'http://127.0.0.1:{self.port}/player/index.html')
        self.page.wait_for_function('window.__ready === true', timeout=60000)
        if self.errors:
            raise RuntimeError('播放器报错：' + ' | '.join(self.errors))

    def rasterize_svg(self, svg_path, longest=1200):
        """SVG → PNG（PIL Image），用浏览器画，保证和设计软件导出的效果一致"""
        from PIL import Image
        import io
        data = base64.b64encode(open(svg_path, 'rb').read()).decode()
        self.page.goto(f'http://127.0.0.1:{self.port}/player/blank.html')
        url = self.page.evaluate("""async ([d, L]) => {
            const im = new Image(); im.src = 'data:image/svg+xml;base64,' + d; await im.decode();
            let w = im.naturalWidth || 1000, h = im.naturalHeight || 1000; const s = L / Math.max(w, h);
            const c = document.createElement('canvas'); c.width = Math.round(w * s); c.height = Math.round(h * s);
            c.getContext('2d').drawImage(im, 0, 0, c.width, c.height); return c.toDataURL('image/png'); }""", [data, longest])
        return Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))

    def svg_layers(self, svg_path, longest=1200, pad=6):
        """列出 SVG 里所有带 id 的 <g>：位置（换算成拆零件用的工作坐标）和层级，给角色定轴心用"""
        svg = open(svg_path, encoding='utf-8').read()
        self.page.goto(f'http://127.0.0.1:{self.port}/player/blank.html')
        return self.page.evaluate("""([svg, L, pad]) => {
            document.body.innerHTML = svg; const el = document.querySelector('svg');
            const vb = el.viewBox.baseVal; const vw = vb && vb.width ? vb.width : el.width.baseVal.value, vh = vb && vb.height ? vb.height : el.height.baseVal.value;
            const vx = vb ? vb.x : 0, vy = vb ? vb.y : 0, s = L / Math.max(vw, vh);
            const out = [];
            el.querySelectorAll('g[id]').forEach(g => {
              const b = g.getBBox(); let par = g.parentElement.closest('g[id]');
              out.push({ id: g.id, parent: par ? par.id : null,
                box: [b.x, b.y, b.x + b.width, b.y + b.height].map((v, i) => +(((i % 2 ? v - vy : v - vx) * s) + pad).toFixed(1)),
                svgBox: [b.x, b.y, b.x + b.width, b.y + b.height].map(v => +v.toFixed(1)) });
            });
            return { scale: s, viewBox: [vx, vy, vw, vh], layers: out };
        }""", [svg, longest, pad])

    def rasterize_svg_layers(self, svg_path, show=None, hide=(), longest=1200):
        """只显示某些组 / 隐藏某些组后再栅格化（角色零件逐个导出用）。show=None 表示全显示"""
        from PIL import Image
        import io
        svg = open(svg_path, encoding='utf-8').read()
        self.page.goto(f'http://127.0.0.1:{self.port}/player/blank.html')
        url = self.page.evaluate("""async ([svg, L, show, hide]) => {
            const doc = new DOMParser().parseFromString(svg, 'image/svg+xml'); const el = doc.documentElement;
            const all = [...el.querySelectorAll('g[id]')];
            if (show) {
              // 只留 show 里的组：其他叶子内容一律隐藏（祖先保留但把它们自己的直接图形藏起来）
              const keep = new Set(show);
              const shown = all.filter(g => keep.has(g.id));
              el.querySelectorAll('*').forEach(n => {
                if (!(n instanceof SVGGraphicsElement) || n.tagName === 'svg') return;
                const inShown = shown.some(g => g === n || g.contains(n));
                const isAncestor = shown.some(g => n.contains(g));
                if (!inShown && !isAncestor) n.setAttribute('display', 'none');
              });
            }
            for (const id of hide) { const g = el.querySelector('#' + CSS.escape(id)); if (g) g.setAttribute('display', 'none'); }
            const txt = new XMLSerializer().serializeToString(el);
            const im = new Image(); im.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(txt))); await im.decode();
            const vb = el.viewBox && el.viewBox.baseVal && el.viewBox.baseVal.width ? el.viewBox.baseVal : null;
            let w = vb ? vb.width : (im.naturalWidth || 1000), h = vb ? vb.height : (im.naturalHeight || 1000); const s = L / Math.max(w, h);
            const c = document.createElement('canvas'); c.width = Math.round(w * s); c.height = Math.round(h * s);
            c.getContext('2d').drawImage(im, 0, 0, c.width, c.height); return c.toDataURL('image/png'); }""", [svg, longest, show, list(hide)])
        return Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))

    def rasterize_text(self, spec, longest=1200):
        """一段文字 → PNG（用本机字体）。spec: lines / font / weight / color / tracking / arc(度) / lineGap / italic"""
        from PIL import Image
        import io
        self.page.goto(f'http://127.0.0.1:{self.port}/player/blank.html')
        url = self.page.evaluate("""async ([sp, L]) => {
            const lines = sp.lines || [sp.text || 'TEXT'];
            const size = 200, weight = sp.weight || 800, fam = sp.font || '"Helvetica Neue", Helvetica, Arial, "PingFang SC", "Hiragino Sans GB", sans-serif';
            const font = `${sp.italic ? 'italic ' : ''}${weight} ${size}px ${fam}`;
            await document.fonts.load(font, lines.join(''));
            const c = document.createElement('canvas'), ctx = c.getContext('2d');
            ctx.font = font;
            const track = (sp.tracking || 0) * size, gap = (sp.lineGap ?? 0.15) * size;
            const sizes = lines.map((l, i) => (sp.sizes ? sp.sizes[i] : 1));
            const widths = lines.map((l, i) => { ctx.font = `${sp.italic ? 'italic ' : ''}${weight} ${size * sizes[i]}px ${fam}`; return [...l].reduce((a, ch) => a + ctx.measureText(ch).width + track * sizes[i], -track * sizes[i]); });
            const arc = (sp.arc || 0) * Math.PI / 180;
            const W = Math.max(...widths) + size, Hh = sizes.reduce((a, s) => a + s * size * 1.15 + gap, 0) + size * (arc ? 1.2 : 0.4);
            c.width = Math.ceil(W); c.height = Math.ceil(Hh);
            ctx.fillStyle = sp.color || '#1b1b1b'; ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'center';
            let y = size * 0.2 + (arc ? size * 0.6 : 0);
            lines.forEach((l, i) => {
              const fs = size * sizes[i]; ctx.font = `${sp.italic ? 'italic ' : ''}${weight} ${fs}px ${fam}`;
              y += fs * 1.0;
              const a = (i === 0 ? arc : 0);
              if (!a) {
                let x = W / 2 - widths[i] / 2;
                for (const ch of l) { const w = ctx.measureText(ch).width; ctx.fillText(ch, x + w / 2, y); x += w + track * sizes[i]; }
              } else {
                const R = widths[i] / a; let ang = -a / 2;
                const cx = W / 2, cy = y + R;
                for (const ch of l) {
                  const w = ctx.measureText(ch).width, da = (w + track * sizes[i]) / R;
                  const m = ang + (w / R) / 2;
                  ctx.save(); ctx.translate(cx + Math.sin(m) * R, cy - Math.cos(m) * R); ctx.rotate(m); ctx.fillText(ch, 0, 0); ctx.restore();
                  ang += da;
                }
              }
              y += fs * 0.15 + gap;
            });
            return c.toDataURL('image/png'); }""", [spec, longest])
        return Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))

    def __exit__(self, *a):
        try:
            self.browser.close(); self.pw.stop()
        finally:
            self.httpd.shutdown()


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode_dataurl(u):
    return base64.b64decode(u.split(',', 1)[1])
