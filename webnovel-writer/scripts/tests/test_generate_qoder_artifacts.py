#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qoder 产物生成器的静态守卫。

只跑纯函数（build_artifacts 不写盘），确认三件事：
1. 产物里不残留任何 Claude 专有表达（漏改会让技能在 Qoder 里静默失败）
2. frontmatter 形状是 Qoder 认的（技能只有 name/description；agent 只有那五个键）
3. 产物里出现的 CLI 子命令都在真源注册过（防 fork 新增子命令后又漏登记）
"""

from __future__ import annotations

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

    dirty = {
        rel: [token for token in module.FORBIDDEN_AFTER_TRANSFORM if token in text]
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
