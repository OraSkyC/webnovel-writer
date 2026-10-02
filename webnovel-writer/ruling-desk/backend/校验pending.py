# -*- coding: utf-8 -*-
"""pending 卡片校验器（扫 rejected 章的章节产物）

用法：
    python -X utf8 工具/裁决台/backend/校验pending.py            # 全量
    python -X utf8 工具/裁决台/backend/校验pending.py --chapter 408
    python -X utf8 工具/裁决台/backend/校验pending.py --json
    python -X utf8 工具/裁决台/backend/校验pending.py --strict   # 顶层规范外字段也判 ERROR
                                                             # （过渡期默认仅计数，见规范 9）
    python -X utf8 校验pending.py --project-root <书项目根>       # 插件里按项目扫

退出码：0 = 无 ERROR；1 = 有 ERROR
"""
import io
import json
import os
import sys
import glob
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import ruling_config                              # noqa: E402


def _argval(name):
    """命令行取值：`--project-root <路径>`；没给返回 None。"""
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


# 🟢 2026-10-02 阶段一 H：项目根改为可传。不传时**保持原行为**（本仓上三级）——
#   插件里 ruling-desk/backend 上三级是插件仓根，会扫错目录（方案 §3.6 的门就废了）。
ROOT = os.path.abspath(_argval('--project-root')
                       or os.path.join(HERE, '..', '..', '..'))
CD = os.path.join(ROOT, '.story-system', 'commits')
# 配置口径与服务端共用（含归属判定：backend/config.json 属于别的书就整份忽略）
CFG = ruling_config.load(ROOT)


def _vocab(field, default):
    """领域词表按书配置：项目 .webnovel/ruling-desk.json 优先于 backend/config.json，
    缺省用规范默认。🔴 两边都要读——插件里受控词表住在项目配置里（方案 §3.6 词表同源），
    只读 backend/config.json 会让门按错误的词表判。"""
    return CFG.vocab(field, default)


SEVERITIES = {'blocking', 'critical', 'high', 'major', 'medium', 'low', 'minor', 'info'}
BUCKETS = _vocab('bucket', {'实体与ID', '时间线', '数值算术', '设定源冲突',
                            '正文一致性', '章节边界', '文书体例', '其他'})
CAND_KEY_RE = re.compile(r'^[A-C]$')
ITEM_ID = re.compile(r'^(P|F)-ch[0-9]{3,4}-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$')
ID_CH = re.compile(r'^(?:P|F)-ch([0-9]{3,4})-')
ID_SUF = re.compile(r'^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$')      # 卡片层 id＝后缀
RX_OLDID = re.compile(r'^ch[0-9]+_')                     # ch{章号}_{后缀} 历史形态
BACKSLASH = re.compile(r'\\')
EMOJI_KEY = re.compile(r'[\U0001F300-\U0001FAFF☀-➿]')
ALLOWED = {
    'id', 'kind', 'chapter', 'volume', 'bucket', 'severity', 'blocking',
    'title', 'statement', 'evidence', 'affected_entities', 'candidates',
    'fix_hint', 'recommendation', 'why_pending', 'impact', 'severity_note',
    'extra',
}
# chapter / kind / volume 由裁决台装载时派生（文件名 + 所在数组），不要求写卡人填
REQUIRED = {'id', 'severity', 'blocking'}
CAND_ALLOWED = {'key', 'title', 'body', 'pros', 'cons', 'recommended'}
CAND_REQUIRED = {'key', 'title', 'body'}
# 与 pending.schema.json 的 maxLength 一致
MAXLEN = {'title': 240, 'statement': 800, 'fix_hint': 800,
          'why_pending': 800, 'impact': 800, 'severity_note': 400}
# 迁移脚本把布尔占位写成过字符串（实测 23 条 fix_hint="False"），schema 认它是合法字符串、
# 界面上却原样显示——没有别的检查能发现，单列一条 WARN。
JUNK_STR = {'false', 'true', 'none', 'null', 'nan', '-'}


