# -*- coding: utf-8 -*-
"""作者裁决台（临时）—— localhost 网页，逐条看证据、点按钮下裁定。

🔴 起因 2026-09-29：pending 1477 条散在 detail/why_pending/topic 等十几个字段里，
   人工翻 JSON 不可行 ⇒ 做个台子让作者直接看证据、直接选。
🔴 2026-09-29 加：每条带**时间线定位**（canon 文件:行号 + 正文 文件:行号），
   作者要求「最好把对应的时间线的位置也带上」。

**只读项目数据，只写一份裁决 JSON**：
  读  .story-system/commits/*.json、.webnovel/index.db（只读）、git diff（只读）
  写  .webnovel/tmp/作者裁定_20260929.json   ← 唯一产物

启动：python -X utf8 .webnovel/tmp/_p4_ruling_web.py
访问：http://127.0.0.1:8848
"""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 🔴 2026-09-30 改为可配置，供其它小说项目复用。
# 🟢 2026-10-02 插件化（阶段一）：配置分层 + CLI 参数，**本仓行为不变**。
#   口径与两个校验器共用 backend/ruling_config.py（含「一份配置只对属于它的
#   那本书生效」的归属判定——踩过的坑见该文件开头）。
#   优先级（高 → 低）：命令行 → 环境变量（键名大写）
#                    → <项目根>/.webnovel/ruling-desk.json（每书配置，插件主入口）
#                    → backend/config.json（本仓开发用 / root 不匹配就整份忽略）
#   项目根来源：--project-root > ROOT 环境变量 > config.json 的 root > 「backend 上两级」。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                      # 让 ruling_config 能被 import 到
    sys.path.insert(0, _HERE)
import ruling_config                            # noqa: E402  （须在 _HERE 之后）


def _parse_cli():
    """命令行参数。**容忍被当模块 import**：用 parse_known_args，
    剩下的参数（pytest 之类）不当错误。"""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--project-root", default=None,
                    help="小说项目根目录（默认取配置/环境变量/本仓上两级）")
    ap.add_argument("--port", type=int, default=None, help="监听端口")
    ap.add_argument("--frontend-dist", default=None, help="前端构建产物目录")
    ns, _ = ap.parse_known_args()
    return ns


CLI = _parse_cli()

# 🔴 项目根要先定下来，才能找到项目里的配置；而 backend/config.json 的 root
#   本身就是候选之一，所以这里读它的原文（归属判定交给 ruling_config.load）。
ROOT = (CLI.project_root or os.environ.get("ROOT")
        or ruling_config.backend_cfg().get("root")
        or os.path.dirname(os.path.dirname(_HERE)))
CFG = ruling_config.load(ROOT, cli={"port": CLI.port,
                                   "frontend_dist": CLI.frontend_dist})
PROJECT_CFG_PATH = (os.path.join(ROOT, ".webnovel", "ruling-desk.json")
                    if os.path.isfile(os.path.join(ROOT, ".webnovel", "ruling-desk.json"))
                    else None)
for _note in CFG.notes:
    print("⚠ " + _note, flush=True)


def _cfg(key, default=None):
    return CFG.get(key, default)


OUT = _cfg("out", os.path.join(ROOT, ".webnovel", "tmp", "作者裁决.json"))
PAGE_FILE = os.path.join(_HERE, "index.html")


def _autodist():
    """没配 frontend_dist 时自动认领构建产物：backend 同级或上级的 frontend/dist。
    🟢 插件里是 ruling-desk/frontend/dist ⇒ 起一个进程就能访问，
    用户机器不需要 npm 跑 vite dev（此前完全没有单端口托管，见 do_GET）。"""
    for rel in (os.path.join("..", "frontend", "dist"),
                os.path.join("frontend", "dist")):
        p = os.path.abspath(os.path.join(_HERE, rel))
        if os.path.isfile(os.path.join(p, "index.html")):
            return p
    return None


FRONTEND_DIST = _cfg("frontend_dist") or _autodist()
PORT = int(_cfg("port", 8848))
LOCK = threading.Lock()

CANON_DIR = _cfg("canon_dir", "")


def _load_desk_info():
    """裁决台组件自己的版本清单（ruling-desk/desk.json）。
    🟢 组件版本与插件版本**各自独立**：插件版本随插件发布走，这里只管
    「代码 + 规范 + 校验器 + 前端」这一坨——它们必须一起走同一个版本，
    否则反馈问题时说不清手上是哪个台子。"""
    p = os.path.join(os.path.dirname(_HERE), "desk.json")
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return {"name": d.get("name") or "ruling-desk",
                "version": d.get("version") or "0.0.0",
                "spec_version": d.get("spec_version") or ""}
    except Exception as e:
        return {"name": "ruling-desk", "version": "?", "spec_version": "",
                "error": str(e)[:80]}


DESK = _load_desk_info()


def _canon_files():
    """卷号 → canon 文件名。留空 = 本书没有设定源（功能降级，不崩）。
    🟢 插件化（阶段一 A）：原先这 13 个卷名硬编码在源码里，换书必错。三个来源：
      ① `canon_files`：{"1": "1. 基石时代.md", …}，显式配置，本仓用它；
      ② `canon_autoscan: true` ⇒ 扫 <canon_dir> 下 ^(\\d+)\\.\\s*.+\\.md$ 自动生成；
      ③ 都没有 ⇒ 空 dict（没有 canon 的书走这条，所有 canon 定位为空）。
    `canon_exclude` 列出要排除的卷号——作者可能有意不把某些卷当权威链
    （本仓第 14 卷 `14. 番外-日记星辰.md` 就在派单禁读清单里）。"""
    out = {}
    raw = _cfg("canon_files") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(str(k).strip())] = str(v)
            except (TypeError, ValueError):
                continue
    if not out and _cfg("canon_autoscan", False) and CANON_DIR \
            and os.path.isdir(CANON_DIR):
        ex = set()
        for x in (_cfg("canon_exclude") or []):
            try:
                ex.add(int(str(x).strip()))
            except (TypeError, ValueError):
                continue
        for name in sorted(os.listdir(CANON_DIR)):
            m = re.match(r"^(\d+)\.\s*.+\.md$", name)
            if m and int(m.group(1)) not in ex:
                out[int(m.group(1))] = name
    return out

# 🟢 重复项合并（作者 2026-10-01：「有些问题重复的可以合并一下」）
#
# 全量扫 2480 条 pending 后：同章 19 对候选**逐对核过全是假阳性**
# （ch386 两条 subject 都是 guchen，但一条是年龄、一条是药价；共享 token ≠ 同题）。
# 真正同议题的只有跨章 2 组，且逐字核对 title/topic/options 全同：
#   ① P-ch442-ch442-p02 ⇄ P-ch443-ch443-p02「li-jian / li_jian（黎坚）双 ID 未归并」
#      两章各报一次，subject/category/conflict_type/severity 逐字相同
#   ② P-ch603-V9C-AM-011 ⇄ P-ch604-V9C-AM-011「ST-225 立项动作跨两章各写一遍白板」
#      连 title/topic/options/description/evidence 都一致
#
# 做法：保留**第一条**，后续同组条目不生成卡片（不是删数据，只是不上台面）。
# 组的顺序按 dict 插入序 ⇒ 第一条即最早出现的那条。
MERGE_GROUPS = _cfg("merge_groups", [
    ["P-ch442-ch442-p02", "P-ch443-ch443-p02"],
    ["P-ch603-V9C-AM-011", "P-ch604-V9C-AM-011"],
])
# 重复项 → 其代表条目的 id
MERGE_INTO = {}
for _g in MERGE_GROUPS or []:
    for _dup in list(_g)[1:]:
        MERGE_INTO[_dup] = _g[0]


def auto_merge(items):
    # 🟢 保守自动去重（2026-10-01）：硬编码 MERGE_GROUPS 只补已知组，重扫一次就
    #    冒出新组（实测 4 组、6 条：黎坚双ID ch525/526、今天早上 ch571-573、
    #    V10-ENTITY-DUALID ch715-717、V10-MOTHER-IDENTITY ch716/717）。
    # 判据：归一 title（≥12 字符）+ bucket + **本卡章的行集合**（textlocs 里
    #    ch == chapter 的那条）全等。
    # 🔴 title 必须 ≥12：拿 subject 当标题的「li_zhaoping / xu_zhisheng /
    #    liu-xingjun」（≤11 字符）可能是同主体的**不同问题**，绝不能并；
    #    空标题条目同理（它们可能共享某句引文却是两件事）。
    seen = {}
    for x in items:
        if x.get("merged_into"):
            continue
        # 🔴 text 类按「同文件多个改动块」生成 -0/-1/-2：标题都只是文件名、
        #    行集合都空，但改前/改后是**不同改动**，绝不能按标题并（实测翻车）
        if x.get("kind") == "text":
            continue
        title = re.sub(r"\s+", " ", str(x.get("title") or "")).strip()
        if len(title) < 12:
            continue
        # 🔴 2026-10-02：textlines 已并入 textlocs——判据取本卡章那条的行集合。
        #    若退回空集，键会退化成「title+bucket」，同题不同行的问题会被错并
        #    （实测 1642 → 1629，多并 13 条）。
        own = next((t.get("lines") for t in (x.get("textlocs") or [])
                    if t.get("ch") == x.get("chapter")), None)
        key = title + "\u0000" + str(x.get("bucket") or "") + "\u0000" + \
              ",".join(str(n) for n in sorted(set(own or [])))
        if key in seen:
            x["merged_into"] = seen[key]
        else:
            seen[key] = x["id"]
