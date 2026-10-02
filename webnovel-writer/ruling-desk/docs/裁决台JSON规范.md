# 裁决台 JSON 规范

> 适用于任何「**待办卡片 → 作者裁定 → 执行**」流程的裁决台，
> 与具体书目无关。配套机器可校验文件：
> `backend/pending.schema.json`、`backend/裁决.schema.json`
> 校验器：`backend/校验pending.py`、`backend/校验裁决JSON.py`

---

## 1. 总则

### 1.1 四条原则

规范只立四条原则。具体字段名是这四条原则的**推论**，
所以换一本书、换一套领域概念，规范本身不用改。

**原则一 · 一个语义一个键**
同一种意思只允许一个键名。想改键名，就写映射，不在 schema 里并存两个。

**原则二 · 值有形状，键不带类型**
同一个键不能既装字符串又装数组。需要真假就用 `bool`，
需要「没有」就用 `null` 或省略，**不要用 `"是"` / `"否"` 表达布尔**——
它们的真值与语义相反。

**原则三 · 派生状态不进数据**
能由别的字段算出来的量（分类、优先级、是否过期、是否已合并），
一律由装载层计算，不写进数据。写卡人填的派生字段必然与事实不一致。

**原则四 · 认不出的进 `extra`**
规范管不住的字段统一放进 `extra`。界面把 `extra` 折叠成「附加信息」，
不进主区。**这样新增字段永远不需要改 schema。**

### 1.2 三条硬性禁止

| 禁止 | 后果 |
|---|---|
| 键名里带 emoji 或装饰符 | 无法写进 schema 的 `enum`，无法做别名表 |
| 用同一个键既装 `str` 又装 `list` / `bool` | 前端必须先判类型再判真假，同一字段能表示两件相反的事 |
| 界面层维护「中文字段别名表」 | 别名表是规范缺位的产物；它一旦存在，规范就永远不会收敛 |

### 1.3 受控词表的归属

词表分两类，通用性不同：

- **过程词表**——`severity`、`status`、`option`、`by`。描述裁决流程本身，
  任何书通用，写死在规范里，不开放配置。
- **领域词表**——`bucket`、`verdict`。描述这本书有什么文件、分什么类，
  **必然因书而异**。规范只给缺省值；项目在 `backend/config.json` 的
  `vocab` 里覆盖（`vocab.bucket` / `vocab.verdict` / `vocab.verdict_no_option`），
  **装载层与校验器同源以 config 为准**（2026-10-02 修：装载层原先写死桶词表，
  换书改配置不生效，新词表的值会被判「未迁移」走旧猜法）。

受控的意义不在「全」，而在「改一次、全库可见」：
换一本书 = 改一处配置，而不是改规范、改代码、改界面。

---

## 2. 术语

| 词 | 含义 |
|---|---|
| **设定源**（authority） | 本书最高的设定依据。如设定集、时间线源文。裁决时默认以它为准 |
| **正文**（text） | 作品本体。权威链：设定源 > 大纲 > 详纲/合同 > 正文 |
| **卡片**（item） | 一条待作者裁决的待办，落在 `pending[]` / `issues[]` 里 |
| **裁决**（ruling） | 作者对一张卡片（或若干同类卡片）作出的决定 |
| **候选解**（candidate） | 给作者**选**的几个方案，多选一。倾向哪个写在候选的 `recommended` 上 |
| **建议改法**（fix_hint） | **施工说明**：定了以后具体怎么改（改哪一处、写成什么）。不表达倾向 |
| **推荐意见**（recommendation） | 车道对这条问题的**立场与把握**：要不要动、为何这样取舍，带 `confidence` |

车道建议有三个载体（`candidates[].recommended` / `recommendation` / `fix_hint`），
分工见 4.6——**同一句话不得两处出现**。

---

## 3. 数据模型

裁决台有两层数据，职责必须分开：

```
卡片 item                          裁决 ruling
┌──────────────────┐              ┌──────────────────┐
│ 车道写            │  作者选      │ 作者写            │
│ candidates[] ────┼── key ──────►│ option: "A"      │
│ fix_hint         │              │ verdict: "改正文" │
│ recommendation   │              │ note: "…"        │
└──────────────────┘              │ status: …        │
                                   └──────────────────┘
        └──────────── 执行状态 status 与裁决同文件 ────────────┘
```

**规则一**：裁决只存候选解的**键**，不复制方案正文。
候选解的正本只在卡片侧（第 5 节）；裁决文件里**不允许出现 `candidates`**。
复制会产生同一份方案两处各有一个副本，改一处忘一处。

**规则二**：`candidates`（给作者选）与 `fix_hint`（施工说明）**分工不同，不许合并**。
把带选择的方案塞进 `fix_hint`，作者就只剩一条路可走；反之把单一改法
拆成假候选，是伪选项。合并与伪装都丢信息。

**规则三**：`status` 与裁决存放在**同一份文件**。
另开一份「进度基线」必然与裁决漂移——漂移一次，就会出现
「基线说处理完了、实际没处理」。

**规则四**：卡片的 `recommendation` 不进裁决。
它是给作者参考的，不是作者的结论；「车道推荐 A、作者选了 B」这件事必须留得住。

