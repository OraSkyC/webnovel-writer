# -*- coding: utf-8 -*-
"""裁决台配置解析：三个入口（服务 / 卡校验器 / 裁决表校验器）共用同一份口径。

🔴 2026-10-02 冒烟测试查出来的真问题（插件化阶段一 A/E/H）：
    `backend/config.json` 是**某本书**的配置（root / canon_dir / out / verdicts /
    vocab / labels）。早先「配置分层」把它当全局兜底，于是带 `--project-root`
    指向夹具书或别人的书时，本书的 canon 目录、词表、**裁决表路径**照样生效
    ——冒烟测试往作者真实的裁决文件里写进了一条夹具记录（已清理，174 条复原）。
    规则改成：**一份配置只对 root 属于它的那本书生效**；
    backend/config.json 的 root 与本次 ROOT 不一致 ⇒ 整份忽略并留痕。

优先级（高 → 低）：
    命令行 → 环境变量（键名大写）→ <ROOT>/.webnovel/ruling-desk.json（每书配置）
    → backend/config.json（仅当它的 root 与本次 ROOT 一致）
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _norm(path):
    """路径归一：大小写、相对/绝对、分隔符统一，才能判「同一本书」。"""
    try:
        return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")
    except Exception:
        return str(path).rstrip("\\/")


def backend_cfg():
    """读 backend/config.json 原文（**不做归属判定**）——
    项目根解析本身要用它的 root，所以这一步必须先于 load()。"""
    return _read_json(os.path.join(HERE, "config.json")) or {}


class Config(object):
    def __init__(self, root, backend, project, cli=None):
        self.root = root
        self.backend = backend or {}
        self.project = project or {}
        self.cli = {k: v for k, v in (cli or {}).items() if v is not None}
        self.notes = []          # 被忽略/不可用的配置，调用方负责打日志

    def get(self, key, default=None):
        if key in self.cli:
            return self.cli[key]
        env = os.environ.get(key.upper())
        if env is not None:
            return env
        v = self.project.get(key)
        if v is not None:
            return v
        v = self.backend.get(key)
        return default if v is None else v

    def vocab(self, field, default):
        """领域词表 vocab.<field>：项目配置优先于 backend 配置，都缺就用规范默认。
        🔴 两边都要读——插件里受控词表住在项目配置里（方案 §3.6 词表同源）。"""
        for src in (self.project, self.backend):
            v = ((src.get("vocab") or {}).get(field))
            if isinstance(v, list) and v:
                return set(v)
        return default


def load(root, cli=None):
    """按项目根装配配置。返回 Config（.notes 里是被忽略的配置说明）。"""
    root = _norm(root)
    project = _read_json(os.path.join(root, ".webnovel", "ruling-desk.json")) or {}
    backend = backend_cfg()
    cfg = Config(root, backend, project, cli)
    brows = backend.get("root")
    if brows and _norm(brows) != root:
        cfg.notes.append(
            "backend/config.json 的 root=%s 与本次项目根 %s 不一致 ⇒ 整份忽略"
            "（这份配置属于另一本书）" % (brows, root))
        cfg.backend = {}
    return cfg