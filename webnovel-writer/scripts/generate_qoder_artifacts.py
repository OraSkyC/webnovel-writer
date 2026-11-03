#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把插件的技能与 agent 生成为 Qoder 用户级产物。

业务真源仍然只有 `skills/*/SKILL.md` 与 `agents/*.md` 那一份；本脚本只做「宿主表达」的
机械转换，不复制业务逻辑，也不改源文件。默认转换本仓库的 webnovel-writer；
`--plugin-root` 指向兄弟插件（如 cadt-ruling）时按同一条规则生成它自己的产物。

用法：
    python -X utf8 scripts/generate_qoder_artifacts.py                      # 本插件
    python -X utf8 scripts/generate_qoder_artifacts.py --plugin-root ../cadt-ruling
    python -X utf8 scripts/generate_qoder_artifacts.py --check              # 只核对漂移
    python -X utf8 scripts/generate_qoder_artifacts.py --out DIR            # 生成到别处，不碰用户目录

产物落点（默认 host=C:/Users/.../.qoder-cn）：
    <host>/skills/<skill>/SKILL.md
    <host>/agents/<前缀>/<规范名>.md
    <host>/<插件目录名>/qoder_artifacts.json   生成清单（源版本、commit、逐个文件哈希）

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
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "webnovel-writer"

# 本插件的规范名映射（历史命名，装了就得稳住，技能正文按这些名字调子代理）
BUILTIN_AGENT_NAME_MAP = {
    "context-agent.md": "webnovel-context-agent",
    "reviewer.md": "webnovel-reviewer",
    "data-agent.md": "webnovel-data-agent",
    "deconstruction-agent.md": "webnovel-deconstruction-agent",
}
BUILTIN_ENV_VAR = "WEBNOVEL_PLUGIN_ROOT"
BUILTIN_AGENT_PREFIX = "webnovel"

# 工作区根提示变量：跨插件共用同一个名字，宿主只需要导出一次
WORKSPACE_ENV = "WEBNOVEL_WORKSPACE_ROOT"

# Claude 专有、Qoder 不识别的 frontmatter 键（保留 name/description 即可）
DROP_FRONTMATTER_KEYS = ("allowed-tools", "argument-hint", "version")


def detect_qoder_home() -> Path:
    for name in ("QODER_CONFIG_DIR", "QODERCN_CONFIG_DIR"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return Path(raw.strip()).expanduser().resolve()
    return Path.home() / ".qoder-cn"


def _load_agent_name_map(plugin_root: Path) -> dict[str, str]:
    """插件自己声明的规范名表：`<插件根>/agents/name-map.json`（`{"文件名.md": "规范名"}`）。

    有它就按它命名（宿主里 agent 名是全局的，谁的名字谁负责）；
    没有就套默认规则：已带前缀的保持原名，裸名加 `<前缀>-`。
    """
    path = plugin_root / "agents" / "name-map.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path} 解析失败：{exc}")
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} 必须是 {{文件名.md: 规范名}} 的映射")
    return {str(key): str(value) for key, value in payload.items()}


def resolve_plugin_config(plugin_root: Path) -> dict[str, Any]:
    """按插件根派生宿主表达所需的一组口径。

    本仓库沿用作者已经装好的名字与变量（改了会让已装产物漂移）；
    兄弟插件按目录名派生：cadt-ruling → 环境变量 CADT_RULING_PLUGIN_ROOT、
    agent 前缀 cadt-ruling、清单落 <插件目录名>/qoder_artifacts.json，
    规范名以该插件自带的 agents/name-map.json 为准。
    """
    plugin_root = plugin_root.resolve()
    dir_name = plugin_root.name
    if plugin_root == PLUGIN_ROOT.resolve():
        return {
            "plugin_root": plugin_root,
            "dir_name": dir_name,
            "env_var": BUILTIN_ENV_VAR,
            "agent_prefix": BUILTIN_AGENT_PREFIX,
            "agent_name_map": dict(BUILTIN_AGENT_NAME_MAP),
            "manifest_rel": f"{dir_name}/qoder_artifacts.json",
        }
    return {
        "plugin_root": plugin_root,
        "dir_name": dir_name,
        "env_var": f"{dir_name.upper().replace('-', '_')}_PLUGIN_ROOT",
        "agent_prefix": dir_name,
        "agent_name_map": _load_agent_name_map(plugin_root),
        "manifest_rel": f"{dir_name}/qoder_artifacts.json",
    }


