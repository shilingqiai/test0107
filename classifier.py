#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Customer FAQ Auto-Classifier (Improved Version)

Key improvements over the original task1_classifier.py:
  - New System Prompt with full category definitions + rules
  - Input/output validation for robustness
  - API call with retry + exponential backoff
  - Structured logging for debugging and audit
  - Batch processing with per-item error recovery
  - argparse CLI with --verbose mode
"""

import json
import time
import logging
import argparse
from typing import Optional

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

# OpenAI client (API key from environment variable, checked at startup)
client = OpenAI(api_key=config.require_api_key())


# ============================================================
# Prompt Construction
# ============================================================

def build_system_prompt() -> str:
    """Build the System Prompt with full category definitions,
    classification rules, and output format constraints."""
    # Category definitions
    category_lines = []
    for idx, cat in enumerate(config.VALID_CATEGORIES, 1):
        definition = config.CATEGORY_DEFINITIONS[cat]
        category_lines.append(f"{idx}. **{cat}**: {definition}")

    categories_text = "\n".join(category_lines)

    # Classification rules
    rules_lines = []
    for idx, rule in enumerate(config.CLASSIFICATION_RULES, 1):
        rules_lines.append(f"{idx}. {rule}")
    rules_text = "\n".join(rules_lines)

    prompt = f"""You are a professional customer service FAQ classification assistant.
Your task is to accurately classify user questions into one of the 6 categories.

## Category Definitions

{categories_text}

## Classification Rules (must follow strictly)

{rules_text}

## Output Format

Reply with only the category name. Do NOT add any punctuation, explanation,
line breaks, or other text.

Correct example: 退款退货
Wrong examples:
- "退款退货。" (extra punctuation)
- "This belongs to 退款退货" (extra explanation)"""

    return prompt


def build_user_message(question: str) -> str:
    """Build the User Message containing only the question to classify."""
    return f'Please classify the following user question:\n\n"{question}"'


# ============================================================
# Core Classification Logic
# ============================================================

def _clean_result(raw: str) -> str:
    """Clean model output: remove whitespace, punctuation, extra text."""
    import re

    result = raw.strip()
    # Remove common punctuation from both ends
    result = result.strip("。，！？、；：“”"
                          "「」『』（）.!,;:\"'`~")

    # Try matching any valid category name within the result
    for cat in config.VALID_CATEGORIES:
        if cat in result:
            return cat

    # Try after removing all non-word characters
    for cat in config.VALID_CATEGORIES:
        if re.sub(r"[^\w]", "", result) == re.sub(r"[^\w]", "", cat):
            return cat

    return result


def _call_api_with_retry(messages: list, question_id: Optional[int] = None) -> str:
    """Call OpenAI API with retry and exponential backoff."""
    last_error = None

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=config.MODEL,
                messages=messages,
                temperature=config.TEMPERATURE,
                max_tokens=config.MAX_TOKENS,
                timeout=config.REQUEST_TIMEOUT,
            )
            return response.choices[0].message.content

        except Exception as e:
            last_error = e
            qid_str = f" [id={question_id}]" if question_id else ""

            if attempt < config.MAX_RETRIES:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "API call failed%s (attempt %d/%d): %s - retrying in %ds",
                    qid_str, attempt + 1, config.MAX_RETRIES + 1, e, wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "API call failed permanently%s (attempt %d/%d): %s",
                    qid_str, attempt + 1, config.MAX_RETRIES + 1, e,
                )

    raise last_error  # type: ignore[misc]


def classify_question(question: str, question_id: Optional[int] = None) -> str:
    """Classify a single user question.

    Args:
        question: The user's question text.
        question_id: Optional ID for log tracing.

    Returns:
        Classification result, guaranteed to be one of VALID_CATEGORIES.

    Raises:
        ValueError: If input is empty or not a string.
        RuntimeError: If API call fails after exhausting retries.
    """
    # -- Input validation --
    if not isinstance(question, str):
        raise ValueError(f"Question must be a string, got: {type(question).__name__}")

    if not question.strip():
        logger.debug("Empty question [id=%s], defaulting to [other]", question_id)
        return config.VALID_CATEGORIES[-1]  # "其他"

    # -- Build messages --
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": build_user_message(question.strip())},
    ]

    # -- API call with retry --
    raw_result = _call_api_with_retry(messages, question_id)

    # -- Output validation --
    cleaned = _clean_result(raw_result)

    if cleaned in config.VALID_CATEGORIES:
        logger.debug("[id=%s] question: %s -> category: %s", question_id, question[:50], cleaned)
        return cleaned

    # Invalid output, fallback to "其他" and log warning
    logger.warning(
        "[id=%s] Model returned invalid category: %r -> fallback to [other]",
        question_id, raw_result[:100],
    )
    return config.VALID_CATEGORIES[-1]


# ============================================================
# Batch Classification
# ============================================================

def batch_classify(input_file: str, output_file: str) -> None:
    """Batch classify questions from a JSON input file.

    Input JSON format:
        [{"id": 1, "question": "..."}, ...]

    Output JSON format:
        [{"id": 1, "question": "...", "predicted_category": "...", "error": null}, ...]
    """
    # -- Load input --
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error("Input file not found: %s", input_file)
        raise
    except json.JSONDecodeError as e:
        logger.error("Input file JSON parse failed: %s", e)
        raise

    if not isinstance(data, list):
        raise ValueError(f"Input JSON must be an array, got: {type(data).__name__}")

    logger.info("Starting batch classification: %d questions", len(data))

    # -- Process each item --
    results = []
    success_count = 0
    error_count = 0

    for item in data:
        # Input structure validation
        if not isinstance(item, dict):
            logger.warning("Skipping non-dict item: %r", item)
            continue

        item_id = item.get("id", "?")
        question = item.get("question")

        if question is None:
            logger.warning("[id=%s] Missing 'question' field, skipping", item_id)
            continue

        try:
            category = classify_question(str(question), question_id=item_id)
            results.append({
                "id": item_id,
                "question": question,
                "predicted_category": category,
                "error": None,
            })
            success_count += 1

        except Exception as e:
            logger.error("[id=%s] Classification failed: %s", item_id, e)
            results.append({
                "id": item_id,
                "question": question,
                "predicted_category": None,
                "error": str(e),
            })
            error_count += 1

    # -- Write output --
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    logger.info(
        "Batch classification complete: %d success, %d failed, %d total -> %s",
        success_count, error_count, len(results), output_file,
    )


# ============================================================
# CLI
# ============================================================

def main() -> None:
    config.setup_logging()

    parser = argparse.ArgumentParser(
        description="Customer FAQ Auto-Classifier (Improved Version)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python classifier.py -i questions.json -o results.json\n"
            "  python classifier.py -i questions.json -o results.json -v"
        ),
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Input JSON file path",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output JSON file path",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG level logging",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Override default model (default: {config.MODEL})",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("DEBUG logging enabled")

    if args.model:
        config.MODEL = args.model
        logger.info("Using model: %s", args.model)
    else:
        logger.info("Using default model: %s", config.MODEL)

    batch_classify(args.input, args.output)


if __name__ == "__main__":
    main()
