#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

try:
    from chapter_paths import volume_num_for_chapter
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import volume_num_for_chapter


_CHAPTER_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def _parse_chapters_range(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = _CHAPTER_RANGE_RE.match(value)
    if not match:
        return None
    try:
        start = int(match.group(1))
        end = int(match.group(2))
    except ValueError:
        return None
    if start <= 0 or end <= 0 or start > end:
        return None
    return start, end


def volume_num_for_chapter_from_state(project_root: Path, chapter_num: int) -> int | None:
    state_path = project_root / ".webnovel" / "state.json"
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(state, dict):
        return None

    progress = state.get("progress")
    if not isinstance(progress, dict):
        return None

    volumes_planned = progress.get("volumes_planned")
    if not isinstance(volumes_planned, list):
        return None

    best: tuple[int, int] | None = None
    for item in volumes_planned:
        if not isinstance(item, dict):
            continue
        volume = item.get("volume")
        if not isinstance(volume, int) or volume <= 0:
            continue
        parsed = _parse_chapters_range(item.get("chapters_range"))
        if not parsed:
            continue
        start, end = parsed
        if start <= chapter_num <= end:
            candidate = (start, volume)
            if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                best = candidate

    return best[1] if best else None


def _find_split_outline_file(outline_dir: Path, chapter_num: int) -> Path | None:
    """查找单章大纲（垫片）。

    >>> shim-subdir-rglob (shikong) >>>
    fork 内置行为（原先由「工具/安装插件垫片子目录补丁.py」在运行时给已安装插件打补丁，
    现在合入 fork 源码，无需再跑补丁脚本）。

    生成的「全局章号垫片」放在 ``大纲/垫片/卷N/`` 子目录下，好让大纲根目录只留作者手写的
    卷级文件：
        - 之前  大纲/第0001章-卷1第01章-序章·幽蓝脉冲.md   （几百个垫片铺满根目录）
        - 之后  大纲/垫片/卷1/第0001章-卷1第01章-序章·幽蓝脉冲.md
    因此改为 rglob 搜索；但保持「根目录优先」的原有优先级，手动放在根目录的同名文件仍优先。
    """
    patterns = [
        f"第{chapter_num}章*.md",
        f"第{chapter_num:02d}章*.md",
        f"第{chapter_num:03d}章*.md",
        f"第{chapter_num:04d}章*.md",
    ]
    for pattern in patterns:
        # 根目录优先（插件原行为），其次任意深度的子目录。
        flat = sorted(outline_dir.glob(pattern))
        if flat:
            return flat[0]
        nested = sorted(outline_dir.rglob(pattern))
        if nested:
            return nested[0]
    return None


def _find_volume_outline_file(project_root: Path, chapter_num: int) -> Path | None:
    outline_dir = project_root / "大纲"
    volume_num = volume_num_for_chapter_from_state(project_root, chapter_num) or volume_num_for_chapter(chapter_num)
    candidates = [
        outline_dir / f"第{volume_num}卷-详细大纲.md",
        outline_dir / f"第{volume_num}卷 - 详细大纲.md",
        outline_dir / f"第{volume_num}卷 详细大纲.md",
    ]
    return next((path for path in candidates if path.exists()), None)


_GENERIC_HEADING_RE = re.compile(r"^(#{1,6})\s+\S.*$", re.MULTILINE)


def _chapter_section_by_heading(content: str, chapter_num: int) -> str | None:
    """按通用章标题扫描取整章段落（`### 第63章 满室星光（…）` 这类无冒号写法也认）。

    段落边界：下一个同级/更高级的章标题，或层级更浅的任意 Markdown 标题（如 `## 卷末备注`），
    都没有就到文件尾。
    """
    matches = list(_CHAPTER_HEADING_RE.finditer(content))
    for index, match in enumerate(matches):
        if _parse_chinese_chapter_num(match.group(2)) != chapter_num:
            continue
        level = len(match.group(1))
        end = len(content)
        for nxt in matches[index + 1 :]:
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        for heading in _GENERIC_HEADING_RE.finditer(content):
            if heading.start() <= match.end():
                continue
            if len(heading.group(1)) < level:
                end = min(end, heading.start())
                break
        return content[match.start() : end].strip()
    return None


def _extract_outline_section(content: str, chapter_num: int) -> str | None:
    # 先按通用章标题取段（真实书的写法多数不带冒号），取不到再回退老式冒号写法。
    section = _chapter_section_by_heading(content, chapter_num)
    if section is not None:
        return section
    patterns = [
        rf"###\s*第\s*{chapter_num}\s*章[：:]\s*(.+?)(?=###\s*第\s*\d+\s*章|##\s|$)",
        rf"###\s*第{chapter_num}章[：:]\s*(.+?)(?=###\s*第\d+章|##\s|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(0).strip()
    return None


def _parse_chinese_chapter_num(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in _CHINESE_NUMERAL_DIGITS:
        return _CHINESE_NUMERAL_DIGITS[text]
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_NUMERAL_DIGITS.get(left, 1 if not left else 0)
        ones = _CHINESE_NUMERAL_DIGITS.get(right, 0) if right else 0
        parsed = tens * 10 + ones
        return parsed or None
    parsed = 0
    for char in text:
        digit = _CHINESE_NUMERAL_DIGITS.get(char)
        if digit is None:
            return None
        parsed = parsed * 10 + digit
    return parsed or None


def _extract_directive_section(content: str, chapter_num: int) -> str | None:
    matches = list(_CHAPTER_HEADING_RE.finditer(content))
    for index, match in enumerate(matches):
        parsed = _parse_chinese_chapter_num(match.group(2))
        if parsed != chapter_num:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        return content[match.start():end].strip()
    return _extract_outline_section(content, chapter_num)


def load_chapter_outline(project_root: Path, chapter_num: int, max_chars: int | None = 1500) -> str:
    outline_dir = project_root / "大纲"

    split_outline = _find_split_outline_file(outline_dir, chapter_num)
    if split_outline is not None:
        return split_outline.read_text(encoding="utf-8")

    volume_outline = _find_volume_outline_file(project_root, chapter_num)
    if volume_outline is None:
        return f"⚠️ 大纲文件不存在：第 {chapter_num} 章"

    outline = _extract_outline_section(volume_outline.read_text(encoding="utf-8"), chapter_num)
    if outline is None:
        return f"⚠️ 未找到第 {chapter_num} 章的大纲"

    if max_chars and len(outline) > max_chars:
        return outline[:max_chars] + "\n...(已截断)"
    return outline

_PLOT_SECTION_FIELD_MAP = {
    "cbn": "cbn",
    "cpns": "cpns",
    "cen": "cen",
    "必须覆盖节点": "mandatory_nodes",
    "本章禁区": "prohibitions",
    "禁区": "prohibitions",
    "禁忌": "prohibitions",
}

_CHAPTER_HEADING_RE = re.compile(
    r"^(#{1,6})\s*第\s*([0-9零〇一二两三四五六七八九十]+)\s*章\b.*$",
    re.MULTILINE,
)

_CHINESE_NUMERAL_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_DIRECTIVE_FIELD_MAP = {
    "目标": "goal",
    "本章目标": "goal",
    "章目标": "goal",
    "阻力": "obstacles",
    "障碍": "obstacles",
    "代价": "cost",
    "时间锚点": "time_anchor",
    "时间": "time_anchor",
    "章内跨度": "chapter_span",
    "章节跨度": "chapter_span",
    "倒计时状态": "countdown",
    "倒计时": "countdown",
    "cbn": "cbn",
    "cpns": "cpns",
    "cen": "cen",
    "必须覆盖节点": "must_cover_nodes",
    "本章禁区": "forbidden_zones",
    "禁区": "forbidden_zones",
    "禁忌": "forbidden_zones",
    "章末未闭合问题": "chapter_end_open_question",
    "章末问题": "chapter_end_open_question",
    "钩子类型": "hook_type",
    "钩子强度": "hook_strength",
    "关键实体": "key_entities",
    "涉及实体": "key_entities",
    "strand": "strand",
    "反派层级": "antagonist_tier",
}

_DIRECTIVE_LIST_FIELDS = {"cpns", "must_cover_nodes", "forbidden_zones", "key_entities"}


_BOLD_LABEL_RE = re.compile(r"^\*\*\s*(.+?)\s*\*\*\s*([：:].*)$")


def _unbold_label(text: str) -> str:
    """`**CBN**：值` → `CBN：值`。只剥标签段的粗体，值里的粗体原样保留（那是作者给下游看的强调）。"""
    match = _BOLD_LABEL_RE.match(text)
    return (match.group(1) + match.group(2)) if match else text


def _match_field_label(cleaned: str, label: str) -> re.Match | None:
    """字段标签匹配的唯一口径：兼容 `- CBN：` 与 `- **CBN**：` 两种真实写法。"""
    return re.match(rf"^{re.escape(label)}\s*[：:]\s*(.*)$", _unbold_label(cleaned), re.IGNORECASE)


def _clean_plot_line(line: str) -> str:
    text = str(line or "").strip()
    text = re.sub(r"^[\-\*•]+\s*", "", text)
    text = re.sub(r"^\d+[\.、]\s*", "", text)
    return text.strip()


_FIELD_LABEL_SHAPE_RE = re.compile(r"^[^：:。；]{1,24}[：:]")
_BULLET_RE = re.compile(r"^[\-\*•]+\s*")


def _starts_new_field(raw_line: str, cleaned: str) -> bool:
    """判断这行是不是「另一个字段的标题行」（用于结束上一个字段的续行）。

    判据两条，缺一不可：
    1. 顶格——缩进行是上一字段的编号续行（如 `  1. 深夜独等：群里通知完七点`），不能当新字段；
    2. 剥掉列表符号与标签段粗体后，形如「短标签 + 冒号」。

    字段表里没有的标签（爽点/Strand/关键实体/本章变化…）据此终结上一字段，
    否则它们会被当作续行并进 `必须覆盖节点`／`本章禁区`，污染下游合同。
    """
    if raw_line[:1] in (" ", "\t"):
        return False
    if not _BULLET_RE.match(raw_line.strip()):
        return False
    return bool(_FIELD_LABEL_SHAPE_RE.match(_unbold_label(cleaned)))


def _append_plot_value(target: Dict[str, Any], field: str, value: str) -> None:
    value = _clean_plot_line(value)
    if not value:
        return

    if field in {"cpns", "mandatory_nodes", "prohibitions"}:
        target.setdefault(field, [])
        candidates = [value]
        if field in {"mandatory_nodes", "prohibitions"}:
            split_values = [part.strip() for part in re.split(r"[、,，；;|]+", value) if part.strip()]
            if split_values:
                candidates = split_values
        for item in candidates:
            if item not in target[field]:
                target[field].append(item)
        return

    if field not in target:
        target[field] = value


def _split_directive_values(value: str) -> list[str]:
    text = _clean_plot_line(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[、,，；;|]+", text) if part.strip()]


def _append_directive_value(target: Dict[str, Any], field: str, value: str) -> None:
    value = _clean_plot_line(value)
    if not value:
        return
    if field in _DIRECTIVE_LIST_FIELDS:
        target.setdefault(field, [])
        for item in _split_directive_values(value) or [value]:
            if item not in target[field]:
                target[field].append(item)
        return
    if field not in target:
        target[field] = value


def parse_chapter_plot_structure(outline_text: str) -> Dict[str, Any]:
    text = str(outline_text or "")
    if not text or text.startswith("⚠️"):
        return {}

    structure: Dict[str, Any] = {}
    current_field = ""

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            current_field = ""
            continue
        if re.match(r"^#{1,6}\s*第\s*\d+\s*章", stripped):
            current_field = ""
            continue

        cleaned = _clean_plot_line(stripped)
        matched_field = ""
        matched_value = ""
        for label, field in _PLOT_SECTION_FIELD_MAP.items():
            match = _match_field_label(cleaned, label)
            if match:
                matched_field = field
                matched_value = match.group(1).strip()
                break

        if matched_field:
            current_field = matched_field
            _append_plot_value(structure, matched_field, matched_value)
            continue

        if _starts_new_field(raw_line, cleaned):
            current_field = ""
            continue

        if current_field:
            _append_plot_value(structure, current_field, cleaned)

    cpns = structure.get("cpns") or []
    mandatory_nodes = structure.get("mandatory_nodes") or []
    prohibitions = structure.get("prohibitions") or []
    if not any([structure.get("cbn"), cpns, structure.get("cen"), mandatory_nodes, prohibitions]):
        return {}

    return {
        "cbn": str(structure.get("cbn") or "").strip(),
        "cpns": cpns,
        "cen": str(structure.get("cen") or "").strip(),
        "mandatory_nodes": mandatory_nodes,
        "prohibitions": prohibitions,
        "source": "chapter_outline",
    }


def load_chapter_plot_structure(project_root: Path, chapter_num: int) -> Dict[str, Any]:
    outline = load_chapter_outline(project_root, chapter_num, max_chars=None)
    return parse_chapter_plot_structure(outline)


def parse_chapter_execution_directive(outline_text: str) -> Dict[str, Any]:
    text = str(outline_text or "")
    if not text or text.startswith("⚠️"):
        return {}

    directive: Dict[str, Any] = {}
    current_field = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            current_field = ""
            continue
        if _CHAPTER_HEADING_RE.match(stripped):
            current_field = ""
            continue

        cleaned = _clean_plot_line(stripped)
        matched_field = ""
        matched_value = ""
        for label, field in _DIRECTIVE_FIELD_MAP.items():
            match = _match_field_label(cleaned, label)
            if match:
                matched_field = field
                matched_value = match.group(1).strip()
                break

        if matched_field:
            current_field = matched_field
            _append_directive_value(directive, matched_field, matched_value)
            continue
        if _starts_new_field(raw_line, cleaned):
            current_field = ""
            continue
        if current_field:
            _append_directive_value(directive, current_field, cleaned)

    plot_structure = parse_chapter_plot_structure(text)
    for source_key, target_key in (
        ("cbn", "cbn"),
        ("cpns", "cpns"),
        ("cen", "cen"),
        ("mandatory_nodes", "must_cover_nodes"),
        ("prohibitions", "forbidden_zones"),
    ):
        if plot_structure.get(source_key) and not directive.get(target_key):
            directive[target_key] = plot_structure[source_key]

    if directive:
        directive["source"] = "chapter_outline"
    return directive


def load_chapter_execution_directive(project_root: Path, chapter_num: int) -> Dict[str, Any]:
    outline_dir = project_root / "大纲"
    split_outline = _find_split_outline_file(outline_dir, chapter_num)
    if split_outline is not None:
        return parse_chapter_execution_directive(split_outline.read_text(encoding="utf-8"))

    volume_outline = _find_volume_outline_file(project_root, chapter_num)
    if volume_outline is None:
        return {}
    section = _extract_directive_section(volume_outline.read_text(encoding="utf-8"), chapter_num)
    if section is None:
        return {}
    return parse_chapter_execution_directive(section)
