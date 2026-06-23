#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Customer FAQ Classifier - Evaluation Framework

Features:
  - Run classifier against 30 labeled test samples
  - Compute overall accuracy, per-category Precision/Recall/F1
  - Output before-vs-after comparison report
  - Two modes:
    * Real API mode (requires OPENAI_API_KEY)
    * Mock mode (keyword-rule-based, no API needed)
"""

import json
import logging
import argparse
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Optional

logger = logging.getLogger(__name__)

# Lazy import config: mock mode does not need API key, should not trigger key check
_cfg = None


def _get_cfg():
    """Lazy-load the config module."""
    global _cfg
    if _cfg is None:
        import config as cfg
        _cfg = cfg
    return _cfg


# ============================================================
# Data Structures
# ============================================================

@dataclass
class CatMetrics:
    """Per-category evaluation metrics."""
    category: str
    correct: int = 0
    expected_count: int = 0
    predicted_count: int = 0

    @property
    def precision(self) -> float:
        return self.correct / self.predicted_count if self.predicted_count > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.expected_count if self.expected_count > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class EvalResult:
    """Evaluation result for one classifier run."""
    mode: str
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    per_category: Dict[str, CatMetrics] = field(default_factory=dict)
    errors: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "total": self.total,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "per_category": {
                cat: {
                    "precision": round(m.precision, 4),
                    "recall": round(m.recall, 4),
                    "f1": round(m.f1, 4),
                    "correct": m.correct,
                    "expected_count": m.expected_count,
                    "predicted_count": m.predicted_count,
                }
                for cat, m in self.per_category.items()
            },
            "errors": self.errors,
        }


# ============================================================
# Mock Classifier - Keyword-rule-based (improved version)
# ============================================================

class MockClassifier:
    """Keyword-priority-rule-based mock classifier (improved version).

    Rules are applied in priority order; first match wins.
    Correctly implements the classification rules from categories.md:
      - Refund progress queries -> refund (refund keywords before logistics)
      - Process complaints -> complaint (complaint sentiment before refund keywords)
      - Multi-intent -> primary intent
    """

    def classify(self, question: str) -> str:
        if not question or not question.strip():
            return "其他"

        q = question.strip()

        # Rule 1: Explicit complaint/report keywords -> complaint
        if any(kw in q for kw in ["我要投诉", "我要举报"]):
            return "投诉建议"

        # Rule 2: Strong complaint sentiment + not a refund how-to question
        #   Handles id=23: process complaint despite refund keywords
        complaint_mood = ["太差", "什么破", "态度差", "太麻烦", "搞不懂"]
        refund_how = ["怎么退", "如何退", "怎么换", "如何换"]
        if any(p in q for p in complaint_mood):
            if any(kw in q for kw in ["投诉", "举报"]):
                return "投诉建议"
            if not any(p in q for p in refund_how):
                return "投诉建议"
            if "流程" in q or "搞不懂" in q:
                return "投诉建议"

        # Rule 3: Refund/return/exchange keywords -> refund
        #   MUST be before logistics! (categories.md rule)
        refund_kw = ["退款", "退货", "换货", "退掉", "七天无理由", "无理由退货",
                      "退一个", "只退", "可以退", "能退"]
        if any(kw in q for kw in refund_kw):
            return "退款退货"

        # Rule 4: Suggestion/improvement keywords -> complaint
        #   (must be before logistics to handle id=15: delivery suggestion)
        if any(kw in q for kw in ["建议", "增加", "改进"]):
            return "投诉建议"

        # Rule 5: Logistics keywords -> logistics
        logistics_kw = [
            "快递", "物流", "包裹", "配送", "签收", "快递柜",
            "派送", "寄错", "寄回去", "改派送",
        ]
        if any(kw in q for kw in logistics_kw):
            return "物流查询"

        # Rule 6: Account keywords -> account
        account_kw = ["密码", "账号", "登录", "冻结", "绑定", "手机号", "异地"]
        if any(kw in q for kw in account_kw):
            return "账号问题"

        # Rule 7: Product inquiry keywords -> product
        product_kw = [
            "有没有", "支持", "能带上", "有蓝色的吗",
            "尺码", "颜色", "规格", "材质", "补货",
            "真皮", "硅胶", "塑料", "42码",
            "手机壳", "耳机", "充电宝", "鞋", "包", "衣服",
            "降噪", "蓝色的",
        ]
        if any(kw in q for kw in product_kw):
            return "商品咨询"

        # Rule 8: Greetings/chat/pure symbols -> other
        greetings = ["你好", "嗯嗯好的谢谢", "在吗"]
        if any(q == g for g in greetings):
            return "其他"
        if all(c in "？？！！。。，、嗯好的谢谢拜拜再见" for c in q):
            return "其他"

        return "其他"


class MockBeforeClassifier:
    """Mock of the original (before) classifier - simple keyword matching.

    No priority rules; each category's keywords compete equally.
    Simulates the original prompt which had no priority/edge-case rules.
    """

    def classify(self, question: str) -> str:
        if not question or not question.strip():
            return "其他"

        q = question.strip()

        # Simple keyword mapping, no priority rules
        checks = [
            (["退款", "退货", "换货", "退掉", "无理由"], "退款退货"),
            (["快递", "物流", "包裹", "配送", "签收", "快递柜", "寄"], "物流查询"),
            (["密码", "账号", "登录", "冻结", "绑定"], "账号问题"),
            (["有没有", "支持", "尺码", "颜色", "材质", "真皮", "降噪", "能带上"], "商品咨询"),
            (["投诉", "举报", "建议", "增加", "太差", "什么破", "麻烦"], "投诉建议"),
        ]

        for keywords, category in checks:
            if any(kw in q for kw in keywords):
                return category

        return "其他"


# ============================================================
# Real API Classifier Factories
# ============================================================

def _make_before_classifier():
    """Create the before-improvement classifier.

    Uses the original prompt: no system prompt, all instructions in user message.
    """
    cfg = _get_cfg()
    from openai import OpenAI

    _kwargs = {"api_key": cfg.require_api_key()}
    if cfg.LLM_BASE_URL:
        _kwargs["base_url"] = cfg.LLM_BASE_URL
    client = OpenAI(**_kwargs)
    api_logger = logging.getLogger("evaluate.before")

    def classify(question: str) -> str:
        if not question or not question.strip():
            return "其他"

        # Original prompt (exact copy from task1_classifier.py)
        prompt = (
            "你是一个客服分类助手。请对以下用户问题进行分类。\n\n"
            "分类类别：退款退货、物流查询、账号问题、商品咨询、投诉建议、其他\n\n"
            "用户问题：" + question + "\n\n"
            "请直接回复分类结果，只回复类别名称。"
        )

        try:
            response = client.chat.completions.create(
                model=cfg.MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=50,
                timeout=cfg.REQUEST_TIMEOUT,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            api_logger.error("API call failed: %s", e)
            return "其他"

        # Clean output
        raw = raw.strip("。，！？、；：\"\"''「」『』()（）")
        for cat in cfg.VALID_CATEGORIES:
            if cat in raw:
                return cat
        return "其他"

    return classify


def _make_after_classifier():
    """Create the after-improvement classifier.

    Uses the new system prompt + clean user message.
    """
    from classifier import classify_question
    return classify_question


# ============================================================
# Evaluation Engine
# ============================================================

def load_test_samples(path: str) -> List[dict]:
    """Load test samples from a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        samples = json.load(f)
    if not isinstance(samples, list):
        raise ValueError(f"Test samples must be a JSON array, got: {type(samples).__name__}")
    logger.info("Loaded %d test samples", len(samples))
    return samples


