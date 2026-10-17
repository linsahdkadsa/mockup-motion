# 空白底图提示词

用户没有底图时，把下面的提示词给用户，用他自己的生图工具出图（Nano Banana、GPT Image、Midjourney、Seedream 都可以）。

## 底图的硬要求
1. **表面完全空白**：上面没有任何字、图案、logo。
2. **表面平整**，形状接近四边形，正对镜头或者稍微斜一点。
3. **表面占画面的 1/4 以上**，前面没有东西挡着。
4. **光线柔和**，表面上没有强烈的光斑或阴影切线。有一点自然的明暗渐变反而更真实。

## 通用结尾（每条提示词后面都加上）
> The surface is completely blank with no text, no logo, no graphics. Flat surface, nothing covering it, soft natural light, photorealistic, high detail.

## 舞台模板（吸收自 Codex，推荐）
> Photorealistic editorial {architectural / product} photograph, {3:4 vertical / 16:9}. {场景一句话}. The {表面：hanging sign / glass window / paper bag front} is the hero: a single large completely blank {材质和颜色} surface, its full four corners visible and unobstructed, occupying about {58}% of the image width and {64}% of the image height, centered near x={50}%, y={46}%. Camera nearly face-on, slightly to one side, subtle clear planar perspective, 50mm lens. The surface is calm and evenly lit, no strong shadow bands across it. {背景和小道具，虚化}. Restrained palette of {2–3 个颜色}, tactile real materials, quiet premium mood. Absolutely no text, letters, logos, printed designs, characters, illustrations, stickers or watermarks anywhere; the surface must be entirely blank so artwork can be composited later.

## 场景要「有戏」，不要一面空墙
- 场景里要有**环境和小道具**：吧台、高脚凳、冷柜、自行车、盆栽、路人（虚化）、托盘上的食物。
- 尽量有**两个以上的空白表面**（橱窗 + 门口立式小黑板、纸袋 + 薯条盒），一段里可以让图形在两处先后出现。
- 光线要有情绪：黄昏、夜景霓虹、暖光室内。
- 表面要大，占画面一半左右；太远就用 `mm frame` 从高清原图裁近一点。

实测效果好的几个（2026-10）：
- 复古美式餐厅：吧台后一大块光面红墙板，旁边玻璃冷柜里是汽水瓶，霓虹 DINER 灯
- 快餐托盘：纸袋（正面平）+ 红色薯条盒 + 纸杯 + 汉堡，暖色窗光
- 街头篮球场：黄昏，一大面浅灰水泥墙、篮框、地上一个篮球、长影子
- 咖啡店橱窗 + 人行道上的立式小黑板：黄昏暖光、屋里有人虚化、落叶
- 夜晚旗舰店：石材外立面，入口上方一块深色玻璃板，湿地面反光
- 科技公司：前景一大块磨砂玻璃，后面是彩色休息区和办公的人

## 场景模板（把方括号换掉）
- **挂牌**：A [cream / white / black] rectangular metal blade sign hanging from a black iron bracket on the corner of a [bakery / café] building, [morning sunlight], blurred street background.
- **外墙招牌**：A square [dark green / navy] matte painted sign mounted flat on a [concrete / brick] wall beside a shop entrance, viewed slightly from the left.
- **海报**：A large [cream] paper poster mounted flat on a [dark charcoal] wall inside a cozy café, warm pendant lights, shelves softly blurred in the background.
- **橱窗贴纸**：A clean storefront glass window of a [dessert shop], the glass is clear and empty, interior softly visible, daytime.
- **纸袋**：A brown kraft paper shopping bag with paper handles standing on a white marble counter, the flat front panel facing the camera.
- **卷帘门**：A closed white metal roller shutter on a shopfront, frontal view, street level.
- **外立面**：A white modern building facade with a large plain wall area above the entrance, frontal view.
- **窗户 + 上方招牌板**（peek 用：角色在窗里，店名放招牌板）：A small shopfront seen straight on: one large clean glass window with a softly lit, slightly dark interior behind it, and directly above the window a wide completely blank [dark green] painted sign board. The window and the sign board are fully visible, the window takes about half of the image, warm afternoon light, nobody in the window.

## 已有一张带 logo 的样机图？
先让用户把 logo 擦掉，再拿空白版当底图。用图生图的修图提示词：
> Remove all printed graphics and text from the [sign] so the surface is plain blank [color]. Keep everything else exactly the same: same framing and camera angle, same lighting and background.