def check_card(card, pid, errors, warns, stat, strict=False):
    tag = '%s:' % pid
    if not isinstance(card, dict):
        # 裁决台装载层对非对象元素是 continue（见 ruling_server.py 的
        # 「if not isinstance(p, dict): continue」），它们是车道散文报告、
        # 不是卡片。判 WARN 而非 ERROR——否则永远修不完。
        warns.append('%s 非对象（%s），裁决台装载时跳过，非卡片'
                     % (tag, type(card).__name__))
        return

    miss = REQUIRED - set(card.keys())
    if miss:
        errors.append('%s 缺必填字段：%s' % (tag, sorted(miss)))

    extra = set(card.keys()) - ALLOWED
    if extra:
        for k in sorted(extra):
            if EMOJI_KEY.search(k):
                errors.append('%s 键名含 emoji：%r——键名必须可进 enum' % (tag, k[:30]))
        if strict:
            errors.append('%s 出现规范外字段：%s（进 extra 或改规范）' % (tag, sorted(extra)))
        stat['offschema'] += len(extra)

    for f, cap in MAXLEN.items():
        v = card.get(f)
        if isinstance(v, str) and len(v) > cap:
            errors.append('%s %s 超长 %d（上限 %d）' % (tag, f, len(v), cap))

    fh = card.get('fix_hint')
    if isinstance(fh, str) and fh.strip().lower() in JUNK_STR:
        warns.append('%s fix_hint=%r 疑似迁移垃圾占位，应删除该键' % (tag, fh.strip()))

    sev = card.get('severity')
    if sev is not None and sev not in SEVERITIES:
        errors.append('%s severity=%r 不在受控词表（%s）'
                      % (tag, str(sev)[:30], '/'.join(sorted(SEVERITIES))))
    b = card.get('bucket')
    if b is not None and b not in BUCKETS:
        errors.append('%s bucket=%r 不在受控词表' % (tag, str(b)[:30]))

    bl = card.get('blocking')
    if bl is not None and not isinstance(bl, bool):
        errors.append('%s blocking=%r 必须是真 bool（字符串「否」是真值，判断会反）'
                      % (tag, str(bl)[:20]))

    ae = card.get('affected_entities')
    if ae is not None and not (isinstance(ae, list) and
                               all(isinstance(x, str) for x in ae)):
        errors.append('%s affected_entities 必须是字符串数组（库内实体 ID）' % tag)

    ev = card.get('evidence')
    if isinstance(ev, dict):
        for a in ev.get('authority', []) or []:
            if not isinstance(a, dict):
                errors.append('%s evidence.authority 条目不是对象' % tag)
                continue
            if 'line' in a or 'label' in a:
                warns.append('%s evidence.authority 出现旧形 line/label：改 {volume, lines[]}，'
                             'label 由装载层生成（见规范 4.3）' % tag)
            if not isinstance(a.get('volume'), int):
                errors.append('%s evidence.authority.volume 必须是整数' % tag)
            lns = a.get('lines')
            if not (isinstance(lns, list) and lns and
                    all(isinstance(x, int) for x in lns)):
                errors.append('%s evidence.authority.lines 必须是非空整数数组'
                              '（旧形 {volume,line,label} 改 {volume,lines[]}）' % tag)
        seen_f = set()
        for f in ev.get('text', []) or []:
            fp = (f or {}).get('file') if isinstance(f, dict) else None
            if isinstance(fp, str) and BACKSLASH.search(fp):
                errors.append('%s evidence.text.file 含反斜杠：%r（统一正斜杠）' % (tag, fp[:60]))
            if isinstance(fp, str):
                if fp in seen_f:
                    warns.append('%s evidence.text 同一 file 拆成多条：应并入一条 lines[]'
                                 '（见规范 4.3）' % tag)
                seen_f.add(fp)
    elif ev is not None:
        errors.append('%s evidence 必须是对象 {authority,text,other}，不是 %s'
                      % (tag, type(ev).__name__))

    cs = card.get('candidates')
    if isinstance(cs, list):
        if not (2 <= len(cs) <= 3):
            errors.append('%s candidates 条数 %d 越界（2-3，超过该拆卡片）' % (tag, len(cs)))
        keys, n_rec = [], 0
        for i, c in enumerate(cs):
            ct = '%s candidates[%d]' % (tag, i)
            if not isinstance(c, dict):
                errors.append('%s 不是对象（字符串数组已废止）' % ct)
                continue
            if set(c.keys()) - CAND_ALLOWED:
                errors.append('%s 出现规范外字段：%s' % (ct, sorted(set(c.keys()) - CAND_ALLOWED)))
            if CAND_REQUIRED - set(c.keys()):
                errors.append('%s 缺必填字段：%s' % (ct, sorted(CAND_REQUIRED - set(c.keys()))))
            k = c.get('key')
            if isinstance(k, str):
                if not CAND_KEY_RE.match(k):
                    errors.append('%s key=%r 非法——须大写单字母 A/B/C' % (ct, k))
                else:
                    keys.append(k)
            t = c.get('title')
            if isinstance(t, str) and len(t) > 40:
                errors.append('%s title 超长 %d（上限 40）' % (ct, len(t)))
            b2 = c.get('body')
            if isinstance(b2, str) and len(b2) > 2000:
                errors.append('%s body 超长 %d（上限 2000）' % (ct, len(b2)))
            for lk in ('pros', 'cons'):
                v = c.get(lk)
                if v is None:
                    continue
                if not isinstance(v, list):
                    errors.append('%s %s 不是数组' % (ct, lk))
                else:
                    for x in v:
                        if not isinstance(x, str):
                            errors.append('%s %s 含非字符串元素' % (ct, lk))
                        elif len(x) > 120:
                            errors.append('%s %s 单条超长 %d（上限 120）' % (ct, lk, len(x)))
            if c.get('recommended') is True:
                n_rec += 1
        dup = [k for k, n in Counter(keys).items() if n > 1]
        if dup:
            errors.append('%s candidates 的 key 重复：%s' % (tag, dup))
        if n_rec > 1:
            errors.append('%s candidates 有 %d 项 recommended=true（至多一个）' % (tag, n_rec))

    rec = card.get('recommendation')
    if rec is not None and not isinstance(rec, (dict, type(None))):
        errors.append('%s recommendation=%s 必须是对象或 null'
                      % (tag, type(rec).__name__))

    if not card.get('title'):
        stat['no_title'] += 1


