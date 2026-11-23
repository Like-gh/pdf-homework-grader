import sys
import os
import json
import zipfile
import pymupdf
from PyQt6.QtWidgets import (QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
                             QInputDialog, QVBoxLayout, QWidget, QLabel, QFileDialog,
                             QPushButton, QHBoxLayout, QMessageBox, QComboBox, QLineEdit)
from PyQt6.QtGui import QPixmap, QImage, QPen, QColor, QFont, QAction
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPainter
from PyQt6.QtGui import QKeySequence, QShortcut

import csv

from comment_manager import CommentManager
from config import load_config as cfg_load_config, save_config as cfg_save_config
from pdf_ops import (_load_pdf as pdf_load_pdf, render_page as pdf_render_page,
                     save_result as pdf_save_result, export_to_canvas as pdf_export_to_canvas,
                     is_submission_file)
from grader import (handle_mouse_click as grader_handle_mouse_click,
                    push_undo as grader_push_undo, undo as grader_undo, redo as grader_redo,
                    update_score_display as grader_update_score_display,
                    draw_mark_on_ui as grader_draw_mark_on_ui,
                    draw_comment_on_ui as grader_draw_comment_on_ui,
                    erase_nearest as grader_erase_nearest)
import batch_manager as bm
import mark_style as ms


