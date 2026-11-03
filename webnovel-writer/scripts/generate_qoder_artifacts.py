#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本插件的技能与 agent 生成为 Qoder 用户级产物。

业务真源仍然只有 Claude 那一份 `skills/*/SKILL.md` 与 `agents/*.md`；
本脚本只做「宿主表达」的机械转换，不复制业务逻辑，也不改源文件。

用法：
    python -X utf8 scripts/generate_qoder_artifacts.py            # 生成并安装到 Qoder 用户目录
    python -X utf8 scripts/generate_qoder_artifacts.py --check    # 只核对漂移，不写文件（非零退出=有漂移）
    python -X utf8 scripts/generate_qoder_artifacts.py --out DIR   # 生成到指定目录（不碰用户目录）

产物落点（默认 host=C:/Users/.../.qoder-cn）：
    <host>/skills/<skill>/SKILL.md
    <host>/agents/webnovel/webnovel-<agent>.md
    <host>/webnovel-writer/qoder_artifacts.json   生成清单（源提交、版本、内容哈希）

技能正文里的 references/templates 不复制进用户目录：SKILL_ROOT 与 SCRIPTS_DIR 都从插件根解析，
资料永远只有插件那一份，避免装完就和源分叉。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "webnovel-writer"
SKILLS_SRC = PLUGIN_ROOT / "skills"
AGENTS_SRC = PLUGIN_ROOT / "agents"

MANIFEST_REL = Path("webnovel-writer") / "qoder_artifacts.json"
AGENTS_GROUP_DIR = "webnovel"

# Claude 专有、Qoder 不识别的 frontmatter 键（保留 name/description 即可）
DROP_FRONTMATTER_KEYS = ("allowed-tools", "argument-hint", "version")

# agent 规范名：加 webnovel- 前缀，避免和宿主里其他 agent 撞名
AGENT_NAME_MAP = {
    "context-agent.md": "webnovel-context-agent",
    "reviewer.md": "webnovel-reviewer",
    "data-agent.md": "webnovel-data-agent",
    "deconstruction-agent.md": "webnovel-deconstruction-agent",
}

# 转换后必须消失的宿主痕迹（断言用，防止漏改导致产物在 Qoder 里静默失败）
FORBIDDEN_AFTER_TRANSFORM = (
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PROJECT_DIR",
    "${SKILL_ROOT}/../..",
    "webnovel-writer:context-agent",
    "webnovel-writer:reviewer",
    "webnovel-writer:data-agent",
    "webnovel-writer:deconstruction-agent",
)


