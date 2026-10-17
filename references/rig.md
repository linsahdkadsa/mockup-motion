# IP 角色分层表演

角色要会「演」（喝一口、挥手、吓一跳、跳起来），SVG 里就得把能动的部位分成**带 id 的组**，比如 `body` `head` `eyes-open` `eyes-closed` `arm-l` `cup` `steam-0`。
没有分层的 PNG 只能整块动（也能做拍扁、滴落、侧滑这些桥段）。

## 没有分层 SVG？用几张对齐的姿势图（推荐，最接近参考片的手绘感）
参考片的动作感来自**关键姿势**（预备 → 动作 → 反应），不是平滑补间。做法：
1. 先有一张干净的角色线稿（用户给的，或者 AI 按用户的图重画成高清线稿）。
2. 用图生图在它的基础上出 2–3 张**同一角色、同一画风、同一位置**的姿势或表情（提示词见 `base-prompts.md`「关键姿势」）。
   - AI 通常只改脸和局部、位置不动 → 刚好逐像素对齐，切换不会跳。
3. 每张当一个零件，用 `show` 切换，再加一个虚拟父零件 `root` 负责整体前倾、后仰、跳。
```json
"rig": {
  "enter": {"motion": "draw", "seed": "top", "at": 0.3, "dur": 1.3},
  "parts": {
    "root":  {"virtual": true, "pivot_frac": [0.68, 0.95]},
    "look":  {"png": "pose_look.jpg",  "parent": "root"},
    "drink": {"png": "pose_drink.jpg", "parent": "root", "hidden": true},
    "happy": {"png": "pose_happy.jpg", "parent": "root", "hidden": true}
  },
  "acts": [
    {"do": "show", "part": "look",  "keys": [[1.75, 1], [1.8, 0], [3.9, 0], [3.95, 1]]},
    {"do": "show", "part": "drink", "keys": [[1.75, 0], [1.8, 1], [2.75, 1], [2.8, 0]]},
    {"do": "show", "part": "happy", "keys": [[2.75, 0], [2.8, 1], [3.9, 1], [3.95, 0]]},
    {"do": "rotate", "part": "root", "keys": [[1.4, 0], [1.68, 4], [1.95, -8], [2.65, -8], [2.92, 2], [3.1, 0]]},
    {"do": "hop", "part": "root", "at": 2.88, "height": 40}
  ]
}
```
- 白底 PNG 会自动去背景，角色身上的白色填充（白脸、白杯子）会保留。
- 每次换姿势会自动轻轻压一下，像手绘动画换帧。
- 喝东西的「咕咚」可以用 `scale` 在 root 上做两下小压缩。
- **换姿势是一帧硬切**（`show` 的关键帧按「到点就切」处理，不做淡化）；要过渡就补中间画。
- **多个角色**：每人一个 graphic（各自的 rig），同一个表面、同样的 `place` 加 `"fit": "art"`；入场同步，反应错开 2–6 帧，循环的周期也不一样（比如 0.5 秒和 0.58 秒），看起来才像两个人。
- **姿势循环**（吸面、聊天）：`{"do": "cycle", "parts": ["slurpA", "slurpB"], "from": 1.35, "frames": 5}`，每张停 5 帧轮流播。
- **橡皮管腿**（代码画的腿，跑步最准）：一个没有 png 的零件写 `hose`，胯跟着身体走，脚踩在固定地面上：
  `"legs": {"parent": "root", "hose": {"hips": [[0.44, 0.85], [0.56, 0.85]], "ground": 1.13, "stride": 0.11, "lift": 0.1, "width": 0.045, "shoe": 0.16, "from": 0.57, "runFrom": 1.0, "dir": 1}}`
  `hips`、`ground` 是图片的比例坐标；`stride`、`lift`、`width`、`shoe` 是图片宽度的比例；跑步默认 5 帧一步、4 张画（参考片量的）。腿零件写在身体前面，就画在身体后面。

## 先看 SVG 里有哪些组
```bash
mm layers 角色.svg      # 列出每个组的 id 和范围（SVG 坐标）
```

