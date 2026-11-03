#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prose_lint 的规则级验收（fork 自带 11 条排版红线，原先零覆盖）。

每个规则都要「命中」和「不命中」两侧断言：只测命中侧的话，
规则被写坏成永远命中也测不出来。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from data_modules import prose_lint as pl


def _book(tmp_path: Path, files: dict[str, str], *, sub: str = "book") -> Path:
    """一个独立的书项目根。同一测试里要多套样本时传不同的 sub，避免文件互相污染。"""
    root = tmp_path / sub
    body = root / "正文"
    body.mkdir(parents=True)
    for name, text in files.items():
        path = body / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# 每段内容唯一：整段重复会被 repeat_para 判违规，基准文本必须先保证自身干净
CLEAN = "\n".join(
    [
        "# 第0003章-测试",
        "",
        "他把日记本合上，灯绳拉到底，屋里只剩窗外的光。",
        "“明天见。”她说得很轻，像在跟自己确认。",
        "",
        "第二天早晨，巷口的豆浆摊照旧冒着白气。",
        "摊主认得他们，多舀了一勺糖，没说什么。",
        "",
        "bus 站牌下有人翻报纸，风把页脚吹起来又放下。",
        "他数了数台阶，第七级那道裂缝还在原来的位置。",
    ]
)


def _codes(report: dict) -> set[str]:
    out = set()
    for result in report["results"]:
        for issue in result["issues"]:
            out.add(issue["rule"])
    return out


# ---------------------------------------------------------------------------
# 逐条规则
# ---------------------------------------------------------------------------

def test_clean_text_passes_all_rules(tmp_path):
    root = _book(tmp_path, {"第0003章-测试.md": CLEAN})

    report = pl.lint(root, min_chars=None, max_chars=None)

    assert report["ok"], report["results"][0]["issues"]
    assert report["scanned"] == 1


def test_half_quote_and_bold(tmp_path):
    root = _book(
        tmp_path,
        {"第0001章-a.md": "他说了\"随便\"。\n\n**她很重要**，他没有回头。\n"},
    )

    report = pl.lint(root, min_chars=None, max_chars=None)

    assert _codes(report) >= {"half_quote", "bold"}
    quote_hit = next(i for i in report["results"][0]["issues"] if i["rule"] == "half_quote")
    assert quote_hit["line"] == 1, "半角引号要报到行号，作者才能定点修"


def test_lone_dash_only_fullwidth(tmp_path):
    """`---` 是推荐的场景分隔符，不能判违规；只有全角破折号独立成段才算。"""
    bad = _book(tmp_path, {"第0001章-a.md": "第一段\n\n——\n\n第二段\n"}, sub="bad")
    good = _book(tmp_path, {"第0001章-a.md": "第一段\n\n---\n\n第二段\n"}, sub="good")

    assert "lone_dash" in _codes(pl.lint(bad, min_chars=None, max_chars=None))
    assert pl.lint(good, min_chars=None, max_chars=None)["ok"]


def test_quote_balance_both_kinds(tmp_path):
    double = _book(tmp_path, {"第0001章-a.md": "“没关上的门\n"}, sub="double")
    single = _book(tmp_path, {"第0001章-a.md": "‘没关上的引号\n"}, sub="single")
    ok = _book(tmp_path, {"第0001章-a.md": "“关好了。”‘这次也’配平了\n"}, sub="ok")

    report = pl.lint(double, min_chars=None, max_chars=None)
    assert "quote_balance" in _codes(report)
    detail = next(i["detail"] for i in report["results"][0]["issues"] if i["rule"] == "quote_balance")
    assert "1/0" in detail, detail
    assert "quote_balance" in _codes(pl.lint(single, min_chars=None, max_chars=None))
    assert "quote_balance" not in _codes(pl.lint(ok, min_chars=None, max_chars=None))


def test_mojibake_and_placeholder(tmp_path):
    root = _book(tmp_path, {"第0001章-a.md": "这里是乱码\ufffd\n\n待补：TBD\n"})

    report = pl.lint(root, min_chars=None, max_chars=None)
    codes = _codes(report)
    details = [i["detail"] for i in report["results"][0]["issues"]]

    assert {"mojibake", "placeholder"} <= codes
    assert "占位符 TBD" in details, details


