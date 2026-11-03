#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宿主差异收敛层（Claude Code / Qoder / 其他 agent 宿主）。

业务真源只有一个：书项目根下的 ``.webnovel/state.json``。宿主之间不同的只是
「用户级数据放哪」「当前工作区从哪告诉插件」「项目上下文副本目录叫什么」。
本模块把这些差异收在一处，其余模块不再直接写死某个宿主的名字。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional

from runtime_compat import normalize_windows_path

# 用户级目录里的宿主家目录，按优先级排列（先匹配到的先用）。
USER_HOME_DIRS: tuple[str, ...] = (".qoder-cn", ".claude")

# 工作区内宿主目录，指针文件写在这些目录里。
WORKSPACE_DOT_DIRS: tuple[str, ...] = (".qoder", ".claude")

POINTER_FILE_NAME = ".webnovel-current-project"

# 显式覆盖：任何宿主只要把自己的家目录/工作区根导出即可，不用改代码。
ENV_AGENT_HOME = "WEBNOVEL_AGENT_HOME"
ENV_CLAUDE_HOME = "CLAUDE_HOME"
ENV_WEBNOVEL_CLAUDE_HOME = "WEBNOVEL_CLAUDE_HOME"
ENV_WORKSPACE_ROOT = "WEBNOVEL_WORKSPACE_ROOT"
ENV_CLAUDE_PROJECT_DIR = "CLAUDE_PROJECT_DIR"
ENV_QODER_CONFIG_DIR = "QODER_CONFIG_DIR"
ENV_QODERCN_CONFIG_DIR = "QODERCN_CONFIG_DIR"


def _as_path(raw: str) -> Path:
    path = normalize_windows_path(raw).expanduser()
    try:
        return path.resolve()
    except OSError:
        return path


def _env_home() -> Optional[Path]:
    """宿主家目录的显式覆盖（保持对旧变量名的兼容）。"""
    for name in (ENV_AGENT_HOME, ENV_WEBNOVEL_CLAUDE_HOME, ENV_CLAUDE_HOME):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return _as_path(raw.strip())
    return None


def agent_homes() -> list[Path]:
    """按优先级返回候选的用户级宿主家目录（去重，不要求都存在）。"""
    override = _env_home()
    if override is not None:
        return [override]
    homes: list[Path] = []
    for name in USER_HOME_DIRS:
        candidate = Path.home() / name
        if candidate not in homes:
            homes.append(candidate)
    return homes


def agent_home() -> Path:
    """写入用的宿主家目录。

    优先已经存过 webnovel 数据的家（保证旧项目的 registry/.env 能续上），
    其次第一个真实存在的宿主目录，兜底 ``~/.claude``（保持历史行为）。
    """
    override = _env_home()
    if override is not None:
        return override
    homes = agent_homes()
    for home in homes:
        if (home / "webnovel-writer").is_dir():
            return home
    for home in homes:
        if home.is_dir():
            return home
    return homes[0] if homes else (Path.home() / ".claude")


def registry_path() -> Path:
    """用户级 workspace → project_root 映射表路径。"""
    return agent_home() / "webnovel-writer" / "workspaces.json"


def global_dotenv_path() -> Path:
    """用户级全局 `.env`（API key 等）路径。"""
    return agent_home() / "webnovel-writer" / ".env"


def existing_global_dotenv_paths() -> list[Path]:
    """所有宿主家里真实存在的全局 `.env`，按宿主优先级排列。"""
    return [
        path
        for path in (home / "webnovel-writer" / ".env" for home in agent_homes())
        if path.is_file()
    ]


def workspace_hints() -> list[Path]:
    """宿主告诉插件「工作区根在哪」的环境变量取值（可能一个都没有）。"""
    hints: list[Path] = []
    for name in (ENV_WORKSPACE_ROOT, ENV_CLAUDE_PROJECT_DIR):
        raw = os.environ.get(name)
        if raw and raw.strip():
            hints.append(_as_path(raw.strip()))
    return hints


def pointer_read_paths(start: Path, *, stop_at: Optional[Path] = None) -> Iterator[Path]:
    """从 start 向上逐级产出各宿主目录下的指针文件路径（不校验是否存在）。"""
    for candidate in (start, *start.parents):
        for dot_dir in WORKSPACE_DOT_DIRS:
            yield candidate / dot_dir / POINTER_FILE_NAME
        if stop_at is not None and candidate == stop_at:
            break


def pointer_write_paths(workspace_root: Path) -> list[Path]:
    """指针写入路径：写入工作区内**所有**已存在的宿主目录。

    多个宿主（Claude Code 与 Qoder）共用同一批书项目时，只写一处会让两边
    跟着不同的指针走。这里同步写，保证「当前书」在两个宿主里是同一本。
    宿主目录不存在时不创建，避免在任意目录下凭空建出 `.qoder/`、`.claude/`。
    """
    return [
        workspace_root / dot_dir / POINTER_FILE_NAME
        for dot_dir in WORKSPACE_DOT_DIRS
        if (workspace_root / dot_dir).is_dir()
    ]


def workspace_dot_dir_exists(path: Path) -> bool:
    """该目录是否是任一宿主的「工作区宿主目录」（.qoder 或 .claude）。"""
    return any((path / dot_dir).is_dir() for dot_dir in WORKSPACE_DOT_DIRS)


def host_reference_path(project_root: Path, file_name: str) -> Path:
    """项目内宿主 references 副本路径：已存在的优先，否则给出首选路径。"""
    for dot_dir in WORKSPACE_DOT_DIRS:
        candidate = project_root / dot_dir / "references" / file_name
        if candidate.is_file():
            return candidate
    return project_root / WORKSPACE_DOT_DIRS[0] / "references" / file_name
