#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章纲正文抽取（load_chapter_outline / _extract_outline_section）的标题写法守卫。

起因：抽取原先只认 `### 第N章：标题`（冒号硬要求），而真实书稿写的是
`### 第63章 满室星光（完整章纲，2026-09-13 立）`、`### 第39章 烈日朝晖 ✅已完成`
这类无冒号标题 → 整本书的章纲静默读不到，plot_structure 连带为空。
本文件锁住「无冒号也要认」以及段落边界，防止再退回旧写法。
第二层坑一并锁住：字段标签写成粗体（`- **CBN**：`）时，plot_structure 与 execution_directive
不能返回空——章纲读到了但结构化字段全空，等于主链仍然拿不到节点与禁区。
第三层坑：字段表外的顶格标签行（`- **爽点**：…`）必须终结上一字段的续行，
但缩进编号条目（`  1. 深夜独等：…`）里的冒号不算标签行，否则 CPN 会被拆断。
"""

from __future__ import annotations

import json

from chapter_outline_loader import (
    load_chapter_execution_directive,
    load_chapter_outline,
    load_chapter_plot_structure,
)


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


def test_plot_structure_reads_bold_labels(tmp_path):
    """真实章纲把标签写成粗体 `- **CBN**：`，结构化字段不能因此为空。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第63章 满室星光（完整章纲）",
                "- **CBN**：沈星辰 | 零点灯亮 | 他被接住",
                "- **必须覆盖节点**：CBN + CPN3 + CPN6",
                "- **本章禁区**：不写病发、不送医",
                "",
            ]
        ),
    )

    structure = load_chapter_plot_structure(root, 63)

    assert structure.get("cbn") == "沈星辰 | 零点灯亮 | 他被接住", structure
    # 节点按顿号/逗号/分号拆，`+` 不是分隔符（章纲用它表示「CBN 加若干 CPN」的组合）
    assert structure.get("mandatory_nodes") == ["CBN + CPN3 + CPN6"], structure
    assert structure.get("prohibitions"), structure


def test_directive_reads_bold_labels_and_keeps_value_emphasis(tmp_path):
    """执行指令同理；值内部的粗体是作者留的强调，不能被剥掉。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第63章 满室星光",
                "- **目标**：零点惊喜",
                "- **时间锚点**：2059/11/15 22:30→11/16 0:30（**章首不重标日期行**）",
                "- **倒计时状态**：D-134（不写明数字）",
                "",
            ]
        ),
    )

    directive = load_chapter_execution_directive(root, 63)

    assert directive.get("goal") == "零点惊喜", directive
    assert "**章首不重标日期行**" in directive.get("time_anchor", ""), directive
    assert "D-134" in directive.get("countdown", ""), directive


def test_unmapped_field_line_ends_previous_field(tmp_path):
    """字段表外的顶格标签行（爽点/Strand/本章变化…）不能被当作续行并进上一字段。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第63章 满室星光",
                "- **必须覆盖节点**：CBN + CPN3 + CPN6",
                "- **爽点**：零点反转；跑调生日歌",
                "- **Strand**：主线情感线",
                "- **本章变化**：十八年第一次被正式庆祝",
                "- **本章禁区**：不写病发、不送医",
                "",
            ]
        ),
    )

    structure = load_chapter_plot_structure(root, 63)

    assert structure.get("mandatory_nodes") == ["CBN + CPN3 + CPN6"], structure
    assert not any("爽点" in item for item in structure.get("mandatory_nodes") or []), structure
    assert structure.get("prohibitions") == ["不写病发", "不送医"], structure


def test_indented_numbered_items_stay_with_their_field(tmp_path):
    """缩进编号条目是上一字段的续行：里面的冒号不能触发「新字段」判定。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第63章 满室星光",
                "- **CPNs**：",
                "  1. 深夜独等：群里通知完七点就安静了",
                "  2. 零点：秒针跨过十二，什么也没发生",
                "- **CEN**：原来有人当真了",
                "",
            ]
        ),
    )

    structure = load_chapter_plot_structure(root, 63)

    assert structure.get("cpns") == [
        "深夜独等：群里通知完七点就安静了",
        "零点：秒针跨过十二，什么也没发生",
    ], structure
    assert structure.get("cen") == "原来有人当真了", structure


def test_directive_unmapped_field_does_not_pollute_list(tmp_path):
    """directive 走的是同一判定：字段表外的标签行不并入 must_cover_nodes。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第63章 满室星光",
                "- **必须覆盖节点**：CBN + CPN5",
                "- **爽点**：她把最大一块蛋糕换给他",
                "",
            ]
        ),
    )

    directive = load_chapter_execution_directive(root, 63)

    assert directive.get("must_cover_nodes") == ["CBN + CPN5"], directive
    assert not any("爽点" in item for item in directive.get("must_cover_nodes") or []), directive


