# scene.json 写法

```json
{
  "base": "base.png",
  "size": [1080, 1440],
  "duration": 4.5,
  "fps": 30,
  "camera": {"push": 0.04},
  "grain": 4,
  "layout": {"labels": ["成片", "原图"], "badge": ""},
  "graphics": [
    {
      "art": "logo.svg",
      "surface": {"seed": [360, 600]},
      "place": {"width": 0.78, "height": 0.8, "anchor": [0.5, 0.5], "offset": [0, 0]},
      "look": {"style": "print", "color": null, "shade": 0.8, "soften": 0.6, "blend": "normal", "opacity": 1},
      "carrier": null,
      "accents": [],
      "auto": {"graphic": {"motion": "draw"}, "title": {"motion": "pop"}, "small": {"motion": "type"}}
    }
  ]
}
```

## 顶层
| 字段 | 说明 | 默认 |
|---|---|---|
| `base` | 空白底图 | 必填 |
| `size` | 输出尺寸 | 底图宽超过 1080 时缩到 1080 宽，否则用原尺寸 |
| `duration` | 每段秒数 | 5 |
| `fps` | 帧率 | 30 |
| `camera.push` | 全程镜头缓推的比例；`camera.focus` 可以指定推向哪个点 | 0.04 |
| `grain` | 胶片颗粒强度，让静帧不那么「死」 | 4 |
| `bases` / `baseHold` | 底图序列：好几张底图按帧轮换（翻页书效果），每 `baseHold` 帧（默认 6）换一张；第一张同时写在 `base` 里。图形的透视按 `base` 算，所以序列适合放「贴纸式」角色（surface 用整幅画面） | 不写就是一张底图 |
| `layout` | 竖版对照的文字：`labels` 两行标签、`badge` 底部角标（字符串，或 `[大字, 小字]` 两行）、`badgeBg` / `badgeFg` 品牌条底色和字色、`center` 标签居中、`refScale` 下面静图的宽度比例、`bg` / `fg` 背景色和文字色 | 标签「成片」「原图」（`ref: "end"` 时是「最后一帧」），没有角标 |

**坐标**（`seed`、`quad`、`focus`、`mask`）都按缩放后的输出尺寸算，也就是 `mm grid` 出的那张网格图上的数字。`mm grid` 会打两套刻度：scene.json 填「scene 坐标」那套，别填「原图像素 src」（那套是量角色图里的位置用的）。

## 声音
| 字段 | 说明 | 默认 |
|---|---|---|
| `sound` | `minimal`：只配看得见的接触声（弹出、落地、敲、脚步、门），书写声减半，打字只响前 3 下；`full`：全部；`none`：不要 | minimal |

渲染时 `mm render --sound full` 可以临时改；`--stills 1,2.5,4` 额外导出静帧；每次渲染都会写 `名字.metadata.json`（时间轴、声音事件、静帧路径）。

## 动画手感
| 字段 | 说明 | 默认 |
|---|---|---|
| `anim.step` | 动画几帧一拍；15 是一拍二的手绘感，0 是完全平滑 | 0 |
| `camera.drift` | 手持感的轻微漂移，0 关掉 | 1 |
| `camera.punch` | 冲击时镜头推一下，`false` 关掉 | 开 |

