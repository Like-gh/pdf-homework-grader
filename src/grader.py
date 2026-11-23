"""Grading and UI utilities for PDFGrader.
Functions expect a PDFGrader instance (self) and manipulate marks, undo/redo, and drawing.

界面绘制与 PDF 导出共用 mark_style 里的一套尺寸定义：
    界面像素 = 参考单位 × 页面系数 × 渲染缩放
这样才能保证「屏幕上看到的」等于「导出的 PDF」。
"""
from PyQt6.QtGui import QPen, QColor, QFont, QFontMetrics
from PyQt6.QtWidgets import QMessageBox, QInputDialog
from PyQt6.QtCore import Qt, QPointF

import mark_style as ms

# 右键擦除的命中半径（参考单位）
ERASE_RADIUS = 22.0


def _factors(self):
    """返回当前页的 (页面系数, 渲染缩放, 单位像素数)。"""
    factor = getattr(self, "page_factor", None) or 1.0
    scale = getattr(self, "render_scale", None) or 1.0
    return factor, scale, ms.unit_px(factor, scale)


def handle_mouse_click(self, event):
    if not self.doc:
        return
    scene_pos = self.view.mapToScene(event.pos())
    x, y = scene_pos.x(), scene_pos.y()
    scale = getattr(self, "render_scale", None) or 1.0
    px, py = x / scale, y / scale          # 还原为 PDF 点

    if event.button() == Qt.MouseButton.RightButton:
        erase_nearest(self, px, py)
        return
    if event.button() != Qt.MouseButton.LeftButton:
        return

    self.push_undo()
    if self.mark_mode == "erase":
        # erase_nearest 内部已记录快照：先入栈再删除，撤销才能还原
        erase_nearest(self, px, py)
        return
    if self.mark_mode == "comment":
        if not self.current_comment_text:
            QMessageBox.warning(self, "警告", "请先选择或输入评语")
            self.undo_stack.pop()
            return
        self.comment_manager.add_comment(
            self.current_page,
            px, py,
            self.current_comment_text,
            color=self.current_comment_color,
            size=self.current_comment_size,
        )
        print(f"Comment added: {self.current_comment_text} at ({px:.0f}, {py:.0f})")
    elif self.mark_mode == "score":
        # 预先选定的分值直接落笔，只有选择"自定义"时才弹窗
        val = self.resolve_score_value()
        if val is None:
            self.undo_stack.pop()
            return
        self.recorded_marks.append((self.current_page, px, py, "score", val))
        self.update_score_display()
        print(f"Score mark: page={self.current_page}, x={px:.0f}, y={py:.0f}, val={val}")
    else:
        self.recorded_marks.append(
            (self.current_page, px, py, self.mark_mode, self.mark_mode))
        print(f"{self.mark_mode} mark: page={self.current_page}, x={px:.0f}, y={py:.0f}")
    self.refresh_display()


def erase_nearest(self, px, py):
    """删除离点击位置最近的评语或标记。

    Args:
        px, py: 点击位置（PDF 点）
    """
    factor = getattr(self, "page_factor", None) or 1.0
    radius = ERASE_RADIUS * factor

    best = None
    best_d = radius

    # 评语优先：文字更占地方，也更可能是误点的对象
    for i, c in enumerate(self.comment_manager.comments):
        if c['page'] != self.current_page:
            continue
        # 以「文本包围盒」做命中判定，整条评语都可被点中
        half_w = max(20.0, len(c['text']) * c['size'] * factor * 0.35)
        top = c['y'] - c['size'] * factor
        bottom = c['y'] + c['size'] * factor * 0.4
        dx = max(0.0, abs(px - c['x']) - half_w)
        dy = max(0.0, max(top - py, py - bottom))
        d = (dx * dx + dy * dy) ** 0.5
        if d < best_d:
            best_d, best = d, ("comment", i)

    for i, m in enumerate(self.recorded_marks):
        if m[0] != self.current_page:
            continue
        d = ((px - m[1]) ** 2 + (py - m[2]) ** 2) ** 0.5
        if d < best_d:
            best_d, best = d, ("mark", i)

    if best is None:
        return False
    self.push_undo()
    if best[0] == "comment":
        self.comment_manager.comments.pop(best[1])
    else:
        self.recorded_marks.pop(best[1])
    self.update_score_display()
    self.refresh_display()
    return True