# 🔴 2026-10-02 起由配置驱动（阶段一 A）：原先这 13 个卷名硬编码在源码里，
#    换书必错。三个来源与开关见 _canon_files()。
#    本仓把原值原样搬进 backend/config.json 的 canon_files ⇒ 行为逐位不变。
#    **14 故意不加** —— `14. 番外-日记星辰.md` 在派单禁读清单里
#    （见记忆 shikong-oswalk-sweeps-forbidden-file），定位栏不能把它当权威链展示。
CANON_FILE = _canon_files()
# 🟢 卷表：扫 正文/ 目录实况生成，不手填。
#   这张表曾只写 7/8/9/10 四卷，ch284（卷5）因此算不出正文路径、
#   textfile 返回 None，卡片上的「正文」chip 成了空按钮（作者 2026-10-02 报）。
#   章数上限也不能硬编码「卷7 当 80、其余当 90」——卷5 只有 31 章。
#   下面这份由 _scan_volumes() 在启动时扫出来，随 /api/items 的 volumes 下发，
#   前端只消费，不自己猜。
VOL_DIR = {}
VOL_BASE = {}
VOL_COUNT = {}
# 🟢 2026-10-02 插件化（阶段一 C）：正文**平铺**布局（正文/第NNNN章*.md，没有卷目录）
#    时的「全局章号 → 正文文件」直查表；非平铺时为空。
FLAT_FILES = {}

VOL_RE = re.compile(r"^第(\d+)卷(?:[-—－_ ].*)?$")
FLAT_CH_RE = re.compile(r"^第0*(\d+)章")


def _scan_volumes():
    """扫 正文/ 目录：卷目录名、卷首全局号、章数 ⇒ 三张表。
    卷首全局号按章数累加（卷1 从 ch1 起）。
    🔴 两个坑（2026-10-02 实测）：
      ① 必须按**卷号数值**排序。sorted(os.listdir(...)) 是字符串序，
         「第10卷」会排在「第2卷」前面，累加出来的卷首号整段错位
         （实测卷5 被算成 base=355，ch284 因此指向卷4 的文件）。
      ② 章数上限不能硬编码「卷7 当 80、其余当 90」——卷5 只有 31 章。
    🟢 阶段一 C 的两条通用化：
      · 卷目录名放宽成 `第N卷` / `第N卷-xxx` / `第N卷 xxx` 都认
        （原先强制带短横线且后面有字，别人的书未必这么命名）；
      · 没有卷目录但正文平铺时，合成「一卷」并建 FLAT_FILES 直查表——
        卷筛选降级但**不崩**（原先 os.listdir 直接抛、正文件永远找不到）。
    """
    d, b, n = {}, {}, {}
    base = 1
    names = []
    root = os.path.join(ROOT, "正文")
    FLAT_FILES.clear()
    try:
        entries = os.listdir(root)
    except OSError:
        return d, b, n
    for name in entries:
        m = VOL_RE.match(name)
        if m:
            names.append((int(m.group(1)), name))
    for v, name in sorted(names):                      # ← 按卷号数值排
        cnt = len(glob.glob(os.path.join(root, name, "第*.md")))
        if not cnt:
            continue
        d[v], n[v], b[v] = name, cnt, base
        base += cnt
    if d:
        return d, b, n
    for p in sorted(glob.glob(os.path.join(root, "第*章*.md"))):
        m = FLAT_CH_RE.match(os.path.basename(p))
        if m:
            FLAT_FILES[int(m.group(1))] = p
    if FLAT_FILES:
        d[1], n[1], b[1] = "", len(FLAT_FILES), 1
    return d, b, n

# ---------------------------------------------------------------- 分类规则
# 🟡 bucket_of() 的正则分类表——**只服务于「卡片还没迁移」的老格式兜底**
#    （迁移后每张卡的 bucket 都是受控词表里的值，不走这里）。
#    🔴 2026-10-02 修复：改 BUCKETS_VOCAB 时把这张表整个删掉了，
#    而 bucket_of() 仍在遍历它 ⇒ 表外 bucket 一进来就 NameError。
#    （模块能 import、只有走到那条分支才炸，所以一直没被发现。）
BUCKETS = [
    ("canon自身矛盾", re.compile(r"canon\s*(?:自身|内部|自相矛盾|互斥)|canon\s*两处|两处口径")),
    ("canon↔正文冲突", re.compile(r"canon\s*(?:未|无|没有|缺|未见|未载|0\s*命中)|与\s*canon\s*冲突")),
    ("同物双ID/别名污染", re.compile(r"双\s*ID|别名|alias|归一|同物|ID\s*沿用")),
    ("详纲↔正文冲突", re.compile(r"详纲|大纲|合同|垫片|CBN|CPN|CEN|HOOK")),
    ("章节边界/归属", re.compile(r"章节边界|边界错位|吞掉|属于下一章|承接")),
    ("年龄/司龄基线", re.compile(r"年龄|岁|生日|生年|享年|司龄")),
    ("数字/算术", re.compile(r"数字|算术|量级|加总|对不上")),
    ("时间线/时刻", re.compile(r"时间锚点|时刻|钟点|几点|日期|跨度")),
    ("人物状态/生死", re.compile(r"生死|去世|牺牲|失踪|死亡|已逝|活着")),
    ("命名归属/建体", re.compile(r"命名授权|未建体|建体|应挂|命名归属")),
]

# 🟢 规范 v1 的 bucket 受控词表（docs/裁决台JSON规范.md 4.5）。
#    卡片迁移时已归到这 8 项；表外说明卡片还是旧格式，走 bucket_of 猜。
#    🔴 名字不能叫 BUCKETS —— 上面那张是 bucket_of() 用的正则分类表，
#    同名会把那张表整个覆盖掉（2026-10-02 实测，害得 backend 起不来）。
# 🟢 规范 1.3：bucket 是**领域词表**，按书配置在 config.json 的 vocab.bucket。
#    校验器早就以 config 为准，装载层这里却写死 8 桶——换书改配置不生效：
#    卡片里新词表的值会被判「未迁移」，走旧猜法并标 bucket_guessed=true。
#    同源读 config；config 没有 vocab.bucket 才退回本仓库缺省值。
#    ORDER 保配置顺序（筛选下拉按它排），VOCAB 是成员判定用的集合。
_BUCKETS_DEFAULT = ["实体与ID", "时间线", "数值算术", "设定源冲突",
                    "正文一致性", "章节边界", "文书体例", "其他"]
BUCKETS_ORDER = list((_cfg("vocab") or {}).get("bucket") or _BUCKETS_DEFAULT)
BUCKETS_VOCAB = set(BUCKETS_ORDER)

# 规范 v1 字段表（docs/裁决台JSON规范.md 4.2）的中文名——**默认值**，
# 不必写进 config.json；配置里有同名字段则以配置为准（可按书改译名）。
FIELD_LABELS_DEFAULT = {
    "id": "条目标识",
    "kind": "条目类型",
    "chapter": "章号",
    "volume": "卷号",
    "bucket": "分类",
    "severity": "严重度",
    "blocking": "是否阻断",
    "title": "标题",
    "statement": "问题描述",
    "evidence": "证据",
    "affected_entities": "涉及实体",
    "candidates": "候选解",
    "fix_hint": "建议改法",
    "recommendation": "推荐意见",
    "why_pending": "挂起原因",
    "impact": "不判的后果",
    "severity_note": "严重度说明",
    "extra": "附加信息",
}

# config.labels：{"override": {字段: 译名}, "extra": {extra键: 译名}}
#   override —— 覆盖上面的默认值（默认空 = 就用规范表的）
#   extra    —— extra 里规范表没定义的键，只能靠这里给中文名
_LBL = _cfg("labels", {}) or {}
LABELS = dict(FIELD_LABELS_DEFAULT)
LABELS.update(_LBL.get("override") or {})
LABELS.update(_LBL.get("extra") or {})


# 固定裁定选项：可在 config.json 的 "verdicts" 里按书配置（不同书词表不同），
# 缺省回落到 DEFAULT_VERDICTS。
# 🟢 2026-10-02 阶段一 I：缺省词表改成**通用 11 项**，与 校验裁决JSON.py 的
#   vocab.verdict 缺省值逐字一致——原先这里是本书的 8 个自定义选项，于是
#   「没配 verdicts 的书」写出的裁决必然过不了自己的校验器（门形同虚设）。
#   本仓 config.json 显式配了 verdicts，行为不变。
DEFAULT_VERDICTS = [
    "改正文", "改设定源", "改大纲", "改详纲", "改合同", "改索引",
    "归一ID", "补建实体", "判为无问题", "无法裁决", "交主流程",
]
# 🔴 作者书的 8 个自定义选项留在 memory 与 config.json 里，但**不要**再当缺省值：
#   自定义选项是「每书词表」，通用缺省必须与受控词表同源，否则门会误判。
_AUTHOR_VERDICTS_20260930 = [
    "以正文/源文为准（改 canon 或详纲）",
    "以 canon 为准（改正文）",
    "改详纲/大纲",
    "同物双 ID 归一",
    "补建实体 / 命名授权",
    "不修，存疑",
    "交给我（主流程）再判",
    "按补充意见修改",
]
VERDICTS = _cfg("verdicts", DEFAULT_VERDICTS)
# 候选解的键号从 N+1 起（N = 固定选项数）；前端同规则推导，见 useDesk.js 的 CAND_BASE
CAND_KEY_OFFSET = len(VERDICTS) + 1


