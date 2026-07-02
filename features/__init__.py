"""HumanizeAI features package.

Public API exposed from features.text_processing:
    apply_strength, humanize_paragraph, generate_sentence_diff,
    passive_to_active, get_readability_scores, tone_mix, formality_adjust,
    preserve_formatting, detect_citations, detect_code_blocks,
    reorder_paragraphs, check_passive_voice
"""

from .text_processing import (
    apply_strength,
    humanize_paragraph,
    generate_sentence_diff,
    passive_to_active,
    get_readability_scores,
    tone_mix,
    formality_adjust,
    preserve_formatting,
    detect_citations,
    detect_code_blocks,
    reorder_paragraphs,
    check_passive_voice,
)

__all__ = [
    "apply_strength",
    "humanize_paragraph",
    "generate_sentence_diff",
    "passive_to_active",
    "get_readability_scores",
    "tone_mix",
    "formality_adjust",
    "preserve_formatting",
    "detect_citations",
    "detect_code_blocks",
    "reorder_paragraphs",
    "check_passive_voice",
]