def push_undo(self):
    """记录一次可撤销的快照（标记 + 评语），并清空重做栈。"""
    self.undo_stack.append((self.recorded_marks.copy(),
                            [dict(c) for c in self.comment_manager.comments]))
    self.redo_stack.clear()


def _apply_snapshot(self, snapshot):
    self.recorded_marks = snapshot[0]
    self.comment_manager.comments = [dict(c) for c in snapshot[1]]
    self.refresh_display()


def undo(self):
    if self.undo_stack:
        self.redo_stack.append((self.recorded_marks.copy(),
                                [dict(c) for c in self.comment_manager.comments]))
        _apply_snapshot(self, self.undo_stack.pop())
    # 栈为空时静默忽略：误按 Ctrl+Z 不应打断批改节奏


def redo(self):
    if self.redo_stack:
        self.undo_stack.append((self.recorded_marks.copy(),
                                [dict(c) for c in self.comment_manager.comments]))
        _apply_snapshot(self, self.redo_stack.pop())


def update_score_display(self):
    self.total_score = sum(m[4] for m in self.recorded_marks if m[3] == "score")
    full = getattr(self, "full_score", None)
    if full:
        try:
            full_val = float(full)
        except (TypeError, ValueError):
            full_val = None
        if full_val is not None:
            self.score_label.setText(f"总分: {self.total_score} / {int(full_val)}")
            over = self.total_score > full_val
            self.score_label.setStyleSheet(
                "font-size: 20px; font-weight: bold; color: #C0392B;" if over
                else "font-size: 20px; font-weight: bold; color: #E74C3C;")
            return
    self.score_label.setText(f"总分: {self.total_score}")
    self.score_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #E74C3C;")


def draw_mark_on_ui(self, x, y, m_type, val):
    """在界面上绘制一个标记。

    所有偏移量与字号都乘以 u（单位像素数），与导出端同一套参考单位，
    因此预览的大小比例与输出完全一致。
    """
    factor, scale, u = _factors(self)
    X, Y = x * scale, y * scale
    pen = QPen(QColor(*ms.UI_COLOR), max(1.0, ms.LINE_WIDTH * factor * scale))

    if m_type == "score":
        r = ms.SCORE_RADIUS * u
        self.scene.addEllipse(X - r, Y - r, r * 2, r * 2, pen)
        font = QFont("Segoe UI")
        font.setPixelSize(max(6, int(round(ms.SCORE_FONT * factor * scale))))
        font.setBold(True)
        text = self.scene.addText(f"+{val}", font)
        text.setDefaultTextColor(QColor(*ms.UI_COLOR))
        rect = text.boundingRect()
        text.setPos(X + ms.SCORE_TEXT_DX * u, Y - rect.height() / 2)
    elif m_type in ("check", "half", "cross"):
        # 与 PDF 导出共用同一份线段定义，保证预览 = 输出
        for p1, p2 in ms.mark_segments(m_type):
            self.scene.addLine(X + p1[0] * u, Y + p1[1] * u,
                               X + p2[0] * u, Y + p2[1] * u, pen)


def draw_comment_on_ui(self, comment):
    factor, scale, u = _factors(self)
    x = comment['x'] * scale
    y = comment['y'] * scale
    text = comment['text']
    color_name = comment['color']
    size = comment['size']
    color_map = {
        'red': QColor(231, 76, 60),
        'blue': QColor(52, 152, 219),
        'green': QColor(46, 204, 113),
        'black': QColor(0, 0, 0),
        'orange': QColor(230, 126, 34),
        'purple': QColor(155, 89, 182),
    }
    color = color_map.get(color_name, QColor(231, 76, 60))
    font = QFont("Arial")
    # 字号同样随页面系数与渲染缩放换算，保证预览等于导出效果
    font.setPixelSize(max(6, int(round(size * u))))
    font.setBold(True)
    text_item = self.scene.addText(text, font)
    text_item.setDefaultTextColor(color)
    # 导出端以左上角为基线基准，界面需减去 ascent 才能对齐到同一位置
    fm = QFontMetrics(font)
    text_item.setPos(x, y - fm.ascent())
