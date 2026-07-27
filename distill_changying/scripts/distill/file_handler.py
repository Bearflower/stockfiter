"""
文件处理模块

负责遍历博客目录、读取 Markdown 文件、提取文件标识符。
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import re

logger = logging.getLogger(__name__)

# frontmatter 的分隔标记（YAML 风格：以 --- 开头和结尾）
_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def list_blog_files(
    blog_dir: str,
    year: str | None = None,
    pattern: str | None = None,
) -> list[str]:
    """遍历博客目录，返回所有 .md 文件的绝对路径列表。

    支持按年份和文件名模式筛选。

    Args:
        blog_dir: 博客源文件目录的绝对路径
        year: 按年份筛选（匹配文件名中以该年份开头的文件，如 "2020"）
        pattern: 按通配符模式筛选文件名（如 "*2020*"）

    Returns:
        list[str]: 符合条件的 .md 文件绝对路径列表
    """
    if not os.path.isdir(blog_dir):
        logger.error("博客目录不存在: %s", blog_dir)
        return []

    files: list[str] = []
    for entry in os.listdir(blog_dir):
        # 排除 ._ 开头的 macOS 隐藏文件和非 .md 文件
        if not entry.endswith(".md") or entry.startswith("._"):
            continue

        # 按年份筛选：匹配文件名以 year 开头的文件
        if year is not None:
            basename = os.path.basename(entry)
            if not basename.startswith(year):
                continue

        # 按通配符模式筛选
        if pattern is not None:
            basename = os.path.basename(entry)
            if not fnmatch.fnmatch(basename, pattern):
                continue

        full_path = os.path.join(blog_dir, entry)
        if os.path.isfile(full_path):
            files.append(full_path)

    # 按文件名排序，保证处理顺序稳定
    files.sort(key=lambda f: os.path.basename(f))
    logger.info("在 %s 中找到 %d 个 .md 文件（year=%s, pattern=%s）", blog_dir, len(files), year, pattern)
    return files


def read_blog(filepath: str) -> str:
    """读取 Markdown 文件，返回去除 frontmatter 后的纯文本内容。

    处理文件编码错误，对无法解码的字符使用替换字符。

    Args:
        filepath: Markdown 文件的绝对路径

    Returns:
        str: 纯文本内容（已去除 frontmatter 如果有的话）

    Raises:
        FileNotFoundError: 文件不存在时抛出
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"博客文件不存在: {filepath}")

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        logger.error("读取文件失败 %s: %s", filepath, e)
        raise

    # 去除 frontmatter（如果有的话）
    content = _FRONTMATTER_PATTERN.sub("", content, count=1).strip()

    if not content:
        logger.warning("文件 %s 内容为空（去除 frontmatter 后）", filepath)

    return content


def get_blog_identifier(filepath: str) -> str:
    """从文件路径提取唯一标识符，用于状态追踪。

    使用文件名的 MD5 哈希前 8 位作为标识符，确保稳定性。

    Args:
        filepath: 文件的绝对路径

    Returns:
        str: 8 位十六进制字符串作为唯一标识符
    """
    basename = os.path.basename(filepath)
    digest = hashlib.md5(basename.encode("utf-8")).hexdigest()
    return digest[:8]