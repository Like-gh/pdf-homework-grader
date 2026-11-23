"""作业批次管理：隔离多次作业之间的批改记录。

问题背景
--------
批改产物（标注 PDF、marks.json、scores.csv）都平铺在作业目录下的 done/ 中，
且以"文件名"为唯一键。这导致跨作业污染：

1. 同名 PDF 会恢复上一批的标记与评语（屏幕上出现上一批笔迹）
2. 总分被累加成两批之和（如 90 + 55 = 145）
3. scores.csv 中残留上一批学生的分数，导出到新成绩单会造成错录
4. 导出 ZIP 时混入上一批的 PDF

解决思路
--------
引入"作业批次（batch）"概念，把产物按批次隔离存放：

    作业目录/
    ├── done/
    │   ├── _batches.json          # 批次清单（当前批次、各批产物清单）
    │   ├── <批次名>/
    │   │   ├── 学生A.pdf
    │   │   ├── 学生A.marks.json
    │   │   └── scores.csv
    │   └── ...

同时在 marks.json 中记录"源文件指纹"（大小 + 修改时间），加载时校验：
即便批次名相同，若源 PDF 已被替换（换成了新作业），旧记录也不会被误恢复。

批次识别
--------
默认按作业目录名 + 目录内容指纹自动生成批次名；用户也可在界面上手动指定，
用于区分同一目录下的多次作业（例如 hw1 / hw2）。
"""
import hashlib
import json
import os
import re
import time

BATCHES_FILE = "_batches.json"
DEFAULT_BATCH = "default"

# 采样指纹读取的头尾字节数
SAMPLE_BYTES = 65536


# ── 源文件指纹 ────────────────────────────────────────────────

def file_fingerprint(path):
    """计算源文件的指纹，用于识别"同名但内容已换"的情况。

    早先只用「大小 + 修改时间」。这在以下场景会误判 —— 只要文件被复制、
    备份、跨盘搬迁或从压缩包解压，mtime 就会变，于是辛苦批改完的记录被
    判定为"源文件已变更"而丢弃。批改记录静默消失的代价远大于误复用一条
    旧记录，因此改为：

        size + 首尾各 64KB 内容的采样哈希

    这样无论文件被搬到哪里、用什么方式复制，指纹都不变；而内容真的换了
    （学生提交了新的同名作业）则几乎必然 size 或采样内容不同。

    只用首尾采样而非全量哈希，是因为作业文件动辄几十 MB（实测最大 89MB），
    全量哈希会明显拖慢打开速度，而首尾采样已足以区分两次不同的提交。
    """
    try:
        st = os.stat(path)
        size = st.st_size
    except OSError:
        return None
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            h.update(f.read(SAMPLE_BYTES))
            if size > SAMPLE_BYTES * 2:
                f.seek(size - SAMPLE_BYTES)
                h.update(f.read(SAMPLE_BYTES))
    except OSError:
        return None
    return {
        "size": size,
        "content": h.hexdigest()[:16],
        "mtime": int(st.st_mtime * 1000),
    }


def fingerprint_matches(saved, current):
    """判断已保存的指纹是否与当前文件一致。

    优先比对内容采样（与存放位置无关）；若快照是旧版本（只有 size+mtime），
    则退化为比对文件大小，并在一致时**放行** —— 宁可恢复一条可能略旧的
    记录，也不能丢掉老师已经批完的内容。
    """
    if not saved or not current:
        return False
    if saved.get("size") != current.get("size"):
        return False
    sc, cc = saved.get("content"), current.get("content")
    if sc and cc:
        return sc == cc
    # 旧格式快照或不完整指纹：大小一致即视为同一份作业
    return True


# ── 批次标识 ──────────────────────────────────────────────────

def sanitize_batch_name(name):
    """把任意文本规整成安全的目录名。"""
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("._")
    return name or DEFAULT_BATCH


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")
SUBMISSION_SUFFIXES = (".pdf",) + IMAGE_SUFFIXES


def dir_content_signature(folder, suffixes=None):
    """对目录内文件（按名称+大小）求摘要，作为"这批提交"的指纹。

    默认统计所有可作为提交的文件（PDF 与常见图片），图片提交也要参与
    目录指纹，否则"换了一批图片作业"会被误判成同一批而复用旧记录。
    """
    suffixes = suffixes or SUBMISSION_SUFFIXES
    h = hashlib.sha1()
    if not os.path.isdir(folder):
        return ""
    try:
        entries = sorted(f for f in os.listdir(folder)
                         if os.path.isfile(os.path.join(folder, f))
                         and f.lower().endswith(tuple(suffixes)))
    except OSError:
        return ""
    for f in entries:
        try:
            size = os.path.getsize(os.path.join(folder, f))
        except OSError:
            size = 0
        h.update(f"{f}:{size}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def suggest_batch_name(submissions_dir, existing=None):
    """为一次新的批改推荐批次名。

    规则：
    - 若目录内已有批次，且目录内容指纹与上次相同 → 沿用上次批次名（续批）
    - 否则 → 生成 "批次_日期_摘要" 形式的新名字
    """
    sig = dir_content_signature(submissions_dir)
    if existing:
        for name, info in existing.items():
            if info.get("signature") == sig and sig:
                return name
    stamp = time.strftime("%Y%m%d_%H%M")
    return sanitize_batch_name(f"batch_{stamp}_{sig[:6] if sig else 'new'}")


# ── 批次清单读写 ──────────────────────────────────────────────

def load_batches(done_dir):
    """读取 done/_batches.json。"""
    path = os.path.join(done_dir, BATCHES_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_batches(done_dir, batches):
    """写入 done/_batches.json。"""
    os.makedirs(done_dir, exist_ok=True)
    path = os.path.join(done_dir, BATCHES_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batches, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存批次清单失败: {e}")


def register_batch(done_dir, batch_name, submissions_dir, extra=None):
    """登记/更新一个批次，返回批次信息字典。"""
    batches = load_batches(done_dir)
    info = batches.get(batch_name, {})
    info.update({
        "batch": batch_name,
        "signature": dir_content_signature(submissions_dir),
        "submissions_dir": os.path.abspath(submissions_dir),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    if extra:
        info.update(extra)
    batches[batch_name] = info
    save_batches(done_dir, batches)
    return info


def batch_dir(done_dir, batch_name):
    """返回某批次的产物目录路径。"""
    return os.path.join(done_dir, sanitize_batch_name(batch_name))


def list_batches(done_dir):
    """列出所有批次，按最近更新排序。"""
    batches = load_batches(done_dir)
    return sorted(batches.items(), key=lambda kv: kv[1].get("updated_at", ""), reverse=True)