def detect_qoder_home() -> Path:
    for name in ("QODER_CONFIG_DIR", "QODERCN_CONFIG_DIR"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return Path(raw.strip()).expanduser().resolve()
    return Path.home() / ".qoder-cn"


# ---------------------------------------------------------------------------
# 文本转换
# ---------------------------------------------------------------------------

def _frontmatter_span(text: str) -> Optional[tuple[int, int]]:
    m = re.match(r"^---[ \t]*\r?\n(.*?)(?:\r?\n)---[ \t]*\r?\n", text, re.DOTALL)
    if not m:
        return None
    return 0, m.end()


def _strip_frontmatter_keys(text: str) -> str:
    span = _frontmatter_span(text)
    if span is None:
        return text
    kept: list[str] = []
    for line in text[span[0] : span[1]].splitlines():
        key = re.match(r"^([A-Za-z_][\w-]*):", line)
        if key and key.group(1) in DROP_FRONTMATTER_KEYS:
            continue
        kept.append(line)
    return "\n".join(kept).rstrip("\r\n") + "\n" + text[span[1] :]


def transform_skill(text: str, *, skill_name: str, plugin_root: Path) -> str:
    """Claude 技能 → Qoder 技能。"""
    text = _strip_frontmatter_keys(text)

    # 1) 变量改名
    text = text.replace("${CLAUDE_PLUGIN_ROOT}", "${WEBNOVEL_PLUGIN_ROOT}")
    text = text.replace("CLAUDE_PLUGIN_ROOT", "WEBNOVEL_PLUGIN_ROOT")
    text = text.replace("${CLAUDE_PROJECT_DIR:-$PWD}", "${WEBNOVEL_WORKSPACE_ROOT:-$PWD}")
    text = text.replace("CLAUDE_PROJECT_DIR", "WEBNOVEL_WORKSPACE_ROOT")

    # 2) 跨技能共享资料：技能装在用户目录，../../ 已指不到插件根，改用插件根变量
    text = text.replace("${SKILL_ROOT}/../../", "${WEBNOVEL_PLUGIN_ROOT}/")

    # 3) SKILL_ROOT 统一指向插件根下的技能目录：资料只有这一份真相，
    #    不往用户目录复制副本（副本会随上游更新过期）。
    text = re.sub(
        r'export SKILL_ROOT="[^"]*"',
        'export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/' + skill_name + '"',
        text,
    )

    # 4) agent 调用名换成 Qoder 里注册的规范名
    for agent_file, canonical in AGENT_NAME_MAP.items():
        bare = agent_file[: -len(".md")]
        text = text.replace(f"webnovel-writer:{bare}", canonical)
        text = text.replace(f"调用 `{bare}`", f"调用 `{canonical}`")
        text = text.replace(f"`Agent` 工具调用 `{bare}`", f"`Agent` 工具调用 `{canonical}`")

    # 5) 引导块插在第一个 ```bash 代码块内（宿主差异集中在这里）
    if "WEBNOVEL_PLUGIN_ROOT:=" not in text:
        text = _inject_bootstrap(text, plugin_root, skill_name=skill_name)

    return text


def _inject_bootstrap(text: str, plugin_root: Path, *, skill_name: str) -> str:
    marker = "```bash"
    idx = text.find(marker)
    if idx < 0:
        return text
    insert_at = idx + len(marker) + 1
    block = (
        "# Qoder 宿主引导：插件根只有一个真源，技能资料与脚本都从这里解析。\n"
        "# 安装位置变化时改这里，或先 export WEBNOVEL_PLUGIN_ROOT。\n"
        ': "${WEBNOVEL_PLUGIN_ROOT:=' + plugin_root.as_posix() + '}"\n'
        'if [ ! -d "${WEBNOVEL_PLUGIN_ROOT}/scripts" ]; then\n'
        '  echo "ERROR: WEBNOVEL_PLUGIN_ROOT 无效，找不到 ${WEBNOVEL_PLUGIN_ROOT}/scripts" >&2\n'
        "  exit 1\n"
        "fi\n"
        'export WEBNOVEL_PLUGIN_ROOT\n'
        'export SCRIPTS_DIR="${WEBNOVEL_PLUGIN_ROOT}/scripts"\n'
        'export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/' + skill_name + '"\n'
    )
    return text[:insert_at] + block + text[insert_at:]


def transform_agent(text: str, *, canonical_name: str) -> str:
    """Claude agent → Qoder agent（frontmatter 换形状，正文只做变量与名字替换）。"""
    span = _frontmatter_span(text)
    body = text[span[1] :] if span else text
    desc = ""
    if span:
        m = re.search(r"^description:[ \t]*(.+)$", text[: span[1]], re.MULTILINE)
        if m:
            desc = m.group(1).strip()
    frontmatter = (
        "---\n"
        f"name: {canonical_name}\n"
        f"description: {desc or canonical_name}\n"
        "permissionMode: auto\n"
        "skills: []\n"
        "mcpServers: []\n"
        "---\n\n"
    )
    body = body.replace("${CLAUDE_PLUGIN_ROOT}", "${WEBNOVEL_PLUGIN_ROOT}")
    body = body.replace("CLAUDE_PROJECT_DIR", "WEBNOVEL_WORKSPACE_ROOT")
    for agent_file, canonical in AGENT_NAME_MAP.items():
        body = body.replace(f"webnovel-writer:{agent_file[: -len('.md')]}", canonical)
    return frontmatter + body


def assert_host_neutral(text: str, label: str) -> None:
    leftovers = [token for token in FORBIDDEN_AFTER_TRANSFORM if token in text]
    if leftovers:
        raise SystemExit(f"转换失败：{label} 仍含宿主痕迹 {leftovers}")


# ---------------------------------------------------------------------------
# 收集产物
# ---------------------------------------------------------------------------

def build_artifacts(plugin_root: Path) -> dict[str, str]:
    """返回 {目标相对路径: 内容}。"""
    artifacts: dict[str, str] = {}

    for skill_md in sorted(SKILLS_SRC.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        text = skill_md.read_text(encoding="utf-8")
        converted = transform_skill(text, skill_name=skill_name, plugin_root=plugin_root)
        assert_host_neutral(converted, f"skills/{skill_name}/SKILL.md")
        artifacts[f"skills/{skill_name}/SKILL.md"] = converted

    for agent_md in sorted(AGENTS_SRC.glob("*.md")):
        canonical = AGENT_NAME_MAP.get(agent_md.name)
        if canonical is None:
            continue
        converted = transform_agent(agent_md.read_text(encoding="utf-8"), canonical_name=canonical)
        assert_host_neutral(converted, f"agents/{agent_md.name}")
        artifacts[f"agents/{AGENTS_GROUP_DIR}/{canonical}.md"] = converted

    return artifacts


def source_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def plugin_version() -> str:
    try:
        payload = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return str(payload.get("version", "unknown"))
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# 落盘 / 核对
# ---------------------------------------------------------------------------

def write_artifacts(artifacts: dict[str, str], out_root: Path, *, plugin_root: Path, qoder_home: Path) -> Path:
    for rel, content in artifacts.items():
        target = out_root / Path(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    manifest = {
        "schema_version": "webnovel-qoder-artifacts/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_plugin_version": plugin_version(),
        "source_commit": source_commit(),
        "plugin_root": str(plugin_root),
        "qoder_home": str(qoder_home),
        "files": {rel: hashlib.sha256(content.encode("utf-8")).hexdigest() for rel, content in sorted(artifacts.items())},
    }
    manifest_path = out_root / MANIFEST_REL
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest_path


def check_drift(artifacts: dict[str, str], host: Path) -> list[str]:
    drifted: list[str] = []
    for rel, content in sorted(artifacts.items()):
        target = host / Path(*rel.split("/"))
        if not target.is_file():
            drifted.append(f"missing: {rel}")
            continue
        if target.read_text(encoding="utf-8") != content:
            drifted.append(f"stale: {rel}")
    return drifted


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Qoder 宿主产物")
    parser.add_argument("--host", default=None, help="Qoder 配置目录（默认自动探测 ~/.qoder-cn）")
    parser.add_argument("--out", default=None, help="只生成到指定目录，不写用户目录")
    parser.add_argument("--check", action="store_true", help="只核对漂移，不写文件")
    args = parser.parse_args(argv)

    host = Path(args.host).expanduser().resolve() if args.host else detect_qoder_home()
    out_root = Path(args.out).expanduser().resolve() if args.out else host

    if not SKILLS_SRC.is_dir():
        print(f"找不到技能源目录：{SKILLS_SRC}", file=sys.stderr)
        return 2

    artifacts = build_artifacts(PLUGIN_ROOT)

    if args.check:
        drifted = check_drift(artifacts, host)
        for line in drifted:
            print(line)
        if drifted:
            print(f"漂移 {len(drifted)} 项：源已改，产物需重跑本脚本", file=sys.stderr)
            return 1
        print(f"无漂移：{len(artifacts)} 个产物与源一致（host={host}）")
        return 0

    manifest_path = write_artifacts(artifacts, out_root, plugin_root=PLUGIN_ROOT, qoder_home=host)
    print(f"生成 {len(artifacts)} 个宿主产物（8 技能 + 4 agent）→ {out_root}")
    print("技能资料不复制副本：SKILL_ROOT / SCRIPTS_DIR 一律从插件根解析，只有一份真相")
    print(f"清单：{manifest_path}")
    print(f"来源：{plugin_version()} @{source_commit()}，插件根 {PLUGIN_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