def pick_candidates(p):
    """取本条自带的候选解 ⇒ 规范形态 [{key, title, body, pros, cons, recommended}]。

    🟢 规范 v1（docs/裁决台JSON规范.md 第 5 节）：候选解在 candidates[] 里，
       key/title/body/pros/cons/recommended 各自独立，**不再从正文正则抠标签**。
       装载层只做两件事：键去重（位次补号）、字段兜底——不压扁成 [键, 文本]，
       界面按对象字段消费，与卡片落盘形态同名同形。
    🟡 旧格式的 CAND_KEYS + RX_CAND_TAG 兜底已删（落地顺序第 ⑥ 步）：
       卡片已全部迁移，旧读法是死代码；留着它等于把「没迁的条目」这件事藏起来。
       卡片若不是规范形态，这里返回空，右栏没有候选解——这是**可见的缺信号**，
       比猜一个标签出来更安全。
    """
    cs = p.get("candidates")
    if not isinstance(cs, list) or not cs:
        return []
    out, seen = [], set()
    for c in cs:
        if not isinstance(c, dict):
            continue
        key = str(c.get("key") or "").strip()
        if not key:
            continue
        if key in seen:                       # 标签重复：位次补号，不覆盖
            i2 = 2
            while "%s%d" % (key, i2) in seen:
                i2 += 1
            key = "%s%d" % (key, i2)
        seen.add(key)
        out.append({
            "key": key,
            "title": str(c.get("title") or "").strip(),
            "body": str(c.get("body") or "").strip(),
            "pros": [str(x) for x in c["pros"]] if isinstance(c.get("pros"), list) else [],
            "cons": [str(x) for x in c["cons"]] if isinstance(c.get("cons"), list) else [],
            "recommended": c.get("recommended") is True,
        })
    return [c for c in out if c["title"] or c["body"]]

def chapter_text_file(ch):
    """全局章号 → 正文文件路径（卷内号 = 全局 − 卷首）。
    🟢 阶段一 C：平铺布局（正文/第NNNN章*.md）走 FLAT_FILES 直查。"""
    if FLAT_FILES:
        try:
            return FLAT_FILES.get(int(ch))
        except (TypeError, ValueError):
            return None
    for vol, base in VOL_BASE.items():
        if base <= ch < base + VOL_COUNT[vol]:
            inner = ch - base + 1
            for p in glob.glob(os.path.join(ROOT, "正文", VOL_DIR[vol],
                                             "第%04d章*" % inner)):
                return p
    return None


def canon_line_count(vol):
    p = os.path.join(CANON_DIR, CANON_FILE[vol])
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


_LINE_CACHE = {}


def canon_total(vol):
    if vol not in _LINE_CACHE:
        _LINE_CACHE[vol] = canon_line_count(vol)
    return _LINE_CACHE[vol]


_CHLINES = {}


def chapter_line_count(ch):
    """🔴 正文真实行数——用来把 canon 行号从「正文行号」里剔出去。
       实测踩坑：ch393 的证据文本里同时含 canon 7:1505 与正文 L136，
       旧正则 `L\\d{1,4}` 把 1505/1529/1579 也当成正文行号收进来了，
       而那章**只有 495 行** ⇒ 三个都是假的。"""
    if ch in _CHLINES:
        return _CHLINES[ch]
    p = chapter_text_file(ch)
    n = 0
    if p:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                n = sum(1 for _ in fh)
        except OSError:
            n = 0
    _CHLINES[ch] = n
    return n


# 出处定位正则：extract_locs 从卡片文本里找「canon 7:2133」「正文 L427」这类串。
# 🔴 这些与候选解无关，不属于落地顺序第 ⑥ 步要删的「标签正则兜底」。
RX_CANON_COLON = re.compile(r"(?:canon\s*)?(\d{1,2})\s*[:：]\s*(\d{3,5})")
RX_CANON_L = re.compile(r"[Ll]\s*(\d{3,5})")
RX_VOL_NEAR = re.compile(r"canon\s*(\d{1,2})")
RX_TEXT_L = re.compile(r"[Ll]\s*(\d{1,4})\b")
RX_CH = re.compile(r"第\s*0*(\d{1,4})\s*章")

# 🟢 2026-10-02 任务五：散文形态的行号引用（迁移后大量证据只剩散文）。
#   ① RX_CH_L —— 「卷4 第0057章第485-489行」「第0064章第517-523行」
#   ② RX_CH_L2 —— 「第0057章 L485」这种
#
# 🔴 **章号是「卷内号」不是全局号**——这是实测定的，不是猜的：
#   ch284 的卡写「卷4 第0057章第485-489行」并引了原文「郑阿姨问呢。」
#   · 读法A（全局 ch57，第0057章-佩雷斯的信.md，仅 184 行）⇒ 引文 0 命中
#   · 读法B（卷4 内第 57 章 ⇒ 全局 ch244，第0057章-琴键上的秘密基地.md）
#     ⇒ 「郑阿姨问呢」逐字命中 L485，「郑阿姨也不问」在 L487 —— 与卡的
#     写法「第485-489行」逐行吻合。
#   ⇒ 卷内号成立。凡无「卷N」前缀的，按本卡所属卷的卷内号读；读不出就不收。
RX_CH_L = re.compile(
    r"(?:卷\s*(\d{1,2})\s*)?第\s*0*(\d{1,4})\s*章\s*第\s*(\d{1,5})"
    r"(?:\s*[-–—~至]\s*(\d{1,5}))?\s*行")
RX_CH_L2 = re.compile(
    r"(?:卷\s*(\d{1,2})\s*)?第\s*0*(\d{1,4})\s*章\s*[Ll]\s*(\d{1,4})")


def _ch_from_vol_inner(vol, inner):
    """(卷号, 卷内章号) → 全局章号；越界返回 None。"""
    if vol not in VOL_BASE:
        return None
    base, cnt = VOL_BASE[vol], VOL_COUNT[vol]
    if not 1 <= inner <= cnt:
        return None
    return base + inner - 1


def extract_chapter_lines(t, own_ch):
    """从散文里抽「第N章第L行」，范围校验后返回 {全局章号: [行号]}。

    🔴 只收**过得了范围校验**的：行号须落在该章真实行数内。
       散文里「L1042」这种 canon 行号混进来是常态（老坑，见 chapter_line_count），
       所以这里一律按目标章的行数判，而不是按本卡章数。
    """
    out = defaultdict(set)
    if not t:
        return out
    own_vol = vol_of(own_ch) if own_ch else None
    for rx in (RX_CH_L, RX_CH_L2):
        for m in rx.finditer(t):
            v = int(m.group(1)) if m.group(1) else None
            inner = int(m.group(2))
            if v is None:
                v = own_vol
            # 「卷N」与本卡所属卷不符时以文本里的为准，但两读法都试一遍取活的
            cands = []
            for vv in ([v] if v else []) + ([own_vol] if own_vol else []):
                ch2 = _ch_from_vol_inner(vv, inner)
                if ch2 and ch2 not in cands:
                    cands.append(ch2)
            if not v and own_ch:
                cands.append(own_ch if inner == own_ch else None)
            for ch2 in [c for c in cands if c]:
                n = chapter_line_count(ch2)
                if not n:
                    continue
                if rx is RX_CH_L:
                    lo, hi = int(m.group(3)), (int(m.group(4))
                                              if m.group(4) else int(m.group(3)))
                else:
                    lo = hi = int(m.group(3))
                if not (1 <= lo <= n and 1 <= hi <= n and lo <= hi):
                    continue
                if hi - lo > 40:            # 别把整章当成一个区间
                    lo = hi
                out[ch2].update(range(lo, hi + 1))
                break
    return out

def extract_locs(ch, p, t):
    """从一条 pending 的全部文本里抽出 canon 与正文的定位。
    🔴 p 是该条 pending 的**原始 dict**——正文行号要从它的证据字段里取，
       早先我用了个全局 FLD_RAW 收集器却从没往里写东西，等于永远取到（空转）。
    🔴 2026-09-30 加 canon_locs：与 canon 字符串一一对应的结构化定位
       {vol, line, label}，前端点 chip 时据此调 /api/file 打开文件上下文。"""
    canon, cmeta, text = [], [], []
    nlines = chapter_line_count(ch)

    def _addcanon(v, ln):
        ref = "%s:%d" % (CANON_FILE[v].replace(".md", ""), ln)
        if ref not in canon:
            canon.append(ref)
            cmeta.append({"vol": v, "line": ln, "label": ref})

    # --- canon：优先「卷:行」 ---
    for m in RX_CANON_COLON.finditer(t):
        v, ln = int(m.group(1)), int(m.group(2))
        if v in CANON_FILE and 1 <= ln <= canon_total(v):
            _addcanon(v, ln)

    # --- canon：「canon7 L1065」这类 ---
    for m in RX_VOL_NEAR.finditer(t):
        v = int(m.group(1))
        if v not in CANON_FILE:
            continue
        seg = t[m.start():m.start() + 40]
        for mm in RX_CANON_L.finditer(seg):
            ln = int(mm.group(1))
            if 1 <= ln <= canon_total(v):
                _addcanon(v, ln)
        break                      # 只取第一个 canonN，避免误抓

    # --- canon 文件名直接出现「7. 余烬初燃.md」+ 行号 ---
    for m in re.finditer(r"(\d{1,2})\.\s*[^`\n]{2,10}?\.md[^\d]{0,12}(\d{3,5})", t):
        v, ln = int(m.group(1)), int(m.group(2))
        if v in CANON_FILE and 1 <= ln <= canon_total(v):
            _addcanon(v, ln)

    # --- 正文行号：只在**证据类**字段里取（那里才有 L1234 这种真行号）---
    for key in ("text_evidence", "chapter_evidence", "evidence", "mentions_in_chapter",
                "mentions", "mention", "evidence_lines", "lines", "location",
                "canon_evidence", "quote"):
        v = p.get(key)
        if not v:
            continue
        sval = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        got = False
        for m in RX_TEXT_L.finditer(sval):
            ln = int(m.group(1))
            # 🔴 必须落在该章**真实行数**内，否则就是 canon 行号被误收
            if 1 <= ln <= (nlines or 2000):
                ref = "L%d" % ln
                if ref not in text:
                    text.append(ref)
                got = True
        if got:
            break
    # 章内文件名（第0010章 → 卷内 10）
    inner = None
    for m in RX_CH.finditer(t):
        v = int(m.group(1))
        if 1 <= v <= 120:
            inner = v
            break
    # 🟢 任务五：散文里的「卷N 第NNNN章第L行」⇒ 跨章定位（textlocs）。
    #    本卡的 text[] 表达不了「别的章的行」，所以单开一路。
    tlocs = []
    for ch2, lns in sorted(extract_chapter_lines(t, ch).items()):
        for ln in sorted(lns):
            tlocs.append({"ch": ch2, "line": ln,
                          "label": "第%04d章:L%d" % (ch2, ln)})
    return canon, text[:12], inner, cmeta, tlocs


