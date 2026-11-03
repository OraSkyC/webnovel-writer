#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宿主兼容层（host_compat）与项目定位的跨宿主行为。

覆盖三类硬约定：
1. 用户级家目录：显式覆盖 > 已存过 webnovel 数据的家 > 第一个存在的家 > ~/.claude
2. 当前书指针：`.qoder/` 与 `.claude/` 都要能读，写入要同步两边，宿主目录不存在时不创建
3. 项目内 references 副本：任一宿主目录里的既有文件都要能找到
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


@pytest.fixture(autouse=True)
def isolate_host_environment(monkeypatch, tmp_path):
    for name in (
        "WEBNOVEL_AGENT_HOME",
        "WEBNOVEL_CLAUDE_HOME",
        "CLAUDE_HOME",
        "WEBNOVEL_PROJECT_ROOT",
        "WEBNOVEL_WORKSPACE_ROOT",
        "CLAUDE_PROJECT_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def _make_book_root(root: Path) -> Path:
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# 1. 宿主家目录选择
# ---------------------------------------------------------------------------

def test_agent_home_honours_explicit_override(monkeypatch, tmp_path):
    _ensure_scripts_on_path()
    import host_compat

    override = tmp_path / "custom-home"
    monkeypatch.setenv("WEBNOVEL_AGENT_HOME", str(override))
    assert host_compat.agent_home() == override.resolve()
    assert host_compat.registry_path() == override.resolve() / "webnovel-writer" / "workspaces.json"
    assert host_compat.global_dotenv_path() == override.resolve() / "webnovel-writer" / ".env"


def test_agent_home_keeps_legacy_claude_home_override(monkeypatch, tmp_path):
    """旧变量名仍然生效：Claude 侧的既有配置不用改。"""
    _ensure_scripts_on_path()
    import host_compat

    legacy = tmp_path / "legacy-claude"
    monkeypatch.setenv("WEBNOVEL_CLAUDE_HOME", str(legacy))
    assert host_compat.agent_home() == legacy.resolve()


def test_agent_home_prefers_home_that_already_holds_webnovel_data(monkeypatch, tmp_path):
    _ensure_scripts_on_path()
    import host_compat

    qoder_home = tmp_path / "qoder-home"
    claude_home = tmp_path / "claude-home"
    (claude_home / "webnovel-writer").mkdir(parents=True)
    qoder_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(host_compat, "USER_HOME_DIRS", (qoder_home.name, claude_home.name))

    assert host_compat.agent_home() == claude_home


def test_agent_home_falls_back_to_first_existing_host_dir(monkeypatch, tmp_path):
    _ensure_scripts_on_path()
    import host_compat

    qoder_home = tmp_path / ".qoder-cn"
    claude_home = tmp_path / ".claude"
    qoder_home.mkdir(parents=True)
    claude_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert host_compat.agent_home() == qoder_home


def test_existing_global_dotenv_paths_collects_every_host(monkeypatch, tmp_path):
    """两个宿主各放一份 API key 时都要被读到（写入序决定优先级，不覆盖已有变量）。"""
    _ensure_scripts_on_path()
    import host_compat

    qoder_home = tmp_path / ".qoder-cn" / "webnovel-writer"
    claude_home = tmp_path / ".claude" / "webnovel-writer"
    qoder_home.mkdir(parents=True)
    claude_home.mkdir(parents=True)
    (qoder_home / ".env").write_text("EMBED_MODEL=from-qoder\n", encoding="utf-8")
    (claude_home / ".env").write_text("EMBED_MODEL=from-claude\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    paths = host_compat.existing_global_dotenv_paths()
    assert [p.parent.parent.name for p in paths] == [".qoder-cn", ".claude"]


# ---------------------------------------------------------------------------
# 2. 当前书指针
# ---------------------------------------------------------------------------

def test_pointer_read_paths_covers_both_hosts_per_level(tmp_path):
    _ensure_scripts_on_path()
    import host_compat

    nested = tmp_path / "workspace" / "book"
    nested.mkdir(parents=True)
    paths = list(host_compat.pointer_read_paths(nested, stop_at=tmp_path / "workspace"))

    assert nested.parent / ".qoder" / ".webnovel-current-project" in paths
    assert nested.parent / ".claude" / ".webnovel-current-project" in paths
    # stop_at 之上不再继续向上找
    assert tmp_path / ".claude" / ".webnovel-current-project" not in paths


def test_pointer_write_paths_skips_missing_host_dirs(tmp_path):
    _ensure_scripts_on_path()
    import host_compat

    ws = tmp_path / "workspace"
    (ws / ".claude").mkdir(parents=True)
    assert host_compat.pointer_write_paths(ws) == [ws / ".claude" / ".webnovel-current-project"]

    (ws / ".qoder").mkdir()
    assert host_compat.pointer_write_paths(ws) == [ws / ".qoder" / ".webnovel-current-project",
                                                  ws / ".claude" / ".webnovel-current-project"]

    assert host_compat.pointer_write_paths(tmp_path / "nowhere") == []


def test_write_pointer_syncs_both_host_dirs_and_never_creates_one(tmp_path, monkeypatch):
    """已有两个宿主目录时两边指针都更新；只有 .claude 时不会凭空造出 .qoder。"""
    _ensure_scripts_on_path()
    import host_compat
    from project_locator import write_current_project_pointer

    empty_home = tmp_path / "empty-home"
    monkeypatch.setenv("WEBNOVEL_AGENT_HOME", str(empty_home))

    ws = tmp_path / "workspace"
    both = ws / "both"
    both_book = _make_book_root(both / "book")
    (both / ".claude").mkdir()
    (both / ".qoder").mkdir()
    write_current_project_pointer(both_book, workspace_root=both)
    assert (both / ".qoder" / ".webnovel-current-project").read_text(encoding="utf-8") == str(both_book)
    assert (both / ".claude" / ".webnovel-current-project").read_text(encoding="utf-8") == str(both_book)

    only_claude = ws / "only-claude"
    only_book = _make_book_root(only_claude / "book")
    (only_claude / ".claude").mkdir()
    write_current_project_pointer(only_book, workspace_root=only_claude)
    assert (only_claude / ".claude" / ".webnovel-current-project").is_file()
    assert not (only_claude / ".qoder").exists()
    assert host_compat  # 兼容层本身不参与写入，仅确认导入路径可用


def test_resolve_project_root_follows_qoder_pointer(tmp_path, monkeypatch):
    """Qoder 工作区（只有 .qoder/）里跑 CLI 时，也能从指针找到当前书。"""
    _ensure_scripts_on_path()
    from project_locator import resolve_project_root

    empty_home = tmp_path / "empty-home"
    monkeypatch.setenv("WEBNOVEL_AGENT_HOME", str(empty_home))

    ws = tmp_path / "workspace"
    ws.mkdir()
    book = _make_book_root(ws / "book-a")
    (ws / ".qoder").mkdir()
    (ws / ".qoder" / ".webnovel-current-project").write_text(str(book), encoding="utf-8")

    (ws / "scripts" / "sub").mkdir(parents=True)
    assert resolve_project_root(cwd=ws / "scripts" / "sub") == book.resolve()


def test_find_workspace_root_accepts_either_host_dir(tmp_path):
    _ensure_scripts_on_path()
    from project_locator import _find_workspace_root_with_host_dir

    ws = tmp_path / "workspace"
    deep = ws / "book" / "正文"
    deep.mkdir(parents=True)
    (ws / ".qoder").mkdir()

    assert _find_workspace_root_with_host_dir(deep) == ws


# ---------------------------------------------------------------------------
# 3. 项目内 references 副本
# ---------------------------------------------------------------------------

def test_host_reference_path_prefers_existing_copy(tmp_path):
    _ensure_scripts_on_path()
    import host_compat

    claude_copy = tmp_path / ".claude" / "references" / "genre-profiles.md"
    claude_copy.parent.mkdir(parents=True)
    claude_copy.write_text("### 现言\n", encoding="utf-8")

    assert host_compat.host_reference_path(tmp_path, "genre-profiles.md") == claude_copy


def test_host_reference_path_prefers_qoder_when_both_exist(tmp_path):
    _ensure_scripts_on_path()
    import host_compat

    for dot in (".qoder", ".claude"):
        target = tmp_path / dot / "references" / "genre-profiles.md"
        target.parent.mkdir(parents=True)
        target.write_text("x", encoding="utf-8")

    assert host_compat.host_reference_path(tmp_path, "genre-profiles.md") == (
        tmp_path / ".qoder" / "references" / "genre-profiles.md"
    )


def test_host_reference_path_returns_candidate_when_missing(tmp_path):
    """都没副本时返回首选路径（调用方仍按 exists() 判断，行为与改造前一致）。"""
    _ensure_scripts_on_path()
    import host_compat

    path = host_compat.host_reference_path(tmp_path, "genre-profiles.md")
    assert not path.exists()
    assert path.parent.name == "references"
