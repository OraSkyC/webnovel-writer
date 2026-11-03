#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目路由覆盖（project_route_override.json）的验收。

这是 fork 的内置行为：作者裁决过的题材口径必须压过插件的通用推理表，
否则《日记星辰》这类不在题材表里的书会被误路由成追妻火葬场/规则怪谈。
原先只有急救脚本重放结果，函数本身没有测试——这里把每个分支钉住：
包括「返回新对象，不污染调用方持有的 payload」这条 fork 硬修。
"""

from __future__ import annotations

import json
from pathlib import Path

from data_modules import story_contracts as sc

OVERRIDE = {
    "route": {"primary_genre": "终末纯爱日记", "sub_genre": "现言"},
    "master_constraints": {
        "core_tone": "甜虐交织、日记对抗遗忘",
        "project_hard_constraints": ["不写病名药名剂量"],
    },
    "anti_patterns": ["不许出现「最后一次」式点破", "不卖惨"],
    "override_policy": {"locked": ["route"]},
    "volume": {"tone": "克制"},
    "review": {"checklist": ["时间线"]},
}


def _project(tmp_path: Path, *, config: object = OVERRIDE, sub: str = "book") -> Path:
    root = tmp_path / sub
    ss = root / ".story-system"
    ss.mkdir(parents=True)
    (root / ".webnovel").mkdir()
    (root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    if config is not None:
        (ss / "project_route_override.json").write_text(
            json.dumps(config, ensure_ascii=False) if not isinstance(config, str) else config,
            encoding="utf-8",
        )
    return root


def _ss(root: Path) -> Path:
    return root / ".story-system"


# ---------------------------------------------------------------------------
# 单元：四种 kind 与降级
# ---------------------------------------------------------------------------

def test_no_override_file_returns_payload_unchanged(tmp_path):
    root = _project(tmp_path, config=None)
    payload = {"route": {"primary_genre": "追妻火葬场"}}

    out = sc._apply_project_route_override(_ss(root), "master", payload)

    assert out == payload


def test_broken_or_non_object_config_is_noop(tmp_path):
    broken = _project(tmp_path, config="{坏 JSON")
    assert sc._apply_project_route_override(_ss(broken), "master", {"a": 1}) == {"a": 1}

    array = _tmp_array_project(tmp_path)
    assert sc._apply_project_route_override(_ss(array), "master", {"a": 1}) == {"a": 1}


def _tmp_array_project(tmp_path: Path) -> Path:
    root = _project(tmp_path, config=None, sub="array")
    (_ss(root) / "project_route_override.json").write_text("[1, 2]", encoding="utf-8")
    return root


def test_master_overrides_only_declared_keys(tmp_path):
    root = _project(tmp_path)
    payload = {"route": {"primary_genre": "规则怪谈"}, "meta": {"keep": 1}, "master_constraints": {"x": 1}}

    out = sc._apply_project_route_override(_ss(root), "master", payload)

    assert out["route"] == OVERRIDE["route"]
    assert out["master_constraints"] == OVERRIDE["master_constraints"]
    assert out["override_policy"] == OVERRIDE["override_policy"]
    assert out["meta"] == {"keep": 1}, "未声明的键必须原样保留"


def test_override_does_not_mutate_caller_payload(tmp_path):
    """fork 硬修：返回新对象。原地改会让调用方在 persist 之后复用到被污染的数据。"""
    root = _project(tmp_path)
    payload = {"route": {"primary_genre": "规则怪谈"}, "master_constraints": {}}

    out = sc._apply_project_route_override(_ss(root), "master", payload)

    assert out is not payload
    assert payload["route"] == {"primary_genre": "规则怪谈"}, "调用方持有的对象不能被改写"


def test_anti_patterns_are_replaced_with_provenance(tmp_path):
    root = _project(tmp_path)

    out = sc._apply_project_route_override(_ss(root), "anti_patterns", [{"text": "插件自己的条目"}])

    assert [item["text"] for item in out] == OVERRIDE["anti_patterns"]
    assert out[0]["source_table"] == "project_override"
    assert out[0]["source_id"] == "QI-01"
    assert out[1]["source_id"] == "QI-02"


def test_anti_patterns_empty_list_falls_through(tmp_path):
    root = _project(tmp_path, config={"anti_patterns": []})
    original = [{"text": "插件条目"}]

    assert sc._apply_project_route_override(_ss(root), "anti_patterns", original) is original


def test_volume_and_review_merge_and_backfill(tmp_path):
    root = _project(tmp_path)

    volume = sc._apply_project_route_override(_ss(root), "volume", {"chapter_scope": "1-3"})
    review = sc._apply_project_route_override(_ss(root), "review", {})

    assert volume["tone"] == "克制"
    assert volume["anti_patterns"] == OVERRIDE["anti_patterns"]
    assert review["checklist"] == ["时间线"]
    assert review["system_constraints"] == [OVERRIDE["master_constraints"]["core_tone"]], "缺 system_constraints 时用基调兜底"


def test_review_keeps_explicit_system_constraints(tmp_path):
    root = _project(tmp_path)

    out = sc._apply_project_route_override(_ss(root), "review", {"system_constraints": ["作者自己写的"]})

    assert out["system_constraints"] == ["作者自己写的"]


def test_volume_section_already_carrying_anti_patterns_wins(tmp_path):
    root = _project(tmp_path, config={**OVERRIDE, "volume": {"anti_patterns": ["本节自带"]}})

    out = sc._apply_project_route_override(_ss(root), "volume", {})

    assert out["anti_patterns"] == ["本节自带"]


# ---------------------------------------------------------------------------
# 端到端：写盘后合同里就是作者口径
# ---------------------------------------------------------------------------

def test_persist_story_seed_applies_override(tmp_path):
    root = _project(tmp_path)

    sc.persist_story_seed(
        root,
        {"route": {"primary_genre": "规则怪谈"}, "master_constraints": {}},
        {"meta": {"chapter": 3}, "focus": "测试章"},
        [{"text": "插件条目", "source_table": "genre_rules", "source_id": "GR-001"}],
    )

    master = json.loads((root / ".story-system" / "MASTER_SETTING.json").read_text(encoding="utf-8"))
    anti = json.loads((root / ".story-system" / "anti_patterns.json").read_text(encoding="utf-8"))
    chapter = json.loads((root / ".story-system" / "chapters" / "chapter_003.json").read_text(encoding="utf-8"))

    assert master["route"] == OVERRIDE["route"]
    assert [item["text"] for item in anti] == OVERRIDE["anti_patterns"]
    assert chapter["meta"]["chapter"] == 3
    assert (root / ".story-system" / "MASTER_SETTING.md").is_file()


def test_persist_runtime_contracts_applies_override(tmp_path):
    root = _project(tmp_path)

    sc.persist_runtime_contracts(root, 3, {"chapter_scope": "1-9"}, {"checklist": []})

    volume = json.loads((root / ".story-system" / "volumes" / "volume_001.json").read_text(encoding="utf-8"))
    review = json.loads((root / ".story-system" / "reviews" / "chapter_003.review.json").read_text(encoding="utf-8"))

    assert volume["tone"] == "克制"
    assert review["system_constraints"] == [OVERRIDE["master_constraints"]["core_tone"]]
    assert review["anti_patterns"] == OVERRIDE["anti_patterns"]
