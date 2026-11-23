"""
PDF评语管理器
支持在PDF上添加、编辑、删除评语
"""

import pymupdf  # PyMuPDF
from typing import List, Dict, Tuple


class CommentManager:
    """评语管理器"""

    # 预设评语
    PRESETS = {
        "excellent": "Well done! ✓",
        "good": "Good effort! 👍",
        "improve": "Needs improvement",
        "add_oil": "Add oil! 加油!",
        "careful": "Be more careful",
        "check": "Please check again",
        "great": "Great work!",
        "nice": "Nice job!",
        "perfect": "Perfect! 完美!",
        "try_again": "Try again!",
    }

    # 颜色映射
    COLOR_MAP = {
        'red': (1, 0, 0),
        'blue': (0, 0, 1),
        'green': (0, 1, 0),
        'black': (0, 0, 0),
        'orange': (1, 0.5, 0),
        'purple': (0.5, 0, 0.5),
    }

    def __init__(self):
        """初始化评语管理器"""
        self.comments: List[Dict] = []  # 存储所有评语
        self._next_id = 0              # 单调递增ID，避免删除后ID重复

    def add_comment(self, page: int, x: float, y: float, text: str,
                   color: str = "red", size: int = 12) -> int:
        """
        添加评语

        Args:
            page: 页码（0开始）
            x: X坐标
            y: Y坐标
            text: 评语文本
            color: 颜色名称
            size: 字体大小

        Returns:
            评语ID
        """
        comment_id = self._next_id
        self._next_id += 1

        self.comments.append({
            'id': comment_id,
            'page': page,
            'x': x,
            'y': y,
            'text': text,
            'color': color,
            'size': size
        })

        return comment_id

    def get_page_comments(self, page: int) -> List[Dict]:
        """
        获取某页的所有评语

        Args:
            page: 页码

        Returns:
            该页的评语列表
        """
        return [c for c in self.comments if c['page'] == page]

    def remove_comment(self, comment_id: int) -> bool:
        """
        删除评语

        Args:
            comment_id: 评语ID

        Returns:
            是否删除成功
        """
        for i, comment in enumerate(self.comments):
            if comment['id'] == comment_id:
                self.comments.pop(i)
                return True

        return False

    def remove_last_comment(self) -> bool:
        """
        删除最后一条评语

        Returns:
            是否删除成功
        """
        if self.comments:
            comment = self.comments.pop()
            return True

        return False

    def update_comment(self, comment_id: int, **kwargs) -> bool:
        """
        更新评语

        Args:
            comment_id: 评语ID
            **kwargs: 要更新的字段 (text, x, y, color, size)

        Returns:
            是否更新成功
        """
        for comment in self.comments:
            if comment['id'] == comment_id:
                comment.update(kwargs)
                return True

        return False

    def clear_page_comments(self, page: int) -> int:
        """
        清除某页的所有评语

        Args:
            page: 页码

        Returns:
            删除的评语数量
        """
        original_count = len(self.comments)
        self.comments = [c for c in self.comments if c['page'] != page]
        deleted_count = original_count - len(self.comments)
        return deleted_count

    def clear_all_comments(self) -> int:
        """
        清除所有评语

        Returns:
            删除的评语数量
        """
        count = len(self.comments)
        self.comments = []
        self._next_id = 0
        return count

    def save_to_pdf(self, input_pdf_path: str, output_pdf_path: str) -> bool:
        """
        保存评语到PDF文件

        Args:
            input_pdf_path: 输入PDF路径
            output_pdf_path: 输出PDF路径

        Returns:
            是否保存成功
        """
        try:
            doc = pymupdf.open(input_pdf_path)

            for comment in self.comments:
                page_num = comment['page']
                if page_num >= len(doc):
                    continue

                page = doc[page_num]

                # 获取颜色
                color = self.COLOR_MAP.get(comment['color'], (1, 0, 0))

                # 添加文本
                point = pymupdf.Point(comment['x'], comment['y'])
                page.insert_text(
                    point,
                    comment['text'],
                    fontsize=comment['size'],
                    color=color
                )

            doc.save(output_pdf_path)
            doc.close()

            return True

        except Exception as e:
            return False

    def export_comments_to_csv(self, output_path: str) -> bool:
        """
        导出评语到CSV文件

        Args:
            output_path: 输出CSV路径

        Returns:
            是否导出成功
        """
        try:
            import csv

            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['页码', '位置X', '位置Y', '评语', '颜色', '字体大小'])

                for comment in self.comments:
                    writer.writerow([
                        comment['page'],
                        comment['x'],
                        comment['y'],
                        comment['text'],
                        comment['color'],
                        comment['size']
                    ])

            return True

        except Exception as e:
            return False

    def get_statistics(self) -> Dict:
        """
        获取评语统计信息

        Returns:
            统计信息字典
        """
        if not self.comments:
            return {
                'total': 0,
                'by_page': {},
                'by_color': {},
                'by_text': {}
            }

        by_page = {}
        by_color = {}
        by_text = {}

        for comment in self.comments:
            # 按页统计
            page = comment['page']
            by_page[page] = by_page.get(page, 0) + 1

            # 按颜色统计
            color = comment['color']
            by_color[color] = by_color.get(color, 0) + 1

            # 按文本统计
            text = comment['text']
            by_text[text] = by_text.get(text, 0) + 1

        return {
            'total': len(self.comments),
            'by_page': by_page,
            'by_color': by_color,
            'by_text': by_text
        }

    def get_summary(self) -> str:
        """
        获取评语摘要

        Returns:
            摘要字符串
        """
        stats = self.get_statistics()

        summary = f"评语总数: {stats['total']}\n"

        if stats['by_page']:
            summary += f"涉及页数: {len(stats['by_page'])}\n"

        if stats['by_text']:
            summary += "常用评语:\n"
            for text, count in sorted(stats['by_text'].items(), key=lambda x: x[1], reverse=True)[:5]:
                summary += f"  - {text}: {count}次\n"

        return summary