实测 Nano Banana Pro 擦出来的空白版和原图逐像素对齐。

## 卷帘门要「拉下来」？再要一张打开版
同一张关着的卷帘门底图，用图生图改成打开：
> Edit this photo: the roller shutter is now fully rolled up and open, revealing a dark empty shop interior behind the doorway. Keep everything else exactly the same: same framing and camera angle, same shutter housing box at the top, same door frame, wall, sidewalk and lighting.

## 角色关键姿势（图生图，以第一张为底图）
先把角色重画成高清干净线稿：
> Redraw this exact line-art illustration as a clean, crisp, high-resolution illustration on a pure white background. Keep exactly the same style: thick uniform black outlines with rounded ends, the same fills, no shading, no text. Same pose, same proportions, centered with generous white margins.

再以它为底图出别的姿势或表情（每次只改一件事）：
> Same character, same exact line-art style, same size and same position in the frame, pure white background. Change only: [eyes open looking at the cup with a small smile / eyes closed happily with a big smile / surprised with wide eyes and open mouth]. Keep everything else at the same place.

一个动作一般 3 张就够：预备（看着东西）→ 动作（喝、吃、跳）→ 反应（满足、吓一跳）。

## 中间画（让动作更顺）
把前后两张关键姿势一起作为参考图（第一张放前面的姿势），提示词：
> Image 1 and image 2 are two key poses of the same character. Draw the in-between drawing exactly halfway from image 1 to image 2: [描述中间状态，比如 knees half bent, arms coming down halfway]. Exact same style, colors, size and position in the frame, same background.

实测 Nano Banana Pro 画出来和前后两张逐像素对齐。每张中间画在时间轴上只停 2–3 帧。

## 多个角色
可以画在同一张图里（同一画风、同一次生成，比例、位置最协调），再用 `mm split` 切开成单独的角色。切开后会给每人重新描一圈白边，两人不会粘成一张贴纸。

## 白色窗贴 / 玻璃上的白线稿（10/13）
在纯黑底上出白图，出完用 `mm decal 图.jpg`（线细就加 `--bold 3`）转成透明白贴纸。`mm decal` 默认会裁掉四周空白；几张姿势要逐像素对齐时加 `--keep-margin`：
> White vinyl window decal artwork on a solid pure black background: {角色或字}. Bold flat white shapes, details as thin black negative-space lines. Only pure white and pure black, no gray, no frame, no text. Centered with generous black margin.

- 字标要能一个个弹出来，提示词里写 every letter separate and not touching。还是连笔的就重出一张；实在要用，就在修图软件里沿最细处切开，字母之间留一条透明缝（`art-direction.md` 3.5）。
- 白色产品（纸袋、杯子）先改黑底：`Edit this product photo: replace the background with a solid flat pure black background, no shadow. Keep the product exactly the same.`，再从黑底版改表情。

## 照参考片的姿势出「自己的角色」（10/14）
要同一个角色摆出参考片里的几个姿势（比如跑步循环的 5 张）：
- **只给姿势参考图，不要把角色基础图也放进参考**——两张一起给，AI 会直接照抄基础图，姿势一点不变（实测 3 次都这样）。
- 角色靠**同一段文字描述**锁住：长相、衣服、配色、线条风格、白描边一字不改，每个姿势各跑一次，出来就是同一个人。
> Redraw the character in this image as a new character, keeping exactly the same body pose, limb positions, framing and size: {角色描述}. {风格：几种颜色、线条、白描边}. Replace the background with a solid flat mid gray, no shadow, no text.
- 灰底比白底好抠（白描边贴纸在白底上分不开）。

## 街景 + 真人走动底片（street-crowd，10/15）
**街景**（Midjourney 或别的出片感强的生图工具，16:9；Midjourney 用 `--stylize 120`）——原片构图：站在路口一角，近处一栋楼占一侧被画面边裁掉，中间一块天：
> cinematic street photo standing at a sunny downtown street corner at low eye level, a tall pale stone building very close to the camera fills the right third of the frame and is cut off by the right edge, its corner and a traffic light pole in front of it, a wide zebra crosswalk running diagonally across the lower part of the frame, a row of leafy street trees and old lamp posts along the far sidewalk, classic mid-rise stone buildings on the far side of the street, a clear pale blue sky above the far buildings in the upper middle, a few tiny distant pedestrians, nobody close to the camera, warm afternoon light, shot on 35mm --no text, logo, signs with words

