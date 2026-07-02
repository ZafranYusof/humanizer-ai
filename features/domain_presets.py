"""
Domain presets and specialized humanization modes for HumanizeAI.

Provides domain-specific system prompts, language detection,
and mode-based text transformation (SEO, academic, summary, etc.).
"""

import re
import json
import requests

LLM_URL = "http://localhost:20128/v1/chat/completions"
LLM_HEADERS = {"Authorization": "Bearer 123456", "Content-Type": "application/json"}

SUPPORTED_LANGUAGES = [
    {"code": "en", "name": "English"},
    {"code": "ms", "name": "Malay"},
    {"code": "zh", "name": "Chinese"},
    {"code": "ar", "name": "Arabic"},
    {"code": "es", "name": "Spanish"},
    {"code": "fr", "name": "French"},
    {"code": "de", "name": "German"},
    {"code": "ja", "name": "Japanese"},
    {"code": "ko", "name": "Korean"},
    {"code": "pt", "name": "Portuguese"},
]


def get_domain_presets():
    """Return dict of domain presets with system_prompt and constraints."""
    return {
        "Medical": {
            "system_prompt": (
                "You are a medical writing expert. Rewrite the following text to sound "
                "naturally human-written while preserving all medical accuracy, terminology, "
                "drug names, dosages, and clinical findings. Do NOT alter factual medical claims. "
                "Use clear, professional medical prose."
            ),
            "constraints": [
                "preserve_all_medical_terms",
                "preserve_dosages",
                "preserve_claims",
                "no_hallucination",
            ],
        },
        "Legal": {
            "system_prompt": (
                "You are a legal writing specialist. Rewrite the text to sound naturally human "
                "while preserving all legal terminology, citations, case references, statute numbers, "
                "and contractual language. Maintain formal register."
            ),
            "constraints": [
                "preserve_legal_terms",
                "preserve_citations",
                "preserve_case_refs",
                "formal_register",
            ],
        },
        "Technical": {
            "system_prompt": (
                "You are a technical writing expert. Rewrite to sound human while preserving "
                "all technical terms, code references, API names, version numbers, and specifications. "
                "Keep explanations clear and precise."
            ),
            "constraints": [
                "preserve_technical_terms",
                "preserve_code_refs",
                "preserve_versions",
                "clarity",
            ],
        },
        "Creative": {
            "system_prompt": (
                "You are a creative writing coach. Rewrite the text with vivid, engaging language. "
                "Vary sentence structure, use sensory details, and let the author's voice come through. "
                "Feel free to restructure for flow and impact."
            ),
            "constraints": [
                "varied_sentences",
                "sensory_detail",
                "voice_consistency",
                "engaging",
            ],
        },
        "Academic": {
            "system_prompt": (
                "You are an academic writing specialist. Rewrite the text in a scholarly tone "
                "that sounds naturally human. Preserve all citations (in-text and reference list), "
                "quotes, and factual claims. Use formal academic register with varied sentence structure."
            ),
            "constraints": [
                "preserve_citations",
                "preserve_quotes",
                "formal_academic",
                "varied_structure",
            ],
        },
        "Business": {
            "system_prompt": (
                "You are a business communication expert. Rewrite the text in clear, professional "
                "business English. Be concise, action-oriented, and avoid jargon overload. "
                "Preserve all numbers, dates, KPIs, and action items."
            ),
            "constraints": [
                "concise",
                "action_oriented",
                "preserve_numbers",
                "professional_tone",
            ],
        },
        "Casual": {
            "system_prompt": (
                "Rewrite the text in a relaxed, conversational tone as if a real person is chatting. "
                "Use contractions, short sentences, and natural phrasing. Keep the core meaning intact."
            ),
            "constraints": [
                "conversational",
                "contractions",
                "short_sentences",
                "natural",
            ],
        },
        "Scientific": {
            "system_prompt": (
                "You are a scientific writing expert. Rewrite to sound like a human scientist wrote it. "
                "Preserve all data, statistics, methodology descriptions, and scientific terminology. "
                "Maintain precision and objectivity."
            ),
            "constraints": [
                "preserve_data",
                "preserve_statistics",
                "preserve_methodology",
                "objective_tone",
            ],
        },
    }