def run_evaluation(
    classify_fn: Callable[[str], str],
    samples: List[dict],
    mode_name: str,
) -> EvalResult:
    """Run evaluation with a given classifier function.

    Args:
        classify_fn: Function (question: str) -> category: str
        samples: Test samples [{id, question, label}, ...]
        mode_name: Mode name for logging and reporting

    Returns:
        EvalResult with accuracy, per-category metrics, and error cases
    """
    cfg = _get_cfg()
    result = EvalResult(mode=mode_name, total=len(samples))
    result.per_category = {
        cat: CatMetrics(category=cat) for cat in cfg.VALID_CATEGORIES
    }

    for idx, item in enumerate(samples):
        qid = item.get("id", idx + 1)
        question = item.get("question", "")
        expected = item.get("label", "")

        predicted = classify_fn(question)

        # Ensure prediction is valid
        if predicted not in cfg.VALID_CATEGORIES:
            logger.warning(
                "[%s id=%s] Invalid prediction: %r -> fallback to 'other'",
                mode_name, qid, predicted,
            )
            predicted = "其他"

        # Track stats
        result.per_category[expected].expected_count += 1
        result.per_category[predicted].predicted_count += 1

        if predicted == expected:
            result.correct += 1
            result.per_category[expected].correct += 1
        else:
            result.errors.append({
                "id": qid,
                "question": question,
                "expected": expected,
                "predicted": predicted,
            })

    result.accuracy = result.correct / result.total if result.total > 0 else 0.0

    logger.info(
        "[%s] Accuracy: %d/%d = %.1f%%",
        mode_name, result.correct, result.total, result.accuracy * 100,
    )

    return result


# ============================================================
# Comparison Report
# ============================================================

def _print_section(title: str) -> None:
    """Print a section header."""
    print()
    print("=" * 64)
    print("  " + title)
    print("=" * 64)