**规则五**：车道建议三个载体各管一件事，**同一句话不得两处出现**：

| 载体 | 唯一职责 |
|---|---|
| `candidates[].recommended` | 倾向哪个选项。**有候选时，「选哪个」只写在这里** |
| `recommendation` | 立场与把握：要不要动、为何这样取舍、保留意见。有候选时**不复述选项键**，理由写成对比 |
| `fix_hint` | 施工说明：具体怎么改（可条件式「若选 A 则…」）。不表达倾向、不提问题、不请裁定 |

详见 4.6。

---

## 4. 卡片（item）规范

### 4.1 形态

```json
{
  "id": "p01",
  "kind": "pending",
  "chapter": 408,
  "volume": 7,

  "bucket": "实体与ID",
  "severity": "high",
  "blocking": false,

  "title": "三个专名是否建体",
  "statement": "三个专名在设定源与本章都是承重设定，但索引库内确无对应条目。",
  "affected_entities": ["甲", "乙", "丙"],

  "evidence": {
    "authority": [{"volume": 7, "lines": [2133]}],
    "text":      [{"file": "正文/第7卷/第26章.md", "lines": [5, 9, 17]}],
    "other":     ["get-by-alias 紫临走廊 ⇒ NOT_FOUND"]
  },

  "candidates": [
    { "key": "A", "title": "三个都建体",
      "body": "甲→地点；乙/丙→宇宙。",
      "pros": ["检索性最好"],
      "cons": ["宇宙的类型需先定"],
      "recommended": true },
    { "key": "B", "title": "都不建体", "body": "只在正文里以原文保留。" }
  ],

  "fix_hint": "若选 A，在抽取产物的 entity_deltas 里补条目，勿直接写索引库。",

  "recommendation": { "text": "两案成本差集中在本章两处措辞；A 的检索收益贯穿全书，B 只在当章省事。", "by": "lane", "confidence": 0.8 },

  "why_pending": "库内确无该实体 ⇒ 属命名授权，须作者拍板，车道不得自拼 ID。",
  "impact": "不建体则相邻章节的同名字段口径会分叉。",
  "extra": { }
}
```

### 4.2 字段表

| 字段 | 中文名 | 必填 | 类型 | 约束 |
|---|---|---|---|---|
| `id` | 条目标识 | ✅ | string | 卡片内是**后缀**（如 `p01`）；全 id 由装载层拼，见下方说明 |
| `kind` | 条目类型 | | string | 四值：卡片层 `pending` \| `fulfillment`（**由所在数组决定，装载层填**）；装载层合成的条目另有 `dupid`（同物双ID）\| `text`（越改正文） |
| `chapter` | 章号 | | int | 全局章号。**由文件名派生，装载层填** |
| `volume` | 卷号 | | int | 由 chapter 推导，不手填 |
| `bucket` | 分类 | | string | 受控，见 4.5 |
| `severity` | 严重度 | ✅ | string | 受控，见 4.4 |
| `blocking` | 是否阻断 | ✅ | bool | **真 bool**。不用 `"是"` |
| `title` | 标题 | | string | ≤ 240 字，一行 |
| `statement` | 问题描述 | | string | 这条主张什么，≤ 800 字 |
| `evidence` | 证据 | | object | 三段，见 4.3 |
| `affected_entities` | 涉及实体 | | string[] | 库内实体 ID。本卡牵涉哪些实体，供 `归一ID`/`补建实体` 等裁决执行定位 |
| `candidates` | 候选解 | | array | 多选一，见第 5 节 |
| `fix_hint` | 建议改法 | | string | **施工说明**：具体怎么改，≤ 800 字。不表达倾向（见 4.6） |
| `recommendation` | 推荐意见 | | object | 立场与把握，见 4.6 |
| `why_pending` | 挂起原因 | | string | 为何挂起。**唯一的 why\* 字段** |
| `impact` | 不判的后果 | | string | 不判的后果 |
| `severity_note` | 严重度说明 | | string | 严重度的补充理由 |
| `extra` | 附加信息 | | object | 认不出的字段，**唯一允许自由的地方** |

> `id` 的构成：卡片内只写**后缀**；装载层拼成全 id
> `^(P|F)-ch\d{3,4}-{后缀}$`——`P` = 待作者裁决，`F` = 事实类，章号三位或四位。
> 写卡人若直接写了全 id，装载层原样采用，但须核对其中章号与所在文件一致。
> **分段用连字符，段内禁下划线**（下划线是库内 ID 的分隔符，
> 在 id 里混用会让「条目 id」与「实体 id」分不清）。

### 4.3 `evidence` —— 分三段

```json
"evidence": { "authority": [...], "text": [...], "other": [...] }
```

| 段 | 中文名 | 元素形态 | 渲染 |
|---|---|---|---|
| `authority` | 设定源证据 | `{volume, lines[]}` | 徽章 + 行号 chip，点击开文件 |
| `text` | 正文证据 | `{file, lines[]}` | `file:line` chip，点击开文件定位 |
| `other` | 其他证据 | 字符串 | 卡面「其他证据」区块逐条列出（检索记录、复现命令等） |

