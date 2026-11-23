"""Canvas 成绩单 CSV 解析与分数回填的纯逻辑层（无 GUI 依赖，可独立测试）。

职责：
- 兼容多种编码读取 Canvas 导出的成绩单（utf-8-sig / utf-8 / gb18030）
- 识别表头中的作业列（含 "(作业ID)" 后缀）、学生名列、ID 类列
- 依据 done/ 目录下的文件名构建"多标识符 -> 分数"索引
  （Canvas 导出的 SIS Login ID / ID / 学生姓名 均可用于匹配）
- 把分数回填到指定作业列，并产出诊断信息（未匹配学生、未使用成绩）
"""
import csv
import os
import re
import tempfile

# 候选编码，按优先级尝试。Canvas 在中文环境导出常见 utf-8-sig，也有 gb18030/GBK。
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

# 作业列表头形如 "第1次 作业 (135353)"，括号内为 Canvas 作业 ID
_ASSIGNMENT_RE = re.compile(r"^(?P<name>.*?)\s*\(\s*(?P<aid>\d+)\s*\)\s*$")

# 用于从文件名中切出标识符的分隔符
_NAME_SEP_RE = re.compile(r"[_\-\s]+")

# 文件名中形如"作业一""第一次作业"等噪声词，不作为姓名标识符
_NOISE_WORDS = {
    "作业", "第", "一次", "第一次", "第二次", "第三次", "第一次作业", "第二次作业",
    "第三次作业", "作业一", "作业二", "作业三", "hw", "hw1", "hw2", "hw3", "homework",
    "first", "second", "third", "the", "and", "pdf", "first", "第一次作业。",
}


class CourseCSVError(Exception):
    """成绩单解析失败"""


def read_csv_rows(path):
    """读取 CSV 为二维列表，返回 (rows, 使用的编码)。"""
    last_err = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.reader(f)), enc
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise CourseCSVError(f"无法解码文件（尝试过 {', '.join(ENCODINGS)}）：{last_err}")


def write_csv_rows(path, rows, encoding="utf-8-sig"):
    """写出 CSV。默认 utf-8-sig，保证 Excel / Canvas 都能正确识别中文。"""
    with open(path, "w", encoding=encoding, newline="") as f:
        csv.writer(f).writerows(rows)


