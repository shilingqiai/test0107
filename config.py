# -*- coding: utf-8 -*-
"""
Customer FAQ Auto-Classifier - Central Configuration Module

Unified management: API config, category definitions, classification rules,
logging setup. All other modules import from here for a single source of truth.
"""

import os
import logging
from typing import List, Dict

# ============================================================
# API Configuration
# ============================================================

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("FAQ_CLASSIFIER_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.0
MAX_TOKENS = 50
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def require_api_key() -> str:
    """Get API key from environment. Raises RuntimeError with clear
    instructions if not set."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set.\n"
            "Please set it via:\n"
            "  Windows CMD:  set OPENAI_API_KEY=sk-...\n"
            "  Windows PS:   $env:OPENAI_API_KEY='sk-...'\n"
            "  Bash:         export OPENAI_API_KEY='sk-...'\n"
            "\nOr use mock mode: python evaluate.py --mock"
        )
    return key


# ============================================================
# Category Definitions (source: task1_categories.md)
# ============================================================

VALID_CATEGORIES: List[str] = [
    "退款退货",
    "物流查询",
    "账号问题",
    "商品咨询",
    "投诉建议",
    "其他",
]

CATEGORY_DEFINITIONS: Dict[str, str] = {
    "退款退货": (
        "用户要求退款、退货、换货，或咨询退款进度。"
        "典型场景：我要退货、钱什么时候退回来、怎么换货、"
        "退货邮费谁出、退款什么时候到账、七天无理由退货怎么退"
    ),
    "物流查询": (
        "用户询问包裹位置、配送状态、快递信息。"
        "典型场景：快递到哪了、什么时候能到、"
        "包裹显示签收但没收到、物流信息两天没更新、快递能改派送地址吗"
    ),
    "账号问题": (
        "用户遇到登录、密码、账号安全等问题。"
        "典型场景：密码忘了怎么办、账号被锁了、"
        "怎么修改手机号、账号被冻结了怎么回事、异地登录提醒是真的吗"
    ),
    "商品咨询": (
        "用户询问商品信息、规格、库存、价格、材质、功能等。"
        "典型场景：这个商品有蓝色的吗、尺码怎么选、"
        "支持降噪吗、是真皮的吗、能带上飞机吗"
    ),
    "投诉建议": (
        "用户对服务、商品质量不满，或提出改进建议。"
        "典型场景：你们服务太差了、我要投诉、"
        "建议增加XX功能、什么破质量、退货流程太麻烦了搞不懂怎么操作"
    ),
    "其他": (
        "不属于以上任何类别的问题。"
        "典型场景：简单问候（你好）、闲聊（嗯嗯好的谢谢）、"
        "纯表情或符号（???）、无法归类的表述"
    ),
}

# ============================================================
# Classification Rules (source: task1_categories.md)
# ============================================================

CLASSIFICATION_RULES: List[str] = [
    "如果一个问题同时涉及多个类别，以用户的【主要诉求】为准进行分类。",
    (
        "询问退款进度或退款到账时间的问题，归入【退款退货】，"
        "【不是】物流查询。例如：退款什么时候到账 -> 退款退货。"
    ),
    (
        "辱骂类表述如果包含具体的投诉内容（如投诉服务态度、投诉商品质量），"
        "归入【投诉建议】；纯辱骂、无具体诉求的归入【其他】。"
    ),
    "如果确实无法判断类别，默认归类为【其他】。",
    "只回复类别名称，不要添加任何标点符号、解释、换行或其他文字。",
]


# ============================================================
# Logging Setup
# ============================================================

def setup_logging(level: int = logging.INFO) -> None:
    """Configure global logging format (call once in entry script)."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