def _llm_call(system_prompt, user_text, model=None):
    """Call LLM with system prompt and user text. Return response string."""
    payload = {
        "model": model or "default",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
    }
    try:
        resp = requests.post(LLM_URL, headers=LLM_HEADERS, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM Error: {e}]"


def apply_domain(text, domain_name, model=None):
    """Humanize text using a domain-specific system prompt via LLM."""
    presets = get_domain_presets()
    # Case-insensitive lookup
    preset = presets.get(domain_name) or presets.get(domain_name.capitalize()) or presets.get(domain_name.title())
    if not preset:
        # Try fuzzy match
        for k in presets:
            if k.lower() == domain_name.lower():
                preset = presets[k]
                break
    if not preset:
        available = ", ".join(presets.keys())
        return f"[Error: Unknown domain '{domain_name}'. Available: {available}]"
    return _llm_call(preset["system_prompt"], text, model=model)


def academic_integrity_mode(text):
    """Preserve citations/quotes, only rephrase analysis paragraphs."""
    # Split into blocks: quotes (start with ") and citations [...] stay, rest gets rephrased
    lines = text.split('\n')
    result_blocks = []
    buffer = []

    for line in lines:
        stripped = line.strip()
        # Preserve lines that are primarily quotes or citations
        is_quote = stripped.startswith('"') or stripped.startswith('\u201c')
        is_citation_only = bool(re.match(r'^\[.+\]$', stripped)) or bool(re.match(r'^\(.+,\s*\d{4}\)$', stripped))
        is_reference_line = bool(re.match(r'^(References|Bibliography|Works Cited)', stripped, re.I))

        if is_quote or is_citation_only or is_reference_line:
            # Flush buffer first
            if buffer:
                block_text = '\n'.join(buffer)
                prompt = (
                    "Rephrase the following academic analysis paragraph. "
                    "Preserve all factual claims and meaning. Use natural academic prose. "
                    "Do NOT include or modify any citations or quotes—those are handled separately."
                )
                result_blocks.append(_llm_call(prompt, block_text))
                buffer = []
            result_blocks.append(line)
        else:
            buffer.append(line)

    if buffer:
        block_text = '\n'.join(buffer)
        prompt = (
            "Rephrase the following academic analysis paragraph. "
            "Preserve all factual claims and meaning. Use natural academic prose."
        )
        result_blocks.append(_llm_call(prompt, block_text))

    return '\n'.join(result_blocks)


def seo_mode(text, keywords):
    """
    Preserve keywords in-place, humanize surrounding text.
    keywords: list of strings to preserve verbatim.
    """
    if not keywords:
        return _llm_call(
            "Rewrite the following text in a natural, human style suitable for SEO content.",
            text,
        )

    # Build instruction to preserve keywords
    kw_list = ', '.join(f'"{k}"' for k in keywords)
    prompt = (
        f"Rewrite the following text to sound naturally human. "
        f"You MUST preserve these exact keywords/phrases verbatim: {kw_list}. "
        f"They must appear in the output in roughly the same positions. "
        f"Humanize everything else."
    )
    return _llm_call(prompt, text)


def summary_mode(text, target_words=200):
    """Humanize and condense text to approximately target_words."""
    prompt = (
        f"Rewrite and condense the following text to approximately {target_words} words. "
        f"Make it sound naturally human. Preserve the key points and main ideas. "
        f"Be concise and direct."
    )
    return _llm_call(prompt, text)


def expand_mode(text, target_words=500):
    """Humanize and expand text with more detail to approximately target_words."""
    prompt = (
        f"Rewrite and expand the following text to approximately {target_words} words. "
        f"Add relevant detail, examples, and elaboration. Make it sound naturally human. "
        f"Do not invent facts—expand on what's already stated."
    )
    return _llm_call(prompt, text)


