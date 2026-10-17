// mockup-motion 播放器：每一帧都是时间 t 的纯函数。
// 每块图形先在自己的「平面画布」（表面摊平后的正面）里按动法画好，再由 WebGL 按透视贴回底图、吃到表面明暗。
(() => {
const S = window.SCENE;
const W = S.W, H = S.H, DUR = S.duration;

// ---------- 小工具 ----------
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const E = {
  inOutSine: x => -(Math.cos(Math.PI * clamp(x)) - 1) / 2,
  outCubic: x => 1 - Math.pow(1 - clamp(x), 3),
  inCubic: x => Math.pow(clamp(x), 3),
};
// 阻尼弹簧 0→1（过冲大小由 zeta 决定）
function spring(tau, zeta = 0.45, w = 22) {
  if (tau <= 0) return 0;
  const wd = w * Math.sqrt(1 - zeta * zeta);
  return 1 - Math.exp(-zeta * w * tau) * (Math.cos(wd * tau) + (zeta * w / wd) * Math.sin(wd * tau));
}
const b64u8 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const hash = (a, b = 0) => { const x = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return x - Math.floor(x); };
const rgbStr = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const load = src => new Promise((ok, bad) => { const im = new Image(); im.onload = () => im.decode().then(() => ok(im)); im.onerror = () => bad(new Error('加载失败 ' + src)); im.src = src; });
const mk = (w, h) => { const c = document.createElement('canvas'); c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h)); return c; };

// ---------- 音效事件 & 镜头震动 ----------
const EVENTS = [], SHAKES = [], IMPACTS = [];
const impact = (t, gi, xf, amp = 1) => IMPACTS.push({ t, gi, xf, amp });    // xf：冲击点在图形宽度上的比例
function punchAt(t) {     // 冲击时镜头往里推一点再回来
  let z = 0;
  for (const s of IMPACTS) { const k = t - s.t; if (k > 0 && k < 0.8) z += 0.012 * s.amp * Math.exp(-k * 7) * Math.sin(Math.min(k * 14, Math.PI / 2)); }
  return z;
}
// 已经出现的元素被别人的冲击震一下：按离冲击点的远近依次跳起来（涟漪）
function reactXf(gi, t, after, cx, h, artW) {
  let dy = 0, sq = 0;
  for (const s of IMPACTS) {
    if (s.t < after + 0.05) continue;
    const k = t - s.t - Math.abs(cx / artW - s.xf) * 0.18 - (s.gi !== gi ? 0.04 : 0);
    if (k <= 0 || k > 0.7) continue;
    dy -= s.amp * h * 0.07 * Math.max(0, Math.sin(k * 16)) * Math.exp(-k * 7);
    sq += s.amp * 0.05 * Math.exp(-k * 9) * Math.cos(k * 26);
  }
  return { dy, sy: 1 - sq, sx: 1 + sq * 0.6, ax: cx, ay: null };
}
const ev = (t, kind, extra = {}) => EVENTS.push(Object.assign({ t: +t.toFixed(4), kind }, extra));
function shakeAt(t) {
  let x = 0, y = 0;
  for (const s of SHAKES) { const k = t - s.t; if (k < 0 || k > 0.6) continue; const a = s.amp * Math.exp(-k * 13); x += a * Math.sin(k * 71); y += a * Math.cos(k * 59); }
  return [x, y];
}

// ---------- 动法默认参数 ----------
const DEF = {
  draw: { dur: 1.3 }, write: {}, outline: { dur: 1.6 },
  pop: { step: 0.06 }, slam: { step: 0.12 }, type: { step: 0.035 },
  drop: { dur: 1.0, spin: 0.45 }, grow: { dur: 0.7 }, popin: { dur: 0.6 }, peek: { dur: 1.3, from: 'bottom' },
  peel: { dur: 1.0, from: 'tl', backing: '#f3f2ee' }, slide: { dur: 0.8, from: 'top' }, fade: { dur: 0.45 },
  flicker: { step: 0.12, itemDur: 0.55 }, static: { dur: 0 },
  splat: { dur: 1.3 }, drip: { dur: 1.1 }, melt: { dur: 1.0 }, slidein: { dur: 1.2, from: 'left' }, roll: { dur: 1.6, from: 'left' }, flutter: { dur: 1.6 }, bounce: { step: 0.08 }, stamp: { dur: 0.6 }, pixels: { dur: 1.7 },
};
const startOf = (g, k, n) => {
  if (!['pop', 'bounce', 'slam'].includes(g.motion) || n < 3 || g.stagger === 'even') return k * g.step;
  const span = (n - 1) * g.step * 1.05;
  return span * (1 - Math.pow(1 - k / (n - 1), 1.45));
};
function lengthOf(g) {
  const n = g.items ? g.items.length : 1;
  switch (g.motion) {
    case 'pop': return (n - 1) * g.step + 0.45;
    case 'slam': return (n - 1) * g.step + 0.4;
    case 'type': return (n - 1) * g.step + 0.06;
    case 'flicker': return (n - 1) * g.step + g.itemDur;
    case 'bounce': return (n - 1) * g.step + 0.7;
    default: return g.dur;
  }
}

// ---------- 画精灵（锚点变换） ----------
function drawXf(ctx, img, it, xf = {}) {
  const { s = 1, sx = 1, sy = 1, rot = 0, dx = 0, dy = 0, alpha = 1, ax = it.cx, ay = it.cy } = xf;
  if (alpha <= 0 || s * sx <= 0 || s * sy <= 0) return;
  ctx.save();
  ctx.globalAlpha *= alpha;
  ctx.translate(ax + dx, ay + dy);
  ctx.rotate(rot);
  ctx.scale(s * sx, s * sy);
  ctx.translate(-ax, -ay);
  ctx.drawImage(img, it.x, it.y, it.w, it.h);
  ctx.restore();
}
// 变形绘制：按水平切片画，每片宽度、左右偏移不同 → 压扁时底下鼓、甩动像果冻、拖拽时往后倾
// df: { bulge 底宽顶窄, belly 中间鼓, lean 顶部偏移(图形像素), wave: {amp, len, phase} 横向波动 }
function drawDef(ctx, img, it, xf, df) {
  if (!df || (!df.bulge && !df.belly && !df.lean && !(df.wave && df.wave.amp))) return drawXf(ctx, img, it, xf);
  const { s = 1, sx = 1, sy = 1, rot = 0, dx = 0, dy = 0, alpha = 1, ax = it.cx, ay = it.cy } = xf;
  if (alpha <= 0 || s * sx <= 0 || s * sy <= 0) return;
  ctx.save();
  ctx.globalAlpha *= alpha;
  ctx.translate(ax + dx, ay + dy); ctx.rotate(rot); ctx.scale(s * sx, s * sy); ctx.translate(-ax, -ay);
  const n = Math.max(24, Math.min(140, Math.round(it.h / 3))), hs = it.h / n, sh = img.height / n;
  const cx = it.cx;
  for (let i = 0; i < n; i++) {
    const v = (i + 0.5) / n;                         // 0 顶 → 1 底
    const kx = Math.max(0.05, 1 + (df.bulge || 0) * (v - 0.5) + (df.belly || 0) * Math.sin(Math.PI * v));
    let off = (df.lean || 0) * Math.pow(1 - v, 2);
    if (df.wave && df.wave.amp) off += df.wave.amp * Math.sin(2 * Math.PI * v / (df.wave.len || 0.8) + (df.wave.phase || 0)) * (1 - v);
    const x0 = cx - (cx - it.x) * kx + off;
    ctx.drawImage(img, 0, i * sh, img.width, sh + 0.6, x0, it.y + i * hs, it.w * kx, hs + 0.6);
  }
  ctx.restore();
}

// 待机小动作：整块
function idleXf(kind, t, it) {
  const r = 1;
  switch (kind) {
    case 'breathe': { const v = Math.sin(2 * Math.PI * t / 1.7) * r; return { sy: 1 + 0.018 * v, sx: 1 - 0.009 * v, ax: it.cx, ay: it.y + it.h }; }
    case 'soft': { const v = Math.sin(2 * Math.PI * t / 2.1) * r; return { sy: 1 + 0.008 * v, sx: 1 - 0.004 * v, rot: 0.006 * Math.sin(2 * Math.PI * t / 2.9), ax: it.cx, ay: it.y + it.h }; }
    case 'wobble': return { rot: 0.025 * Math.sin(2 * Math.PI * t / 1.4) * r, ax: it.cx, ay: it.y + it.h };
    case 'bob': return { dy: -0.025 * it.h * (0.5 + 0.5 * Math.sin(2 * Math.PI * t / 1.5)) * r };
    case 'jelly': { const v = Math.sin(2 * Math.PI * t / 1.5); return { sy: 1 + 0.02 * v, sx: 1 - 0.012 * v, ax: it.cx, ay: it.y + it.h, _df: { lean: 0.035 * it.h * Math.sin(2 * Math.PI * t / 1.9), belly: 0.02 * v } }; }
    default: return {};
  }
}
const mergeXf = (a, b) => ({ s: (a.s ?? 1) * (b.s ?? 1), sx: (a.sx ?? 1) * (b.sx ?? 1), sy: (a.sy ?? 1) * (b.sy ?? 1),
  rot: (a.rot ?? 0) + (b.rot ?? 0), dx: (a.dx ?? 0) + (b.dx ?? 0), dy: (a.dy ?? 0) + (b.dy ?? 0),
  alpha: (a.alpha ?? 1) * (b.alpha ?? 1), ax: a.ax ?? b.ax, ay: a.ay ?? b.ay });

// ---------- 沿笔画画出来：到达时间图 → 遮罩 ----------
function floodLayer(f) {
  const w = f.w, h = f.h;
  const T = new Uint16Array(b64u8(f.time).buffer);
  const mask = mk(w, h), mctx = mask.getContext('2d'), id = mctx.createImageData(w, h);
  const tmp = mk(w, h), tctx = tmp.getContext('2d');
  return {
    reveal(p, soft = 0.02, Tm = T, out = id, outCtx = mctx, outCanvas = mask, A = null, rgb = null) {
      const e = p * (1 + soft) * 65535, s = soft * 65535, d = out.data;
      for (let i = 0; i < Tm.length; i++) {
        const v = (e - Tm[i]) / s, a = v <= 0 ? 0 : (v >= 1 ? 1 : v);
        if (rgb) { d[i * 4] = rgb[0]; d[i * 4 + 1] = rgb[1]; d[i * 4 + 2] = rgb[2]; }
        d[i * 4 + 3] = (A ? A[i] : 255) * a;
      }
      outCtx.putImageData(out, 0, 0);
      return outCanvas;
    },
    through(sprite, m) {
      tctx.globalCompositeOperation = 'copy'; tctx.drawImage(sprite, 0, 0);
      tctx.globalCompositeOperation = 'destination-in'; tctx.drawImage(m, 0, 0);
      tctx.globalCompositeOperation = 'source-over';
      return tmp;
    },
  };
}

