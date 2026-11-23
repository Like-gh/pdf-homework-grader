"""标记尺寸模型：让勾/叉/分数圈在任何页面尺寸下都保持一致的视觉比例。

问题背景
--------
原先所有笔画都用「绝对 PDF 点」绘制：勾的两段各约 12pt（合计跨度 20pt）、
分数圈半径 12pt、``+分`` 文字 14pt、Grade 印章 35pt，且线宽恒为 2。

当学生提交的页面尺寸差异很大时就会失真。实测同一批作业（第 2 次作业 12 份）：

    页面短边  595pt（A4 导出）～3213pt（高分辨率扫描），相差 5.4 倍

同样一笔 20pt 的勾，在 595pt 页面上占页面宽度 3.4%，在 3213pt 页面上只占
0.6%。导出后在 PDF 里看，前者的勾清晰醒目，后者的勾几乎缩成一个点 ——
这就是「勾/叉和图片大小不匹配」。

解决思路
--------
把笔画尺寸定义为「相对标准页的参考单位」，绘制时乘以页面系数：

    factor = 页面短边 / REFERENCE_SHORT_SIDE × 用户倍率

标准 A4（595×842）的 factor 恰为 1.0，因此历史批改记录的观感保持不变，
无需迁移旧数据。

界面预览同样要乘上页面系数与渲染缩放，否则「屏幕上看到的」与「导出的」
又会不一致（原实现里所有的偏移量都是硬编码像素）。

单位换算
--------
    存储坐标 x, y            PDF 用户空间点（point，1/72 英寸）
    笔画尺寸（PDF 输出）      base_pt × factor
    笔画尺寸（界面像素）      base_pt × factor × render_scale
    其中 render_scale 为该页渲染时的 像素/点 比例

由于页面尺寸差异是本问题的根源，模块把「页面系数」与「渲染缩放」分离，
界面一律通过 unit_px() 换算，避免出现两套互不相干的尺寸口径。
"""

# ── 尺寸基准 ─────────────────────────────────────────────────
# A4 短边。取其为 1.0 基准是为了兼容历史数据 ——
# 旧版在标准 A4 页面上绘制的标记，换算后尺寸完全不变。
REFERENCE_SHORT_SIDE = 595.0

# 渲染一幅作业的目标像素宽度。
# 采用「按页宽自适应」而非固定的 zoom 倍数，才能让不同尺寸页面的
# 观感一致；顺带避免超大页面生成巨幅位图
# （实测 3213pt 页面在 zoom=2 下会产生 157MB 位图）。
TARGET_RENDER_WIDTH = 1400.0
MAX_RENDER_WIDTH = 4200.0

# 页面系数与用户倍率的合理区间，防止极端尺寸下失真
FACTOR_MIN, FACTOR_MAX = 0.3, 8.0
MULTIPLIER_MIN, MULTIPLIER_MAX = 0.5, 3.0

# ── 各笔画的「参考单位」（在标准页上等同于原先的绝对点数）──────

LINE_WIDTH = 2.0            # 线宽
SCORE_RADIUS = 12.0         # 分数圈半径
SCORE_TEXT_DX = 15.0        # "+n" 文字相对圆心的水平偏移
SCORE_FONT = 14.0           # "+n" 字号
GRADE_X, GRADE_Y = 50.0, 50.0   # 首页 Grade 印章位置
GRADE_FONT = 35.0           # Grade 印章字号

# 勾：起点 -> 折点 -> 终点
CHECK_PTS = ((-10.0, 5.0), (-2.0, 12.0), (10.0, -5.0))
# 叉：主对角线两端点，另一条对角线由 mark_segments() 镜像生成
CROSS_PTS = ((-10.0, -10.0), (10.0, 10.0))
# 半勾：一个 √ + 一条「穿过勾体长臂」的短斜线（用户约定的 ⍻ 半对符号）。
#
# 几何上有三条硬约束，任一不满足都会让符号失去辨识度：
#   1. 斜线必须与勾的长臂相交 —— 不相交就只是「勾旁边多一撇」，不是穿过；
#   2. 斜线方向须与长臂（斜率 -1.42）明显不同，此处取近垂直的 +1.38。
#      旧值 ((12,-8),(2,8)) 斜率为 -1.6，与长臂近平行且仅相距约 4pt，
#      在 2pt 线宽下糊成一条，看起来只是「勾被加粗」；
#   3. 长度要短（13.6 单位，约为长臂 20.8 的 65%）。实测更长的斜线会让
#      整个符号看起来像「叉」，与 X 标记混淆。
# 本值精确穿过长臂中点 (4, 3.5)。
HALF_EXTRA = ((0.0, -2.0), (8.0, 9.0))