## graphics（最多 6 块，超了会报错；每块各贴一个表面，几块可以贴同一个表面，比如角色一块、文字一块）
| 字段 | 说明 |
|---|---|
| `art` | 图形文件（PNG / SVG） |
| `text` | 不用文件，直接排一段文字：`{"lines": ["BOBA BUDDY", "SIP · SMILE"], "weight": 900, "color": "#2b2118", "arc": 26, "sizes": [1, 0.3], "tracking": 0.03, "font": "\"PingFang SC\", sans-serif"}`；`arc` 是第一行的弧度（度），`sizes` 是每行相对大小 |
| `rig` | IP 角色分层表演，见 `rig.md`；`rig.only_parts: true` 只画列出的零件（Codex 的约定） |
| `mask` | 遮罩（圆窗、拱门），见 `forms.md` |
| `look.boil` | 线条微抖的强度，0 不抖（LED、立体字默认不抖） |
| `look.clip` | 是否裁到表面里（印刷、贴纸默认裁，霓虹、立体字默认不裁） |
| `surface` | 写 `seed: [x,y]`（自动找四边形，可加 `tol`），或者直接写 `quad: [[x,y]×4]`，顺序是左上、右上、右下、左下 |
| `place.width` / `place.height` | 图形最多占表面宽、高的比例（取两者中更小的那个） |
| `place.anchor` | 图形中心放在表面的什么位置，`[0.5, 0.5]` 是正中 |
| `place.offset` | 再挪一点，以表面宽高为单位 |
| `look.shade` | 图形吃到表面明暗的程度，0 表示完全不受光照影响 |
| `look.soften` | 边缘柔化（像素），让图形像印上去而不是浮在表面上 |
| `look.blend` | `normal` 用于油漆、贴纸；`multiply` 用于透出纸纹的印刷 |
| `look.style` | 材质：`print` / `neon` / `extrude` / `sticker` / `led` / `chalk`，各自的参数见 `forms.md` |
| `look.color` | 把图形整体换成一种颜色，比如 `"#ffffff"` |
| `carrier` | 载体联动（卷帘门），见 `forms.md` |
| `accents` | 点缀（闪光、扫光、热气、冲击线），见 `forms.md` |
| `auto` | 用自动分组时，按组名（`graphic` / `title` / `small`）写动法和参数 |
| `groups` | 手写分组，写了它就不用自动分组了 |

## 手写分组 groups
```json
"groups": [
  {"name": "mascot", "box": [40, 20, 420, 380], "motion": "drop", "idle": "breathe"},
  {"name": "title",  "box": [20, 380, 460, 470], "motion": "pop"},
  {"name": "rest",   "box": "rest", "motion": "type"}
]
```
- `box` 的坐标看 `build/check_art_0.png` 边上的刻度。图形会先缩到最长边 1200 像素，四周再加 6 像素留白，刻度已经按这个算好了。
- 每个零件按它的**中心点**落在哪个框里来归组，框重叠时写在前面的组优先。
- `box: "rest"` 收下所有剩下的零件。没被任何组收下的零件不会出现在画面里。
- 动法参数见 `motions.md`。

## 从 Codex image-logo-motion 迁移
原来的 `config.json`（background + logo.svg + width/height + quad 归一化四角 + layers 关键帧）可以直接转换：
```bash
mm import-codex 旧项目/config.json 新场景/
```
- 每个图层变成一个 rig 零件，关键帧变成 `move` / `scale` / `rotate` / `fade` / `reveal`，`ease` 保留（linear / smooth / ease-in / ease-out / ease-in-out）。
- 镜头晃动、颗粒、冲击默认关掉，渲染结果和原来一致；之后可以在这份 scene.json 上加 mockup-motion 的模板节奏和效果。

## 宣传片版式（照 Higgsfield × clovestudio 那条）
上面是动起来的成片，下面是一张静态参考图（默认是动画开始前的原图），底部品牌条，结尾一张引导评论的字卡：
```json
"layout": {"labels": ["代码让它动起来", "原图：一张 AI 生成的静态场景"], "center": true, "bg": "#000000", "fg": "#ffffff",
           "font": "\"Futura\"", "labelWeight": 500, "labelSize": 42,
           "badge": "SKILL", "badgeBg": "#ffe14d", "badgeFg": "#111111", "badgeFont": "\"Futura\"", "badgeWeight": 700, "badgeSize": 54}
```
- 下面那块默认就放**动画开始前的原图**（空白场景）；要放成片最后一帧写 `"ref": "end"`。观众一眼看懂「一张静图变成了动画」，这是最直接的证明。
- 卖点放开头：第一段用纯文字海报直接说「EVERY FRAME / ANIMATED / IN CODE.」，封面和标题说同一句话；整片控制在 5 段、20 秒左右，同一个套路超过 4 段会开始重复。
- 标签、品牌条这类版式字用系统字就够：标签、品牌条用 Futura；海报大字用 DIN Condensed（weight 700）；店里的小字用 Futura 600。系统默认的 Helvetica 显得普通。画面里的店名、落款还是默认用字标图（`templates.md`）。
- 底图用 16:9 横图（表面占满画面），竖版对照会自动排成上下两块。
- 每段 3.6–4.5 秒、硬切；`mm render 场景 --layout compare` 出每段，`mm concat 宣传片.mp4 段1_对照.mp4 … 结尾.mp4` 拼起来。
- 一个场景最多 6 个图形。