FLD_RAW = {}          # 已不再使用，留着提醒：曾用它却从不写入 ⇒ 静默空转


def vol_of(ch):
    """章号 → 卷号。**由启动扫描的卷表反查**，不再硬编码区间（阶段一 B）。
    🔴 2026-09-29 的教训：原来只写了 7-10 卷的起点，其余落到 (1,1)，
       ch284/ch223 全被标成卷1；后来补的整表也只对这一本书成立
       （卷1=1-85 卷2=86-152 …），换书必错。
    卷表未建（导入即被调用）或超出范围 ⇒ 返回 0（= 无卷信息）。"""
    if not ch or not VOL_BASE:
        return 0
    for vol in sorted(VOL_BASE):
        if VOL_BASE[vol] <= ch < VOL_BASE[vol] + VOL_COUNT.get(vol, 0):
            return vol
    return 0


def bucket_of(t):
    for name, rx in BUCKETS:
        if rx.search(t):
            return name
    return "其他"


def load_pending():
    """🔴 2026-09-29 作者令「放宽过滤，全部回灌」。

    原实现有两道过窄的闸，导致**子代理 852 条里只有 12 条能标上**：
      ① 只读 disambiguation_result.pending ⇒ 812 条 F-ch*（fulfillment
         issues）压根不进台面；
      ② DECIDE 正则要求含「建议/改为/须作者/取舍」等词 ⇒ 20 条措辞不同
         的 P- 条目被过滤（如 P-ch304 写「三条修法互斥」）。
    ⇒ 改为：pending 与 fulfillment 都收，DECIDE 降级为**排序信号**而非
       过滤条件（只影响 prio，不影响收录）。
    """
    out = []
    for path in glob.glob(os.path.join(ROOT, ".story-system/commits/chapter_*.commit.json")):
        m = re.search(r"chapter_(\d+)\.commit", os.path.basename(path))
        if not m:
            continue
        ch = int(m.group(1))
        try:
            j = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if (j.get("meta") or {}).get("status") != "rejected":
            continue

        # 🔴 两类条目都收：P-（disambiguation pending）+ F-（fulfillment issues）
        for src in ("disambiguation_result", "fulfillment_result"):
            if src == "disambiguation_result":
                items = (j.get(src) or {}).get("pending") or []
            else:
                items = (j.get(src) or {}).get("issues") or []
            for i, p in enumerate(items):
                if not isinstance(p, dict):
                    continue
                text = " ".join(str(v) for v in p.values() if isinstance(v, str))
                # 🔴 ID 与车道对齐：F-chN-xxx 用 fulfillment 里的 issue_id/id
                if src == "disambiguation_result":
                    pid = "P-ch%d-%s" % (ch, p.get("id") or p.get("pending_id") or i)
                    kind = "pending"
                else:
                    pid = "F-ch%d-%s" % (ch, p.get("issue_id") or p.get("id") or i)
                    kind = "fulfillment"
                # 🟢 规范 v1：卡片已是规范形态，左栏只渲染 extra（认不出的字段的
                #    唯一去处）。规范字段由顶栏/专区块各��其位，不在这里重复。
                # 🟡 旧格式兜底：条目还没迁移时，仍按老办法全量搬进 extra。
                SPEC_KEYS = {
                    "id", "pending_id", "issue_id", "kind", "chapter", "volume",
                    "bucket", "severity", "blocking", "title", "statement",
                    "evidence", "candidates", "fix_hint", "recommendation",
                    "why_pending", "impact", "severity_note", "extra",
                }
                ex = p.get("extra")
                if isinstance(ex, dict):
                    src_extra = ex
                else:
                    src_extra = {k: v for k, v in p.items()
                                if k not in ("id", "pending_id", "issue_id")}
                extra = {}
                for k, v in sorted(src_extra.items()):
                    if k in ("id", "pending_id", "issue_id"):
                        continue
                    if isinstance(v, str) and v.strip():
                        extra[k] = v[:900]
                    elif isinstance(v, list):
                        extra[k] = json.dumps(v, ensure_ascii=False)[:900]
                    elif isinstance(v, (int, float, bool)):
                        extra[k] = v
                canon, tlines, inner, cmeta, tlocs = extract_locs(ch, p, text)
                sev = (p.get("severity") or "").strip()
                # 🟢 规范 v1（docs/裁决台JSON规范.md 4.2）：卡片已是规范形态，
                #    字段直读，不再猜、不再抠、不再靠别名表。
                if not sev and not p.get("bucket") and not p.get("evidence"):
                    # 🟡 旧格式兜底（尚未迁移的条目）：只在这里保留旧读法
                    sev = (p.get("level") or "").strip()
                bucket = p.get("bucket") if p.get("bucket") in BUCKETS_VOCAB else None
                guessed = False
                if not bucket:
                    # 表外 = 卡片还没迁移（或自己写了表外值）：退回旧猜法并标注
                    bucket, guessed = bucket_of(text), True
                ev = p.get("evidence") if isinstance(p.get("evidence"), dict) else None
                # 🟢 规范 4.3 三段证据之三：other[]（检索记录/复现命令）——
                #    此前装载层把它整段丢掉、界面无处可见；现在原样直传，
                #    由卡面「其他证据」区块渲染。
                other_ev = []
                # 🟢 2026-10-02 载荷投影统一为合并形态（作者裁：label/逐行条目都是重复）：
                #    canon_locs = [{vol, lines[]}]，textlocs = [{ch, lines[]}]（含本卡章），
                #    不再有逐行 {vol,line,label}、不再有独立 textlines；
                #    显示名由前端拼（canon 用 canon_vols[vol]，正文用「第NNNN章」）。
                cv_map, _tl = {}, {}                       # vol/ch → set(行号)
                if ev:
                    for a in ev.get("authority") or []:
                        if not isinstance(a, dict):
                            continue
                        v = a.get("volume")
                        lns = a.get("lines")
                        if not isinstance(lns, list):
                            # 旧形 {volume, line, label} 兼容读取
                            lns = [a["line"]] if isinstance(a.get("line"), int) else []
                        cv_map.setdefault(v, set()).update(
                            n for n in lns if isinstance(n, int))
                    # 🔴 text[] 条目按「文件所属章」归并：本卡的章与别的章各立条目。
                    #    文件名「第N卷 第M章」换算成全局章号（见 _ch_from_vol_inner）。
                    for a in (ev.get("text") or []):
                        if not isinstance(a, dict):
                            continue
                        ch2 = None
                        if re.search(r"第(\d{4})章", str(a.get("file") or "")):
                            vv = re.search(r"第(\d{1,2})卷", str(a.get("file") or ""))
                            iv = re.search(r"第(\d{1,4})章", str(a.get("file") or ""))
                            if vv and iv:
                                ch2 = _ch_from_vol_inner(int(vv.group(1)),
                                                          int(iv.group(1)))
                        _tl.setdefault(ch2 or ch, set()).update(
                            n for n in (a.get("lines") or []) if isinstance(n, int))
                    inner = None
                    other_ev = [s for s in (ev.get("other") or [])
                                if isinstance(s, str) and s.strip()]
                else:
                    # 旧路径（尚未迁移的条目）：正则抽出的 'L123' 属本卡章；
                    # canon 正则结果（{vol,line,label}）并入同一容器
                    for s in tlines:
                        try:
                            _tl.setdefault(ch, set()).add(int(str(s).lstrip("Ll")))
                        except ValueError:
                            pass
                    for m in cmeta:
                        if isinstance(m, dict) and isinstance(m.get("line"), int):
                            cv_map.setdefault(m.get("vol"), set()).add(m["line"])
                # 散文「第N章第L行」抽出的定位（两条路径共用；与 evidence 撞行则并集）
                for t in tlocs:
                    if isinstance(t, dict) and isinstance(t.get("line"), int):
                        _tl.setdefault(t.get("ch") or ch, set()).add(t["line"])
                _order = ([ch] if ch in _tl else []) + \
                         sorted(c for c in _tl if c != ch)
                cmeta = [{"vol": v, "lines": sorted(cv_map[v])} for v in cv_map]
                textlocs = [{"ch": c, "lines": sorted(_tl[c])} for c in _order]
                out.append({
                    "id": pid,
                    "kind": kind, "chapter": ch, "volume": vol_of(ch),
                    "bucket": bucket, "bucket_guessed": guessed,
                    "severity": sev,
                    "blocking": p.get("blocking"),
                    # 🔴 DECIDE 降级为排序信号：命中与否都收录，只影响 prio
                    "prio": ("P0" if (p.get("blocking") is True or sev in
                                      ("blocking", "critical"))
                             else "P1" if sev in ("high", "major")
                             else "P2" if sev in ("medium",)
                             else "P3" if sev in ("low", "minor")
                             else "P9" if sev else "P9"),
                    "title": (p.get("title") or p.get("subject") or p.get("topic")
                              or p.get("question") or p.get("statement")
                              or p.get("summary") or "")[:200],
                    "statement": p.get("statement") or "",
                    "why_pending": p.get("why_pending") or "",
                    "impact": p.get("impact") or "",
                    "severity_note": p.get("severity_note") or "",
                    "fix_hint": p.get("fix_hint") or "",
                    "recommendation": p.get("recommendation"),
                    "affected_entities": p.get("affected_entities") or [],
                    "canon_locs": cmeta,
                    "textlocs": textlocs, "inner": inner,
                    "other_evidence": other_ev,
                    "textfile": chapter_text_file(ch),
                    "extra": extra,
                    # 🔴 子代理裁决回灌：本条自带的候选解
                    "candidates": pick_candidates(p),
                    # 🟢 重复项合并：本条若被合并，则指向代表条目（见 MERGE_GROUPS）
                    "merged_into": MERGE_INTO.get(pid, ""),
                })
    return out


