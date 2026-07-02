"""HumanizeAI text processing backend module.

Standalone functions for text analysis, transformation, and humanization.
LLM calls go to localhost:20128 via 9router. Regex/text tasks stay local.
"""

import json
import random
import re
import requests
from difflib import SequenceMatcher

LLM_URL = "http://localhost:20128/v1/chat/completions"
LLM_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer 123456",
}
LLM_MODEL = "cx/gpt-5.4-mini"


def _llm(system: str, user: str, temperature: float = 0.7, max_tokens: int = 4096) -> str:
    """Send chat completion request to local LLM. Returns assistant message text."""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(LLM_URL, headers=LLM_HEADERS, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# 1. apply_strength
# ---------------------------------------------------------------------------
def apply_strength(text: str, level: str = "medium") -> dict:
    """Rewrite text at given intensity level (light/medium/aggressive).

    Returns dict with keys: original, rewritten, level.
    """
    prompts = {
        "light": (
            "You are a light editor. Make minimal changes to make the text sound "
            "more natural and human-written. Fix awkward phrasing but keep the "
            "original structure and vocabulary largely intact. Return only the "
            "rewritten text."
        ),
        "medium": (
            "You are a text humanizer. Rewrite the text to sound like a natural, "
            "human-written piece. Vary sentence structure, replace stiff phrases "
            "with conversational alternatives, add subtle imperfections. Keep the "
            "meaning intact. Return only the rewritten text."
        ),
        "aggressive": (
            "You are a radical rewriter. Heavily restructure the text so it reads "
            "like a real human wrote it casually. Break up long sentences, use "
            "contractions, add colloquialisms, vary paragraph length dramatically. "
            "The output should feel organic and unpolished in a human way. Return "
            "only the rewritten text."
        ),
    }
    sys_prompt = prompts.get(level, prompts["medium"])
    rewritten = _llm(sys_prompt, text, temperature=0.8)
    return {"original": text, "rewritten": rewritten, "level": level}


# ---------------------------------------------------------------------------
# 2. humanize_paragraph
# ---------------------------------------------------------------------------
def humanize_paragraph(text: str, paragraph: str, idx: int) -> dict:
    """Humanize a single paragraph in context of full text.

    Args:
        text: full original text
        paragraph: the specific paragraph to humanize
        idx: 0-based paragraph index

    Returns dict with keys: idx, original, humanized.
    """
    sys_prompt = (
        "You are a text humanizer. You will receive a full document and a "
        "specific paragraph to rewrite. Make the paragraph sound natural, "
        "human-written, and conversational while preserving its meaning. "
        "Consider the surrounding context for tone consistency. Return only "
        "the rewritten paragraph."
    )
    user_msg = (
        f"Full document:\n{text}\n\n"
        f"---\nRewrite this paragraph (index {idx}):\n{paragraph}"
    )
    humanized = _llm(sys_prompt, user_msg, temperature=0.7)
    return {"idx": idx, "original": paragraph, "humanized": humanized}


# ---------------------------------------------------------------------------
# 3. generate_sentence_diff
# ---------------------------------------------------------------------------
def generate_sentence_diff(original: str, humanized: str) -> list:
    """Word-level diff between original and humanized text.

    Returns list of dicts: [{text: str, type: 'added'|'removed'|'unchanged'}].
    """
    orig_words = original.split()
    hum_words = humanized.split()
    matcher = SequenceMatcher(None, orig_words, hum_words)
    result = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for w in orig_words[i1:i2]:
                result.append({"text": w, "type": "unchanged"})
        elif op == "replace":
            for w in orig_words[i1:i2]:
                result.append({"text": w, "type": "removed"})
            for w in hum_words[j1:j2]:
                result.append({"text": w, "type": "added"})
        elif op == "delete":
            for w in orig_words[i1:i2]:
                result.append({"text": w, "type": "removed"})
        elif op == "insert":
            for w in hum_words[j1:j2]:
                result.append({"text": w, "type": "added"})
    return result


# ---------------------------------------------------------------------------
# 4. passive_to_active
# ---------------------------------------------------------------------------
def passive_to_active(text: str) -> dict:
    """Convert passive voice sentences to active voice via LLM.

    Returns dict with keys: original, active_text.
    """
    sys_prompt = (
        "You are a grammar expert. Convert passive voice sentences to active "
        "voice. If a subject is unknown, choose a reasonable one. Keep the "
        "meaning intact. Return only the rewritten text. If the text has no "
        "passive voice, return it unchanged."
    )
    active_text = _llm(sys_prompt, text, temperature=0.5)
    return {"original": text, "active_text": active_text}


# ---------------------------------------------------------------------------
# 5. get_readability_scores
# ---------------------------------------------------------------------------
def get_readability_scores(text: str) -> dict:
    """Compute readability metrics for given text.

    Returns dict with keys:
        flesch_kincaid_grade, gunning_fog, coleman_liau_index,
        word_count, sentence_count, syllable_count.
    """
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = re.findall(r'\b[a-zA-Z]+\b', text)
    word_count = len(words)
    sentence_count = max(len(sentences), 1)

    # syllable counter
    def count_syllables(word):
        word = word.lower()
        if len(word) <= 3:
            return 1
        word = re.sub(r'(?:[^laeiouy]es|ed|[^laeiouy]e)$', '', word)
        word = re.sub(r'^y', '', word)
        matches = re.findall(r'[aeiouy]{1,2}', word)
        return max(len(matches), 1)

    total_syllables = sum(count_syllables(w) for w in words)
    avg_sentence_length = word_count / sentence_count
    avg_syllables_per_word = total_syllables / max(word_count, 1)

    # Flesch-Kincaid Grade Level
    fk_grade = (0.39 * avg_sentence_length) + (11.8 * avg_syllables_per_word) - 15.59

    # Gunning Fog Index
    complex_words = sum(1 for w in words if count_syllables(w) >= 3)
    fog = 0.4 * (avg_sentence_length + 100 * complex_words / max(word_count, 1))

    # Coleman-Liau Index
    letters = sum(len(w) for w in words)
    avg_letters = letters / max(word_count, 1) * 100
    avg_sentences = sentence_count / max(word_count, 1) * 100
    cli = 0.0588 * avg_letters - 0.296 * avg_sentences - 15.8

    return {
        "flesch_kincaid_grade": round(fk_grade, 2),
        "gunning_fog": round(fog, 2),
        "coleman_liau_index": round(cli, 2),
        "word_count": word_count,
        "sentence_count": sentence_count,
        "syllable_count": total_syllables,
    }


# ---------------------------------------------------------------------------
# 6. tone_mix
# ---------------------------------------------------------------------------
def tone_mix(text: str, primary: str, secondary: str, ratio: float = 0.7) -> dict:
    """Mix two tones at given ratio via LLM.

    Args:
        text: input text
        primary: primary tone name (e.g. 'formal', 'casual')
        secondary: secondary tone name
        ratio: 0.0-1.0 weight toward primary tone

    Returns dict with keys: original, mixed_text, primary, secondary, ratio.
    """
    pct_primary = int(ratio * 100)
    pct_secondary = 100 - pct_primary
    sys_prompt = (
        f"You are a tone mixing expert. Rewrite the following text blending "
        f"two tones: {primary} ({pct_primary}%) and {secondary} ({pct_secondary}%). "
        f"The result should feel natural, not forced. Return only the rewritten text."
    )
    mixed = _llm(sys_prompt, text, temperature=0.7)
    return {
        "original": text,
        "mixed_text": mixed,
        "primary": primary,
        "secondary": secondary,
        "ratio": ratio,
    }


# ---------------------------------------------------------------------------
# 7. formality_adjust
# ---------------------------------------------------------------------------
def formality_adjust(text: str, level: int = 50) -> dict:
    """Adjust text formality from 0 (casual) to 100 (formal).

    Returns dict with keys: original, adjusted_text, level.
    """
    if level <= 20:
        desc = "extremely casual, slang-heavy, use contractions and colloquialisms"
    elif level <= 40:
        desc = "casual and conversational, like chatting with a friend"
    elif level <= 60:
        desc = "neutral, balanced between casual and formal"
    elif level <= 80:
        desc = "professional and polished, suitable for business communication"
    else:
        desc = "highly formal and academic, precise vocabulary, no contractions"

    sys_prompt = (
        f"You are a formality adjuster. Rewrite the text to be {desc}. "
        f"Target formality level: {level}/100. Return only the rewritten text."
    )
    adjusted = _llm(sys_prompt, text, temperature=0.6)
    return {"original": text, "adjusted_text": adjusted, "level": level}


# ---------------------------------------------------------------------------
# 8. preserve_formatting
# ---------------------------------------------------------------------------
def preserve_formatting(original: str, humanized: str) -> str:
    """Re-apply original formatting (bullets, numbered lists, headers) to humanized text.

    Identifies formatting patterns in original and applies them to humanized output.
    Returns formatted string.
    """
    orig_lines = original.split('\n')
    hum_lines = humanized.split('\n')

    # Build prefix map: line index -> formatting prefix
    prefix_pattern = re.compile(
        r'^(\s*(?:'
        r'[-*•]\s+'           # bullet points
        r'|\d+[.)]\s+'       # numbered lists
        r'|#{1,6}\s+'        # markdown headers
        r'|>\s?'             # blockquotes
        r'))'
    )

    orig_prefixes = []
    for line in orig_lines:
        m = prefix_pattern.match(line)
        orig_prefixes.append(m.group(1) if m else None)

    hum_prefixes = []
    for line in hum_lines:
        m = prefix_pattern.match(line)
        hum_prefixes.append(m.group(1) if m else None)

    result = []
    hum_idx = 0
    for i, orig_prefix in enumerate(orig_prefixes):
        if orig_prefix and hum_idx < len(hum_lines):
            # Strip any existing prefix from humanized line
            hum_line = hum_lines[hum_idx] if hum_idx < len(hum_lines) else ""
            if hum_prefixes[hum_idx]:
                hum_line = hum_line[len(hum_prefixes[hum_idx]):]
            result.append(orig_prefix + hum_line)
            hum_idx += 1
        elif hum_idx < len(hum_lines):
            result.append(hum_lines[hum_idx])
            hum_idx += 1

    # Append remaining humanized lines
    while hum_idx < len(hum_lines):
        result.append(hum_lines[hum_idx])
        hum_idx += 1

    return '\n'.join(result)


# ---------------------------------------------------------------------------
# 9. detect_citations
# ---------------------------------------------------------------------------
def detect_citations(text: str) -> list:
    """Detect citation spans in text.

    Returns list of dicts: [{text: str, start: int, end: int, type: str}].
    Detects: APA, MLA, Chicago, IEEE, bracket refs, footnote refs.
    """
    citations = []
    patterns = [
        # APA: (Author, Year) or (Author, Year, p. X)
        (r'\([A-Z][a-zA-Z]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-zA-Z]+))?,?\s*\d{4}(?:,\s*p+\.\s*\d+)?\)', "APA"),
        # MLA: (Author Page)
        (r'\([A-Z][a-zA-Z]+\s+\d+\)', "MLA"),
        # Author (Year)
        (r'[A-Z][a-zA-Z]+\s+\(\d{4}\)', "Author-Year"),
        # Bracket numbers: [1], [2,3], [1-5]
        (r'\[\d+(?:\s*[,–-]\s*\d+)*\]', "Numeric"),
        # Footnote refs: [^1], [^note]
        (r'\[\^[^\]]+\]', "Footnote"),
        # DOI
        (r'(?:doi:\s*|https?://doi\.org/)\S+', "DOI"),
        # Chicago superscript style: endnote numbers
        (r'(?<=\w)\d+(?=\s*$)', "Endnote"),
    ]
    for pattern, ctype in patterns:
        for m in re.finditer(pattern, text, re.MULTILINE):
            # skip Chicago endnote false positives (numbers in text)
            if ctype == "Endnote" and len(m.group()) > 3:
                continue
            citations.append({
                "text": m.group(),
                "start": m.start(),
                "end": m.end(),
                "type": ctype,
            })
    # Sort by position, remove overlaps
    citations.sort(key=lambda c: c["start"])
    filtered = []
    last_end = -1
    for c in citations:
        if c["start"] >= last_end:
            filtered.append(c)
            last_end = c["end"]
    return filtered


# ---------------------------------------------------------------------------
# 10. detect_code_blocks
# ---------------------------------------------------------------------------
def detect_code_blocks(text: str) -> list:
    """Detect code blocks in text.

    Returns list of dicts: [{text: str, start: int, end: int, language: str}].
    Detects fenced (```...```) and indented code blocks.
    """
    blocks = []
    # Fenced code blocks
    for m in re.finditer(r'```(\w*)\n(.*?)```', text, re.DOTALL):
        blocks.append({
            "text": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "language": m.group(1) or "unknown",
        })
    # Inline code (backtick-wrapped)
    for m in re.finditer(r'`([^`\n]+)`', text):
        blocks.append({
            "text": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "language": "inline",
        })
    # Indented code blocks (4+ spaces or tab, on consecutive lines)
    for m in re.finditer(r'(?:^(?: {4}|\t).+\n?)+', text, re.MULTILINE):
        # skip if already captured as fenced
        overlap = False
        for b in blocks:
            if m.start() >= b["start"] and m.end() <= b["end"]:
                overlap = True
                break
        if not overlap:
            blocks.append({
                "text": m.group(0),
                "start": m.start(),
                "end": m.end(),
                "language": "indented",
            })
    blocks.sort(key=lambda b: b["start"])
    return blocks


# ---------------------------------------------------------------------------
# 11. reorder_paragraphs
# ---------------------------------------------------------------------------
def reorder_paragraphs(text: str) -> dict:
    """Randomly shuffle paragraph order.

    Returns dict with keys: original, reordered, mapping.
    mapping: list of (new_index, old_index) tuples showing where each original paragraph went.
    """
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p for p in paragraphs if p.strip()]
    indices = list(range(len(paragraphs)))
    new_order = indices[:]
    random.shuffle(new_order)
    reordered = [paragraphs[i] for i in new_order]
    mapping = list(enumerate(new_order))
    return {
        "original": text,
        "reordered": '\n\n'.join(reordered),
        "mapping": mapping,
    }