def forbidden_tokens(config: dict[str, Any]) -> tuple[str, ...]:
    """转换后必须消失的宿主痕迹（断言用，漏改会让产物在 Qoder 里静默失败）。"""
    return (
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PROJECT_DIR",
        "${SKILL_ROOT}/../..",
        f"{config['dir_name']}:context-agent",
        f"{config['dir_name']}:reviewer",
        f"{config['dir_name']}:data-agent",
        f"{config['dir_name']}:deconstruction-agent",
        f"{config['dir_name']}:ruling-executor",
        f"{config['dir_name']}:analyzer",
    )


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


def _agent_canonical_name(config: dict[str, Any], file_name: str) -> Optional[str]:
    """agent 规范名：显式映射优先，其次已带前缀的保持原名，否则加 `<前缀>-`。

    加前缀是为了不和宿主里其它包的 agent 撞名（宿主按名字全局注册）。
    """
    mapped = config["agent_name_map"].get(file_name)
    if mapped:
        return mapped
    stem = file_name[: -len(".md")]
    if not stem.startswith(config["agent_prefix"] + "-"):
        return f"{config['agent_prefix']}-{stem}"
    return stem


def _agent_names(config: dict[str, Any]) -> list[tuple[str, str]]:
    """(源文件名去 .md, 规范名) 列表，供正文里的调用名替换。"""
    agents_dir = config["plugin_root"] / "agents"
    pairs: list[tuple[str, str]] = []
    for agent_md in sorted(agents_dir.glob("*.md")):
        canonical = _agent_canonical_name(config, agent_md.name)
        if canonical:
            pairs.append((agent_md.name[: -len(".md")], canonical))
    return pairs


def _agent_call_pattern(config: dict[str, Any]) -> Optional[re.Pattern[str]]:
    """正文里「调用 agent」的写法：动词 + 裸名或反引号名。

    只认结构位置（前面必须有动词），所以不会误伤 `agents/ruling-executor.md` 这种路径，
    也不会把 prose 里孤立的词改掉；lookbehind 挡住已带前缀的名字，防止二次套叠。
    """
    pairs = dict(_agent_names(config))
    # 已带前缀的规范名自己也是候选（源里可能已写成规范名），保持幂等
    for _, canonical in list(pairs.items()):
        pairs.setdefault(canonical, canonical)
    names = sorted((key for key in pairs if key), key=len, reverse=True)
    if not names:
        return None
    return re.compile(
        r"(调用|使用|用|起|交给|让|由)\s*`?(?<![-\w])(" + "|".join(re.escape(name) for name in names) + r")`?(?![\w.-])"
    )


def _replace_agent_calls(text: str, config: dict[str, Any]) -> str:
    pairs = dict(_agent_names(config))
    for _, canonical in list(pairs.items()):
        pairs.setdefault(canonical, canonical)
    pattern = _agent_call_pattern(config)
    if pattern is None:
        return text

    def _sub(match: re.Match) -> str:
        return f"{match.group(1)} `{pairs[match.group(2)]}`"

    return pattern.sub(_sub, text)