## 写法（graphics 里的一块）
```json
{
  "art": "cat.svg",
  "rig": {
    "enter": {"motion": "peek", "from": "bottom", "clip": "body", "at": 0.3},
    "parts": {
      "body":        {"pivot": [259, 626]},
      "head":        {"parent": "body", "pivot": [298, 466]},
      "eyes-open":   {"parent": "head"},
      "eyes-closed": {"parent": "head", "hidden": true},
      "cup":         {"parent": "body", "pivot": [352, 520]},
      "steam-0":     {"parent": "cup"}
    },
    "acts": [
      {"do": "rotate", "part": "head", "keys": [[0.9, 0], [1.15, -9], [1.6, 9], [2.0, 0]]},
      {"do": "hop", "part": "body", "at": 2.15, "height": 34},
      {"do": "float", "part": "steam-0", "from": 1.2, "rise": 22, "period": 1.5},
      {"do": "blink", "open": "eyes-open", "closed": "eyes-closed", "at": [1.45, 5.1]}
    ]
  },
  "groups": [{"name": "title", "box": [100, 135, 728, 255], "motion": "pop", "at": 2.45}]
}
```
- `parts`：要动的组。`pivot` 是轴心（SVG 坐标，转动、缩放都绕它），`parent` 是父零件（头跟着身体走），`hidden` 表示一开始不显示（闭眼）。
- 没写进 `parts` 的内容（比如字）照常拆零件，用 `groups` 或 `auto` 让它们动。
- `enter`：角色整体怎么入场，可以用任何整块动法和桥段：`popin` `drop` `splat` `drip` `melt` `slidein` `peek` `grow` `draw`…
  - `peek` 加 `clip: "body"`：从角色自己的地面线后面冒出来。
  - `clip: "none"`：靠表面边缘遮住（比如从橱窗下沿冒出来）。

## 表演 acts
| do | 写法 | 用途 |
|---|---|---|
| `rotate` | `keys: [[秒, 角度], …]` | 歪头、挥手、举杯 |
| `move` | `keys: [[秒, dx, dy], …]`（SVG 单位） | 杯子送到嘴边 |
| `scale` | `keys: [[秒, sx, sy], …]` | 张嘴、鼓腮 |
| `show` | `keys: [[秒, 透明度], …]` | 换表情（睁眼 ↔ 闭眼）、换手势 |
| `blink` | `open` `closed` `at: [秒…]` 或 `every` | 眨眼 |
| `hop` | `at` `height` `dur` `impact` | 蹲一下 → 起跳拉长 → 落地压扁；落地算冲击（字会被震起，带音效） |
| `wiggle` | `amp`（度）`period` `from` | 吸管晃、尾巴摇 |
| `bob` | `amp_px` `period` `from` | 珍珠颤、上下浮 |
| `float` | `rise` `period` `phase` `from` | 热气一缕缕往上飘、循环 |
| `impact` | `at` `amp` `shake` `sfx` | 自己用 `move` 做的落地：标一个冲击点（别的元素被震起来、镜头轻推），`sfx` 配一声 |

关键帧之间默认 sine 缓入缓出；每段可以单独指定：`"eases": ["smooth", "ease-out", ...]`（第 i 个对应第 i → i+1 个关键帧，可选 linear / smooth / ease-in / ease-out / ease-in-out）。
另外两个：`fade`（连续透明度 `[[秒, 0~1], …]`）、`reveal`（自上而下露出 `[[秒, 0~1], …]`）。

## 怎么让表演好看（来自参考片）
- **先平静，再出事**：先张望、慢慢探头，再突然吓一跳（`alert` 点缀 + `hop`）。反差就是笑点。
- **动作有因果**：跳起来落地 → 字被震起来；门拉到底 → 角色才做反应。
- **一直有小循环**：热气、眨眼、吸管晃，画面不要停住。
- **一次只突出一个动作**，不要所有部位一起乱动。

## 角色在整个画面里移动（10/14，scribble-pop 用到）
- `"travel": true`：角色画布用整张图，不按角色范围裁——角色要从一头飞到另一头时必须开，否则会被裁掉。姿势图都画在和底图一样大的透明画布上、脚底对齐同一点，`root` 的轴心放在那一点，用 `move` / `scale` 关键帧带着整个角色走、变大变小。
- `"shadow": {"ground": 0.84, "size": 0.08, "opacity": 0.55, "lift": 0.25}`：接触影子画在地面线（图片高度比例）上，跟着根零件的脚走；离地越高越淡越小（`lift` 是消失的高度，图片高度比例）。
- `move` 的数值是工作尺寸（图形最长边缩到 1200）：画面像素 × 1200 / 画面最长边。
