#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

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


def main() -> int:
    if _truthy(os.environ.get(DISABLE_ENV)):
        return 0

    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
    workspace_root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    webnovel = plugin_root / "scripts" / "webnovel.py"
    if not webnovel.is_file():
        return 0

    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(webnovel),
                "--project-root",
                str(workspace_root),
                "project-status",
                "--format",
                "summary",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=4,
        )
    except Exception:
        return 0

    output = _clip(proc.stdout or proc.stderr or "")
    if output:
        print(output)
    # 🟢 待裁提醒：有 rejected 章产物就提示一句，指向**另一个插件**的 /webnovel-ruling。
    #    不检查它装没装（本插件看不到别的插件目录），纯只读、失败即静默。
    try:
        pending = len(list((workspace_root / ".story-system" / "commits")
                           .glob("chapter_*.commit.json")))
        if pending:
            print("待裁卡在 .story-system/commits（%d 章产物）；装了 webnovel-ruling 插件可用 /webnovel-ruling 开台子"
                  % pending)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