// ---------- 各动法 ----------
function makeGroup(g, img, items) {
  const t0 = g.at, t1 = g.at + lengthOf(g);
  g.t1 = t1;
  const M = g.motion, sp = g.sprite;
  const sfx = g.sfx !== false;
  const idleKind = g.idle === undefined ? 'soft' : g.idle;     // 默认带一点点呼吸，不让画面死掉
  const idle = (ctx, t, base) => {          // 动画结束后的待机 + 被别人震一下
    const k = t - t1;
    const xf = k > 0 && idleKind && idleKind !== 'none' ? idleXf(idleKind, k, sp) : {};
    if (k > 0 && idleKind) { const ramp = clamp(k / 0.3); for (const key of ['sy', 'sx']) if (xf[key] != null) xf[key] = 1 + (xf[key] - 1) * ramp; if (xf.rot) xf.rot *= ramp; if (xf.dy) xf.dy *= ramp; }
    const rx = reactXf(g._gi, t, t1, sp.cx, sp.h, g._art[0]); rx.ay = sp.y + sp.h;
    // 冲击过后的余震：左右甩几下（果冻）
    let df = xf._df || base?._df || null;
    for (const im of IMPACTS) { const k = t - im.t; if (k > 0 && k < 1 && im.t > t1 + 0.05) { df = Object.assign({}, df); df.lean = (df.lean || 0) + im.amp * 0.05 * sp.h * Math.sin(k * 15) * Math.exp(-k * 5); } }
    drawDef(ctx, img, sp, mergeXf(mergeXf(base || {}, xf), rx), df);
  };

  if (M === 'draw' || M === 'write') {
    const fl = floodLayer(g.flood);
    if (sfx) ev(t0, 'scratch', { dur: g.dur, gain: 0.9 });
    const ez = g.ease ? (EASES[g.ease] || E.inOutSine) : E.inOutSine;     // 液体铺开用 ease-out：先快后慢，滴往下越流越慢
    return ctx => t => {
      const u = (t - t0) / g.dur;
      if (u <= 0) return;
      if (u >= 1) return idle(ctx, t);
      const p = ez(u);
      const m = fl.reveal(p, M === 'write' ? 0.012 : 0.02);
      const f = g.flood;
      if (g.rig) {                // 角色：从整块画布里裁出时间图那一块
        if (!g._crop) g._crop = mk(f.w, f.h);
        const cc = g._crop.getContext('2d'); cc.clearRect(0, 0, f.w, f.h); cc.drawImage(img, f.x - sp.x, f.y - sp.y, f.w, f.h, 0, 0, f.w, f.h);
        ctx.drawImage(fl.through(g._crop, m), f.x, f.y);
      } else ctx.drawImage(fl.through(img, m), sp.x, sp.y);
      if (g.tipPath) {          // 发光笔头：跟着刚画到的地方走
        const P = g.tipPath, f = p * (P.length - 1), i = Math.min(P.length - 2, Math.floor(f)), k = f - i;
        const x = P[i][0] + (P[i + 1][0] - P[i][0]) * k, y = P[i][1] + (P[i + 1][1] - P[i][1]) * k;
        const r = g.tipSize || Math.max(10, 0.06 * Math.min(sp.w, sp.h));
        const col = g.tipColor || [255, 255, 255];
        const gr = ctx.createRadialGradient(x, y, 0, x, y, r);
        gr.addColorStop(0, rgbStr(col, 1)); gr.addColorStop(0.25, rgbStr(col, 0.8)); gr.addColorStop(1, rgbStr(col, 0));
        ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.restore();
      }
    };
  }

  if (M === 'outline') {
    const f = g.flood, n = f.w * f.h;
    const fl = floodLayer(f);
    const bandA = b64u8(f.band), bandT = new Uint16Array(b64u8(f.bandTime).buffer), sil = b64u8(f.sil);
    const bandC = mk(f.w, f.h), bctx = bandC.getContext('2d'), bid = bctx.createImageData(f.w, f.h);
    const hex = h => typeof h === 'string' ? [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)) : h;
    const fillC = hex(g.fillColor || f.fill), bandC2 = hex(g.bandColor || f.fill);   // 轮廓线色、剪影填充色可以分开指定（深色玻璃上：白线 + 深色填充）
    const silC = mk(f.w, f.h); { const c = silC.getContext('2d'), d = c.createImageData(f.w, f.h); for (let i = 0; i < n; i++) { d.data[i * 4] = fillC[0]; d.data[i * 4 + 1] = fillC[1]; d.data[i * 4 + 2] = fillC[2]; d.data[i * 4 + 3] = sil[i]; } c.putImageData(d, 0, 0); }
    if (sfx) { ev(t0, 'scratch', { dur: g.dur * 0.42, gain: 0.8 }); ev(t0 + g.dur * 0.44, 'pop', { pitch: 0.8, gain: 0.7 }); }
    return ctx => t => {
      const u = (t - t0) / g.dur;
      if (u <= 0) return;
      if (u >= 1) return idle(ctx, t);
      const fillA = clamp((u - 0.42) / 0.14) * (1 - clamp((u - 0.62) / 0.3));
      if (u > 0.55) ctx.drawImage(img, sp.x, sp.y);
      if (fillA > 0) { ctx.save(); ctx.globalAlpha = fillA; const s = 0.97 + 0.03 * clamp((u - 0.42) / 0.14); ctx.translate(sp.cx, sp.cy); ctx.scale(s, s); ctx.translate(-sp.cx, -sp.cy); ctx.drawImage(silC, f.x, f.y); ctx.restore(); }
      const bandAlpha = 1 - clamp((u - 0.62) / 0.3);
      if (bandAlpha > 0) {
        const b = fl.reveal(E.inOutSine(u / 0.45), 0.02, bandT, bid, bctx, bandC, bandA, bandC2);
        ctx.save(); ctx.globalAlpha = bandAlpha; ctx.drawImage(b, f.x, f.y); ctx.restore();
      }
    };
  }

  if (M === 'pop' || M === 'slam' || M === 'type' || M === 'flicker' || M === 'bounce') {
    const n = items.length;
    const fallBase = g._art[1] * 0.35;
    items.forEach((it, k) => {
      const ts = t0 + startOf(g, k, n);
      if (M === 'bounce') { SHAKES.push({ t: ts + 0.32, amp: 1.5 }); impact(ts + 0.32, g._gi, it.cx / g._art[0], 0.35); }
      if (M === 'slam') impact(ts + 0.17, g._gi, it.cx / g._art[0], 0.8);
      if (!sfx) return;
      if (M === 'pop') ev(ts, 'pop', { pitch: 0.85 + 0.55 * (n > 1 ? k / (n - 1) : 0), gain: 0.75 });
      if (M === 'type' && (k < 3 || k % 3 === 0) && k < 18) ev(ts, 'tick', { gain: 0.6 });
      if (M === 'slam') { ev(ts + 0.17, 'slam', { gain: 0.9 }); SHAKES.push({ t: ts + 0.17, amp: 5 }); }
      if (M === 'flicker') { ev(ts + 0.05, 'zap', { gain: 0.7 }); ev(ts + g.itemDur * 0.75, 'zap', { gain: 0.9 }); }
      if (M === 'bounce') ev(ts + 0.32, 'knock', { pitch: 0.9 + 0.3 * hash(k), gain: 0.8 });
    });
    // 霓虹通电：每个零件自己一段闪烁，最后常亮
    const flick = (u, k) => {
      if (u >= 1) return 1;
      const slot = Math.floor(u * 11);
      return hash(k * 13.1, slot) < (slot + 2) / 11 ? 0.65 + 0.35 * hash(k, slot + 50) : 0.04;
    };
    if (M === 'slam' && !sfx) items.forEach((it, k) => SHAKES.push({ t: t0 + k * g.step + 0.17, amp: 5 }));
    return ctx => t => {
      items.forEach((it, k) => {
        const tau = t - (t0 + startOf(g, k, n));
        if (tau <= 0) return;
        let xf;
        if (M === 'pop') {
          const s = spring(tau, 0.42 + 0.08 * hash(k, 5), 22 * (0.85 + 0.3 * hash(k, 4))), dir = k % 2 ? 1 : -1;
          xf = { s: Math.max(s, 0), rot: dir * 0.25 * (1 - s), dy: 0.15 * it.h * (1 - s), alpha: clamp(tau / 0.05), _df: { belly: (s - 1) * 0.6, lean: dir * 0.08 * it.h * (1 - s) } };
        } else if (M === 'slam') {
          const u = tau / 0.17;
          if (u < 1) xf = { s: 2.3 - 1.3 * E.inCubic(u), alpha: E.outCubic(u * 1.6) };
          else { const k2 = tau - 0.17; xf = { s: 1 - 0.07 * Math.exp(-k2 * 12) * Math.cos(k2 * 38) }; }
        } else if (M === 'flicker') {
          xf = { alpha: flick(tau / g.itemDur, k) };
        } else if (M === 'bounce') {
          const tf = 0.32, fall = it.y + it.h * 1.2 + fallBase;
          if (tau < tf) { const u = tau / tf; xf = { dy: -fall * (1 - u * u), sy: 1 + 0.06 * u, sx: 1 - 0.03 * u, ax: it.cx, ay: it.y + it.h }; }
          else { const k2 = tau - tf, sq = 0.24 * Math.exp(-k2 * 9) * Math.cos(k2 * 24); xf = { sy: 1 - sq, sx: 1 + 0.6 * sq, ax: it.cx, ay: it.y + it.h, _df: { bulge: sq * 1.2, lean: 0.07 * it.h * Math.sin(k2 * 16) * Math.exp(-k2 * 5) } }; }
        } else xf = { alpha: clamp(tau / 0.05) };
        if (g.idle === 'wave' && t > t1) xf.dy = (xf.dy || 0) + Math.sin(2 * Math.PI * 0.9 * (t - t1) - k * 0.6) * 0.035 * it.h * clamp((t - t1) / 0.3);
        const itEnd = t0 + startOf(g, k, n) + 0.45;
        const rx = reactXf(g._gi, t, itEnd, it.cx, it.h, g._art[0]); rx.ay = it.y + it.h;
        const df = xf._df;
        if (rx.dy || rx.sy !== 1) xf = mergeXf(xf, rx);
        drawDef(ctx, it.img, it, xf, df);
      });
      if (M === 'type' && g.cursor) {     // 打字光标：跟在最后一个字后面，打完再闪几下
        const shown = items.filter((it, k) => t >= t0 + k * g.step);
        const after = t - t1;
        if (shown.length && after < 1.6 && (after < 0 || Math.floor(after * 3.2) % 2 === 0)) {
          const it = shown[shown.length - 1];
          const line = items.filter(x => x.line === it.line);
          const top = Math.min(...line.map(x => x.y)), bot = Math.max(...line.map(x => x.y + x.h));
          const h = bot - top, w = Math.max(2, h * 0.09);
          ctx.fillStyle = rgbStr(g.cursorColor || g._ink);
          ctx.fillRect(it.x + it.w + w * 0.8, top, w, h);
        }
      }
    };
  }

  if (M === 'drop') {
    const tf = (g.fallFrac ?? 0.42) * g.dur, fall = g.fall != null ? g.fall * g._art[1] : sp.y + sp.h * 1.15;
    if (sfx) { ev(t0, 'whoosh', { dur: tf, gain: 0.7 }); ev(t0 + tf, 'thud', { gain: 1 }); }
    SHAKES.push({ t: t0 + tf, amp: 7 }); impact(t0 + tf, g._gi, sp.cx / g._art[0], 1);
    const SQ = (g.squash ?? 0.34) / 0.34;     // 角色自己有落地蹲姿时把代码压扁调小（squash 0.1 左右），不然会压两遍
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      const base = { ax: sp.cx, ay: sp.y + sp.h };
      if (tau < tf) { const u = tau / tf; drawDef(ctx, img, sp, { ...base, dy: -fall * (1 - u * u), dx: (g.dx0 || 0) * g._art[0] * (1 - u), rot: g.spin * (1 - u), sy: 1 + 0.18 * u * SQ, sx: 1 - 0.1 * u * SQ }, { bulge: -0.25 * u * SQ }); }
      else { const k = tau - tf, sq = 0.34 * SQ * Math.exp(-k * 8) * Math.cos(k * 22);
        drawDef(ctx, img, sp, { ...base, sy: 1 - sq, sx: 1 + 0.75 * sq, dy: -sp.h * 0.1 * Math.max(0, Math.sin(k * 8)) * Math.exp(-k * 5) },
          { bulge: sq * 1.3, belly: sq * 0.4, lean: 0.06 * sp.h * Math.sin(k * 15) * Math.exp(-k * 4.5) }); }
    };
  }

  if (M === 'grow') {
    if (sfx) ev(t0, 'boing', { gain: 0.8 });
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      const s = spring(tau, 0.42, 17);
      drawXf(ctx, img, sp, { sy: Math.max(s, 0), sx: 1 + 0.25 * (1 - s), ax: sp.cx, ay: sp.y + sp.h, alpha: clamp(tau / 0.04) });
    };
  }

  if (M === 'peek' || M === 'slide') {
    const from = g.from;
    const clip = g.clip === 'art' ? { x: 0, y: 0, w: g._art[0], h: g._art[1] } : g.clip === 'none' ? null
      : g.clip === 'body' && g.body ? { x: sp.x, y: sp.y, w: sp.w, h: g.body[3] - sp.y + 2 }       // 从自己的地面线后面冒出来
      : { x: sp.x - 2, y: sp.y - 2, w: sp.w + 4, h: sp.h + 4 };
    const cl = clip || { x: sp.x, y: sp.y, w: sp.w, h: g.body ? g.body[3] - sp.y + 4 : sp.h };   // 不额外裁切时（靠表面边缘遮住），位移按角色本身算
    const ph2 = (g.riseDur ?? 0.6) + (g.hold ?? 0.9);
    if (sfx) { ev(t0, 'whoosh', { dur: 0.4, gain: 0.35 }); if (M === 'peek') ev(t0 + ph2, 'boing', { gain: 0.7 }); }
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      let v = { bottom: [0, cl.h], top: [0, -cl.h], left: [-cl.w, 0], right: [cl.w, 0] }[from] || [0, cl.h];
      let osMax = Infinity;
      if (!clip && g._surfArt) {      // 靠表面边缘遮住：位移按「刚好藏到表面边缘外」算（窗户里探头）
        const b = g.body || [sp.x, sp.y, sp.x + sp.w, sp.y + sp.h], S2 = g._surfArt;
        v = { bottom: [0, Math.max(0, S2[3] - b[1])], top: [0, -Math.max(0, b[3] - S2[1])], left: [-Math.max(0, b[2] - S2[0]), 0], right: [Math.max(0, S2[2] - b[0]), 0] }[from] || v;
        // 蹦出过冲不能把藏在窗沿后面的底边带出来（不然底边会浮在窗沿上几帧、又被切平）：
        // 过冲上限 = 稳住时底边藏在窗沿后的深度 − 安全量（角色高 5% + 6px：白描边、换姿势那一下压缩都会多露一点）
        const hid = { bottom: b[3] - S2[3], top: S2[1] - b[1], left: S2[0] - b[0], right: b[2] - S2[2] }[from] ?? 0, L = Math.hypot(v[0], v[1]);
        const pad = 0.05 * (from === 'left' || from === 'right' ? b[2] - b[0] : b[3] - b[1]) + 6;
        if (hid > 0 && L > 0) osMax = Math.max(0, hid - pad) / L;
      }
      let r;
      if (M === 'peek') {
        // 照参考片量的：慢慢升到 rise（先快后慢）→ 停 hold 秒张望 → 0.1 秒冲到 1.16 → 每 0.4 秒回弹一次稳住
        const rise = g.rise ?? 0.58, rd = g.riseDur ?? 0.6, hold = g.hold ?? 0.9, pd = g.popDur ?? 0.11, os = Math.min(g.overshoot ?? 0.16, osMax);
        if (tau < rd) r = rise * E.outCubic(tau / rd);
        else if (tau < rd + hold) r = rise + 0.008 * Math.sin((tau - rd) * 9);
        else if (tau < rd + hold + pd) { const u = (tau - rd - hold) / pd; r = rise + (1 + os - rise) * (u * u * (3 - 2 * u)); }
        else { const k = tau - rd - hold - pd; r = 1 + os * Math.exp(-k * 2.3) * Math.cos(k * 15); }
      }
      else r = spring(tau, 0.6, 13);
      const off = 1 - r;
      ctx.save(); if (clip) { ctx.beginPath(); ctx.rect(clip.x, clip.y, clip.w, clip.h); ctx.clip(); }
      drawXf(ctx, img, sp, { dx: v[0] * off, dy: v[1] * off });
      ctx.restore();
    };
  }

  if (M === 'peel') {
    const back = mk(sp.w, sp.h); { const c = back.getContext('2d'); c.drawImage(img, 0, 0); c.globalCompositeOperation = 'source-in'; c.fillStyle = g.backing; c.fillRect(0, 0, sp.w, sp.h); }
    const corner = { tl: [sp.x, sp.y], tr: [sp.x + sp.w, sp.y], bl: [sp.x, sp.y + sp.h], br: [sp.x + sp.w, sp.y + sp.h] }[g.from] || [sp.x, sp.y];
    let dx = sp.x + sp.w / 2 - corner[0], dy = sp.y + sp.h / 2 - corner[1];
    const dl = Math.hypot(dx, dy); dx /= dl; dy /= dl;
    const L = Math.abs(sp.w * dx) + Math.abs(sp.h * dy);
    if (sfx) ev(t0, 'peel', { dur: g.dur, gain: 0.9 });
    const half = (ctx, P, sgn) => {     // 线的一侧（sgn<0：已经贴上的那侧）
      const nx = -dy, ny = dx, B = 1e4;
      ctx.beginPath();
      ctx.moveTo(P[0] + nx * B, P[1] + ny * B); ctx.lineTo(P[0] - nx * B, P[1] - ny * B);
      ctx.lineTo(P[0] - nx * B + sgn * dx * B, P[1] - ny * B + sgn * dy * B); ctx.lineTo(P[0] + nx * B + sgn * dx * B, P[1] + ny * B + sgn * dy * B);
      ctx.closePath();
    };
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      const p = E.inOutSine(tau / g.dur);
      const s = p * L * 1.02;
      const P = [corner[0] + dx * s, corner[1] + dy * s];
      // 已贴上的部分
      ctx.save(); half(ctx, P, -1); ctx.clip(); ctx.drawImage(img, sp.x, sp.y, sp.w, sp.h);
      // 折线旁的一点阴影
      const gr = ctx.createLinearGradient(P[0], P[1], P[0] - dx * 18, P[1] - dy * 18);
      gr.addColorStop(0, 'rgba(0,0,0,0.22)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.globalCompositeOperation = 'source-atop'; ctx.fillStyle = gr; ctx.fillRect(sp.x - 50, sp.y - 50, sp.w + 100, sp.h + 100);
      ctx.restore();
      // 翻起来的背纸：线外侧一条带，沿折线镜像到内侧
      const f = Math.min(s, 0.3 * L) * (1 - p * 0.85);
      if (f > 0.5) {
        const pd = P[0] * dx + P[1] * dy;
        ctx.save();
        ctx.transform(1 - 2 * dx * dx, -2 * dx * dy, -2 * dx * dy, 1 - 2 * dy * dy, 2 * pd * dx, 2 * pd * dy);
        half(ctx, P, 1); ctx.clip();
        const Q = [P[0] + dx * f, P[1] + dy * f]; half(ctx, Q, -1); ctx.clip();
        ctx.drawImage(back, sp.x, sp.y);
        const sh = ctx.createLinearGradient(P[0], P[1], Q[0], Q[1]);
        sh.addColorStop(0, 'rgba(0,0,0,0.18)'); sh.addColorStop(1, 'rgba(255,255,255,0.15)');
        ctx.globalCompositeOperation = 'source-atop'; ctx.fillStyle = sh; ctx.fillRect(sp.x - 50, sp.y - 50, sp.w + 100, sp.h + 100);
        ctx.restore();
      }
    };
  }

  if (M === 'stamp') {        // 盖章：从上面压下来，落下那一下闷响
    const th = 0.16;
    if (sfx) ev(t0 + th, 'knock', { pitch: 0.7, gain: 1 });
    SHAKES.push({ t: t0 + th, amp: 2.5 }); impact(t0 + th, g._gi, sp.cx / g._art[0], 0.6);
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      let xf;
      if (tau < th) { const u = tau / th; xf = { s: 1.3 - 0.3 * E.inCubic(u), alpha: clamp(u * 2.5), rot: -0.06 * (1 - u) }; }
      else { const k = tau - th; xf = { s: 1 - 0.025 * Math.exp(-k * 14) * Math.cos(k * 30) }; }
      drawXf(ctx, img, sp, xf);
    };
  }

  if (M === 'pixels') {       // 像素块从四周飞来拼出图形，再变清晰（AI 生成感）
    const cell = g.cell || Math.max(6, Math.round(Math.max(sp.w, sp.h) / 70));
    const c = mk(sp.w, sp.h), cx = c.getContext('2d', { willReadFrequently: true });
    cx.drawImage(img, 0, 0);
    const d = cx.getImageData(0, 0, sp.w, sp.h).data;
    const cells = [];
    for (let y = 0; y < sp.h; y += cell) for (let x = 0; x < sp.w; x += cell) {
      let a = 0, r = 0, gg = 0, b = 0, n = 0;
      for (let yy = y; yy < Math.min(y + cell, sp.h); yy += 2) for (let xx = x; xx < Math.min(x + cell, sp.w); xx += 2) {
        const i = (yy * sp.w + xx) * 4; a += d[i + 3]; r += d[i] * d[i + 3]; gg += d[i + 1] * d[i + 3]; b += d[i + 2] * d[i + 3]; n++;
      }
      if (a / n < 90) continue;
      const k = cells.length;
      cells.push({ x: sp.x + x, y: sp.y + y, col: `rgb(${r / a | 0},${gg / a | 0},${b / a | 0})`,
        sx: sp.x + x + (hash(k, 1) - 0.5) * sp.w * 1.3, sy: sp.y + y + (hash(k, 2) - 0.6) * sp.h * 2.6,
        delay: 0.5 * (x / sp.w) + 0.12 * hash(k, 3) });
    }
    if (sfx) ev(t0, 'shimmer', { dur: g.dur * 0.7, gain: 1 });
    const fly = 0.42 * g.dur, settle = 0.8 * g.dur;
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      const crisp = clamp((tau - settle) / (g.dur - settle));
      if (crisp < 1) {
        ctx.save(); ctx.globalAlpha = 1 - crisp;
        for (const q of cells) {
          const u = clamp((tau - q.delay * g.dur * 0.5) / fly);
          if (u <= 0) continue;
          const e = E.outCubic(u), sz = cell * (0.55 + 0.4 * e);
          ctx.fillStyle = q.col;
          ctx.globalAlpha = (1 - crisp) * clamp(u * 3);
          ctx.fillRect(q.sx + (q.x - q.sx) * e + (cell - sz) / 2, q.sy + (q.y - q.sy) * e + (cell - sz) / 2, sz, sz);
        }
        ctx.restore();
      }
      if (crisp > 0) drawXf(ctx, img, sp, { alpha: crisp });
    };
  }

  if (M === 'splat' || M === 'drip' || M === 'melt' || M === 'slidein') {
    const bot = g.body ? g.body[3] : sp.y + sp.h, H = bot - (g.body ? g.body[1] : sp.y);
    const base = { ax: sp.cx, ay: bot };
    const tf = M === 'splat' ? 0.3 * g.dur : M === 'drip' ? 0.32 * g.dur : 0;
    if (M === 'splat' || M === 'drip') {
      if (sfx) { ev(t0, 'whoosh', { dur: tf, gain: 0.5 }); ev(t0 + tf, M === 'splat' ? 'thud' : 'knock', { gain: 0.9 }); ev(t0 + tf + 0.22, 'boing', { gain: 0.5 }); }
      impact(t0 + tf, g._gi, sp.cx / g._art[0], M === 'splat' ? 1 : 0.6); SHAKES.push({ t: t0 + tf, amp: M === 'splat' ? 6 : 3 });
      if (g.dust !== false) g._dust = { t: t0 + tf, x: sp.cx, y: bot, w: sp.w * (M === 'splat' ? 0.9 : 0.6) };
    } else if (sfx) ev(t0, M === 'melt' ? 'boing' : 'whoosh', { gain: 0.6, dur: 0.4 });
    const fall = (g.body ? g.body[3] : sp.y + sp.h) + H * 0.4;
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      let xf, df;
      if (M === 'splat') {          // 砸下来拍扁成一摊 → 停一下 → 弹回来甩几下
        if (tau < tf) { const u = tau / tf; xf = { ...base, dy: -fall * (1 - u * u), sy: 1 + 0.25 * u, sx: 1 - 0.14 * u }; df = { bulge: -0.3 * u }; }
        else {
          const k = tau - tf, hold = 0.14 * g.dur;
          if (k < hold) { xf = { ...base, sy: 0.32, sx: 1.42 }; df = { bulge: 0.5, belly: 0.25 }; }
          else { const k2 = k - hold, s2 = spring(k2, 0.32, 14); const sy = 0.32 + 0.68 * s2;
            xf = { ...base, sy, sx: 1 + (1 - sy) * 0.6 }; df = { bulge: (1 - sy) * 0.8, lean: 0.07 * H * Math.sin(k2 * 13) * Math.exp(-k2 * 3.5), belly: (sy - 1) * 0.4 }; }
        }
      } else if (M === 'drip') {    // 像一滴液体拉长着落下，落地才成形，再抖几下
        if (tau < tf) { const u = tau / tf; xf = { ...base, dy: -fall * (1 - u * u), sx: 0.3 + 0.1 * u, sy: 2.2 - 0.6 * u }; df = { bulge: 0.9 }; }
        else { const k = tau - tf, s2 = spring(k, 0.3, 17);
          xf = { ...base, sx: 0.4 + 0.6 * s2, sy: 1.6 - 0.6 * s2 }; df = { bulge: 0.9 * (1 - s2) + 0.15 * Math.sin(k * 20) * Math.exp(-k * 6), lean: 0.06 * H * Math.sin(k * 14) * Math.exp(-k * 4) }; }
      } else if (M === 'melt') {    // 从地上一摊长出来，像坐起来一样
        const s2 = spring(tau, 0.34, 11);
        xf = { ...base, sy: Math.max(0.04, s2), sx: 1 + 0.5 * (1 - s2), alpha: clamp(tau / 0.08) }; df = { bulge: 0.9 * (1 - s2), lean: -0.12 * H * (1 - s2) + 0.05 * H * Math.sin(tau * 12) * Math.exp(-tau * 3.5) };
      } else {                      // 侧滑进来：拖着往后倾，停下时往前冲一下再晃回来
        const dir = g.from === 'right' ? 1 : -1, dist = g._art[0] * (g.dist ?? 0.9);
        const u = tau / (0.55 * g.dur), e = u < 1 ? E.outCubic(u) : 1, vel = u < 1 ? 3 * Math.pow(1 - u, 2) : 0;
        const k = Math.max(0, tau - 0.55 * g.dur);
        xf = { ...base, dx: dir * dist * (1 - e), sx: 1 + 0.12 * vel, sy: 1 - 0.06 * vel };
        df = { lean: dir * 0.1 * H * vel - dir * 0.09 * H * Math.sin(k * 11) * Math.exp(-k * 4) * (u >= 1 ? 1 : 0) };
      }
      drawDef(ctx, img, sp, xf, df);
    };
  }

  if (M === 'roll') {         // 圆标像硬币一样滚进来：转的角度和走的距离对得上，停下前后晃两下
    const dir = g.from === 'right' ? -1 : 1, dist = g._art[0] * 0.95, R = Math.min(sp.w, sp.h) * 0.5 * 0.82;
    if (sfx) { ev(t0, 'whoosh', { dur: 0.6 * g.dur, gain: 0.45 }); ev(t0 + 0.62 * g.dur, 'knock', { pitch: 1.2, gain: 0.6 }); }
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      const u = tau / (0.62 * g.dur), e = u < 1 ? E.outCubic(u) : 1;
      const x = -dir * dist * (1 - e);
      const k = Math.max(0, tau - 0.62 * g.dur);
      const rock = u >= 1 ? 0.12 * Math.sin(k * 12) * Math.exp(-k * 4.5) : 0;
      drawXf(ctx, img, sp, { dx: x + rock * R, rot: x / R + rock, ax: sp.cx, ay: sp.cy });
    };
  }

  if (M === 'flutter') {      // 像叶子一样左右飘着落下，轻轻落定
    const fall = sp.y + sp.h * 2.2;
    if (sfx) ev(t0 + g.dur * 0.85, 'pop', { pitch: 1.5, gain: 0.35 });
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      const u = tau / g.dur, e = E.inOutSine(u);
      const sw = Math.sin(u * Math.PI * 3.2) * (1 - e);
      drawXf(ctx, img, sp, { dy: -fall * (1 - e), dx: sw * sp.w * 1.6, rot: sw * 0.7, alpha: clamp(u * 6), ax: sp.cx, ay: sp.y + sp.h });
    };
  }

  if (M === 'popin') {        // 整块弹出：先缩一下再弹大，过冲回落
    if (sfx) ev(t0, 'pop', { pitch: 0.7, gain: 0.9 });
    return ctx => t => {
      const tau = t - t0;
      if (tau <= 0) return;
      if (tau >= g.dur) return idle(ctx, t);
      const s = spring(tau, 0.38, 16);
      drawXf(ctx, img, sp, { s: Math.max(s, 0), rot: -0.12 * (1 - s), ax: sp.cx, ay: g.body ? g.body[3] : sp.y + sp.h, alpha: clamp(tau / 0.04) });
    };
  }

  if (M === 'static') {       // 一开始就在（比如印在卷帘门上，跟着门一起下来）
    return ctx => t => idle(ctx, t);
  }

  if (M === 'fade') {
    return ctx => t => {
      const u = (t - t0) / g.dur;
      if (u <= 0) return;
      if (u >= 1) return idle(ctx, t);
      drawXf(ctx, img, sp, { alpha: E.inOutSine(u) });
    };
  }
  throw new Error('不认识的动法 ' + M);
}