两侧同形：**一条 = 一个来源（卷 / 文件）+ 若干行**。同一卷（文件）的多个行号
合并进一条 `lines[]`，不要拆成多条重复卷号/文件名的条目；行号升序、去重。

**`label` 不是卡片字段**：显示名（`卷文件名:行`，如 `7. 余烬初燃:2133`）
由装载层从 `volume` 生成——写卡人写了也进不了数据（原则三）。

三段都直传到界面：`authority`/`text` 由装载层投影成定位键
（见 7.5.4），`other` 原样直传为载荷的 `other_evidence[]`——**没有丢弃项**。

**路径统一用正斜杠。** `file` 不接受反斜杠。

三段都可省略。**不要**用「同一段字符串里用 `；` 分隔多条证据」的做法——
它无法区分「一条证据含两个分号」和「两条证据」。

### 4.4 `severity` 受控词表

```
blocking  critical  high  major  medium  low  minor  info
```

`severity=blocking`（红档一员）与 `blocking`（流程布尔，见 4.2）是**两个独立维度**：
数据里互不蕴含，不要用一个替代另一个。优先级由装载层按二者共同**算**出，
不写进数据（原则三）。

### 4.5 `bucket` 受控词表

词表按书配置：`backend/config.json` 的 `vocab.bucket`（见 1.3），校验器以 config 为准。
本书的缺省起始值：

```
实体与ID   时间线   数值算术   设定源冲突   正文一致性   章节边界   文书体例   其他
```

新增类别改配置一处——**这正是「受控」的意义：改一次，全库可见。**

### 4.6 车道建议的三个载体

一张卡里「车道建议」有三个容器，各管一件事，**同一句话不得两处出现**：

| 载体 | 唯一职责 | 典型越界写法 |
|---|---|---|
| `candidates[].recommended` | 倾向哪个选项。**有候选时，「选哪个」只写在这里** | 又在 `recommendation.text` 复述「倾向 A」 |
| `recommendation` | 立场与把握：要不要动、为何这样取舍、保留意见。有候选时把理由写成对比，不复述选项键 | 把施工步骤写进 `text` |
| `fix_hint` | 施工说明：具体怎么改（可条件式「若选 A 则…」） | 「请裁定…」「建议在正文补半句」——提问与立场不属于这里 |

没有候选、只有一条明确改法 → 只写 `fix_hint`（不要伪装成 candidates，见第 5 节）。
对问题本身拿不准 → 写 `recommendation` 的 `confidence`。
两者同时出现时各说各的：`fix_hint` 说**怎么改**，`recommendation` 说**为什么值得改 / 代价在哪**。

#### `recommendation` 的形态

```json
"recommendation": { "text": "…", "by": "lane", "confidence": 0.8, "note": "…" }
```

| 字段 | 中文名 | 类型 | 约束 |
|---|---|---|---|
| `text` | 建议内容 | string | 立场与理由，不是施工步骤 |
| `by` | 建议方 | string | `lane`（车道）\| `author` \| `main`（主流程） |
| `confidence` | 置信度 | number | 0–1，**float 不是字符串** |
| `note` | 备注 | string | 可选 |

**`confidence` 的语义**：判定**确定性**——「我有多确信这是一个真问题、这条判断可靠」，
**不是**「我多确信该选哪个候选」。对候选的倾向只放在候选解的 `recommended` 上
（第 5 节），两者不混用；实测锚点：`confidence: 1.0` + "未判定选哪个" 是合法组合。

---

## 5. 候选解（candidate）规范

候选解的正本在**卡片侧**；裁决以 `option` 只引用键（第 3 节规则一），
**裁决文件里不出现 `candidates`**。形态：

```json
{
  "key": "A",
  "title": "三个都建体",
  "body": "甲→地点；乙/丙→宇宙。",
  "pros": ["检索性最好"],
  "cons": ["宇宙的类型需先定"],
  "recommended": true
}
```

| 字段 | 中文名 | 必填 | 类型 | 约束 |
|---|---|---|---|---|
| `key` | 选项键 | ✅ | string | `^[A-C]$`，**大写单字母** |
| `title` | 方案短标题 | ✅ | string | ≤ 40 字，一行不折行 |
| `body` | 方案正文 | ✅ | string | 完整方案，≤ 2000 字 |
| `pros` | 利 | | string[] | 每条 ≤ 120 字 |
| `cons` | 弊 | | string[] | 每条 ≤ 120 字 |
| `recommended` | 推荐标记 | | bool | **全数组至多一个为 true** |

**三条约束**

1. **标签不从正文正则抠。** 标签写进 `key` 字段落盘。
   正则抠标签必然误伤——`(a)` `①` `A：` `A（推荐）：` 四种手写习惯
   会引出一堆分支，而正常开头的专名（`Apple 基金会…`）会被剥成标签。
2. **`key` 至多 3 个候选**（A/B/C）。超过说明该拆卡片。
3. **`recommended` 是「倾向哪个」的唯一落点**：只出现在候选解上；
   有候选时，`recommendation.text` 不复述选项键（见 4.6）。

---

## 6. 裁决（ruling）规范

### 6.1 文件结构