def transform_skill(text: str, *, skill_name: str, config: dict[str, Any]) -> str:
    """Claude 技能 → Qoder 技能。"""
    env_var = config["env_var"]
    text = _strip_frontmatter_keys(text)

    # 1) 变量改名
    text = text.replace("${CLAUDE_PLUGIN_ROOT}", f"${{{env_var}}}")
    text = text.replace("CLAUDE_PLUGIN_ROOT", env_var)
    text = text.replace("${CLAUDE_PROJECT_DIR:-$PWD}", f"${{{WORKSPACE_ENV}:-$PWD}}")
    text = text.replace("CLAUDE_PROJECT_DIR", WORKSPACE_ENV)

    # 2) 跨技能共享资料：技能装在用户目录，../../ 已指不到插件根，改用插件根变量
    text = text.replace("${SKILL_ROOT}/../../", f"${{{env_var}}}/")

    # 3) SKILL_ROOT 统一指向插件根下的技能目录：资料只有这一份真相，
    #    不往用户目录复制副本（副本会随上游更新过期）。
    text = re.sub(
        r'export SKILL_ROOT="[^"]*"',
        f'export SKILL_ROOT="${{{env_var}}}/skills/{skill_name}"',
        text,
    )

    # 4) agent 调用名换成 Qoder 里注册的规范名
    for bare, canonical in _agent_names(config):
        text = text.replace(f"{config['dir_name']}:{bare}", canonical)
    text = _replace_agent_calls(text, config)

    # 5) 引导块插在第一个 ```bash 代码块内（宿主差异集中在这里）
    if f"{env_var}:=" not in text:
        text = _inject_bootstrap(text, config, skill_name=skill_name)

    return text


def _inject_bootstrap(text: str, config: dict[str, Any], *, skill_name: str) -> str:
    marker = "```bash"
    idx = text.find(marker)
    if idx < 0:
        return text
    insert_at = idx + len(marker) + 1
    env_var = config["env_var"]
    plugin_posix = config["plugin_root"].as_posix()
    block = (
        "# Qoder 宿主引导：插件根只有一个真源，技能资料与脚本都从这里解析。\n"
        f"# 安装位置变化时改这里，或先 export {env_var}。\n"
        f': "${{{env_var}:={plugin_posix}}}"\n'
        f'if [ ! -d "${{{env_var}}}/scripts" ]; then\n'
        f'  echo "ERROR: {env_var} 无效，找不到 ${{{env_var}}}/scripts" >&2\n'
        "  exit 1\n"
        "fi\n"
        f"export {env_var}\n"
        f'export SCRIPTS_DIR="${{{env_var}}}/scripts"\n'
        f'export SKILL_ROOT="${{{env_var}}}/skills/{skill_name}"\n'
    )
    return text[:insert_at] + block + text[insert_at:]


def transform_agent(text: str, *, canonical_name: str, config: dict[str, Any]) -> str:
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
    body = body.replace("${CLAUDE_PLUGIN_ROOT}", f"${{{config['env_var']}}}")
    body = body.replace("CLAUDE_PROJECT_DIR", WORKSPACE_ENV)
    for bare, canonical in _agent_names(config):
        body = body.replace(f"{config['dir_name']}:{bare}", canonical)
    body = _replace_agent_calls(body, config)
    return frontmatter + body


def assert_host_neutral(text: str, label: str, config: dict[str, Any]) -> None:
    leftovers = [token for token in forbidden_tokens(config) if token in text]
    if leftovers:
        raise SystemExit(f"转换失败：{label} 仍含宿主痕迹 {leftovers}")


# ---------------------------------------------------------------------------
# 收集产物
# ---------------------------------------------------------------------------

