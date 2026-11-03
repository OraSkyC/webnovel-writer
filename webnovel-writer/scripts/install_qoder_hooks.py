#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本插件的两个运行时护栏注册进 Qoder 用户级 settings.json。

装的是 Claude 侧同一对脚本（不复制、不改写业务逻辑）：
- hooks/session_start.py —— 开场打印项目状态（替代宿主专属的提示注入）
- hooks/guard_runtime_write.py —— 拦截绕过 runtime 直写 Story System/读模型文件

配置形状与 Qoder 自带插件的 hooks/hooks.json 一致：
`{"hooks": {"<Event>": [{"matcher": ..., "hooks": [{"type":"command","name":...,"command":...}]}]}}`

用法：
    python -X utf8 scripts/install_qoder_hooks.py             # 安装/更新
    python -X utf8 scripts/install_qoder_hooks.py --check     # 只核对（有漂移非零退出）
    python -X utf8 scripts/install_qoder_hooks.py --remove    # 只摘掉本插件的条目
    python -X utf8 scripts/install_qoder_hooks.py --settings PATH  # 指到别的 settings.json（测试用）

写文件前自动留备份；合并只动 `hooks` 里带本插件 name 标记的条目，
其它键、其它宿主编排的钩子原样保留。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parent

HOOK_NAME_PREFIX = "webnovel-writer-"
OWN_NAMES = (
    "webnovel-writer-session-start",
    "webnovel-writer-guard-write",
    "webnovel-writer-guard-bash",
)
WRITE_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"
BACKUP_SUFFIX = ".pre-webnovel-hooks.bak"


def detect_settings_path() -> Path:
    for name in ("QODER_CONFIG_DIR", "QODERCN_CONFIG_DIR"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return Path(raw.strip()).expanduser() / "settings.json"
    return Path.home() / ".qoder-cn" / "settings.json"


def build_entries(*, python_executable: str, plugin_root: Path) -> dict[str, list[dict[str, Any]]]:
    """本插件需要的钩子条目（键为事件名）。"""

    def command(name: str, script: str, matcher: str, timeout: int) -> dict[str, Any]:
        return {
            "matcher": matcher,
            "hooks": [
                {
                    "type": "command",
                    "name": name,
                    "command": f'{python_executable} -X utf8 "{(plugin_root / "hooks" / script).as_posix()}"',
                    "timeout": timeout,
                }
            ],
        }

    return {
        "SessionStart": [command(OWN_NAMES[0], "session_start.py", "*", 8)],
        "PreToolUse": [
            command(OWN_NAMES[1], "guard_runtime_write.py", WRITE_MATCHER, 5),
            command(OWN_NAMES[2], "guard_runtime_write.py", "Bash", 5),
        ],
    }


def _is_own_group(group: dict[str, Any]) -> bool:
    for hook in group.get("hooks") or []:
        if isinstance(hook, dict) and str(hook.get("name") or "").startswith(HOOK_NAME_PREFIX):
            return True
    return False


def merge_entries(settings: dict[str, Any], entries: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """先摘掉本插件旧条目，再追加新条目；外来条目与外来键一律不动。"""
    merged = json.loads(json.dumps(settings))  # 深拷贝，不改调用方对象
    hooks = merged.get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}
    merged_hooks: dict[str, Any] = {event: list(groups) for event, groups in hooks.items() if isinstance(groups, list)}

    for event, groups in merged_hooks.items():
        merged_hooks[event] = [group for group in groups if not (isinstance(group, dict) and _is_own_group(group))]

    for event, groups in entries.items():
        remaining = [group for group in merged_hooks.get(event, []) if group]
        merged_hooks[event] = remaining + list(groups)

    merged["hooks"] = {event: groups for event, groups in merged_hooks.items() if groups}
    return merged


def strip_entries(settings: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(settings))
    hooks = merged.get("hooks")
    if not isinstance(hooks, dict):
        return merged
    cleaned: dict[str, Any] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            cleaned[event] = groups
            continue
        kept = [group for group in groups if not (isinstance(group, dict) and _is_own_group(group))]
        if kept:
            cleaned[event] = kept
    merged["hooks"] = cleaned
    return merged


def current_entries(settings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """取出 settings 里属于本插件的条目，按事件分组（用于比对漂移）。"""
    result: dict[str, list[dict[str, Any]]] = {}
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        own = [group for group in groups if isinstance(group, dict) and _is_own_group(group)]
        if own:
            result[event] = own
    return result


def load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"读不了 {path}：{exc}（先手工修好 JSON 再来，我不猜）")
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} 顶层不是 JSON 对象，拒绝改写")
    return payload


def write_settings(path: Path, payload: dict[str, Any], *, backup: bool = True) -> Optional[Path]:
    backup_path: Optional[Path] = None
    if backup and path.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}{BACKUP_SUFFIX}.{stamp}")
        backup_path.write_bytes(path.read_bytes())
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f"{path.name}.webnovel-tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return backup_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="注册/核对/摘除 webnovel-writer 的 Qoder 钩子")
    parser.add_argument("--settings", default=None, help="settings.json 路径（默认自动探测宿主目录）")
    parser.add_argument("--plugin-root", default=str(PLUGIN_ROOT), help="插件根（钩子脚本所在包）")
    parser.add_argument("--check", action="store_true", help="只核对漂移，不写文件")
    parser.add_argument("--remove", action="store_true", help="摘除本插件条目")
    args = parser.parse_args(argv)

    settings_path = Path(args.settings).expanduser().resolve() if args.settings else detect_settings_path()
    plugin_root = Path(args.plugin_root).expanduser().resolve()
    guard_script = plugin_root / "hooks" / "guard_runtime_write.py"
    start_script = plugin_root / "hooks" / "session_start.py"
    if not args.remove and (not guard_script.is_file() or not start_script.is_file()):
        print(f"找不到钩子脚本：{start_script} / {guard_script}", file=sys.stderr)
        return 2

    settings = load_settings(settings_path)

    if args.remove:
        if not current_entries(settings):
            print(f"未安装本插件钩子：{settings_path}")
            return 0
        backup_path = write_settings(settings_path, strip_entries(settings))
        print(f"已摘除本插件钩子：{settings_path}")
        print(f"备份：{backup_path}")
        print("重启 Qoder 后生效。")
        return 0

    entries = build_entries(python_executable=sys.executable, plugin_root=plugin_root)

    if args.check:
        installed = current_entries(settings)
        drifted = [event for event in entries if installed.get(event) != entries[event]]
        missing = [event for event in entries if event not in installed]
        for event in drifted:
            print(f"stale: {event}" + (" (missing)" if event in missing else ""))
        if drifted:
            print(f"漂移 {len(drifted)} 项：重跑本脚本即可", file=sys.stderr)
            return 1
        print(f"无漂移：本插件钩子已在位（{settings_path}）")
        return 0

    merged = merge_entries(settings, entries)
    was_installed = bool(current_entries(settings))
    backup_path = write_settings(settings_path, merged)
    print(f"{'已更新' if was_installed else '已安装'} webnovel-writer 钩子 → {settings_path}")
    print("  SessionStart: * → session_start.py（开场项目状态）")
    print(f"  PreToolUse: {WRITE_MATCHER} → guard_runtime_write.py（拦直写 Story System/读模型）")
    print("  PreToolUse: Bash → guard_runtime_write.py（拦绕过 runtime 的投影写）")
    print(f"备份：{backup_path}")
    print("生效需要重启 Qoder；stdin 载荷字段名尚未实测，首次触发时用 WEBNOVEL_HOOK_DEBUG=1 跑一次可落日志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