```json
{
  "schema_version": 1,
  "updated_at": "2026-01-01T12:00:00+08:00",
  "rulings": {
    "P-ch408-01": {
      "verdict": "改正文",
      "option": "A",
      "note": "① 按 A 改 universe 那两处",
      "by": "author",
      "at": "2026-01-01T11:00:00+08:00",
      "status": "applied",
      "applied_at": "2026-01-01T12:00:00+08:00",
      "override": null,
      "index_target": null
    }
  }
}
```

加信封（`schema_version` / `updated_at` / `rulings`）是为了：
版本可判、元数据有地方放、将来加顶层字段不必动条目。
信封另有两个可选字段：`source`（卡片池路径）、`deprecated_ids`（已废弃条目 id，留痕）。

### 6.2 字段表

| 字段 | 中文名 | 必填 | 类型 | 约束 |
|---|---|---|---|---|
| `verdict` | 裁决结论 | ✅ | string\|null | 受控词表，见 6.3。**禁自由文本**；迁移期允许 `null`（须同时 `needs_reclassify=true`） |
| `option` | 选中候选键 | ✅ | string\|null | `"A"\|"B"\|"C"\|null` |
| `note` | 补充意见 | | string\|null | **选填**——作者写了就有，没写就省略。建议 ≤ 200 字，硬上限 2000 |
| `by` | 裁决方 | | string | `author` \| `main` \| `migration` |
| `at` | 裁决时间 | | string | ISO 8601 带时区 |
| `status` | 执行状态 | ✅ | string | `pending` \| `applied` \| `skipped` \| `overridden` |
| `applied_at` | 执行时间 | | string\|null | `status=applied` 时必填 |
| `override` | 改判留痕 | | object\|null | 改判留痕，见 6.5 |
| `index_target` | 索引修改目标 | | object\|null | `verdict=改索引` 时必填：`{entity_id, field}` |
| `verdict_raw` | 原始裁决留痕 | | string\|null | **迁移期临时字段**：迁移前的原始 verdict 原文，归类完成后清空 |
| `needs_reclassify` | 待归类标记 | | bool | **迁移期临时字段**：`true` = 尚未归类（此时 `verdict` 必为 `null`），主流程读到须补判 |

### 6.3 `verdict` 受控词表

**只回答一个问题：这次要改的文件在哪一侧。**

「谁对谁错」不写在这里——权威链（设定源 > 大纲 > 详纲 > 正文）
已经规定了默认顺序，作者要做的只是在默认之外**指定例外**。

词表按书配置：`backend/config.json` 的 `vocab.verdict`（不带候选的子集在
`vocab.verdict_no_option`，见 1.3）。下面的表是本书缺省。

| verdict | 含义 | 执行动作 |
|---|---|---|
| `改正文` | 按权威链改正文 | 改正文 |
| `改设定源` | 作者指定设定源有误 | 改设定源 |
| `改大纲` | 卷级大纲有误 | 改卷纲 |
| `改详纲` | 章节详纲有误 | 改详纲 |
| `改合同` | 只有章节合同字段有误 | 改合同 |
| `改索引` | 索引库字段有误 | 改索引库（须带 `index_target`） |
| `归一ID` | 同物双 ID 归并 | 出清单，作者执行 |
| `补建实体` | 索引里缺实体 | 在抽取产物的 `entity_deltas` 里补 |
| `判为无问题` | 误报，不改任何文件 | 关闭该条 |
| `无法裁决` | 作者也判不了 | 留在待决池 |
| `交主流程` | 作者授权主流程按权威链自行判断 | 主流程判断后写 `override` |

**约束**

- 必须是上述 11 个值之一，**不接受任何自由文本**
- `判为无问题` / `无法裁决` / `交主流程` 三者，`option` 必须为 `null`
- `改索引` 必须同时带 `index_target`

### 6.4 `note` 的边界

**选填**：作者没有补充就整个省略，不要用空字符串占位（原则二）。

**只放补充，不放结论、不放指令全文。**

| | |
|---|---|
| ✅ 装 | `① 改回设定源`、`说话人是 B 不是 A`、`正文不动，只改设定源那一侧` |
| ❌ 不装 | 结论（「改 canon」属于 `verdict`）、文件路径、行号定位的完整改法脚本、方案 A 的全文 |
| ❌ 不装 | **任何绝对路径**——只有一个输入框时，路径会被吸进 note |

### 6.5 `status` 与 `override`

| status | 含义 | 谁写 |
|---|---|---|
| `pending` | 已裁，待执行 | 服务端（作者在界面提交时**代写**，见 8.2） |
| `applied` | 已落盘，且**回读过** | 执行方 |
| `skipped` | 裁决为不改（`判为无问题`/`无法裁决`），无需执行 | 执行方 |
| `overridden` | 执行方依权威链改判过作者的原裁 | 执行方 |

作者裁了，但按权威链执行会撞上别的东西——**不许覆盖作者的裁定**，
另开 `override`，原值留在原处：

```json
{
  "verdict": "改正文",
  "override": {
    "from": "交主流程",
    "to": "改正文",
    "by": "main",
    "at": "2026-01-01T12:30:00+08:00",
    "reason": "改正文会与设定源另一处冲突；改设定源侧更安全"
  }
}
```