# ---------------------------------------------------------------------------
# 12. check_passive_voice
# ---------------------------------------------------------------------------
# Common irregular past participles for passive detection
_PAST_PARTICIPLES = (
    r'(?:been|become|begun|bitten|bled|blown|broken|bred|brought|built|'
    r'burnt|burst|bought|caught|chosen|clung|come|cost|crept|cut|'
    r'dealt|dug|done|drawn|drunk|driven|eaten|fallen|fed|felt|'
    r'fought|found|flown|forbidden|forgotten|forgiven|frozen|'
    r'given|gone|grown|had|heard|hidden|hit|held|hurt|kept|'
    r'knelt|known|laid|led|left|lent|let|lain|lost|made|meant|'
    r'met|paid|proven|put|read|rid|risen|run|rung|risen|'
    r'said|seen|sent|set|sewn|shaken|shone|shot|shown|'
    r'shrunk|shut|sung|sunk|sat|slept|slid|spoken|spent|'
    r'spun|spread|stood|stolen|stuck|stung|stunk|struck|'
    r'sworn|swept|swum|swung|taken|taught|torn|told|'
    r'thought|thrown|understood|woken|worn|won|withdrawn|written)'
)


def check_passive_voice(text: str) -> list:
    """Detect passive voice sentences in text.

    Uses regex heuristics: be-verb + past participle patterns.
    Returns list of dicts: [{sentence: str, index: int, hint: str}].
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    be_verbs = r'(?:am|is|are|was|were|be|been|being|get|got|getting|gets|gotten|has|have|had)'
    pp = _PAST_PARTICIPLES
    # Pattern: be-verb + (adverbs)* + past participle (irregular or -ed/-en)
    pattern = re.compile(
        rf'\b{be_verbs}\b(?:\s+\w+(?:ly|)){{0,2}}\s+(?:\b{pp}\b|\b\w+(?:ed|en)\b)',
        re.IGNORECASE,
    )
    results = []
    for idx, sent in enumerate(sentences):
        if pattern.search(sent):
            results.append({
                "sentence": sent.strip(),
                "index": idx,
                "hint": "passive voice detected",
            })
    return results


# ---------------------------------------------------------------------------
# CLI test block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== text_processing module test ===\n")

    sample = (
        "The report was written by the team. It has been observed that "
        "many students struggle with passive constructions. The data was "
        "analyzed carefully. John went to the store."
    )

    # Test readability scores (no LLM)
    print("--- get_readability_scores ---")
    scores = get_readability_scores(sample)
    print(json.dumps(scores, indent=2))

    # Test check_passive_voice (no LLM)
    print("\n--- check_passive_voice ---")
    passive = check_passive_voice(sample)
    for p in passive:
        print(f"  [{p['index']}] {p['sentence']}")

    # Test generate_sentence_diff (no LLM)
    print("\n--- generate_sentence_diff ---")
    diff = generate_sentence_diff("The cat sat on the mat", "A cat was sitting on the mat")
    print(" ".join(f"{'+' if d['type']=='added' else '-' if d['type']=='removed' else '='}{d['text']}" for d in diff))

    # Test detect_citations (no LLM)
    print("\n--- detect_citations ---")
    cite_text = "As shown in prior work (Smith, 2020) and [1], the method works (Doe & Lee, 2023, p. 12)."
    for c in detect_citations(cite_text):
        print(f"  {c['type']}: '{c['text']}' at {c['start']}-{c['end']}")

    # Test detect_code_blocks (no LLM)
    print("\n--- detect_code_blocks ---")
    code_text = "Use `print(x)` for output.\n\n```python\nprint('hello')\n```"
    for b in detect_code_blocks(code_text):
        print(f"  {b['language']}: '{b['text']}' at {b['start']}-{b['end']}")

    # Test reorder_paragraphs (no LLM)
    print("\n--- reorder_paragraphs ---")
    para_text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    result = reorder_paragraphs(para_text)
    print(f"  mapping: {result['mapping']}")

    # Test preserve_formatting (no LLM)
    print("\n--- preserve_formatting ---")
    formatted = preserve_formatting(
        "- Item one\n- Item two\n- Item three",
        "First item\nSecond item\nThird item",
    )
    print(f"  {formatted}")

    # LLM tests (require running server)
    try:
        print("\n--- apply_strength (medium) ---")
        r = apply_strength(sample, "medium")
        print(f"  {r['rewritten'][:120]}...")

        print("\n--- passive_to_active ---")
        r = passive_to_active(sample)
        print(f"  {r['active_text'][:120]}...")
    except Exception as e:
        print(f"\n  [LLM tests skipped - server not available: {e}]")

    print("\nDone.")
