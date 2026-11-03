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

# 兄弟插件（裁决台）走同一条规则，规范名由它自己的 agents/name-map.json 决定
python -X utf8 webnovel-writer/scripts/generate_qoder_artifacts.py --plugin-root <cadt-ruling 路径>
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

## 运行时钩子（开场状态 + 写入拦截）

Claude 侧那两个钩子脚本装的是同一份文件（不复制、不改逻辑），注册走独立脚本：

```bash
python -X utf8 webnovel-writer/scripts/install_qoder_hooks.py            # 安装/更新（自动留备份）
python -X utf8 webnovel-writer/scripts/install_qoder_hooks.py --check    # 漂移核对，非零退出
python -X utf8 webnovel-writer/scripts/install_qoder_hooks.py --remove   # 只摘本插件条目
```

写进 `<host>/settings.json` 的 `hooks` 键，形状与 Qoder 自带插件的 `hooks/hooks.json` 一致
（`SessionStart` / `PreToolUse` + `matcher` + `{"type":"command","name","command","timeout"}`）。
合并只动带 `webnovel-writer-` 前缀 `name` 的条目，其它键与其它宿主编排的钩子原样保留；
换插件根或解释器路径会报漂移（防止 settings 里留着旧安装位置）。

**生效要重启 Qoder**。装载后 Qoder 传给钩子的 stdin 字段名尚未实测，脚本对
`tool_name|toolName|tool`、`tool_input|toolInput|input`、`file_path|path|filename` 都做了兼容，
并留了探针：`WEBNOVEL_HOOK_DEBUG=1` 时把收到的原始载荷落一行到
`~/.qoder-cn/webnovel-writer/hook_probe.jsonl`（只读旁路，不改变拦截判定，默认不写）。

两处宿主差异带来的修复（Claude 侧同样受益）：

- **开场钩子改跑 `preflight`**（实测 0.4s），不再跑 `project-status`——后者在 705 章的书上实测
  25-30 秒，作为会话开场必然超时静默；同时先 `where` 解析不到书项目就整个钩子静默退出，
  非书项目（写代码、做 PPT 的会话）不会再被塞「未命名项目 / blocking: missing .webnovel/state.json」。
- **待裁计数改真口径**：原先数 `commits` 目录里的文件总数（等于每章提交都算待裁，61/726 这种数没有信息量），
  现在只数 `meta.status == rejected` 或 `disambiguation_result.pending` 非空的章。

## 宿主加载语义（实测，含一条否定结果）

| 组件 | 装完是否立刻可用 | 实测依据 |
|------|------------------|----------|
| 技能（`<host>/skills/*/SKILL.md`） | **是**，当场进可用技能列表 | 装完 8 个 `webnovel-*` 与 9 个 `cadt-ruling-*` 后，本会话技能列表即时出现 |
| agent（`<host>/agents/<组>/*.md`） | **否，要重启 Qoder** | `list_agents` 立刻显示 4 个 `webnovel-*` 与 2 个 `cadt-ruling-*` 且 `enabled/ready`；但用新会话（`create_chat_session`，同一 Qoder 进程内）探针调用 `Agent(subagent_type="webnovel-reviewer")` 仍报 `Unknown agent type`，可用列表只有 general-purpose / Explore / Plan / 三个【示例】agent |

结论要点：**Agent 工具的类型表是应用进程级快照，新开会话不算刷新**。名字、UUID、`组/名` 三种写法都不认。
`list_agents` 的 `ready` 只证明磁盘上的定义被登记，不证明当前进程能调用它。

重启前的替代做法（已实测可用）：用 `general-purpose` 承载 agent 定义文件，让它在第一步 Read
`~/.qoder-cn/agents/webnovel/webnovel-reviewer.md` 并把它当角色规范执行——内容链（路径约定、
runtime 命令、输出格式）能完整验通，只是不走原生注册名。

## 未做与边界

| 项 | 状态 | 替代做法 |
|----|------|----------|
| Hooks（`session_start.py` 开场状态、`guard_runtime_write.py` 写入拦截） | 已装到用户级 settings.json，**生效需重启**；stdin 字段名待首次实测（探针已就位） | 手工替代：`webnovel.py preflight` / `project-status --format summary`；写入保护本来也在 runtime 侧 `write-gate` 与 `chapter-commit` 门禁里 |
| 裁决台 `/cadt-ruling`（另一个插件） | 已生成 9 技能 + 2 agent（`--plugin-root ../cadt-ruling`），技能当场可用；**agent 调用要重启** | 未重启前写章技能会按源里的分支退回 `AskUserQuestion` |
| Dashboard | 可用 | `/webnovel-dashboard` 起的是本地只读服务，与宿主无关 |
| 全量 pytest 在深路径 checkout 下 | 已修 | `conftest.py` 的每测试目录名改为「测试名截断 48 字符 + 12 位随机后缀」，临时文件全路径回到预算内；`.tmp/pytest` 这个仓库内根保持不变（它存在的理由是避开 WindowsApps Python 的 0o700 临时目录） |
| 覆盖率门槛 90% | 已达标（90.02%，`pytest` 退出码 0） | 补了 prose-lint（0%→97%）、路由覆盖、doctor、断点账本四块测试；`rag_adapter`/`webnovel.py` CLI 分支仍是主要欠账 |

## 双宿主共存注意

- 两边共用同一个 checkout 当插件根，Qoder 侧不需要额外拷贝一份 runtime
- 指针对两个宿主是同一本当前书（写入即同步），但 **registry 只有一份生效**：`~/.qoder-cn/webnovel-writer/` 一旦建出来就会成为首选家目录，`~/.claude` 那份继续作为兜底被读
