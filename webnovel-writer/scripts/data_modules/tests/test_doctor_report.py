#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor 报告构建与格式化的验收（原先体检主链大量分支没测到）。

覆盖思路：一次 build_doctor_report 会串起文件/JSON/SQLite/投影日志/RAG/依赖/台面七段检查，
所以用「带库的书项目」和「缺件的书项目」两份样本把通过侧与降级侧都打到，
再单独测坏 JSON、坏库、格式化两种输出与 CLI 退出码。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from data_modules import doctor as dc

_STATE = {"project_info": {"title": "测试书", "genre": "现言"}, "progress": {"current_chapter": 3}}


def _book(tmp_path: Path, *, sub: str, with_dbs: bool = True, state: object = _STATE) -> Path:
    """造一个最小书项目：`.webnovel/state.json` + 可选的两个 sqlite 库。"""
    root = tmp_path / sub
    (root / ".webnovel").mkdir(parents=True)
    (root / ".webnovel" / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if with_dbs:
        for name, table in (("index.db", "chapters"), ("vectors.db", "vectors")):
            with sqlite3.connect(root / ".webnovel" / name) as conn:
                conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, payload TEXT)")
                conn.execute(f"INSERT INTO {table} (payload) VALUES ('x')")
                conn.commit()
    return root


def _by_id(report: dict) -> dict[str, dict]:
    return {item["id"]: item for item in report["checks"]}


def test_no_project_returns_blocker(tmp_path):
    report = dc.build_doctor_report(tmp_path / "不存在")
    checks = _by_id(report)

    assert report["ok"] is False
    assert checks["project.root"]["severity"] == "blocker"
    assert "webnovel-init" in checks["project.root"]["repair"]


def test_full_book_passes_sqlite_section(tmp_path):
    root = _book(tmp_path, sub="full", with_dbs=True)

    checks = _by_id(dc.build_doctor_report(root))

    assert checks["sqlite.index_db.chapters"]["status"] == dc.CHECK_OK
    assert checks["sqlite.index_db.chapters"]["actual"] == "rows=1"
    assert checks["sqlite.vector_db.vectors"]["status"] == dc.CHECK_OK


def test_missing_dbs_degrade_to_warning(tmp_path):
    root = _book(tmp_path, sub="bare", with_dbs=False)

    checks = _by_id(dc.build_doctor_report(root))

    assert checks["sqlite.index_db.chapters"]["status"] == dc.CHECK_WARNING
    assert checks["sqlite.index_db.chapters"]["actual"] == "missing"
    assert checks["sqlite.index_db.chapters"]["repair"]


def test_broken_state_json_is_reported_not_raised(tmp_path):
    root = _book(tmp_path, sub="broken", with_dbs=False, state={})
    (root / ".webnovel" / "state.json").write_text("{坏掉的 json", encoding="utf-8")

    report = dc.build_doctor_report(root)

    json_checks = [item for item in report["checks"] if item["id"].startswith("json.")]
    assert json_checks, [item["id"] for item in report["checks"]]
    assert any(item["status"] in {dc.CHECK_ERROR, dc.CHECK_WARNING} for item in json_checks)


def test_corrupt_sqlite_file_hits_error_branch(tmp_path):
    root = _book(tmp_path, sub="corrupt", with_dbs=False)
    (root / ".webnovel" / "index.db").write_text("这不是 sqlite 文件", encoding="utf-8")

    checks = _by_id(dc.build_doctor_report(root))

    assert checks["sqlite.index_db.chapters"]["status"] == dc.CHECK_WARNING
    assert checks["sqlite.index_db.chapters"]["actual"] not in {"missing", ""}, "损坏库要报 sqlite 错误，不能当成缺文件"


def test_deep_adds_dashboard_section(tmp_path):
    root = _book(tmp_path, sub="deep", with_dbs=True)

    shallow_ids = [item["id"] for item in dc.build_doctor_report(root, deep=False)["checks"]]
    deep_ids = [item["id"] for item in dc.build_doctor_report(root, deep=True)["checks"]]

    assert not any(i.startswith("dashboard.") for i in shallow_ids)
    assert any(i.startswith("dashboard.") for i in deep_ids), deep_ids


