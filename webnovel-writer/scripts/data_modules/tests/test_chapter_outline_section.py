#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章纲正文抽取（load_chapter_outline / _extract_outline_section）的标题写法守卫。

起因：抽取原先只认 `### 第N章：标题`（冒号硬要求），而真实书稿写的是
`### 第63章 满室星光（完整章纲，2026-09-13 立）`、`### 第39章 烈日朝晖 ✅已完成`
这类无冒号标题 → 整本书的章纲静默读不到，plot_structure 连带为空。
本文件锁住「无冒号也要认」以及段落边界，防止再退回旧写法。
"""

from __future__ import annotations

import json

from chapter_outline_loader import load_chapter_outline, load_chapter_plot_structure


def _project(tmp_path, body: str, chapters_range: str = "1-100"):
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    (tmp_path / ".webnovel").mkdir()
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"volumes_planned": [{"volume": 1, "chapters_range": chapters_range}]}}),
        encoding="utf-8",
    )
    (outline_dir / "第1卷-详细大纲.md").write_text(body, encoding="utf-8")
    return tmp_path


def test_outline_found_without_colon(tmp_path):
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第63章 满室星光（完整章纲，2026-09-13 立；3.1 拆为 ch63/64 两章）",
                "- 目标：零点灯亮，他被接住",
                "- 必须覆盖节点：灯亮与那场哭；切蛋糕与换蛋糕",
                "- 本章禁区：不写病发、不送医",
                "",
            ]
        ),
    )

    outline = load_chapter_outline(root, 63, max_chars=None)

    assert "未找到" not in outline, outline
    assert "零点灯亮" in outline
    assert "不写病发" in outline


def test_outline_stops_at_next_chapter_heading(tmp_path):
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第39章 烈日朝晖 ✅已完成",
                "- 目标：A",
                "",
                "### 第40章 旧笔星笺 ✅已完成",
                "- 目标：B",
                "",
            ]
        ),
    )

    outline = load_chapter_outline(root, 39, max_chars=None)

    assert "- 目标：A" in outline
    assert "第40章" not in outline, outline
    assert "- 目标：B" not in outline


def test_outline_not_swallowing_shallower_section(tmp_path):
    """同级章段落不能被下一节的 `## 卷末备注` 吞进来。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第41章 试音",
                "- 目标：C",
                "",
                "## 卷末备注",
                "作废条目",
                "",
            ]
        ),
    )

    outline = load_chapter_outline(root, 41, max_chars=None)

    assert "- 目标：C" in outline
    assert "作废条目" not in outline


def test_legacy_colon_heading_still_works(tmp_path):
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第3章：试炼开局",
                "- 必须覆盖节点：发现陷阱",
                "",
                "### 第4章：试炼",
                "- 目标：D",
                "",
            ]
        ),
    )

    outline = load_chapter_outline(root, 3, max_chars=None)

    assert "发现陷阱" in outline
    assert "第4章" not in outline


def test_chinese_numeral_heading_resolves(tmp_path):
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第一章：债从天降",
                "- 目标：搞清楚借据条款的荒谬",
                "",
            ]
        ),
    )

    assert "借据条款" in load_chapter_outline(root, 1, max_chars=None)


def test_plot_structure_populated_from_no_colon_outline(tmp_path):
    """load_chapter_plot_structure 先取 outline；大纲读不到时它一直是空 dict。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第63章 满室星光",
                "- CBN：他以为没人记得",
                "- 必须覆盖节点：灯亮与那场哭",
                "- 本章禁区：不写病发、不送医",
                "",
            ]
        ),
    )

    structure = load_chapter_plot_structure(root, 63)

    assert structure.get("mandatory_nodes"), structure
    assert structure.get("prohibitions"), structure