class PDFGrader311(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("作业批改系统")
        self.total_score = 0
        self.recorded_marks = []  # 存储: (page_num, x, y, type, value)
        self.view_scale = 1.0     # 渲染相对缩放：1.0 = 页面渲染到标准像素宽度
        self.mark_multiplier = 1.0  # 笔画尺寸倍率
        self.page_factor = 1.0    # 当前页的尺寸系数（见 mark_style）
        self.render_scale = 1.0   # 当前页的 渲染像素/点 比例
        self.pdf_path = ""
        self.render_source_path = ""   # 实际渲染的文件（图片提交时为转换后的PDF）
        self.source_is_image = False
        self.doc = None
        self.current_page = 0     # 当前页码
        self.mark_mode = "check"  # check, cross, half, score, comment, erase
        self.page_height = 0      # 当前页面高度
        self.undo_stack = []      # 撤销栈（快照 = 标记列表 + 评语列表）
        self.redo_stack = []      # 重做栈

        # 评语管理
        self.comment_manager = CommentManager()
        self.current_comment_text = ""
        self.current_comment_color = "red"
        self.current_comment_size = 12

        # Canvas集成
        self.canvas_client = None
        self.assignment_manager = None
        self.canvas_panel = None

        # 配置和作业导航
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(script_dir, "config.json")
        self.default_submissions_path = ""
        self.last_student = ""
        self.submission_list = []  # 排序后的作业文件列表
        self.current_submission_index = -1
        # 作业批次：批改产物按批次隔离，避免多次作业之间互相污染
        self.current_batch = ""
        self.load_config()
        self.current_comment_size = self.comment_size
        # 如果已有默认路径，扫描作业列表
        if self.default_submissions_path:
            self.scan_submissions(self.default_submissions_path)
            # 启动时若配置中的批次仍与当前目录内容匹配则沿用，否则自动重新识别
            self.ensure_batch(self.default_submissions_path, silent=True,
                              prefer_existing=True)

        self.init_ui()
        # 界面创建后再刷新一次批次标签（ensure_batch 调用时标签尚未创建）
        self.update_batch_label()
        self.update_progress_label()

    def init_ui(self):
        # 直接将PDF批改界面作为主界面
        pdf_grader_widget = self.create_pdf_grader_tab()
        self.setCentralWidget(pdf_grader_widget)
        self.resize(1100, 850)


    def create_pdf_grader_tab(self) -> QWidget:
        """创建PDF批改标签页"""
        widget = QWidget()
        layout = QVBoxLayout()

        # ── 第一行：文件操作 + 学生导航 + 总分 + 撤销/重做 ──
        row1 = QHBoxLayout()

        btn_open = QPushButton("打开")
        btn_open.setMinimumHeight(36)
        btn_open.clicked.connect(self.open_file)
        row1.addWidget(btn_open)

        btn_save = QPushButton("完成")
        btn_save.setMinimumHeight(36)
        btn_save.setStyleSheet("background-color: #27AE60; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.save_result)
        row1.addWidget(btn_save)

        # 分隔线
        sep1 = QLabel("│")
        sep1.setStyleSheet("color: #ccc; font-size: 20px;")
        row1.addWidget(sep1)

        btn_set_path = QPushButton("设置路径")
        btn_set_path.setMinimumHeight(36)
        btn_set_path.clicked.connect(self.set_submissions_path)
        row1.addWidget(btn_set_path)

        btn_batch = QPushButton("批次")
        btn_batch.setMinimumHeight(36)
        btn_batch.setToolTip("切换/新建作业批次（同一目录下批改多次作业时使用）")
        btn_batch.clicked.connect(self.switch_batch)
        row1.addWidget(btn_batch)

        self.batch_label = QLabel("批次: -")
        self.batch_label.setStyleSheet("font-size: 12px; color: #7F8C8D;")
        self.batch_label.setToolTip("当前批改批次，批改产物按批次隔离存放")
        row1.addWidget(self.batch_label)

        btn_first_student = QPushButton("|◀")
        btn_first_student.setMinimumHeight(36)
        btn_first_student.setMaximumWidth(36)
        btn_first_student.setToolTip("第一位")
        btn_first_student.clicked.connect(self.open_first_student)
        row1.addWidget(btn_first_student)

        btn_prev_student = QPushButton("◀")
        btn_prev_student.setMinimumHeight(36)
        btn_prev_student.setMaximumWidth(36)
        btn_prev_student.setToolTip("上一位")
        btn_prev_student.clicked.connect(self.open_prev_student)
        row1.addWidget(btn_prev_student)

        btn_next_student = QPushButton("▶")
        btn_next_student.setMinimumHeight(36)
        btn_next_student.setMaximumWidth(36)
        btn_next_student.setToolTip("下一位")
        btn_next_student.clicked.connect(self.open_next_student)
        row1.addWidget(btn_next_student)

        btn_last_student = QPushButton("▶|")
        btn_last_student.setMinimumHeight(36)
        btn_last_student.setMaximumWidth(36)
        btn_last_student.setToolTip("最末位")
        btn_last_student.clicked.connect(self.open_last_student)
        row1.addWidget(btn_last_student)

        btn_ungraded = QPushButton("⏭")
        btn_ungraded.setMinimumHeight(36)
        btn_ungraded.setMaximumWidth(36)
        btn_ungraded.setToolTip("跳到下一位尚未批改的学生")
        btn_ungraded.clicked.connect(self.open_next_ungraded)
        row1.addWidget(btn_ungraded)

        self.progress_label = QLabel("进度: -/-")
        self.progress_label.setStyleSheet("font-size: 12px; color: #7F8C8D;")
        self.progress_label.setToolTip("当前批次已批改人数 / 提交总数")
        row1.addWidget(self.progress_label)

        sep_export = QLabel("│")
        sep_export.setStyleSheet("color: #ccc; font-size: 20px;")
        row1.addWidget(sep_export)

        btn_export = QPushButton("导出Canvas")
        btn_export.setMinimumHeight(36)
        btn_export.setStyleSheet("background-color: #2980B9; color: white; font-weight: bold;")
        btn_export.clicked.connect(self.export_to_canvas)
        row1.addWidget(btn_export)

        row1.addSpacing(16)

        btn_prev = QPushButton("<")
        btn_prev.setMaximumWidth(32)
        btn_prev.setMinimumHeight(36)
        btn_prev.setToolTip("上一页")
        btn_prev.clicked.connect(self.prev_page)
        row1.addWidget(btn_prev)

        btn_next = QPushButton(">")
        btn_next.setMaximumWidth(32)
        btn_next.setMinimumHeight(36)
        btn_next.setToolTip("下一页")
        btn_next.clicked.connect(self.next_page)
        row1.addWidget(btn_next)

        row1.addSpacing(12)

        btn_undo = QPushButton("↩")
        btn_undo.setMaximumWidth(32)
        btn_undo.setMinimumHeight(36)
        btn_undo.setToolTip("撤销")
        btn_undo.clicked.connect(self.undo)
        row1.addWidget(btn_undo)

        btn_redo = QPushButton("↪")
        btn_redo.setMaximumWidth(32)
        btn_redo.setMinimumHeight(36)
        btn_redo.setToolTip("重做")
        btn_redo.clicked.connect(self.redo)
        row1.addWidget(btn_redo)

        row1.addSpacing(12)

        btn_zoom_out = QPushButton("-")
        btn_zoom_out.setMaximumWidth(32)
        btn_zoom_out.setMinimumHeight(36)
        btn_zoom_out.setToolTip("缩小")
        btn_zoom_out.clicked.connect(self.zoom_out)
        row1.addWidget(btn_zoom_out)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setMaximumWidth(32)
        btn_zoom_in.setMinimumHeight(36)
        btn_zoom_in.setToolTip("放大")
        btn_zoom_in.clicked.connect(self.zoom_in)
        row1.addWidget(btn_zoom_in)

        row1.addStretch()

        self.score_label = QLabel("总分: 0")
        self.score_label.setStyleSheet("font-size: 20px; color: #E74C3C; font-weight: bold;")
        row1.addWidget(self.score_label)

        layout.addLayout(row1)

        # ── 第二行：标记模式 + 评语 + 翻页 + 缩放 ──
        row2 = QHBoxLayout()

        self.mode_label = QLabel("标记: 打勾")
        self.mode_label.setStyleSheet("font-size: 12px; font-weight: bold;")
        row2.addWidget(self.mode_label)

        btn_check = QPushButton("✓勾")
        btn_check.setMaximumWidth(48)
        btn_check.setMinimumHeight(30)
        btn_check.clicked.connect(lambda: self.set_mark_mode("check"))
        row2.addWidget(btn_check)

        btn_cross = QPushButton("✗差")
        btn_cross.setMaximumWidth(48)
        btn_cross.setMinimumHeight(30)
        btn_cross.clicked.connect(lambda: self.set_mark_mode("cross"))
        row2.addWidget(btn_cross)

        btn_half = QPushButton("◐半")
        btn_half.setMaximumWidth(48)
        btn_half.setMinimumHeight(30)
        btn_half.clicked.connect(lambda: self.set_mark_mode("half"))
        row2.addWidget(btn_half)

        btn_score = QPushButton("计分")
        btn_score.setMaximumWidth(48)
        btn_score.setMinimumHeight(30)
        btn_score.clicked.connect(lambda: self.set_mark_mode("score"))
        row2.addWidget(btn_score)

        # 计分预设：选定分值后点击即可落笔，免去每次弹输入框
        row2.addWidget(QLabel("分值:"))
        self.score_combo = QComboBox()
        self.score_combo.setMaximumWidth(90)
        self.score_combo.setMinimumHeight(30)
        self.score_combo.setToolTip("计分模式下点击批注时使用的分值；选「自定义…」可临时输入")
        self.refresh_score_combo()
        row2.addWidget(self.score_combo)

        sep_score = QLabel("│")
        sep_score.setStyleSheet("color: #ccc; font-size: 18px;")
        row2.addWidget(sep_score)

        btn_erase = QPushButton("⌫擦除")
        btn_erase.setMaximumWidth(64)
        btn_erase.setMinimumHeight(30)
        btn_erase.setToolTip("擦除模式：点击删除最近的标记或评语（也可直接右键）")
        btn_erase.clicked.connect(lambda: self.set_mark_mode("erase"))
        row2.addWidget(btn_erase)

        btn_clear_marks = QPushButton("清空本页")
        btn_clear_marks.setMaximumWidth(72)
        btn_clear_marks.setMinimumHeight(30)
        btn_clear_marks.setToolTip("清除当前页面的所有标记与评语（可撤销）")
        btn_clear_marks.clicked.connect(self.clear_current_page)
        row2.addWidget(btn_clear_marks)

        sep4 = QLabel("│")
        sep4.setStyleSheet("color: #ccc; font-size: 18px;")
        row2.addWidget(sep4)

        # ── 评语区（预设可自定义，持久化到 config.json）──
        btn_comment = QPushButton("💬")
        btn_comment.setMaximumWidth(40)
        btn_comment.setMinimumHeight(30)
        btn_comment.clicked.connect(lambda: self.set_mark_mode("comment"))
        row2.addWidget(btn_comment)

        self.preset_buttons = []
        self.preset_row = QHBoxLayout()
        self.rebuild_preset_buttons()
        row2.addLayout(self.preset_row)

        btn_manage_presets = QPushButton("＋")
        btn_manage_presets.setMaximumWidth(34)
        btn_manage_presets.setMinimumHeight(30)
        btn_manage_presets.setToolTip("管理预设评语（增删）")
        btn_manage_presets.clicked.connect(self.manage_presets)
        row2.addWidget(btn_manage_presets)

        btn_custom = QPushButton("自定义")
        btn_custom.setMaximumWidth(60)
        btn_custom.setMinimumHeight(30)
        btn_custom.clicked.connect(self.input_custom_comment)
        row2.addWidget(btn_custom)

        btn_delete_comment = QPushButton("删评")
        btn_delete_comment.setMaximumWidth(48)
        btn_delete_comment.setMinimumHeight(30)
        btn_delete_comment.setToolTip("删除最后一条评语")
        btn_delete_comment.clicked.connect(self.delete_last_comment)
        row2.addWidget(btn_delete_comment)

        self.comment_color_combo = QComboBox()
        self.comment_color_combo.addItems(["红色", "蓝色", "绿色", "黑色"])
        self.comment_color_combo.setMaximumWidth(70)
        self.comment_color_combo.setMinimumHeight(30)
        self.comment_color_combo.currentTextChanged.connect(self.on_comment_color_changed)
        row2.addWidget(self.comment_color_combo)

        self.comment_size_combo = QComboBox()
        self.comment_size_combo.addItems(["10", "12", "14", "16", "20", "26"])
        self.comment_size_combo.setMaximumWidth(60)
        self.comment_size_combo.setMinimumHeight(30)
        self.comment_size_combo.setToolTip("评语字号（随页面尺寸自动缩放）")
        self.comment_size_combo.setCurrentText(str(getattr(self, "comment_size", 12)))
        self.comment_size_combo.currentTextChanged.connect(self.on_comment_size_changed)
        row2.addWidget(self.comment_size_combo)

        row2.addStretch()

        sep5 = QLabel("│")
        sep5.setStyleSheet("color: #ccc; font-size: 18px;")
        row2.addWidget(sep5)

        btn_mark_smaller = QPushButton("A-")
        btn_mark_smaller.setMaximumWidth(34)
        btn_mark_smaller.setMinimumHeight(30)
        btn_mark_smaller.setToolTip("缩小笔画（适配特别密集的作业）")
        btn_mark_smaller.clicked.connect(lambda: self.set_mark_size(-0.2))
        row2.addWidget(btn_mark_smaller)

        btn_mark_bigger = QPushButton("A+")
        btn_mark_bigger.setMaximumWidth(34)
        btn_mark_bigger.setMinimumHeight(30)
        btn_mark_bigger.setToolTip("放大笔画（适配特别稀疏的作业）")
        btn_mark_bigger.clicked.connect(lambda: self.set_mark_size(0.2))
        row2.addWidget(btn_mark_bigger)

        self.mark_size_label = QLabel(f"笔画: {int(self.mark_multiplier * 100)}%")
        self.mark_size_label.setStyleSheet("font-size: 12px; color: #7F8C8D;")
        self.mark_size_label.setToolTip("笔画相对标准页的比例，自动随页面尺寸调整")
        row2.addWidget(self.mark_size_label)

        btn_full_score = QPushButton("满分")
        btn_full_score.setMaximumWidth(44)
        btn_full_score.setMinimumHeight(30)
        btn_full_score.setToolTip("设置本次作业满分，超出时总分标红")
        btn_full_score.clicked.connect(self.set_full_score)
        row2.addWidget(btn_full_score)

        layout.addLayout(row2)

        # 渲染区域
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.mousePressEvent = self.handle_mouse_click
        layout.addWidget(self.view)

        widget.setLayout(layout)

        # 设置快捷键
        self.setup_shortcuts()

        return widget

    def load_config(self):
        return cfg_load_config(self)

    def save_config(self):
        return cfg_save_config(self)
    def scan_submissions(self, folder: str):
        """扫描folder中的作业文件（PDF 与常见图片），按文件名排序。

        图片一律纳入，避免"交了图片却被打 0 分/被静默忽略"的情况。
        """
        if not folder or not os.path.isdir(folder):
            self.submission_list = []
            self.current_submission_index = -1
            return
        files = sorted(
            [os.path.join(folder, f) for f in os.listdir(folder)
             if os.path.isfile(os.path.join(folder, f)) and is_submission_file(f)]
        )
        self.submission_list = files
        # 恢复到last_student位置
        self.current_submission_index = 0 if files else -1
        if self.last_student:
            for i, p in enumerate(files):
                if os.path.basename(p) == self.last_student:
                    self.current_submission_index = i
                    break

    # ── 批改进度 ─────────────────────────────────────────────
    def _batch_dir(self):
        if not self.default_submissions_path:
            return None
        return bm.batch_dir(os.path.join(self.default_submissions_path, "done"),
                            self.current_batch or bm.DEFAULT_BATCH)

    def is_submission_graded(self, index):
        """判断第 index 位学生在当前批次是否已有批改产物。"""
        bdir = self._batch_dir()
        if bdir is None or index is None:
            return False
        if index < 0 or index >= len(self.submission_list):
            return False
        base = os.path.splitext(os.path.basename(self.submission_list[index]))[0]
        return os.path.exists(os.path.join(bdir, base + ".marks.json"))

    def graded_count(self):
        return sum(1 for i in range(len(self.submission_list))
                   if self.is_submission_graded(i))

    def update_progress_label(self):
        """刷新工具栏上的批改进度。"""
        label = getattr(self, "progress_label", None)
        if label is None:
            return
        total = len(self.submission_list)
        if total == 0:
            label.setText("进度: -/-")
            return
        done = self.graded_count()
        label.setText(f"进度: {done}/{total}")
        label.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #27AE60;" if done >= total
            else "font-size: 12px; color: #7F8C8D;")

    def open_next_ungraded(self):
        """跳到下一位尚未批改的学生。"""
        if not self.submission_list:
            QMessageBox.information(self, "提示", "请先设置作业目录")
            return
        if self.pdf_path:
            self.save_result(show_dialog=False)
        n = len(self.submission_list)
        for step in range(1, n + 1):
            idx = (self.current_submission_index + step) % n
            if not self.is_submission_graded(idx):
                self.open_submission_at_index(idx)
                return
        QMessageBox.information(self, "提示", "本批次所有学生均已批改")

    def ensure_batch(self, folder: str, silent=False, prefer_existing=True):
        """为指定作业目录确定当前批次。

        判定顺序：
        1. 若目录内容指纹与某个已有批次一致 → 续用该批次
           （手动指定的批次在此优先）
        2. 否则 → 开新批次（batch_日期_摘要），因为目录内容已变，
           说明是另一次作业的提交，绝不能复用旧批次的记录
        """
        if not folder or not os.path.isdir(folder):
            return self.current_batch
        done_dir = os.path.join(folder, "done")
        batches = bm.load_batches(done_dir)

        # 找出内容指纹相同的批次（续批）
        sig = bm.dir_content_signature(folder)
        match = None
        for bname, info in batches.items():
            if info.get("signature") == sig and sig:
                match = bname
                break

        # 用户手动指定的批次优先（前提：属于当前目录，且内容指纹一致）
        if prefer_existing and self.current_batch in batches:
            info = batches.get(self.current_batch, {})
            if (os.path.normcase(info.get("submissions_dir", "")) ==
                    os.path.normcase(os.path.abspath(folder))
                    and info.get("signature") == sig):
                match = self.current_batch

        name = match or bm.suggest_batch_name(folder, batches)
        self.current_batch = name
        bm.register_batch(done_dir, name, folder)
        self.update_batch_label()
        if not silent:
            self.save_config()
        return name

    def switch_batch(self):
        """手动切换/新建批次，用于同一目录下批改多次作业的场景。"""
        folder = self.default_submissions_path
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, "提示", "请先设置作业目录")
            return
        done_dir = os.path.join(folder, "done")
        batches = bm.load_batches(done_dir)
        items = list(batches.keys())
        if items:
            items.append("＋ 新建批次…")
            labels = []
            for it in items:
                info = batches.get(it, {})
                n = len([f for f in os.listdir(bm.batch_dir(done_dir, it))
                         if f.lower().endswith('.pdf')]) if os.path.isdir(bm.batch_dir(done_dir, it)) else 0
                labels.append(f"{it}   [已批改 {n} 份]  {info.get('updated_at','')}")
            chosen, ok = QInputDialog.getItem(
                self, "切换批次", "请选择作业批次：", labels, 0, False)
            if not ok:
                return
            if chosen == items[-1]:
                name, ok = QInputDialog.getText(self, "新建批次", "请输入批次名称（如 hw2）:")
                if not ok or not name.strip():
                    return
                name = bm.sanitize_batch_name(name)
            else:
                name = chosen.split()[0]
        else:
            name, ok = QInputDialog.getText(self, "新建批次", "请输入批次名称（如 hw1）:")
            if not ok or not name.strip():
                return
            name = bm.sanitize_batch_name(name)

        self.current_batch = name
        bm.register_batch(done_dir, name, folder)
        self.update_batch_label()
        self.save_config()
        QMessageBox.information(self, "已切换", f"当前批次：{name}")

    def update_batch_label(self):
        """刷新界面上的批次标签。"""
        if getattr(self, "batch_label", None) is not None:
            self.batch_label.setText(f"批次: {self.current_batch}" if self.current_batch else "批次: -")

    def set_submissions_path(self):
        """让用户选择作业目录并保存"""
        folder = QFileDialog.getExistingDirectory(
            self, "选择作业目录", self.default_submissions_path or ""
        )
        if folder:
            self.default_submissions_path = folder
            self.scan_submissions(folder)
            # 切换目录时重新确定批次：内容相同则续批，内容变了则开新批次
            self.ensure_batch(folder)
            self.save_config()

    def open_submission_at_index(self, idx: int):
        """打开submission_list中第idx个PDF"""
        if not self.submission_list or idx < 0 or idx >= len(self.submission_list):
            return
        self.current_submission_index = idx
        path = self.submission_list[idx]
        self._load_pdf(path)

    def open_first_student(self):
        """第一位学生"""
        if not self.submission_list:
            QMessageBox.information(self, "提示", "请先设置作业目录")
            return
        if self.pdf_path:
            self.save_result(show_dialog=False)
        self.open_submission_at_index(0)

    def open_last_student(self):
        """最末位学生"""
        if not self.submission_list:
            QMessageBox.information(self, "提示", "请先设置作业目录")
            return
        if self.pdf_path:
            self.save_result(show_dialog=False)
        self.open_submission_at_index(len(self.submission_list) - 1)

    def open_prev_student(self):
        """上一位学生"""
        if not self.submission_list:
            QMessageBox.information(self, "提示", "请先设置作业目录")
            return
        # 静默保存当前批阅
        if self.pdf_path:
            self.save_result(show_dialog=False)
        idx = self.current_submission_index - 1
        if idx < 0:
            QMessageBox.information(self, "提示", "已是第一位")
            return
        self.open_submission_at_index(idx)

    def open_next_student(self):
        """下一位学生"""
        if not self.submission_list:
            QMessageBox.information(self, "提示", "请先设置作业目录")
            return
        # 静默保存当前批阅
        if self.pdf_path:
            self.save_result(show_dialog=False)
        idx = self.current_submission_index + 1
        if idx >= len(self.submission_list):
            QMessageBox.information(self, "提示", "已是最后一位")
            return
        self.open_submission_at_index(idx)

    def _load_pdf(self, path: str):
        return pdf_load_pdf(self, path)

    def erase_at(self, x_pt, y_pt):
        return grader_erase_nearest(self, x_pt, y_pt)

    def open_file(self):
        start_dir = self.default_submissions_path or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择作业（PDF 或图片）", start_dir,
            "作业文件 (*.pdf *.jpg *.jpeg *.png *.bmp *.webp);;PDF Files (*.pdf);;所有文件 (*.*)")
        if not path:
            return
        folder = os.path.dirname(path)
        # 打开的目录与当前作业目录不同则整体切换，避免批次仍指向旧目录
        if folder != self.default_submissions_path:
            self.default_submissions_path = folder
            self.scan_submissions(folder)
            self.ensure_batch(folder, silent=True)
            self.save_config()
        fname = os.path.basename(path)
        for i, p in enumerate(self.submission_list):
            if os.path.basename(p) == fname:
                self.current_submission_index = i
                break
        else:
            self.current_submission_index = -1
        self._load_pdf(path)

    def render_page(self, page_num):
        return pdf_render_page(self, page_num)

    def handle_mouse_click(self, event):
        return grader_handle_mouse_click(self, event)

    def draw_mark_on_ui(self, x, y, m_type, val):
        return grader_draw_mark_on_ui(self, x, y, m_type, val)

    def update_score_display(self):
        return grader_update_score_display(self)

    def update_page_label(self):
        if self.submission_list and self.current_submission_index >= 0:
            nav_info = (f"学生: {self.current_submission_index + 1}/{len(self.submission_list)}"
                        + ("  ✓已批改" if self.is_submission_graded(self.current_submission_index) else ""))
        else:
            nav_info = "学生: -/-"
        if self.doc:
            page_info = f"Page: {self.current_page + 1}/{len(self.doc)}"
        else:
            page_info = "Page: 0/0"
        scale = getattr(self, "render_scale", 1.0)
        zoom_info = f"缩放: {int(round(getattr(self, 'view_scale', 1.0) * 100))}%  ({scale:.2f}px/pt)"
        filename = os.path.basename(self.pdf_path) if self.pdf_path else "(未打开)"
        self.statusBar().showMessage(f"{nav_info}    {page_info}    {zoom_info}    {filename}")

    def prev_page(self):
        if self.doc and self.current_page > 0:
            self.current_page -= 1
            self.refresh_display()

    def next_page(self):
        if self.doc and self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.refresh_display()

    def zoom_in(self):
        self.view_scale = min(self.view_scale + 0.15, 3.0)
        self.save_config()
        if self.doc:
            self.refresh_display()

    def zoom_out(self):
        self.view_scale = max(self.view_scale - 0.15, 0.4)
        self.save_config()
        if self.doc:
            self.refresh_display()

    def set_mark_size(self, delta):
        """调整笔画尺寸倍率（0.5～3.0），并立即重绘。"""
        val = getattr(self, "mark_multiplier", 1.0) + delta
        self.mark_multiplier = max(0.5, min(3.0, round(val, 2)))
        self.save_config()
        self.mark_size_label.setText(f"笔画: {int(self.mark_multiplier * 100)}%")
        if self.doc:
            self.refresh_display()

    def set_full_score(self):
        """设置作业满分，超出时总分标红提醒。"""
        cur = str(getattr(self, "full_score", None) or "")
        text, ok = QInputDialog.getText(
            self, "满分设置", "本次作业满分（留空表示不限制）：", text=cur)
        if not ok:
            return
        text = text.strip()
        if not text:
            self.full_score = None
        else:
            try:
                self.full_score = float(text)
            except ValueError:
                QMessageBox.warning(self, "警告", "请输入数字")
                return
        self.save_config()
        self.update_score_display()

    def set_mark_mode(self, mode):
        self.mark_mode = mode
        mode_names = {
            "check": "打勾",
            "cross": "打叉",
            "half":  "半勾",
            "score": "计分",
            "comment": "评语",
            "erase": "擦除",
        }
        self.mode_label.setText(f"标记: {mode_names.get(mode, mode)}")

    # ── 计分预设 ─────────────────────────────────────────────
    def refresh_score_combo(self):
        """重建分值下拉列表。"""
        combo = getattr(self, "score_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        for v in getattr(self, "score_presets", []):
            combo.addItem(str(v), int(v))
        combo.addItem("自定义…", None)
        combo.blockSignals(False)

    def resolve_score_value(self):
        """取得计分模式当前选用的分值。

        返回 None 表示用户取消了输入。
        """
        combo = getattr(self, "score_combo", None)
        if combo is None:
            return 1
        val = combo.currentData()
        if val is not None:
            return int(val)
        val, ok = QInputDialog.getInt(self, "计分", "输入该题得分:",
                                      value=1, min=-50, max=100)
        if not ok:
            return None
        # 记忆这次输入的自定义值，避免同类题目反复输入
        presets = list(getattr(self, "score_presets", []))
        if val not in presets:
            presets.append(int(val))
            presets.sort()
            self.score_presets = presets
            self.save_config()
            self.refresh_score_combo()
            idx = self.score_combo.findData(int(val))
            if idx >= 0:
                self.score_combo.setCurrentIndex(idx)
        return int(val)

    # ── 预设评语 ─────────────────────────────────────────────
    def rebuild_preset_buttons(self):
        """依据 config 中的 comment_presets 重建预设按钮。"""
        layout = getattr(self, "preset_row", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for text in getattr(self, "comment_presets", []):
            short = text if len(text) <= 6 else text[:6] + "…"
            btn = QPushButton(short)
            btn.setMaximumWidth(88)
            btn.setMinimumHeight(30)
            btn.setToolTip(text)
            btn.clicked.connect(lambda checked, t=text: self.set_preset_comment(t))
            layout.addWidget(btn)

    def manage_presets(self):
        """增删预设评语，结果写回配置文件。"""
        current = getattr(self, "comment_presets", [])
        items = list(current) + ["＋ 新增一条…"]
        chosen, ok = QInputDialog.getItem(
            self, "预设评语", "选择要删除的评语，或选择最后一项新增：", items, len(items) - 1, False)
        if not ok:
            return
        if chosen == items[-1]:
            text, ok = QInputDialog.getText(self, "新增预设评语", "评语内容：")
            if not ok or not text.strip():
                return
            current = list(current) + [text.strip()]
        else:
            current = [t for t in current if t != chosen]
        self.comment_presets = current
        self.save_config()
        self.rebuild_preset_buttons()

    def clear_current_page(self):
        """清空当前页面的所有标记与评语（可撤销）。"""
        if not self.doc:
            return
        n_mark = sum(1 for m in self.recorded_marks if m[0] == self.current_page)
        n_com = len(self.comment_manager.get_page_comments(self.current_page))
        if n_mark + n_com == 0:
            QMessageBox.information(self, "提示", "本页没有标记或评语")
            return
        ans = QMessageBox.question(
            self, "确认", f"将清除本页 {n_mark} 个标记与 {n_com} 条评语，是否继续？\n（可用 Ctrl+Z 撤销）")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.push_undo()
        self.recorded_marks = [m for m in self.recorded_marks if m[0] != self.current_page]
        self.comment_manager.clear_page_comments(self.current_page)
        self.update_score_display()
        self.refresh_display()

    def on_comment_size_changed(self, text):
        try:
            self.comment_size = int(text)
        except (TypeError, ValueError):
            return
        self.current_comment_size = self.comment_size
        self.save_config()

    def set_preset_comment(self, text):
        """设置预设评语"""
        self.current_comment_text = text
        self.set_mark_mode("comment")

    def input_custom_comment(self):
        """输入自定义评语"""
        text, ok = QInputDialog.getText(
            self,
            "输入评语",
            "请输入评语内容:",
            text=self.current_comment_text
        )
        if ok and text:
            self.current_comment_text = text
            self.set_mark_mode("comment")

    def on_comment_color_changed(self, color_name):
        """评语颜色改变"""
        color_map = {
            "红色": "red",
            "蓝色": "blue",
            "绿色": "green",
            "黑色": "black"
        }
        self.current_comment_color = color_map.get(color_name, "red")

    def delete_last_comment(self):
        """删除最后一条评语"""
        if self.comment_manager.remove_last_comment():
            self.refresh_display()
        else:
            QMessageBox.warning(self, "警告", "没有评语可删除")
    def setup_shortcuts(self):
        """设置快捷键

        注意：单字母快捷键（t/c/s/b/e）只在非输入控件获得焦点时才有意义，
        因此在 setEnabled 之外额外做一次焦点判断，避免在分值下拉、
        评语输入框里打字时误切换模式。
        """
        from PyQt6.QtWidgets import QApplication

        def safe(action):
            def _run():
                w = QApplication.focusWidget()
                from PyQt6.QtWidgets import QLineEdit, QComboBox
                if isinstance(w, (QLineEdit, QComboBox)):
                    return
                action()
            return _run

        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence("Ctrl+R"), self, self.redo)
        QShortcut(QKeySequence("Ctrl++"), self, self.zoom_in)
        QShortcut(QKeySequence("Ctrl+="), self, self.zoom_in)  # 免按Shift
        QShortcut(QKeySequence("Ctrl+-"), self, self.zoom_out)
        QShortcut(QKeySequence("t"), self, safe(lambda: self.set_mark_mode("check")))
        QShortcut(QKeySequence("c"), self, safe(lambda: self.set_mark_mode("cross")))
        QShortcut(QKeySequence("s"), self, safe(lambda: self.set_mark_mode("score")))
        QShortcut(QKeySequence("b"), self, safe(lambda: self.set_mark_mode("half")))
        QShortcut(QKeySequence("e"), self, safe(lambda: self.set_mark_mode("erase")))
        # 翻页
        QShortcut(QKeySequence("Ctrl+."), self, self.next_page)
        QShortcut(QKeySequence("Ctrl+,"), self, self.prev_page)
        # 学生导航
        QShortcut(QKeySequence("Ctrl+N"), self, self.open_next_student)
        QShortcut(QKeySequence("Ctrl+P"), self, self.open_prev_student)
        QShortcut(QKeySequence("Ctrl+F"), self, self.open_first_student)
        QShortcut(QKeySequence("Ctrl+L"), self, self.open_last_student)
        QShortcut(QKeySequence("Ctrl+G"), self, self.open_next_ungraded)
        QShortcut(QKeySequence("Ctrl+S"), self, lambda: self.save_result())

    def push_undo(self):
        return grader_push_undo(self)

    def undo(self):
        return grader_undo(self)

    def redo(self):
        return grader_redo(self)

    def refresh_display(self):
        """刷新显示

        先渲染页面（render_page 会按页宽算出渲染比例与尺寸系数），
        再按同一套单位重绘标记与评语，保证预览等于导出结果。
        """
        if not self.doc:
            return
        self.render_page(self.current_page)

        # 重新绘制当前页面的所有标记
        for p_num, x, y, m_type, val in self.recorded_marks:
            if p_num == self.current_page:
                self.draw_mark_on_ui(x, y, m_type, val)

        # 重新绘制当前页面的所有评语
        for comment in self.comment_manager.get_page_comments(self.current_page):
            self.draw_comment_on_ui(comment)

        self.update_score_display()
        self.update_page_label()
        self.update_progress_label()

    def draw_comment_on_ui(self, comment):
        return grader_draw_comment_on_ui(self, comment)

    def save_result(self, show_dialog=True):
        result = pdf_save_result(self, show_dialog)
        self.update_progress_label()
        return result

    def update_scores_csv(self, done_dir, pdf_filename):
        """更新当前批次的 scores.csv，记录学号和总分（覆盖语义，不累加）。

        done_dir 为当前批次的产物目录，不同批次各有一份 scores.csv，
        因此多次作业之间不会互相影响。
        """

        # 从文件名中提取学号（使用"_"分割后的第一部分）
        student_id = pdf_filename.split("_")[0]

        scores_path = os.path.join(done_dir, "scores.csv")

        # 读取现有的scores.csv
        scores_dict = {}
        if os.path.exists(scores_path):
            try:
                with open(scores_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) >= 2:
                            scores_dict[row[0]] = row[1]
            except Exception as e:
                print(f"读取scores.csv失败: {e}")

        # 更新或添加该学号的成绩
        scores_dict[student_id] = str(self.total_score)

        # 保存更新后的scores.csv
        try:
            with open(scores_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                for sid, score in sorted(scores_dict.items()):
                    writer.writerow([sid, score])
        except Exception as e:
            print(f"保存scores.csv失败: {e}")

    def export_to_canvas(self):
        return pdf_export_to_canvas(self)

    def closeEvent(self, event):
        """程序退出时保存配置"""
        self.save_config()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # 设置应用风格
    app.setStyle("Fusion") 
    window = PDFGrader311()
    window.show()
    sys.exit(app.exec())