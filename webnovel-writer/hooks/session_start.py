#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


MAX_LINES = 8
MAX_CHARS = 1000
DISABLE_ENV = "WEBNOVEL_DISABLE_SESSION_STATUS_HOOK"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clip(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()][:MAX_LINES]
    clipped = "\n".join(lines).strip()
    if len(clipped) > MAX_CHARS:
        clipped = clipped[: MAX_CHARS - 3].rstrip() + "..."
    return clipped


def _plugin_root() -> Path:
    """宿主导出的插件根优先（Claude / Qoder），都没有就从脚本自身位置反推。

    用户级 settings.json 注册的钩子没有插件根变量，反推那条路径是它的正常入口。
    """
    for name in ("WEBNOVEL_PLUGIN_ROOT", "QODER_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return Path(raw.strip()).expanduser()
    return Path(__file__).resolve().parents[1]


def _workspace_root() -> str:
    """工作区根：宿主提示变量优先，退到当前工作目录。"""
    for name in ("WEBNOVEL_WORKSPACE_ROOT", "CLAUDE_PROJECT_DIR"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return raw.strip()
    return os.getcwd()


def _pending_ruling_count(commits_dir: Path) -> int:
    """只数真的待裁：`meta.status == rejected`，或 `disambiguation_result.pending` 非空。

    原先直接数 commits 目录里的文件总数（日记星辰 61、时空纪元 726），
    等于每章提交都算待裁 —— 那行提示从此没有信息量。
    """
    if not commits_dir.is_dir():
        return 0
    total = 0
    for path in commits_dir.glob("chapter_*.commit.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        meta = payload.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        disambiguation = payload.get("disambiguation_result")
        pending = disambiguation.get("pending") if isinstance(disambiguation, dict) else None
        if str(meta.get("status") or "").lower() == "rejected" or (isinstance(pending, list) and pending):
            total += 1
    return total


def main() -> int:
    if _truthy(os.environ.get(DISABLE_ENV)):
        return 0

    plugin_root = _plugin_root()
    workspace_root = _workspace_root()
    webnovel = plugin_root / "scripts" / "webnovel.py"
    if not webnovel.is_file():
        return 0

    def _run(*args: str, timeout: int = 6) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(webnovel), "--project-root", str(workspace_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )

    # 先确认这里真有一本 webnovel 项目：解析不到就整个钩子静默退出。
    # 不这么做的话，非书项目（写代码、做 PPT 的会话）开场会被塞一坨
    # “未命名项目 / blocking: missing .webnovel/state.json” 的噪音。
    try:
        located = _run("where")
    except Exception:
        return 0
    if located.returncode != 0 or not (located.stdout or "").strip():
        return 0
    project_root = Path(located.stdout.strip().splitlines()[-1])

    # 开场只用 preflight（实测 0.4s）：它是同一入口，已带 project_root 与 story_runtime 关键读数。
    # 不用 project-status —— 在 705 章的书上实测要 25-30s，作为会话开场钩子必然超时静默。
    try:
        proc = _run("preflight", "--format", "text")
    except Exception:
        return 0

    output = _clip(proc.stdout or proc.stderr or "")
    if output:
        print(output)
    # 🟢 待裁提醒：只数真的待裁（rejected 章 或 pending 卡非空），指向**另一个插件**的 /cadt-ruling。
    #    不检查它装没装（本插件看不到别的插件目录），纯只读、失败即静默。
    try:
        pending = _pending_ruling_count(project_root / ".story-system" / "commits")
        if pending:
            print("待裁卡：%d 章（rejected 或有 pending 卡）；装了 cadt-ruling 插件可用 /cadt-ruling 开台子"
                  % pending)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