# 备选：U+237B NOT CHECK MARK 的标准印刷字形其实是「勾 + 水平短横」，
# 并非斜线（已用 Segoe UI Symbol 与 Cambria 两套字体的实际字形核对）。
# 若需严格贴合该字符的印刷字形，把 HALF_EXTRA 改为本常量即可，其余代码不动。
HALF_EXTRA_STANDARD_BAR = ((-1.0, 3.5), (9.0, 3.5))

# 界面绘制用色，与导出端的红色保持一致
UI_COLOR = (231, 76, 60)


def mark_segments(m_type):
    """返回标记的线段列表 [((x1,y1), (x2,y2)), ...]，单位为参考单位。

    界面预览与 PDF 导出共用这一份定义，避免两处各写一套坐标公式而漂移。
    历史缺陷：导出端叉的第二条对角线曾写成 (ax,-by)->(bx,-ay)，在
    CROSS_PTS 对称取值时与第一条完全重合，导致界面显示 X、导出 PDF
    却只剩一条斜线。统一由本函数生成即可根除该类不一致。
    """
    if m_type == "cross":
        ax, ay = CROSS_PTS[0]
        bx, by = CROSS_PTS[1]
        return [
            ((ax, ay), (bx, by)),       # 左上 -> 右下
            ((ax, -ay), (bx, -by)),     # 左下 -> 右上
        ]
    segments = [
        (CHECK_PTS[0], CHECK_PTS[1]),
        (CHECK_PTS[1], CHECK_PTS[2]),
    ]
    if m_type == "half":
        segments.append(HALF_EXTRA)
    return segments


def clamp_factor(value, lo=FACTOR_MIN, hi=FACTOR_MAX):
    return max(lo, min(hi, float(value)))


def page_factor(page_width, page_height, multiplier=1.0):
    """计算页面的尺寸系数。

    Args:
        page_width, page_height: 页面尺寸（PDF 点）
        multiplier: 用户手动倍率（"" 工具栏），用于个别超密/超疏的作业

    Returns:
        float: 尺寸系数。标准 A4 短边返回 1.0（多倍率也为 1.0 时）
    """
    try:
        w = float(page_width)
        h = float(page_height)
    except (TypeError, ValueError):
        return 1.0
    short = min(w, h)
    if short <= 0:
        return 1.0
    try:
        mult = float(multiplier) if multiplier else 1.0
    except (TypeError, ValueError):
        mult = 1.0
    mult = max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, mult))
    return clamp_factor(short / REFERENCE_SHORT_SIDE * mult)


def render_scale(page_width, relative_zoom=1.0):
    """计算渲染所需的「像素/点」比例。

    语义从「固定倍数」改为「按页宽归一化」：所有页面都渲染到相近的像素宽度，
    因此不同尺寸的作业在屏幕上观感一致，笔画大小也随之统一。

    Args:
        page_width: 页面宽度（PDF 点）
        relative_zoom: 用户的相对缩放偏好，1.0 表示标准宽度

    Returns:
        float: 像素/点 比例
    """
    try:
        w = float(page_width)
        z = float(relative_zoom) if relative_zoom else 1.0
    except (TypeError, ValueError):
        return 1.0
    if w <= 0:
        return 1.0
    scale = TARGET_RENDER_WIDTH / w * z
    # 限制像素上限：极大页面配合高缩放时不能失控
    if w * scale > MAX_RENDER_WIDTH:
        scale = MAX_RENDER_WIDTH / w
    return max(0.05, scale)


def unit_px(factor, scale):
    """返回「一个参考单位」在当前界面上对应的像素数。"""
    return factor * scale
