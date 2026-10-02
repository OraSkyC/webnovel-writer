---
name: webnovel-ruling
description: 启动作者裁决台，逐条看证据、点选裁定；裁定结果落盘成可执行指令，由 ruling-executor 施工。
allowed-tools: Bash Read
---

# 作者裁决台（Ruling Desk）

## 目标

把插件产出的待裁卡（`.story-system/commits/*.commit.json` 里 `meta.status=rejected` 的
`disambiguation_result.pending` 与 `fulfillment_result.issues`）变成**逐条可裁、落盘可执行**的台子：

- 作者在浏览器里看证据 → 选裁定 → 保存（裁决表：`<项目根>/.webnovel/tmp/作者裁决.json`）；
- 裁完由 `ruling-executor` 按裁定施工，并重跑 chapter-commit 让状态从 rejected 翻回 accepted。

它是**工具**，不是流程闸门：写章流程会在需要时自动调用本技能，作者也可以随时手动开
（回看历史裁、攒几章一起裁）。台子只读项目数据，**只写那一份裁决 JSON**。

## 何时调用

- 主流程在 Step 3 遇到无法定点修复的 blocking 问题、Step 5.2 得到 rejected 时调用；
- 作者直接说「开裁决台 / 裁一下 / 看看待裁」时调用；
- 作者说「裁完了」时，接着拉起 `ruling-executor`（见「收尾」）。

## 执行步骤

### Step 1：解析项目根

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export DESK_DIR="${CLAUDE_PLUGIN_ROOT}/ruling-desk"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

`DESK_DIR` 下应有 `backend/ruling_server.py`、`backend/config.example.json`、`docs/裁决台JSON规范.md`。

### Step 2：检查构建产物是否随包

```bash
if [ ! -f "${DESK_DIR}/frontend/dist/index.html" ]; then
  echo "ERROR: 缺少前端构建产物 ${DESK_DIR}/frontend/dist/index.html（dist 应随插件打包，确认插件完整安装）" >&2
  exit 1
fi
```

单端口托管：后端自己发页面，**不需要 npm / vite / 额外端口**。

### Step 3：首次运行写一份每书配置（可选但建议）

没有 `<PROJECT_ROOT>/.webnovel/ruling-desk.json` 时，写一份最小配置（不写也能跑，用通用缺省）：

```json
{
  "port": 8851,
  "canon_dir": "",
  "canon_files": {},
  "vocab": { "bucket": ["实体与ID", "时间线", "数值算术", "设定源冲突", "正文一致性", "章节边界", "文书体例", "其他"] }
}
```

字段含义见 `ruling-desk/backend/config.example.json`（随包，带注释）。**只有当这本书确实有
卷级设定源文件时**才填 `canon_dir` / `canon_files`；否则 canon 定位为空，属正常降级。

### Step 4：启动服务

```bash
python -X utf8 "${DESK_DIR}/backend/ruling_server.py" --project-root "${PROJECT_ROOT}" --port 8851
```

后台运行（`run_in_background` 或 `&`）。纯标准库，**不装任何依赖**。

启动后确认接口可用：`GET /api/meta` 返回 200；`GET /api/items?page=0` 能拿到条目。
再把访问地址告诉作者：`http://127.0.0.1:8851`。

### Step 5：卡校验门（分流，不是拦截）

```bash
python -X utf8 "${DESK_DIR}/backend/校验pending.py" --project-root "${PROJECT_ROOT}" --json
```

退出码 0 = 无 ERROR。**有 ERROR 时按性质分流，不要重跑 reviewer**（插件硬规则：审查只跑一轮）：

| 门的结果 | 去向 |
|---|---|
| 0 ERROR | 引作者去台子里裁（Step 4 的地址） |
| 形状错（缺必填 / 词表外 / id 不合规） | 判给**产出侧就地修补**（消歧与履约产物重写），不重跑 reviewer |
| 内容级（证据不足、改不改） | 本来就该作者裁 ⇒ 进台子 |

### 收尾：裁完之后

作者说「裁完了」时：

1. 先校验裁决表（只执行合法的裁定）：
   ```bash
   python -X utf8 "${DESK_DIR}/backend/校验裁决JSON.py" --project-root "${PROJECT_ROOT}" --json
   ```
2. 用 `Agent` 工具起 `ruling-executor`，让它按裁定施工并回写执行记录。
3. executor 施工完**重跑写章流程 Step 5.2 的 chapter-commit 命令**（原样）——
   pending 清空即 accepted，projection 自动推进 committed，流程才能继续。

## 失败处理

| 症状 | 处理 |
|---|---|
| 端口占用 | 换 `--port`（并同步更新 Step 3 配置里的 `port`） |
| dist 缺失 | 确认插件完整安装（dist 随包） |
| 启动日志出现「backend/config.json 的 root=… 与本次项目根 … 不一致 ⇒ 整份忽略」 | 正常：那是随包示例在自我隔离。想启用就改项目侧 `.webnovel/ruling-desk.json` |
| 启动日志出现「verdict 不在受控词表 vocab.verdict 内」 | 作者的自定义裁定词表与受控词表不一致，executor 会拒执行 ⇒ 把 `vocab.verdict` 对齐成实际在用的选项 |
| `/api/items` 为空 | 本项目还没有 `meta.status=rejected` 的章；跑一次写章流程即可 |

## 边界

- 台子**只读**项目数据（commit json、`index.db`、git diff），**只写**裁决 JSON；
- 规范（卡片字段、受控词表、三个载体分工、ERROR/WARN 清单）在
  `${DESK_DIR}/docs/裁决台JSON规范.md`，改判定逻辑先读它。