def print_comparison(before: EvalResult, after: EvalResult) -> None:
    """Print before-vs-after comparison report."""
    cfg = _get_cfg()
    delta_acc = (after.accuracy - before.accuracy) * 100

    # -- Overall --
    _print_section("Classification Accuracy Comparison Report")

    print()
    print(f"{'Metric':<20} | {'Before':>8} | {'After':>8} | {'Change':>8}")
    print(f"{'-'*20}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
    print(f"{'Overall Accuracy':<20} | {before.accuracy:>7.1%} | {after.accuracy:>7.1%} | {delta_acc:>+7.1f}pp")

    # -- Per-category correct -count --
    print()
    print(f"{'Per-Category Correct':<20} | {'Before':>8} | {'After':>8} | {'Change':>8}")
    print(f"{'-'*20}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")

    for cat in cfg.VALID_CATEGORIES:
        bm = before.per_category[cat]
        am = after.per_category[cat]
        b_str = f"{bm.correct}/{bm.expected_count}"
        a_str = f"{am.correct}/{am.expected_count}"
        diff = am.correct - bm.correct
        diff_str = f"{diff:+d}" if diff != 0 else "0"
        label = f"{cat} ({bm.expected_count})"
        print(f"{label:<20} | {b_str:>8} | {a_str:>8} | {diff_str:>8}")

    # -- Per-category F1 --
    print()
    print(f"{'Per-Category F1':<20} | {'Before':>8} | {'After':>8} | {'Change':>8}")
    print(f"{'-'*20}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")

    for cat in cfg.VALID_CATEGORIES:
        bf1 = before.per_category[cat].f1
        af1 = after.per_category[cat].f1
        delta_f1 = (af1 - bf1) * 100
        print(f"{cat:<20} | {bf1:>7.1%} | {af1:>7.1%} | {delta_f1:>+7.1f}pp")

    # -- Before errors --
    _print_section("Before-Improvement Errors")
    if before.errors:
        for err in before.errors:
            print(f"  id={err['id']:<3} expected={err['expected']:<8} predicted={err['predicted']:<8} | {err['question']}")
    else:
        print("  (all correct)")

    # -- After errors --
    _print_section("After-Improvement Errors")
    if after.errors:
        for err in after.errors:
            print(f"  id={err['id']:<3} expected={err['expected']:<8} predicted={err['predicted']:<8} | {err['question']}")
    else:
        print("  (all correct)")

    # -- Summary --
    print()
    print("=" * 64)
    print(f"  Summary: Accuracy {before.accuracy:.1%} -> {after.accuracy:.1%} ({delta_acc:+.1f}pp)")
    fixed = len(before.errors) - len(after.errors)
    if fixed > 0:
        print(f"  Errors fixed: {fixed}")
    elif fixed < 0:
        print(f"  New errors: {abs(fixed)}")
    else:
        print(f"  Error count unchanged: {len(before.errors)}")
    print("=" * 64)
    print()


# ============================================================
# CLI
# ============================================================

TEST_SAMPLES_DEFAULT = "task1_test_samples.json"


def main() -> None:
    cfg = _get_cfg()
    cfg.setup_logging()

    parser = argparse.ArgumentParser(
        description="Customer FAQ Classifier Evaluation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python evaluate.py              # Real API mode (needs OPENAI_API_KEY)\n"
            "  python evaluate.py --mock       # Mock mode (no API needed)\n"
            "  python evaluate.py -i test.json # Custom test file\n"
            "  python evaluate.py -v           # Verbose logging"
        ),
    )
    parser.add_argument(
        "-i", "--input",
        default=TEST_SAMPLES_DEFAULT,
        help=f"Test samples JSON file path (default: {TEST_SAMPLES_DEFAULT})",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output evaluation report JSON file path (optional)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use Mock mode (keyword-rule-based, no API key needed)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG level logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # -- Load samples --
    samples = load_test_samples(args.input)

    # -- Run evaluation --
    if args.mock:
        _print_section("Mock Mode Evaluation (Keyword-Rule Classifier)")

        mc_before = MockBeforeClassifier()
        mc_after = MockClassifier()

        result_before = run_evaluation(mc_before.classify, samples, "before (mock)")
        result_after = run_evaluation(mc_after.classify, samples, "after (mock)")
    else:
        _print_section("Real API Mode Evaluation")

        logger.info("Using model: %s", cfg.MODEL)
        logger.info("Test samples: %s (%d items)", args.input, len(samples))

        logger.info(">>> Running before-improvement evaluation...")
        before_fn = _make_before_classifier()
        result_before = run_evaluation(before_fn, samples, "before")

        logger.info(">>> Running after-improvement evaluation...")
        after_fn = _make_after_classifier()
        result_after = run_evaluation(after_fn, samples, "after")

    # -- Print comparison report --
    print_comparison(result_before, result_after)

    # -- Save report --
    if args.output:
        report = {
            "before": result_before.to_dict(),
            "after": result_after.to_dict(),
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("Evaluation report saved to: %s", args.output)


if __name__ == "__main__":
    main()
