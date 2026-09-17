"""企鹅部件绘制（rig）。

造型：一只**矮胖圆滚滚、头全黑**的傻企鹅。
头不是单独一个圆——头和身体是**同一个黑色轮廓**（两个椭圆的并集生成），
所以看不出"头是个球"；配白肚皮、白眼底 + 两个小黑点、单层宽三角喙、红围脖。

只做几何绘制，不含任何状态与时间逻辑——输入 Pose，输出图形。
所有坐标基于 200x200 设计画布；窗口坐标 = 设计坐标 + (OFF_X, OFF_Y)，
外加一套"以脚底为枢轴"的整体变换（平移 / 旋转 / 挤压拉伸 / 水平翻转）。

绘制顺序（后 -> 前）：
    地面阴影 -> 脚蹼 -> 合并轮廓(头+身体) -> 肚皮 -> 五官(眼/喙/腮红) -> 翅膀 -> 围脖 -> 道具

两个刻意的设计约束，都是为了"别在动画里穿帮"：

1. **头是并集算出来的，不是叠上去的圆。** 头部动画（head_x/head_y/head_rot）
   直接作用在头椭圆的形状上再和身体取并集，任何幅度下轮廓都是连续的，
   不会出现两个圆互相错位露出的接缝。
2. **喙只有一层三角形。** 以前是上喙 + 下喙两层各自位移，插值稍微越界就会
   露出中间的黑缝、或者两层互相翻过去。现在张嘴 = 把同一个三角形整体拉长，
   结构上不可能穿帮。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTransform,
)

from . import palette as pal
from .pose import (
    BODY_CX,
    BODY_CY,
    BODY_RX,
    BODY_RY,
    EYE_CY,
    EYE_DX,
    EYE_RX,
    EYE_RY,
    FEET_Y,
    HEAD_CX,
    HEAD_CY,
    HEAD_PIVOT_Y,
    HEAD_RX,
    HEAD_RY,
    OFF_X,
    OFF_Y,
    PUPIL_R,
    PROP_BALL,
    PROP_HEART,
    PROP_NONE,
    PROP_STAR,
    PROP_Z,
    WING_PIVOT_L,
    WING_PIVOT_R,
    Pose,
)

# ------------------------------------------------------------------ 几何常量
PUPIL_MAX = 3.0            # 黑点跟随视线的最大偏移（刻意很小，保持"呆"）
EYE_CLOSE_AT = 0.45        # eye_open 低于这个值就画弯钩（闭眼）

BEAK_W = 27.0              # 喙的底边宽度（比原来的 20 宽得多）
BEAK_TOP = 89.0            # 喙的基线高度
BEAK_H = 11.0              # 喙的基础高度

FOOT_L = (78.0, 182.0)
FOOT_R = (122.0, 182.0)
FOOT_RX, FOOT_RY = 19.0, 9.0
FOOT_TOE = 6.0

BELLY_C = (100.0, 148.0)
BELLY_RX, BELLY_RY = 42.0, 35.0

BLUSH_L = (71.0, 96.0)
BLUSH_R = (129.0, 96.0)
BLUSH_R_ = 7.5

# 围脖：两端落在"脖子"最细的地方（y≈106），中间下垂到 y≈120 盖住胸口。
# 下垂量很关键——正因为中间垂下来，喙尖（最高到 107）才不会和围脖撞上。
SCARF_Y = 106.0            # 围脖两端的高度 = 脖子的位置
SCARF_DIP = 7.0            # 中间下垂量
SCARF_W = 11.0             # 围脖粗细
SCARF_X0, SCARF_X1 = 67.0, 133.0
KNOT_C = (74.0, 114.0)     # 结打在脖子侧面

SHADOW_CY = 190.0
SHADOW_RX, SHADOW_RY = 48.0, 9.5

WING_LOCAL_CY = 15.0
WING_RX, WING_RY = 13.0, 23.0


# --------------------------------------------------------------------------
# 坐标变换
# --------------------------------------------------------------------------
def transform_point(pose: Pose, x: float, y: float) -> tuple[float, float]:
    """把设计坐标点映射到窗口坐标，与 _apply_body_transform 的顺序保持一致。"""
    # 1. 以脚底为枢轴的挤压拉伸
    x = BODY_CX + (x - BODY_CX) * pose.body_sx
    y = FEET_Y + (y - FEET_Y) * pose.body_sy
    # 2. 水平翻转（镜像轴 x = 100）
    if pose.flip < 0:
        x = 2 * BODY_CX - x
    # 3. 以脚底为枢轴的整体倾斜
    a = math.radians(pose.body_rot)
    ca, sa = math.cos(a), math.sin(a)
    dx, dy = x - BODY_CX, y - FEET_Y
    x = BODY_CX + dx * ca - dy * sa
    y = FEET_Y + dx * sa + dy * ca
    # 4. 平移 + 画布偏移
    return x + pose.body_x + OFF_X, y + pose.body_y + OFF_Y


def _inverse_transform_point(pose: Pose, wx: float, wy: float) -> tuple[float, float]:
    """transform_point 的严格逆运算：窗口坐标 -> 设计坐标。"""
    x = wx - OFF_X - pose.body_x
    y = wy - OFF_Y - pose.body_y
    # 逆旋转
    a = math.radians(-pose.body_rot)
    ca, sa = math.cos(a), math.sin(a)
    dx, dy = x - BODY_CX, y - FEET_Y
    x = BODY_CX + dx * ca - dy * sa
    y = FEET_Y + dx * sa + dy * ca
    # 逆翻转
    if pose.flip < 0:
        x = 2.0 * BODY_CX - x
    # 逆缩放（防 0 除）
    sx = pose.body_sx if abs(pose.body_sx) > 1e-6 else 1e-6
    sy = pose.body_sy if abs(pose.body_sy) > 1e-6 else 1e-6
    return (BODY_CX + (x - BODY_CX) / sx, FEET_Y + (y - FEET_Y) / sy)


def _head_qtransform(pose: Pose) -> QTransform:
    """头部变换：绕脖子枢轴旋转 + 平移。绘制与形状生成共用同一套。"""
    t = QTransform()
    t.translate(pose.head_x, pose.head_y)
    t.translate(BODY_CX, HEAD_PIVOT_Y)
    t.rotate(pose.head_rot)
    t.translate(-BODY_CX, -HEAD_PIVOT_Y)
    return t


def head_transform_point(pose: Pose, x: float, y: float) -> tuple[float, float]:
    """把设计坐标按头部动画变换一次（用于把五官/头顶锚点摆到正确位置）。"""
    p = _head_qtransform(pose).map(QPointF(x, y))
    return p.x(), p.y()


def _in_ellipse(px: float, py: float, cx: float, cy: float, rx: float, ry: float) -> bool:
    if rx <= 0.0 or ry <= 0.0:
        return False
    return ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1.0


def hit_test(pose: Pose, wx: float, wy: float, margin: float = 6.0) -> bool:
    """窗口坐标点是否落在企鹅身上。

    先把点逆变换回设计坐标，再分别对"身体椭圆 / 头部椭圆"做判定——
    这样身体旋转、挤压拉伸、水平翻转都能被正确处理（旧版忽略旋转，
    在翻跟头、摔倒这类动作里判定区会和实际图形错开）。
    """
    x, y = _inverse_transform_point(pose, wx, wy)
    k = (abs(pose.body_sx) + abs(pose.body_sy)) * 0.5
    m = margin / max(0.25, k)

    if _in_ellipse(x, y, BODY_CX, BODY_CY, BODY_RX + m, BODY_RY + m):
        return True

    inv, ok = _head_qtransform(pose).inverted()
    if ok:
        hp = inv.map(QPointF(x, y))
        if _in_ellipse(hp.x(), hp.y(), HEAD_CX, HEAD_CY, HEAD_RX + m, HEAD_RY + m):
            return True
    return False


def outline_top(pose: Pose) -> tuple[float, float]:
    """头顶最高点（设计坐标，含头部动画），气泡锚点/道具悬浮用它。"""
    return head_transform_point(pose, HEAD_CX, HEAD_CY - HEAD_RY)


def _apply_body_transform(p: QPainter, pose: Pose) -> None:
    p.translate(OFF_X + pose.body_x, OFF_Y + pose.body_y)
    p.translate(BODY_CX, FEET_Y)
    p.rotate(pose.body_rot)
    p.scale(pose.body_sx, pose.body_sy)
    p.translate(-BODY_CX, -FEET_Y)
    if pose.flip < 0:
        p.translate(BODY_CX, 0.0)
        p.scale(-1.0, 1.0)
        p.translate(-BODY_CX, 0.0)


def _apply_head_transform(p: QPainter, pose: Pose) -> None:
    p.translate(pose.head_x, pose.head_y)
    p.translate(BODY_CX, HEAD_PIVOT_Y)
    p.rotate(pose.head_rot)
    p.translate(-BODY_CX, -HEAD_PIVOT_Y)


# --------------------------------------------------------------------------
# 几何构造
# --------------------------------------------------------------------------
def body_shape_path() -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(BODY_CX, BODY_CY), BODY_RX, BODY_RY)
    return path


def head_shape_path(pose: Pose) -> QPainterPath:
    """头部椭圆按头部动画变换后的形状（设计坐标）。"""
    base = QPainterPath()
    base.addEllipse(QPointF(HEAD_CX, HEAD_CY), HEAD_RX, HEAD_RY)
    return _head_qtransform(pose).map(base)


def silhouette_path(pose: Pose) -> QPainterPath:
    """整只企鹅的轮廓 = 身体椭圆 ∪ 头部椭圆。

    这是"头全黑、看不出头是个圆"的关键：两者取并集后只有一条外边线，
    中间那道圆是看不见的。头部动画作用在头椭圆上，所以头摆动时
    轮廓仍然是连续的（脖子处会自然地被拉出来一点，像真的脖子）。
    """
    return body_shape_path().united(head_shape_path(pose))


def head_path(pose: Pose | None = None) -> QPainterPath:
    """未变换的头部椭圆（设计坐标）。画五官时用它做裁剪。"""
    path = QPainterPath()
    path.addEllipse(QPointF(HEAD_CX, HEAD_CY), HEAD_RX, HEAD_RY)
    return path


def belly_path() -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(*BELLY_C), BELLY_RX, BELLY_RY)
    return path


def wing_path(angle: float = 0.0, stretch: float = 1.0) -> QPainterPath:
    """翅膀：绕枢轴的椭圆，局部坐标（枢轴在原点，翅向下垂）。"""
    path = QPainterPath()
    path.addEllipse(
        QPointF(0.0, WING_LOCAL_CY), WING_RX, WING_RY * stretch
    )
    return path


# --------------------------------------------------------------------------
# 各部件绘制
# --------------------------------------------------------------------------
def _draw_shadow(p: QPainter, pose: Pose) -> None:
    # 跳得越高，影子越小越淡
    lift = max(0.0, -pose.body_y)
    k = max(0.35, 1.0 - lift / 90.0)
    px, py = transform_point(pose, BODY_CX, SHADOW_CY)
    p.save()
    p.setPen(Qt.NoPen)
    col = QColor(pal.SHADOW)
    col.setAlpha(int(pal.SHADOW.alpha() * k))
    p.setBrush(QBrush(col))
    p.drawEllipse(QPointF(px, py), SHADOW_RX * k, SHADOW_RY * k)
    p.restore()


def _draw_feet(p: QPainter, pose: Pose) -> None:
    for (cx, cy), dy in ((FOOT_L, pose.foot_l_y), (FOOT_R, pose.foot_r_y)):
        c = QPointF(cx, cy + dy)
        p.setPen(QPen(pal.OUTLINE, pal.OUTLINE_W))
        p.setBrush(QBrush(pal.FOOT))
        p.drawEllipse(c, FOOT_RX, FOOT_RY)
        # 两个趾缝
        p.setPen(QPen(pal.FOOT_SHADE, 1.5))
        for off in (-FOOT_TOE, FOOT_TOE):
            p.drawLine(
                QPointF(c.x() + off, c.y() - 1.4),
                QPointF(c.x() + off * 0.70, c.y() + FOOT_RY * 0.78),
            )


def _draw_silhouette(p: QPainter, pose: Pose) -> None:
    """整只企鹅的黑色轮廓：身体 + 头一次成型。"""
    path = silhouette_path(pose)

    p.setPen(QPen(pal.OUTLINE, pal.OUTLINE_W))
    p.setBrush(QBrush(pal.BODY))
    p.drawPath(path)

    # 右上方向的边缘冷光：内描一圈渐变，做出圆球体积感
    grad = QLinearGradient(QPointF(46.0, 192.0), QPointF(158.0, 44.0))
    grad.setColorAt(0.0, QColor(0, 0, 0, 0))
    grad.setColorAt(0.58, QColor(0, 0, 0, 0))
    grad.setColorAt(1.0, pal.BODY_RIM)
    p.save()
    p.setClipPath(path)
    pen = QPen(QBrush(grad), 9.0)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.restore()

    # 大脑门高光：圆圆的一坨才够傻
    hc = head_transform_point(pose, HEAD_CX, HEAD_CY)
    p.save()
    p.setClipPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(pal.BODY_TOP_HILITE))
    p.translate(hc[0] - 19.0, hc[1] - 23.0)
    p.rotate(-22.0)
    p.drawEllipse(QPointF(0.0, 0.0), 17.0, 9.0)
    p.restore()


def _draw_belly(p: QPainter, pose: Pose) -> None:
    path = belly_path()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(pal.BELLY))
    p.drawPath(path)

    # 肚皮下缘淡阴影，避免大白蛋一样平
    p.save()
    p.setClipPath(path)
    grad = QLinearGradient(QPointF(100.0, 150.0), QPointF(100.0, 186.0))
    grad.setColorAt(0.0, QColor(0, 0, 0, 0))
    grad.setColorAt(1.0, pal.BELLY_SHADE)
    pen = QPen(QBrush(grad), 7.0)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.restore()


def _draw_eyes(p: QPainter, pose: Pose, head_clip: QPainterPath) -> None:
    """白眼底 + 小黑点。闭眼时把黑点换成一道弯钩（眼白保留，看得见）。"""
    open_ = max(0.0, min(1.2, pose.eye_open))
    squint = max(0.0, min(1.0, pose.eye_squint))
    closed = open_ < EYE_CLOSE_AT

    # 越闭越扁；眯眼（笑眼 / 皱眉）再压一点
    ry = EYE_RY * (0.78 + 0.22 * min(1.0, open_)) * (1.0 - 0.40 * squint)
    rx = EYE_RX * (1.0 - 0.10 * squint)
    dot_r = min(PUPIL_R, rx * 0.62, max(1.6, ry * 0.62))

    p.save()
    p.setClipPath(head_clip)
    for c in _eye_positions():
        if closed:
            # 弯钩：朝上拱的一笔，一眼看得出是闭着的
            p.setPen(QPen(pal.EYE, 3.2, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(QBrush(pal.EYE_WHITE))
            p.drawEllipse(c, rx, ry)
            p.setBrush(Qt.NoBrush)
            hook = QPainterPath()
            hook.moveTo(c.x() - rx * 0.44, c.y() + ry * 0.30)
            hook.quadTo(c.x(), c.y() - ry * 0.52, c.x() + rx * 0.44, c.y() + ry * 0.30)
            p.drawPath(hook)
            continue

        p.setPen(QPen(pal.OUTLINE, 1.1))
        p.setBrush(QBrush(pal.EYE_WHITE))
        p.drawEllipse(c, rx, ry)
        p.setPen(Qt.NoPen)

        pc = QPointF(
            c.x() + max(-PUPIL_MAX, min(PUPIL_MAX, pose.pupil_x)),
            c.y() + max(-PUPIL_MAX, min(PUPIL_MAX, pose.pupil_y)),
        )
        p.setBrush(QBrush(pal.EYE))
        p.drawEllipse(pc, dot_r, dot_r)
    p.restore()


def _eye_positions() -> tuple[QPointF, QPointF]:
    return (
        QPointF(BODY_CX - EYE_DX, EYE_CY),
        QPointF(BODY_CX + EYE_DX, EYE_CY),
    )


def _draw_beak(p: QPainter, pose: Pose, head_clip: QPainterPath) -> None:
    """单层宽三角喙。

    张嘴 = 把同一个三角形整体向下拉长。没有第二层，就没有"两层错位露缝"
    这种穿帮方式——这是刻意的结构约束，不是为了省代码。
    """
    open_ = max(0.0, min(1.0, pose.beak_open))
    x0 = BODY_CX - BEAK_W / 2.0
    x1 = BODY_CX + BEAK_W / 2.0
    tip = BEAK_TOP + BEAK_H + open_ * 7.0

    path = QPainterPath()
    path.moveTo(x0, BEAK_TOP)
    path.lineTo(x1, BEAK_TOP)
    path.lineTo(BODY_CX, tip)
    path.closeSubpath()

    pen = QPen(pal.OUTLINE, 1.4)
    pen.setJoinStyle(Qt.RoundJoin)     # 圆角尖，别是刀片状
    pen.setCapStyle(Qt.RoundCap)
    p.save()
    p.setClipPath(head_clip)
    p.setPen(pen)
    p.setBrush(QBrush(pal.BEAK))
    p.drawPath(path)
    p.restore()


def _draw_blush(p: QPainter, pose: Pose, head_clip: QPainterPath) -> None:
    if pose.blush <= 0.02:
        return
    p.save()
    p.setClipPath(head_clip)
    p.setPen(Qt.NoPen)
    for c in (BLUSH_L, BLUSH_R):
        grad = QLinearGradient(
            QPointF(c[0] - BLUSH_R_, c[1]), QPointF(c[0] + BLUSH_R_, c[1])
        )
        mid = QColor(pal.BLUSH)
        mid.setAlpha(int(165 * min(1.0, pose.blush)))
        edge = QColor(pal.BLUSH)
        edge.setAlpha(0)
        grad.setColorAt(0.0, edge)
        grad.setColorAt(0.5, mid)
        grad.setColorAt(1.0, edge)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(c[0], c[1]), BLUSH_R_, BLUSH_R_ * 0.78)
    p.restore()


def _draw_wings(p: QPainter, pose: Pose) -> None:
    p.setPen(QPen(pal.OUTLINE, pal.OUTLINE_W))
    p.setBrush(QBrush(pal.WING))
    for pivot, angle, stretch in (
        (WING_PIVOT_L, pose.wing_l, pose.wing_l_stretch),
        (WING_PIVOT_R, pose.wing_r, pose.wing_r_stretch),
    ):
        p.save()
        p.translate(*pivot)
        p.rotate(angle)
        p.drawPath(wing_path(angle, stretch))
        p.restore()


def _draw_scarf(p: QPainter, pose: Pose) -> None:
    if pose.scarf_on <= 0.01:
        return
    p.save()
    p.setOpacity(pose.scarf_on)

    # 主体：一条中间下垂的粗弧线，像围在脖子上
    band = QPainterPath()
    band.moveTo(SCARF_X0, SCARF_Y)
    band.quadTo(BODY_CX, SCARF_Y + SCARF_DIP * 2.0, SCARF_X1, SCARF_Y)
    pen = QPen(pal.SCARF, SCARF_W)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(band)

    # 下缘暗部
    pen2 = QPen(pal.SCARF_SHADE, 2.2)
    pen2.setCapStyle(Qt.RoundCap)
    p.setPen(pen2)
    low = QPainterPath()
    low.moveTo(SCARF_X0 + 3.0, SCARF_Y + 7.0)
    low.quadTo(BODY_CX, SCARF_Y + SCARF_DIP * 2.0 + 7.0, SCARF_X1 - 3.0, SCARF_Y + 7.0)
    p.drawPath(low)

    # 结 + 两条飘带（飘带随 scarf_wave 摆动）
    knot_c = QPointF(*KNOT_C)
    p.setPen(QPen(pal.OUTLINE, 1.2))
    p.setBrush(QBrush(pal.SCARF))
    p.drawEllipse(knot_c, 10.0, 9.5)

    wave = math.sin(pose.scarf_wave * 2.0 * math.pi)
    for i, (dx, dy, ln) in enumerate(((2.0, 8.0, 26.0), (-3.0, 9.0, 20.0))):
        start = QPointF(knot_c.x() + dx, knot_c.y() + dy)
        c1 = QPointF(start.x() - 6.0 + wave * 3.0 * (1 + i), start.y() + ln * 0.5)
        c2 = QPointF(start.x() + 6.0 + wave * 4.0 * (1 + i), start.y() + ln * 0.9)
        end = QPointF(start.x() - 2.0 + wave * 7.0 * (1 + i), start.y() + ln)
        tail = QPainterPath(start)
        tail.cubicTo(c1, c2, end)
        pen3 = QPen(pal.SCARF, 6.0 - i)
        pen3.setCapStyle(Qt.RoundCap)
        p.setPen(pen3)
        p.drawPath(tail)

    p.restore()


def _draw_prop(p: QPainter, pose: Pose) -> None:
    if pose.prop == PROP_NONE:
        return
    hx, hy = transform_point(pose, *outline_top(pose))
    phase = pose.scarf_wave % 1.0

    if pose.prop == PROP_Z:
        # 睡眠的 Z：三个 Z 依次上浮淡出
        p.save()
        p.setPen(QPen(pal.PROP_Z, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        for i in range(3):
            t = (phase + i / 3.0) % 1.0
            x = hx + 22.0 + t * 16.0 + i * 3.0
            y = hy - 6.0 - t * 30.0
            size = 6.0 + i * 1.6
            col = QColor(pal.PROP_Z)
            col.setAlpha(int(200 * (1.0 - t)))
            p.setPen(QPen(col, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            z = QPainterPath()
            z.moveTo(x - size, y - size)
            z.lineTo(x + size, y - size)
            z.lineTo(x - size, y + size)
            z.lineTo(x + size, y + size)
            p.drawPath(z)
        p.restore()
        return

    if pose.prop == PROP_STAR:
        p.save()
        p.setPen(QPen(_faded(pal.PROP_STAR), 1.0))
        p.setBrush(QBrush(pal.PROP_STAR))
        for i in range(3):
            a = (phase + i / 3.0) * 2.0 * math.pi
            x = hx + math.cos(a) * 30.0
            y = hy - 12.0 + math.sin(a) * 9.0
            _draw_star(p, QPointF(x, y), 6.0)
        p.restore()
        return

    if pose.prop == PROP_HEART:
        p.save()
        p.setPen(Qt.NoPen)
        col = QColor(pal.PROP_HEART)
        col.setAlpha(210)
        p.setBrush(QBrush(col))
        x = hx + 26.0
        y = hy - 4.0 - phase * 18.0
        _draw_heart(p, QPointF(x, y), 7.0)
        p.restore()
        return

    if pose.prop == PROP_BALL:
        p.save()
        r = 9.0
        p.setPen(QPen(pal.OUTLINE, 1.2))
        p.setBrush(QBrush(pal.PROP_BALL))
        x, y = transform_point(pose, BODY_CX + 42.0, 126.0)
        p.drawEllipse(QPointF(x, y), r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 170)))
        p.drawEllipse(QPointF(x - 2.6, y - 3.0), 3.0, 2.4)
        p.restore()


def _faded(col: QColor, alpha: int = 150) -> QColor:
    c = QColor(col)
    c.setAlpha(alpha)
    return c


def _draw_star(p: QPainter, c: QPointF, r: float) -> None:
    path = QPainterPath()
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5.0
        rr = r if i % 2 == 0 else r * 0.45
        pt = QPointF(c.x() + math.cos(ang) * rr, c.y() + math.sin(ang) * rr)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)


def _draw_heart(p: QPainter, c: QPointF, r: float) -> None:
    path = QPainterPath()
    path.moveTo(c.x(), c.y() + r * 0.85)
    path.cubicTo(
        c.x() - r * 1.5, c.y() - r * 0.35, c.x() - r * 0.5, c.y() - r * 1.3, c.x(), c.y() - r * 0.45
    )
    path.cubicTo(
        c.x() + r * 0.5, c.y() - r * 1.3, c.x() + r * 1.5, c.y() - r * 0.35, c.x(), c.y() + r * 0.85
    )
    path.closeSubpath()
    p.drawPath(path)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def paint_penguin(p: QPainter, pose: Pose) -> None:
    p.setRenderHint(QPainter.Antialiasing, True)
    if pose.alpha <= 0.01:
        return
    prev_opacity = p.opacity()
    p.setOpacity(prev_opacity * pose.alpha)

    _draw_shadow(p, pose)

    p.save()
    _apply_body_transform(p, pose)
    _draw_feet(p, pose)
    _draw_silhouette(p, pose)      # 头与身体是同一条轮廓，没有独立的"头圆"
    _draw_belly(p, pose)

    # 五官跟着头部动画走；裁剪用未变换的头椭圆（两者在同一变换下必然对齐）
    p.save()
    _apply_head_transform(p, pose)
    hclip = head_path(pose)
    _draw_eyes(p, pose, hclip)
    _draw_beak(p, pose, hclip)
    _draw_blush(p, pose, hclip)
    p.restore()

    _draw_wings(p, pose)
    # 围脖放在最后画：这样它的两端会自然地"压在"翅膀上，
    # 否则围脖侧面的结与飘带会和翅膀轮廓撞在一起，那一侧显得很乱。
    _draw_scarf(p, pose)
    p.restore()

    _draw_prop(p, pose)
    p.setOpacity(prev_opacity)


def render_to_pixmap(pose: Pose, size: int = 240, dpr: float = 1.0):
    """离屏渲染，供托盘图标 / 预览图使用。"""
    from PySide6.QtGui import QPixmap

    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    scale = size / 240.0
    p.scale(scale, scale)
    paint_penguin(p, pose)
    p.end()
    return pm