def simplify_mode(text):
    """Reduce reading level to grade 8."""
    prompt = (
        "Rewrite the following text so a 13-14 year old can understand it. "
        "Use short sentences, common words (no jargon), and simple sentence structures. "
        "Preserve all key information. Make it sound natural, not dumbed down."
    )
    return _llm_call(prompt, text)


def professional_mode(text):
    """Rewrite to business writing standards."""
    prompt = (
        "Rewrite the following text in professional business English. "
        "Be clear, concise, and action-oriented. Use proper grammar and formal register. "
        "Avoid slang, contractions, and unnecessary jargon. Preserve all numbers and facts."
    )
    return _llm_call(prompt, text)


def storytelling_mode(text):
    """Narrative/conversational rewrite."""
    prompt = (
        "Rewrite the following text in a storytelling style. "
        "Make it feel like a real person is telling you about it—use a conversational tone, "
        "vary sentence length, add natural transitions, and make it engaging. "
        "Preserve the core meaning."
    )
    return _llm_call(prompt, text)


def detect_language(text):
    """
    Detect language of text. Returns language code.
    Uses simple heuristics — for short texts or mixed content, defaults to 'en'.
    """
    if not text or not text.strip():
        return "en"

    # Check for common character ranges
    sample = text[:2000]

    # Arabic: U+0600–U+06FF
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', sample))
    # Chinese: U+4E00–U+9FFF
    chinese_chars = len(re.findall(r'[\u4E00-\u9FFF]', sample))
    # Japanese: hiragana/katakana U+3040–U+30FF
    japanese_chars = len(re.findall(r'[\u3040-\u30FF]', sample))
    # Korean: U+AC00–U+D7AF
    korean_chars = len(re.findall(r'[\uAC00-\uD7AF]', sample))
    # Malay indicators: common Malay words
    malay_words = len(re.findall(r'\b(adalah|dan|yang|ini|untuk|dengan|tidak|pada|juga)\b', sample, re.I))

    total_alpha = max(1, len(re.findall(r'[a-zA-Z]', sample)))

    # Decision thresholds
    if chinese_chars > 10:
        return "zh"
    if japanese_chars > 5:
        return "ja"
    if korean_chars > 5:
        return "ko"
    if arabic_chars > 10:
        return "ar"
    if malay_words >= 2 and (malay_words / max(1, len(sample.split()))) > 0.05:
        return "ms"
    # Spanish indicators
    if len(re.findall(r'\b(el|los|las|una|por|con|para|está|son|pero|como|más)\b', sample, re.I)) > 3:
        es_ratio = len(re.findall(r'\b(el|los|las|una|por|con|para|está|son|pero|como|más)\b', sample, re.I)) / max(1, len(sample.split()))
        if es_ratio > 0.08:
            return "es"

    return "en"


def get_supported_languages():
    """Return list of supported language dicts with code and name."""
    return SUPPORTED_LANGUAGES


if __name__ == "__main__":
    # --- Test block ---
    print("=== Domain Presets Test ===")
    presets = get_domain_presets()
    for name, p in presets.items():
        print(f"  {name}: constraints={p['constraints']}")
    print(f"\nSupported languages: {get_supported_languages()}")

    # Language detection tests
    print("\n=== Language Detection ===")
    print(f"  English: {detect_language('The quick brown fox jumps over the lazy dog.')}")
    print(f"  Malay:   {detect_language('Ini adalah contoh teks dalam bahasa Melayu untuk ujian.')}")
    print(f"  Chinese: {detect_language('这是一个中文测试文本，用于语言检测功能。')}")
    print(f"  Arabic:  {detect_language('هذا نص عربي للاختبار')}")
    print(f"  Spanish: {detect_language('El perro grande está en la casa con los gatos.')}")
    print(f"  Empty:   {detect_language('')}")

    # LLM modes (require running LLM server)
    print("\n=== LLM Modes (require localhost:20128) ===")
    test_text = "Artificial intelligence is transforming healthcare by enabling faster diagnosis."
    print(f"  Original: {test_text}")
    try:
        result = apply_domain(test_text, "Medical")
        print(f"  Medical domain: {result[:120]}...")
    except Exception as e:
        print(f"  Medical domain: skipped ({e})")
