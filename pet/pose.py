"""姿势参数：动画系统的唯一数据载体。

Pose 是一个扁平的参数包，渲染层只认它，动画层只产出它。
这样 rig 不需要知道任何时间/动作逻辑，animator 也不需要知道任何绘制细节。
"""
from __future__ import annotations

from dataclasses import dataclass, fields

# ------------------------------------------------------------------ 画布常量
DESIGN = 200.0          # 设计画布尺寸（所有几何坐标都基于它）
OFF_X = 20.0            # 设计坐标 -> 窗口坐标的偏移（给跳跃留头顶空间）
OFF_Y = 20.0
WIN_W = int(DESIGN + OFF_X * 2)     # 240
WIN_H = int(DESIGN + OFF_Y * 2)     # 240

# ------------------------------------------------------------------ 躯干
# 造型思路：**矮胖圆滚滚**。原来身体是 42x48 的窄梨形 + 一个独立的圆脑袋，
# 整体高瘦（高宽比 1.79），头看上去就是"挂了一个球"。
# 现在把两个椭圆做得又宽又接近圆，并靠并集合成**同一个黑色轮廓**，
# 高宽比降到 1.29，"头是个圆"这件事就看不出来了。
BODY_CX = 100.0
BODY_CY = 142.0
BODY_RX = 56.0
BODY_RY = 44.0          # 轮廓 98..186
FEET_Y = 182.0          # 整体变换的枢轴（脚底），挤压拉伸都绕它

# ------------------------------------------------------------------ 头部
HEAD_CX = 100.0
HEAD_CY = 74.0
HEAD_RX = 43.0
HEAD_RY = 38.0          # 轮廓 36..112
# 头部旋转的枢轴放在"脖子"处：头晃动时像是从身上长出来的，
# 而不是绕头顶打转。并集轮廓也用它，所以头和身体永远是连着的。
#
# 这两个椭圆的相对位置是**刻意**留出一段收腰的：头 43 宽、身体 56 宽、
# 交界处只有约 30，于是并集轮廓在 y≈104 处天然有个"脖子"。
# 没有这段收腰，围脖就只是一条贴在肚子上的红带子（用户反馈过）。
HEAD_PIVOT_Y = 104.0

# ------------------------------------------------------------------ 五官
EYE_CY = 72.0
EYE_DX = 19.0           # 两只眼睛相对中线的水平距离（拉开一点更呆）
EYE_RX = 12.5
EYE_RY = 13.5
PUPIL_R = 5.2
BEAK_CY = 96.0          # 喙的参考高度（实际绘制在 rig 里）

# ------------------------------------------------------------------ 翅膀
WING_PIVOT_L = (52.0, 122.0)
WING_PIVOT_R = (148.0, 122.0)

# ------------------------------------------------------------------ 道具
PROP_NONE = 0
PROP_Z = 1
PROP_STAR = 2
PROP_HEART = 3
PROP_BALL = 4

# 参与插值的字段（flip / prop 是离散量，单独处理）
_LERP_FIELDS = (
    "body_x", "body_y", "body_sx", "body_sy", "body_rot",
    "head_x", "head_y", "head_rot",
    "wing_l", "wing_r", "wing_l_stretch", "wing_r_stretch",
    "eye_open", "eye_squint", "pupil_x", "pupil_y", "beak_open",
    "foot_l_y", "foot_r_y",
    "scarf_wave", "scarf_on", "blush", "alpha",
)


@dataclass(slots=True)
class Pose:
    # 整体
    body_x: float = 0.0
    body_y: float = 0.0
    body_sx: float = 1.0        # 横向缩放（挤压拉伸）
    body_sy: float = 1.0        # 纵向缩放
    body_rot: float = 0.0       # 整体倾斜（度），绕脚底

    # 头部
    head_x: float = 0.0
    head_y: float = 0.0
    head_rot: float = 0.0

    # 翅膀（度，正值为顺时针；左翅 +12 表示自然外张）
    wing_l: float = 12.0
    wing_r: float = -12.0
    wing_l_stretch: float = 1.0
    wing_r_stretch: float = 1.0

    # 表情
    eye_open: float = 1.0
    eye_squint: float = 0.0
    pupil_x: float = 0.0
    pupil_y: float = 0.0
    beak_open: float = 0.0
    blush: float = 0.0

    # 脚
    foot_l_y: float = 0.0
    foot_r_y: float = 0.0

    # 配饰 / 其他
    scarf_wave: float = 0.0     # 0..1 相位，驱动飘带与道具动画
    scarf_on: float = 1.0       # 0 = 没戴围脖
    alpha: float = 1.0
    flip: float = 1.0           # 1 朝右 / -1 朝左（镜像轴 x=100）
    prop: int = PROP_NONE

    def copy(self) -> "Pose":
        return Pose(*[getattr(self, f) for f in _ALL])


_ALL = tuple(f.name for f in fields(Pose))


def pose_from(overrides: dict) -> Pose:
    """从部分字段构造完整 Pose，未给出的字段用默认值。"""
    p = Pose()
    for k, v in overrides.items():
        if hasattr(p, k):
            setattr(p, k, v)
    return p


def pose_lerp(a: Pose, b: Pose, t: float) -> Pose:
    """逐字段线性插值。离散字段（flip / prop）取 b 的值。"""
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    out = Pose()
    for f in _LERP_FIELDS:
        va = getattr(a, f)
        vb = getattr(b, f)
        setattr(out, f, va + (vb - va) * t)
    # 镜子方向在过半时翻转，避免中途出现"压扁的侧面"以外的怪异状态
    out.flip = a.flip if t < 0.5 else b.flip
    out.prop = a.prop if t < 0.5 else b.prop
    return out


def apply_volume(p: Pose, amount: float = 0.6) -> None:
    """体积守恒：横向压扁时纵向自动拉长，让 squash & stretch 更自然。"""
    delta = 1.0 - p.body_sx
    p.body_sy = 1.0 + delta * amount