def test_half_period_tail(tmp_path):
    """`abc.` 结尾放行（英文/数字），中文句后跟半角句点才算违规。"""
    bad = _book(tmp_path, {"第0001章-a.md": "他走了。\n然后离开.\n"}, sub="bad")
    ok = _book(tmp_path, {"第0001章-a.md": "版本 3.\n他走了。\n"}, sub="ok")

    assert "half_period" in _codes(pl.lint(bad, min_chars=None, max_chars=None))
    assert "half_period" not in _codes(pl.lint(ok, min_chars=None, max_chars=None))


def test_blank_runs(tmp_path):
    root = _book(tmp_path, {"第0001章-a.md": "第一段\n\n\n\n第二段\n"})

    assert "blank_runs" in _codes(pl.lint(root, min_chars=None, max_chars=None))


def test_repeat_para_and_its_two_exemptions(tmp_path):
    """重复段落：近距离重复算；隔很远是回环不算；短句对白不算；豁免清单命中不算。"""
    long_para = "他把那件洗得发白的外套叠好，压在枕头下面，动作很慢。"
    near = _book(tmp_path, {"第0001章-a.md": long_para + "\n\n过渡段。\n\n" + long_para + "\n"}, sub="near")
    # 填充段必须各不相同，否则填充段自己就成了近距离重复
    fillers = [f"填充段{i}，内容各不相同。" for i in range(30)]
    far = _book(tmp_path, {"第0001章-b.md": "\n\n".join([long_para] + fillers + [long_para]) + "\n"}, sub="far")
    dialog = _book(tmp_path, {"第0001章-c.md": "“你回来啦。”\n\n中间。\n\n“你回来啦。”\n"}, sub="dialog")

    assert "repeat_para" in _codes(pl.lint(near, min_chars=None, max_chars=None))
    assert "repeat_para" not in _codes(pl.lint(far, min_chars=None, max_chars=None))
    assert "repeat_para" not in _codes(pl.lint(dialog, min_chars=None, max_chars=None))

    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps([["第0001章-a", "洗得发白"]], ensure_ascii=False), encoding="utf-8")
    exempted = pl.lint(near, min_chars=None, max_chars=None, allow_file=str(allow))
    assert "repeat_para" not in _codes(exempted), exempted["results"][0]["issues"]


def test_repeat_broken_by_allow_file(tmp_path):
    """豁免清单坏 JSON / 形状不对时按空清单走，不能整条命令崩掉。"""
    long_para = "他把那件洗得发白的外套叠好，压在枕头下面，动作很慢。"
    root = _book(tmp_path, {"第0001章-a.md": long_para + "\n\n过渡。\n\n" + long_para + "\n"})
    bad = tmp_path / "bad.json"
    bad.write_text("{不是合法 JSON", encoding="utf-8")

    assert pl._load_allow(str(bad)) == []
    assert pl._load_allow(str(tmp_path / "不存在.json")) == []
    assert pl._load_allow(None) == []
    assert "repeat_para" in _codes(pl.lint(root, min_chars=None, max_chars=None, allow_file=str(bad)))


def test_word_count_range(tmp_path):
    root = _book(tmp_path, {"第0001章-a.md": "很短。\n"})

    report = pl.lint(root, min_chars=100, max_chars=200)

    issue = next(i for i in report["results"][0]["issues"] if i["rule"] == "word_count")
    assert "字数越界" in issue["detail"]
    assert pl.lint(root, min_chars=None, max_chars=None)["ok"]


def test_rule_selection_isolates(tmp_path):
    root = _book(tmp_path, {"第0001章-a.md": "**加粗**还有\"半角引号\"\n"})

    only_bold = pl.lint(root, min_chars=None, max_chars=None, rules={"bold"})

    assert _codes(only_bold) == {"bold"}


# ---------------------------------------------------------------------------
# 章节定位与布局
# ---------------------------------------------------------------------------

