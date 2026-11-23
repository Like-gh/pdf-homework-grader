"""PDF operations for PDFGrader.
Contains functions that handle loading, rendering, saving and exporting PDFs.
All functions expect a PDFGrader instance (self) with appropriate attributes.
"""
import os, json, zipfile, csv
from PyQt6.QtWidgets import QMessageBox, QFileDialog, QInputDialog
from PyQt6.QtGui import QImage, QPixmap
import pymupdf

import canvas_export as ce
import batch_manager as bm
import mark_style as ms

# 可作为作业提交的文件类型。图片会被转成标准尺寸的 PDF 页再批改
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")
SUBMISSION_SUFFIXES = (".pdf",) + IMAGE_SUFFIXES

# 图片转 PDF 后的缓存目录（位于作业目录下）
CONVERT_DIR_NAME = "_converted"


def write_zip_for_moodle(zip_path, files):
    """打包 PDF 供 Moodle「批量上传提交项」使用。

    关键：Moodle 用 PHP ZipArchive 解析文件名来匹配学生。Windows 上
    zipfile.ZipFile.write() 默认写入 create_system=0(DOS) 且对中文文件名
    的 UTF-8 标志处理不一致，PHP 会按 CP437 解码导致文件名乱码，匹配失败，
    报「重新上传提交项时出现问题，请检查压缩文件」。

    因此这里统一用 ZipInfo 显式设置：
      - flag_bits 强制打 UTF-8 标志(0x800)，保证中文名按 UTF-8 解码
      - create_system=3(Unix)、create_version=63，与 Moodle 自导出的包一致
      - external_attr 写入 Unix 普通文件权限，避免 DOS 属性歧义

    files: [(源文件绝对路径, zip 内文件名), ...]
    """
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for src_path, arc_name in files:
            zi = zipfile.ZipInfo(arc_name)
            zi.compress_type = zipfile.ZIP_DEFLATED
            # 核心修复：所有条目都声明文件名为 UTF-8
            zi.flag_bits |= 0x800
            # 与 Moodle 自导出包一致的平台/版本元数据
            zi.create_system = 3
            zi.create_version = 63
            zi.extract_version = 20
            zi.external_attr = (0o100644 << 16)  # Unix 普通文件 rw-r--r--
            with open(src_path, 'rb') as fh:
                zf.writestr(zi, fh.read())


def is_submission_file(fname):
    return fname.lower().endswith(SUBMISSION_SUFFIXES)


def normalize_rotation(doc):
    """把页面旋转固化到内容中，消除 /Rotate 标记。

    背景：部分学生提交的 PDF 带有 /Rotate 90（扫描件常见）。此时
    PyMuPDF 的 page.rect 是"显示坐标"（已应用旋转），而 draw_line /
    insert_text 等绘图 API 使用的是"用户坐标"（未旋转的 mediabox），
    两者相差一个 90° 旋转，导致标记画到错误位置。

    remove_rotation() 会把旋转烘焙进页面内容并清除 /Rotate，之后
    显示坐标与用户坐标完全一致。实测渲染结果与原文件像素级一致。

    Returns:
        (处理页数, 是否发生旋转)
    """
    rotated = 0
    for page in doc:
        if page.rotation:
            try:
                page.remove_rotation()
                rotated += 1
            except Exception as e:
                print(f"第{page.number}页旋转归一化失败: {e}")
    return rotated, rotated > 0


def image_to_pdf(image_path, out_pdf_path, reference_short_side=None):
    """把一张图片转成单页 PDF，页面短边归一化到标准值。

    学生直接用手机拍照提交时，图片短边从几百到几千像素不等；若按原像素
    建页，页面尺寸会相差数倍，笔画大小又回到本题要解决的问题上。
    这里统一把页面短边设为 ms.REFERENCE_SHORT_SIDE（A4 短边），
    于是任意尺寸的图片转换后尺寸系数恒为 1.0，批改标记整齐划一。

    图片本身的像素数据完整嵌入 PDF，放大查看仍然清晰。

    Returns:
        (是否成功, 页面宽度, 页面高度)
    """
    ref = float(reference_short_side or ms.REFERENCE_SHORT_SIDE)
    try:
        pix = pymupdf.Pixmap(image_path)
        # 带 alpha 通道或 CMYK 的图要先转成 RGB，否则 insert_image 会失败
        if pix.alpha:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        elif pix.n - pix.alpha > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        short = min(pix.width, pix.height) or 1
        k = ref / short
        width, height = pix.width * k, pix.height * k
        doc = pymupdf.open()
        page = doc.new_page(width=width, height=height)
        page.insert_image(page.rect, pixmap=pix)
        os.makedirs(os.path.dirname(out_pdf_path) or ".", exist_ok=True)
        if os.path.exists(out_pdf_path):
            os.remove(out_pdf_path)
        doc.save(out_pdf_path, deflate=True)
        doc.close()
        return True, width, height
    except Exception as e:
        print(f"图片转PDF失败 {image_path}: {e}")
        return False, 0, 0


