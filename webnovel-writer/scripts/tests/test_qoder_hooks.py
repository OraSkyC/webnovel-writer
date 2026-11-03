#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qoder 钩子注册与本插件护栏行为的验收。

覆盖三层：
1. settings.json 合并：只动带本插件 name 标记的条目，外来键/外来钩子原样保留；重复执行不堆叠
2. --check / --remove 的语义（漂移非零退出，摘除后外来条目仍在）
3. guard_runtime_write.py 的真实拦截判定（合成 stdin 载荷走子进程，含 Claude/Qoder 两种字段名）
   与 WEBNOVEL_HOOK_DEBUG 探针落盘（探针失败不能影响判定）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import install_qoder_hooks as installer

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
GUARD = PLUGIN_ROOT / "hooks" / "guard_runtime_write.py"


def _run_guard(payload: dict, *, env: dict | None = None) -> subprocess.CompletedProcess:
    run_env = dict(os.environ)
    run_env.pop("WEBNOVEL_DISABLE_RUNTIME_GUARD_HOOK", None)
    run_env.pop("WEBNOVEL_HOOK_DEBUG", None)
    run_env.pop("WEBNOVEL_HOOK_PROBE_DIR", None)
    run_env["PYTHONUTF8"] = "1"
    run_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-X", "utf-8", str(GUARD)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=run_env,
        timeout=30,
    )


