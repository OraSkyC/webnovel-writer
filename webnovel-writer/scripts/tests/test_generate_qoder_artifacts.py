#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qoder 产物生成器的静态守卫。

只跑纯函数（build_artifacts 不写盘），确认三件事：
1. 产物里不残留任何 Claude 专有表达（漏改会让技能在 Qoder 里静默失败）
2. frontmatter 形状是 Qoder 认的（技能只有 name/description；agent 只有那五个键）
3. 产物里出现的 CLI 子命令都在真源注册过（防 fork 新增子命令后又漏登记）
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = SCRIPTS_DIR.parent


def _ensure_scripts_on_path() -> None:
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="module")
def gen():
    _ensure_scripts_on_path()
    import generate_qoder_artifacts as module

    return module


@pytest.fixture(scope="module")
def artifacts(gen):
    return gen.build_artifacts(gen.PLUGIN_ROOT)


def test_builds_eight_skills_and_four_agents(artifacts):
    skills = sorted(rel for rel in artifacts if rel.startswith("skills/"))
    agents = sorted(rel for rel in artifacts if rel.startswith("agents/webnovel/"))
    assert len(skills) == 8, skills
    assert agents == [
        "agents/webnovel/webnovel-context-agent.md",
        "agents/webnovel/webnovel-data-agent.md",
        "agents/webnovel/webnovel-deconstruction-agent.md",
        "agents/webnovel/webnovel-reviewer.md",
    ], agents


def test_no_claude_host_tokens_left(artifacts):
    _ensure_scripts_on_path()
    import generate_qoder_artifacts as module

    forbidden = module.forbidden_tokens(module.resolve_plugin_config(module.PLUGIN_ROOT))
    dirty = {
        rel: [token for token in forbidden if token in text]
        for rel, text in artifacts.items()
    }
    dirty = {rel: hits for rel, hits in dirty.items() if hits}
    assert not dirty, f"产物残留宿主痕迹：{dirty}"


def test_skill_frontmatter_only_uses_qoder_keys(artifacts):
    allowed = {"name", "description"}
    dropped = {"allowed-tools", "argument-hint", "version"}
    for rel, text in artifacts.items():
        if not rel.startswith("skills/"):
            continue
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        assert m, f"{rel}: 缺 frontmatter"
        keys = {k.group(1) for k in re.finditer(r"^([A-Za-z_][\w-]*):", m.group(1), re.MULTILINE)}
        assert keys <= allowed, f"{rel}: Qoder 不认的键 {keys - allowed}"
        assert not (keys & dropped), f"{rel}: 应已删掉的 Claude 键 {keys & dropped}"
        assert "description" in keys, f"{rel}: 描述是技能触发依据，不能丢"


def test_skill_root_resolves_from_plugin_root_not_relative(artifacts):
    """技能装在用户目录，../../ 指不到插件根；跨技能资料必须走 WEBNOVEL_PLUGIN_ROOT。"""
    for rel, text in artifacts.items():
        if not rel.startswith("skills/"):
            continue
        assert "${SKILL_ROOT}/../.." not in text, rel
        assert "export WEBNOVEL_PLUGIN_ROOT" in text, f"{rel}: 缺宿主引导"
        assert "export SCRIPTS_DIR=" in text, f"{rel}: 缺脚本根"


def test_agent_frontmatter_shape(artifacts):
    for rel, text in artifacts.items():
        if not rel.startswith("agents/"):
            continue
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        assert m, f"{rel}: 缺 frontmatter"
        keys = {k.group(1) for k in re.finditer(r"^([A-Za-z_][\w-]*):", m.group(1), re.MULTILINE)}
        assert {"name", "description", "permissionMode"} <= keys, f"{rel}: 键不足 {keys}"
        assert not (keys & {"tools", "model", "color"}), f"{rel}: Claude 专有键残留 {keys}"
        name = re.search(r"^name: (\S+)$", m.group(1), re.MULTILINE)
        assert name and name.group(1) == Path(rel).stem, f"{rel}: name 与文件名不一致"


def test_agent_calls_use_canonical_names(artifacts):
    """技能里调用 agent 的名字必须是 Qoder 注册的规范名，不能是插件前缀或裸名。"""
    canonical = {
        "webnovel-context-agent",
        "webnovel-reviewer",
        "webnovel-data-agent",
        "webnovel-deconstruction-agent",
    }
    text = artifacts["skills/webnovel-write/SKILL.md"] + artifacts["skills/webnovel-review/SKILL.md"]
    assert "调用 `reviewer`" not in text
    assert "调用 `context-agent`" not in text
    hits = {name for name in canonical if name in text}
    assert {"webnovel-reviewer", "webnovel-context-agent"} <= hits, hits