// ---------- 点缀：和品牌有关的小东西（闪光、扫光、热气、冲击线），画在图形平面上 ----------
function star(ctx, x, y, r, rot) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.beginPath();
  for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2; ctx.quadraticCurveTo(0, 0, Math.cos(a) * r, Math.sin(a) * r); ctx.quadraticCurveTo(0, 0, Math.cos(a + Math.PI / 4) * r * 0.12, Math.sin(a + Math.PI / 4) * r * 0.12); }
  ctx.closePath(); ctx.fill(); ctx.restore();
}
function makeAccent(a, G) {
  const at = a.at ?? 0, col = a.color ? (a.color.startsWith('#') ? [1, 3, 5].map(i => parseInt(a.color.slice(i, i + 2), 16)) : a.color) : [255, 255, 255];
  const [bx0, by0, bx1, by1] = a.box, bw = bx1 - bx0, bh = by1 - by0;
  const size = a.size || Math.max(18, 0.16 * Math.min(bw, bh) + 0.05 * Math.max(bw, bh));
  if (a.kind === 'sparkle') {
    const n = a.n || 4, dur = a.dur || 0.9;
    if (a.sfx !== false) ev(at, 'shimmer', { dur: 0.5, gain: 0.8 });
    const spots = a.spots || Array.from({ length: n }, (_, k) => [a.pos[0] + (hash(k, 7) - 0.5) * bw * 1.1, a.pos[1] + (hash(k, 8) - 0.5) * bh * 1.1]);
    return (ctx, t) => spots.forEach((p, k) => {
      const u = (t - at - k * 0.07) / dur;
      if (u <= 0 || u >= 1) return;
      const s = Math.sin(Math.PI * u) * (0.6 + 0.6 * hash(k, 9));
      ctx.fillStyle = rgbStr(col, Math.min(1, 1.6 * Math.sin(Math.PI * u)));
      star(ctx, p[0], p[1], size * s, u * 0.8);
    });
  }
  if (a.kind === 'shine') {
    const dur = a.dur || 0.8;
    return (ctx, t) => {
      const u = (t - at) / dur;
      if (u <= 0 || u >= 1) return;
      const x = bx0 - bw * 0.3 + (bw * 1.6) * E.inOutSine(u), w = bw * (a.width || 0.18);
      const gr = ctx.createLinearGradient(x - w, by0, x + w, by1);
      gr.addColorStop(0, 'rgba(255,255,255,0)'); gr.addColorStop(0.5, `rgba(255,255,255,${a.strength || 0.7})`); gr.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.save(); ctx.globalCompositeOperation = 'source-atop'; ctx.fillStyle = gr; ctx.fillRect(bx0 - bw, by0 - bh, bw * 3, bh * 3); ctx.restore();
    };
  }
  if (a.kind === 'steam') {
    const n = a.n || 3, H = a.height || bh * 0.8, period = a.period || 1.8, lw = a.width || Math.max(2, size * 0.12);
    return (ctx, t) => {
      if (t < at) return;
      const ramp = clamp((t - at) / 0.5);
      ctx.save(); ctx.lineCap = 'round'; ctx.lineWidth = lw;
      for (let k = 0; k < n; k++) {
        const ph = ((t - at) / period + k / n) % 1;
        const x0 = a.pos[0] + (k - (n - 1) / 2) * size * 0.7, y0 = a.pos[1] - ph * H * 0.5;
        ctx.strokeStyle = rgbStr(col, ramp * Math.sin(Math.PI * ph) * 0.9);
        ctx.beginPath();
        for (let j = 0; j <= 12; j++) { const v = j / 12, y = y0 - v * H * 0.5, x = x0 + Math.sin(v * 5 + ph * 6 + k) * size * 0.18; j ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
        ctx.stroke();
      }
      ctx.restore();
    };
  }
  if (a.kind === 'strokes') {     // 扫线条：几道粗线「头沿路径走、尾巴跟着」扫过，常用来「从无到有」变出角色
    // lines: [{pts: 4 个点（画布宽高的比例，三次贝塞尔）, at, dur, color, width（画布宽度比例）, trail 尾巴长度 0–1}]
    // splat: {pos, at, dur, color, size}：线条砸到地面啪地溅开一小摊，再被吸回去
    const P2 = q => [bx0 + q[0] * bw, by0 + q[1] * bh];
    const lines = (a.lines || []).map(l => ({ ...l, pts: l.pts.map(P2), rgb: l.color ? [1, 3, 5].map(i => parseInt(l.color.slice(i, i + 2), 16)) : col }));
    const bez = (p, u) => { const v = 1 - u; return [0, 1].map(j => v * v * v * p[0][j] + 3 * v * v * u * p[1][j] + 3 * v * u * u * p[2][j] + u * u * u * p[3][j]); };
    if (a.sfx !== false) for (const l of lines) ev(l.at, 'whoosh', { dur: l.dur, gain: 0.35 });
    const sp_ = a.splat && { ...a.splat, p: P2(a.splat.pos), rgb: a.splat.color ? [1, 3, 5].map(i => parseInt(a.splat.color.slice(i, i + 2), 16)) : col };
    return (ctx, t) => {
      ctx.save(); ctx.lineCap = 'round';
      for (const l of lines) {
        const u = (t - l.at) / l.dur; if (u <= 0 || u >= 1) continue;
        const head = E.outQuad ? E.outQuad(u) : 1 - (1 - u) ** 2, tail = Math.max(0, head - (l.trail ?? 0.6) * (1 - u * 0.6));
        ctx.strokeStyle = rgbStr(l.rgb, 1); ctx.lineWidth = (l.width || 0.02) * bw; ctx.beginPath();
        for (let k = 0; k <= 24; k++) { const q = bez(l.pts, tail + (head - tail) * k / 24); k ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); }
        ctx.stroke();
      }
      if (sp_) {
        const u = (t - sp_.at) / (sp_.dur || 0.12);
        if (u > 0 && u < 1) {
          const r = (sp_.size || 0.035) * bw * Math.sin(u * Math.PI); ctx.fillStyle = rgbStr(sp_.rgb, 1);
          ctx.beginPath(); ctx.ellipse(sp_.p[0], sp_.p[1] - 0.11 * bh * u * u, r * 1.6, r * 0.45, 0, 0, Math.PI * 2); ctx.fill();
          for (let i = 0; i < 5; i++) { const an = Math.PI + i / 4 * Math.PI, rr = r * 0.9 + 0.06 * bw * u; ctx.beginPath(); ctx.arc(sp_.p[0] + Math.cos(an) * rr * 1.4, sp_.p[1] + Math.sin(an) * rr * 0.6, 0.006 * bw * (1 - u), 0, Math.PI * 2); ctx.fill(); }
        }
      }
      ctx.restore();
    };
  }
  if (a.kind === 'burst') {
    const n = a.n || 8, dur = a.dur || 0.35, r0 = size * 0.9, r1 = size * 2.1;
    return (ctx, t) => {
      const u = (t - at) / dur;
      if (u <= 0 || u >= 1) return;
      ctx.save(); ctx.strokeStyle = rgbStr(col, 1 - u); ctx.lineCap = 'round'; ctx.lineWidth = Math.max(2, size * 0.14);
      for (let k = 0; k < n; k++) { const an = k / n * Math.PI * 2 + 0.3; const a0 = r0 + (r1 - r0) * E.outCubic(u) * 0.6, a1 = r0 + (r1 - r0) * E.outCubic(u);
        ctx.beginPath(); ctx.moveTo(a.pos[0] + Math.cos(an) * a0, a.pos[1] + Math.sin(an) * a0); ctx.lineTo(a.pos[0] + Math.cos(an) * a1, a.pos[1] + Math.sin(an) * a1); ctx.stroke(); }
      ctx.restore();
    };
  }
  if (a.kind === 'dust') {      // 落地烟尘：两边冒出几团白云，往外飘、变大、消失
    const n = a.n || 3, dur = a.dur || 0.6, w = a.width || bw, r0 = a.size || Math.max(10, w * 0.07);
    const ink = a.outline || [40, 34, 30], fill = col;
    return (ctx, t) => {
      const u = (t - at) / dur;
      if (u <= 0 || u >= 1) return;
      ctx.save(); ctx.lineWidth = Math.max(2, r0 * 0.16);
      for (const side of [-1, 1]) for (let k = 0; k < n; k++) {
        const uu = clamp(u * 1.15 - k * 0.07); if (uu <= 0 || uu >= 1) continue;
        const x = a.pos[0] + side * (w * 0.35 + w * 0.32 * E.outCubic(uu) * (0.6 + 0.25 * k)), y = a.pos[1] - r0 * (0.3 + 1.4 * E.outCubic(uu) * (0.3 + 0.25 * k));
        const r = r0 * (0.6 + 0.5 * k / n) * (0.4 + 0.8 * Math.sin(Math.PI * Math.min(1, uu * 1.2)));
        if (r <= 0.5) continue;
        ctx.fillStyle = rgbStr(fill, 1 - uu * 0.6); ctx.strokeStyle = rgbStr(ink, 1 - uu * 0.6);
        ctx.beginPath(); for (let j = 0; j < 3; j++) ctx.arc(x + (j - 1) * r * 0.55, y - (j === 1 ? r * 0.35 : 0), r * (j === 1 ? 0.75 : 0.55), 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
      ctx.restore();
    };
  }
  if (a.kind === 'alert') {     // 吓一跳：头顶冒出三道线 / 「!」
    const dur = a.dur || 0.9, L = size * 1.1;
    if (a.sfx !== false) ev(at, 'pop', { pitch: 1.4, gain: 0.6 });
    return (ctx, t) => {
      const u = (t - at) / dur;
      if (u <= 0 || u >= 1) return;
      const s = spring(t - at, 0.4, 24), fade = 1 - clamp((u - 0.7) / 0.3);
      ctx.save(); ctx.strokeStyle = rgbStr(col, fade); ctx.lineCap = 'round'; ctx.lineWidth = Math.max(3, size * 0.16);
      for (const an of [-0.6, 0, 0.6]) { const x0 = a.pos[0] + Math.sin(an) * L * 0.5, y0 = a.pos[1] - Math.cos(an) * L * 0.5;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0 + Math.sin(an) * L * 0.6 * s, y0 - Math.cos(an) * L * 0.6 * s); ctx.stroke(); }
      ctx.restore();
    };
  }
  if (a.kind === 'bubble') {    // 对话气泡：弹出来，一直留着
    const text = a.text || 'hi!', fs = a.fontSize || size * 0.9;
    if (a.sfx !== false) ev(at, 'pop', { pitch: 1.2, gain: 0.7 });
    const ink = a.ink || [40, 34, 30];
    return (ctx, t) => {
      if (t <= at) return;
      const s = spring(t - at, 0.4, 18);
      ctx.save(); ctx.translate(a.pos[0], a.pos[1]); ctx.scale(Math.max(s, 0), Math.max(s, 0)); ctx.rotate((a.tilt || -0.08) * (1 + (1 - s)));
      ctx.font = `800 ${fs}px "Helvetica Neue", Arial, "PingFang SC", sans-serif`;
      const tw = ctx.measureText(text).width, w = tw + fs * 1.2, h = fs * 1.7;
      ctx.lineWidth = Math.max(3, fs * 0.12); ctx.fillStyle = rgbStr(col); ctx.strokeStyle = rgbStr(ink);
      ctx.beginPath(); ctx.ellipse(0, 0, w / 2, h / 2, 0, 0, Math.PI * 2);
      const tx = (a.tail || -1) * w * 0.28; ctx.moveTo(tx - fs * 0.25, h * 0.42); ctx.lineTo(tx - fs * 0.6 * (a.tail || -1) * -1, h * 0.8); ctx.lineTo(tx + fs * 0.3, h * 0.38);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = rgbStr(ink); ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text, 0, fs * 0.05);
      ctx.restore();
    };
  }
  if (a.kind === 'hearts') {    // 爱心往上飘
    const n = a.n || 3, dur = a.dur || 1.4;
    return (ctx, t) => {
      for (let k = 0; k < n; k++) {
        const u = (t - at - k * 0.18) / dur; if (u <= 0 || u >= 1) continue;
        const x = a.pos[0] + (hash(k, 3) - 0.5) * bw * 0.6 + Math.sin(u * 6 + k) * size * 0.2, y = a.pos[1] - u * bh * (a.rise ?? 0.8), r = size * 0.35 * Math.sin(Math.PI * Math.min(1, u * 1.5));
        ctx.save(); ctx.translate(x, y); ctx.fillStyle = rgbStr(col.length ? col : [255, 90, 120], 1 - u * 0.7); ctx.beginPath();
        ctx.moveTo(0, r * 0.35); ctx.bezierCurveTo(-r * 1.2, -r * 0.4, -r * 0.4, -r * 1.2, 0, -r * 0.45); ctx.bezierCurveTo(r * 0.4, -r * 1.2, r * 1.2, -r * 0.4, 0, r * 0.35); ctx.fill(); ctx.restore();
      }
    };
  }
  if (a.kind === 'curls') {     // 吃东西时头两边冒出的小卷线，左右交替一闪一闪
    const period = a.period || 0.5, r = size * 0.35, off = a.spread || bw * 0.62;
    return (ctx, t) => {
      if (t < at || (a.until != null && t >= a.until)) return;
      ctx.save(); ctx.strokeStyle = rgbStr(col); ctx.lineWidth = Math.max(3, size * 0.09); ctx.lineCap = 'round';
      [-1, 1].forEach((side, i) => {
        const ph = (((t - at) / period) + i * 0.5) % 1; if (ph > 0.7) return;
        const s = Math.sin(Math.PI * ph / 0.7), x = a.pos[0] + side * off, y = a.pos[1] - ph * r * 1.2;
        ctx.globalAlpha = s; ctx.beginPath();
        for (let j = 0; j <= 20; j++) { const v = j / 20, an = v * Math.PI * 2.4, rr = r * s * (1 - v * 0.6); const px = x + side * Math.cos(an) * rr, py = y + Math.sin(an) * rr * 0.8; j ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }
        ctx.stroke();
      });
      ctx.restore();
    };
  }
  if (a.kind === 'bulb') {      // 头顶亮起一个小灯泡（想到了 / 精神了）
    const r = size * 0.5, ink = a.ink || [30, 30, 30], fill = a.fill || [255, 214, 74];
    if (a.sfx !== false) ev(at, 'pop', { pitch: 1.6, gain: 0.6 });
    return (ctx, t) => {
      if (t <= at) return;
      const s = Math.max(0, spring(t - at, 0.38, 20)), on = 0.75 + 0.25 * Math.sin((t - at) * 18);
      ctx.save(); ctx.translate(a.pos[0], a.pos[1]); ctx.scale(s, s);
      ctx.lineWidth = Math.max(3, r * 0.14); ctx.strokeStyle = rgbStr(ink); ctx.lineCap = 'round';
      ctx.fillStyle = rgbStr(fill);
      ctx.beginPath(); ctx.arc(0, 0, r, Math.PI * 0.8, Math.PI * 2.2); ctx.lineTo(r * 0.42, r * 1.05); ctx.lineTo(-r * 0.42, r * 1.05); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-r * 0.4, r * 1.3); ctx.lineTo(r * 0.4, r * 1.3); ctx.stroke();
      for (const an of [-2.2, -1.57, -0.94, -2.9, -0.24]) { const a0 = r * 1.35, a1 = r * (1.75 + 0.15 * on);
        ctx.beginPath(); ctx.moveTo(Math.cos(an) * a0, Math.sin(an) * a0); ctx.lineTo(Math.cos(an) * a1, Math.sin(an) * a1); ctx.stroke(); }
      ctx.restore();
    };
  }
  if (a.kind === 'puff') {      // 呼出的一团气：从 pos 飘到 to，边飘边变大变淡（字可以接着在 to 出现）
    const dur = a.dur || 0.6, to = a.to || [a.pos[0] + bw * 0.3, a.pos[1]], ink = a.ink || [30, 30, 30];
    if (a.sfx !== false) ev(at, 'whoosh', { dur: dur, gain: 0.35 });
    return (ctx, t) => {
      const u = (t - at) / dur; if (u <= 0 || u >= 1) return;
      const e = E.outCubic(u), x = a.pos[0] + (to[0] - a.pos[0]) * e, y = a.pos[1] + (to[1] - a.pos[1]) * e - Math.sin(Math.PI * u) * size * 0.4;
      const r = size * (0.25 + 0.9 * e), al = 1 - clamp((u - 0.55) / 0.45);
      ctx.save(); ctx.lineWidth = Math.max(3, size * 0.07); ctx.strokeStyle = rgbStr(ink, al); ctx.fillStyle = rgbStr(col, al);
      ctx.beginPath(); for (let j = 0; j < 4; j++) { const an = j / 4 * Math.PI * 2 + u; ctx.arc(x + Math.cos(an) * r * 0.45, y + Math.sin(an) * r * 0.3, r * 0.42, 0, Math.PI * 2); }
      ctx.fill(); ctx.stroke(); ctx.restore();
    };
  }
  if (a.kind === 'fizz') {      // 汽水气泡：一个个小圆圈往上冒，晃着变大、消失，一直循环
    const n = a.n || 10, period = a.period || 1.6, H = a.height || bh * 1.2;
    return (ctx, t) => {
      if (t < at) return;
      const ramp = clamp((t - at) / 0.4);
      ctx.save(); ctx.lineWidth = Math.max(2, size * 0.06);
      for (let k = 0; k < n; k++) {
        const ph = (((t - at) / (period * (0.7 + 0.6 * hash(k, 1)))) + hash(k, 2)) % 1;
        const x = bx0 + bw * hash(k, 3) + Math.sin(ph * 9 + k) * size * 0.15, y = by1 - ph * H;
        const r = size * (0.08 + 0.12 * hash(k, 4)) * (0.6 + 0.6 * ph);
        ctx.strokeStyle = rgbStr(col, ramp * Math.sin(Math.PI * ph) * 0.95);
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.restore();
    };
  }
  if (a.kind === 'speedlines') { // 速度线：一段时间里在一侧闪过几道横线
    const dur = a.dur || 0.5, dir = a.dir || -1, n = a.n || 4;
    return (ctx, t) => {
      const u = (t - at) / dur; if (u <= 0 || u >= 1) return;
      ctx.save(); ctx.strokeStyle = rgbStr(col, 1 - u); ctx.lineCap = 'round'; ctx.lineWidth = Math.max(2, size * 0.1);
      for (let k = 0; k < n; k++) { const y = a.pos[1] + (k - (n - 1) / 2) * size * 0.6, x = a.pos[0] + dir * (bw * 0.55 + u * size * 2), l = size * (1.2 + hash(k, 2));
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + dir * l, y); ctx.stroke(); }
      ctx.restore();
    };
  }
  throw new Error('不认识的点缀 ' + a.kind);
}