def load_dupids():
    """同物双 ID 组（读 .webnovel/index.db，只读）。
    🟢 阶段一 D：缺库 / 缺表 / 结构不符一律降级为空并打日志，**不许把启动打挂**
    ——插件场景里「还没建过索引的书」是常态，不是错误。"""
    try:
        return _load_dupids()
    except Exception as e:
        print("  ⚠ 同物双ID 载入跳过（%s）——缺 .webnovel/index.db 或表结构不符"
              % e, flush=True)
        return []


def _load_dupids():
    db = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, ".webnovel/index.db"), uri=True)
    c = db.cursor()
    ents = c.execute("SELECT id, canonical_name, type FROM entities").fetchall()
    app = defaultdict(set)
    for a, eid in c.execute("SELECT alias, entity_id FROM aliases"):
        if a:
            app[eid].add(a.strip())
    nch = defaultdict(set)
    for eid, ch in c.execute("SELECT entity_id, chapter FROM appearances"):
        nch[eid].add(int(ch))

    def norm(s):
        s = re.sub(r"[（(\[【].*?[)）\]】]", "", s or "")
        return re.sub(r"[\s·．\.]+", "", s).strip()

    def pin(s):
        return bool(s) and bool(re.match(r"^[a-z0-9_.\-]+$", s.strip()))

    g = defaultdict(list)
    for eid, cn, t in ents:
        n = norm(cn)
        if n and not pin(cn):
            g[n].append((eid, cn, t))
    out = []
    for n, lst in sorted(g.items()):
        if len(lst) < 2:
            continue
        chs = set().union(*[nch.get(e[0], set()) for e in lst]) or set()
        mx = min(chs) if chs else 0
        out.append({
            "id": "DUP-%s" % n, "kind": "dupid", "chapter": mx, "volume": vol_of(mx),
            # 🟢 同物双 ID 归入受控桶「实体与ID」（规范 4.5）
            "bucket": "实体与ID", "bucket_guessed": False, "severity": "high",
            "blocking": False, "statement": "", "why_pending": "",
            "impact": "", "fix_hint": "", "recommendation": None,
            "affected_entities": [],
            "title": "%s ← %s" % (n, " / ".join(e[0] for e in lst)),
            "textfile": chapter_text_file(mx) if mx else None,
            "extra": {
                "canonical": " / ".join("%s(%r)" % (e[0], e[1]) for e in lst),
                "type": "/".join(sorted({e[2] for e in lst})),
                "涉及章数": len(chs),
                "别名": "；".join("; ".join(sorted(app.get(e[0], set()))[:3]) for e in lst),
            },
        })
    db.close()
    return out


def load_textedits():
    """越界改正文的未提交改动（git diff，只读）。
    🟢 阶段一 D：项目不是 git worktree、或环境里没有 git ⇒ 降级为空，不打挂启动。"""
    try:
        return _load_textedits()
    except Exception as e:
        print("  ⚠ 越界改正文载入跳过（%s）——非 git 仓库或缺 git 可执行文件" % e,
              flush=True)
        return []


def _load_textedits():
    r = subprocess.run(["git", "-c", "core.quotepath=false", "diff", "--unified=0", "--", "正文"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    files, cur = defaultdict(lambda: {"-": [], "+": []}), None
    for line in r.stdout.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r"b/(.+)$", line)
            cur = m.group(1) if m else "?"
        elif line.startswith(("+++", "---")):
            continue
        elif line.startswith(("+", "-")):
            body = re.sub(r"@@.*@@", "", line[1:]).strip()
            if body and cur:
                files[cur][line[0]].append(body)
    out = []
    for f, d in sorted(files.items()):
        pairs = list(zip(d["-"], d["+"])) or [("", "")]
        for i, (a, b) in enumerate(pairs[:8]):
            out.append({
                "id": "TXT-%s-%d" % (os.path.basename(f)[:28], i),
                "kind": "text", "chapter": 0, "volume": 0, "bucket": "越界改正文（待定夺）",
                "severity": "", "title": os.path.basename(f),
                "textfile": os.path.join(ROOT, f),
                "extra": {"文件": f, "改前": a[:400], "改后": b[:400]},
            })
    return out


def load_subagent():
    """🔴 2026-09-29：加载五路裁决车道的回灌结果（旁路，**不覆盖作者裁定**）。

    为什么分两层：实测作者的 14 条里有 6 条与子代理结论不一致
    （ch284 / ch386-01 / ch391 / ch392 / ch393-R02 / ch394-P03），
    作者的判断必须压过子代理，故子代理结果只作「已判」标记。
    """
    p = os.path.join(ROOT, ".webnovel", "tmp", "子代理已判_20260929.json")
    if not os.path.exists(p):
        return {}, {}, 0
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}, {}, 0
    return d.get("rulings") or {}, d.get("conflict") or {}, \
        d.get("subagent_total", 0)


def load_rulings():
    """读裁决表。**两种形态都收**：
      · 信封形（规范 6.1）：{"schema_version":1, …, "rulings": {id: rec}}
      · 裸映射（历史形）：{id: rec}
    内部一律用裸映射（{id: rec}），写回时统一升成信封形。
    🟢 阶段一 I：原先只收裸映射、写也写裸映射，于是裁决台自己产出的文件
    过不了自己的 校验裁决JSON.py（缺 schema_version / status / by），
    方案 §3.6「executor 只执行合法裁决」那道门就没意义了。"""
    global RULINGS_ENVELOPE
    if os.path.exists(OUT):
        try:
            d = json.load(open(OUT, encoding="utf-8"))
            if isinstance(d, dict):
                if "schema_version" in d:
                    RULINGS_ENVELOPE = True
                    r = d.get("rulings")
                    if not isinstance(r, dict):
                        raise ValueError("信封里的 rulings 不是对象: %r" % type(r).__name__)
                    return r
                RULINGS_ENVELOPE = False
                return d
            raise ValueError("顶层不是对象: %r" % type(d).__name__)
        except Exception as e:
            # 🔴 裁定文件是唯一产物：损坏时绝不能当空表用（否则下一次保存
            #    会把残局覆盖掉）。改名留证 + 大声报错，让作者从备份恢复。
            stamp = time.strftime("%Y%m%d_%H%M%S")
            quar = OUT + ".corrupt_" + stamp
            try:
                os.replace(OUT, quar)
            except OSError:
                quar = "(改名失败)"
            print("[rulings] ⚠ 裁定文件解析失败(%s)，已改名 %s —— 请从 裁决备份/ 恢复"
                  % (e, quar), file=sys.stderr, flush=True)
    return {}


def _now_iso():
    """带时区的 ISO 8601（规范 6.2 要求 at 带时区；校验器 ISO 正则卡 Z/±HH:MM）。"""
    tz = time.strftime("%z")
    return time.strftime("%Y-%m-%dT%H:%M:%S") + (
        (tz[:3] + ":" + tz[3:]) if len(tz) == 5 else "Z")


def save_rulings(d):
    """整份写回（单写者，规范 §8.1），**统一写成信封形**。"""
    global RULINGS_ENVELOPE
    with LOCK:
        _atomic_save(OUT, {"schema_version": 1, "updated_at": _now_iso(),
                           "source": "ruling-desk", "rulings": d})
        RULINGS_ENVELOPE = True


def upgrade_rulings_file(d):
    """启动时把历史裸映射升成信封形（一次性；_atomic_save 自带快照备份）。"""
    save_rulings(d)
    print("  裁决表已升级为信封形（schema_version=1），旧文件已快照到 裁决备份/",
          flush=True)


def audit_rulings_vocab(d):
    """启动自检：裁决表里的 verdict 有多少不在受控词表内。
    🔴 词表两处各管一头——UI 选项读 config 的 `verdicts`，而受控词表读
    `vocab.verdict`（校验器据此判 ERROR）。两处不一致时**必须自己报出来**，
    否则门默默拒执行，作者只看到「裁决没生效」。"""
    vocab = (CFG.get("vocab") or {}).get("verdict")
    vocab = set(vocab) if isinstance(vocab, list) and vocab else set(DEFAULT_VERDICTS)
    bad = sorted({r.get("verdict") for r in d.values()
                  if isinstance(r, dict) and r.get("verdict")
                  and r.get("verdict") not in vocab})
    if bad:
        print("  ⚠ 裁决表有 %d 个 verdict 不在受控词表 vocab.verdict 内 ⇒ "
              "校验裁决JSON.py 会判 ERROR，executor 会拒执行：" % len(bad), flush=True)
        for v in bad[:6]:
            print("      %r" % v, flush=True)
        print("      修法：把 config 的 vocab.verdict 对齐成你实际在用的选项"
              "（或改用规范 6.3 的通用词表）", flush=True)
    return bad