def build_artifacts(plugin_root: Path) -> dict[str, str]:
    """返回 {目标相对路径: 内容}（按插件根派生口径，不写盘）。"""
    config = resolve_plugin_config(plugin_root)
    artifacts: dict[str, str] = {}

    for skill_md in sorted((config["plugin_root"] / "skills").glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        converted = transform_skill(skill_md.read_text(encoding="utf-8"), skill_name=skill_name, config=config)
        assert_host_neutral(converted, f"skills/{skill_name}/SKILL.md", config)
        artifacts[f"skills/{skill_name}/SKILL.md"] = converted

    for agent_md in sorted((config["plugin_root"] / "agents").glob("*.md")):
        canonical = _agent_canonical_name(config, agent_md.name)
        if canonical is None:
            continue
        converted = transform_agent(agent_md.read_text(encoding="utf-8"), canonical_name=canonical, config=config)
        assert_host_neutral(converted, f"agents/{agent_md.name}", config)
        artifacts[f"agents/{config['agent_prefix']}/{canonical}.md"] = converted

    return artifacts


def source_commit(plugin_root: Path) -> str:
    repo = _repo_of(plugin_root)
    if repo is None:
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _repo_of(plugin_root: Path) -> Optional[Path]:
    for candidate in (plugin_root, *plugin_root.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def plugin_version(plugin_root: Path) -> str:
    for rel in (".claude-plugin/plugin.json", ".qoder-plugin/plugin.json", "plugin.json"):
        path = plugin_root / rel
        if path.is_file():
            try:
                return str(json.loads(path.read_text(encoding="utf-8")).get("version", "unknown"))
            except (OSError, ValueError):
                return "unknown"
    return "unknown"


# ---------------------------------------------------------------------------
# 落盘 / 核对
# ---------------------------------------------------------------------------

def write_artifacts(artifacts: dict[str, str], out_root: Path, *, config: dict[str, Any], qoder_home: Path) -> Path:
    for rel, content in artifacts.items():
        target = out_root / Path(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    manifest = {
        "schema_version": "webnovel-qoder-artifacts/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "plugin_dir": config["dir_name"],
        "source_plugin_version": plugin_version(config["plugin_root"]),
        "source_commit": source_commit(config["plugin_root"]),
        "plugin_root": str(config["plugin_root"]),
        "plugin_root_env": config["env_var"],
        "qoder_home": str(qoder_home),
        "files": {rel: hashlib.sha256(content.encode("utf-8")).hexdigest() for rel, content in sorted(artifacts.items())},
    }
    manifest_path = out_root / Path(*config["manifest_rel"].split("/"))
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
    parser.add_argument("--plugin-root", default=str(PLUGIN_ROOT), help="插件根（默认本仓库 webnovel-writer；可指向兄弟插件）")
    parser.add_argument("--out", default=None, help="只生成到指定目录，不写用户目录")
    parser.add_argument("--check", action="store_true", help="只核对漂移，不写文件")
    args = parser.parse_args(argv)

    host = Path(args.host).expanduser().resolve() if args.host else detect_qoder_home()
    out_root = Path(args.out).expanduser().resolve() if args.out else host
    config = resolve_plugin_config(Path(args.plugin_root).expanduser())

    skills_dir = Path(config["plugin_root"]) / "skills"
    if not skills_dir.is_dir():
        print(f"找不到技能源目录：{skills_dir}", file=sys.stderr)
        return 2

    artifacts = build_artifacts(Path(config["plugin_root"]))
    skill_count = sum(1 for rel in artifacts if rel.startswith("skills/"))
    agent_count = len(artifacts) - skill_count

    if args.check:
        drifted = check_drift(artifacts, host)
        for line in drifted:
            print(line)
        if drifted:
            print(f"漂移 {len(drifted)} 项：源已改，产物需重跑本脚本", file=sys.stderr)
            return 1
        print(f"无漂移：{len(artifacts)} 个产物与源一致（host={host}，plugin={config['dir_name']}）")
        return 0

    manifest_path = write_artifacts(artifacts, out_root, config=config, qoder_home=host)
    print(f"生成 {len(artifacts)} 个宿主产物（{skill_count} 技能 + {agent_count} agent）→ {out_root}")
    print(f"插件根变量：{config['env_var']}（技能资料不复制副本，SCRIPTS_DIR/SKILL_ROOT 都从插件根解析）")
    print(f"清单：{manifest_path}")
    print(f"来源：{config['dir_name']} {plugin_version(Path(config['plugin_root']))} @{source_commit(Path(config['plugin_root']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