---

## 7. 渲染契约

这一节是规范的**验收标准**。满足它，界面不需要任何正则猜测或别名表。

### 7.1 卡片

| 元素 | 来源 | 渲染要求 |
|---|---|---|
| 标题 | `title` | 缺失则取 `statement` 首句截断 40 字；都空则显示 id。**不留空** |
| 分类徽章 | `bucket` | 受控词表 → 固定短标签 |
| 类别徽章 | `kind` | 四值 → 中文短标签（待裁决/履约/同物双ID/越改正文），不显示英文原值 |
| 严重度徽章 | `severity` | 受控词表 → 固定颜色（blocking/critical 红、high/major 橙、其余灰） |
| 严重度说明 | `severity_note` | 结构化区块（标题「严重度说明」），排在 impact 与 fix_hint 之间；徽章补语，不用强调色 |
| 阻断标记 | `blocking` | **`=== true` 真值比较**（不用真值判断）→ 红色「⛔ 阻断」徽章；与 `severity=blocking` 词表项是两个维度 |
| 设定源证据 | 载荷 `canon_locs[]`（源自 `evidence.authority[]`） | 徽章 + 行号 chip，点击开文件 |
| 正文证据 | 载荷 `textlocs[]`（源自 `evidence.text[]`，含本卡章） | `file:line` chip，点击定位 |
| 其他证据 | 载荷 `other_evidence[]`（源自 `evidence.other[]`） | 结构化区块「其他证据」，逐条列出，弱化色 |
| 候选列表 | `candidates[]` | 见 7.2 |
| 建议改法 | `fix_hint` | **与候选分块显示**，标题「建议的改法」 |
| 推荐 | `recommendation` | 对象渲染：`text` + `by` 徽章 + `confidence` |
| 附加信息 | `extra` | **折叠**，不展开成主区 |

### 7.2 候选列表

| 元素 | 渲染要求 |
|---|---|
| 序号键 | 固定选项占 1–N（N = `backend/config.json` 里 `verdicts` 的条数，按书配置），候选从 **N+1** 起；`0` 清空 |
| 标签 | 原样显示 `key`，**不从正文抠** |
| 标题 | `title` 加粗一行，不折行 |
| 正文 | `body` 走 markdown-lite（加粗/行内码/分点徽章） |
| 利弊 | `pros` / `cons` 两个独立小节，**不做行内分点拆分** |
| 推荐 | `recommended` 项右侧「推荐」徽章 |
| 点击 | 只写 `option: key`，**绝不写 body 进 verdict** |

### 7.3 裁决条

| 元素 | 渲染要求 |
|---|---|
| 结论徽章 | `verdict` 受控词表 → 固定中文短标签 |
| 候选选中态 | 高亮 `candidates[key == option]` |
| 补充意见 | 空则整块不渲染；超 200 字折叠 |
| 执行状态 | 徽章：待执行 / 已执行 / 主流程改判 |
| 改判痕迹 | `status=overridden` 时展开「作者原裁 → 执行方改为」 |

### 7.4 硬性禁止

| 禁止 | 原因 |
|---|---|
| 界面层维护中文字段别名表 | 规范落地后它应为空；非空说明还有没归类的键 |
| 对任意字段值做正则抠标签 | 必然误伤正常开头的专名 |
| 用真值判断 `blocking` 一类的字段 | 字符串 `"否"` 是真值，判断会反 |
| 从多个键里挑（`title`/`subject`/`topic`） | 规范只认 `title` |
| 把候选正文拼进 `verdict` | 裁决会退化成无结构的长文本 |
| 渲染层改数据 | 渲染层只做转义与排版，不改文字 |

### 7.5 接口契约

§7 说的是**一条卡片**怎么渲染；本节说**装载层与界面之间**怎么传。
同 §7 是验收标准：实现不符即为不合规。

#### 7.5.1 端点一览

| 端点 | 用途 | 返回 |
|---|---|---|
| `GET /api/meta` | 启动元数据，**只拉一次** | `verdicts` `labels` `vocab`（领域词表，`vocab.bucket` 供筛选下拉）`volumes` `canon_vols` `done` `revision` |
| `GET /api/items` | 条目，**过滤/排序/分页下推服务端** | `items` `total` `page` `size` `counts` `revision` |
| `GET /api/rulings` | 裁决表 | `?ids=a,b,c` 只回这些 id；不带 `ids` = 全量 |
| `GET /api/file` | 打开文件上下文 | `?vol=<canon卷号>` 或 `?ch=<正文章号>`，可带 `line`（可重复） |
| `POST /api/ruling` | 存一条裁定 | `ok` `total` `revision` `id` `ruling`（**保存后的该条**）；冲突时 **409** |
| `POST /api/ruling {"action":"reload"}` | 重扫条目，不必重启服务 | `ok` `revision` `items` |

首屏 = `/api/meta` ＋ 一页 `/api/items` ＋ `/api/rulings?ids=<本页 id>`，
三个请求。翻页与改筛选 = 一页 `/api/items` ＋ `/api/rulings?ids=`，两个请求。

