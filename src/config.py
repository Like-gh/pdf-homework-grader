"""Configuration management for PDFGrader.

Provides load_config and save_config methods that operate on a PDFGrader instance.

配置项
------
submissions_path  作业目录
last_student      上次打开的学生文件
view_scale        界面渲染的相对缩放（1.0 = 页面渲染到标准像素宽度）
                  取代旧版的 zoom（绝对倍数），见 mark_style.render_scale
zoom              旧版遗留字段，仅用于迁移；不再写入
mark_multiplier   笔画尺寸倍率，用于个别特别密集或稀疏的作业
full_score        作业满分，超出时总分标红
comment_size      评语字号（参考单位）
score_presets     计分预设值列表
comment_presets   预设评语列表
current_batch     当前作业批次
"""
import json, os

# 计分面板的默认预设值
DEFAULT_SCORE_PRESETS = [1, 2, 3, 5, 10, 20]

# 预设评语（可在界面上增删，结果写回 config.json）
DEFAULT_COMMENT_PRESETS = [
    "Well done! ✓",
    "Good effort!",
    "Add oil! 加油!",
    "Needs improvement",
]


def _read_raw(config_path):
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"读取配置失败: {e}")
        return {}


def load_config(self):
    """Load configuration from config.json into the instance."""
    cfg = _read_raw(self.config_path)

    self.default_submissions_path = cfg.get("submissions_path", "")
    self.last_student = cfg.get("last_student", "")

    # ── 缩放：旧版 zoom 是绝对倍数，新版 view_scale 相对页宽 ──
    view_scale = cfg.get("view_scale")
    if view_scale is None:
        # 迁移：旧配置常见 zoom=2.0，在新语义下相当于约 0.85 倍标准宽度
        old_zoom = cfg.get("zoom", 1.0)
        try:
            old_zoom = float(old_zoom)
        except (TypeError, ValueError):
            old_zoom = 1.0
        # A4 在旧版 zoom=2 下渲染成 1190px 宽，接近标准的 1400px
        view_scale = 1.0 if old_zoom > 1.5 else old_zoom
    try:
        self.view_scale = float(view_scale)
    except (TypeError, ValueError):
        self.view_scale = 1.0
    self.view_scale = max(0.4, min(3.0, self.view_scale))
    # 兼容旧代码里可能残留的 self.zoom
    self.zoom = self.view_scale

    try:
        self.mark_multiplier = float(cfg.get("mark_multiplier", 1.0))
    except (TypeError, ValueError):
        self.mark_multiplier = 1.0
    self.mark_multiplier = max(0.5, min(3.0, self.mark_multiplier))

    full = cfg.get("full_score", "")
    try:
        self.full_score = float(full) if str(full).strip() else None
    except (TypeError, ValueError):
        self.full_score = None

    try:
        self.comment_size = int(cfg.get("comment_size", 12))
    except (TypeError, ValueError):
        self.comment_size = 12

    presets = cfg.get("score_presets")
    self.score_presets = presets if isinstance(presets, list) and presets else list(DEFAULT_SCORE_PRESETS)

    cps = cfg.get("comment_presets")
    if isinstance(cps, list) and cps:
        self.comment_presets = [str(t) for t in cps if str(t).strip()]
    else:
        self.comment_presets = list(DEFAULT_COMMENT_PRESETS)

    if hasattr(self, "current_batch"):
        self.current_batch = cfg.get("current_batch", "")


def save_config(self):
    """Save current configuration to config.json."""
    try:
        cfg = {
            "submissions_path": self.default_submissions_path,
            "last_student": self.last_student,
            "view_scale": round(getattr(self, "view_scale", 1.0), 2),
            "mark_multiplier": round(getattr(self, "mark_multiplier", 1.0), 2),
            "full_score": getattr(self, "full_score", None) or "",
            "comment_size": getattr(self, "comment_size", 12),
            "score_presets": getattr(self, "score_presets", list(DEFAULT_SCORE_PRESETS)),
            "comment_presets": getattr(self, "comment_presets", list(DEFAULT_COMMENT_PRESETS)),
        }
        if hasattr(self, "current_batch"):
            cfg["current_batch"] = self.current_batch
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存配置失败: {e}")
