#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写章断点账本（run_ledger）与续跑计划的验收。

这块决定「中断后重跑同一章」是补跑失败步骤还是从头再来，判错方向会重写已完成的正文，
所以通过侧与降级侧都要断言：
- 正文与上次记录一致 → skip；被手改过 → 要作者确认
- 章纲比正文新 → 也要确认
- commit 已 accepted → 直接跳过起草，不再依赖正文指纹
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from data_modules import run_ledger as rl


def _project(tmp_path: Path, *, chapter: int = 7, body: str = "正文内容。\n") -> Path:
    root = tmp_path / f"book{chapter}"
    (root / "正文").mkdir(parents=True)
    (root / ".webnovel").mkdir(parents=True)
    (root / "正文" / f"第{chapter:04d}章-测试.md").write_text(body, encoding="utf-8")
    return root


def _chapter_file(root: Path, chapter: int) -> Path:
    return root / "正文" / f"第{chapter:04d}章-测试.md"


# ---------------------------------------------------------------------------
# 账本读写
# ---------------------------------------------------------------------------

def test_load_ledger_normalizes_missing_and_stale(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()

    assert rl.load_ledger(root) == {"schema_version": rl.SCHEMA_VERSION, "write": {}}

    stale = tmp_path / "stale"
    rl.ledger_path(stale).parent.mkdir(parents=True)
    rl.ledger_path(stale).write_text(json.dumps({"schema_version": "other/v0", "write": {"x": 1}}), encoding="utf-8")
    assert rl.load_ledger(stale) == {"schema_version": rl.SCHEMA_VERSION, "write": {}}

    broken = tmp_path / "broken"
    rl.ledger_path(broken).parent.mkdir(parents=True)
    rl.ledger_path(broken).write_text("{坏 JSON", encoding="utf-8")
    assert rl.load_ledger(broken) == {"schema_version": rl.SCHEMA_VERSION, "write": {}}

    not_a_dict = tmp_path / "weird"
    rl.ledger_path(not_a_dict).parent.mkdir(parents=True)
    rl.ledger_path(not_a_dict).write_text(
        json.dumps({"schema_version": rl.SCHEMA_VERSION, "write": []}), encoding="utf-8"
    )
    assert rl.load_ledger(not_a_dict)["write"] == {}


def test_file_signature_missing_and_present(tmp_path):
    gone = tmp_path / "不存在.md"
    here = tmp_path / "在.md"
    here.write_text("内容", encoding="utf-8")

    assert rl.file_signature(gone) == {"path": str(gone), "exists": False}
    sig = rl.file_signature(here)
    assert sig["exists"] is True and len(sig["sha256"]) == 64


def test_record_write_step_rejects_unknown_step(tmp_path):
    root = _project(tmp_path)

    with pytest.raises(ValueError, match="unknown write step"):
        rl.record_write_step(root, chapter=7, step="猜一步", status="completed")


def test_record_then_resume_skips_trusted_draft(tmp_path):
    root = _project(tmp_path)
    chapter_file = _chapter_file(root, 7)

    rl.record_write_step(
        root,
        chapter=7,
        step="draft",
        status="completed",
        outputs={"chapter_file": chapter_file},
    )
    plan = rl.build_write_resume_plan(root, chapter=7)

    draft = next(item for item in plan["steps"] if item["step"] == "draft")
    assert draft["action"] == "skip", plan
    assert not plan.get("needs_user_confirmation")


def test_hand_edited_chapter_requires_confirmation(tmp_path):
    root = _project(tmp_path)
    chapter_file = _chapter_file(root, 7)
    rl.record_write_step(root, chapter=7, step="draft", status="completed", outputs={"chapter_file": chapter_file})

    chapter_file.write_text("作者手改过的正文。\n", encoding="utf-8")
    plan = rl.build_write_resume_plan(root, chapter=7)

    codes = [item["code"] for item in plan.get("needs_user_confirmation") or []]
    assert "chapter_file_changed" in codes, plan
    assert next(i for i in plan["steps"] if i["step"] == "draft")["action"] == "run"


def test_newer_outline_than_draft_requires_confirmation(tmp_path):
    root = _project(tmp_path)
    chapter_file = _chapter_file(root, 7)
    rl.record_write_step(root, chapter=7, step="draft", status="completed", outputs={"chapter_file": chapter_file})

    contract_dir = root / ".story-system" / "chapters"
    contract_dir.mkdir(parents=True)
    contract = contract_dir / "chapter_007.json"
    contract.write_text("{}", encoding="utf-8")
    future = chapter_file.stat().st_mtime_ns + 5 * 60 * 10**9
    os.utime(contract, ns=(future, future))

    plan = rl.build_write_resume_plan(root, chapter=7)

    codes = [item["code"] for item in plan.get("needs_user_confirmation") or []]
    assert "outline_newer_than_draft" in codes, plan


def test_accepted_commit_short_circuits_draft(tmp_path):
    root = _project(tmp_path)
    commits = root / ".story-system" / "commits"
    commits.mkdir(parents=True)
    (commits / "chapter_007.commit.json").write_text(
        json.dumps({"meta": {"chapter": 7, "status": "accepted"}}, ensure_ascii=False), encoding="utf-8"
    )

    plan = rl.build_write_resume_plan(root, chapter=7)

    assert next(i for i in plan["steps"] if i["step"] == "draft")["action"] == "skip"
    assert plan.get("chapter") == 7


def test_corrupt_run_entry_degrades_to_full_rerun(tmp_path):
    """账本里该章的条目不是对象时，要按「无记录」处理而不是崩掉。"""
    root = _project(tmp_path)
    rl.save_ledger(root, {"schema_version": rl.SCHEMA_VERSION, "write": {rl._chapter_key(7): "坏了"}})

    plan = rl.build_write_resume_plan(root, chapter=7)

    assert next(i for i in plan["steps"] if i["step"] == "draft")["action"] == "run"


# ---------------------------------------------------------------------------
# 格式化与参数解析
# ---------------------------------------------------------------------------

def test_format_plan_json_and_text(tmp_path):
    root = _project(tmp_path)
    plan = rl.build_write_resume_plan(root, chapter=7)

    payload = json.loads(rl.format_resume_plan(plan, "json"))
    text = rl.format_resume_plan(plan, "text")

    assert payload["chapter"] == 7
    assert text.splitlines()[0].startswith("resume_from:")
    assert "- draft:" in text


def test_path_and_list_parsers(tmp_path):
    """入参一律走 JSON 字符串；形状不对要报错而不是静默当成空。"""
    root = _project(tmp_path)
    chapter_file = _chapter_file(root, 7)

    assert rl._parse_path_map("") == {}
    assert rl._parse_path_map(json.dumps({"chapter_file": str(chapter_file)})) == {"chapter_file": str(chapter_file)}
    assert rl._parse_string_list("") == []
    assert rl._parse_string_list(json.dumps(["a", "b", 3])) == ["a", "b", "3"]

    with pytest.raises(ValueError, match="不是合法 JSON"):
        rl._parse_path_map("chapter_file=坏")
    with pytest.raises(ValueError, match="必须是 JSON object"):
        rl._parse_path_map("[]")
    with pytest.raises(ValueError, match="必须是 JSON list"):
        rl._parse_string_list("{}")

    entry = rl.record_write_step(
        root,
        chapter=7,
        step="review",
        status="completed",
        problems=["一处待定"],
        auto_handled=[],
        inputs={"chapter_file": chapter_file},
        duration_ms=12,
    )
    assert entry["problems"] == ["一处待定"]
    assert entry["inputs"]["chapter_file"]["exists"] is True
    assert entry["duration_ms"] == 12


def test_signature_trust_helpers(tmp_path):
    """指纹判定的边界：缺字段、exists=False、路径不一致都不能算可信。"""
    root = _project(tmp_path)
    chapter_file = _chapter_file(root, 7)
    good = rl.file_signature(chapter_file)

    assert rl._same_signature(good, good) is True
    assert rl._same_signature(None, good) is False
    assert rl._same_signature({"exists": True, "sha256": "其它"}, good) is False

    entry = {"outputs": {"chapter_file": good}, "inputs": {"chapter_file": good}}
    assert rl._trusted_output(entry, "chapter_file") is True
    assert rl._trusted_output(entry, "不存在的东西") is False
    assert rl._trusted_output(None, "chapter_file") is False
    assert rl._trusted_input(entry, "chapter_file", chapter_file) is True
    assert rl._trusted_input(entry, "chapter_file", None) is False
    assert rl._trusted_input(entry, "chapter_file", tmp_path / "另一个文件.md") is False


def test_main_cli_records_and_resumes(tmp_path, monkeypatch, capsys):
    """CLI 形状：`--project-root` 在前，子命令的参数用 JSON 字符串。"""
    root = _project(tmp_path)
    chapter_file = _chapter_file(root, 7)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ledger",
            "--project-root",
            str(root),
            "record-write-step",
            "--chapter",
            "7",
            "--step",
            "draft",
            "--status",
            "completed",
            "--outputs-json",
            json.dumps({"chapter_file": str(chapter_file)}),
        ],
    )
    rl.main()
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["step"] == "draft"
    assert recorded["outputs"]["chapter_file"]["exists"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_ledger", "--project-root", str(root), "write-resume", "--chapter", "7", "--format", "text"],
    )
    rl.main()
    out = capsys.readouterr().out
    assert "resume_from:" in out, out
    assert "- draft: skip" in out, out


def test_main_cli_rejects_unknown_step(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_ledger", "--project-root", str(root), "record-write-step", "--chapter", "7", "--step", "猜一步", "--status", "completed"],
    )

    with pytest.raises((SystemExit, ValueError)):
        rl.main()