🟢 **位置记忆**（2026-10-02）：当前条目 id 存本地，刷新/重开按它定位回原卡——
在当前筛选第一页内命中则**零额外请求**；否则一次 `locate` ＋ 目标页
（**不放宽筛选**：启动恢复不该动作者视图，定位不到就停在第一条）。
🔴 入场时保存 watcher 会先于恢复写入「第一条」，所以恢复必须用**入场快照**
（`BOOT_POS`），不能即读 localStorage——否则永远恢复第一条（实测翻车）。

🔴 **固定元数据不得随每页下发**（2026-10-02 实测：原先 `verdicts`/`labels`/
`volumes`/`canon_vols`/`done` 跟着每页重发，翻页/改筛选都在重复传输）。
`counts` 例外——它是全量口径统计且随保存变化，留在每页响应里。

🔴 **裁决表按页取**：界面只关心当前可见的条目，一次拉全表是浪费。
`ids` 上限 = `size` 上限的 1.5 倍（size≤200 ⇒ ids≤300），超限返回 400。
界面侧按 id 缓存，**已取过的 id 不重复拉**（含「查过但无裁决」也算取过）。

🔴 `POST /api/ruling` 响应带 `ruling`（保存后的该条记录），界面据此更新
本地状态，**不要再为这一条发一次回读**。

#### 7.5.2 `/api/items` 查询参数

| 参数 | 取值 | 缺省 |
|---|---|---|
| `page` | ≥0 的整数 | `0` |
| `size` | 1–200，**超 200 夹到 200** | `50` |
| `bucket` | 4.5 词表内 | 全 |
| `kind` | `pending` `fulfillment` `dupid` `text` | 全 |
| `vol` | 卷号 | 全 |
| `ruled` | `1` 已裁／`0` 未裁 | 全 |
| `status` | `only` `no` `v` `moot` `hide` `sub` `nosub` `cf` `todo` `need` `ruled` | 全 |
| `prio` | 逗号分隔，如 `P0,P1` | 全 |
| `q` | 全文子串 | — |
| `hide_sub` `hide_no_issue` `only_unruled` | `1` 开 | 关 |
| `sort` | `prio`（缺省）`id` `rev` | `prio` |

**不带任何查询参数 = 全量返回**（旧行为，向后兼容，此时无 `total` 键）。
这一点是有意保留的：旧客户端与离线脚本仍可用。

`counts` 是**全量口径**（不随筛选变），与界面顶栏显示的统计同源。
对齐时踩过的两个坑，实现时别再错：

- 「已裁」取**裁决表里所有**已裁的 id，**不限于当前台面上的条目**。
  按当前列表算会偏低（实测 173 → 162，差 11 条：有些已裁的条目
  已被合并或移出台面）。
- 「需我拍板」判据是**裁决表里没有这条 id**，不是「没有有效裁定」。
  id 存在但 `verdict`/`option` 全为空串的记录，算作者已碰过。
  两种口径差 1 条。

#### 7.5.3 `revision` 与乐观锁

`revision` 是一个整数，**装载数据或裁决表发生变更就 +1**。

- `GET` 类端点都带 `revision`。
- `POST /api/ruling` 的请求体可带 `revision`：
  - **不带** ⇒ 接受（兼容旧客户端）
  - **带了且与服务端一致** ⇒ 接受
  - **带了且不一致** ⇒ **409**，响应 `{"ok":false,"error":…,"revision":<现值>}`，
    **不写入**

这是给 8.1 单写者装的门闩。单写者意味着服务端整份写回裁决表，
任何外部改动都会被无声冲掉——曾经因此抹掉过 8 条裁决。
有了版本检查，冲突会被**显式报出来**，而不是悄悄覆盖。

界面收到 409 必须提示「数据已更新，请重载后重试」，
**不许**自动重试或静默覆盖。

#### 7.5.4 `/api/items` 条目载荷字段

一条 `items[]` 元素 = **卡片字段直通** ＋ **装载层投影/派生**。键名以本表为准，
界面不认别名、不做二次改名。

**直通**（键名、形状与卡片一致，见 4.2）：

```
id  kind  chapter  volume  bucket  severity  blocking  title  statement
affected_entities  candidates  fix_hint  recommendation  why_pending
impact  severity_note  extra
```

（`id` 是装载层拼好的全 id；`candidates` 为 5 节对象形态。）

**投影**（`evidence` 三段 → 定位用键，唯一入口是 4.3）：

| 载荷键 | 来源 | 形状 |
|---|---|---|
| `canon_locs` | `evidence.authority[]` | `{vol, lines[]}[]`，同卷合并；文件显示名前端用 `canon_vols[vol]` 拼，点击开文件用 |
| `textlocs` | `evidence.text[]` ＋散文「第N章第L行」，**含本卡章** | `{ch, lines[]}[]`，按章合并、本卡章排最前；本卡章渲染在「正文」组，其余按「第NNNN章」分组 |
| `textfile` | 本卡章正文文件 | string \| null |
| `other_evidence` | `evidence.other[]` | `string[]`，原样直传 |

> 2026-10-02 三处删除：`canon[]`（`canon_locs[].label` 的投影）、
> 投影里的逐行条目与 `label`（`{vol,line,label}`→`{vol,lines[]}`）、
> `textlines`（本卡章的行并入 `textlocs` 的同一条目）——同一语义只留一个键。