def _atomic_save(path, d):
    # 🔴 曾经是 open(OUT,"w") 直接覆盖：进程死在写入中途 ⇒ 文件截断，
    #    983 条裁定陪葬。tmp + os.replace 原子替换；每次保存前把旧文件
    #    留一份时间戳快照（轮转 200 份，单文件 ~100KB）。
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        bdir = os.path.join(os.path.dirname(path), "裁决备份")
        os.makedirs(bdir, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(path, os.path.join(bdir, "作者裁决_%s.json" % stamp))
            olds = sorted(glob.glob(os.path.join(bdir, "作者裁决_*.json")))
            for p in olds[:-200]:
                os.remove(p)
        os.replace(tmp, path)
    except OSError as e:
        # 备份环节出问题不能拦住保存本身
        print("[rulings] 备份/替换失败(%s)，退回直写" % e, file=sys.stderr, flush=True)
        os.replace(tmp, path)


def load_stale_split():
    """🔴 把 66 条 stale 分成两类（**这个区分是实测出来的，不是推断**）：
         with_canon 17 条 —— 有 canon_ref，问题**可独立于失效引文核验**（ch500-502/608-609）
         only_quote 48 条 —— 只有失效的正文引文
    ⚠️ 我曾据「只有失效引文」推断「问题已不存在」⇒ **被抽查 6 条证伪**
       （ch409/453 的问题实为跨章矛盾，与那句引文无关；ch504 本身是记录条目）。
       ⇒ 两类**都仍须作者判**，此处只作提示，不作自动 resolved。
    """
    p = os.path.join(ROOT, ".webnovel/tmp/_p4_stale_autojudge.json")
    if not os.path.exists(p):
        return set()
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return set()
    return {"P-ch%s-%s" % (x["ch"], x["id"]) for x in d.get("with_canon", [])}


def load_moot():
    """🔴 2026-09-29：归一 58 组同物双 ID 后，**77 条 pending 的问题对象已不存在**
    （如「库内 li-jian 与 li_jian 并存」——现在只剩 li-jian）。
    ⇒ 这类**不必再问作者**，自动标 resolved。
    ⚠️ 只认「该条诉求就是双 ID 登记」；若一条里还夹别的诉求，不算。"""
    p = os.path.join(ROOT, ".webnovel/tmp/_p4_now_moot.json")
    if not os.path.exists(p):
        return set()
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return set()
    return {"P-ch%s-%s" % (x["ch"], x["id"]) for x in d if x.get("id")}


def load_stale():
    """🔴 载入 stale 复核结果：引文在当前正文 0 命中、且非创作类。
    这批**降级为「待确认」而不是「已解决」**——正文不引用 ≠ 问题不成立
    （可能作者已换写法修掉，也可能是缺陷还在只是那句被挪位，二者无法在
    不读全文的前提下区分）。"""
    p = os.path.join(ROOT, ".webnovel/tmp/_p4_stale_verified.json")
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    # 🔴 2026-09-29 修 id 格式不一致：stale 文件存的是 pending 的**原始 id**
    #    （如 `P-409-03`），而 items 的 id 是**带章号的复合 id**（`P-ch409-P-409-03`）
    #    ⇒ 原来直接用原始 id 去匹配，62 条全部匹配不上（实测标记数为 0）。
    #    ⇒ 按 items 的同一套规则拼出复合 id。
    out = set()
    for x in d.get("ok", []):
        out.add("P-ch%s-%s" % (x["ch"], x["id"]))
    return out


ITEMS = None
RULINGS = {}
RULINGS_ENVELOPE = False      # 裁决表是不是信封形（规范 6.1）；裸映射会被升格
STALE = set()
# 🟢 任务六：这几个原先只在 main() 里赋值，reload_items()（另一个函数）
#    引用时拿不到 ⇒ NameError。声明成模块级，reload 才跑得起来。
SUB = {}
CONFLICT = set()
MOOT = set()
STALE_V = set()


def _resolve_target(q):
    """🟢 2026-09-30 /api/file·/api/open 共用：query 参数 → 文件绝对路径。
    只认 vol（canon 卷号）或 ch（正文章号）——**不收任意路径**，
    定位栏能打开哪些文件由后端这张白名单说了算。"""
    try:
        if q.get("vol"):
            vol = int(q["vol"][0])
            if vol not in CANON_FILE:
                return None
            return os.path.join(CANON_DIR, CANON_FILE[vol])
        if q.get("ch"):
            ch = int(q["ch"][0])
            if not 1 <= ch <= 9999:
                return None
            return chapter_text_file(ch)
    except (ValueError, KeyError):
        return None
    return None


def _hl_lines(q, total):
    """query 里的 line（可重复、可逗号分隔）→ 去重排序的合法行号列表。
    🔴 容忍「L123」带前缀写法 —— 前端 textlines 原始形态就是它。"""
    out = set()
    for part in q.get("line", []):
        for x in re.split(r"[,\s]+", part):
            m = re.search(r"(\d+)", x)
            if m and 1 <= int(m.group(1)) <= total:
                out.add(int(m.group(1)))
    return sorted(out)


_CTYPES = {".html": "text/html; charset=utf-8",
           ".js": "text/javascript; charset=utf-8",
           ".css": "text/css; charset=utf-8",
           ".json": "application/json; charset=utf-8",
           ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
           ".jpeg": "image/jpeg", ".gif": "image/gif", ".ico": "image/x-icon",
           ".woff2": "font/woff2", ".woff": "font/woff",
           ".map": "application/json; charset=utf-8"}


def _serve_static(self, path):
    """托管 FRONTEND_DIST（阶段一 F）。单页应用 ⇒ 未知路径回 index.html。
    🔴 必须防路径穿越：url 里 ../ 能跳出 dist 读到任意文件。
    🔴 也不许把 dist 之外的绝对路径当文件读（同一条 abspath 前缀判定挡住）。"""
    root = os.path.abspath(FRONTEND_DIST)
    rel = urllib.parse.unquote(path or "/").lstrip("/")
    target = os.path.abspath(os.path.join(root, rel)) if rel else root
    if target != root and not target.startswith(root + os.sep):
        return self._send(403, "403 路径越界", "text/plain; charset=utf-8")
    if os.path.isdir(target):
        target = os.path.join(target, "index.html")
    if not os.path.isfile(target):
        target = os.path.join(root, "index.html")
    if not os.path.isfile(target):
        return self._send(500, "缺 index.html：%s" % root,
                          "text/plain; charset=utf-8")
    ctype = _CTYPES.get(os.path.splitext(target)[1].lower(),
                        "application/octet-stream")
    try:
        with open(target, "rb") as fh:
            return self._send(200, fh.read(), ctype)
    except OSError as e:
        return self._send(500, str(e), "text/plain; charset=utf-8")


def _serve_file(self, q):
    path = _resolve_target(q)
    if not path or not os.path.exists(path):
        return self._send(200, json.dumps(
            {"ok": False, "error": "文件不存在（%s）" % (path or "参数缺 vol/ch")},
            ensure_ascii=False))
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            all_lines = fh.read().split("\n")
    except OSError as e:
        return self._send(200, json.dumps(
            {"ok": False, "error": str(e)}, ensure_ascii=False))
    if all_lines and all_lines[0].startswith("\ufeff"):
        all_lines[0] = all_lines[0][1:]
    total = len(all_lines)
    hl = _hl_lines(q, total)
    # 窗口：canon 卷 8000-10000 行，不能整发 —— 首个引用行上 120 行、
    # 末个引用行下 200 行，封顶 700 行（裁决核对足够，响应也轻）。
    if hl:
        a = max(1, hl[0] - 120)
        b = min(total, hl[-1] + 200)
    else:
        a, b = 1, min(total, 400)
    if b - a + 1 > 700:
        b = a + 699
    lines = [[i, all_lines[i - 1].rstrip("\r")] for i in range(a, b + 1)]
    return self._send(200, json.dumps(
        {"ok": True, "name": os.path.basename(path), "path": path,
         "total": total, "from": a, "to": b, "hl": hl, "lines": lines},
        ensure_ascii=False))


def _serve_open(self, q):
    """「在外部打开」两种方式（🟢 2026-09-30 作者要求默认走系统默认程序）：
      how=default —— os.startfile，即系统默认 .md 程序（作者机上是 Typora）；
                      ⚠️ startfile 带不上行号，只能开文件。
      how=code    —— VS Code `code -g 文件:行号`，精确跳行；没装 code CLI 退 default。"""
    path = _resolve_target(q)
    if not path or not os.path.exists(path):
        return self._send(200, '{"ok":false,"error":"文件不存在"}')
    m = re.match(r"(\d+)", (q.get("line") or ["1"])[0])
    line = int(m.group(1)) if m else 1
    how_req = (q.get("how") or ["default"])[0]
    try:
        if how_req == "code":
            code = shutil.which("code")
            if code:
                subprocess.Popen([code, "-g", "%s:%d" % (path, line)], cwd=ROOT)
                return self._send(200, json.dumps({"ok": True, "how": "code"}))
        if not hasattr(os, "startfile"):
            return self._send(200, '{"ok":false,"error":"本机没有 code CLI，也未提供 startfile"}')
        os.startfile(path)              # noqa: S606 —— 作者本机工具，有意为之
        return self._send(200, json.dumps({"ok": True, "how": "startfile"}))
    except Exception as e:
        return self._send(200, json.dumps({"ok": False, "error": str(e)},
                                          ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════
# 任务六：分页 + 过滤下推。原先 /api/items 一次回传全部条目（连同
# verdicts/labels/volumes/done 元数据），前端再全量 computed 过滤排序——
# 卡片涨到上万张时首屏传输、JSON 解析、重算都线性恶化。
# 现在：过滤/排序/分页全在服务端做，前端只拿当前页。
# 契约见 docs/裁决台JSON规范.md §7.5。
#
# 🔴 写路径语义不变：仍只写一份 OUT 文件（规范 §8.1 单写者），POST 不变。
# ══════════════════════════════════════════════════════════════════════
REVISION = {"n": 0}


def _revision():
    return REVISION["n"]


def _revision_bump():
    """装载数据或裁决表变更 ⇒ 递增。前端拿它做乐观锁。"""
    REVISION["n"] += 1
    return REVISION["n"]


def is_authored(rec):
    """作者已裁 = verdict 或 option 任一非空。
    🔴 两者是独立维度（规范 §6）：选定了候选解 A 就算裁了，
       verdict 可以是空串。旧口径只看 verdict，把候选型裁决误判成未裁。"""
    if not isinstance(rec, dict):
        return False
    return bool(rec.get("verdict") or rec.get("option"))


_STATUS_FLAGS = {
    "only": lambda x: bool(x.get("stale")),
    "no": lambda x: not x.get("stale"),
    "v": lambda x: bool(x.get("stale_v")),
    "moot": lambda x: bool(x.get("moot")),
    "hide": lambda x: not x.get("moot"),
    "sub": lambda x: bool(x.get("sub_done")),
    "nosub": lambda x: not x.get("sub_done"),
    "cf": lambda x: bool(x.get("conflict")),
}


def _sub_applied(x):
    """子代理结论**已执行**。装载时已算好 sub_applied（见 load 的回灌段），
    旧数据无该字段时按 sub_verdict != '无法裁决' 兜底。"""
    if x.get("sub_applied") is not None:
        return bool(x["sub_applied"])
    return x.get("sub_verdict") != "无法裁决"


def _sort_key(x):
    """优先级排序：装载层按 severity + blocking **算**出来（规范原则三：
    派生状态不进数据，但排序键正是装载层的职责，下沉到服务端正合适）。"""
    return (x.get("prio") or "P9", 0 if x.get("blocking") is True else 1,
            x.get("id") or "")


def _meta_payload():
    """小而稳定的启动元数据——原先每次 /api/items 都捎带一份（实测占 1/4 体积）。"""
    return {
        "verdicts": VERDICTS, "labels": LABELS,
        # 🟢 组件自己的版本（与插件版本独立）——界面顶栏显示它，反馈问题时报这个号
        "desk": DESK,
        # 🟢 规范 1.3：领域词表随 meta 下发——筛选下拉用**完整配置词表**
        #    （含暂无卡片的类别），不在界面另抄一份、也不只看数据里出现过的值。
        "vocab": {"bucket": BUCKETS_ORDER},
        "volumes": [{"vol": v, "dir": VOL_DIR[v], "base": VOL_BASE[v],
                     "count": VOL_COUNT[v]} for v in sorted(VOL_DIR)],
        "canon_vols": {v: CANON_FILE[v].replace(".md", "") for v in CANON_FILE},
        "done": sorted(RULINGS.keys()),
        "revision": _revision(),
    }


_QUERY_KEYS = ("bucket", "kind", "vol", "ruled", "status", "prio", "q",
                "sort", "page", "size", "hide_sub", "hide_no_issue",
                "only_unruled", "locate")


def _items_payload(q):
    """过滤/排序/分页下推服务端。

    🔴 2026-10-02 任务六收尾：**不再捎带 meta**。
       原先每页都重发 verdicts/labels/volumes/canon_vols/done(174 个 id)，
       实测带参响应 165.1 KB 里 items 本体只占 95.7 KB、其余 ~70 KB 是
       这份固定元数据 —— 翻页/改筛选都在重复发。
       现在固定元数据只由 /api/meta 拉一次（5.9 KB）。
       `counts` 仍留在每页（它是全量口径统计，随保存变化）。
    """
    if not any(k in q for k in _QUERY_KEYS):
        # 无参数 = 全量。保留给迁移脚本/校验器/离线排查，
        # 前端从不走这条（前端总是带 page&size）。
        return {"items": ITEMS, "revision": _revision()}

    one = lambda k, d="": (q.get(k) or [d])[0]
    flag = lambda k: one(k).strip().lower() in ("1", "true", "yes", "on")
    bucket, kind, vol = one("bucket"), one("kind"), one("vol")
    status, prio = one("status"), one("prio")
    word, ruled = one("q").strip(), one("ruled")
    prioset = {x for x in prio.split(",") if x} if prio else None
    hide_sub = flag("hide_sub")
    hide_no_issue = flag("hide_no_issue")
    only_unruled = flag("only_unruled")

    def keep(x):
        if bucket and x.get("bucket") != bucket:
            return False
        if kind and x.get("kind") != kind:
            return False
        if vol and str(x.get("volume")) != vol:
            return False
        auth = is_authored(RULINGS.get(x.get("id")))
        if ruled == "1" and not auth:
            return False
        if ruled == "0" and auth:
            return False
        if only_unruled and auth:
            return False
        # 判「无问题」的子代理结论默认藏起来（前端同名开关）
        noissue = x.get("sub_done") and x.get("sub_verdict") == "判为无问题"
        if hide_no_issue and noissue:
            return False
        # 🔴 只藏「已执行」的；子代理判「无法裁决」的无结论未落盘，留给作者
        if hide_sub and x.get("sub_done") and _sub_applied(x) and not auth:
            return False
        if status:
            f = _STATUS_FLAGS.get(status)
            if f and not f(x):
                return False
            if status == "todo" and noissue:
                return False
            # 🔴 判据必须与 _counts 的 needAuthor 一致：「裁决表里没有这条 id」，
            #    不是「没有有效裁定」。id 在表里但 verdict/option 全空的记录，
            #    算作者已碰过（前端 !RUL.value[x.id]）。两种口径差 1 条。
            if status == "need" and not (x.get("sub_done")
                                         and not _sub_applied(x)
                                         and x.get("id") not in RULINGS):
                return False
            if status == "ruled" and not auth:
                return False
        if prioset and (x.get("prio") or "P9") not in prioset:
            return False
        if word and word not in json.dumps(x, ensure_ascii=False):
            return False
        return True

    rows = [x for x in ITEMS if keep(x)]
    sort = one("sort")
    if sort == "id":
        rows.sort(key=lambda x: x.get("id") or "")
    elif sort == "rev":
        rows.sort(reverse=True, key=_sort_key)
    else:
        rows.sort(key=_sort_key)
    # 🟢 跳转定位：locate=<id 片段> ⇒ 返回该条在**当前筛选+排序**下的全局序号。
    #    前端拿它换页+定位——旧实现清完筛选只搜第 0 页（前 50 条），
    #    目标在第 50 条之外一律误报「无匹配 ID」（实测 284 卡）。
    loc = one("locate").strip().lower()
    if loc:
        rank = next((i for i, x in enumerate(rows)
                     if loc in str(x.get("id") or "").lower()), -1)
        return {"located": rank,
                "id": rows[rank].get("id") if rank >= 0 else None,
                "total": len(rows), "revision": _revision(), "counts": _counts(ITEMS)}
    try:
        size = int(one("size", "50"))
    except ValueError:
        size = 50
    size = max(1, min(200, size))          # 上限 200，防一次拉爆
    try:
        page = max(0, int(one("page", "0")))
    except ValueError:
        page = 0
    meta = {"total": len(rows), "page": page, "size": size,
            "items": rows[page * size:(page + 1) * size],
            "revision": _revision()}
    # 🔴 顶栏计数必须是**全量口径**，不随筛选变——前端 stats 一直是拿
    #    IT（全量）算的，不是 LIST。实测按筛选后的 rows 算会让「已裁」
    #    从 173 变 162（差 11 条：裁决表里有 11 条的条目已被合并/移出台面）。
    meta["counts"] = _counts(ITEMS)
    return meta


def _counts(rows):
    """顶栏统计。口径**逐项对齐前端 stats**（useDesk.js）：
      · auth      —— 裁决表里所有「已裁」的条目（不限于当前台面上的）
      · sub/union —— 子代理已判 / 与作者裁定的并集
      · total     —— 台面条目总数
    全部对**全量**算，与筛选无关。"""
    authIds = [k for k, v in RULINGS.items() if is_authored(v)]
    union = set(authIds)
    sub = applied = hidden = need = cf = 0
    pend = ful = dup = txt = 0
    for x in rows:
        a = is_authored(RULINGS.get(x.get("id")))
        ap = _sub_applied(x)
        noissue = x.get("sub_done") and x.get("sub_verdict") == "判为无问题"
        sub += bool(x.get("sub_done"))
        if x.get("sub_done") and ap:
            applied += 1
            union.add(x.get("id"))
        hidden += bool(noissue)
        # 🔴 needAuthor 的判据是「**裁决表里没有这条 id**」，不是「没有有效裁定」。
        #    前端 useDesk.js L153 写的是 !RUL.value[x.id] —— 一条 id 存在
        #    但 verdict/option 都是空串的记录，仍然算「作者没碰过」。
        #    实测两种口径差 1 条（有条 id 有记录但内容全空）。
        need += bool(x.get("sub_done") and not ap and x.get("id") not in RULINGS)
        cf += bool(x.get("conflict"))
        k = x.get("kind")
        pend += k == "pending"
        ful += k == "fulfillment"
        dup += k == "dupid"
        txt += k == "text"
    return {"total": len(rows), "auth": len(authIds), "sub": sub,
            "union": len(union),
            "todo": max(0, len(rows) - len(union)),
            "needAuthor": need, "hidden": hidden, "conflict": cf,
            "pending": pend, "fulfillment": ful, "dupid": dup, "text": txt,
            "buckets": sorted({x.get("bucket") for x in rows if x.get("bucket")}),
            "vols": sorted({x.get("volume") for x in rows if x.get("volume")})}


def reload_items():
    """重扫 .story-system/commits/ 重建 ITEMS。
    🔴 迁移脚本改完卡片后 UI 不重启就是旧数据（ITEMS 是启动时构建的），
       这就是「点一下 reload 即可见」的落点。"""
    global ITEMS
    with LOCK:
        a = load_pending()
        b = load_dupids()
        c = load_textedits()
        items = a + b + c
        auto_merge(items)
        items = [x for x in items if not x.get("merged_into")]
        for x in items:
            s = SUB.get(x["id"])
            x["sub_done"] = bool(s)
            x["sub_verdict"] = (s or {}).get("verdict", "")
            x["sub_lane"] = (s or {}).get("lane", "")
            x["sub_applied"] = (s or {}).get(
                "applied", x["sub_verdict"] != "无法裁决")
            x["conflict"] = x["id"] in CONFLICT
            x["stale"] = x["id"] in STALE
            x["moot"] = x["id"] in MOOT
            x["stale_v"] = x["id"] in STALE_V
        ITEMS = items
        return _revision_bump(), len(items)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        # 🔴 前端独立跑（vite dev server 5173）⇒ 必须 CORS，否则浏览器拦跨源
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send(204, "")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/items":
            return self._send(200, json.dumps(_items_payload(q), ensure_ascii=False))
        if u.path == "/api/meta":
            return self._send(200, json.dumps(_meta_payload(), ensure_ascii=False))
        if u.path == "/api/rulings":
            # 🟢 任务六收尾：?ids=a,b,c 只回这些 id 的裁决（前端按页取，
            #    50 条 ≈ 8 KB；全量 174 条是 25.6 KB）。
            #    无 ids 参数的全量保留给迁移脚本/校验器/离线排查。
            raw_ids = (q.get("ids") or [""])[0].strip()
            if raw_ids:
                want = {x.strip() for x in raw_ids.split(",") if x.strip()}
                # 上限 = PAGE_SIZE 的 1.5 倍（size 上限 200 ⇒ 300），
                # 防一个畸形请求把整表吐出来
                cap = 300
                if len(want) > cap:
                    return self._send(400, json.dumps(
                        {"ok": False,
                         "error": "ids 过多：%d 条（上限 %d）" % (len(want), cap)},
                        ensure_ascii=False))
                return self._send(200, json.dumps(
                    {"rulings": {k: v for k, v in RULINGS.items() if k in want},
                     "revision": _revision()}, ensure_ascii=False))
            return self._send(200, json.dumps({"rulings": RULINGS,
                                               "revision": _revision()},
                                              ensure_ascii=False))
        if u.path == "/api/file":
            return _serve_file(self, q)
        if u.path == "/api/open":
            return _serve_open(self, q)
        # 🟢 阶段一 F：单端口托管构建产物（此前只有 PAGE_FILE 兜底，而
        #    backend/index.html 并不存在 ⇒ 插件里没法「起一个进程就访问」，
        #    用户机器也不会 npm 跑 vite dev）。
        if FRONTEND_DIST:
            return _serve_static(self, u.path)
        try:
            page = open(PAGE_FILE, encoding="utf-8").read()
        except OSError as e:
            return self._send(500, str(e), "text/plain; charset=utf-8")
        return self._send(200, page, "text/html; charset=utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            d = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            print("POST 解析失败:", repr(e), flush=True)
            return self._send(400, '{"ok":false}')
        # 🟢 任务六④：乐观锁。服务端整份写回 OUT 文件（规范 §8.1 单写者），
        #    外部改动会被无声冲掉——之前就因为这个把 8 条裁决抹了。
        #    客户端带上自己看到的 revision，不一致就 409，让前端提示重载，
        #    **不要**闷头覆盖。
        rv = d.get("revision")
        if rv is not None and int(rv) != _revision():
            return self._send(409, json.dumps(
                {"ok": False, "error": "数据已更新（服务端 rev=%s，你的 rev=%s），"
                                        "请重载后重试" % (_revision(), rv),
                 "revision": _revision()}, ensure_ascii=False))
        if d.get("action") == "reload":
            rev, n = reload_items()
            return self._send(200, json.dumps(
                {"ok": True, "revision": rev, "items": n}, ensure_ascii=False))
        # 🟢 规范 v1：verdict（改哪一侧）与 option（选哪个候选解）**分开存**。
        #    旧版把候选正文拼进 verdict（实测最长 480 字），这里不再那样。
        # 🟢 阶段一 I：补齐规范 6.2 的必填字段——status（作者已裁但未施工 ⇒
        #    pending，executor 做完改成 applied）与 by（裁决台里点的一律是
        #    作者本人）。缺这两项会让 校验裁决JSON.py 判 ERROR，那道门
        #    （方案 §3.6）就会把作者自己刚裁的条目判成非法。
        rec = {"verdict": d.get("verdict", "") or "", "note": d.get("note", "") or "",
               "by": "author", "status": "pending", "at": _now_iso()}
        opt = d.get("option")
        if opt:
            rec["option"] = str(opt)[:8]
        prev = RULINGS.get(d["id"]) or {}
        if "option" in prev and "option" not in rec:
            rec["option"] = prev["option"]
        # 迁移遗留字段（verdict_raw/override/needs_reclassify/index_target…）留着别丢
        for k, v in prev.items():
            if k not in rec:
                rec[k] = v
        RULINGS[d["id"]] = rec
        save_rulings(RULINGS)
        _revision_bump()
        # 🟢 任务六收尾：把**保存后的这条记录**一并回给前端，
        #    免得它为更新本地 RUL 再发一次 GET /api/rulings?ids=。
        return self._send(200, json.dumps(
            {"ok": True, "total": len(RULINGS), "revision": _revision(),
             "id": d["id"], "ruling": rec}, ensure_ascii=False))


def main():
    global ITEMS, RULINGS
    global VOL_DIR, VOL_BASE, VOL_COUNT
    # 🔴 任务六：SUB/CONFLICT/STALE/STALE_V/MOOT 要声明成 global，
    #    否则它们只是 main() 的局部名，reload_items() 读不到（NameError）。
    global SUB, CONFLICT, STALE, STALE_V, MOOT
    VOL_DIR, VOL_BASE, VOL_COUNT = _scan_volumes()
    print("卷表 %d 卷，全书 %d 章" % (len(VOL_DIR), sum(VOL_COUNT.values())), flush=True)
    print("载入 pending…", flush=True)
    a = load_pending()
    print("  %d 条" % len(a), flush=True)
    print("载入同物双ID…", flush=True)
    b = load_dupids()
    print("  %d 组" % len(b), flush=True)
    print("载入越界改正文…", flush=True)
    c = load_textedits()
    print("  %d 条" % len(c), flush=True)
    ITEMS = a + b + c
    # 🟢 自动去重（见 auto_merge）：在已硬编码组之外再识别同议题跨章重复
    auto_merge(ITEMS)
    # 🟢 重复项合并：被合并的条目**不上台面**（不生成卡片），
    #    但保留「它并进了哪条」的账，便于作者核。
    merged = [x for x in ITEMS if x.get("merged_into")]
    if merged:
        ITEMS = [x for x in ITEMS if not x.get("merged_into")]
        print("合并重复 %d 条 → %s"
              % (len(merged), sorted({x["merged_into"] for x in merged})), flush=True)
    RULINGS = load_rulings()
    # 🟢 阶段一 I：历史裸映射升成信封形（规范 6.1），让自家产物过自家校验器
    if RULINGS and not RULINGS_ENVELOPE:
        upgrade_rulings_file(RULINGS)
    #    再自检 verdict 词表：UI 选项表与受控词表不一致时大声报（否则门默默拒执行）
    audit_rulings_vocab(RULINGS)
    SUB, CONFLICT, SUB_TOTAL = load_subagent()
    STALE = load_stale()
    STALE_V = load_stale_split()
    MOOT = load_moot()
    for x in ITEMS:
        x["stale"] = x["id"] in STALE
        x["moot"] = x["id"] in MOOT      # 🔴 归一后已自动解决
        x["stale_v"] = x["id"] in STALE_V  # 🔴 stale 且有独立 canon 依据
        # 🔴 子代理裁决回灌：已判标记 + 与作者裁定冲突标记
        s = SUB.get(x["id"])
        x["sub_done"] = bool(s)
        x["sub_verdict"] = (s or {}).get("verdict", "")
        x["sub_lane"] = (s or {}).get("lane", "")
        # 🔴 2026-10-01：区分「子代理已执行」与「子代理无结论、待作者拍板」。
        # 「无法裁决」＝ 子代理明确说「这得你定」，既没有结论也没有落盘。
        # 若并进 sub_done 会被前端算成「已裁」，「待裁」严重低估
        # （实测 309 条被算成已裁，真实待办 8+309−13=304 条）。
        x["sub_applied"] = (s or {}).get("applied", x["sub_verdict"] != "无法裁决")
        x["conflict"] = x["id"] in CONFLICT
    print("子代理已判 %d 条（其中与作者冲突 %d 条）"
          % (SUB_TOTAL, len(CONFLICT)), flush=True)
    print("stale 待确认 %d 条（其中 %d 条有独立 canon 依据）｜归一后 moot %d 条"
          % (len(STALE), len(STALE_V), len(MOOT)), flush=True)
    nc = sum(1 for x in ITEMS if x.get("canon_locs"))
    print("合计 %d 项（带 canon 定位 %d），已裁 %d"
          % (len(ITEMS), nc, len(RULINGS)), flush=True)

    class Threaded(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    print("裁决台 v%s（规范 %s）｜配置：%s"
          % (DESK.get("version"), DESK.get("spec_version") or "未标",
             PROJECT_CFG_PATH or "无项目配置（用 backend/config.json 或内置缺省）"),
          flush=True)
    print("启动：http://127.0.0.1:%d" % PORT, flush=True)
    Threaded(("127.0.0.1", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