def write_csv_rows_atomic(path, rows, encoding="utf-8-sig"):
    """把 rows 原子写回 path：先写同目录临时文件，成功后再整体替换。

    不再另存 .bak 备份，但安全性不降低：直接以 "w" 打开原文件会先截断它，
    一旦写入中途失败（磁盘满、编码错误、被 Excel 占用）成绩单就损坏了；
    原子替换则保证原文件要么完整保留、要么被完整的新内容取代。
    临时文件在成功或失败后都会被清除，目录里不会留下任何多余文件。
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix="._canvas_", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        write_csv_rows(tmp, rows, encoding=encoding)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def parse_course_csv(rows):
    """解析 Canvas 成绩单结构。

    返回 dict:
      header        表头行
      data_start    数据起始行下标
      student_col   学生名列下标（可能为 None）
      id_cols       可用于匹配的 ID 类列下标列表
      name_col      姓名列下标
      assignments   {作业ID: 列下标}
      assignment_labels {作业ID: 显示名}
    """
    if not rows:
        raise CourseCSVError("成绩单为空")

    header = rows[0]
    stripped = [h.strip() for h in header]

    student_col = None
    name_col = None
    id_cols = []
    assignments = {}
    assignment_labels = {}

    for i, h in enumerate(stripped):
        low = h.lower()
        if h == "Student" or low == "student":
            student_col = i
        elif h in ("Name", "name") and name_col is None:
            name_col = i
        elif "sis login id" in low or "sis user id" in low or low in ("id", "sis id", "student id"):
            id_cols.append(i)
        m = _ASSIGNMENT_RE.match(h)
        if m:
            aid = m.group("aid")
            assignments[aid] = i
            assignment_labels[aid] = m.group("name") or h

    if not assignments:
        raise CourseCSVError(
            "未在表头中找到任何作业列（形如 “作业名 (作业ID)”）。\n当前表头：\n"
            + " | ".join(stripped)
        )

    # 数据起始行：Canvas 导出在表头后常有一行 "Points Possible"，需跳过
    data_start = 1
    if len(rows) > 1:
        first = rows[1][0].strip().lower() if rows[1] else ""
        if "points possible" in first:
            data_start = 2

    return {
        "header": header,
        "data_start": data_start,
        "student_col": student_col,
        "id_cols": id_cols,
        "name_col": name_col,
        "assignments": assignments,
        "assignment_labels": assignment_labels,
    }


def _tokens_from_filename(base):
    """从文件名切出候选标识符（数字串或中文姓名）。"""
    stem = os.path.splitext(base)[0]
    parts = [p for p in _NAME_SEP_RE.split(stem) if p]
    digits, names = [], []
    for p in parts:
        if p.isdigit():
            digits.append(p)
            continue
        low = p.lower()
        if low in _NOISE_WORDS:
            continue
        # 纯中文、长度 2-4 的片段才可能是姓名
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", p):
            names.append(p)
    return digits, names


def build_identifier_index(scores, done_dir=None, student_names=None):
    """构建 "标识符 -> 分数" 索引。

    scores:        {学号: 分数字符串}，来自 scores.csv
    done_dir:      done/ 目录，用其中的文件名补充 ID / 姓名标识符
    student_names: 成绩单中出现的学生姓名集合，用于过滤文件名里的噪声

    返回 (index, conflicts)
      index    {标识符: 分数}
      conflicts 标识符 -> 已存在分数（说明不同学生共用了同一标识符）
    """
    index = {}
    conflicts = {}
    primary_of = {}   # 标识符 -> 其所属的成绩主键（scores.csv 的学号）

    def put(key, score, primary=None):
        if not key:
            return
        if key in index:
            if index[key] != score:
                conflicts.setdefault(key, index[key])
            return
        index[key] = score
        primary_of[key] = primary if primary is not None else key

    for key, score in scores.items():
        put(key, score, key)

    if done_dir and os.path.isdir(done_dir):
        names = set(student_names or ())
        for fname in sorted(os.listdir(done_dir)):
            # 只从 marks.json 提取标识符：PDF 是产物，scores.csv 是汇总表
            if not fname.lower().endswith(".marks.json"):
                continue
            base = os.path.splitext(fname)[0]
            key = base.split("_")[0]
            score = scores.get(key)
            if score is None:
                score = scores.get(base)
            if score is None:
                continue
            digits, cand_names = _tokens_from_filename(base)
            for d in digits:
                put(d, score, key)
            for n in cand_names:
                if n in names:
                    put(n, score, key)

    return index, conflicts, primary_of


def load_scores(scores_path):
    """读取 scores.csv（无表头，两列：学号, 分数）。"""
    scores = {}
    if not os.path.exists(scores_path):
        return scores
    with open(scores_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip():
                scores[row[0].strip()] = row[1].strip()
    return scores


# Canvas 导出成绩单时自带的示例行，不属于真实学生。
# Canvas 中文界面下该行名为"测验学生"，英文界面为 "Test Student"，
# 两者都要拦掉 —— 否则缺交登记会把示例行写成 0 分混进成绩单。
_TEST_ROW_MARKERS = ("test student", "学生, 测试", "学生, 测验",
                     "测验学生", "测试学生", "points possible")


def looks_like_real_student(name, ids):
    """判断一行是否代表真实学生（排除 Canvas 的示例行/空行）。

    Canvas 示例行有三处特征，任一命中即判为假：
      - 姓名为测试账号（中英文界面写法不同，都列出）
      - SIS Login ID 是 32 位十六进制串（Canvas 给示例行的固定写法）
      - 姓名与学号同时为空（汇总行等残留）
    """
    if not name and not ids:
        return False
    lowered = (name or "").lower()
    for marker in _TEST_ROW_MARKERS:
        if marker in lowered:
            return False
    for sid in ids:
        if len(sid) == 32 and all(c in "0123456789abcdefABCDEF" for c in sid):
            return False
    return True


def apply_scores(rows, meta, index, assignment_id, primary_of=None, absent_value=None):
    """把分数回填到指定作业列。

    Args:
        absent_value: 若为 None（默认），未匹配的行保持原值不变。
            若给字符串（如 "0"），则把「有身份但本批次没有成绩」的真实学生行
            写成该值，用于登记缺交/零分。Canvas 示例行始终跳过。

    返回 dict:
      updated    成功回填分数的行数
      absent     被登记为缺交的行数
      unmatched  既无成绩也未登记的行（列表 of dict）
      unused     有成绩但未写入到任何行的学生主键（列表）
    """
    col = meta["assignments"][assignment_id]
    id_cols = meta["id_cols"]
    name_col = meta["name_col"] if meta["name_col"] is not None else meta["student_col"]
    primary_of = primary_of or {}

    updated = 0
    absent = 0
    unmatched = []
    absent_names = []
    used_primaries = set()

    for r in range(meta["data_start"], len(rows)):
        row = rows[r]
        if not row or not any(c.strip() for c in row):
            continue

        score = None
        matched_key = None

        # 1) ID 类列优先（SIS Login ID / SIS User ID / ID）
        for ci in id_cols:
            if ci < len(row):
                val = row[ci].strip()
                if val and val in index:
                    score = index[val]
                    matched_key = val
                    break

        # 2) 学生姓名兜底（覆盖“测试学生”这类无学号账号）
        if score is None and name_col is not None and name_col < len(row):
            val = row[name_col].strip()
            if val and val in index:
                score = index[val]
                matched_key = val

        label = row[name_col].strip() if name_col is not None and name_col < len(row) else ""
        ids = [row[c].strip() for c in id_cols if c < len(row) and row[c].strip()]

        if score is None:
            # 缺交登记：本次作业没有成绩的真实学生，明确写 0
            if absent_value is not None and looks_like_real_student(label, ids):
                while len(row) <= col:
                    row.append("")
                row[col] = absent_value
                absent += 1
                absent_names.append(label or ids[0])
                continue
            unmatched.append({"name": label, "ids": ids})
            continue

        while len(row) <= col:
            row.append("")
        row[col] = score
        used_primaries.add(primary_of.get(matched_key, matched_key))
        updated += 1

    # 有成绩、但没能在成绩单中找到对应行的学生主键
    unused = sorted(p for p in set(primary_of.values()) if p and p not in used_primaries)

    return {"updated": updated, "absent": absent, "absent_names": absent_names,
            "unmatched": unmatched, "unused": unused}


def describe_unmatched(unmatched, limit=12):
    """把未匹配列表整理成可读文本。"""
    if not unmatched:
        return ""
    lines = []
    for item in unmatched[:limit]:
        name = item.get("name") or "(无姓名)"
        ids = ", ".join(item.get("ids", []))
        lines.append(f"  - {name}  [{ids}]")
    if len(unmatched) > limit:
        lines.append(f"  ... 另有 {len(unmatched) - limit} 人")
    return "\n".join(lines)