**派生**（原则三：装载层计算，不进数据、不进裁决）：

| 载荷键 | 语义 |
|---|---|
| `prio` | 由 `severity` ＋ `blocking` 算出：`P0`（blocking 或 critical）`P1`（high/major）`P2`（medium）`P3`（low/minor）`P9`（无 severity） |
| `bucket_guessed` | `true` = bucket 非词表值，退回旧猜法（未迁移卡片） |
| `merged_into` | 本条目被合并时指向代表条目 id；空串 = 未合并 |
| `stale` | 引文在当前正文 0 命中（正文不引用≠问题不成立） |
| `stale_v` | `stale` 但另有 canon 依据 ⇒ 可独立核验 |
| `moot` | 同物双 ID 已归一，本条的问题对象已不存在 |
| `conflict` | 子代理结论与作者裁定不一致 |
| `sub_done` `sub_verdict` `sub_lane` `sub_applied` | 车道判定回灌（sidecar 读取，永不入库） |
| `inner` | **遗留**：旧格式的章内序号提示；规范卡片恒为 `null`，新代码勿依赖 |

**空缺形态**：字符串字段用空串、数组用 `[]`、对象/未知用 `null`——
界面不得依赖「键不存在」判空。合成条目（`dupid`/`text`）只保证上表键存在，
直通字段可全为空。

#### 7.5.5 硬性禁止

| 禁止 | 原因 |
|---|---|
| 界面层维护中独立的一份筛选词表 | 词表以 `backend/config.json`（装载层经 `meta.vocab` 下发）与本规范为唯一来源（1.3）；下拉显示**完整词表**，不只看数据里出现过的值 |
| 界面层维护一份统计口径的副本 | `counts` 由服务端给；两份算法必然漂移 |
| 界面层全量重算排序 | 排序键是 `severity`+`blocking` **算**出来的（原则三），归装载层 |
| 卡片按整表返回 | 条目量会涨到上万条，传输与解析线性恶化 |
| 服务端改写 `evidence` 里的路径分隔符 | `file` 一律正斜杠（4.3） |

---

## 8. 写入规则

### 8.1 单写者

**裁决文件只能有一个写入方。**

界面服务端持有裁决表的内存副本，保存时**整份写回**。
任何来自它之外的写入——手工编辑、脚本、迁移——都会在它下次保存时被覆盖。

所以：

- 要改裁决，**走服务端接口**，不要直接改文件
- 批量脚本改裁决前，必须先确认服务端**已停止**
- 写入采用「临时文件 + 原子替换」，不做原地覆盖

### 8.2 各角色可写字段

| 角色 | 可写 | 禁止 |
|---|---|---|
| 界面（作者） | `verdict`、`option`、`note`、`at` | 不得写 `status`、`applied_at`、`override` |
| 执行方（主流程） | 全部 | **不得直接改作者的 `verdict`**；要改走 `override` |
| 迁移工具 | 全部 | 只做机械可判的映射，判不了的标 `needs_reclassify`，**不猜** |

界面不直接写文件（8.1 单写者）：作者在界面提交时，由服务端**代写**
`status=pending` 与 `by=author`；`applied_at`、`override` 只由执行方写。

---

## 9. 校验

两个校验器都**只用标准库**，退出码 `1` 表示有 ERROR，可直接挂 pre-commit / CI。

```
python -X utf8 backend/校验pending.py            # 扫全部卡片
python -X utf8 backend/校验pending.py --chapter 408
python -X utf8 backend/校验pending.py --json
python -X utf8 backend/校验pending.py --strict   # 顶层规范外字段也判 ERROR

python -X utf8 backend/校验裁决JSON.py           # 扫裁决文件（默认路径读 backend/config.json 的 out）
python -X utf8 backend/校验裁决JSON.py --json
```

### ERROR（数据损坏，必须修）

- `verdict` 不在受控词表 / `severity` 不在受控词表 / `bucket` 不在受控词表
- 缺必填字段 / 出现规范外字段（`extra` 除外；卡片顶层字段在过渡期默认仅计数，加 `--strict` 才判 ERROR）
- `blocking` 不是真 bool
- `evidence` 不是三段结构 / `evidence.text.file` 含反斜杠 /
  `evidence.authority` 条目缺 `volume` 或 `lines`（非空整数数组）
- `recommendation` 不是对象
- 候选解：`key` 非大写单字母 / `key` 重复 / `recommended` 多于一个 /
  缺 `key`/`title`/`body` / 出现规范外字段
- `判为无问题`/`无法裁决`/`交主流程` 却带 `option`
- `改索引` 缺 `index_target`
- `status=applied` 但 `applied_at` 为空 / 反之
- 键名含 emoji
- `note` 含绝对路径 / 超长

### WARN（已知待办，不阻塞）

- 候选解方案正文偏长
- `note` 偏长（> 200 字，可能混入了方案正文）
- `verdict=null` 且 `needs_reclassify=true`（尚未归类，主流程补判）
- `by` 缺失
- 卡片缺 `title`
- `fix_hint` 是 `"False"` / `"None"` 之类占位字面量（迁移垃圾，schema 查不出）
- `evidence.authority` 出现旧形 `line` 或 `label`（应改 `{volume, lines[]}`，label 装载层生成）
- `evidence.text` 同一 `file` 拆成多条（应并入一条 `lines[]`，见 4.3）

