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
import shutil
import subprocess
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


def bash_safe_command(*parts: str | Path) -> str:
    """拼出宿主钩子命令串。

    🔴 两个必须：可执行文件与脚本路径都要加引号，且用正斜杠。
    Qoder 通过 bash 执行 command，实测裸写
    `C:\\Users\\admin\\...\\python.exe -X utf8 \"...\"` 会被吃成
    `C:Usersadmin...python.exe: command not found`（退出码 127），
    而宿主对非零退出是「记录告警后继续执行」——护栏直接 fail open。
    """
    return " ".join(f'"{Path(str(part)).as_posix()}"' for part in parts)


def build_entries(*, python_executable: str, plugin_root: Path) -> dict[str, list[dict[str, Any]]]:
    """本插件需要的钩子条目（键为事件名）。"""

    def command(name: str, script: str, matcher: str, timeout: int) -> dict[str, Any]:
        return {
            "matcher": matcher,
            "hooks": [
                {
                    "type": "command",
                    "name": name,
                    "command": bash_safe_command(python_executable, "-X", "utf8", plugin_root / "hooks" / script),
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


GUARD_DENY_PAYLOADS = {
    "webnovel-writer-guard-write": (
        json.dumps({"tool_name": "Write", "tool_input": {"file_path": "D:/tmp/probe/.webnovel/index.db"}}),
        2,
    ),
    "webnovel-writer-guard-bash": (
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "python chapter_commit.py --project-root book"}}),
        2,
    ),
    "webnovel-writer-session-start": ("{}", 0),
}


def hook_shell() -> str:
    """自检用哪个 shell：宿主在 Git Bash 里执行钩子命令，自检必须用同一个。

    实测过另一种走法：PATH 上先命中的 bash（非 Git Bash）会把同一条命令跑成退出码 1，
    三条钩子全被误报成「没生效」；而宿主自己执行同一条命令是能拦的。
    """
    raw = os.environ.get("WEBNOVEL_HOOK_SHELL")
    if raw and raw.strip():
        return raw.strip()
    for candidate in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("bash")
    if found:
        return found
    raise SystemExit("找不到 bash，无法做钩子自检；用 WEBNOVEL_HOOK_SHELL 指定宿主用的 shell")


def verify(settings: dict[str, Any], *, cwd: Path) -> list[str]:
    """真跑一遍已装的钩子命令，确认它「跑得起来且判定正确」。

    宿主对非零退出是记录告警后继续执行，所以命令拼错（如 Windows 反斜杠被 bash 吃掉、
    退出码 127）等于护栏静默失效——只看 --check 比对 JSON 是查不出来的。
    """
    problems: list[str] = []
    shell = hook_shell()
    installed = current_entries(settings)
    commands = {
        hook.get("name"): hook.get("command")
        for groups in installed.values()
        for group in groups
        for hook in group.get("hooks") or []
    }
    for name, (payload, expected_code) in GUARD_DENY_PAYLOADS.items():
        command = commands.get(name)
        if not command:
            problems.append(f"{name}: settings 里没有这条钩子")
            continue
        try:
            proc = subprocess.run(
                [shell, "-c", command],
                input=payload.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd),
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            problems.append(f"{name}: 无法执行（{exc}）；shell={shell}")
            continue
        # 按字节读再容错解码：子进程输出编码不统一，解码异常不能被当成钩子失败
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != expected_code:
            note = "（命令没跑起来 = 护栏静默放行，最常见是路径反斜杠被 bash 吃掉）" if proc.returncode in {126, 127, 1} else ""
            problems.append(
                f"{name}: 退出码 {proc.returncode}，期望 {expected_code}{note}；shell={shell}"
                + (f"；stderr: {stderr[:160]}" if stderr else "")
            )
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="注册/核对/摘除 webnovel-writer 的 Qoder 钩子")
    parser.add_argument("--settings", default=None, help="settings.json 路径（默认自动探测宿主目录）")
    parser.add_argument("--plugin-root", default=str(PLUGIN_ROOT), help="插件根（钩子脚本所在包）")
    parser.add_argument("--check", action="store_true", help="只核对漂移，不写文件")
    parser.add_argument("--verify", action="store_true", help="真跑一遍已装命令，确认护栏没在静默失效")
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

    if args.verify:
        problems = verify(settings, cwd=Path(plugin_root))
        for line in problems:
            print(line, file=sys.stderr)
        if problems:
            print(f"护栏未生效 {len(problems)} 项", file=sys.stderr)
            return 1
        print("已验证：三条钩子命令都能跑起来且判定正确")
        return 0

    merged = merge_entries(settings, entries)
    was_installed = bool(current_entries(settings))
    backup_path = write_settings(settings_path, merged)
    print(f"{'已更新' if was_installed else '已安装'} webnovel-writer 钩子 → {settings_path}")
    print("  SessionStart: * → session_start.py（开场项目状态）")
    print(f"  PreToolUse: {WRITE_MATCHER} → guard_runtime_write.py（拦直写 Story System/读模型）")
    print("  PreToolUse: Bash → guard_runtime_write.py（拦绕过 runtime 的投影写）")
    print(f"备份：{backup_path}")

    problems = verify(merged, cwd=Path(plugin_root))
    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        print("已写入 settings，但护栏自检未通过：宿主对非零退出是继续执行，这等于没拦。修好前别依赖它。", file=sys.stderr)
        return 1
    print("自检通过：三条命令实跑判定正确。宿主钩子在会话开始时装载，改完建议重启 Qoder。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