def ensure_renderable(source_path, cache_dir):
    """取得可直接用 PyMuPDF 打开的路径。

    PDF 直接返回原路径；图片则转换为缓存 PDF 后返回缓存路径。
    缓存按「大小 + 修改时间」判断是否需要重建。

    Returns:
        (可渲染路径, 是否为图片转换而来)
    """
    if source_path.lower().endswith(".pdf"):
        return source_path, False
    os.makedirs(cache_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(source_path))[0]
    # 同名不同扩展名的图片会冲突，附加一个短哈希区分
    try:
        st = os.stat(source_path)
        tag = f"{st.st_size:x}"
    except OSError:
        tag = "0"
    out_pdf = os.path.join(cache_dir, f"{base}_{tag}.pdf")
    if not os.path.exists(out_pdf):
        ok, _, _ = image_to_pdf(source_path, out_pdf)
        if not ok:
            raise ValueError(f"无法把图片转换为可批改的PDF：{os.path.basename(source_path)}")
    return out_pdf, True


def _current_batch_dir(self, pdf_path):
    """返回当前批次的产物目录，并确保存在。"""
    done_dir = os.path.join(os.path.dirname(pdf_path), "done")
    batch = getattr(self, "current_batch", None) or bm.DEFAULT_BATCH
    out_dir = bm.batch_dir(done_dir, batch)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _load_pdf(self, path: str):
    """Load a PDF (or image converted on the fly), restore marks, render."""
    try:
        self.pdf_path = path
        if self.doc:
            self.doc.close()
            self.doc = None

        cache_dir = os.path.join(os.path.dirname(path), CONVERT_DIR_NAME)
        try:
            render_path, from_image = ensure_renderable(path, cache_dir)
        except ValueError as e:
            QMessageBox.critical(self, "错误", str(e))
            return
        self.render_source_path = render_path
        self.source_is_image = from_image

        self.doc = pymupdf.open(render_path)
        if len(self.doc) == 0:
            raise ValueError("PDF文件为空")
        # 归一化页面旋转，使显示坐标与绘图坐标一致（见 normalize_rotation 说明）
        normalize_rotation(self.doc)
        self.total_score = 0
        self.recorded_marks = []
        self.comment_manager.clear_all_comments()
        self.current_page = 0
        # 每位学生的撤销/重做栈独立，避免把上一位学生的标记撤到下一位身上
        self.undo_stack = []
        self.redo_stack = []
        # 页面尺寸系数（用于笔画缩放），见 mark_style 模块说明
        page = self.doc[0]
        self.page_factor = ms.page_factor(
            page.rect.width, page.rect.height, getattr(self, "mark_multiplier", 1.0))
        self.render_scale = ms.render_scale(
            page.rect.width, getattr(self, "view_scale", 1.0))

        # 恢复批改记录：仅限当前批次，且需校验源文件指纹
        pdf_dir = os.path.dirname(path)
        pdf_filename = os.path.basename(path)
        out_dir = _current_batch_dir(self, path)
        marks_json_path = os.path.join(out_dir, os.path.splitext(pdf_filename)[0] + ".marks.json")
        if os.path.exists(marks_json_path):
            try:
                with open(marks_json_path, "r", encoding="utf-8") as f:
                    marks_data = json.load(f)
                # 源文件已被替换（换成了新作业）则不恢复旧记录
                saved_fp = marks_data.get("source_fingerprint")
                cur_fp = bm.file_fingerprint(path)
                if saved_fp is not None and not bm.fingerprint_matches(saved_fp, cur_fp):
                    print(f"{pdf_filename}: 源文件已变更，忽略旧批改记录")
                else:
                    self.recorded_marks = [tuple(m) for m in marks_data.get('recorded_marks', [])]
                    for c in marks_data.get('comments', []):
                        self.comment_manager.comments.append(c)
                    # 同步ID计数器，避免恢复后新增评语ID与已有冲突
                    ids = [int(c.get('id', 0)) for c in self.comment_manager.comments
                           if str(c.get('id', 0)).lstrip('-').isdigit()]
                    self.comment_manager._next_id = (max(ids) + 1) if ids else 0
                    self.update_score_display()
            except Exception as e:
                print(f"恢复批阅记录失败: {e}")

        self.refresh_display()
        self.setWindowTitle(f"作业批改系统 - {os.path.dirname(path)}")
    except Exception as e:
        QMessageBox.critical(self, "错误", f"打开PDF失败: {str(e)}")
        self.doc = None