def test_preflight_failures_are_merged(tmp_path):
    root = _book(tmp_path, sub="pre", with_dbs=True)
    preflight = {"checks": [{"name": "project_root", "status": "failed", "detail": "missing"}]}

    ids = [item["id"] for item in dc.build_doctor_report(root, preflight_report=preflight)["checks"]]

    assert any("preflight" in i for i in ids), ids


def test_format_text_skips_ok_lines_and_json_roundtrips(tmp_path):
    report = dc.build_doctor_report(_book(tmp_path, sub="fmt", with_dbs=True), deep=True)

    text = dc.format_doctor_report(report, "text")
    payload = json.loads(dc.format_doctor_report(report, "json"))

    assert text.splitlines()[0].startswith(("OK ", "ERROR ")), text.splitlines()[0]
    assert "project_root:" in text
    for item in report["checks"]:
        if item["status"] == dc.CHECK_OK:
            # 按「<状态> <id>:」整行形态比对：id 之间互为前缀（file.dir..webnovel 与
            # file.dir..webnovel/backups），裸 substring 会误判
            assert f" {item['id']}:" not in text, item["id"]
    assert payload["schema_version"] == report["schema_version"]
    assert len(payload["checks"]) == len(report["checks"])


def test_story_runtime_health_failure_degrades_to_warning(tmp_path, monkeypatch):
    """主链健康摘要算不出来时，doctor 要出一条 warning 并继续跑完其它检查。"""
    root = _book(tmp_path, sub="health", with_dbs=True)

    def boom(*args, **kwargs):
        raise RuntimeError("合同读不了")

    monkeypatch.setattr(dc, "build_story_runtime_health", boom)

    report = dc.build_doctor_report(root)
    checks = _by_id(report)

    assert checks["story_runtime.health"]["status"] == dc.CHECK_WARNING
    assert "合同读不了" in checks["story_runtime.health"]["actual"]
    assert checks["sqlite.index_db.chapters"]["status"] == dc.CHECK_OK, "后面的检查不能被一个异常带崩"


def test_db_present_but_table_missing(tmp_path):
    """库在但表不在：要报 table_missing 侧的降级，而不是当成缺文件。"""
    root = _book(tmp_path, sub="notable", with_dbs=False)
    with sqlite3.connect(root / ".webnovel" / "index.db") as conn:
        conn.execute("CREATE TABLE other (id INTEGER)")
    (root / ".webnovel" / "vectors.db").write_bytes(b"")

    checks = _by_id(dc.build_doctor_report(root))

    assert checks["sqlite.index_db.chapters"]["status"] == dc.CHECK_WARNING
    assert checks["sqlite.index_db.chapters"]["actual"] == "table_missing"
    assert checks["sqlite.index_db.chapters"]["repair"]


def test_rel_falls_back_for_foreign_paths(tmp_path):
    """项目外的绝对路径算不出相对路径时，原样返回字符串（不能抛异常）。"""
    inside = tmp_path / "book" / ".webnovel" / "state.json"
    outside = Path("Z:/somewhere/else.json")

    assert dc._rel(tmp_path / "book", inside) == str(Path(".webnovel") / "state.json")
    assert dc._rel(tmp_path / "book", outside) == str(outside)


def test_main_cli_json_and_text(tmp_path, monkeypatch, capsys):
    root = _book(tmp_path, sub="cli", with_dbs=True)

    monkeypatch.setattr(sys, "argv", ["doctor", "--project-root", str(root), "--format", "json", "--deep"])
    with pytest.raises(SystemExit) as first:
        dc.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]
    assert first.value.code in {0, 1}

    monkeypatch.setattr(sys, "argv", ["doctor", "--project-root", str(root), "--format", "text"])
    with pytest.raises(SystemExit):
        dc.main()
    assert capsys.readouterr().out.strip()