- 备选「低楼、上半张天空」（small-town main street, low two- and three-story buildings, the upper half of the image is open sky）：好放，但大怪没东西挡，显小、像气球。

**去近景**（能照原图改的改图模型，比如 Nano Banana Pro；参考图 = 上一张空街景，给 `mm occluder` 用）：
> Edit this street photo: remove the big building close to the camera on the right side, its traffic light pole, all the street trees and all the lamp posts. Where they were, continue the far background naturally: more distant mid-rise buildings, the far sidewalk and the clear pale blue sky. Keep everything else exactly the same: same framing, same far buildings on the left, same crosswalk and road, same light and colors.

**把行人画进去**（同一个改图模型，参考图就是街景）：
> Edit this street photo: add three ordinary real pedestrians seen from behind, walking away from the camera across the zebra crosswalk, already in the middle distance. Their feet are on the crosswalk stripes in the lower part of the image, and each person is about one third of the image height. {每个人：年纪、衣服、在画面哪个位置}. Matching sunlight direction and soft shadows on the road. Keep everything else in the photo exactly the same.

**让他们走起来**（用你手上的图生视频工具，海螺 H3、Seedance 都行：首帧 = 上一张图，只放这一张参考；固定机位；时长 ≥ 成片；不要声音；比例跟首帧走）：
> Locked-off static camera on a tripod, absolutely no camera movement, no zoom, no pan. The three pedestrians keep walking away from the camera across the zebra crosswalk toward the far sidewalk at a normal relaxed pace, seen from behind, getting gradually smaller as they move into the distance. Nobody else enters the frame. Buildings, trees and sky stay exactly the same, natural daylight, realistic documentary street footage.
- 镜头运动留给代码（`camera`），视频一定要固定机位，天空遮罩、近处遮挡层才对得上。`mm plate` 会报首尾差，超过 25 多半是镜头动了。
- 出片一般要几分钟，价格看你用的平台。视频是 24、25 还是 30 帧都行：`mm plate` 用 `--dur`（取几秒）、`--start-sec`（从第几秒开始）按秒取，自动按视频帧率换算帧数，帧率记进 `plate/plate.json`，渲染时自动对上。
- 摆人时**人和人之间留出一个小怪的宽度**，小怪才能夹在人中间走。

**走路小怪**（GPT Image 这类平涂干净的生图模型，3:2，2K，灰底）：
> flat 2D cartoon sprite sheet on a plain solid medium gray (#808080) background. Two original cute characters, each drawn 4 times as a walk cycle, seen in three-quarter view from behind walking away into the distance, head turned to the side so the face (eye, cheek, smile) is visible in profile. Long thin noodle arms and legs, big bouncy strides with the front foot far ahead of the back foot, arms swinging opposite to the legs. Top row = {怪 A}. Bottom row = {怪 B}. Bold flat colors with hard clean edges, no texture, no grain, no outlines, no ground shadows, same size and proportions in every pose, well separated, nothing touching, no text
- 验收：能看到脸；第 1、3 张前后脚距离 ≥ 身高 0.3；有颗粒纹理、纯背影就重出。

**大怪 + 配角**（同上，3:2，2K，灰底）：
> flat 2D cartoon character sheet on a plain solid medium gray (#808080) background, bold flat colors with hard clean edges, no texture, no grain, no outlines, no ground shadows, every figure well separated and nothing touching, no text. Left half: one GIANT {大怪}, only the upper body (head and shoulders and two big arms), seen in three-quarter view from its side, leaning forward and peeking, two big round open eyes looking down to the lower left, its hand raised with chunky fingers curled forward as if gripping the edge of a wall, bottom edge cut straight. Right half: five small cute standing characters with clearly different silhouettes and dot eyes: {配角 ×5}
- 不要「正面挥手」：原片的大怪是侧身往下看街、手扣着楼角。

**落款字标**（同上，3:2，2K）：
> Hand-lettered shop wordmark that reads exactly "{店名}": {字体风格}, pure white letters on a solid pure black background, every letter clearly separate and not touching, centered with generous black margin, no other text
- 然后 `mm decal 字标原图.png --color 对比色 --out 镜1/sp/wordmark.png`（自动裁掉空白；要放进镜头文件夹里，`mm package` 只认那里的文件），比系统字好看得多。