def render_page(self, page_num: int):
    """Render the specified page to the graphics view, sized to the page.

    渲染比例按页宽归一化（ms.render_scale），使得 595pt 与 3213pt 的页面
    都呈现相近的像素宽度 —— 观感一致，且避免超大页面产生巨幅位图。
    """
    if not self.doc or page_num < 0 or page_num >= len(self.doc):
        return
    self.current_page = page_num
    page = self.doc[page_num]
    self.page_height = page.rect.height
    self.page_factor = ms.page_factor(
        page.rect.width, page.rect.height, getattr(self, "mark_multiplier", 1.0))
    self.render_scale = ms.render_scale(
        page.rect.width, getattr(self, "view_scale", 1.0))
    scale = self.render_scale
    mat = pymupdf.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    img = QImage(
        pix.samples,
        pix.width,
        pix.height,
        pix.stride,
        QImage.Format.Format_RGB888,
    )
    pixmap = QPixmap.fromImage(img.copy())
    self.scene.clear()
    self.scene.addPixmap(pixmap)
    self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
    self.update_score_display()
    self.update_page_label()


def _pdf_font_for(text):
    """为文本选择合适的内置字体，并剔除字体无法渲染的字符。

    PyMuPDF 默认字体 Helvetica 无中文字形，直接写入中文会变成 "··"。
    这里对含中日韩字符的文本改用内置 CJK 字体 china-s，
    并删除连 china-s 也不支持的字符（如 emoji），避免输出方块乱码。
    """
    text = str(text)
    has_cjk = any("\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"
                   or "\uff00" <= ch <= "\uffef" for ch in text)
    fontname = "china-s" if has_cjk else "helv"
    try:
        font = pymupdf.Font(fontname)
        text = "".join(ch for ch in text if font.has_glyph(ord(ch)))
    except Exception:
        pass
    return fontname, text


def _insert_text_safe(page, point, text, fontsize=12, color=(1, 0, 0)):
    """向 PDF 写入文本，自动处理中文字体与不支持字符。"""
    fontname, safe = _pdf_font_for(text)
    if not safe:
        return
    page.insert_text(point, safe, fontsize=fontsize, color=color, fontname=fontname)


