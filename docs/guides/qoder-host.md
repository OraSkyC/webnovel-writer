# Qoder 宿主适配说明

同一份技能/agent/runtime 源，额外给 Qoder 生成一套可直接使用的产物。Claude Code 那条路径完全不变。

设计口径与上游的多宿主草案一致（`docs/architecture/multi-agent-adaptation-spec-2026-06-05.md`）：
业务真源只有 runtime 与书项目数据，宿主差异收敛到「宿主表达」一层，由脚本生成，不手抄。

## 装了什么

| 组件 | 源（事实源） | 生成产物落点 |
|------|--------------|--------------|
| 8 个技能 | `webnovel-writer/skills/*/SKILL.md` | `~/.qoder-cn/skills/webnovel-*/SKILL.md` |
| 4 个 agent | `webnovel-writer/agents/*.md` | `~/.qoder-cn/agents/webnovel/webnovel-*.md` |
| 生成清单 | —— | `~/.qoder-cn/webnovel-writer/qoder_artifacts.json`（源版本、commit、逐个文件哈希） |
| runtime / references / templates / dashboard | **不复制** | 技能内所有路径从插件根 `webnovel-writer/` 解析 |

技能正文里会注入一段引导，装在本机插件根的绝对路径上，可用环境变量覆盖：

```bash
: "${WEBNOVEL_PLUGIN_ROOT:=D:/OraSkyC/Project/CandySweet/CaDt-Plugins/webnovel-writer/webnovel-writer}"
if [ ! -d "${WEBNOVEL_PLUGIN_ROOT}/scripts" ]; then
  echo "ERROR: WEBNOVEL_PLUGIN_ROOT 无效，找不到 ${WEBNOVEL_PLUGIN_ROOT}/scripts" >&2
  exit 1
fi
export WEBNOVEL_PLUGIN_ROOT
export SCRIPTS_DIR="${WEBNOVEL_PLUGIN_ROOT}/scripts"
export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/<技能名>"
```

路径解析只在 `--project-root` 之外多认两样东西（见 `scripts/host_compat.py`）：

- 工作区指针 `.qoder/.webnovel-current-project` 与 `.claude/.webnovel-current-project` 都能读；写入时**两边一起更新**，避免两个宿主跟着不同指针走
- 用户级 registry 与全局 `.env` 在 `~/.qoder-cn/webnovel-writer/` 和 `~/.claude/webnovel-writer/` 都会找，已存过数据的那家优先；`WEBNOVEL_AGENT_HOME` 可强制指定

## 安装

```bash
# 1) 依赖（含 dev 测试插件，仓库已列）
python -m pip install -r requirements.txt

# 2) 生成并安装到 Qoder 用户目录
python -X utf8 webnovel-writer/scripts/generate_qoder_artifacts.py

# 只想先看转换结果、不碰用户目录
python -X utf8 webnovel-writer/scripts/generate_qoder_artifacts.py --out D:/tmp/wn-qoder
```

装完在 Qoder 里直接可用：`/webnovel-init`、`/webnovel-plan`、`/webnovel-write`、`/webnovel-review`、
`/webnovel-query`、`/webnovel-learn`、`/webnovel-dashboard`、`/webnovel-doctor`，
子代理名为 `webnovel-context-agent` / `webnovel-reviewer` / `webnovel-data-agent` / `webnovel-deconstruction-agent`。

## 验证

```bash
# 转换没漏改宿主痕迹、产物里的 CLI 子命令都已注册
python -m pytest webnovel-writer/scripts/tests/test_generate_qoder_artifacts.py

# 产物与源是否一致（有漂移非零退出）
python -X utf8 webnovel-writer/scripts/generate_qoder_artifacts.py --check

# 从书项目里跑一次链路（技能 bootstrap 原样可用）
cd <书项目根>
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "$PWD" preflight
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "$PWD" project-status --format summary
```

验收看四件事：技能列表里出现 8 个 `webnovel-*`；agent 列表里 4 个 `webnovel-*` 且 ready；
`preflight` 的 `skill_root`/`project_root` 都是 OK；多书工作区下 `where` 能跟着指针落到具体那本。

## 升级流程

合并上游后**必须重跑生成器**，否则 Qoder 侧继续吃旧文案：

```bash
git fetch upstream --tags
git merge upstream/master
python -m pytest                                   # 先确认 runtime 没被合坏
python -X utf8 webnovel-writer/scripts/generate_qoder_artifacts.py
python -X utf8 webnovel-writer/scripts/generate_qoder_artifacts.py --check
```

新增技能或 agent 时改 `generate_qoder_artifacts.py` 里的 `AGENT_NAME_MAP`（agent 必须加 `webnovel-` 前缀再注册，
否则会和其他包的 agent 撞名）。新增 CLI 子命令不用改测试——守卫从 `data_modules/webnovel.py` 的
`sub.add_parser("x")` 派生。

## 未做与边界

| 项 | 状态 | 替代做法 |
|----|------|----------|
| Hooks（`session_start.py` 开场状态、`guard_runtime_write.py` 写入拦截） | 未移植 | 开场状态跑 `project-status --format summary`；写入保护靠 runtime 侧 `write-gate` 与 `chapter-commit` 门禁，技能流程本身必经 |
| 裁决台 `/cadt-ruling`（另一个插件） | Qoder 侧未装 | 写章技能在待裁卡出现时按源里的分支退回 `AskUserQuestion`；需要台子逐条裁时把 `cadt-ruling` 也走一遍同样的生成流程 |
| Dashboard | 可用 | `/webnovel-dashboard` 起的是本地只读服务，与宿主无关 |
| 全量 pytest 在本仓库路径下 | 1 例失败 | `test_webnovel_skill_flow_..._stubbed_vector_model` 的临时文件全路径 261 字符撞 Windows 260 上限；同一 commit 在短路径（如 `D:\wnx`）通过。要么把 checkout 放短路径，要么改 `scripts/conftest.py` 的 basetemp 位置 |
| 覆盖率门槛 90% | 87.3% | 缺口主要在 `prose_lint.py` 与几个投影写手，属 fork 自带欠账，合并前后一致 |

## 双宿主共存注意

- 两边共用同一个 checkout 当插件根，Qoder 侧不需要额外拷贝一份 runtime
- 指针对两个宿主是同一本当前书（写入即同步），但 **registry 只有一份生效**：`~/.qoder-cn/webnovel-writer/` 一旦建出来就会成为首选家目录，`~/.claude` 那份继续作为兜底被读