// ---------- IP 角色：零件按父子关系叠起来，表演（转、移、缩放、眨眼、跳、晃、热气） ----------
const EASES = {
  linear: x => x, smooth: x => x * x * (3 - 2 * x), 'ease-in': x => x * x, 'ease-out': x => x * (2 - x),
  'ease-in-out': x => x < 0.5 ? 2 * x * x : 1 - 2 * (1 - x) * (1 - x), sine: x => E.inOutSine(x),
};
// eases[i] 是第 i 段（关键帧 i → i+1）的缓动；不写就用 sine
function keysAt(keys, t, eases) {
  if (t <= keys[0][0]) return keys[0].slice(1);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t <= b[0]) { const f = EASES[(eases && eases[i]) || 'sine'] || EASES.sine; const u = f(clamp((t - a[0]) / Math.max(b[0] - a[0], 1e-6))); return a.slice(1).map((v, j) => v + (b[j + 1] - v) * u); }
  }
  return keys[keys.length - 1].slice(1);
}
function makeRig(g, gi) {
  const P = {}; for (const p of g.parts) P[p.id] = p;
  const sp = g.sprite;
  const can = mk(sp.w, sp.h), cx = can.getContext('2d');
  // 跳一下的落地算冲击，配一声
  for (const a of g.acts) if (a.do === 'hop') {
    const d = a.dur || 0.55, land = a.at + d * 0.75;
    impact(land, gi, sp.cx / g._art[0], a.impact ?? 0.6); if (a.sfx !== false) ev(land, 'knock', { pitch: 1.1, gain: 0.7 });
  }
  for (const a of g.acts) if (a.sfx && a.at != null) ev(a.at, a.sfx, { gain: 0.6 });
  for (const a of g.acts) if (a.do === 'impact') {       // 自己用 move 做的落地：标一个冲击点，别的元素会被震起来、镜头轻推
    impact(a.at, gi, sp.cx / g._art[0], a.amp ?? 0.6); SHAKES.push({ t: a.at, amp: a.shake ?? 2 });
  }
  for (const p of g.parts) if (p.hose && p.hose.runFrom != null) {        // 每一步脚落地配一声软底脚步（看得见的接触点才配音）
    const per = (p.hose.frames || 5) / 30, end = p.hose.runUntil ?? DUR;
    for (let t = p.hose.runFrom + per * 0.5; t < end; t += per) ev(t, 'step', { gain: 0.5 });
  }
  const showParts = new Set(g.acts.filter(a => a.do === 'show').map(a => a.part).concat(g.acts.filter(a => a.do === 'cycle').flatMap(a => a.parts)));   // 有显示指令的零件，显隐由指令说了算
  const swaps = [];
  for (const a of g.acts) if (a.do === 'show') for (let i = 1; i < a.keys.length; i++) if (a.keys[i][1] > a.keys[i - 1][1] + 0.5) swaps.push(a.keys[i][0]);
  const pose = t => {
    const Q = {};
    for (const p of g.parts) Q[p.id] = { rot: 0, dx: 0, dy: 0, sx: 1, sy: 1, alpha: p.hidden && !showParts.has(p.id) ? 0 : 1, reveal: 1 };
    let pop = 0;
    for (const ts of swaps) { const k = t - ts; if (k > 0 && k < 0.5) pop += 0.05 * Math.exp(-k * 10) * Math.cos(k * 30); }
    if (pop) for (const p of g.parts) if (!p.parent) { Q[p.id].sy *= 1 - pop; Q[p.id].sx *= 1 + pop * 0.6; }
    for (const a of g.acts) {
      const q = Q[a.part];
      switch (a.do) {
        case 'rotate': if (q) q.rot += keysAt(a.keys, t, a.eases)[0] * Math.PI / 180; break;
        case 'move': if (q) { const v = keysAt(a.keys, t, a.eases); q.dx += v[0]; q.dy += v[1]; } break;
        case 'scale': if (q) { const v = keysAt(a.keys, t, a.eases); q.sx *= v[0]; q.sy *= v[1] ?? v[0]; } break;
        case 'fade': if (q) q.alpha *= clamp(keysAt(a.keys, t, a.eases)[0]); break;        // 连续透明度
        case 'reveal': if (q) q.reveal = clamp(keysAt(a.keys, t, a.eases)[0]); break;     // 自上而下露出（Codex 兼容）
        case 'show': if (q) { let v = a.keys[0][1]; for (const k of a.keys) if (t >= k[0]) v = k[1]; q.alpha *= v; } break;   // 换姿势是一帧硬切，不做淡化
        case 'cycle': {     // 几张姿势轮流播：parts 列表，每张停 frames 帧（30fps 计）
          const from = a.from ?? 0; if (t < from || (a.until != null && t >= a.until)) break;
          const fr = a.frames || 4, n = a.parts.length;
          const k = Math.floor((t - from) * 30 / fr) % n;
          a.parts.forEach((id, i) => { if (Q[id]) Q[id].alpha = i === k ? 1 : 0; });
          break;
        }
        case 'blink': {
          const times = Array.isArray(a.at) ? a.at : [];
          if (a.every) for (let x = a.from ?? 1; x < DUR; x += a.every) times.push(x);
          const on = times.some(x => t >= x && t < x + (a.dur || 0.13));
          if (on) { if (Q[a.open]) Q[a.open].alpha = 0; if (Q[a.closed]) Q[a.closed].alpha = 1; }
          break;
        }
        case 'wiggle': if (q && t > (a.from ?? 0)) q.rot += (a.amp || 6) * Math.PI / 180 * Math.sin(2 * Math.PI * (t - (a.from ?? 0)) / (a.period || 1.2)) * clamp((t - (a.from ?? 0)) / 0.3); break;
        case 'bob': if (q && t > (a.from ?? 0)) q.dy += (a.amp_px || 6) * Math.sin(2 * Math.PI * (t - (a.from ?? 0)) / (a.period || 1.4)) * clamp((t - (a.from ?? 0)) / 0.3); break;
        case 'float': {     // 热气：往上飘、淡入淡出，循环
          if (!q) break;
          if (t < (a.from ?? 0)) { q.alpha = 0; break; }
          const per = a.period || 1.6, f = (((t - (a.from ?? 0)) / per) + (a.phase || 0)) % 1;
          q.dy -= (a.rise || 20) * f; q.alpha *= Math.sin(Math.PI * f); q.sx *= 1 + 0.15 * f; break;
        }
        case 'hop': {       // 跳：先蹲（预备）→ 起跳拉长 → 落地压扁 → 回弹
          if (!q) break;
          const d = a.dur || 0.55, u = (t - a.at) / d, H = a.height || 30;
          if (u <= 0 || u >= 1.4) break;
          if (u < 0.18) { const v = Math.sin(Math.PI * u / 0.18); q.sy *= 1 - 0.12 * v; q.sx *= 1 + 0.08 * v; }
          else if (u < 0.75) { const v = (u - 0.18) / 0.57; q.dy -= H * Math.sin(Math.PI * v); q.sy *= 1 + 0.07 * Math.sin(Math.PI * v); q.sx *= 1 - 0.04 * Math.sin(Math.PI * v); }
          else { const k = (u - 0.75) * d, sq = 0.13 * Math.exp(-k * 10) * Math.cos(k * 28); q.sy *= 1 - sq; q.sx *= 1 + sq * 0.7; }
          break;
        }
      }
    }
    return Q;
  };
  // 橡皮管腿：胯跟着身体走（父零件的变换），脚踩在固定的地面线上；跑步 5 帧一步、4 张画（参考片量的）
  function drawHose(cx, p, Q, memo, t) {
    const h = p.hose, M = p.parent && P[p.parent] ? world(p.parent, Q, memo) : new DOMMatrix();
    if (h.from != null && t < h.from) return;
    const dir = h.dir ?? 1, G0 = h.ground, W2 = h.width || 14, shoe = h.shoe || 40, stride = h.stride || 40, lift = h.lift || 30;
    const running = h.runFrom != null && t >= h.runFrom && (h.runUntil == null || t < h.runUntil);
    const per = (h.frames || 5) / 30, draws = h.drawings || 4;
    let base = 0;
    if (running) { const fr = ((t - h.runFrom) / per) % 1; base = Math.min(draws - 1, Math.floor(fr * (h.frames || 5))) / draws; }
    cx.save(); cx.setTransform(new DOMMatrix().translate(-g.sprite.x, -g.sprite.y));
    cx.lineCap = 'round'; cx.lineJoin = 'round';
    h.hips.forEach((hip, i) => {
      const H0 = M.transformPoint(new DOMPoint(hip[0], hip[1]));
      let fx, fy, kneeOut;
      if (running) {
        const ph = (base + (i ? 0.5 : 0)) % 1;
        if (ph < 0.5) { fx = H0.x + dir * stride * (1 - 4 * ph); fy = G0; }                 // 着地往后蹬
        else { const u = (ph - 0.5) * 2; fx = H0.x + dir * stride * (-1 + 2 * u); fy = G0 - lift * Math.sin(Math.PI * u); }   // 抬起往前迈
        kneeOut = dir * (h.bend || stride * 0.6) * (0.4 + 0.6 * Math.abs(Math.sin(Math.PI * ph * 2)));
      } else { fx = H0.x + (i ? 1 : -1) * stride * 0.18; fy = G0; kneeOut = dir * (h.bend || stride * 0.6) * 0.25; }
      const mx = (H0.x + fx) / 2 + kneeOut, my = (H0.y + fy) / 2;
      const leg = () => { cx.beginPath(); cx.moveTo(H0.x, H0.y); cx.quadraticCurveTo(mx, my, fx, fy - shoe * 0.18); };
      cx.strokeStyle = h.ink || '#1b1b1b'; cx.lineWidth = W2 + (h.outline ?? 7); leg(); cx.stroke();
      cx.strokeStyle = h.color || '#ffffff'; cx.lineWidth = W2; leg(); cx.stroke();
      cx.save(); cx.translate(fx + dir * shoe * 0.25, fy - shoe * 0.2); cx.rotate(running && fy < G0 - 1 ? -dir * 0.25 : 0);
      cx.beginPath(); cx.ellipse(0, 0, shoe * 0.62, shoe * 0.3, 0, 0, Math.PI * 2);
      cx.fillStyle = h.color || '#ffffff'; cx.fill(); cx.lineWidth = h.outline ?? 7; cx.strokeStyle = h.ink || '#1b1b1b'; cx.stroke(); cx.restore();
    });
    cx.restore();
  }
  const world = (id, Q, memo) => {
    if (memo[id]) return memo[id];
    const p = P[id], q = Q[id];
    let M = p.parent && P[p.parent] ? world(p.parent, Q, memo) : new DOMMatrix();
    M = M.translate(p.pivot[0] + q.dx, p.pivot[1] + q.dy).rotate(q.rot * 180 / Math.PI).scale(q.sx, q.sy).translate(-p.pivot[0], -p.pivot[1]);
    memo[id] = M; return M;
  };
  return {
    canvas: can,
    load: async () => { for (const p of g.parts) if (p.png) p.img = await load('/build/' + p.png); },
    render(t) {
      const Q = pose(t), memo = {};
      cx.setTransform(1, 0, 0, 1, 0, 0); cx.clearRect(0, 0, sp.w, sp.h);
      if (g.shadow) {          // 接触影子：跟着根零件的脚（轴心）走，离地越高越淡越小
        const sh = g.shadow, root = g.parts.find(p => !p.parent) || g.parts[0];
        const M = world(root.id, Q, memo), f = M.transformPoint(new DOMPoint(root.pivot[0], root.pivot[1]));
        const lift = Math.max(0, sh.ground - f.y), k = clamp(1 - lift / sh.lift), sc = Math.hypot(M.a, M.b);
        let a = sh.opacity * k; for (const p of g.parts) if (!p.parent) a *= Q[p.id].alpha > 0 ? 1 : 1;
        const anyVis = g.parts.some(p => p.img && Q[p.id].alpha > 0.01);
        if (anyVis && a > 0.01) {
          cx.save(); cx.translate(-sp.x, -sp.y); cx.globalAlpha = a; cx.fillStyle = sh.color; cx.filter = `blur(${Math.max(2, sh.size * 0.06)}px)`;
          cx.beginPath(); cx.ellipse(f.x, sh.ground, sh.size * sc * (0.6 + 0.4 * k), sh.size * sc * 0.16 * (0.6 + 0.4 * k), 0, 0, Math.PI * 2); cx.fill(); cx.restore();
        }
      }
      for (const p of g.parts) {
        if (p.hose) { drawHose(cx, p, Q, memo, t); continue; }
        if (!p.img) continue;              // 虚拟父零件只负责带着子零件动
        let a = Q[p.id].alpha;
        for (let par = p.parent; par && P[par]; par = P[par].parent) a *= Q[par].alpha;
        if (a <= 0.001) continue;
        const M = world(p.id, Q, memo);
        cx.setTransform(new DOMMatrix().translate(-sp.x, -sp.y).multiply(M));
        cx.globalAlpha = a;
        const rv = Q[p.id].reveal;
        if (rv >= 1) cx.drawImage(p.img, p.x, p.y, p.w, p.h);
        else if (rv > 0) cx.drawImage(p.img, 0, 0, p.img.width, p.img.height * rv, p.x, p.y, p.w, p.h * rv);
      }
      cx.globalAlpha = 1; cx.setTransform(1, 0, 0, 1, 0, 0);
    },
  };
}

