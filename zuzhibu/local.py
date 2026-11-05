"""本机设置：维护者电脑上的路径和姓名不进仓库，写在用户目录的 local.json，和界面的 settings.json 在同一目录。

    Windows：%APPDATA%\\zuzhibu\\local.json；其他系统：~/.config/zuzhibu/local.json

    {
      "python": "计划任务用的 python.exe",
      "aipolis": "aipolis 仓库目录（一局每日更新并入金标简历用）",
      "aipolis_rules": "aipolis 规则目录（四局重核拼提示词用）",
      "library": "素材库目录（tools/import_portraits.py 找本机照片用）",
      "glossary_master": "译名表母本目录（tools/sync_glossary.py 用）",
      "maintainer_name": "维护者姓名（写进仓库前换成“AI政见”）"
    }

每一项都可以用环境变量 ZUZHIBU_<大写项名> 覆盖，例如 ZUZHIBU_AIPOLIS_RULES；命令行参数又优先于这两者。
计划任务的两个 .ps1 读同一个文件（只读 python 一项）。只用标准库。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: 写进仓库的东西里，维护者姓名统一换成这个（docs/公开前检查.md 第十二节第 5 项）。
MAINTAINER = "AI政见"


def path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "zuzhibu" / "local.json"


def get(key: str, default: str | None = None) -> str | None:
    """本机设置的一项：环境变量 ZUZHIBU_<KEY> 优先，再看 local.json；都没有就返回 default。"""
    env = os.environ.get("ZUZHIBU_" + key.upper())
    if env:
        return env
    try:
        data = json.loads(path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default
    value = data.get(key) if isinstance(data, dict) else None
    return str(value) if value else default


def get_path(key: str) -> Path | None:
    value = get(key)
    return Path(value) if value else None


def scrub(text: str) -> str:
    """把本机设置里的维护者姓名换成“AI政见”；没设姓名就原样返回。"""
    name = (get("maintainer_name") or "").strip()
    return text.replace(name, MAINTAINER) if name else text