def _settings(tmp_path: Path, extra: dict | None = None) -> Path:
    path = tmp_path / "settings.json"
    payload = {
        "enabledPlugins": {"webnovel-writer@webnovel-writer-marketplace": True},
        "mcpServers": {"demo": {"command": "node", "args": ["server.cjs"]}},
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. 合并语义
# ---------------------------------------------------------------------------

def test_install_creates_entries_and_keeps_foreign_keys(tmp_path):
    settings = _settings(tmp_path)
    entries = installer.build_entries(python_executable=sys.executable, plugin_root=PLUGIN_ROOT)
    merged = installer.merge_entries(installer.load_settings(settings), entries)

    assert merged["enabledPlugins"] == {"webnovel-writer@webnovel-writer-marketplace": True}
    assert merged["mcpServers"]["demo"]["args"] == ["server.cjs"]
    assert [hook["name"] for group in merged["hooks"]["SessionStart"] for hook in group["hooks"]] == [
        "webnovel-writer-session-start"
    ]
    names = [hook["name"] for group in merged["hooks"]["PreToolUse"] for hook in group["hooks"]]
    assert names == ["webnovel-writer-guard-write", "webnovel-writer-guard-bash"]
    matchers = [group["matcher"] for group in merged["hooks"]["PreToolUse"]]
    assert matchers == [installer.WRITE_MATCHER, "Bash"]
    # 命令必须指向真实存在的脚本，且用绝对路径（用户级钩子没有插件根变量）
    for group in merged["hooks"]["PreToolUse"]:
        command = group["hooks"][0]["command"]
        assert f'{GUARD.as_posix()}"' in command, command


def test_install_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    entries = installer.build_entries(python_executable=sys.executable, plugin_root=PLUGIN_ROOT)
    once = installer.merge_entries(installer.load_settings(settings), entries)
    twice = installer.merge_entries(once, entries)

    assert twice == once, "重复安装不得堆叠条目"
    assert len(twice["hooks"]["PreToolUse"]) == 2


def test_foreign_hook_groups_survive(tmp_path):
    settings = _settings(
        tmp_path,
        extra={
            "hooks": {
                "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "name": "other", "command": "echo hi"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}],
            }
        },
    )
    entries = installer.build_entries(python_executable=sys.executable, plugin_root=PLUGIN_ROOT)
    merged = installer.merge_entries(installer.load_settings(settings), entries)

    starters = [hook["name"] for group in merged["hooks"]["SessionStart"] for hook in group["hooks"]]
    assert starters == ["other", "webnovel-writer-session-start"], starters
    assert merged["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo stop"


def test_remove_strips_only_ours(tmp_path):
    settings = _settings(
        tmp_path,
        extra={"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}},
    )
    entries = installer.build_entries(python_executable=sys.executable, plugin_root=PLUGIN_ROOT)
    merged = installer.merge_entries(installer.load_settings(settings), entries)
    cleaned = installer.strip_entries(merged)

    assert "PreToolUse" not in cleaned["hooks"]
    assert "SessionStart" not in cleaned["hooks"]
    assert cleaned["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo stop"
    assert cleaned["mcpServers"]["demo"]["command"] == "node"


def test_write_settings_backs_up_original(tmp_path):
    settings = _settings(tmp_path)
    original = settings.read_text(encoding="utf-8")

    backup = installer.write_settings(settings, {"enabledPlugins": {"x": True}})

    assert backup is not None and backup.is_file()
    assert backup.read_text(encoding="utf-8") == original
    assert json.loads(settings.read_text(encoding="utf-8"))["enabledPlugins"] == {"x": True}


def test_check_cli_reports_in_sync_and_drift(tmp_path, capsys):
    settings = _settings(tmp_path)
    assert installer.main(["--settings", str(settings), "--plugin-root", str(PLUGIN_ROOT)]) == 0
    capsys.readouterr()

    assert installer.main(["--settings", str(settings), "--plugin-root", str(PLUGIN_ROOT), "--check"]) == 0
    capsys.readouterr()

    # 模拟「插件搬家了但 settings 里还留着旧路径」：必须报漂移，不能继续静默拦截失败
    payload = json.loads(settings.read_text(encoding="utf-8"))
    for groups in payload["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                hook["command"] = hook["command"].replace(PLUGIN_ROOT.as_posix(), "D:/old/place/webnovel-writer")
    settings.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert installer.main(["--settings", str(settings), "--plugin-root", str(PLUGIN_ROOT), "--check"]) == 1
    assert "漂移" in capsys.readouterr().err


def test_install_cli_refuses_missing_hook_scripts(tmp_path, capsys):
    settings = _settings(tmp_path)

    rc = installer.main(["--settings", str(settings), "--plugin-root", str(tmp_path / "moved-plugin")])

    assert rc == 2, "脚本不在场时不能装出一个每次工具调用都报错的钩子"
    assert "找不到钩子脚本" in capsys.readouterr().err


def test_remove_cli_is_safe_when_nothing_installed(tmp_path, capsys):
    settings = _settings(tmp_path)

    assert installer.main(["--settings", str(settings), "--remove"]) == 0
    assert "未安装" in capsys.readouterr().out
    assert settings.read_text(encoding="utf-8").count("hooks") == 0, "不该凭空造出 hooks 键"


def test_load_settings_rejects_non_object(tmp_path):
    bad = tmp_path / "settings.json"
    bad.write_text("[]", encoding="utf-8")

    try:
        installer.load_settings(bad)
    except SystemExit as exc:
        assert "拒绝改写" in str(exc)
    else:
        raise AssertionError("顶层不是对象时必须拒绝，否则会把用户配置写坏")


# ---------------------------------------------------------------------------
# 2. 护栏真实判定（子进程 + 合成 stdin）
# ---------------------------------------------------------------------------

def test_guard_blocks_direct_edit_of_protected_file(tmp_path):
    proc = _run_guard(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "book" / ".webnovel" / "index.db")},
        }
    )

    assert proc.returncode == 2, proc.stderr
    decision = json.loads(proc.stderr.strip().splitlines()[-1])
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_guard_allows_unrelated_edit(tmp_path):
    proc = _run_guard(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "book" / "正文" / "第0063章.md")},
        }
    )

    assert proc.returncode == 0, proc.stderr


def test_guard_blocks_bypass_command(tmp_path):
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "python chapter_commit.py --project-root book"},
        }
    )

    assert proc.returncode == 2, proc.stderr


def test_guard_allows_runtime_commit(tmp_path):
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {"command": 'python -X utf8 "scripts/webnovel.py" --project-root book chapter-commit --chapter 63'},
        }
    )

    assert proc.returncode == 0, proc.stderr


def test_guard_accepts_camel_case_payload_shape(tmp_path):
    """宿主字段名口径未完全实测前，两种形状都必须拦住（不能因为读不到就放行）。"""
    proc = _run_guard(
        {
            "toolName": "Write",
            "toolInput": {"path": "D:/books/demo/.story-system/commits/chapter_063.commit.json"},
        }
    )

    assert proc.returncode == 2, proc.stderr


def test_guard_disable_env_short_circuits(tmp_path):
    proc = _run_guard(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "D:/books/demo/.webnovel/vectors.db"},
        },
        env={"WEBNOVEL_DISABLE_RUNTIME_GUARD_HOOK": "1"},
    )

    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# 3. 探针（实测宿主字段名用）