---

## 10. 子代理提示词（prompt）

写卡与执行都是子代理干的活。子代理**没有会话上下文**，派发时把下面整段
发给它即可，不需要另附文档。提示词是规范的**投影**：规范改了，这里跟着改；
两者冲突时以规范为准。

### 10.1 写卡子代理（车道）

```text
你是「裁决台」的写卡子代理。任务：扫描指定章节，产出待作者裁决的卡片。
输出：一个 JSON 数组，每个元素是一张卡片，除此之外不要输出任何文字。

【卡片格式】
- id：只写后缀（如 "p01"），分段用连字符，段内禁下划线；全 id 由装载层拼。
- 必填三个：id、severity、blocking。blocking 必须是 true/false，
  禁止 "是"/"否"/"yes"/"no" 之类的字符串。
- severity 只允许：blocking / critical / high / major / medium / low / minor / info
- bucket 只允许（词表按书配置，派发时以本项目的为准）：
  实体与ID / 时间线 / 数值算术 / 设定源冲突 / 正文一致性 / 章节边界 / 文书体例 / 其他
- title ≤ 240 字，一行；statement ≤ 800 字，写清「这条主张什么」。
- 本卡牵涉的库内实体写 affected_entities（字符串数组，如 ["wu_yuyan"]）。
- 尽量给 evidence，三段：
    authority: [{"volume": 卷, "lines": [行号]}]                （设定源证据；同卷多行合并，
                                                                  label 不写——装载层生成）
    text:      [{"file": "正文/…md", "lines": [行号]}]          （正文证据，路径用正斜杠）
    other:     ["检索记录 / 复现命令"]                           （字符串数组）
  禁止把多条证据塞进一个字符串里用分号隔开。
- 要作者「选」的方案放 candidates：2–3 个对象，每个必含
  key（"A"/"B"/"C" 大写单字母）、title（≤40 字短标题）、body（≤2000 字完整方案），
  可选 pros / cons（字符串数组）与 recommended（布尔，全组至多一个 true）。
  标签写进 key 字段，禁止混在 body 正文里。
- 建议只有三个载体，各管一件事，同一句话不得两处出现：
  · 选哪个 → 候选解的 recommended（有候选时只写这里）；
  · 要不要动 / 为何取舍 → recommendation {"text","by","confidence"}，
    有候选时把理由写成对比，不复述「倾向 A」；confidence 指「多确信这是真问题」；
  · 定了以后具体怎么改 → fix_hint（≤800 字符串，可写「若选 A 则…」）。
    不表达倾向、不提问题、不请裁定。
- 没有候选、只有一条明确改法时，只写 fix_hint，不要伪装成 candidates。
- 为什么挂起写 why_pending，不判的后果写 impact。
- 上列词表外的值一律不要发明；确实装不下的字段全部放进 extra（对象）。

【四条禁止】
1. 键名里禁止 emoji 或装饰符——它们进不了受控词表。
2. 同一个键禁止有时装字符串、有时装数组或布尔。
3. 派生状态（优先级、是否过期、是否已合并）禁止写进卡片，装载层会算。
4. 你只产出卡片，绝不写裁决文件；裁决由作者作出。

拿不准是不是问题 → 不报，或 severity 用 "info"。
```

### 10.2 执行子代理

```text
你是「裁决台」的执行子代理。输入：裁决文件中 status="pending" 的条目。
对每条：
1. 只按 verdict 执行。verdict 是受控词表：
   改正文 / 改设定源 / 改大纲 / 改详纲 / 改合同 / 改索引 /
   归一ID / 补建实体 / 判为无问题 / 无法裁决 / 交主流程
2. verdict="改索引" 必带 index_target: {"entity_id": …, "field": …}，按它改。
3. 执行落盘并**回读确认**后，写 status="applied" 与 applied_at
   （ISO 8601，带时区）。
4. 作者的裁定与权威链冲突时，**禁止改写 verdict**——写 override
   {"from": 原值, "to": 新值, "by": "main", "at": …, "reason": "…"}，
   并把 status 置为 "overridden"。
5. 判为无问题 / 无法裁决 → status="skipped"，不改任何文件。
6. 裁决文件单写者：动手前确认界面服务端已停止，或改走服务端接口；
   写入用临时文件 + 原子替换。
每次执行前重读裁决文件——你上次离开后它可能已被服务端更新。
```

---

## 11. 落地顺序

规范上线按这个顺序，每步可独立验证：

1. **先上校验器**——不改任何数据结构，立刻能量出存量问题
2. **新数据按规范写**——车道与工具从此刻起只写规范形态
3. **改界面**——去掉正则猜测与别名表，改读受控词表
4. **存量迁移**——机械可判的自动映射，判不了的标 `needs_reclassify` 人工补
5. **删除别名表**——此时它应该已经空了；非空就是还有没归类的键
6. **删除正则兜底**——此时它应该已经用不上了