# -*- coding: utf-8 -*-
"""裁决 JSON 校验器（不依赖 jsonschema，纯标准库）

用法：
    python -X utf8 工具/裁决台/backend/校验裁决JSON.py            # 校验默认文件（路径见 config.json 的 out）
    python -X utf8 工具/裁决台/backend/校验裁决JSON.py <path> --json
    python -X utf8 校验裁决JSON.py --project-root <书项目根>        # 插件里按项目取默认裁决文件

退出码：0 = 无 ERROR；1 = 有 ERROR（可挂 pre-commit / CI）
"""
import io
import json
import os
import re
import sys
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


# 🟢 2026-10-02 阶段一 H：项目根改为可传，不传时保持原行为（本仓上三级）。
ROOT = os.path.abspath(_argval('--project-root')
                       or os.path.join(HERE, '..', '..', '..'))
# 配置口径与服务端、卡校验器共用（含归属判定）
CFG = ruling_config.load(ROOT)


def _default_path():
    """默认裁决文件：环境变量 OUT → 项目 .webnovel/ruling-desk.json
    → backend/config.json（仅当 root 与本项目一致）→ <ROOT>/.webnovel/tmp/作者裁决.json。
    🟢 阶段一 H：与 ruling_server.py 同口径；插件里裁决表住在项目里。"""
    return CFG.get('out', os.path.join(ROOT, '.webnovel', 'tmp', '作者裁决.json'))


def _vocab(field, default=None):
    """领域词表 vocab.<field>：与 ruling_server / 校验pending 同源（ruling_config），
    项目配置优先于 backend 配置，backend 的 root 不匹配时整份忽略。"""
    v = ((CFG.project.get('vocab') or {}).get(field))
    if not (isinstance(v, list) and v):
        v = ((CFG.backend.get('vocab') or {}).get(field))
    if isinstance(v, list) and v:
        return set(v)
    return default if default is not None else set()


VERDICTS = _vocab('verdict', None) or set(CFG.get('verdicts') or []) or {
    '改正文', '改设定源', '改大纲', '改详纲', '改合同',
    '改索引', '归一ID', '补建实体', '判为无问题', '无法裁决', '交主流程',
}
# 🟢 阶段一 I：verdict 词表退化链 = vocab.verdict → 每书 UI 选项 `verdicts`
#   → 规范 6.3 通用词表。原来只看 vocab.verdict，于是「只配了 verdicts 的书」
#   会被判得一条都不合法（而 UI 里点出来的正是 verdicts 里的词）。
#   ⚠ 两处都配却不一致时，仍以 vocab.verdict 为准并在服务启动时自检报出
#   （见 ruling_server.py 的 audit_rulings_vocab）。
NO_OPTION = _vocab('verdict_no_option', {'判为无问题', '无法裁决', '交主流程'})
STATUSES = {'pending', 'applied', 'skipped', 'overridden'}
OPTIONS = {'A', 'B', 'C'}
ITEM_ID = re.compile(r'^(P|F)-ch[0-9]{3,4}-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$')
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$')
# Windows 绝对路径：C:\ 或 D:\ …；也拦 UNC
ABSPATH = re.compile(r'(?<![\w])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._-]+\\')
ALLOWED_KEYS = {
    'verdict', 'option', 'note', 'by', 'at', 'status', 'applied_at',
    'override', 'verdict_raw', 'needs_reclassify', 'index_target',
}
REQUIRED_KEYS = {'verdict', 'option', 'status'}
# v1 里候选装过的旧字段名——只认 candidates，见规范 1.1/5
LEGACY_CAND_KEYS = ('candidate_resolutions', 'resolution_options', 'options')

NOTE_SOFT = 200
NOTE_HARD = 2000


