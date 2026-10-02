#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正文排版红线检查（prose-lint）。

为什么有它：插件对正文排版只有三份**文字**规范（references/writing/typesetting.md、
references/polish-guide.md、references/style-adapter.md），**没有任何一行代码读过正文做判定**
——三道 write_gate 里也没有一条正文正则。把确定性的检查留给「模型自觉遵守的 prompt」，
等于把质量门从确定性降级成概率性。这个模块把那 11 条规则变成可执行的。

规则清单（每条都可单独关掉）：
    half_quote      半角引号 " '
    bold            叙事加粗 **…**
    lone_dash       独立成段破折号（只判全角；`---` 不算，见下）
    quote_balance   双/单引号配平
    mojibake        乱码 �
    placeholder     占位符 TBD/XXX/TODO/PLACEHOLDER
    half_period     半角句点结尾（排除 `abc.` 这类缩写/编号）
    blank_runs      连续空行（≥3 个空行）
    repeat_para     重复段落（同段两次出现且间隔 < 25 段；短台词豁免）
    word_count      字数越界（**按 --min-chars/--max-chars 参数化**）

与项目脚本 `工具/红线校验.py` 的两处刻意差异（合并进插件时定下的规矩，别改回去）：
    ① 字数区间参数化。插件 `references/style-adapter.md:18` 写「常规章节默认 2000-2500 字」，
       原脚本硬编 2500-4500 —— 不参数化就会和插件自己的规范互相打脸。
       默认取插件口径（2000-2500），项目侧用 `--min-chars 2500 --max-chars 4500` 传自己的。
    ② 重复段落的豁免清单外置。原脚本把「第0006章/第25章 + 某句」硬编在源码里，那是项目私产，
       搬进通用包会被别人的书误用；现在从 `--allow-file` 读（JSON 数组）。

关于 `---`：**只判全角破折号独立成段**（`——`/`—`/`——。`/`——，`），`---` 不算违规。
这是项目 2026-09 之后定下的规则（memory MEMORY.md:259），别按字面直觉「修」成也判 `---`。

用法：
    python -X utf8 -m data_modules.prose_lint --project-root <根> [--chapter N] [--format json|text]
    python -X utf8 -m data_modules.prose_lint --project-root <根> --min-chars 2500 --max-chars 4500
    python -X utf8 -m data_modules.prose_lint --project-root <根> --allow-file prose-lint-allow.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

if __package__ in {None, ""}:  # pragma: no cover - direct script entry
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

# 字数统计口径：与项目脚本一致——去掉空白与中文标点后数「有效字符」
STRIP = ' \t\n，。、；：！？“”‘’（）()｜|/—－-·《》〈〉「」『』…'
_STRIP_RE = re.compile('[' + re.escape(STRIP) + ']')

ALL_RULES = (
    "half_quote", "bold", "lone_dash", "quote_balance", "mojibake",
    "placeholder", "half_period", "blank_runs", "repeat_para", "word_count",
)

PLACEHOLDERS = ("TBD", "XXX", "TODO", "PLACEHOLDER")
# 独立成段的破折号：只全角。`---` 是 typesetting.md 推荐的场景分隔符，不算违规。
LONE_DASHES = ("——", "—", "——。", "——，")
BLANK_RUN_RE = re.compile(r"\n{4,}")
BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
# 半角句点结尾：`abc.` / `3.` 这类不算
HALF_PERIOD_TAIL_RE = re.compile(r"[A-Za-z0-9]\.$")
REPEAT_MIN_LEN = 12          # 段落长度门槛（短句重复是正常对白）
REPEAT_MAX_GAP = 25          # 两次出现的段距上限（隔很远的重复是回环，不是 bug）
SHORT_DIALOG_MAX = 26        # 纯引号包裹且短于这个长度的，算正常对白回环


def _norm_len(text: str) -> int:
    return len(_STRIP_RE.sub("", text))


def _load_allow(path: str | None) -> List[List[str]]:
    """读豁免清单：[["文件名片段", "段落片段"], …]，命中即不算重复段落 bug。"""
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for row in data if isinstance(data, list) else []:
        if isinstance(row, (list, tuple)) and len(row) == 2:
            out.append([str(row[0]), str(row[1])])
    return out


def _resolve_by_chapter(root: Path, chapter: int) -> List[Path]:
    """按**全局章号**定位正文文件。

    🔴 不能只按文件名里的数字找：卷内编号的书里，文件名是卷内号
       （卷2 从第0001章起，全局号却是 86+），实测 `--chapter 408` 一个都找不到。
       这里走插件自己的 chapter_paths.find_chapter_file（它含卷内重编号与垫片查找），
       找不到再退回文件名匹配。
    """
    try:
        from chapter_paths import find_chapter_file
    except ImportError:  # pragma: no cover
        try:
            from scripts.chapter_paths import find_chapter_file
        except ImportError:
            find_chapter_file = None
    if find_chapter_file is not None:
        hit = find_chapter_file(root, chapter)
        if hit:
            return [Path(hit)]
    picked = []
    for f in sorted((root / "正文").rglob("第*章*.md")):
        m = re.search(r"第0*(\d+)章", f.name)
        if m and int(m.group(1)) == chapter:
            picked.append(f)
    return picked


def find_chapters(root: Path, chapter: int | None = None) -> List[Path]:
    """找正文文件。卷目录布局（正文/第N卷-xxx/第NNNN章-*.md）与平铺布局都认。"""
    body = root / "正文"
    if not body.is_dir():
        return []
    if chapter is not None:
        return _resolve_by_chapter(root, chapter)
    return sorted(body.rglob("第*章*.md"))