def main():
    only = None
    if '--chapter' in sys.argv:
        only = int(sys.argv[sys.argv.index('--chapter') + 1])
    as_json = '--json' in sys.argv
    strict = '--strict' in sys.argv

    errors, warns, stat = [], [], Counter()
    n = 0
    for p in sorted(glob.glob(os.path.join(CD, 'chapter_*.commit.json'))):
        try:
            j = json.load(io.open(p, encoding='utf-8'))
        except Exception:
            continue
        if (j.get('meta') or {}).get('status') != 'rejected':
            continue
        ch = (j.get('meta') or {}).get('chapter')
        if only is not None and ch != only:
            continue
        for src, key, kind in (('disambiguation_result', 'pending', 'pending'),
                               ('fulfillment_result', 'issues', 'fulfillment')):
            arr = (j.get(src) or {}).get(key) or []
            for i, card in enumerate(arr):
                n += 1
                if not isinstance(card, dict):
                    check_card(card, '%s[%d]' % (kind, i), errors, warns, stat, strict)
                    continue
                # 卡片内 id 是后缀；写卡人直接写了全 id 则原样采用并核对章号
                cid = card.get('id')
                if isinstance(cid, str) and ITEM_ID.match(cid):
                    pid = cid
                    m = ID_CH.match(cid)
                    if m and int(m.group(1)) != ch:
                        errors.append('%s: id 内章号与所在文件章号 %d 不一致' % (pid, ch))
                else:
                    pid = '%s-ch%d-%s' % (kind[0].upper(), ch, cid)
                    if isinstance(cid, str) and cid and not ITEM_ID.match(pid):
                        # ch{章号}_{后缀} 是一键可归一的历史形态：
                        # 改名会改装载层拼出的全 id，已入裁决表的条目须同步改键，
                        # 裁决台在跑时改裁决文件会被整份覆盖 ⇒ 这里只提醒，不报错。
                        if RX_OLDID.match(cid) and ID_SUF.match(RX_OLDID.sub('', cid)):
                            warns.append('%s: id=%r 带 ch{章号}_ 前缀，剥掉即合规；'
                                         '改名须与裁决表旧键同步' % (pid, cid))
                        else:
                            errors.append('%s: 拼全 id 后不符合 ^(P|F)-ch\\d{3,4}-…$' % pid)
                    elif not cid:
                        # 缺 id 已由必填检查报，此处不重复
                        pid = '%s[%d]' % (kind, i)
                check_card(card, pid, errors, warns, stat, strict)

    rep = {'scanned': n, 'offschema_keys': stat['offschema'],
           'no_title': stat['no_title'], 'errors': len(errors), 'warnings': len(warns)}
    if as_json:
        print(json.dumps({'summary': rep, 'errors': errors[:200], 'warnings': warns[:100]},
                         ensure_ascii=False, indent=2))
    else:
        print('扫 rejected 章的卡片 %d 张%s' % (n, '（strict）' if strict else ''))
        print('规范外字段累计 %d 处｜缺 title %d 张' % (stat['offschema'], stat['no_title']))
        if errors:
            print('\nERROR %d：' % len(errors))
            for e in errors[:60]:
                print('  ✗ %s' % e)
            if len(errors) > 60:
                print('  … 另有 %d 条' % (len(errors) - 60))
        else:
            print('\n✅ 全部通过')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