def save_result(self, show_dialog=True):
    """Save grading results back into PDF and JSON files.
    """
    if not self.pdf_path:
        return
    if not self.recorded_marks and not self.comment_manager.comments:
        return
    try:
        pdf_filename = os.path.basename(self.pdf_path)
        # 产物写入当前批次目录，实现多次作业之间互不干扰
        out_dir = _current_batch_dir(self, self.pdf_path)
        # 图片提交转成的 PDF：输出仍以图片主名命名，便于与 Canvas 归档对应
        out_name = os.path.splitext(pdf_filename)[0] + ".pdf"
        output_path = os.path.join(out_dir, out_name)
        source_ext = os.path.splitext(pdf_filename)[1].lower()
        source_id = source_ext[1:] if source_ext else "pdf"
        marks_json_path = os.path.join(out_dir, os.path.splitext(pdf_filename)[0] + ".marks.json")
        marks_data = {
            'recorded_marks': self.recorded_marks,
            'comments': self.comment_manager.comments,
            # 记录源文件指纹：若同名PDF被换成新作业，加载时不会误恢复旧记录
            'source_fingerprint': bm.file_fingerprint(self.pdf_path),
            'batch': getattr(self, "current_batch", bm.DEFAULT_BATCH),
            'source_type': source_id,
            'saved_at': __import__('time').strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(marks_json_path, 'w', encoding='utf-8') as f:
            json.dump(marks_data, f, ensure_ascii=False, indent=2)
        # 图片提交须以转换后的 PDF 为底稿，原始 jpg 无法直接写入标记
        base_doc = getattr(self, "render_source_path", None) or self.pdf_path
        out_doc = pymupdf.open(base_doc)
        # 与加载时保持一致：归一化旋转后再绘制，否则标记会错位 90°
        normalize_rotation(out_doc)
        for p_num, x, y, m_type, val in self.recorded_marks:
            if p_num >= len(out_doc):
                continue
            page = out_doc[p_num]
            # 笔画随页面尺寸缩放，避免大页面上的勾/叉小到看不清
            f = ms.page_factor(page.rect.width, page.rect.height,
                               getattr(self, "mark_multiplier", 1.0))
            lw = ms.LINE_WIDTH * f
            pdf_x, pdf_y = x, y
            if m_type == "score":
                r = ms.SCORE_RADIUS * f
                rect = pymupdf.Rect(pdf_x-r, pdf_y-r, pdf_x+r, pdf_y+r)
                annot = page.add_circle_annot(rect)
                annot.set_colors(stroke=(1,0,0))
                annot.set_border(width=lw)
                annot.update()
                _insert_text_safe(page, (pdf_x + ms.SCORE_TEXT_DX*f, pdf_y + ms.SCORE_FONT*f*0.35),
                                  f"+{val}", fontsize=ms.SCORE_FONT*f, color=(1,0,0))
            elif m_type in ("check", "half", "cross"):
                # 与界面预览共用同一份线段定义（见 mark_style.mark_segments），
                # 避免两处公式漂移导致「屏幕是 X、导出只有一条斜线」等问题
                for p1, p2 in ms.mark_segments(m_type):
                    page.draw_line((pdf_x + p1[0]*f, pdf_y + p1[1]*f),
                                   (pdf_x + p2[0]*f, pdf_y + p2[1]*f),
                                   color=(1,0,0), width=lw)
        for comment in self.comment_manager.comments:
            page_num = comment['page']
            if page_num < len(out_doc):
                page = out_doc[page_num]
                color = self.comment_manager.COLOR_MAP.get(comment['color'], (1,0,0))
                # 评语字号同样随页面缩放，否则大页面上的评语小得看不见
                pf = ms.page_factor(page.rect.width, page.rect.height,
                                    getattr(self, "mark_multiplier", 1.0))
                point = pymupdf.Point(comment['x'], comment['y'])
                _insert_text_safe(page, point, comment['text'],
                                  fontsize=comment['size'] * pf, color=color)
        # Grade 印章：位置与字号同步随页面缩放
        pf0 = ms.page_factor(out_doc[0].rect.width, out_doc[0].rect.height,
                             getattr(self, "mark_multiplier", 1.0))
        _insert_text_safe(out_doc[0],
                          (ms.GRADE_X*pf0, ms.GRADE_Y*pf0),
                          f"Grade: {self.total_score}",
                          fontsize=ms.GRADE_FONT*pf0, color=(1,0,0))
        # PyMuPDF 不允许直接覆盖已存在的同名文件，先移除再保存
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        out_doc.save(output_path, garbage=3, deflate=True)
        out_doc.close()
        self.update_scores_csv(out_dir, pdf_filename)
        if show_dialog:
            QMessageBox.information(self, "成功", f"批改完成！\n文件保存至：{output_path}")
        self.last_student = os.path.basename(self.pdf_path)
        self.save_config()
    except Exception as e:
        QMessageBox.critical(self, "错误", f"保存失败: {str(e)}")


def _identify_absentees(rows, meta, index):
    """列出成绩单里「有身份但本批次没有成绩」的真实学生。

    用于在导出前询问是否登记为 0 分，避免缺交学生被静默跳过。
    """
    id_cols = meta["id_cols"]
    name_col = meta["name_col"] if meta["name_col"] is not None else meta["student_col"]
    found = []
    for r in range(meta["data_start"], len(rows)):
        row = rows[r]
        if not row or not any(c.strip() for c in row):
            continue
        hit = False
        for ci in id_cols:
            if ci < len(row) and row[ci].strip() and row[ci].strip() in index:
                hit = True
                break
        if not hit and name_col is not None and name_col < len(row):
            if row[name_col].strip() and row[name_col].strip() in index:
                hit = True
        if hit:
            continue
        label = row[name_col].strip() if name_col is not None and name_col < len(row) else ""
        ids = [row[c].strip() for c in id_cols if c < len(row) and row[c].strip()]
        if ce.looks_like_real_student(label, ids):
            found.append((label or (ids[0] if ids else "(未知)"), ids))
    return found


def _group_absentees(self, absentees, done_dir):
    """把没有成绩的学生区分为「尚未批改」与「没有提交」。

    二者性质完全不同：前者只是还没轮到，若误记为 0 分会造成错录。
    区分依据是作业目录里是否存在该学生的作业文件。
    """
    graded = set()
    if os.path.isdir(done_dir):
        for f in os.listdir(done_dir):
            if f.endswith(".marks.json"):
                graded.add(f[:-len(".marks.json")])

    submitted = {}
    for p in getattr(self, "submission_list", None) or []:
        base = os.path.splitext(os.path.basename(p))[0]
        submitted[base] = p

    ungraded, nosubmit = [], []
    for name, ids in absentees:
        matched_ungraded = False
        for base in submitted:
            hit = any(sid and sid in base for sid in ids)
            if not hit:
                hit = bool(name) and name in base
            if hit:
                if base not in graded:
                    matched_ungraded = True
                break
        if matched_ungraded:
            ungraded.append(name)
        else:
            nosubmit.append(name)
    return ungraded, nosubmit


def export_to_canvas(self):
    """Export graded PDFs as a ZIP and update the Canvas gradebook CSV.
    """
    if self.pdf_path:
        root_done = os.path.join(os.path.dirname(self.pdf_path), "done")
    elif self.default_submissions_path:
        root_done = os.path.join(self.default_submissions_path, "done")
    else:
        QMessageBox.warning(self, "警告", "请先打开作业目录")
        return
    if not os.path.isdir(root_done):
        QMessageBox.warning(self, "警告", f"done目录不存在:\n{root_done}")
        return

    # 只导出当前批次的产物，避免混入其它作业的批改结果
    batch = getattr(self, "current_batch", None) or bm.DEFAULT_BATCH
    done_dir = bm.batch_dir(root_done, batch)
    if not os.path.isdir(done_dir):
        QMessageBox.warning(self, "警告", f"当前批次目录不存在:\n{done_dir}")
        return

    scores_path = os.path.join(done_dir, "scores.csv")
    if not os.path.exists(scores_path):
        QMessageBox.warning(self, "警告", f"scores.csv不存在:\n{scores_path}")
        return

    # ── 1. 选择 Canvas 成绩单 CSV ──
    course_csv_path, _ = QFileDialog.getOpenFileName(
        self, "选择Canvas成绩单 CSV（Grades 导出）", root_done, "CSV Files (*.csv)")
    if not course_csv_path:
        return

    try:
        rows, encoding = ce.read_csv_rows(course_csv_path)
        meta = ce.parse_course_csv(rows)
    except ce.CourseCSVError as e:
        QMessageBox.critical(self, "错误", str(e))
        return
    except Exception as e:
        QMessageBox.critical(self, "错误", f"读取成绩单失败: {str(e)}")
        return

    # ── 2. 选择作业列（下拉选择，避免手输 ID 选错列）──
    labels, aid_by_label = [], {}
    for aid in meta["assignments"]:
        label = f"{meta['assignment_labels'].get(aid, aid)}   [作业ID {aid}]"
        labels.append(label)
        aid_by_label[label] = aid
    chosen, ok = QInputDialog.getItem(
        self, "选择作业", "请选择要写入分数的作业列：", labels, 0, False)
    if not ok:
        return
    assignment_id = aid_by_label[chosen]

    # ── 3. 读取成绩并建立匹配索引 ──
    scores = ce.load_scores(scores_path)
    if not scores:
        QMessageBox.warning(self, "警告", f"scores.csv 中没有成绩记录:\n{scores_path}")
        return

    student_names = set()
    if meta["student_col"] is not None:
        for row in rows[meta["data_start"]:]:
            if meta["student_col"] < len(row):
                v = row[meta["student_col"]].strip()
                if v:
                    student_names.add(v)

    index, conflicts, primary_of = ce.build_identifier_index(scores, done_dir, student_names)
    absentees = _identify_absentees(rows, meta, index)
    absent_value = None
    if absentees:
        ungraded, nosubmit = _group_absentees(self, absentees, done_dir)
        lines = [f"本批次有 {len(absentees)} 位学生没有成绩记录："]
        if ungraded:
            lines.append("")
            lines.append(f"  · 尚未批改（作业已提交，但本批次未批）{len(ungraded)} 人："
                         + "、".join(ungraded[:8]) + ("…" if len(ungraded) > 8 else ""))
        if nosubmit:
            lines.append("")
            lines.append(f"  · 没有提交记录（目录中找不到作业）{len(nosubmit)} 人："
                         + "、".join(nosubmit[:8]) + ("…" if len(nosubmit) > 8 else ""))
        lines.append("")
        if ungraded:
            lines.append("⚠ 若本次作业尚未批改完，请不要登记：")
            lines.append("   “尚未批改”的学生会被一并写成 0 分。")
            lines.append("")
        lines.append("是否为这些学生登记 0 分（缺交）？")
        lines.append("选择“否”表示保留成绩单中的原值不动。")
        ans = QMessageBox.question(self, "缺交登记", "\n".join(lines))
        if ans == QMessageBox.StandardButton.Yes:
            absent_value = "0"

    result = ce.apply_scores(rows, meta, index, assignment_id, primary_of,
                             absent_value=absent_value)

    # ── 4. 原地写回成绩单（原子替换，不生成 .bak 等额外文件）──
    # 沿用成绩单原本的编码，保证「只更新分数、不改变文件本身」。
    # 原子写入替代 .bak 的安全作用：写入失败时原文件完好无损。
    try:
        ce.write_csv_rows_atomic(course_csv_path, rows, encoding=encoding)
    except Exception as e:
        QMessageBox.critical(self, "错误", f"写入成绩单失败: {str(e)}")
        return

    # ── 5. 打包 ZIP：默认文件名与作业目录同名，位置跟随成绩单所在目录 ──
    folder = self.default_submissions_path or (
        os.path.dirname(self.pdf_path) if self.pdf_path else "")
    folder_name = os.path.basename(os.path.normpath(folder)) if folder else ""
    default_zip = os.path.join(os.path.dirname(course_csv_path),
                               (folder_name or batch) + ".zip")
    zip_path, _ = QFileDialog.getSaveFileName(
        self, "保存ZIP文件", default_zip, "ZIP Files (*.zip)")
    if not zip_path:
        QMessageBox.information(self, "完成", f"成绩单已更新：\n{course_csv_path}")
        return
    if not zip_path.lower().endswith(".zip"):
        zip_path += ".zip"

    pdf_files = [f for f in sorted(os.listdir(done_dir)) if f.lower().endswith('.pdf')]
    if not pdf_files:
        QMessageBox.warning(self, "警告", "当前批次中没有PDF文件")
        return
    try:
        # 用 Moodle 兼容的方式打包：显式 UTF-8 标志 + Unix 平台元数据，
        # 避免中文文件名在 Moodle/PHP 端被按 DOS 编码解码而匹配失败。
        write_zip_for_moodle(zip_path,
                             [(os.path.join(done_dir, f), f) for f in pdf_files])
    except Exception as e:
        QMessageBox.critical(self, "错误", f"打包失败: {str(e)}")
        return

    # ── 6. 汇报结果，含未匹配诊断 ──
    lines = [
        f"成绩单已更新：{course_csv_path}",
        f"ZIP 已保存：{zip_path}",
        f"批次：{batch}（共打包 {len(pdf_files)} 份 PDF）",
        "",
        f"已回填成绩 {result['updated']} 人（作业列：{chosen}，编码 {encoding}）",
    ]
    if result.get("absent"):
        names = "、".join(result["absent_names"][:8]) + ("…" if len(result["absent_names"]) > 8 else "")
        lines.append(f"缺交登记 0 分 {result['absent']} 人：{names}")
    if result["unmatched"]:
        lines.append("")
        lines.append(f"⚠ 以下 {len(result['unmatched'])} 行未匹配到成绩（该列保持原值）：")
        lines.append(ce.describe_unmatched(result["unmatched"]))
    if result["unused"]:
        lines.append("")
        lines.append(f"⚠ 以下 {len(result['unused'])} 个学号的成绩未写入成绩单：")
        lines.append("  " + ", ".join(result["unused"][:20]))
    if conflicts:
        lines.append("")
        lines.append("⚠ 标识符冲突（同名不同分）：" + ", ".join(list(conflicts)[:10]))

    QMessageBox.information(self, "导出完成", "\n".join(lines))
