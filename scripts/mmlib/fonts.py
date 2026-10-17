"""自检图上的标签字体：找一个能写中文的系统字体（mm grid 图例、摆放自检图、cutout 对照图用）。"""
import os

_PATHS = ('/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc', '/System/Library/Fonts/STHeiti Medium.ttc',
          '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
          '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc', 'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf')


def cjk_font(size):
    """返回能写中文的字体；一个都找不到返回 None，调用方改写英文标签"""
    from PIL import ImageFont
    for p in _PATHS:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except OSError: pass
    return None