def check_file(path: Path, *, min_chars: int | None, max_chars: int | None,
               rules: set[str], allow: List[List[str]]) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    paras = [x for x in lines if x.strip()]
    issues: List[Dict[str, Any]] = []

    def add(code: str, detail: str, line: int | None = None) -> None:
        issues.append({"rule": code, "detail": detail, "line": line,
                       "severity": "high", "file": path.name})

    if "half_quote" in rules:
        for i, l in enumerate(lines, 1):
            if '"' in l or "'" in l:
                add("half_quote", "半角引号", i)

    if "bold" in rules and BOLD_RE.search(text):
        add("bold", "叙事加粗（**…**）")

    if "lone_dash" in rules:
        for i, l in enumerate(paras, 1):
            if l.strip() in LONE_DASHES:
                add("lone_dash", "独立成段破折号", i)

    if "quote_balance" in rules:
        if text.count("“") != text.count("”"):
            add("quote_balance", "引号不配平 %d/%d" % (text.count("“"), text.count("”")))
        if text.count("‘") != text.count("’"):
            add("quote_balance", "单引号不配平 %d/%d" % (text.count("‘"), text.count("’")))

    if "mojibake" in rules and "�" in text:
        add("mojibake", "乱码字符")

    if "placeholder" in rules:
        for k in PLACEHOLDERS:
            if k in text:
                add("placeholder", "占位符 %s" % k)

    if "half_period" in rules:
        for i, l in enumerate(paras, 1):
            if l.rstrip().endswith(".") and not HALF_PERIOD_TAIL_RE.search(l):
                add("half_period", "半角句点结尾", i)

    if "blank_runs" in rules and BLANK_RUN_RE.search(text):
        add("blank_runs", "连续空行（≥3）")

    if "repeat_para" in rules:
        pos: Dict[str, List[int]] = {}
        for idx, x in enumerate(paras):
            pos.setdefault(x, []).append(idx)
        for para, ix in pos.items():          # ix = 出现位置列表（不是次数）
            if len(ix) <= 1 or len(para) <= REPEAT_MIN_LEN:
                continue
            if not any(ix[i + 1] - ix[i] < REPEAT_MAX_GAP for i in range(len(ix) - 1)):
                continue
            if any(fn in path.name and frag in para for fn, frag in allow):
                continue
            if (para.startswith("“") and para.endswith("。”")
                    and len(para) < SHORT_DIALOG_MAX):
                continue
            add("repeat_para", "重复段落 x%d：%s" % (len(ix), para[:30]))

    chars = _norm_len(text)
    if "word_count" in rules and min_chars is not None and max_chars is not None:
        if not (min_chars <= chars <= max_chars):
            add("word_count", "字数越界 %d（区间 %d-%d）" % (chars, min_chars, max_chars))

    return {"file": str(path), "name": path.name, "chars": chars,
            "issues": issues, "ok": not issues}


def lint(root: str | Path, *, chapter: int | None = None,
         min_chars: int | None = 2000, max_chars: int | None = 2500,
         rules: set[str] | None = None,
         allow_file: str | None = None) -> Dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    rules = set(rules or ALL_RULES)
    allow = _load_allow(allow_file)
    files = find_chapters(root_path, chapter)
    results = [check_file(f, min_chars=min_chars, max_chars=max_chars,
                          rules=rules, allow=allow) for f in files]
    bad = [r for r in results if not r["ok"]]
    by_rule: Counter = Counter()
    for r in bad:
        for it in r["issues"]:
            by_rule[it["rule"]] += 1
    return {"ok": not bad, "scanned": len(results), "failed": len(bad),
            "by_rule": dict(by_rule.most_common()), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="正文排版红线检查（11 条可执行规则）")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, default=None, help="只查这一章（按文件里的章号）")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--min-chars", type=int, default=2000,
                        help="字数下限（默认取 style-adapter.md 的 2000）")
    parser.add_argument("--max-chars", type=int, default=2500,
                        help="字数上限（默认取 style-adapter.md 的 2500）")
    parser.add_argument("--rule", action="append", default=None,
                        help="只跑某几条（可重复）；默认全跑。可选：%s" % ", ".join(ALL_RULES))
    parser.add_argument("--allow-file", default=None,
                        help="重复段落豁免清单（JSON 数组 [[文件名片段, 段落片段], …]）")
    args = parser.parse_args()

    rules = set(args.rule) if args.rule else set(ALL_RULES)
    unknown = rules - set(ALL_RULES)
    if unknown:
        # 🔴 用 parser.error 而不是 raise SystemExit("…")：插件的
        #    _run_data_module 会 int(e.code)，字符串退出码会崩成 ValueError 栈。
        parser.error("未知规则：%s（可选：%s）" % (sorted(unknown), ", ".join(ALL_RULES)))

    report = lint(args.project_root, chapter=args.chapter, min_chars=args.min_chars,
                  max_chars=args.max_chars, rules=rules, allow_file=args.allow_file)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if report["ok"]:
        print("OK %d 章全部通过（%d-%d 字）" % (report["scanned"], args.min_chars, args.max_chars))
        return
    for r in report["results"]:
        if r["ok"]:
            continue
        first = "; ".join("%s%s" % (it["detail"], "" if it["line"] is None else " @L%d" % it["line"])
                          for it in r["issues"][:4])
        print("ERR %-28s %5d字  %s" % (r["name"], r["chars"], first))
    print("\n共 %d 章，异常 %d 章；按规则：%s"
          % (report["scanned"], report["failed"],
             " ".join("%s=%d" % kv for kv in report["by_rule"].items())))


if __name__ == "__main__":
    main()
