#!/usr/bin/env python3
"""
Chapter file path helpers.

This project has seen multiple chapter filename conventions:
1) Legacy flat layout: 正文/第0007章.md
2) Volume layout:    正文/第1卷/第007章-章节标题.md

To keep scripts robust, always resolve chapter files via these helpers instead of hardcoding a format.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


_CHAPTER_NUM_RE = re.compile(r"第(?P<num>\d+)章")
_OUTLINE_HEADING_RE = re.compile(r"^#{1,6}\s*第\s*(?P<num>\d+)\s*章[：:]\s*(?P<title>.+?)\s*$", re.MULTILINE)
_SPLIT_OUTLINE_FILENAME_RE = re.compile(r"^第0*(?P<num>\d+)章[-—_ ]+(?P<title>.+?)\.md$")


def volume_num_for_chapter(chapter_num: int, *, chapters_per_volume: int = 50) -> int:
    if chapter_num <= 0:
        raise ValueError("chapter_num must be >= 1")
    return (chapter_num - 1) // chapters_per_volume + 1


def extract_chapter_num_from_filename(filename: str) -> Optional[int]:
    m = _CHAPTER_NUM_RE.search(filename)
    if not m:
        return None
    try:
        return int(m.group("num"))
    except ValueError:
        return None


def _safe_title_for_filename(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        return ""

    try:
        from security_utils import sanitize_filename
    except ImportError:  # pragma: no cover
        from scripts.security_utils import sanitize_filename

    safe_title = sanitize_filename(cleaned, max_length=60)
    return "" if safe_title == "unnamed_entity" else safe_title


def _extract_title_from_outline_text(outline_text: str, chapter_num: int) -> str:
    for match in _OUTLINE_HEADING_RE.finditer(outline_text):
        if int(match.group("num")) != chapter_num:
            continue
        return _safe_title_for_filename(match.group("title"))
    return ""


def _extract_title_from_split_outline_filename(outline_dir: Path, chapter_num: int) -> str:
    patterns = [
        f"第{chapter_num}章*.md",
        f"第{chapter_num:02d}章*.md",
        f"第{chapter_num:03d}章*.md",
        f"第{chapter_num:04d}章*.md",
    ]
    for pattern in patterns:
        for path in sorted(outline_dir.glob(pattern)):
            match = _SPLIT_OUTLINE_FILENAME_RE.match(path.name)
            if not match:
                continue
            if int(match.group("num")) != chapter_num:
                continue
            title = _safe_title_for_filename(match.group("title"))
            if title:
                return title
    return ""


def extract_chapter_title(project_root: Path, chapter_num: int) -> str:
    """从详细大纲提取章节标题，用于生成更直观的章节文件名。"""
    try:
        from chapter_outline_loader import load_chapter_outline
    except ImportError:  # pragma: no cover
        from scripts.chapter_outline_loader import load_chapter_outline

    outline_text = load_chapter_outline(project_root, chapter_num, max_chars=None)
    if not outline_text.startswith("⚠️"):
        title = _extract_title_from_outline_text(outline_text, chapter_num)
        if title:
            return title

    outline_dir = project_root / "大纲"
    if outline_dir.exists():
        return _extract_title_from_split_outline_filename(outline_dir, chapter_num)
    return ""


def _build_chapter_filename(project_root: Path, chapter_num: int, *, use_volume_layout: bool) -> str:
    padded = f"{chapter_num:03d}" if use_volume_layout else f"{chapter_num:04d}"
    title = extract_chapter_title(project_root, chapter_num)
    if title:
        return f"第{padded}章-{title}.md"
    return f"第{padded}章.md"


def find_chapter_file(project_root: Path, chapter_num: int) -> Optional[Path]:
    """
    Find an existing chapter file for chapter_num under project_root/正文.
    Returns the first match (stable sorted order) or None if not found.
    """
    chapters_dir = project_root / "正文"
    if not chapters_dir.exists():
        return None

    legacy = chapters_dir / f"第{chapter_num:04d}章.md"
    if legacy.exists():
        return legacy

    vol_dir = chapters_dir / f"第{volume_num_for_chapter(chapter_num)}卷"
    if vol_dir.exists():
        candidates = sorted(vol_dir.glob(f"第{chapter_num:03d}章*.md")) + sorted(vol_dir.glob(f"第{chapter_num:04d}章*.md"))
        for c in candidates:
            if c.is_file():
                return c

    # >>> vol-local-renumber (shikong) >>>
    # fork 内置行为（原先由「工具/安装插件卷内编号补丁.py」在运行时给已安装插件打补丁，
    # 现在合入 fork 源码，无需再跑补丁脚本）。
    #
    # 背景：本项目「卷内重新编号」——正文文件名里的编号是**卷内号**，不是全局章号。
    # 同时卷目录名带中文后缀（正文/第2卷-理想构筑，而非 正文/第2卷），每卷章数不等
    # （85/67/35/75），而 volume_num_for_chapter 硬编码每卷 50 章。上面两条规则因此
    # 从第 86 章起一律返回 None。
    #
    # 做法：先查 大纲/垫片/卷N/ 下的「全局章号垫片」，从文件名解析出卷号+卷内号，
    # 再据此到 正文/第N卷*/ 下定位正文。
    #
    # 垫片命名约定（唯一真源，由项目侧 工具/章节编号同步.py 从 state.json 的
    # volumes_planned 重建，校验工具为 工具/全局章号映射.py）：
    #     大纲/垫片/卷N/第{全局号:04d}章-卷{vol}第{idx:02d}章-{标题}.md
    # 例：第0086章-卷2第01章-黄金六个月.md → 全局 86 / 卷 2 / 卷内 1
    shim_root = project_root / "大纲" / "垫片"
    if shim_root.is_dir():
        for d in sorted(shim_root.iterdir()):
            if not d.is_dir():
                continue
            for shim in sorted(d.glob(f"第{chapter_num:04d}章-卷*第*章-*.md")):
                m = re.match(r"^第\d+章-卷(\d+)第(\d+)章-", shim.name)
                if not m:
                    continue
                vol, idx = int(m.group(1)), int(m.group(2))
                # 卷内编号风格不统一：4 位补零 / 3 位补零 / 不补零，三种都试。
                for vol_dir in sorted(chapters_dir.glob(f"第{vol}卷*")):
                    if not vol_dir.is_dir():
                        continue
                    for pat in (f"第{idx:04d}章*.md", f"第{idx:03d}章*.md", f"第{idx}章*.md"):
                        for c in sorted(vol_dir.glob(pat)):
                            if c.is_file():
                                return c
    # <<< vol-local-renumber (shikong) <<<

    # Fallback: search anywhere under 正文/ (supports custom layouts)
    candidates = sorted(chapters_dir.rglob(f"第{chapter_num:03d}章*.md")) + sorted(chapters_dir.rglob(f"第{chapter_num:04d}章*.md"))
    for c in candidates:
        if c.is_file():
            return c

    return None


def default_chapter_draft_path(project_root: Path, chapter_num: int, *, use_volume_layout: bool = False) -> Path:
    """
    Preferred draft path when creating a new chapter file.

    Args:
        project_root: 项目根目录
        chapter_num: 章节号
        use_volume_layout: True 使用卷布局 (正文/第N卷/第NNN章-章节标题.md)，False 使用平坦布局 (正文/第NNNN章-章节标题.md)

    Default is flat layout. If the detailed outline already has a chapter title,
    append it to the filename for better discoverability.
    """
    if use_volume_layout:
        vol_dir = project_root / "正文" / f"第{volume_num_for_chapter(chapter_num)}卷"
        return vol_dir / _build_chapter_filename(project_root, chapter_num, use_volume_layout=True)
    else:
        return project_root / "正文" / _build_chapter_filename(project_root, chapter_num, use_volume_layout=False)