// ---------- 时间线：没写 at 的组接在上一组后面 ----------
let cursor = 0.35;
for (const G of S.graphics) for (const g of G.groups) {
  Object.assign(g, Object.assign({}, DEF[g.motion], g));
  if (g.motion === 'peek') g.dur = Math.max(g.dur || 0, (g.riseDur ?? 0.6) + (g.hold ?? 0.9) + 1.2);   // peek 的真实时长在这里就定下来：后面没写 at 的组接在它真正结束之后
  if (g.motion === 'write' && !g.dur) g.dur = clamp(0.14 * (g.n || 6), 0.5, 1.8);
  if (g.at == null) g.at = cursor;
  g._art = G.art; g._ink = G.ink; g._gi = S.graphics.indexOf(G);
  { const [k, tx, ty] = G.art2plane, r = G.surfRect; g._surfArt = [(r[0] - tx) / k, (r[1] - ty) / k, (r[0] + r[2] - tx) / k, (r[1] + r[3] - ty) / k]; }
  cursor = Math.max(cursor, g.at + lengthOf(g) - 0.12);
}

// ---------- 材质：sticker 白边贴纸 / neon 霓虹 / extrude 立体字（在平面画布上后处理） ----------
function stylize(X) {
  const st = X.G.style, L = X.G.look || {}, k = X.G.art2plane[0];
  if (!['sticker', 'neon', 'extrude'].includes(st)) return X.plane;
  const o = X.octx, P = X.plane, W2 = P.width, H2 = P.height;
  o.setTransform(1, 0, 0, 1, 0, 0); o.clearRect(0, 0, W2, H2); o.globalCompositeOperation = 'source-over'; o.globalAlpha = 1; o.filter = 'none';
  const artMax = Math.max(...X.G.art);
  if (st === 'sticker') {
    const b = (L.border ?? artMax * 0.022) * k, c = L.borderColor || '#ffffff', d = b * 0.7;
    const sh = Math.max(2, b * 0.5);
    o.filter = [[b, 0], [-b, 0], [0, b], [0, -b], [d, d], [-d, d], [d, -d], [-d, -d]].map(([x, y]) => `drop-shadow(${x.toFixed(1)}px ${y.toFixed(1)}px 0 ${c})`).join(' ') + ` drop-shadow(0 ${sh}px ${sh * 1.6}px rgba(0,0,0,0.28))`;
    o.drawImage(P, 0, 0); o.filter = 'none';
  } else if (st === 'neon') {
    const g = (L.glow ?? 1) * k * artMax;
    o.globalCompositeOperation = 'lighter';
    for (const [r, a] of [[0.004, 0.95], [0.012, 0.7], [0.035, 0.45], [0.09, 0.25]]) { o.filter = `blur(${(r * g).toFixed(1)}px)`; o.globalAlpha = a; o.drawImage(P, 0, 0); }
    o.filter = 'none'; o.globalAlpha = 1;
    const t = X.tint, tc = t.getContext('2d');
    tc.globalCompositeOperation = 'copy'; tc.drawImage(P, 0, 0); tc.globalCompositeOperation = 'source-atop'; tc.fillStyle = 'rgba(255,255,255,0.6)'; tc.fillRect(0, 0, W2, H2);
    o.drawImage(t, 0, 0);
    o.globalCompositeOperation = 'source-over';
  } else if (st === 'extrude') {
    const depth = (L.depth ?? artMax * 0.012) * k;
    let [dx, dy] = L.dir || [0.35, 1]; const dl = Math.hypot(dx, dy); dx /= dl; dy /= dl;
    const ink = X.G.ink, side = L.side ? L.side : `rgb(${ink.map(v => Math.round(v * 0.45)).join(',')})`;
    const t = X.tint, tc = t.getContext('2d');
    const tint = (col) => { tc.globalCompositeOperation = 'copy'; tc.drawImage(P, 0, 0); tc.globalCompositeOperation = 'source-in'; tc.fillStyle = col; tc.fillRect(0, 0, W2, H2); tc.globalCompositeOperation = 'source-over'; return t; };
    // 墙上的投影
    o.filter = `blur(${(depth * 1.2).toFixed(1)}px)`; o.globalAlpha = L.shadow ?? 0.35;
    o.drawImage(tint('rgb(0,0,0)'), dx * depth * 2.2, dy * depth * 2.2);
    o.filter = 'none'; o.globalAlpha = 1;
    // 厚度
    tint(side);
    const steps = Math.max(2, Math.ceil(depth));
    for (let i = steps; i >= 1; i--) o.drawImage(t, dx * depth * i / steps, dy * depth * i / steps);
    // 正面 + 一点金属高光
    o.drawImage(P, 0, 0);
    if (L.sheen) {
      tc.globalCompositeOperation = 'copy'; tc.drawImage(P, 0, 0); tc.globalCompositeOperation = 'source-in';
      const gr = tc.createLinearGradient(0, 0, 0, H2); gr.addColorStop(0, `rgba(255,255,255,${L.sheen})`); gr.addColorStop(0.5, 'rgba(255,255,255,0)'); gr.addColorStop(1, `rgba(0,0,0,${L.sheen * 0.4})`);
      tc.fillStyle = gr; tc.fillRect(0, 0, W2, H2); tc.globalCompositeOperation = 'source-over';
      o.drawImage(t, 0, 0);
    }
  }
  return X.out;
}

