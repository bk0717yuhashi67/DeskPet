"""配色：平面卡通色板，走"头全黑的傻企鹅"路子。

深蓝黑身体（头与身体同一个黑）+ 白肚皮 + 白眼底黑瞳 + 橙色宽喙与脚蹼 + 红围脖。
所有颜色都偏"沉稳不刺眼"，因为它要长时间待在桌面上。
"""
from __future__ import annotations

from PySide6.QtGui import QColor

# 身体（头与身体共用，所以整只企鹅是一颗黑团子）
BODY = QColor("#17253F")
BODY_RIM = QColor("#3A5A96")        # 右上方向的边缘冷光
BODY_TOP_HILITE = QColor(255, 255, 255, 30)
WING = QColor("#1D2E50")            # 翅膀比身体略亮一点，否则糊成一团

# 肚皮
BELLY = QColor("#FDFDFD")
BELLY_SHADE = QColor("#E4EAF4")

# 喙（只画一层，所以只需要一个主色）
BEAK = QColor("#F6A21E")
BEAK_SHADE = QColor("#DE8612")
MOUTH = QColor("#4A1B1E")

# 脚蹼
FOOT = QColor("#F08A24")
FOOT_SHADE = QColor("#C96E12")

# 围脖
SCARF = QColor("#D8342E")
SCARF_SHADE = QColor("#A82622")

# 眼睛：白眼底 + 黑点
EYE_WHITE = QColor("#FFFFFF")
EYE = QColor("#10161F")
HILITE = QColor("#FFFFFF")

# 腮红
BLUSH = QColor("#F58AA0")

# 描边
OUTLINE = QColor("#0B1220")
OUTLINE.setAlpha(165)
OUTLINE_W = 1.7

# 阴影
SHADOW = QColor(0, 0, 0, 30)

# 道具
PROP_Z = QColor("#8FA3C8")
PROP_STAR = QColor("#FFC94D")
PROP_HEART = QColor("#FF6B81")
PROP_BALL = QColor("#FF8A3D")

# 背景（数据面板用）
PANEL_BG = QColor("#F4F7FC")
PANEL_CARD = QColor("#FFFFFF")
PANEL_LINE = QColor("#E4EAF4")
PANEL_TEXT = QColor("#1B2A4A")
PANEL_SUB = QColor("#7C8AA3")
PANEL_ACCENT = QColor("#2B6FE0")