def test_find_chapters_volume_layout(tmp_path):
    root = _book(tmp_path, {})
    nested = root / "正文" / "第2卷-生日"
    nested.mkdir(parents=True)
    (nested / "第0063章-满室星光.md").write_text("正文。\n", encoding="utf-8")

    files = pl.find_chapters(root)

    assert [f.name for f in files] == ["第0063章-满室星光.md"]


def test_no_body_dir_returns_empty(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()

    assert pl.find_chapters(root) == []
    assert pl.lint(root, min_chars=None, max_chars=None)["ok"]


def test_chapter_lookup_by_global_number(tmp_path):
    root = _book(tmp_path, {"第0063章-满室星光.md": "正文。\n", "第0064章-生日二字.md": "正文。\n"})

    picked = pl.find_chapters(root, 63)

    assert [f.name for f in picked] == ["第0063章-满室星光.md"]


def test_chapter_lookup_falls_back_when_absent(tmp_path):
    """章号解析与文件名匹配都没有时返回空列表，不能报错。"""
    root = _book(tmp_path, {"第0063章-满室星光.md": "正文。\n"})

    assert pl.find_chapters(root, 999) == []
    assert pl.lint(root, chapter=999, min_chars=None, max_chars=None) == {
        "ok": True,
        "scanned": 0,
        "failed": 0,
        "by_rule": {},
        "results": [],
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _run_main(monkeypatch, capsys, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["prose_lint", *argv])
    try:
        pl.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_cli_json_and_text_formats(tmp_path, monkeypatch, capsys):
    root = _book(tmp_path, {"第0001章-a.md": "很短。\n\n**加粗**\n"})

    _run_main(monkeypatch, capsys, ["--project-root", str(root), "--format", "json", "--min-chars", "100", "--max-chars", "200"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] == 1
    assert payload["by_rule"]["word_count"] == 1

    _run_main(monkeypatch, capsys, ["--project-root", str(root), "--format", "text", "--min-chars", "100", "--max-chars", "200"])
    text = capsys.readouterr().out
    assert "ERR" in text and "字数越界" in text and "异常 1 章" in text


def test_cli_text_ok_line(tmp_path, monkeypatch, capsys):
    root = _book(tmp_path, {"第0001章-a.md": CLEAN})

    _run_main(monkeypatch, capsys, ["--project-root", str(root), "--format", "text", "--min-chars", "1", "--max-chars", "99999"])

    assert "全部通过" in capsys.readouterr().out


def test_cli_unknown_rule_exits_cleanly(tmp_path, monkeypatch, capsys):
    """未知规则必须走 parser.error（退出码 2），不能抛字符串 SystemExit：
    上层 _run_data_module 会把退出码 int()，字符串会崩成 ValueError 栈。"""
    root = _book(tmp_path, {"第0001章-a.md": "正文。\n"})

    rc = _run_main(monkeypatch, capsys, ["--project-root", str(root), "--rule", "不存在的规则"])

    assert rc == 2
    assert "未知规则" in capsys.readouterr().err


def test_cli_chapter_and_allow_file(tmp_path, monkeypatch, capsys):
    long_para = "他把那件洗得发白的外套叠好，压在枕头下面，动作很慢。"
    root = _book(tmp_path, {
        "第0001章-a.md": long_para + "\n\n过渡。\n\n" + long_para + "\n",
        "第0002章-b.md": "只有这一章是干净的正文。\n",
    })

    _run_main(monkeypatch, capsys, ["--project-root", str(root), "--chapter", "2", "--format", "json", "--min-chars", "1", "--max-chars", "9999"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["scanned"] == 1
    assert payload["results"][0]["name"] == "第0002章-b.md"

    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps([["第0001章-a", "洗得发白"]], ensure_ascii=False), encoding="utf-8")
    _run_main(monkeypatch, capsys, ["--project-root", str(root), "--allow-file", str(allow), "--format", "json", "--min-chars", "1", "--max-chars", "9999"])
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_norm_len_counts_effective_chars():
    """字数口径与项目脚本一致：空白与中文标点不计。"""
    assert pl._norm_len("你好，世界。") == 4
    assert pl._norm_len("a b\tc\n") == 3