def check_one(item_id, r, errors, warns):
    tag = '%s:' % item_id

    if not isinstance(r, dict):
        errors.append('%s 条目不是对象（%s）' % (tag, type(r).__name__))
        return

    if not ITEM_ID.match(item_id):
        errors.append('%s id 不符合 ^(P|F)-ch\\d{3,4}-…$' % tag)

    for lk in LEGACY_CAND_KEYS:
        if lk in r:
            errors.append('%s 出现旧候选字段名 %r——只认 candidates，且 candidates 属于'
                          '卡片侧，裁决文件不出现（规范 3 规则一）' % (tag, lk))

    extra = set(r.keys()) - ALLOWED_KEYS
    if extra:
        errors.append('%s 出现规范外字段：%s' % (tag, sorted(extra)))
    missing = REQUIRED_KEYS - set(r.keys())
    if missing:
        errors.append('%s 缺必填字段：%s' % (tag, sorted(missing)))

    v = r.get('verdict')
    if v is None:
        if not r.get('needs_reclassify'):
            errors.append('%s verdict=null 但 needs_reclassify 不为 true' % tag)
        else:
            warns.append('%s 尚未归类（needs_reclassify）' % tag)
    elif v not in VERDICTS:
        errors.append('%s verdict=%r 不在受控词表内（禁自由文本）' % (tag, str(v)[:60]))

    o = r.get('option')
    if o is not None and o not in OPTIONS:
        errors.append('%s option=%r 非法（只允许 A/B/C 或 null）' % (tag, str(o)))
    if v in NO_OPTION and o is not None:
        errors.append('%s verdict=%s 不应带 option' % (tag, v))

    note = r.get('note')
    if note is not None and not isinstance(note, str):
        errors.append('%s note 不是字符串' % tag)
    else:
        note = note or ''
        if ABSPATH.search(note):
            errors.append('%s note 含绝对路径——补充意见不装路径，路径属于 pending/override' % tag)
        if len(note) > NOTE_HARD:
            errors.append('%s note 超长 %d（上限 %d），疑似粘错内容'
                          % (tag, len(note), NOTE_HARD))
        elif len(note) > NOTE_SOFT:
            warns.append('%s note 偏长 %d 字（建议 ≤%d）：方案全文应放 pending 侧'
                         % (tag, len(note), NOTE_SOFT))

    for tf in ('at', 'applied_at'):
        t = r.get(tf)
        if isinstance(t, str) and t and not ISO.match(t):
            errors.append('%s %s 不是带时区的 ISO 8601：%r' % (tag, tf, str(t)[:40]))

    st = r.get('status')
    if st not in STATUSES:
        errors.append('%s status=%r 非法（%s）' % (tag, str(st), '/'.join(sorted(STATUSES))))
    elif st == 'applied':
        if not r.get('applied_at'):
            errors.append('%s status=applied 但 applied_at 为空' % tag)
    elif r.get('applied_at'):
        errors.append('%s status=%s 不该带 applied_at' % (tag, st))

    if v == '改索引' and not r.get('index_target'):
        errors.append('%s verdict=改索引 缺 index_target{entity_id,field}' % tag)
    if v != '改索引' and r.get('index_target'):
        errors.append('%s verdict=%s 不该带 index_target' % (tag, v))

    ov = r.get('override')
    if ov is not None:
        if not isinstance(ov, dict) or not ov.get('reason'):
            errors.append('%s override 缺 reason' % tag)
        else:
            for k in ('from', 'to', 'by', 'at', 'reason'):
                if k not in ov:
                    errors.append('%s override 缺 %s' % (tag, k))

    if not r.get('by'):
        warns.append('%s by 缺失（无法区分作者/主流程）' % tag)
    if r.get('needs_reclassify') and v is not None:
        errors.append('%s needs_reclassify=true 但 verdict 已非 null' % tag)
    if st == 'applied' and r.get('verdict_raw') and not r.get('override'):
        warns.append('%s 有 verdict_raw 但无 override——迁移遗留，请确认' % tag)


def main():
    argv = sys.argv[1:]
    # 🔴 --project-root 的**值**不是位置参数，先摘掉，否则会被当成 <path>
    #    （原先所有非 -- 开头的都当路径，加了这个开关就错位了）。
    if '--project-root' in argv:
        i = argv.index('--project-root')
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith('--')]
    path = args[0] if args else _default_path()
    as_json = '--json' in sys.argv

    if not os.path.exists(path):
        print('找不到 %s' % path, file=sys.stderr)
        return 1
    d = json.load(io.open(path, encoding='utf-8'))

    errors, warns = [], []

    if not isinstance(d, dict):
        errors.append('顶层不是对象')
        print('\n'.join(errors))
        return 1
    if 'schema_version' not in d:
        errors.append('缺 schema_version —— 顶层信封字段，见规范 6.1')
        # 无信封的存量文件：仍然逐条检查
        rulings = d
    else:
        if d['schema_version'] != 1:
            errors.append('schema_version=%r，本校验器只认 1' % d['schema_version'])
        extra = set(d.keys()) - {'schema_version', 'updated_at', 'source',
                                 'deprecated_ids', 'rulings'}
        if extra:
            errors.append('顶层出现规范外字段：%s' % sorted(extra))
        if not isinstance(d.get('rulings'), dict):
            errors.append('rulings 不是对象')
        rulings = d.get('rulings') or {}

    for item_id, r in rulings.items():
        check_one(item_id, r, errors, warns)

    vc = Counter()
    sc = Counter()
    n_rc = 0
    long_v = 0
    for r in rulings.values():
        if isinstance(r, dict):
            v = r.get('verdict')
            # 摘要里长 verdict 一律截断——完整打出来没法看
            vc[(v[:28] + '…') if isinstance(v, str) and len(v) > 28 else (v or '(null)')] += 1
            sc[r.get('status') or '(null)'] += 1
            if r.get('needs_reclassify'):
                n_rc += 1
            if isinstance(v, str) and len(v) > 40:
                long_v += 1

    summary = {
        'file': path,
        'total': len(rulings),
        'needs_reclassify': n_rc,
        'verdict_over_40chars': long_v,
        'by_verdict': dict(vc.most_common()),
        'by_status': dict(sc.most_common()),
        'errors': len(errors),
        'warnings': len(warns),
    }

    if as_json:
        print(json.dumps({'summary': summary,
                          'errors': errors, 'warnings': warns},
                         ensure_ascii=False, indent=2))
    else:
        print('裁决文件：%s' % path)
        print('条目 %d 条｜待归类 %d 条｜verdict 超 40 字 %d 条'
              % (len(rulings), n_rc, long_v))
        print('按 verdict（>28 字已截断）：')
        for v, c in vc.most_common():
            print('  %4d  %s' % (c, v))
        print('按 status ：%s' % dict(sc.most_common()))
        if errors:
            print('\nERROR %d：' % len(errors))
            for e in errors[:80]:
                print('  ✗ %s' % e)
            if len(errors) > 80:
                print('  … 另有 %d 条' % (len(errors) - 80))
        if warns:
            print('\nWARN %d：' % len(warns))
            for w in warns[:40]:
                print('  ! %s' % w)
            if len(warns) > 40:
                print('  … 另有 %d 条' % (len(warns) - 40))
        if not errors and not warns:
            print('\n✅ 全部通过')

    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