// ---------- WebGL ----------
const cv = document.getElementById('gl');
cv.width = W; cv.height = H;
const gl = cv.getContext('webgl2', { preserveDrawingBuffer: true, premultipliedAlpha: false, antialias: false });
if (!gl) throw new Error('浏览器不支持 WebGL2');
const VS = `#version 300 es
in vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;
const NG = 6;            // 一个场景最多几个图形（贴图槽位：底图 0，图形 1–6，卷帘门打开图 7，遮罩 8–13，不超过 WebGL2 保证的 16 个）
const chain = (fn, name) => Array.from({ length: NG }, (_, i) => i < NG - 1 ? `if (i == ${i}) return ${fn}(${name}${i}, ARGS);` : `return ${fn}(${name}${i}, ARGS);`).join(' ');
const FS = `#version 300 es
precision highp float;
uniform sampler2D uBase, ${Array.from({ length: NG }, (_, i) => 'uArt' + i).join(', ')};
uniform vec2 uSize, uFocus, uShake; uniform float uZoom, uGrain, uSeed; uniform int uCount;
uniform mat3 uHinv[${NG}]; uniform vec2 uPlane[${NG}];
uniform float uMeanLum[${NG}], uShade[${NG}], uMode[${NG}], uSoft[${NG}], uOpacity[${NG}], uCell[${NG}];
uniform vec4 uSurf[${NG}];
uniform mat3 uH[${NG}]; uniform float uDoor[${NG}]; uniform sampler2D uOpen; uniform float uClip[${NG}]; uniform float uBoil[${NG}]; uniform float uBoilSeed;
uniform sampler2D ${Array.from({ length: NG }, (_, i) => 'uMask' + i).join(', ')}; uniform float uHasMask[${NG}];
out vec4 o;
float lum(vec3 c){ return dot(c, vec3(0.299, 0.587, 0.114)); }
float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float maskAt(int i, vec2 uv){
  if (uHasMask[i] < 0.5) return 1.0;
  ${chain('texture', 'uMask').replaceAll('ARGS)', 'uv).r')}
}
float vnoise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1, 0)), f.x), mix(h21(i + vec2(0, 1)), h21(i + vec2(1, 1)), f.x), f.y); }
vec4 tapL(int i, vec2 uv, float lod){
  ${chain('textureLod', 'uArt').replaceAll('ARGS', 'uv, lod')}
}
vec4 tap(int i, vec2 uv){
  ${chain('texture', 'uArt').replaceAll('ARGS', 'uv')}
}
vec4 art(int i, vec2 uv, vec2 d){
  return (tap(i, uv) * 2.0 + tap(i, uv + d) + tap(i, uv - d) + tap(i, uv + vec2(d.x, -d.y)) + tap(i, uv + vec2(-d.x, d.y))) / 6.0;
}
void main(){
  vec2 p = vec2(gl_FragCoord.x, uSize.y - gl_FragCoord.y);
  vec2 q = (p - uFocus) / uZoom + uFocus + uShake;
  vec2 buv = q / uSize;
  vec3 col = texture(uBase, buv).rgb;
  float L = lum(textureLod(uBase, buv, 4.5).rgb) * 255.0;
  for (int i = 0; i < ${NG}; i++) {
    if (i >= uCount) break;
    vec3 h = uHinv[i] * vec3(q, 1.0);
    vec2 a = h.xy / h.z;
    vec2 uv = a / uPlane[i];
    if (uDoor[i] >= 0.0) {                   // 卷帘门：门（连同上面的图）从上往下拉，门下面露出打开状态
      vec4 r = uSurf[i];
      float inx = step(r.x, a.x) * step(a.x, r.x + r.z), iny = step(r.y, a.y) * step(a.y, r.y + r.w);
      if (inx * iny > 0.5) {
        float yb = r.y + uDoor[i] * r.w;
        if (a.y < yb) {
          vec2 a2 = vec2(a.x, a.y + (1.0 - uDoor[i]) * r.w);
          vec3 g2 = uH[i] * vec3(a2, 1.0);
          col = texture(uBase, (g2.xy / g2.z) / uSize).rgb;
          col *= 1.0 - 0.25 * smoothstep(r.w * 0.04, 0.0, yb - a.y);       // 门底边一点暗
          uv = a2 / uPlane[i];
        } else {
          col = texture(uOpen, buv).rgb;
          col *= 1.0 - 0.35 * smoothstep(r.w * 0.05, 0.0, a.y - yb);       // 门下面一道影子
          continue;
        }
      }
    }
    if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) continue;
    float mk = maskAt(i, buv);
    if (mk < 0.002) continue;
    if (uClip[i] > 0.5) {                    // 印在表面上的东西不能跑出表面
      vec4 r = uSurf[i]; vec2 aa = uv * uPlane[i];
      if (aa.x < r.x || aa.y < r.y || aa.x > r.x + r.z || aa.y > r.y + r.w) continue;
    }
    float mode = uMode[i];
    if (mode > 2.5 && mode < 3.5) {          // LED 点阵：按格子取色，圆点发光；没亮的格子也有一点点阵纹理
      float cs = uCell[i];
      vec2 cc = (floor(a / cs) + 0.5) * cs;
      float dm = smoothstep(0.46, 0.30, length(a - cc) / cs);
      vec4 r = uSurf[i];
      float inside = step(r.x, a.x) * step(r.y, a.y) * step(a.x, r.x + r.z) * step(a.y, r.y + r.w);
      col = mix(col, col * 0.7 + vec3(0.035), dm * 0.6 * inside);
      vec4 s = tapL(i, cc / uPlane[i], log2(cs) - 0.5);
      vec4 glow = tapL(i, uv, log2(cs) + 2.0);
      col += (s.rgb * dm * 2.3 + glow.rgb * 0.5) * uOpacity[i] * mk;
      continue;
    }
    if (uBoil[i] > 0.0) {                    // 手绘线条微抖：每几帧换一次很缓的扭动
      vec2 aa = uv * uPlane[i] * 0.02 + uBoilSeed * 3.17;
      uv += (vec2(vnoise(aa), vnoise(aa + 19.7)) - 0.5) * 2.0 * uBoil[i] / uPlane[i];
    }
    vec4 s = art(i, uv, vec2(uSoft[i]) / uPlane[i] * 0.7);
    if (s.a < 0.002) continue;
    if (mode > 1.5 && mode < 2.5) { col += s.rgb * uOpacity[i] * mk; continue; }     // 发光：叠加
    vec3 c = s.rgb / s.a;
    c *= mix(1.0, clamp(L / max(uMeanLum[i], 1.0), 0.55, 1.5), uShade[i]);
    float A = s.a * uOpacity[i] * mk;
    if (mode > 3.5) {                        // 粉笔：颗粒感 + 不均匀
      A *= mix(0.25, 1.0, smoothstep(0.15, 0.85, h21(floor(a * 0.8)))) * mix(0.65, 1.0, vnoise(a * 0.05));
    }
    col = mode > 0.5 && mode < 1.5 ? mix(col, col * c, A) : mix(col, c, A);
  }
  float n = fract(sin(dot(p + uSeed, vec2(12.9898, 78.233))) * 43758.5453);
  col += (n - 0.5) * uGrain / 255.0;
  o = vec4(clamp(col, 0.0, 1.0), 1.0);
}`;
function shader(type, src) { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; }
const prog = gl.createProgram();
gl.attachShader(prog, shader(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog);
if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
gl.useProgram(prog);
const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
const loc = gl.getAttribLocation(prog, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
const U = n => gl.getUniformLocation(prog, n);
function tex(unit, src) {
  const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  if (src) upload(unit, t, src, false);
  return t;
}
function upload(unit, t, src, premul) {
  gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, premul);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
  gl.generateMipmap(gl.TEXTURE_2D);
}

// ---------- 装配 ----------
const GFX = [];
let baseTex, focus, BASES = [], baseIdx = -1;
async function setup() {
  const base = await load('/build/base.png');
  baseTex = tex(0, base);
  BASES = await Promise.all((S.bases || []).map(b => load('/build/' + b)));     // 底图序列：每 baseHold 帧换一张
  gl.uniform1i(U('uBase'), 0);
  gl.uniform2f(U('uSize'), W, H);
  if (S.graphics.length > NG) throw new Error(`一个场景最多 ${NG} 个图形，现在有 ${S.graphics.length} 个：把几行小字合成一个 text，或者拆成两个场景`);
  for (let i = 0; i < S.graphics.length; i++) {
    const G = S.graphics[i];
    const plane = mk(G.plane[0], G.plane[1]);
    const ctx = plane.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    const drawers = [];
    for (const g of G.groups) {
      let img = null, items = null;
      if (g.rig) { const R = makeRig(g, i); await R.load(); img = R.canvas; g._rig = R; }
      else if (g.sprite) img = await load('/build/' + g.sprite.png);
      if (g.items) { items = g.items; for (const it of items) it.img = await load('/build/' + it.png); }
      drawers.push(makeGroup(g, img, items)(ctx));
    }
    const t = tex(1 + i, null);
    gl.uniform1i(U('uArt' + i), 1 + i);
    gl.uniformMatrix3fv(U(`uHinv[${i}]`), false, new Float32Array(G.hinv));
    gl.uniform2f(U(`uPlane[${i}]`), G.plane[0], G.plane[1]);
    gl.uniform1f(U(`uMeanLum[${i}]`), G.meanLum);
    gl.uniform1f(U(`uShade[${i}]`), G.shade);
    const mode = { neon: 2, led: 3, chalk: 4 }[G.style] ?? (G.blend === 'multiply' ? 1 : G.blend === 'add' ? 2 : 0);
    gl.uniform1f(U(`uMode[${i}]`), mode);
    gl.uniform1f(U(`uCell[${i}]`), (G.look.cell || Math.max(...G.art) / 90) * G.art2plane[0]);
    gl.uniform4f(U(`uSurf[${i}]`), ...G.surfRect);
    gl.uniformMatrix3fv(U(`uH[${i}]`), false, new Float32Array(G.h));
    gl.uniform1f(U(`uDoor[${i}]`), -1);
    gl.uniform1f(U(`uHasMask[${i}]`), G.mask ? 1 : 0);
    if (G.mask) { const mimg = await load('/build/' + G.mask); tex(NG + 2 + i, mimg); gl.uniform1i(U('uMask' + i), NG + 2 + i); }
    else gl.uniform1i(U('uMask' + i), 0);
    gl.uniform1f(U(`uClip[${i}]`), (G.look.clip ?? !['neon', 'extrude'].includes(G.style)) ? 1 : 0);
    gl.uniform1f(U(`uBoil[${i}]`), (G.look.boil ?? (['led', 'extrude'].includes(G.style) ? 0 : 1.2)) * G.art2plane[0] * Math.max(...G.art) / 600);
    if (G.carrier) {
      const c = G.carrier;
      Object.assign(c, Object.assign({ at: 0.3, dur: 1.4 }, c));
      const op = await load('/build/' + c.open);
      tex(NG + 1, op); gl.uniform1i(U('uOpen'), NG + 1);
      ev(c.at, 'roll', { dur: c.dur, gain: 0.8 }); ev(c.at + c.dur, 'thud', { gain: 0.8 }); SHAKES.push({ t: c.at + c.dur, amp: 2.5 });
    }
    gl.uniform1f(U(`uSoft[${i}]`), G.soften);
    gl.uniform1f(U(`uOpacity[${i}]`), G.opacity);
    const out = mk(G.plane[0], G.plane[1]), tint = mk(G.plane[0], G.plane[1]);
    const accents = (G.accents || []).map(a => makeAccent(a, G));
    // 落地自动冒烟尘
    for (const g of G.groups) if (g._dust) accents.push(makeAccent({ kind: 'dust', at: g._dust.t, pos: [g._dust.x, g._dust.y], width: g._dust.w, box: [0, 0, G.art[0], G.art[1]], color: G.look.dustColor || '#ffffff' }, G));
    GFX.push({ G, plane, ctx, drawers, accents, out, octx: out.getContext('2d'), tint, tex: t, unit: 1 + i });
  }
  for (let i = GFX.length; i < NG; i++) { gl.uniform1i(U('uArt' + i), 1); gl.uniform1f(U(`uDoor[${i}]`), -1); gl.uniform1f(U(`uBoil[${i}]`), 0); gl.uniform1f(U(`uHasMask[${i}]`), 0); gl.uniform1i(U('uMask' + i), 0); }   // 空位指向一张已有的纹理
  if (!S.graphics.some(G => G.carrier)) gl.uniform1i(U('uOpen'), 0);
  gl.uniform1i(U('uCount'), GFX.length);
  const qs = S.graphics.flatMap(G => G.quad);
  focus = S.camera.focus || [qs.reduce((a, q) => a + q[0], 0) / qs.length, qs.reduce((a, q) => a + q[1], 0) / qs.length];
  EVENTS.sort((a, b) => a.t - b.t);
}

function renderAt(t0, still = false) {
  const step = S.anim?.step ?? 0;             // 默认平滑；写 15 是一拍二的手绘感（镜头和颗粒照常 30 帧）
  const t = step && !still ? Math.floor(t0 * step + 1e-6) / step : t0;
  if (BASES.length) {
    const k = still ? 0 : Math.floor(t0 * (S.fps || 30) / (S.baseHold || 6) + 1e-6) % BASES.length;
    if (k !== baseIdx) { baseIdx = k; upload(0, baseTex, BASES[k], false); }
  }
  for (const X of GFX) {
    const [k, tx, ty] = X.G.art2plane;
    X.ctx.setTransform(1, 0, 0, 1, 0, 0);
    X.ctx.clearRect(0, 0, X.plane.width, X.plane.height);
    for (const g of X.G.groups) if (g._rig) g._rig.render(t);
    X.drawers.forEach((d, i) => { const g = X.G.groups[i]; if (g.until != null && t >= g.until) return; X.ctx.setTransform(k, 0, 0, k, tx, ty); d(t); });
    X.ctx.setTransform(k, 0, 0, k, tx, ty);
    for (const a of X.accents) a(X.ctx, t);
    upload(X.unit, X.tex, stylize(X), true);
    if (X.G.carrier) {
      const c = X.G.carrier, u = (t - c.at) / c.dur, s0 = c.startAt || 0;
      // linear：匀速，最后 15% 减速（参考片量的）；默认：先快后慢
      const ease = c.ease === 'linear' ? (x => x < 0.85 ? x * 0.92 / 0.85 : 0.92 + 0.08 * (1 - Math.pow(1 - (x - 0.85) / 0.15, 2))) : (x => 1 - Math.pow(1 - x, 2.2));
      let p = u <= 0 ? s0 : u >= 1 ? 1 - 0.012 * Math.exp(-(t - c.at - c.dur) * 9) * Math.sin((t - c.at - c.dur) * 30) : s0 + (1 - s0) * ease(u);
      gl.uniform1f(U(`uDoor[${GFX.indexOf(X)}]`), still ? 1 : clamp(p, 0, 1));
    }
  }
  const z = still ? 1 : 1 + (S.camera.push || 0) * E.inOutSine(t0 / DUR) + (S.camera.punch === false ? 0 : punchAt(t0));
  const dr = S.camera.drift ?? 1;             // 手持感：很轻的漂移
  const sh = still ? [0, 0] : shakeAt(t0).map((v, i) => v + dr * (i ? 1.4 * Math.sin(t0 * 1.13 + 1.7) : 2 * Math.sin(t0 * 0.83)));
  gl.uniform1f(U('uZoom'), z);
  gl.uniform2f(U('uFocus'), focus[0], focus[1]);
  gl.uniform2f(U('uShake'), sh[0], sh[1]);
  gl.uniform1f(U('uGrain'), still ? 0 : S.grain);
  gl.uniform1f(U('uSeed'), still ? 0 : (Math.round(t0 * 60) % 97) * 1.37);
  gl.uniform1f(U('uBoilSeed'), still ? 0 : Math.floor(t0 * 10) % 7);
  gl.viewport(0, 0, W, H);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

// ---------- 竖版对照排版（上：成片；下：参考静帧） ----------
// 标签默认中文：上「成片」，下「原图」（layout.ref = "end" 时下面放的是最后一帧）；layout.labels 可覆盖
const LAY = Object.assign({ labels: ['成片', (S.layout || {}).ref === 'end' ? '最后一帧' : '原图'], badge: '', bg: '#ffffff', fg: '#111111', size: [1080, 1920] }, S.layout || {});
const lay = document.getElementById('lay');
lay.width = LAY.size[0]; lay.height = LAY.size[1];
const lctx = lay.getContext('2d');
let refCanvas = null;
function layoutFrame() {
  const LW = lay.width, LH = lay.height, c = lctx;
  c.fillStyle = LAY.bg; c.fillRect(0, 0, LW, LH);
  c.fillStyle = LAY.fg; c.textBaseline = 'alphabetic';
  const font = (px, wgt = 500, fam = LAY.font) => `${wgt} ${px}px ${fam || '"Helvetica Neue", Helvetica, Arial'}, "PingFang SC", sans-serif`;   // layout.font / badgeFont 可换字体
  const shadow = (on) => { c.shadowColor = on ? 'rgba(0,0,0,0.28)' : 'transparent'; c.shadowBlur = on ? 28 : 0; c.shadowOffsetY = on ? 10 : 0; };
  if (W / H >= 1.15) {          // 横图：成片在上、原图在下
    const rh = LW * H / W, y0 = Math.round((LH - rh - 60 - 0.85 * rh - 220) / 2) + 60;
    const lx = LAY.center ? LW / 2 : 135;          // center: 标签居中（宣传片常见的排法）
    c.textAlign = LAY.center ? 'center' : 'left';
    c.font = font(LAY.center ? (LAY.labelSize || 40) : 34, LAY.labelWeight || 500); c.fillText(LAY.labels[0], lx, y0 - (LAY.center ? 26 : 16));
    c.drawImage(cv, 0, y0, LW, rh);
    const w2 = LW * (LAY.refScale || 0.85), h2 = w2 * H / W, x2 = (LW - w2) / 2, y2 = y0 + rh + (LAY.center ? 110 : 80);
    c.fillText(LAY.labels[1], lx, y2 - (LAY.center ? 26 : 16));
    c.textAlign = 'left';
    shadow(true); c.drawImage(refCanvas, x2, y2, w2, h2); shadow(false);
    if (LAY.badge) {              // 底部品牌条：一行字，或者 [大字, 小字] 两行
      const lines = Array.isArray(LAY.badge) ? LAY.badge : [LAY.badge];
      c.textAlign = 'center';
      const by = y2 + h2 + 100;
      const bf = LAY.badgeFont || LAY.font, bs = LAY.badgeSize || 46;
      if (LAY.badgeBg) {
        c.font = font(bs, LAY.badgeWeight || 900, bf); const bw = c.measureText(lines[0]).width + bs * 0.9;
        c.fillStyle = LAY.badgeBg; c.fillRect(LW / 2 - bw / 2, by - bs * 1.02, bw, bs * 1.32); c.fillStyle = LAY.badgeFg || '#111';
        c.fillText(lines[0], LW / 2, by); c.fillStyle = LAY.fg;
      } else { c.font = font(bs, LAY.badgeWeight || 900, bf); c.fillText(lines[0], LW / 2, by); }
      if (lines[1]) { c.font = font(28, 700); c.fillText(lines[1], LW / 2, by + 44); }
      c.textAlign = 'left';
    }
  } else {                      // 竖图/方图：成片放大，参考静帧做左下角小图
    let rw = LW - 80, rh = rw * H / W;
    if (rh > LH * 0.72) { rh = LH * 0.72; rw = rh * W / H; }
    const x0 = (LW - rw) / 2, y0 = 120;
    c.font = font(34); c.fillText(LAY.labels[0], x0, y0 - 22);
    c.drawImage(cv, x0, y0, rw, rh);
    const h2 = LH - (y0 + rh) - 150, w2 = h2 * W / H, y2 = y0 + rh + 100;
    const x2 = LAY.badge ? x0 : (LW - w2) / 2;
    c.textAlign = LAY.badge ? 'left' : 'center';
    c.fillText(LAY.labels[1], LAY.badge ? x0 : LW / 2, y2 - 22);
    c.textAlign = 'left';
    shadow(true); c.drawImage(refCanvas, x2, y2, w2, h2); shadow(false);
    if (LAY.badge) { c.font = font(40, 800); c.fillText(LAY.badge, x0 + w2 + 40, y2 + h2 / 2 + 14); }
  }
}

// ---------- 对外接口 ----------
window.__ready = false;
setup().then(() => {
  const tEnd = Math.max(...S.graphics.flatMap(G => G.groups.map(g => g.t1)), 0.1);
  // 竖版对照下面那块：默认放动画开始前的原图（一张静态场景），看得出「静图变成了动画」；layout.ref = "end" 放成片最后一帧
  renderAt(LAY.ref === 'end' ? Math.min(tEnd + 0.01, DUR) : 0, true);
  refCanvas = mk(W, H); refCanvas.getContext('2d').drawImage(cv, 0, 0);
  window.__ready = true;
}).catch(e => { console.error(e.stack || String(e)); window.__error = String(e); });

window.__events = () => EVENTS;
window.__timeline = () => S.graphics.map(G => G.groups.map(g => ({ name: g.name, motion: g.motion, at: +g.at.toFixed(3), end: +g.t1.toFixed(3) })));
window.__frame = (t, want = ['pure'], q = 0.95) => {
  renderAt(t);
  const out = {};
  if (want.includes('pure')) out.pure = cv.toDataURL('image/jpeg', q);
  if (want.includes('compare')) { layoutFrame(); out.compare = lay.toDataURL('image/jpeg', q); }
  return out;
};
})();