def test_cli_subcommands_in_artifacts_are_registered(artifacts):
    source = (SCRIPTS_DIR / "data_modules" / "webnovel.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'\bsub\.add_parser\(\s*"([a-z][\w-]*)"', source))
    assert registered, "CLI 真源派生失败"
    offenders = []
    for rel, text in artifacts.items():
        for cmd in re.findall(r'webnovel\.py["\s]+--project-root\s+[^\s]+\s+([a-z][\w-]*)', text):
            if cmd not in registered:
                offenders.append(f"{rel}:{cmd}")
    assert not offenders, f"产物引用了未注册子命令：{offenders}"


# ---------------------------------------------------------------------------
# 参数化：兄弟插件（cadt-ruling）走同一条转换规则
# ---------------------------------------------------------------------------

CADT_ROOT = PLUGIN_ROOT.parent.parent / "cadt-ruling"


def test_builtin_config_keeps_installed_names():
    """本插件的名字与变量名不许漂：宿主里已按这些名字装了技能与 agent。"""
    _ensure_scripts_on_path()
    import generate_qoder_artifacts as module

    config = module.resolve_plugin_config(module.PLUGIN_ROOT)

    assert config["env_var"] == "WEBNOVEL_PLUGIN_ROOT"
    assert config["agent_prefix"] == "webnovel"
    assert config["manifest_rel"] == "webnovel-writer/qoder_artifacts.json"
    assert config["agent_name_map"], "本插件必须沿用显式规范名映射"


def test_sibling_plugin_config_is_derived(tmp_path):
    _ensure_scripts_on_path()
    import generate_qoder_artifacts as module

    sibling = tmp_path / "cadt-ruling"
    sibling.mkdir()
    config = module.resolve_plugin_config(sibling)

    assert config["env_var"] == "CADT_RULING_PLUGIN_ROOT"
    assert config["agent_prefix"] == "cadt-ruling"
    assert config["manifest_rel"] == "cadt-ruling/qoder_artifacts.json"


def test_agent_canonical_name_rule(tmp_path):
    """规范名以插件自带的 name-map 为准；没有 map 才套默认规则（已带前缀保持，裸名加前缀）。"""
    _ensure_scripts_on_path()
    import generate_qoder_artifacts as module

    # 无 map：走默认规则
    bare = module.resolve_plugin_config(tmp_path / "cadt-ruling")
    assert module._agent_canonical_name(bare, "analyzer.md") == "cadt-ruling-analyzer"
    assert module._agent_canonical_name(bare, "cadt-ruling-analyzer.md") == "cadt-ruling-analyzer"
    assert module._agent_canonical_name(bare, "ruling-executor.md") == "cadt-ruling-ruling-executor"

    # 有 map：插件自己定的名字胜出（避免叠词，也不让转换脚本替作者命名）
    plugin = tmp_path / "named" / "cadt-ruling"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "agents" / "name-map.json").write_text(
        json.dumps({"ruling-executor.md": "cadt-ruling-executor"}, ensure_ascii=False), encoding="utf-8"
    )
    config = module.resolve_plugin_config(plugin)
    assert module._agent_canonical_name(config, "ruling-executor.md") == "cadt-ruling-executor"


def test_agent_name_map_json_rejects_bad_shape(tmp_path):
    _ensure_scripts_on_path()
    import generate_qoder_artifacts as module

    plugin = tmp_path / "bad" / "cadt-ruling"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "agents" / "name-map.json").write_text('["不是映射"]', encoding="utf-8")

    try:
        module.resolve_plugin_config(plugin)
    except SystemExit as exc:
        assert "映射" in str(exc)
    else:
        raise AssertionError("name-map 形状非法时必须拒绝，否则产物名会静默错掉")


def test_cadt_ruling_artifacts_build(tmp_path):
    """真·兄弟插件：9 技能 + 2 agent 能生成，宿主痕迹清零，调用名换成规范名。"""
    _ensure_scripts_on_path()
    import generate_qoder_artifacts as module

    if not (CADT_ROOT / "skills").is_dir():
        pytest.skip("本机没有 cadt-ruling 仓库")

    artifacts = module.build_artifacts(CADT_ROOT)
    skills = sorted(rel for rel in artifacts if rel.startswith("skills/"))
    agents = sorted(rel for rel in artifacts if rel.startswith("agents/cadt-ruling/"))

    assert len(skills) == 9, skills
    assert agents == [
        "agents/cadt-ruling/cadt-ruling-analyzer.md",
        "agents/cadt-ruling/cadt-ruling-executor.md",
    ], agents

    forbidden = module.forbidden_tokens(module.resolve_plugin_config(CADT_ROOT))
    for rel, text in artifacts.items():
        assert not [token for token in forbidden if token in text], rel

    write = artifacts["skills/cadt-ruling/SKILL.md"]
    assert "CADT_RULING_PLUGIN_ROOT:=" in write, "缺宿主引导"
    assert "cadt-ruling-executor" in artifacts["skills/cadt-ruling-execute/SKILL.md"], "调用名必须已换成规范名"