# ---------------------------------------------------------------------------

def test_probe_records_payload_when_debug_on(tmp_path):
    probe_dir = tmp_path / "probe"
    proc = _run_guard(
        {"tool_name": "Write", "tool_input": {"file_path": "D:/books/demo/.webnovel/index.db"}},
        env={"WEBNOVEL_HOOK_DEBUG": "1", "WEBNOVEL_HOOK_PROBE_DIR": str(probe_dir)},
    )

    log = probe_dir / "hook_probe.jsonl"
    assert proc.returncode == 2, "探针不得改变拦截判定"
    assert log.is_file(), list(probe_dir.iterdir())
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["hook"] == "guard_runtime_write"
    assert record["keys"] == ["tool_input", "tool_name"]


def test_probe_stays_silent_by_default(tmp_path):
    probe_dir = tmp_path / "probe"
    _run_guard(
        {"tool_name": "Write", "tool_input": {"file_path": "D:/books/demo/.webnovel/index.db"}},
        env={"WEBNOVEL_HOOK_PROBE_DIR": str(probe_dir)},
    )

    assert not probe_dir.exists(), "没开调试就写探针等于给每次工具调用加副作用"


# ---------------------------------------------------------------------------
# 4. 开场钩子：非书项目必须完全静默，待裁计数只算真待裁
# ---------------------------------------------------------------------------

SESSION_START = PLUGIN_ROOT / "hooks" / "session_start.py"


def _run_session_start(cwd: Path, *, env: dict | None = None) -> subprocess.CompletedProcess:
    run_env = dict(os.environ)
    for name in ("WEBNOVEL_WORKSPACE_ROOT", "CLAUDE_PROJECT_DIR", "WEBNOVEL_DISABLE_SESSION_STATUS_HOOK"):
        run_env.pop(name, None)
    run_env["PYTHONUTF8"] = "1"
    run_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-X", "utf-8", str(SESSION_START)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=run_env,
        cwd=str(cwd),
        timeout=60,
    )


def test_session_start_is_silent_outside_a_book(tmp_path):
    """非书目录（写代码、做 PPT 的会话）不能被动辄 25 秒的项目状态噪音污染开场。"""
    outside = tmp_path / "not_a_book"
    outside.mkdir()
    home = tmp_path / "empty-agent-home"
    (home / "webnovel-writer").mkdir(parents=True)

    proc = _run_session_start(
        outside,
        env={"WEBNOVEL_AGENT_HOME": str(home), "WEBNOVEL_HOOK_PROBE_DIR": str(home)},
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout


def test_session_start_prints_status_inside_a_book():
    """真实书项目（日记星辰）要有开场读数，且不得出现「未命名项目」。"""
    book = Path("D:/OraSkyC/Novel/日记星辰")
    if not (book / ".webnovel" / "state.json").is_file():
        import pytest

        pytest.skip("本机没有这本书")

    proc = _run_session_start(book)

    assert proc.returncode == 0, proc.stderr
    assert "project_root" in proc.stdout, proc.stdout
    assert "未命名项目" not in proc.stdout


def test_pending_ruling_count_only_counts_rejected_or_pending(tmp_path):
    sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
    import importlib

    module = importlib.import_module("session_start")
    commits = tmp_path / ".story-system" / "commits"
    commits.mkdir(parents=True)

    def write(name: str, meta_status: str, pending: list) -> None:
        (commits / name).write_text(
            json.dumps(
                {"meta": {"chapter": 1, "status": meta_status}, "disambiguation_result": {"pending": pending}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    write("chapter_001.commit.json", "accepted", [])
    write("chapter_002.commit.json", "rejected", [])
    write("chapter_003.commit.json", "accepted", [{"id": "P-ch3-a", "title": "候选解不足"}])

    assert module._pending_ruling_count(commits) == 2
    assert module._pending_ruling_count(tmp_path / "缺失目录") == 0


def test_pending_ruling_count_survives_broken_json(tmp_path):
    sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
    import importlib

    module = importlib.import_module("session_start")
    commits = tmp_path / "commits"
    commits.mkdir()
    (commits / "chapter_001.commit.json").write_text("{不是合法 json", encoding="utf-8")
    (commits / "chapter_002.commit.json").write_text(
        json.dumps({"meta": {"status": "rejected"}, "disambiguation_result": {}}), encoding="utf-8"
    )

    assert module._pending_ruling_count(commits) == 1