def test_jinji_alias_populates_prohibitions(tmp_path):
    """本书章纲用「禁忌」而非「本章禁区」；两条别名都要落到同一个字段。"""
    root = _project(
        tmp_path,
        "\n".join(
            [
                "### 第64章 生日二字",
                "- **CBN**：三件礼物与两本日记",
                "- **禁忌**：",
                "  1. 不写她察觉病情",
                "  2. 不出现「最后一次」字样",
                "",
            ]
        ),
    )

    structure = load_chapter_plot_structure(root, 64)
    directive = load_chapter_execution_directive(root, 64)

    assert structure.get("prohibitions") == [
        "不写她察觉病情",
        "不出现「最后一次」字样",
    ], structure
    assert directive.get("forbidden_zones") == [
        "不写她察觉病情",
        "不出现「最后一次」字样",
    ], directive


# ---------------------------------------------------------------------------
# 卷内重编号方言（时空纪元卷10 起）与空壳垫片回落
# ---------------------------------------------------------------------------

_V10_BODY = "\n".join(
    [
        "### 卷内 1（全局 634）· 五十年",
        "",
        "- **时间锚点**：2077-02-04 08:00 ｜ 章内跨度：约1小时",
        "- **目标**：五十周年纪念确立「守夜人」基调",
        "- **CBN**：恩格瓦拉 | 独自站上断崖 | 上一代离场",
        "- **禁忌**：",
        "  1. 不写哭",
        "  2. 不写庆祝",
        "",
        "### 卷内 2（全局 635）· 三份报告",
        "",
        "- **目标**：三份报告各自埋雷",
        "",
    ]
)


def _v10_project(tmp_path, shim_text: str | None = None):
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    (tmp_path / ".webnovel").mkdir()
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"volumes_planned": [{"volume": 10, "chapters_range": "634-725"}]}}),
        encoding="utf-8",
    )
    (outline_dir / "第10卷-详细大纲.md").write_text(_V10_BODY, encoding="utf-8")
    if shim_text is not None:
        shim_dir = outline_dir / "垫片" / "卷10"
        shim_dir.mkdir(parents=True)
        (shim_dir / "第0634章-卷10第01章-五十年.md").write_text(shim_text, encoding="utf-8")
    return tmp_path


def test_volume_local_global_heading_resolves(tmp_path):
    """`### 卷内 1（全局 634）· 标题` 必须按全局号取到整段。"""
    root = _v10_project(tmp_path)

    outline = load_chapter_outline(root, 634, max_chars=None)

    assert "守夜人" in outline, outline
    assert "三份报告" not in outline, outline


def test_empty_shim_falls_back_to_volume_outline(tmp_path):
    """空壳垫片（只有标题＋生成注释）不算章纲，要回落到卷文件。"""
    root = _v10_project(
        tmp_path,
        "# 第0634章-卷10第01章-五十年\n\n<!-- 机器生成垫片，勿手改。编号真源＝.webnovel/state.json -->\n",
    )

    outline = load_chapter_outline(root, 634, max_chars=None)
    directive = load_chapter_execution_directive(root, 634)
    structure = load_chapter_plot_structure(root, 634)

    assert "守夜人" in outline, outline
    assert directive.get("goal") == "五十周年纪念确立「守夜人」基调", directive
    assert structure.get("prohibitions") == ["不写哭", "不写庆祝"], structure


def test_shim_with_body_still_wins(tmp_path):
    """垫片带正文时保持原有优先级：编号真源是它，不能被卷文件盖掉。"""
    root = _v10_project(tmp_path, "# 第0634章-卷10第01章-五十年\n\n- **目标**：垫片里的目标\n")

    assert "垫片里的目标" in load_chapter_outline(root, 634, max_chars=None)


def test_local_number_does_not_cross_match_global(tmp_path):
    """查卷内号 1 不能命中全局 634 那段（避免跨卷串号）。"""
    root = _v10_project(tmp_path)

    assert "守夜人" not in load_chapter_outline(root, 1, max_chars=None)
