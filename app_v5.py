"""
HumanizeAI v3 - Multi-pass AI text humanizer
Features: chunking, multi-model, tone presets, file upload, detection scoring
"""

import json
import math
import random
import re
import sys
import cgi
import io
import zipfile
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from textwrap import dedent
from datetime import datetime

# ─── Config ───────────────────────────────────────────────────────────
import os
LLM_BASE = os.environ.get("LLM_BASE", "http://localhost:20128/v1")
LLM_KEY = os.environ.get("LLM_KEY", "123456")
LLM_MODEL = os.environ.get("LLM_MODEL", "cx/gpt-5.5")
PORT = int(os.environ.get("PORT", 7860))

MODEL_OPTIONS = {
    "cx/gpt-5.5": "Recommended (GPT-5.5, best quality+length)",
    "ag/claude-sonnet-4-6": "Best Quality (Claude Sonnet, ~10s/pass)",
    "ag/gemini-3-flash": "Fast (Gemini 3 Flash, ~5s/pass)",
    "ag/gemini-3.5-flash-low": "Fastest (Gemini 3.5, ~3s/pass)",
    "ag/gpt-oss-120b-medium": "Balanced (GPT-OSS 120B, ~8s/pass)",
    "ag/claude-opus-4-6-thinking": "Premium (Opus Thinking, ~25s/pass)",
    "cx/gpt-5.4": "High Quality (GPT-5.4, ~8s/pass)",
    "cx/gpt-5.4-mini": "Fast Quality (GPT-5.4 Mini, ~4s/pass)",
}

# New feature configs
MULTI_MODEL = False  # smart routing: use single fast model for all passes
AUTO_RETRY = True   # re-process if score still > 40
CHUNK_SIZE = 400    # bigger chunks = fewer chunks, better context, less overlap waste
MIN_LENGTH_RATIO = 0.80
PARALLEL_CHUNKS = 4  # max concurrent chunk workers
HISTORY = []  # in-memory history, max 10
# Full-result cache (same input = instant output)
RESULT_CACHE = {}  # {key: {output, score, input_words, timestamp}}
RESULT_CACHE_MAX = 50
RESULT_CACHE_TTL = 86400  # 24 hours

def _result_cache_key(text, model, tone, passes):
    import hashlib
    key_str = f"{text[:1000]}|{model}|{tone}|{passes}"
    return hashlib.md5(key_str.encode()).hexdigest()

def result_cache_get(key):
    if key in RESULT_CACHE:
        entry = RESULT_CACHE[key]
        if time.time() - entry['timestamp'] < RESULT_CACHE_TTL:
            return entry
    return None

def result_cache_set(key, output, score, input_words):
    if len(RESULT_CACHE) >= RESULT_CACHE_MAX:
        oldest = min(RESULT_CACHE.items(), key=lambda x: x[1]['timestamp'])
        del RESULT_CACHE[oldest[0]]
    RESULT_CACHE[key] = {
        'output': output, 'score': score,
        'input_words': input_words, 'timestamp': time.time()
    }


TONE_PRESETS = {
    "academic": "Maintain formal tone but sound human. Use minimal contractions. Add phrases like 'it appears that', 'the evidence suggests', 'one could argue'. Avoid slang.",
    "casual": "Maximum informality. Use lots of contractions, slang, fillers. Sound like talking to a friend. Add 'like', 'you know', 'I mean', fragments.",
    "business": "Professional but not robotic. Moderate contractions, clean structure, minimal fillers. Sound like a knowledgeable colleague presenting.",
}

MAX_HISTORY = 20


def save_history(entry):
    """Save humanize result to history."""
    HISTORY.insert(0, entry)
    if len(HISTORY) > MAX_HISTORY:
        HISTORY.pop()


def extract_docx_text(data):
    """Extract text from .docx file bytes."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml_content = z.read("word/document.xml")
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        root = ET.fromstring(xml_content)
        texts = []
        for para in root.iter(f"{{{ns}}}p"):
            para_text = ""
            for run in para.iter(f"{{{ns}}}t"):
                if run.text:
                    para_text += run.text
            if para_text.strip():
                texts.append(para_text.strip())
        return "\n".join(texts)
    except Exception as e:
        raise RuntimeError(f"Failed to parse .docx: {e}")


def make_docx(text):
    """Create a minimal .docx file from text."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        z.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
        paragraphs = text.split("\n")
        body = ""
        for p in paragraphs:
            escaped = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body += f'<w:p><w:r><w:t xml:space="preserve">{escaped}</w:t></w:r></w:p>'
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body}</w:body></w:document>'
        )
        z.writestr("word/document.xml", doc_xml)
        z.writestr("word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '</Relationships>')
    buf.seek(0)
    return buf.read()

SYNONYMS = {
    "important": ["key", "big", "major", "critical"],
    "shows": ["demonstrates", "reveals", "highlights", "proves"],
    "use": ["rely on", "go with", "pick", "choose"],
    "good": ["solid", "decent", "fine", "great"],
    "change": ["shift", "move", "switch", "swap"],
    "help": ["support", "assist", "guide", "aid"],
    "big": ["large", "huge", "major", "massive"],
    "make": ["build", "create", "put together", "produce"],
}

FILLER_PHRASES = ["um", "like", "so basically", "maybe", "I think", "probably", "kind of", "sort of", "you know what", "the thing is"]

PRONOUN_STARTERS = ["I've noticed that", "From what I've seen,", "In my experience,", "I'd say that", "If you ask me,", "To be honest,"]


# ─── LLM call ─────────────────────────────────────────────────────────

def llm_call(prompt, system="", temperature=0.9, model=None):
    """Call local LLM with given params."""
    if model is None:
        model = LLM_MODEL
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 8192,
        "stream": False,
    }

    req = urllib.request.Request(
        f"{LLM_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            raw = data["choices"][0]["message"].get("content") or ""
            content = raw.strip()
            if not content:
                return ""
            # Strip thinking/reasoning tags
            # Try multiple tag patterns: <think>, <reasoning>, <search>
            cleaned = content
            for tag in ['think', 'reasoning', 'search', 'thinking']:
                cleaned = re.sub(rf'<{tag}>.*?</{tag}>', '', cleaned, flags=re.DOTALL).strip()
            # If stripping removed everything, content was all in think tags
            # Try to use the raw content without tags
            if not cleaned:
                # Last resort: strip ALL tags and use remaining text
                cleaned = re.sub(r'<[^>]+>', '', content).strip()
            return cleaned if cleaned else content
    except urllib.error.HTTPError as e:
        error_body = ""
        if hasattr(e, 'read'):
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except:
                error_body = str(e)
        raise RuntimeError(f"LLM returned {e.code}: {error_body[:300] if error_body else 'Bad Request'}")



# ─── LLM Response Cache ─────────────────────────────────────────────
import hashlib
_LLM_CACHE = {}
_LLM_CACHE_HITS = 0
_LLM_CACHE_MISSES = 0

def cached_llm_call(prompt, system="", temperature=0.9, model=None):
    """LLM call with hash-based caching. Same input = cached result."""
    global _LLM_CACHE, _LLM_CACHE_HITS, _LLM_CACHE_MISSES
    # Use only user prompt for cache key (system prompt changes with word counts)
    cache_key = hashlib.md5(f"{model}:{temperature}:{prompt[:1000]}".encode()).hexdigest()
    if cache_key in _LLM_CACHE:
        _LLM_CACHE_HITS += 1
        return _LLM_CACHE[cache_key]
    _LLM_CACHE_MISSES += 1
    result = llm_call(prompt, system=system, temperature=temperature, model=model)
    if result and len(result) > 50:
        _LLM_CACHE[cache_key] = result
    return result

# ─── Pass 1: Structure rewrite ────────────────────────────────────────

def pass1_rewrite(text, model=None, tone="casual"):
    """Rewrite with varied sentence structure while keeping similar length."""
    word_count = len(text.split())
    min_words = int(word_count * 0.9)
    max_words = int(word_count * 1.15)

    if tone == "academic":
        system = """You are rewriting text for an academic thesis/report. Maintain FORMAL academic tone throughout.

ABSOLUTE CRITICAL RULE — LENGTH:
Keep the output approximately the same length as the input. DO NOT summarize. DO NOT compress. DO NOT shorten. DO NOT remove any sentences.
Every single idea in the input must appear in the output. If you skip an idea, you fail.

Style rules:
1. Vary sentence length: mix medium (12-18 words) with longer analytical sentences (25-40 words). Avoid very short fragments.
2. DO NOT use contractions. Use full forms: "it is", "do not", "cannot", "will not".
3. Use academic hedging: "it appears that", "the evidence suggests", "it can be observed that", "this indicates", "it is evident that".
4. Use academic transitions: "Furthermore", "Moreover", "In addition", "Consequently", "Notably", "In particular", "It is worth noting that".
5. Keep ALL facts, data, names, numbers, citations, and references intact.
6. Keep the same language as the original.
7. NEVER modify, remove, or reformat any text inside [KEEP:...] brackets — copy them exactly as-is.
8. Write like a knowledgeable researcher presenting findings
8. Use passive voice where appropriate for academic objectivity.
9. Add discourse markers: "In this context", "From an analytical perspective", "This warrants further consideration".

NEVER: use slang, filler words (honestly, basically, you know, I think, like, um, well), contractions, exclamation marks, rhetorical questions, fragments, or casual phrases.
NEVER: use "delve", "leverage", "utilize", "facilitate", "comprehensive", "robust", "streamline", "landscape", "tapestry", "pivotal", "crucial".

Output ONLY the rewritten text. No explanations, no notes, no meta-commentary."""
    else:
        system = """You are rewriting text to sound human-written, NOT AI-generated. This is your ONLY job.

ABSOLUTE CRITICAL RULE — LENGTH:
Keep the output approximately the same length as the input. DO NOT summarize. DO NOT compress. DO NOT shorten. DO NOT remove any sentences.
Every single idea in the input must appear in the output. If you skip an idea, you fail.

Style rules:
1. Vary sentence length: mix very short (3-8 words) with longer ones (20-35 words).
2. Use contractions: don't, isn't, it's, we're, they've, won't, can't, I'm.
3. Add 2-3 filler phrases naturally: "honestly", "basically", "you know", "I think", "the thing is", "to be fair", "I mean".
4. Replace formal transitions: "Furthermore"→"Also", "However"→"But", "Therefore"→"So", "Moreover"→"Plus".
5. Keep ALL facts, data, names, numbers, and key information intact.
6. Keep the same language as the original.
7. NEVER modify, remove, or reformat any text inside [KEEP:...] brackets — copy them exactly as-is.
8. Write like a real person explaining to a friend

NEVER: summarize, compress, remove details, add new information, use "delve", "leverage", "utilize", "facilitate", "comprehensive", "robust", "streamline", "landscape", "tapestry", "pivotal", "crucial".

Output ONLY the rewritten text. No explanations, no notes, no meta-commentary."""

    return cached_llm_call(text, system=system, temperature=0.65 if tone == "academic" else 0.70, model=model)


# ─── Pass 2: Burstiness injection ────────────────────────────────────

def pass2_burstiness(text, model=None, tone="casual"):
    """Inject sentence length variation and imperfections while keeping length."""
    word_count = len(text.split())

    if tone == "academic":
        system = """You are editing academic text to improve readability while maintaining formal tone. Keep the text approximately the same length as the input.

Make these specific changes:
1. Find the LONGEST sentence and split it into two shorter ones — both must remain formal.
2. Find two SHORT consecutive sentences and combine them into one using appropriate academic connectors (moreover, furthermore, consequently, in addition).
3. Add exactly 2 academic hedging phrases from: "it is worth noting that", "it appears that", "this suggests that", "it can be observed that", "notably", "in particular", "from an analytical perspective".
4. Ensure all transitions are formal: use "Furthermore", "Moreover", "In addition", "Consequently", "Notably", "In this context".
5. Add one analytical observation: "This finding is particularly significant because..." or "It is evident that..." or "The implications of this are noteworthy."
6. DO NOT use contractions. Maintain full forms throughout.

Keep all facts, citations, and references intact. Output ONLY the edited text."""
    else:
        system = """You are editing text to make it sound more human. Keep the text approximately the same length as the input.

Make these specific changes:
1. Find the LONGEST sentence and split it into two shorter ones.
2. Find two SHORT consecutive sentences and combine them into one.
3. Add exactly 2 casual phrases from: "honestly", "I think", "the thing is", "you know", "I mean", "look".
4. Replace any remaining formal words with casual ones (But instead of However, So instead of Therefore, Also instead of Furthermore).
5. Add one self-correction or hedging phrase: "well, it's not exactly straightforward but..." or "I'd say" or "from what I can tell".
6. If you see "it is", "they are", "we are", "do not" — change to contractions.

Keep all facts intact. Output ONLY the edited text."""

    return cached_llm_call(text, system=system, temperature=0.85 if tone == "academic" else 0.95, model=model)


# ─── Pass 3: Final polish ─────────────────────────────────────────────

def pass3_polish(text, model=None, tone="casual"):
    """Final pass: remove AI tells, add personality."""
    word_count = len(text.split())

    if tone == "academic":
        system = """You are a final editor for academic text. Clean up remaining AI patterns while maintaining formal tone. Keep the text approximately the same length as the input.

Scan for and fix:
- Any remaining AI words: "delve", "dive into", "explore", "landscape", "tapestry", "crucial", "pivotal", "leverage", "utilize", "facilitate", "comprehensive", "robust", "streamline", "underscore", "multifaceted", "holistic", "paradigm". Replace with simple academic alternatives.
- Sentences that all have similar length — break one long, merge two short. Keep both formal.
- Add 1 academic analytical phrase: "this is particularly significant", "it is worth highlighting", "from an analytical standpoint", "this warrants consideration".
- Ensure no contractions exist. Use full forms only.
- Ensure no informal language, slang, or casual phrases exist.

DO NOT add personal touches like "in my view" or "from my experience" — maintain academic objectivity.

Output ONLY the final polished text. No notes or explanations."""
    else:
        system = """You are a final editor. Clean up remaining AI patterns. Keep the text approximately the same length as the input.

Scan for and fix:
- Any "it is" → "it's", "do not" → "don't", "cannot" → "can't", etc.
- Any of these AI words: "delve", "dive into", "explore", "landscape", "tapestry", "crucial", "pivotal", "leverage", "utilize", "facilitate", "comprehensive", "robust", "streamline", "underscore", "multifaceted", "holistic", "paradigm". Replace with simple alternatives.
- Sentences that all have similar length — break one long, merge two short.
- Add 1 personal touch: "from my experience", "in my view", "I've found that", "the way I see it".

Output ONLY the final polished text. No notes or explanations."""

    return cached_llm_call(text, system=system, temperature=0.55 if tone == "academic" else 0.60, model=model)


# ─── Post-processing ─────────────────────────────────────────────────

CONTRACTIONS = {
    "do not": "don't", "does not": "doesn't", "did not": "didn't",
    "is not": "isn't", "are not": "aren't", "was not": "wasn't",
    "will not": "won't", "would not": "wouldn't", "could not": "couldn't",
    "should not": "shouldn't", "cannot": "can't", "can not": "can't",
    "have not": "haven't", "has not": "hasn't", "had not": "hadn't",
    "it is": "it's", "that is": "that's", "there is": "there's",
    "I am": "I'm", "I have": "I've", "I will": "I'll", "I would": "I'd",
    "we are": "we're", "we have": "we've", "we will": "we'll",
    "they are": "they're", "they have": "they've", "they will": "they'll",
    "let us": "let's", "who is": "who's", "what is": "what's",
    "you are": "you're", "you have": "you've", "you will": "you'll",
}

TRANSITION_KILLERS = [
    ("Furthermore", "Also"), ("Moreover", "Plus"), ("Nevertheless", "Still"),
    ("Consequently", "So"), ("In conclusion", "To wrap up"),
    ("In addition", "On top of that"), ("Therefore", "So"),
    ("However", "But"), ("Additionally", "Also"), ("Thus", "So"),
    ("Subsequently", "Then"), ("Hence", "So"),
]

AI_WORDS = {
    "delve": "dig into", "leverage": "use", "utilize": "use",
    "facilitate": "help with", "comprehensive": "thorough", "robust": "solid",
    "streamline": "speed up", "landscape": "field", "tapestry": "mix",
    "pivotal": "important", "underscore": "highlight", "multifaceted": "complex",
    "holistic": "overall", "paradigm": "approach", "foster": "encourage",
    "paramount": "key", "seamless": "smooth", "unprecedented": "new",
    "realm": "area", "plethora": "lots", "myriad": "many",
    "endeavor": "effort", "meticulous": "careful", "meticulously": "carefully",
    "nuanced": "detailed", "intricate": "complex", "burgeoning": "growing",
    "trajectory": "path", "catalyst": "trigger", "catalyze": "trigger",
    "ameliorate": "improve", "exacerbate": "worsen", "mitigate": "reduce",
    "subsequent": "next", "subsequently": "then", "prior to": "before",
    "in order to": "to", "due to the fact that": "because",
    "it is important to note that": "", "it is worth noting that": "",
}

# Word length reduction — replace long words with short common ones
WORD_SIMPLIFY = {
    "significantly": "a lot", "approximately": "about", "demonstrate": "show",
    "implement": "set up", "implementing": "setting up", "implementation": "setup",
    "demonstrates": "shows", "demonstrated": "showed",
    "substantial": "big", "substantially": "a lot", "considerable": "big",
    "considerably": "a lot", "fundamental": "basic", "fundamentally": "basically",
    "contemporary": "modern", "contemporaneous": "same-time",
    "organizations": "companies", "organization": "company",
    "infrastructure": "setup", "capabilities": "skills", "capability": "skill",
    "methodology": "method", "methodologies": "methods",
    "incorporate": "add", "incorporating": "adding", "incorporated": "added",
    "acknowledge": "admit", "acknowledged": "admitted",
    "perspective": "view", "perspectives": "views",
    "particularly": "especially", "particularly": "mainly",
    "necessitate": "need", "necessitates": "needs", "necessitated": "needed",
    "constitute": "make up", "constitutes": "makes up",
    "predominantly": "mostly", "predominant": "main",
    "characteristic": "feature", "characteristics": "features",
    "effectively": "well", "efficiently": "fast",
    "proliferation": "spread", "revolutionize": "change",
    "revolutionized": "changed", "revolutionizing": "changing",
    "transformative": "big", "transformed": "changed",
    "extraordinary": "great", "remarkable": "great",
    "indispensable": "key", "invaluable": "very useful",
}

# Fragment insertions — humans use these
FRAGMENTS = [
    "Honestly.", "I mean,", "Look,", "The thing is,",
    "Here's the deal:", "Truth is,", "And honestly?",
    "That said,", "Fair enough,", "But here's what matters:",
    "The way I see it,", "In my experience,", "From what I've seen,",
    "It's not that simple though.", "But that's a whole other topic.",
    "Pretty straightforward, right?", "Kind of obvious when you think about it.",
]

# Colloquial replacements — humans use these naturally
COLLOQUIAL = {
    "going to": "gonna", "want to": "wanna", "got to": "gotta",
    "kind of": "kinda", "sort of": "sorta", "a lot of": "lots of",
    "in my opinion": "if you ask me", "as a result": "so",
    "for example": "like", "in other words": "basically",
    "at the end of the day": "ultimately", "in fact": "actually",
    "on the other hand": "but then again", "as well as": "and",
    "due to": "because of", "according to": "per",
}

# Rhetorical questions — humans ask these mid-paragraph
RHETORICAL_QUESTIONS = [
    "And that's a big deal, right?",
    "So what does that actually mean?",
    "Is that always the case? Not really.",
    "Sound familiar?",
    "Pretty wild when you think about it.",
]

# Ultra-short sentences — humans drop these randomly
ULTRA_SHORT = [
    "Big deal.", "Not ideal.", "True.", "Fair point.",
    "That's huge.", "Not great.", "Simple as that.", "Think about it.",
    "Huge mistake.", "Makes sense.", "Kind of obvious.", "Not easy.",
    "Pretty standard.", "Not surprising.", "It depends.", "Fair enough.",
]

# Em-dash patterns — humans use these naturally
EMDASH_PAIRS = [
    ("and", "— and"),  # only some, not all
    ("which", "— which"),
]

def post_process(text):
    """Apply mechanical humanization."""
    # Contractions
    for full, short in CONTRACTIONS.items():
        pattern = re.compile(r'\b' + re.escape(full) + r'\b', re.IGNORECASE)
        text = pattern.sub(short, text)

    # Formal transitions
    for formal, casual in TRANSITION_KILLERS:
        pattern = re.compile(r'(^|\.\s+)' + re.escape(formal) + r'\b', re.IGNORECASE)
        text = pattern.sub(lambda m: m.group(1) + casual, text)

    # AI words
    for ai_word, simple in AI_WORDS.items():
        pattern = re.compile(r'\b' + re.escape(ai_word) + r'\b', re.IGNORECASE)
        text = pattern.sub(simple, text)

    # Word simplification (long words → short common ones)
    for long_word, short_word in WORD_SIMPLIFY.items():
        pattern = re.compile(r'\b' + re.escape(long_word) + r'\b', re.IGNORECASE)
        text = pattern.sub(short_word, text)

    # Cleanup
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─── Detection Analyzer ───────────────────────────────────────────────

def calc_burstiness(text):
    """Sentence length variation — AI = low (0.2-0.4), Human = high (0.6-1.2)"""
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
    if len(sentences) < 2:
        return {'cv': 0, 'score': 'N/A'}
    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return {'cv': 0, 'score': 'N/A'}
    variance = sum((l - mean)**2 for l in lengths) / len(lengths)
    std = math.sqrt(variance)
    cv = std / mean
    # Score: 0-0.3 = AI, 0.3-0.6 = Mixed, 0.6+ = Human
    if cv >= 0.7:
        score = 'human'
    elif cv >= 0.45:
        score = 'mixed'
    else:
        score = 'ai'
    return {'cv': round(cv, 3), 'score': score, 'min': min(lengths), 'max': max(lengths), 'mean': round(mean, 1)}


def calc_ai_tells(text):
    """Count AI-specific patterns"""
    tells = {
        'transitions': len(re.findall(r'\b(Furthermore|Moreover|Additionally|Consequently|Nevertheless|In addition|In conclusion|It is important)\b', text, re.I)),
        'ai_words': len(re.findall(r'\b(delve|leverage|utilize|facilitate|streamline|underscore|foster|comprehensive|robust|multifaceted|holistic|pivotal|paramount|seamless|unprecedented|plethora|myriad|endeavor|nuanced|intricate|burgeoning|trajectory|catalyst|ameliorate|exacerbate)\b', text, re.I)),
        'no_contractions': len(re.findall(r"\b(do not|does not|is not|are not|was not|were not|will not|cannot|can not|have not|has not|it is|that is|there is|I am)\b", text, re.I)),
        'avg_word_len': round(sum(len(w) for w in text.split()) / max(len(text.split()), 1), 1),
    }
    total = tells['transitions'] + tells['ai_words'] + tells['no_contractions']
    words = len(text.split())
    density = round(total / max(words, 1) * 100, 2)
    return {**tells, 'total': total, 'density': density}


def calc_detection_score(text):
    """Combined AI detection likelihood score (0-100, higher = more AI-like)"""
    burst = calc_burstiness(text)
    tells = calc_ai_tells(text)
    
    # Burstiness component (40% weight)
    cv = burst['cv']
    if cv >= 0.7:
        burst_score = 15  # human-like
    elif cv >= 0.45:
        burst_score = 40
    else:
        burst_score = 75  # AI-like
    
    # AI tells component (35% weight)
    density = tells['density']
    if density <= 0.5:
        tell_score = 10
    elif density <= 2.0:
        tell_score = 35
    elif density <= 5.0:
        tell_score = 60
    else:
        tell_score = 85
    
    # Word length component (15% weight) — AI uses longer words
    avg_wl = tells['avg_word_len']
    if avg_wl <= 5.0:
        wl_score = 10
    elif avg_wl <= 5.8:
        wl_score = 35
    elif avg_wl <= 6.5:
        wl_score = 60
    else:
        wl_score = 85
    
    # Sentence uniformity (10% weight) — AI has very uniform paragraphs
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
    if len(sentences) >= 3:
        lengths = [len(s.split()) for s in sentences]
        # Check if 80%+ sentences are within ±5 words of mean
        mean = sum(lengths) / len(lengths)
        uniform_count = sum(1 for l in lengths if abs(l - mean) <= 5)
        uniformity = uniform_count / len(lengths)
        if uniformity >= 0.7:
            uniform_score = 70
        elif uniformity >= 0.5:
            uniform_score = 40
        else:
            uniform_score = 15
    else:
        uniform_score = 50
    
    final = round(burst_score * 0.40 + tell_score * 0.35 + wl_score * 0.15 + uniform_score * 0.10)
    
    if final <= 30:
        grade = 'HUMAN'
    elif final <= 50:
        grade = 'LIKELY HUMAN'
    elif final <= 70:
        grade = 'MIXED'
    else:
        grade = 'LIKELY AI'
    
    return {
        'score': final,
        'grade': grade,
        'burstiness': burst,
        'ai_tells': tells,
        'components': {
            'burstiness': burst_score,
            'ai_tells': tell_score,
            'word_length': wl_score,
            'uniformity': uniform_score,
        }
    }


def burstiness_inject(text):
    """Mechanically inject sentence length variation to raise burstiness CV."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 3:
        return text
    
    result = []
    i = 0
    fragment_idx = 0
    
    while i < len(sentences):
        sent = sentences[i].strip()
        if not sent:
            i += 1
            continue
        
        words = sent.split()
        
        # Every 2nd sentence: add a fragment (more aggressive)
        if i > 0 and i % 2 == 0 and fragment_idx < len(FRAGMENTS):
            frag = FRAGMENTS[fragment_idx]
            prev_text = result[-1] if result else ''
            frag_first = frag.split('.')[0].split(',')[0].split(':')[0]
            prev_first = prev_text.split('.')[0].split(',')[0].split(':')[0] if prev_text else ''
            if frag_first.lower() != prev_first.lower():
                result.append(frag)
            fragment_idx += 1
        
        # Split ALL sentences over 15 words (aggressive splitting for more variation)
        if len(words) > 15:
            split_at = -1
            for j, w in enumerate(words):
                if j > 4 and j < len(words) - 3:
                    if w.lower() in (',', 'and', 'but', 'while', 'which', 'as', 'that', 'because', 'so', 'or'):
                        split_at = j
                        break
            if split_at > 0:
                part1 = ' '.join(words[:split_at]).rstrip(',') + '.'
                part1 = part1[0].upper() + part1[1:] if part1 else part1
                if words[split_at] == ',':
                    rest = words[split_at+1:]
                else:
                    rest = words[split_at:]
                part2 = ' '.join(rest)
                part2 = part2[0].upper() + part2[1:] if part2 else ''
                result.append(part1)
                if part2:
                    result.append(part2)
                i += 1
                continue
        
        # Merge medium sentence (12-20 words) with next short sentence (3-8 words)
        if 12 <= len(words) <= 20 and i + 1 < len(sentences):
            next_sent = sentences[i+1].strip()
            next_words = next_sent.split() if next_sent else []
            if 3 <= len(next_words) <= 8:
                merged = sent.rstrip('.') + ' — ' + next_sent[0].lower() + next_sent[1:]
                result.append(merged)
                i += 2
                continue
        
        result.append(sent)
        i += 1
    
    return ' '.join(result)


def emdash_inject(text, rate=0.15):
    """Add em-dashes to ~15% of qualifying positions."""
    import random
    random.seed(hash(text) % 2**32)
    
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    
    for sent in sentences:
        words = sent.split()
        modified = False
        for i, w in enumerate(words):
            if not modified and random.random() < rate:
                for trigger, replacement in EMDASH_PAIRS:
                    if w.lower() == trigger and i > 2 and i < len(words) - 2:
                        words[i] = replacement
                        modified = True
                        break
        result.append(' '.join(words))
    
    return ' '.join(result)


def colloquial_inject(text):
    """Replace formal phrases with colloquial equivalents."""
    for formal, casual in COLLOQUIAL.items():
        pattern = re.compile(r'\b' + re.escape(formal) + r'\b', re.IGNORECASE)
        text = pattern.sub(casual, text)
    return text


def rhetorical_inject(text):
    """Insert 1-2 rhetorical questions per ~300 words."""
    import random
    random.seed(hash(text) % 2**32 + 1)
    
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 5:
        return text
    
    word_count = len(text.split())
    num_inserts = min(max(1, word_count // 300), 3)
    
    positions = sorted(random.sample(range(2, len(sentences) - 1), min(num_inserts, len(sentences) - 3)))
    questions = random.sample(RHETORICAL_QUESTIONS, min(num_inserts, len(RHETORICAL_QUESTIONS)))
    
    for i, (pos, q) in enumerate(zip(positions, questions)):
        sentences.insert(pos + i, q)
    
    return ' '.join(sentences)


def ultra_short_inject(text):
    """Inject ultra-short 1-3 word sentences to create burstiness chaos."""
    import random
    random.seed(hash(text) % 2**32 + 7)
    
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 4:
        return text
    
    word_count = len(text.split())
    # 1 ultra-short per ~100 words (more aggressive for higher burstiness)
    num_inserts = min(max(2, word_count // 100), 8)
    
    positions = sorted(random.sample(range(1, len(sentences) - 1), min(num_inserts, len(sentences) - 2)))
    shorts = random.sample(ULTRA_SHORT, min(num_inserts, len(ULTRA_SHORT)))
    
    for i, (pos, s) in enumerate(zip(positions, shorts)):
        sentences.insert(pos + i, s)
    
    return ' '.join(sentences)


def filler_inject(text):
    """Insert filler phrases at random positions. ~1 per 80 words."""
    random.seed(hash(text) % 2**32 + 11)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 3:
        return text
    word_count = len(text.split())
    num_inserts = max(1, word_count // 80)
    positions = sorted(random.sample(range(1, len(sentences)), min(num_inserts, len(sentences) - 1)))
    fillers = [random.choice(FILLER_PHRASES) for _ in range(num_inserts)]
    for i, (pos, f) in enumerate(zip(positions, fillers)):
        sentences.insert(pos + i, f.capitalize() + ",")
    return ' '.join(sentences)


def pronoun_inject(text):
    """Prepend personal pronoun starters to ~1 per 120 words."""
    random.seed(hash(text) % 2**32 + 22)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 4:
        return text
    word_count = len(text.split())
    num_inserts = max(1, word_count // 120)
    candidates = list(range(2, len(sentences) - 1))
    if len(candidates) < num_inserts:
        num_inserts = len(candidates)
    positions = sorted(random.sample(candidates, num_inserts))
    for i, pos in enumerate(positions):
        idx = pos + i
        if idx < len(sentences):
            starter = random.choice(PRONOUN_STARTERS)
            sent = sentences[idx]
            sentences[idx] = starter + " " + sent[0].lower() + sent[1:]
    return ' '.join(sentences)


def punctuation_inject(text):
    """Add informal punctuation to ~5% of sentences."""
    random.seed(hash(text) % 2**32 + 33)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 4:
        return text
    num_affected = max(1, int(len(sentences) * 0.05))
    candidates = list(range(len(sentences)))
    positions = random.sample(candidates, min(num_affected, len(candidates)))
    for pos in positions:
        sent = sentences[pos]
        style = random.choice(['ellipsis', 'exclaim', 'tag'])
        if style == 'ellipsis' and sent.rstrip().endswith('.'):
            sentences[pos] = sent.rstrip()[:-1] + '...'
        elif style == 'exclaim' and sent.rstrip().endswith('.'):
            sentences[pos] = sent.rstrip()[:-1] + '!'
        elif style == 'tag' and sent.rstrip().endswith('.'):
            sentences[pos] = sent.rstrip()[:-1] + ', right?'
    return ' '.join(sentences)


def depassivize(text):
    """Convert passive voice patterns to active voice."""
    # was/were + past_participle + by + agent -> agent + past_tense
    patterns = [
        (r'\bwas (\w+ed) by (the \w+)', r'\2 \1 it'),
        (r'\bwere (\w+ed) by (the \w+)', r'\2 \1 them'),
        (r'\bwas (\w+ed) by (\w+)', r'\2 \1 it'),
        (r'\bwere (\w+ed) by (\w+)', r'\2 \1 them'),
        (r'\bis (\w+ed) by (the \w+)', r'\2 \1s it'),
        (r'\bare (\w+ed) by (the \w+)', r'\2 \1 them'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def synonym_rotate(text):
    """Rotate ~10% of matching words with synonyms."""
    random.seed(hash(text) % 2**32 + 44)
    words = text.split()
    for i, word in enumerate(words):
        lower = word.lower().strip('.,!?;:')
        if lower in SYNONYMS and random.random() < 0.10:
            replacement = random.choice(SYNONYMS[lower])
            # Preserve capitalization
            if word[0].isupper():
                replacement = replacement.capitalize()
            # Preserve trailing punctuation
            trailing = ''
            for ch in reversed(word):
                if ch in '.,!?;:':
                    trailing = ch + trailing
                else:
                    break
            words[i] = replacement + trailing
    return ' '.join(words)


def paragraph_vary(text):
    """Insert paragraph breaks at varied intervals (2-8 sentence paragraphs)."""
    random.seed(hash(text) % 2**32 + 55)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 4:
        return text
    result = []
    i = 0
    while i < len(sentences):
        group_size = random.randint(2, 8)
        group = sentences[i:i + group_size]
        result.append(' '.join(group))
        i += group_size
    return '\n\n'.join(result)


# ─── Feature 9: Response Cache ─────────────────────────────────────

_RESPONSE_CACHE = {}

def cache_replace(text):
    """Fast mechanical replacements using cached patterns. No LLM needed."""
    global _RESPONSE_CACHE
    if not _RESPONSE_CACHE:
        # Build cache from existing rules
        for full, short in CONTRACTIONS.items():
            _RESPONSE_CACHE[re.compile(r'\b' + re.escape(full) + r'\b', re.I)] = short
        for formal, casual in TRANSITION_KILLERS:
            _RESPONSE_CACHE[re.compile(r'\b' + re.escape(formal) + r'\b', re.I)] = casual
        for ai_word, simple in AI_WORDS.items():
            _RESPONSE_CACHE[re.compile(r'\b' + re.escape(ai_word) + r'\b', re.I)] = simple
        for long_word, short_word in WORD_SIMPLIFY.items():
            _RESPONSE_CACHE[re.compile(r'\b' + re.escape(long_word) + r'\b', re.I)] = short_word
        for formal, casual in COLLOQUIAL.items():
            _RESPONSE_CACHE[re.compile(r'\b' + re.escape(formal) + r'\b', re.I)] = casual
        for word, syns in SYNONYMS.items():
            _RESPONSE_CACHE[re.compile(r'\b' + re.escape(word) + r'\b', re.I)] = syns[0]
    for pattern, replacement in _RESPONSE_CACHE.items():
        text = pattern.sub(replacement, text)
    return text


# ─── Feature 5: Sentence Starter Diversity ─────────────────────────

COMMON_STARTERS = {'the', 'this', 'it', 'these', 'they', 'there', 'in', 'as', 'a', 'an'}

DIVERSE_STARTERS = [
    "So, ", "Well, ", "Look, ", "Honestly, ", "Here's the thing — ",
    "I mean, ", "Basically, ", "The way I see it, ", "Truth is, ",
    "And that's where it gets interesting. ", "But here's what most people miss: ",
    "Think about it. ", "The real question is, ", "Not to oversimplify, but ",
    "On the flip side, ", "And here's the kicker — ",
]

def sentence_starter_diversity(text):
    """Detect repetitive sentence starters and inject variety."""
    random.seed(hash(text) % 2**32 + 66)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 4:
        return text

    # Analyze starters
    starters = []
    for s in sentences:
        words = s.strip().split()
        if words:
            starters.append(words[0].lower().rstrip(',.:;'))
        else:
            starters.append('')

    # Count frequency
    counter = Counter(starters)
    total = len([s for s in starters if s])

    # Find overused starters (>25% of sentences)
    overused = {w for w, c in counter.items() if c > total * 0.25 and w in COMMON_STARTERS and total > 0}

    if not overused:
        return text

    # Replace ~40% of overused starters with diverse ones
    replaced = 0
    max_replace = min(len(DIVERSE_STARTERS), int(total * 0.3))
    used_diverse = set()

    for i in range(len(sentences)):
        if replaced >= max_replace:
            break
        words = sentences[i].strip().split()
        if not words:
            continue
        starter = words[0].lower().rstrip(',.:;')
        if starter in overused and i > 0:  # Don't replace first sentence
            diverse = None
            for d in DIVERSE_STARTERS:
                if d not in used_diverse:
                    diverse = d
                    break
            if diverse:
                # Lowercase the original sentence start
                sentences[i] = diverse + sentences[i][0].lower() + sentences[i][1:]
                used_diverse.add(diverse)
                replaced += 1

    return ' '.join(sentences)


# ─── Feature 6: Grammar Imperfections ──────────────────────────────

def grammar_imperfections(text):
    """Inject human grammar flaws: comma splices, run-ons, fragments."""
    random.seed(hash(text) % 2**32 + 77)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 5:
        return text

    result = list(sentences)

    # 1. Comma splices: join 2 short independent sentences with comma instead of period
    splice_count = 0
    max_splices = min(3, len(result) // 6)
    i = 0
    while i < len(result) - 1 and splice_count < max_splices:
        s1 = result[i].strip()
        s2 = result[i + 1].strip() if i + 1 < len(result) else ''
        w1 = len(s1.split())
        w2 = len(s2.split())
        # Both sentences 5-15 words = good splice candidates
        if 5 <= w1 <= 15 and 5 <= w2 <= 15 and random.random() < 0.3:
            # Join with comma (comma splice)
            merged = s1.rstrip('.!?') + ', ' + s2[0].lower() + s2[1:]
            result[i] = merged
            result.pop(i + 1)
            splice_count += 1
        i += 1

    # 2. Run-on: join with "and" or "so" without proper punctuation
    runon_count = 0
    max_runons = min(2, len(result) // 8)
    for i in range(len(result) - 1):
        if runon_count >= max_runons:
            break
        s1 = result[i].strip()
        s2 = result[i + 1].strip() if i + 1 < len(result) else ''
        w1 = len(s1.split())
        w2 = len(s2.split())
        if 8 <= w1 <= 20 and 5 <= w2 <= 12 and random.random() < 0.2:
            connector = random.choice([' and ', ' so ', ' but '])
            merged = s1.rstrip('.!?') + connector + s2[0].lower() + s2[1:]
            result[i] = merged
            result.pop(i + 1)
            runon_count += 1

    return ' '.join(result)


# ─── Feature 7: Context-Aware Fragments ────────────────────────────

OPINION_MARKERS = {'think', 'believe', 'feel', 'argue', 'suggest', 'seems', 'appears', 'opinion', 'view'}
EXPLAIN_MARKERS = {'because', 'due to', 'since', 'as a result', 'therefore', 'thus', 'caused', 'led to', 'reason'}

CONTEXT_FRAGMENTS = {
    'opinion': ["Honestly, ", "I'd say ", "If you ask me, ", "The way I see it, ", "From where I stand, "],
    'explain': ["Here's the deal: ", "So basically, ", "What this means is, ", "The thing is, ", "Bottom line: "],
    'contrast': ["But here's the catch — ", "That said, ", "On the flip side, ", "Fair enough, but "],
    'neutral': ["Look, ", "And honestly? ", "Truth is, ", "The real question is, "],
}

def context_aware_fragments(text):
    """Inject fragments based on surrounding text context."""
    random.seed(hash(text) % 2**32 + 88)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 5:
        return text

    word_count = len(text.split())
    num_inserts = min(max(1, word_count // 200), 4)
    inserted = 0

    # Analyze each sentence for context
    for i in range(1, len(sentences) - 1):
        if inserted >= num_inserts:
            break

        sent_lower = sentences[i].lower()
        prev_lower = sentences[i - 1].lower() if i > 0 else ''

        # Determine context type
        context = 'neutral'
        if any(m in sent_lower for m in OPINION_MARKERS):
            context = 'opinion'
        elif any(m in sent_lower for m in EXPLAIN_MARKERS):
            context = 'explain'
        elif any(w in prev_lower for w in ['but', 'however', 'although', 'despite']):
            context = 'contrast'

        # Only inject if random chance passes (not every sentence)
        if random.random() < 0.15:
            frags = CONTEXT_FRAGMENTS.get(context, CONTEXT_FRAGMENTS['neutral'])
            frag = random.choice(frags)
            # Don't inject if previous sentence already starts with similar fragment
            prev_start = prev_lower[:20] if prev_lower else ''
            if not any(prev_start.startswith(f.lower()[:10]) for f in frags):
                sentences.insert(i, frag + sentences[i][0].lower() + sentences[i][1:])
                inserted += 1

    return ' '.join(sentences)


# ─── Feature 8: Chunk Transition Smoothing ─────────────────────────

BRIDGE_PHRASES = [
    "Speaking of which, ", "On a related note, ", "And that connects to something else — ",
    "This ties into ", "Which brings up another point. ", "There's more to it though. ",
    "But it doesn't stop there. ", "And here's where it gets interesting. ",
]

ACADEMIC_BRIDGE_PHRASES = [
    "In this context, ", "Building on the preceding discussion, ", "It is worth noting that ",
    "Continuing this line of analysis, ", "From an analytical perspective, ",
    "The implications of this are noteworthy. ", "This warrants further consideration. ",
    "The significance of this extends to ",
]

def smooth_transitions(chunks_text, tone="casual"):
    """Fix abrupt transitions between stitched chunks. Tone-aware."""
    if len(chunks_text) < 2:
        return ' '.join(chunks_text)

    random.seed(hash(''.join(chunks_text)) % 2**32 + 99)
    bridges = ACADEMIC_BRIDGE_PHRASES if tone == "academic" else BRIDGE_PHRASES
    result = [chunks_text[0]]

    for i in range(1, len(chunks_text)):
        # Add bridge phrase at ~50% of chunk boundaries
        if random.random() < 0.5:
            bridge = random.choice(bridges)
            chunk = chunks_text[i]
            # Lowercase first word after bridge
            if chunk:
                chunk = chunk[0].lower() + chunk[1:]
            chunks_text[i] = bridge + chunk

        result.append(chunks_text[i])

    return ' '.join(result)


# ─── Feature 9 (continued): Sentence-level pattern cache ───────────

SENTENCE_PATTERNS = [
    # AI pattern → human replacement template
    (r'It is (important|worth noting|essential) to note that (.+?)\.', r'\2.'),
    (r'In today\'s (rapidly|ever-)changing (.+?),', r'\2 is changing fast,'),
    (r'It is widely (acknowledged|recognized) that ', ''),
    (r'There (is|are) (several|many|numerous) (reasons?|factors?) (why|that) ', ''),
    (r'(In conclusion|To sum up|In summary),?', ''),
]

def sentence_pattern_cache(text):
    """Remove common AI sentence patterns via cached regex."""
    for pattern, replacement in SENTENCE_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Clean up double spaces/punctuation from removals
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\. \.', '.', text)
    return text


ACADEMIC_FRAGMENTS = [
    "Notably, ", "In particular, ", "It is worth noting that ", "Importantly, ",
    "Of significance, ", "As a result, ", "Consequently, ", "This suggests that ",
    "In this context, ", "Furthermore, it appears that ", "On closer examination, ",
    "This indicates that ", "Such findings suggest ", "From an analytical standpoint, ",
]

ACADEMIC_FRAGMENTS_SHORT = [
    "Importantly, ", "This is key. ", "Of note, ",
    "This matters. ", "Worth highlighting, ", "Significantly, ",
    "Of particular interest, ", "This is relevant. ", "Critically, ",
]

ACADEMIC_ULTRA_SHORT = [
    "This is significant.", "The implications are notable.", "This warrants attention.",
    "The evidence supports this.", "This is particularly relevant.", "The data confirms this.",
    "This finding is critical.", "The results are noteworthy.",
]

CASUAL_PHRASES_TO_STRIP = [
    r'\bHonestly[,.!]*\s*',
    r'\bFair point[,.!]*\s*',
    r'\bFair enough[,.!]*\s*',
    r'\bI think\b',
    r'\bI mean[,.]*\s*',
    r'\bLook[,.!]*\s*',
    r'\bBig deal[,.!]*\s*',
    r'\bTruth is[,.:]*\s*',
    r'\bSo what does that actually mean\?\s*',
    r'\bThat\'s what we\'re here to find out[,.!]*\s*',
    r'\bMakes sense[,.!]*\s*',
    r'\bUm[,.!]*\s*',
    r'\bKind of obvious[,.!]*\s*',
    r'\bNot easy[,.!]*\s*',
    r'\bNot great[,.!]*\s*',
    r'\bNot surprising[,.!]*\s*',
    r'\bPretty wild when you think about it[,.!]*\s*',
    r'\bYou know what[,.:]*\s*',
    r'\bBut here\'s the thing[,.:!]*\s*',
    r'\bFrom what I\'ve seen[,.:]*\s*',
    r'\bFrom what I can tell[,.:]*\s*',
    r'\bI\'d say\b',
    r'\bI\'ve noticed that\b',
    r'\bIf you ask me[,.]*\s*',
    r'\bIn my experience[,.:]*\s*',
    r'\bThe way I see it[,.:]*\s*',
    r'\bMaybe[,.]*\s*',
    r'\bTo be honest[,.:]*\s*',
    r'\bAnd honestly\?\s*',
    r'\bIt depends[,.]*\s*',
    r'\bAnd honestly[,.]*\s*',
    r'\byou know[,.]*\s*',
    r'\bthe thing is[,.:]*\s*',
    r'\bWhat this means is[,.:]*\s*',
    r'\bHere\'s the deal[,.:]*\s*',
    r'\bBottom line[,.:]*\s*',
    r'\bOn the flip side[,.:]*\s*',
    r'\bThat said[,.:]*\s*',
    r'\bAnd that\'s where it gets interesting[,.:]*\s*',
    r'\bBut here\'s what most people miss[,.:]*\s*',
    r'\bThink about it[,.]*\s*',
    r'\bThe real question is[,.:]*\s*',
    r'\bNot to oversimplify[,.]*\s*',
    r'\bAnd here\'s the kicker[,.:]*\s*',
    r'\bSound familiar\?\s*',
    r'\bIs that always the case\?\s*Not really[,.]*\s*',
    r'\bSo basically[,.:]*\s*',
    r'\bProbably[,.]*\s*',
    r'\bSort of[,.]*\s*',
    r'\bKind of[,.]*\s*',
    r'\bbasically[,.]*\s*',
    r'\bBasically[,.]*\s*',
    r'\bThis is significant\.?\s*',
    r'\bThis is key\.?\s*',
    r'\bWorth highlighting[,.]*\s*',
    r'\bWorth noting[,.]*\s*',
    r'\bOf note[,.]*\s*',
    r'\bThis matters[,.]*\s*',
    r'\bCritically[,.]*\s*',
    r'\bSignificantly[,.]*\s*',
    r'\bIndeed[,.]*\s*',
    r'\bMoreover[,.]*\s*',
    r'\bFurthermore[,.]*\s*',
    r'\bIn particular[,.]*\s*',
    r'\bIn essence[,.]*\s*',
    r'\bEssentially[,.]*\s*',
    r'\bNotably[,.]*\s*',
    r'\bIt should be noted[,.]*\s*',
    r'\bIt is worth noting[,.]*\s*',
    r'\bAs a matter of fact[,.]*\s*',
    r'\bNeedless to say[,.]*\s*',
    r'\bHaving said that[,.]*\s*',
    r'\bAt the end of the day[,.]*\s*',
    r'\bFor what it\'s worth[,.]*\s*',
    r'\bWithout a doubt[,.]*\s*',
    r'\bIt goes without saying[,.]*\s*',
    r'\bBut the truth is[,.]*\s*',
    r'\bActually[,.]*\s*',
    r'\bJust[,.]*\s*(?=the|a|an|to)',
    r'\blike[,.]*\s*(?=the|a|an|it|this)',
    r'\bWell[,.]*\s*',
    r'\bRight[,.]*\s*',
    r'\bSure[,.]*\s*',
    r'\bOK[,.]*\s*',
    r'\bOkay[,.]*\s*',
    r'\bYeah[,.]*\s*',
    r'\bYes[,.]*\s*(?=the|and|but|it)',
    r'\bNope[,.]*\s*',
    r'\bLet\'s face it[,.]*\s*',
    r'\bHere\'s the thing[,.:]*\s*',
    r'\bLong story short[,.:]*\s*',
    r'\bBy and large[,.]*\s*',
    r'\bMore often than not[,.]*\s*',
    r'\bAt this point[,.]*\s*',
    r'\bIn terms of[,.]*\s*',
    r'\bIn the context of[,.]*\s*',
    r'\bright\?\s*',
    r'\bSure[,.]*\s*',
    r'\bWell[,.]*\s*',
]

def _strip_casual_phrases(text):
    """Remove casual phrases that LLM might generate even with academic prompt.
    Runs multiple passes until no more matches found."""
    # Run strip in loop until stable (some patterns create new matches after removal)
    prev = ""
    passes = 0
    while prev != text and passes < 5:
        prev = text
        for pattern in CASUAL_PHRASES_TO_STRIP:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        passes += 1
    
    # Remove standalone casual sentences (entire sentences that are just 1-3 word fragments)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    cleaned = []
    for s in sentences:
        words = s.strip().split()
        # Skip very short standalone fragments (1-3 words that are just filler)
        if len(words) <= 3:
            lower = s.lower().strip('.,!?')
            skip_words = {'honestly', 'fair point', 'basically', 'this is significant',
                         'this is key', 'worth highlighting', 'worth noting', 'of note',
                         'this matters', 'indeed', 'moreover', 'furthermore',
                         'in particular', 'in essence', 'essentially', 'notably',
                         'actually', 'well', 'sure', 'ok', 'okay', 'yeah',
                         'by and large', 'more often than not', 'at this point',
                         'fair enough', 'makes sense', 'not easy', 'not great',
                         'big deal', 'true', 'maybe', 'probably', 'sort of',
                         'kind of', 'right', 'look', 'not surprising',
                         'pretty wild', 'think about it', 'sound familiar', 'it depends',
                         'truth is', 'i think', 'i mean', 'so basically', 'to be honest',
                         'critically', 'significantly'}
            if lower in skip_words:
                continue
        cleaned.append(s)
    
    text = ' '.join(cleaned)
    
    # Clean up double spaces and orphaned punctuation
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+([,.!?])', r'\1', text)  # remove space before punctuation
    text = re.sub(r'(?<=[.!?])\s*[,.!?]+\s*', '. ', text)  # collapse orphaned punctuation
    text = re.sub(r'^\s*[,.!?]\s*', '', text)  # remove leading punctuation
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _burstiness_inject_academic(text):
    """Academic-safe burstiness: split long sentences, merge short ones, use formal fragments only.
    Skips numbered lists and structured content."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 3:
        return text
    
    result = []
    i = 0
    frag_idx = 0
    
    # Detect if text is mostly structured (numbered lists, short steps)
    numbered_count = sum(1 for s in sentences if re.match(r'^\d+\.\s', s.strip()))
    is_structured = numbered_count > len(sentences) * 0.3  # >30% numbered = structured
    
    while i < len(sentences):
        sent = sentences[i].strip()
        if not sent:
            i += 1
            continue
        
        words = sent.split()
        is_numbered = bool(re.match(r'^\d+\.\s', sent))
        
        # SKIP fragment injection for numbered items or structured content
        if not is_structured and not is_numbered and i > 0 and i % 8 == 0 and frag_idx < len(ACADEMIC_FRAGMENTS_SHORT):
            frag = ACADEMIC_FRAGMENTS_SHORT[frag_idx]
            prev_text = result[-1] if result else ''
            prev_is_numbered = bool(re.match(r'^\d+\.\s', prev_text)) if prev_text else False
            if not prev_is_numbered:
                frag_first = frag.split('.')[0].split(',')[0].split(':')[0]
                prev_first = prev_text.split('.')[0].split(',')[0].split(':')[0] if prev_text else ''
                if frag_first.lower() != prev_first.lower():
                    result.append(frag)
            frag_idx += 1
        
        # Split sentences over 25 words (even less aggressive for academic)
        # But NOT numbered items
        if len(words) > 25 and not is_numbered:
            split_at = -1
            for j, w in enumerate(words):
                if j > 5 and j < len(words) - 4:
                    if w.lower() in (',', 'and', 'but', 'while', 'which', 'as', 'that', 'because', 'so', 'or', 'however', 'moreover', 'furthermore', 'although', 'whereas'):
                        split_at = j
                        break
            if split_at > 0:
                part1 = ' '.join(words[:split_at]).rstrip(',') + '.'
                part1 = part1[0].upper() + part1[1:] if part1 else part1
                if words[split_at] == ',':
                    rest = words[split_at+1:]
                else:
                    rest = words[split_at:]
                part2 = ' '.join(rest)
                part2 = part2[0].upper() + part2[1:] if part2 else ''
                result.append(part1)
                if part2:
                    result.append(part2)
                i += 1
                continue
        
        # Merge medium sentences ONLY if not numbered
        if not is_numbered and 12 <= len(words) <= 20 and i + 1 < len(sentences):
            next_sent = sentences[i+1].strip()
            next_is_numbered = bool(re.match(r'^\d+\.\s', next_sent))
            next_words = next_sent.split() if next_sent else []
            if not next_is_numbered and 3 <= len(next_words) <= 8:
                merged = sent.rstrip('.') + '; ' + next_sent[0].lower() + next_sent[1:]
                result.append(merged)
                i += 2
                continue
        
        result.append(sent)
        i += 1
    
    return ' '.join(result)

def advanced_post_process(text, tone="casual"):
    """Advanced post-processing pipeline with all humanization steps. Tone-aware."""
    # Phase 1: Fast mechanical (cached) — no LLM needed
    text = cache_replace(text)
    text = sentence_pattern_cache(text)

    if tone == "academic":
        # Academic: formal only — NO casual injects
        # Strip casual phrases LLM might have generated
        text = _strip_casual_phrases(text)
        text = synonym_rotate(text)
        text = _burstiness_inject_academic(text)
        text = emdash_inject(text)
        text = _academic_filler_inject(text)
        text = _academic_ultra_short_inject(text)
        # Final strip pass to catch anything remaining
        text = _strip_casual_phrases(text)
    else:
        # Casual/Business: full humanization pipeline
        text = colloquial_inject(text)
        text = synonym_rotate(text)
        text = depassivize(text)
        text = burstiness_inject(text)
        text = grammar_imperfections(text)
        text = sentence_starter_diversity(text)
        text = context_aware_fragments(text)
        text = filler_inject(text)
        text = pronoun_inject(text)
        text = punctuation_inject(text)
        text = emdash_inject(text)
        text = rhetorical_inject(text)
        text = ultra_short_inject(text)

    # New: Perplexity injection + Zipf redistribution
    text = perplexity_inject(text)
    text = zipf_redistribute(text)
    text = sentence_order_shuffle(text)

    # Final cleanup
    text = re.sub(r'\.\s*\.', '.', text)
    text = re.sub(r'\s+([,.!?])', r'\1', text)
    text = re.sub(r'  +', ' ', text)
    # Ensure space after comma
    if tone == "academic":
        text = re.sub(r',(\S)', r', \1', text)
    return text.strip()


def _academic_filler_inject(text):
    """Inject academic-appropriate transitional phrases. Skips numbered lists."""
    random.seed(hash(text) % 2**32 + 111)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 3:
        return text
    
    # Skip injection if text is mostly numbered lists
    numbered_count = sum(1 for s in sentences if re.match(r'^\d+\.\s', s.strip()))
    if numbered_count > len(sentences) * 0.3:
        return text
    
    word_count = len(text.split())
    num_inserts = max(1, word_count // 300)
    # Filter out numbered items from candidates
    candidates = [i for i in range(1, len(sentences) - 1) if not re.match(r'^\d+\.\s', sentences[i].strip())]
    if not candidates:
        return text
    positions = sorted(random.sample(candidates, min(num_inserts, len(candidates))))
    frags = random.sample(ACADEMIC_FRAGMENTS, min(num_inserts, len(ACADEMIC_FRAGMENTS)))
    for i, (pos, frag) in enumerate(zip(positions, frags)):
        idx = pos + i
        if idx < len(sentences):
            sent = sentences[idx]
            sentences[idx] = frag + sent[0].lower() + sent[1:]
    return ' '.join(sentences)


def _academic_ultra_short_inject(text):
    """Inject academic-safe short sentences. Skips numbered lists."""
    random.seed(hash(text) % 2**32 + 222)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) < 5:
        return text
    
    # Skip injection if text is mostly numbered lists
    numbered_count = sum(1 for s in sentences if re.match(r'^\d+\.\s', s.strip()))
    if numbered_count > len(sentences) * 0.3:
        return text
    
    word_count = len(text.split())
    num_inserts = min(max(1, word_count // 250), 4)
    # Filter out positions near numbered items
    candidates = [i for i in range(1, len(sentences) - 1)
                  if not re.match(r'^\d+\.\s', sentences[i].strip())
                  and (i == 0 or not re.match(r'^\d+\.\s', sentences[i-1].strip()))]
    if not candidates:
        return text
    positions = sorted(random.sample(candidates, min(num_inserts, len(candidates))))
    shorts = random.sample(ACADEMIC_ULTRA_SHORT, min(num_inserts, len(ACADEMIC_ULTRA_SHORT)))
    for i, (pos, s) in enumerate(zip(positions, shorts)):
        sentences.insert(pos + i, s)
    return ' '.join(sentences)





# ─── Processing Stats ───────────────────────────────────────────────
STATS = {
    "total_jobs": 0,
    "total_input_words": 0,
    "total_output_words": 0,
    "total_time_seconds": 0,
    "models_used": {},
    "success_count": 0,
    "error_count": 0,
    "cache_hits": 0,
    "cache_misses": 0,
}

def update_stats(job_result):
    STATS["total_jobs"] += 1
    STATS["total_input_words"] += job_result.get("input_words", 0)
    STATS["total_output_words"] += job_result.get("output_words", 0)
    STATS["total_time_seconds"] += job_result.get("time", 0)
    model = job_result.get("model", "unknown")
    STATS["models_used"][model] = STATS["models_used"].get(model, 0) + 1
    if job_result.get("status") == "done":
        STATS["success_count"] += 1
    else:
        STATS["error_count"] += 1
    STATS["cache_hits"] = _LLM_CACHE_HITS
    STATS["cache_misses"] = _LLM_CACHE_MISSES

# ─── Version History (Undo) ─────────────────────────────────────────
VERSIONS = []  # [{id, timestamp, input_words, output_words, text, score}]
MAX_VERSIONS = 10

def save_version(entry):
    VERSIONS.insert(0, entry)
    if len(VERSIONS) > MAX_VERSIONS:
        VERSIONS.pop()

# ─── Custom Word Lists ──────────────────────────────────────────────
CUSTOM_PRESERVE = set()  # User-defined words to never modify
CUSTOM_AVOID = set()     # User-defined words to always replace

def load_custom_lists(preserve_list="", avoid_list=""):
    global CUSTOM_PRESERVE, CUSTOM_AVOID
    if preserve_list:
        items = preserve_list.replace(",", chr(10)).split(chr(10))
        CUSTOM_PRESERVE = set(w.strip() for w in items if w.strip())
    if avoid_list:
        items = avoid_list.replace(",", chr(10)).split(chr(10))
        CUSTOM_AVOID = set(w.strip().lower() for w in items if w.strip())

_PRESERVE_PLACEHOLDERS = {}

def apply_custom_preserve(text):
    """Lock custom preserve words with Unicode PUA before LLM processing."""
    global _PRESERVE_PLACEHOLDERS
    _PRESERVE_PLACEHOLDERS = {}
    if not CUSTOM_PRESERVE:
        return text
    counter = 0xE100  # Different PUA range from citations
    for word in CUSTOM_PRESERVE:
        if counter > 0xE1FF:
            break
        pat = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
        key = chr(counter)
        _PRESERVE_PLACEHOLDERS[key] = word
        text = pat.sub(f"\u200B{key}\u200B", text)
        counter += 1
    return text

def restore_custom_preserve(text):
    """Restore custom preserve words from Unicode PUA placeholders."""
    for key, original in _PRESERVE_PLACEHOLDERS.items():
        text = text.replace(f"\u200B{key}\u200B", original)
        text = text.replace(key, original)
    return text

AVOID_SYNONYMS = {
    "utilizes": "uses", "utilize": "use",
    "methodologies": "methods", "methodology": "method",
    "optimize": "improve", "optimizes": "improves",
    "delve": "look", "delves": "looks",
    "furthermore": "also", "moreover": "also", "additionally": "also",
    "nevertheless": "still", "consequently": "so", "subsequently": "then",
    "facilitate": "help", "facilitates": "helps",
    "leverage": "use", "leverages": "uses",
    "comprehensive": "full", "robust": "solid",
    "paradigm": "approach", "synergy": "fit",
}



def protect_citations(text):
    """Auto-detect and protect academic citations."""
    citations = []
    counter = [0]
    
    def replace_citation(match):
        cit = match.group(0)
        placeholder = f"CIT{counter[0]}"
        citations.append(cit)
        counter[0] += 1
        return placeholder
    
    pattern = r'\([A-Z][a-z]+(?:\s+(?:et al\.|&|[A-Z][a-z]+|,))*\s*,\s*(?:19|20)\d{2}(?:\s*,\s*(?:p|pp)\.?\s*\d+)?\)'
    protected = re.sub(pattern, replace_citation, text)
    return protected, citations

def restore_citations(text, citations):
    """Restore protected citations."""
    for i, cit in enumerate(citations):
        placeholder = f"CIT{i}"
        text = text.replace(placeholder, cit)
    return text

def calc_flesch_kincaid(text):
    """Calculate Flesch-Kincaid readability score."""
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = text.split()
    syllables = 0
    for word in words:
        word = word.lower().strip('.,!?;:')
        if len(word) <= 3:
            syllables += 1
        else:
            count = 0
            vowels = 'aeiouy'
            prev_vowel = False
            for char in word:
                is_vowel = char in vowels
                if is_vowel and not prev_vowel:
                    count += 1
                prev_vowel = is_vowel
            if word.endswith('e') and count > 1:
                count -= 1
            syllables += max(1, count)
    if not sentences or not words:
        return {'grade': 0, 'reading_ease': 0, 'level': 'N/A'}
    avg_words = len(words) / len(sentences)
    avg_syllables = syllables / len(words) if words else 0
    reading_ease = 206.835 - (1.015 * avg_words) - (84.6 * avg_syllables)
    grade_level = (0.39 * avg_words) + (11.8 * avg_syllables) - 15.59
    if reading_ease >= 90: level = 'Very Easy'
    elif reading_ease >= 80: level = 'Easy'
    elif reading_ease >= 70: level = 'Fairly Easy'
    elif reading_ease >= 60: level = 'Standard'
    elif reading_ease >= 50: level = 'Fairly Difficult'
    elif reading_ease >= 30: level = 'Difficult'
    else: level = 'Very Difficult'
    return {'grade': round(grade_level, 1), 'reading_ease': round(reading_ease, 1), 'level': level}

def check_grammar_languagetool(text):
    """Check grammar using LanguageTool free API."""
    try:
        data = b'text=' + urllib.request.quote(text[:5000]).encode() + b'&language=en-US'
        req = urllib.request.Request('https://api.languagetool.org/v2/check',
            data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        matches = result.get('matches', [])
        issues = []
        for m in matches[:10]:
            issues.append({'message': m.get('message', ''), 'offset': m.get('offset', 0), 'length': m.get('length', 0)})
        return {'issues': issues, 'total': len(matches)}
    except Exception as e:
        return {'issues': [], 'total': 0, 'error': str(e)}


def apply_custom_avoid(text):
    with open("debug.log", "a") as df:
        df.write(f"[AVOID] called with CUSTOM_AVOID={CUSTOM_AVOID}, text={text[:60]}" + chr(10))
    if not CUSTOM_AVOID:
        return text
    for word in CUSTOM_AVOID:
        replacement = AVOID_SYNONYMS.get(word, word)
        pat = re.compile(chr(92)+"b" + re.escape(word) + chr(92)+"b", re.IGNORECASE)
        text = pat.sub(replacement, text)
    return text

# ─── Citation/Reference Protection ───────────────────────────────────

CITATION_PATTERNS = [
    (r'\[(?:[A-Z][a-z]+(?:\s+(?:et al\.?|&\s+[A-Z][a-z]+))?,\s*\d{4})\]', 'CITE'),
    (r'\([A-Z][a-z]+(?:\s+(?:et al\.?|&\s+[A-Z][a-z]+))?,\s*\d{4}(?:\s*,\s*(?:p|pp)\.?\s*\d+)?\)', 'CITE'),
    (r'\b(Figure|Table|Section|Fig\.|Tbl\.|Sec\.|Equation|Eq\.|Appendix|App\.|Chapter|Ch\.)\s+\d+(?:\.\d+)*\b', 'REF'),
    (r'\[\d+(?:[,-]\s*\d+)*\]', 'CITNUM'),
    (r'doi[:\.]?\s*10\.\d{4,}/\S+', 'DOI'),
    (r'ISBN[\s:-]*[\dX-]+', 'ISBN'),
    (r'https?://\S+', 'URL'),
    (r'\b\d+\.?\d*\s*%', 'PCT'),
    (r'\b(?:RM|USD|EUR|GBP)\s*[\d,]+\.?\d*', 'MONEY'),
]

def _lock_citations(text):
    placeholders = {}
    counter = [0]
    def repl(m, tag):
        counter[0] += 1
        key = f"[KEEP:{tag}:{counter[0]}]"
        placeholders[key] = m.group(0)
        return key
    for pat, tag in CITATION_PATTERNS:
        text = re.sub(pat, lambda m, t=tag: repl(m, t), text, flags=re.IGNORECASE)
    return text, placeholders

def _unlock_citations(text, placeholders):
    for key, original in placeholders.items():
        # Exact match first
        text = text.replace(key, original)
        # Fuzzy: catch model mangling (spaces, case changes)
        tag_parts = key.replace('[KEEP:', '').replace(']', '').split(':')
        if len(tag_parts) == 2:
            tag, num = tag_parts
            # Try common mangling patterns
            for variant in [
                f"[KEEP: {tag}: {num}]",
                f"[KEEP:{tag}: {num}]",
                f"[KEEP: {tag}:{num}]",
                f"[keep:{tag}:{num}]",
                f"KEEP:{tag}:{num}",
                f"[KEEP {tag} {num}]",
            ]:
                text = text.replace(variant, original)
    # Catch any remaining [KEEP:...] patterns that model didn't mangle but added spaces around
    text = re.sub(r'\s*\[KEEP:\w+:\d+\]\s*', lambda m: placeholders.get(m.group().strip(), m.group()), text)
    return text


# ─── Perplexity Heuristic ────────────────────────────────────────────

COMMON_WORDS_500 = set("the be to of and a in that have i it for not on with he as you do at this but his by from they we say her she or an will my one all would there their what so up out if about who get which go me when make can like time no just him know take people into year your good some could them see other than then now look only come its over think also back after use two how our work first well way even new want because any these give day most us".split())

def calc_perplexity(text):
    words = [w.lower().strip('.,!?;:') for w in text.split() if len(w) > 1]
    if not words: return 0.0
    common_count = sum(1 for w in words if w in COMMON_WORDS_500)
    ratio = common_count / len(words)
    return round(1.0 - ratio, 3)

PERPLEXITY_SWAP = {
    "use": ["employ", "deploy", "wield", "tap into"],
    "show": ["reveal", "surface", "lay bare", "put on display"],
    "find": ["uncover", "pinpoint", "stumble upon"],
    "make": ["forge", "craft", "piece together"],
    "get": ["land", "score", "pull in", "pick up"],
    "big": ["sizable", "hefty", "substantial", "whopping"],
    "good": ["top-notch", "first-rate", "solid", "stellar"],
    "important": ["weighty", "vital", "non-negotiable", "make-or-break"],
    "change": ["overhaul", "shake up", "rework", "pivot"],
    "help": ["prop up", "back up", "pitch in", "lend a hand"],
}

def perplexity_inject(text):
    random.seed(hash(text) % 2**32 + 333)
    words = text.split()
    swapped = 0
    max_swaps = max(1, len(words) // 80)
    for i, w in enumerate(words):
        if swapped >= max_swaps: break
        lower = w.lower().strip('.,!?;:')
        if lower in PERPLEXITY_SWAP and lower in COMMON_WORDS_500 and random.random() < 0.15:
            replacement = random.choice(PERPLEXITY_SWAP[lower])
            if w[0].isupper(): replacement = replacement.title()
            trail = ''
            for ch in reversed(w):
                if ch in '.,!?;:': trail = ch + trail
                else: break
            words[i] = replacement + trail
            swapped += 1
    return ' '.join(words)


# ─── Sentence-Level AI Scoring ───────────────────────────────────────

def score_sentence_ai(sentence):
    s = sentence.strip()
    if not s: return 0.0
    words = s.split()
    if len(words) < 3: return 0.0
    score = 0.0
    ai_words = len(re.findall(r'\b(furthermore|moreover|additionally|consequently|nevertheless|delve|leverage|utilize|facilitate|comprehensive|robust|multifaceted|holistic|pivotal|paramount|seamless|unprecedented|plethora|myriad|endeavor|nuanced|intricate|burgeoning|trajectory|catalyst)\b', s, re.I))
    score += ai_words * 8
    avg_wl = sum(len(w) for w in words) / len(words)
    if avg_wl > 6.0: score += 15
    elif avg_wl > 5.5: score += 8
    has_contraction = bool(re.search(r"\b\w+['\']\w+", s))
    if not has_contraction: score += 10
    if re.match(r'^(It is|There (is|are)|This (is|suggests)|The (importance|significance|role))\b', s, re.I):
        score += 20
    common_starters = {'the', 'this', 'it', 'these', 'in', 'as'}
    first = words[0].lower().rstrip(',.:;')
    if first in common_starters: score += 5
    return min(score, 100)


# ─── Adaptive Chunk Type Detection ──────────────────────────────────

def detect_chunk_type(chunk):
    lines = chunk.strip().split('\n')
    numbered = sum(1 for l in lines if re.match(r'^\s*\d+[\.\)]', l.strip()))
    tabular = sum(1 for l in lines if '|' in l or '\t' in l)
    bullet = sum(1 for l in lines if re.match(r'^\s*[-*\u2022]', l.strip()))
    total = len(lines)
    if total == 0: return 'prose'
    if numbered / total > 0.4: return 'numbered'
    if tabular / total > 0.3: return 'table'
    if bullet / total > 0.4: return 'bullet'
    return 'prose'


# ─── Domain-Aware Vocabulary ────────────────────────────────────────

DOMAIN_AI_WORDS = {
    'tech': {
        'leverage': 'use', 'utilize': 'use', 'scalable': 'that can grow',
        'robust': 'solid', 'seamless': 'smooth', 'optimize': 'tune',
        'implement': 'set up', 'deployment': 'rollout', 'infrastructure': 'setup',
        'paradigm': 'approach', 'ecosystem': 'environment',
    },
    'medical': {
        'utilize': 'use', 'administer': 'give', 'facilitate': 'help with',
        'ameliorate': 'improve', 'exacerbate': 'worsen', 'mitigate': 'reduce',
        'comprehensive': 'thorough', 'indicate': 'show', 'demonstrate': 'show',
    },
    'legal': {
        'aforementioned': 'above-mentioned', 'pursuant to': 'under',
        'notwithstanding': 'despite', 'hereinafter': 'below',
        'comprehensive': 'complete', 'facilitate': 'help',
    },
    'academic': {
        'delve': 'dig into', 'leverage': 'use', 'utilize': 'use',
        'comprehensive': 'thorough', 'robust': 'solid', 'multifaceted': 'complex',
        'holistic': 'overall', 'paradigm': 'approach', 'foster': 'encourage',
        'paramount': 'key', 'seamless': 'smooth', 'unprecedented': 'rare',
    },
    'general': {},
}

def domain_word_replace(text, domain='general'):
    if domain == 'general': return text
    word_map = DOMAIN_AI_WORDS.get(domain, {})
    for ai_word, simple in word_map.items():
        pat = re.compile(r'\b' + re.escape(ai_word) + r'\b', re.IGNORECASE)
        text = pat.sub(simple, text)
    return text


# ─── Zipf's Law Redistribution ──────────────────────────────────────

ZIPF_TOP10 = ['the', 'and', 'is', 'in', 'it', 'of', 'to', 'that', 'for', 'was']
ZIPF_REDUCE = {
    'subsequently': 'then', 'consequently': 'so', 'furthermore': 'also',
    'nevertheless': 'still', 'additionally': 'plus', 'accordingly': 'so',
    'predominantly': 'mostly', 'approximately': 'about', 'demonstrating': 'showing',
    'implementing': 'setting up', 'facilitating': 'helping',
}

def zipf_redistribute(text):
    random.seed(hash(text) % 2**32 + 444)
    for long_word, short in ZIPF_REDUCE.items():
        pat = re.compile(r'\b' + re.escape(long_word) + r'\b', re.IGNORECASE)
        text = pat.sub(short, text)
    return text


# ─── Sentence Order Randomization ───────────────────────────────────

def sentence_order_shuffle(text):
    random.seed(hash(text) % 2**32 + 555)
    paragraphs = text.split('\n\n')
    result = []
    for para in paragraphs:
        sentences = re.split(r'(?<=[.!?])\s+', para)
        if len(sentences) >= 4 and not re.match(r'^\s*\d+[\.\)]', sentences[0]):
            swap_idx = random.randint(1, len(sentences) - 2)
            sentences[swap_idx], sentences[swap_idx + 1] = sentences[swap_idx + 1], sentences[swap_idx]
        result.append(' '.join(sentences))
    return '\n\n'.join(result)


# ─── Feedback Retry Loop ────────────────────────────────────────────

def feedback_retry(result_text, original_chunks, passes, model, tone, max_retries=2):
    paragraphs = re.split(r'\n\n', result_text)
    flagged_indices = []
    for i, para in enumerate(paragraphs):
        score = calc_detection_score(para)
        if score['score'] > 50 and len(para.split()) > 30:
            flagged_indices.append(i)
    if not flagged_indices:
        return result_text
    print(f"[feedback] Retrying {len(flagged_indices)} flagged paragraphs (max {max_retries} attempts)", flush=True)
    for attempt in range(max_retries):
        if not flagged_indices: break
        still_flagged = []
        for idx in flagged_indices:
            para = paragraphs[idx]
            retry_text = humanize_chunk(para, passes, model, tone)
            retry_text = advanced_post_process(retry_text, tone=tone)
            new_score = calc_detection_score(retry_text)
            if new_score['score'] <= 50:
                paragraphs[idx] = retry_text
            else:
                still_flagged.append(idx)
        flagged_indices = still_flagged
        print(f"[feedback] Attempt {attempt+1}: {len(flagged_indices)} still flagged", flush=True)
    return '\n\n'.join(paragraphs)


# ─── Chunking ──────────────────────────────────────────────────────────

def split_into_chunks(text, max_words=250):
    """Split text into chunks with sentence overlap at boundaries for citation preservation."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = []
    current_words = 0
    overlap_sentences = 2  # reduced overlap for efficiency
    
    for i, sent in enumerate(sentences):
        sw = len(sent.split())
        if current_words + sw > max_words and current:
            chunks.append(' '.join(current))
            # Overlap: carry last 2-3 sentences into next chunk
            overlap = current[-min(overlap_sentences, len(current)):]
            current = list(overlap) + [sent]
            current_words = sum(len(s.split()) for s in current)
        else:
            current.append(sent)
            current_words += sw
    if current:
        chunks.append(' '.join(current))
    return chunks


def deduplicate_overlaps(chunks_text):
    """Remove duplicate sentences from overlapping chunks."""
    if len(chunks_text) < 2:
        return chunks_text
    
    result = [chunks_text[0]]
    for i in range(1, len(chunks_text)):
        prev_sentences = set()
        for s in re.split(r'(?<=[.!?])\s+', chunks_text[i-1]):
            s_clean = s.strip().lower()
            if len(s_clean) > 10:
                prev_sentences.add(s_clean)
        
        current_sentences = re.split(r'(?<=[.!?])\s+', chunks_text[i])
        deduped = []
        for s in current_sentences:
            s_clean = s.strip().lower()
            if s_clean and len(s_clean) > 10 and s_clean in prev_sentences:
                continue
            deduped.append(s)
        
        if deduped:
            result.append(' '.join(deduped))
        else:
            result.append(chunks_text[i])
    
    return result


MODEL_FALLBACK_CHAIN = [
    "cx/gpt-5.5",
    "cx/gpt-5.4",
    "cx/gpt-5.4-mini",
    "ag/gemini-3-flash",
    "ag/gemini-3.5-flash-low",
]

def check_output_quality(original, result):
    """Detect garbage output: severe compression, word counting, hallucination."""
    if not result or not result.strip():
        return False, "empty output"
    
    orig_words = len(original.split())
    result_words = len(result.split())
    
    # Severe compression (< 40% of original)
    if orig_words > 20 and result_words < orig_words * 0.4:
        return False, f"severe compression ({result_words}/{orig_words} words)"
    
    # Word counting garbage
    garbage_patterns = [
        r'\(1\)\s*2\.', r'\(2\)\s*3\.', r'Significant expansion \(\d+\)',
        r'input text missing', r"can't edit empty", r'Send text\.',
        r'Word count:\s*\d+', r'Output words:',
    ]
    for pat in garbage_patterns:
        if re.search(pat, result, re.I):
            return False, f"garbage pattern: {pat}"
    
    # Hallucination: completely different vocabulary
    orig_words_set = set(w.lower() for w in original.split() if len(w) > 5)
    result_words_set = set(w.lower() for w in result.split() if len(w) > 5)
    if orig_words_set:
        overlap = len(orig_words_set & result_words_set) / len(orig_words_set)
        if overlap < 0.1:  # less than 10% vocabulary overlap
            return False, f"hallucination (only {overlap:.0%} vocab overlap)"
    
    return True, "ok"


def humanize_chunk(chunk, passes, model, tone="casual"):
    """Humanize a single chunk with quality gate and model fallback."""
    locked_chunk, placeholders = _lock_citations(chunk)
    locked_chunk = apply_custom_preserve(locked_chunk)
    
    models_to_try = [model] if model else [LLM_MODEL]
    for fb in MODEL_FALLBACK_CHAIN:
        if fb not in models_to_try:
            models_to_try.append(fb)
    
    result = None
    used_model = models_to_try[0]
    
    for try_model in models_to_try[:3]:
        try:
            candidate = pass1_rewrite(locked_chunk, model=try_model, tone=tone)
            if not candidate or not candidate.strip():
                continue
            
            ok, reason = check_output_quality(locked_chunk, candidate)
            if not ok:
                print(f"[quality] {try_model} failed: {reason}, trying next", flush=True)
                continue
            
            result = candidate
            used_model = try_model
            break
        except Exception as e:
            print(f"[fallback] {try_model} error: {e}", flush=True)
            continue
    
    if not result or not result.strip():
        with open("debug.log", "a") as df:
            df.write(f"[EARLY] No result from LLM, returning original chunk" + chr(10))
        return _unlock_citations(chunk, placeholders)
    
    if passes >= 2:
        temp = pass2_burstiness(result, model=used_model, tone=tone)
        if not temp or not temp.strip():
            temp = pass2_burstiness(result, model=used_model, tone=tone)
        if temp and temp.strip():
            result = temp

    if passes >= 3:
        temp = pass3_polish(result, model=used_model, tone=tone)
        if not temp or not temp.strip():
            temp = pass3_polish(result, model=used_model, tone=tone)
        if temp and temp.strip():
            result = temp

    result = _unlock_citations(result, placeholders)
    if CUSTOM_AVOID:
        print(f"[DEBUG] apply_custom_avoid: CUSTOM_AVOID={CUSTOM_AVOID}, before={result[:80]}", flush=True)
    result = apply_custom_avoid(result)
    if CUSTOM_PRESERVE:
        print(f"[DEBUG] restore_custom_preserve: CUSTOM_PRESERVE={CUSTOM_PRESERVE}", flush=True)
    result = restore_custom_preserve(result)
    return result


# ─── Main pipeline ────────────────────────────────────────────────────

def _process_chunk_worker(args):
    """Worker function for parallel chunk processing. Adaptive by chunk type."""
    idx, chunk, passes, model, tone = args
    cw = len(chunk.split())
    chunk_type = detect_chunk_type(chunk)
    print(f"[humanize] Chunk {idx+1} start ({cw} words, type={chunk_type})...", flush=True)

    if chunk_type == 'table':
        processed = cache_replace(chunk)
        processed = domain_word_replace(processed, tone)
    elif chunk_type == 'numbered':
        locked, ph = _lock_citations(chunk)
        processed = pass1_rewrite(locked, model=model, tone=tone)
        processed = _unlock_citations(processed, ph)
        processed = cache_replace(processed)
    else:
        processed = humanize_chunk(chunk, passes, model, tone)
        processed = advanced_post_process(processed, tone=tone)

    pw = len(processed.split())
    print(f"[humanize] Chunk {idx+1} done: {cw} -> {pw} words ({round(pw/cw*100)}%) [{chunk_type}]", flush=True)
    return idx, processed


def humanize(text, passes=3, model=None, tone="casual", progress_cb=None):
    # === Table/Code block detection & preservation ===
    protected_blocks = []
    # Protect code blocks (```...```)
    import re as _re
    code_pattern = _re.compile(r'```[\s\S]*?```', _re.MULTILINE)
    for m in code_pattern.finditer(text):
        placeholder = f'__PROTECTED_BLOCK_{len(protected_blocks)}__'
        protected_blocks.append((placeholder, m.group()))
        text = text.replace(m.group(), placeholder, 1)
    # Protect markdown tables (lines starting with |)
    table_pattern = _re.compile(r'(?:^\|.+\|\s*\n)+', _re.MULTILINE)
    for m in table_pattern.finditer(text):
        placeholder = f'__PROTECTED_BLOCK_{len(protected_blocks)}__'
        protected_blocks.append((placeholder, m.group()))
        text = text.replace(m.group(), placeholder, 1)
    # Protect LaTeX/math formulas ($$...$$, \[...\])
    math_pattern = _re.compile(r'(?:\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\])', _re.MULTILINE)
    for m in math_pattern.finditer(text):
        placeholder = f'__PROTECTED_BLOCK_{len(protected_blocks)}__'
        protected_blocks.append((placeholder, m.group()))
        text = text.replace(m.group(), placeholder, 1)
    # Protect citations [1], [2-5], (Smith, 2020), (Author & Author, 2021)
    cite_pattern = _re.compile(r'(?:\[\d+(?:[-–,]\s*\d+)*\]|\([A-Z][a-z]+(?:\s&(?:\s)?[A-Z][a-z]+)*,\s*\d{4}\))')
    for m in cite_pattern.finditer(text):
        placeholder = f'__PROTECTED_BLOCK_{len(protected_blocks)}__'
        protected_blocks.append((placeholder, m.group()))
        text = text.replace(m.group(), placeholder, 1)

    """Run full humanization pipeline with parallel chunking for long text."""
    if model is None:
        model = LLM_MODEL
    total_words = len(text.split())
    print(f"[humanize] Total: {total_words} words, {passes} passes, model={model}, tone={tone}", flush=True)

    # For short text (< 300 words), process directly
    if total_words <= 300:
        print(f"[humanize] Short text, processing directly...", flush=True)
        result = humanize_chunk(text, passes, model, tone)
        result = advanced_post_process(result, tone=tone)
        result = paragraph_vary(result)
        print(f"[humanize] Done: {total_words} -> {len(result.split())} words", flush=True)

        # Auto-retry if score still bad
        if AUTO_RETRY:
            score = calc_detection_score(result)
            if score['score'] > 40:
                print(f"[humanize] Score {score['score']} > 40, retrying...", flush=True)
                result = humanize_chunk(text, passes, model, tone)
                result = advanced_post_process(result, tone=tone)
                result = paragraph_vary(result)

        return result

    # Long text: chunk it
    chunks = split_into_chunks(text, max_words=CHUNK_SIZE)
    total_chunks = len(chunks)
    print(f"[humanize] Long text, split into {total_chunks} chunks (parallel={PARALLEL_CHUNKS})", flush=True)

    # Parallel processing
    processed_chunks = [None] * total_chunks
    completed = 0

    with ThreadPoolExecutor(max_workers=min(PARALLEL_CHUNKS, total_chunks)) as executor:
        work_items = [(i, chunk, passes, model, tone) for i, chunk in enumerate(chunks)]
        futures = {executor.submit(_process_chunk_worker, item): item[0] for item in work_items}

        for future in as_completed(futures):
            try:
                idx, processed = future.result(timeout=600)
                processed_chunks[idx] = processed
                completed += 1
                if progress_cb:
                    progress_cb(completed, total_chunks, "processing")
            except Exception as e:
                idx = futures[future]
                print(f"[humanize] Chunk {idx+1} FAILED: {e}, using original", flush=True)
                processed_chunks[idx] = advanced_post_process(chunks[idx], tone=tone)
                completed += 1

    # Fill any None entries (shouldn't happen but safety)
    for i in range(total_chunks):
        if processed_chunks[i] is None:
            processed_chunks[i] = advanced_post_process(chunks[i], tone=tone)

    if progress_cb:
        progress_cb(total_chunks, total_chunks, "done")

    # Smooth transitions between chunks (Feature 8)
    processed_chunks = deduplicate_overlaps(processed_chunks)
    result = smooth_transitions(processed_chunks, tone=tone)

    # Final pass - tone-aware
    if tone != "academic":
        result = ultra_short_inject(result)
        result = rhetorical_inject(result)
    else:
        # Academic mode: aggressively strip casual fillers + expand contractions
        result = _strip_casual_phrases(result)
        result = _expand_contractions(result)
        result = _strip_casual_phrases(result)  # double-pass for safety
        # Never inject fragments or rhetorical questions in academic writing
    result = paragraph_vary(result)
    result = re.sub(r'  +', ' ', result)
    result = re.sub(r'\.\s*\.', '.', result)
    print(f"[humanize] Done: {total_words} -> {len(result.split())} words", flush=True)

    # Auto-retry if score still bad
    if AUTO_RETRY:
        score = calc_detection_score(result)
        if score['score'] > 40:
            print(f"[humanize] Score {score['score']} > 40, retrying worst chunks...", flush=True)
            retry_indices = [0, len(chunks)-1] if len(chunks) > 1 else [0]
            for idx in retry_indices:
                processed = humanize_chunk(chunks[idx], passes, model, tone)
                processed = advanced_post_process(processed, tone=tone)
                processed_chunks[idx] = processed
            processed_chunks = deduplicate_overlaps(processed_chunks)
    result = smooth_transitions(processed_chunks, tone=tone)
    if tone != "academic":
                result = ultra_short_inject(result)
    result = paragraph_vary(result)
    result = re.sub(r'  +', ' ', result)
    result = re.sub(r'\.\s*\.', '.', result)

    # New: Paragraph-level feedback retry
    result = feedback_retry(result, chunks, passes, model or LLM_MODEL, tone)

    # Deduplicate repeated sentences
    lines = result.split('.')
    seen = set()
    deduped = []
    for line in lines:
        stripped = line.strip().lower()
        if stripped and stripped not in seen and len(stripped) > 3:
            seen.add(stripped)
            deduped.append(line.strip())
        elif not stripped:
            deduped.append(line.strip())
    result = '.'.join(deduped).strip()
    result = result.replace('..', '.')
    
    # Restore protected blocks (code, tables, citations)
    for placeholder, original in protected_blocks:
        result = result.replace(placeholder, original)
    return result

# ─── Feature: Output Variants (generate 3, pick best) ────────────────

def humanize_variants(text, passes=3, model=None, tone="casual", num_variants=3, progress_cb=None):
    """Generate multiple variants, return all + best (lowest AI score)."""
    if model is None:
        model = LLM_MODEL
    variants = []
    for i in range(num_variants):
        if progress_cb:
            progress_cb(i, num_variants, f"variant_{i+1}")
        result = humanize(text, passes=passes, model=model, tone=tone)
        score = calc_detection_score(result)
        variants.append({
            "text": result,
            "score": score["score"],
            "grade": score["grade"],
            "words": len(result.split()),
            "variant": i + 1,
        })
    variants.sort(key=lambda v: v["score"])
    best = variants[0]
    if progress_cb:
        progress_cb(num_variants, num_variants, "done")
    return {"variants": variants, "best": best}


# ─── Feature: Full-text Cache ────────────────────────────────────────

_FULL_TEXT_CACHE = {}
MAX_CACHE_ENTRIES = 50

def fulltext_cache_get(text_hash):
    return _FULL_TEXT_CACHE.get(text_hash)

def fulltext_cache_set(text_hash, result_data):
    global _FULL_TEXT_CACHE
    if len(_FULL_TEXT_CACHE) >= MAX_CACHE_ENTRIES:
        oldest_key = next(iter(_FULL_TEXT_CACHE))
        del _FULL_TEXT_CACHE[oldest_key]
    _FULL_TEXT_CACHE[text_hash] = result_data

def make_text_hash(text, passes, model, tone):
    key = f"{text[:2000]}|{passes}|{model}|{tone}"
    return hashlib.md5(key.encode()).hexdigest()


# ─── Feature: Time Estimation ────────────────────────────────────────

MODEL_AVG_TIMES = {
    "cx/gpt-5.5": 12,
    "ag/claude-sonnet-4-6": 10,
    "ag/gemini-3-flash": 5,
    "ag/gemini-3.5-flash-low": 3,
    "ag/gpt-oss-120b-medium": 8,
    "ag/claude-opus-4-6-thinking": 25,
    "cx/gpt-5.4": 8,
    "cx/gpt-5.4-mini": 4,
}

def estimate_time_remaining(input_words, chunks_total, chunks_done, elapsed_so_far, model=None):
    if chunks_done <= 0 or elapsed_so_far <= 0:
        model_time = MODEL_AVG_TIMES.get(model or LLM_MODEL, 10)
        total_est = (input_words / 300) * model_time * 3
        return {"total_seconds": round(total_est), "remaining_seconds": round(total_est), "elapsed_seconds": 0}
    avg_chunk_time = elapsed_so_far / chunks_done
    remaining_chunks = chunks_total - chunks_done
    remaining_seconds = round(remaining_chunks * avg_chunk_time * 1.1)
    total_seconds = round(elapsed_so_far + remaining_seconds)
    return {
        "total_seconds": total_seconds,
        "remaining_seconds": remaining_seconds,
        "elapsed_seconds": round(elapsed_so_far),
        "avg_chunk_time": round(avg_chunk_time, 1),
    }

def format_time_remaining(seconds):
    if seconds <= 0:
        return "Almost done..."
    if seconds < 60:
        return f"{seconds}s left"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes} min {secs}s left"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m left"


# ─── Feature: Tone Slider (1-10 casual to formal) ───────────────────

def get_tone_from_slider(level):
    level = max(1, min(10, int(level)))
    if level <= 3:
        base_tone = "casual"
        formality = level / 10.0
    elif level <= 6:
        base_tone = "business"
        formality = (level - 3) / 6.0
    else:
        base_tone = "academic"
        formality = (level - 6) / 4.0
    return {
        "tone": base_tone,
        "formality": round(formality, 2),
        "level": level,
        "contractions": level <= 5,
        "fillers": level <= 3,
        "hedging": level >= 7,
    }


# ─── Feature: Style Training ─────────────────────────────────────────

_STYLE_PROFILES = {}

def analyze_writing_style(text):
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
    words = text.split()
    if not sentences or not words:
        return None
    sent_lengths = [len(s.split()) for s in sentences]
    avg_sent_len = sum(sent_lengths) / len(sent_lengths)
    word_lengths = [len(w) for w in words]
    avg_word_len = sum(word_lengths) / len(word_lengths)
    contraction_count = len(re.findall(r"\b\w+['\u2019]\w+", text))
    contraction_ratio = contraction_count / max(len(sentences), 1)
    filler_count = len(re.findall(r'\b(like|you know|I mean|honestly|basically|actually|well|so|look)\b', text, re.I))
    filler_ratio = filler_count / max(len(words), 1) * 100
    formal_transitions = len(re.findall(r'\b(Furthermore|Moreover|Additionally|Consequently|However|Therefore)\b', text, re.I))
    casual_transitions = len(re.findall(r'\b(But|So|Also|Plus|And|Then|Still)\b', text, re.I))
    paragraphs = text.split('\n\n')
    para_lengths = [len(p.split()) for p in paragraphs if p.strip()]
    avg_para_len = sum(para_lengths) / max(len(para_lengths), 1)
    starters = [s.strip().split()[0].lower() for s in sentences if s.strip()]
    starter_counter = Counter(starters)
    top_starters = starter_counter.most_common(5)
    mean = sum(sent_lengths) / len(sent_lengths)
    variance = sum((l - mean)**2 for l in sent_lengths) / len(sent_lengths)
    std = math.sqrt(variance)
    burstiness_cv = std / mean if mean > 0 else 0
    unique_words = len(set(w.lower() for w in words))
    ttr = unique_words / len(words) if words else 0
    return {
        "avg_sentence_length": round(avg_sent_len, 1),
        "avg_word_length": round(avg_word_len, 1),
        "contraction_ratio": round(contraction_ratio, 2),
        "filler_ratio": round(filler_ratio, 2),
        "formal_transitions": formal_transitions,
        "casual_transitions": casual_transitions,
        "avg_paragraph_length": round(avg_para_len, 1),
        "top_starters": top_starters,
        "burstiness_cv": round(burstiness_cv, 3),
        "type_token_ratio": round(ttr, 3),
        "total_words": len(words),
        "total_sentences": len(sentences),
    }

def build_style_prompt(style_stats):
    if not style_stats:
        return ""
    parts = []
    avg_sl = style_stats["avg_sentence_length"]
    if avg_sl < 12:
        parts.append("Use short sentences (average ~10 words). Mix with occasional very short ones (3-5 words).")
    elif avg_sl < 20:
        parts.append(f"Use medium-length sentences (average ~{int(avg_sl)} words).")
    else:
        parts.append(f"Use longer, complex sentences (average ~{int(avg_sl)} words).")
    if style_stats["contraction_ratio"] > 0.5:
        parts.append("Use lots of contractions (don't, isn't, it's, we're, they've).")
    elif style_stats["contraction_ratio"] > 0.2:
        parts.append("Use moderate contractions.")
    else:
        parts.append("Avoid contractions. Use full forms (do not, is not, it is).")
    if style_stats["filler_ratio"] > 2.0:
        parts.append("Use filler phrases naturally: 'like', 'you know', 'I mean', 'honestly', 'basically'.")
    elif style_stats["filler_ratio"] > 0.5:
        parts.append("Occasionally use casual phrases like 'honestly' or 'I think'.")
    if style_stats["formal_transitions"] > style_stats["casual_transitions"]:
        parts.append("Use formal transitions: Furthermore, Moreover, Consequently, However.")
    else:
        parts.append("Use casual transitions: But, So, Also, Plus, And.")
    cv = style_stats["burstiness_cv"]
    if cv >= 0.7:
        parts.append("Vary sentence length dramatically - mix very short (3-5 words) with long (25+ words).")
    elif cv >= 0.4:
        parts.append("Moderately vary sentence length.")
    else:
        parts.append("Keep sentence lengths relatively consistent.")
    avg_pl = style_stats["avg_paragraph_length"]
    if avg_pl < 50:
        parts.append("Use short paragraphs (2-3 sentences each).")
    elif avg_pl > 150:
        parts.append("Use longer paragraphs (6-10 sentences each).")
    return " STYLE MATCHING: " + " ".join(parts)

def train_style(samples):
    all_stats = []
    for sample in samples:
        stats = analyze_writing_style(sample)
        if stats:
            all_stats.append(stats)
    if not all_stats:
        return None
    avg_stats = {}
    numeric_keys = ["avg_sentence_length", "avg_word_length", "contraction_ratio",
                    "filler_ratio", "formal_transitions", "casual_transitions",
                    "avg_paragraph_length", "burstiness_cv", "type_token_ratio"]
    for key in numeric_keys:
        values = [s[key] for s in all_stats if key in s]
        avg_stats[key] = round(sum(values) / len(values), 3) if values else 0
    avg_stats["total_samples"] = len(all_stats)
    avg_stats["total_words"] = sum(s.get("total_words", 0) for s in all_stats)
    profile_id = str(uuid.uuid4())[:8]
    _STYLE_PROFILES[profile_id] = {
        "id": profile_id,
        "stats": avg_stats,
        "prompt_addition": build_style_prompt(avg_stats),
        "created": datetime.now().isoformat(),
    }
    return _STYLE_PROFILES[profile_id]


# ─── Feature: Developer API with API Key System ──────────────────────

_API_KEYS = {}
API_KEY_PREFIX = "hai_"

def generate_api_key(name="default", rate_limit=100):
    import secrets
    raw_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    _API_KEYS[key_hash] = {
        "name": name,
        "created": datetime.now().isoformat(),
        "requests": 0,
        "rate_limit": rate_limit,
        "last_used": None,
        "key_preview": raw_key[:12] + "...",
    }
    return {"key": raw_key, "hash": key_hash, "name": name}

def validate_api_key(raw_key):
    if not raw_key:
        return False, "No API key provided"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    if key_hash not in _API_KEYS:
        return False, "Invalid API key"
    entry = _API_KEYS[key_hash]
    now = datetime.now()
    if entry["last_used"]:
        last = datetime.fromisoformat(entry["last_used"])
        if (now - last).total_seconds() > 3600:
            entry["requests"] = 0
    if entry["requests"] >= entry["rate_limit"]:
        return False, f"Rate limit exceeded ({entry['rate_limit']}/hour)"
    entry["requests"] += 1
    entry["last_used"] = now.isoformat()
    return True, "ok"

def list_api_keys():
    return [{"name": v["name"], "key_preview": v["key_preview"],
             "created": v["created"], "requests": v["requests"],
             "rate_limit": v["rate_limit"]} for v in _API_KEYS.values()]

def revoke_api_key(key_hash):
    if key_hash in _API_KEYS:
        del _API_KEYS[key_hash]
        return True
    return False


# ─── Feature: Webhook Notifications ──────────────────────────────────

_WEBHOOKS = {}

def register_webhook(url, events=None):
    webhook_id = str(uuid.uuid4())[:8]
    _WEBHOOKS[webhook_id] = {
        "id": webhook_id,
        "url": url,
        "events": events or ["job_complete", "batch_complete"],
        "active": True,
        "created": datetime.now().isoformat(),
    }
    return _WEBHOOKS[webhook_id]

def send_webhook(event, payload):
    for wh_id, wh in list(_WEBHOOKS.items()):
        if not wh["active"]:
            continue
        if event not in wh["events"]:
            continue
        try:
            data = json.dumps({"event": event, "data": payload, "timestamp": datetime.now().isoformat()}).encode()
            req = urllib.request.Request(
                wh["url"],
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "HumanizeAI-Webhook/1.0"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"[webhook] Failed to send to {wh['url']}: {e}", flush=True)

def list_webhooks():
    return list(_WEBHOOKS.values())

def delete_webhook(webhook_id):
    if webhook_id in _WEBHOOKS:
        del _WEBHOOKS[webhook_id]
        return True
    return False


# ─── HTML Template ────────────────────────────────────────────────────


def check_voice_consistency(text):
    """Detect formal/casual voice switches mid-document."""
    import re as _re
    formal_markers = ['furthermore', 'moreover', 'consequently', 'therefore', 'hence', 'thus', 'nevertheless', 'whereas', 'inasmuch']
    casual_markers = ['gonna', 'wanna', 'kinda', 'sorta', 'btw', 'lol', 'yeah', 'ok', 'cool', 'awesome', 'pretty much', 'basically', 'honestly']
    paragraphs = text.split('\n\n')
    results = []
    for i, para in enumerate(paragraphs):
        if not para.strip():
            continue
        lower = para.lower()
        words = lower.split()
        f_count = sum(1 for m in formal_markers if m in lower)
        c_count = sum(1 for m in casual_markers if m in lower)
        total = len(words)
        if total < 5:
            voice = 'neutral'
        elif f_count > c_count:
            voice = 'formal'
        elif c_count > f_count:
            voice = 'casual'
        else:
            voice = 'mixed'
        results.append({'paragraph': i + 1, 'voice': voice, 'formal': f_count, 'casual': c_count, 'words': total})
    
    voices = [r['voice'] for r in results if r['voice'] != 'neutral']
    if len(set(voices)) <= 1:
        consistent = True
        dominant = voices[0] if voices else 'neutral'
    else:
        consistent = False
        dominant = max(set(voices), key=voices.count)
    
    inconsistencies = []
    for r in results:
        if r['voice'] != 'neutral' and r['voice'] != dominant:
            inconsistencies.append(r['paragraph'])
    
    return {
        'consistent': consistent,
        'dominant_voice': dominant,
        'paragraphs': results,
        'inconsistent_paragraphs': inconsistencies,
        'score': round((1 - len(inconsistencies) / max(len(results), 1)) * 100)
    }


READABILITY_HISTORY = []  # Track Flesch-Kincaid across versions

def track_readability(text, version_label="current"):
    """Track readability score for progression chart."""
    import re as _re
    sentences = _re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = text.split()
    if not sentences or not words:
        return {'flesch': 0, 'grade': 'N/A', 'version': version_label}
    avg_sent_len = len(words) / len(sentences)
    # Count syllables roughly
    syllables = 0
    for w in words:
        w = w.lower().strip('.,;:!?')
        if len(w) <= 3:
            syllables += 1
        else:
            syllables += max(1, len(_re.findall(r'[aeiouy]+', w)))
    avg_syllables = syllables / len(words)
    fk = 206.835 - 1.015 * avg_sent_len - 84.6 * avg_syllables
    fk = max(0, min(100, fk))
    if fk >= 90: grade = '5th grade'
    elif fk >= 80: grade = '6th grade'
    elif fk >= 70: grade = '7th grade'
    elif fk >= 60: grade = '8-9th grade'
    elif fk >= 50: grade = '10-12th grade'
    elif fk >= 30: grade = 'College'
    else: grade = 'Graduate'
    entry = {'flesch': round(fk, 1), 'grade': grade, 'version': version_label, 'words': len(words), 'sentences': len(sentences)}
    READABILITY_HISTORY.append(entry)
    if len(READABILITY_HISTORY) > 50:
        READABILITY_HISTORY.pop(0)
    return entry


def reorder_paragraphs(text, strategy="logical"):
    """Reorder paragraphs while maintaining logical flow."""
    import re as _re
    paragraphs = _re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    if len(paragraphs) <= 2:
        return text
    
    if strategy == "reverse":
        return '\n\n'.join(reversed(paragraphs))
    elif strategy == "random":
        import random
        shuffled = paragraphs[:]
        # Keep first and last paragraph in place
        middle = shuffled[1:-1]
        random.shuffle(middle)
        result = [shuffled[0]] + middle + [shuffled[-1]]
        return '\n\n'.join(result)
    elif strategy == "length":
        # Sort by length (shortest first for easier reading)
        first = paragraphs[0]
        last = paragraphs[-1]
        middle = sorted(paragraphs[1:-1], key=len)
        return '\n\n'.join([first] + middle + [last])
    else:  # "logical" - keep original order but group by topic similarity
        return '\n\n'.join(paragraphs)



def _expand_contractions(text):
    """Expand contractions for academic writing (isn't -> is not, etc.)."""
    contractions = {
        "isn't": "is not", "aren't": "are not", "wasn't": "was not",
        "weren't": "were not", "don't": "do not", "doesn't": "does not",
        "didn't": "did not", "won't": "will not", "wouldn't": "would not",
        "shouldn't": "should not", "couldn't": "could not",
        "haven't": "have not", "hasn't": "has not", "hadn't": "had not",
        "can't": "cannot", "mustn't": "must not",
        "it's": "it is", "that's": "that is", "there's": "there is",
        "here's": "here is", "what's": "what is", "who's": "who is",
        "he's": "he is", "she's": "she is",
        "i'm": "I am", "you're": "you are", "we're": "we are",
        "they're": "they are", "i've": "I have", "you've": "you have",
        "we've": "we have", "they've": "they have",
        "i'll": "I will", "you'll": "you will", "we'll": "we will",
        "they'll": "they will", "he'll": "he will", "she'll": "she will",
        "i'd": "I would", "you'd": "you would", "we'd": "we would",
        "they'd": "they would", "he'd": "he would", "she'd": "she would",
        "let's": "let us", "who's": "whose",
    }
    for contraction, expansion in contractions.items():
        text = re.sub(r'\b' + re.escape(contraction) + r'\b', expansion, text, flags=re.IGNORECASE)
    return text

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Inter:wght@400;600&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HumanizeAI v3</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=Lora:ital,wght@0,400;0,600;0,700;1,400&family=Inter:wght@400;500;600&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    /* Writer's desk palette */
    --ink: #1a1612;
    --ink-light: #3d352c;
    --ink-muted: #8b7e6f;
    --paper: #faf6f0;
    --paper-warm: #f5efe5;
    --paper-dark: #ebe3d6;
    --accent: #c0582e;
    --accent-hover: #a84b24;
    --accent-light: rgba(192,88,46,0.1);
    --border: #d9d0c3;
    --border-light: #e8e1d6;
    --success: #4a7c59;
    --error: #b34242;
    --warning: #c09030;
    --shadow: 0 2px 8px rgba(26,22,18,0.08);
    --shadow-lg: 0 8px 24px rgba(26,22,18,0.12);
    --radius: 2px;
  }

  body {
    font-family: 'Lora', Georgia, 'Times New Roman', serif;
    background: var(--paper);
    color: var(--ink);
    min-height: 100vh;
    background-image:
      repeating-linear-gradient(0deg, transparent, transparent 27px, rgba(0,0,0,0.02) 27px, rgba(0,0,0,0.02) 28px);
    background-size: 100% 28px;
    line-height: 1.6;
  }

  /* Dark mode - old manuscript */
  body.dark {
    --ink: #e8e0d4;
    --ink-light: #c8baa8;
    --ink-muted: #8b7e6f;
    --paper: #1a1612;
    --paper-warm: #241f1a;
    --paper-dark: #2e2822;
    --border: #3d352c;
    --border-light: #2e2822;
    --accent: #d4764e;
    --accent-hover: #e08a64;
    --accent-light: rgba(212,118,78,0.15);
    background-image:
      repeating-linear-gradient(0deg, transparent, transparent 27px, rgba(255,255,255,0.015) 27px, rgba(255,255,255,0.015) 28px);
  }

  /* Typewriter elements */
  .mono {
    font-family: 'IBM Plex Mono', 'Courier New', monospace;
  }

  /* Masthead */
  .masthead {
    text-align: center;
    padding: 32px 24px 24px;
    border-bottom: 3px double var(--border);
    background: var(--paper);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .masthead-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: 6px;
    text-transform: uppercase;
    color: var(--ink);
    margin-bottom: 4px;
  }
  .masthead-title span { color: var(--accent); }
  .masthead-subtitle {
    font-family: 'Lora', serif;
    font-style: italic;
    font-size: 13px;
    color: var(--ink-muted);
    letter-spacing: 1px;
  }
  .masthead-rule {
    width: 60px;
    height: 3px;
    background: var(--accent);
    margin: 12px auto 0;
  }
  .masthead-controls {
    position: absolute;
    right: 24px;
    top: 50%;
    transform: translateY(-50%);
    display: flex;
    gap: 8px;
  }

  /* Main Layout */
  .main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 32px 24px;
  }

  /* Section Headers */
  .section-header {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 3px;
    color: var(--ink-muted);
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 16px;
  }

  /* Controls Bar - newspaper classified ad style */
  .controls {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    padding: 20px 24px;
    background: var(--paper-warm);
    border: 1px solid var(--border);
    margin-bottom: 24px;
    position: relative;
  }
  .controls::before {
    content: 'CONFIGURATION';
    position: absolute;
    top: -8px;
    left: 16px;
    background: var(--paper);
    padding: 0 8px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 2px;
    color: var(--ink-muted);
    text-transform: uppercase;
  }
  .control-group {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .control-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 600;
    color: var(--ink-muted);
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  select {
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 0;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    color: var(--ink);
    background: var(--paper);
    cursor: pointer;
    transition: border-color 0.15s;
  }
  select:focus { outline: none; border-color: var(--accent); }

  /* Humanize Button - typewriter key style */
  .btn-humanize {
    padding: 14px 40px;
    background: var(--ink);
    color: var(--paper);
    border: none;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    cursor: pointer;
    margin-left: auto;
    transition: all 0.15s;
    position: relative;
  }
  .btn-humanize:hover {
    background: var(--accent);
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(192,88,46,0.3);
  }
  .btn-humanize:active { transform: translateY(1px); box-shadow: none; }
  .btn-humanize:disabled { background: var(--ink-muted); cursor: not-allowed; transform: none; box-shadow: none; }

  /* Editor Grid - newspaper column layout */
  .editors {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    margin-bottom: 24px;
    border: 1px solid var(--border);
    background: var(--paper);
  }
  .editor {
    display: flex;
    flex-direction: column;
    min-height: 450px;
  }
  .editor:first-child { border-right: 1px solid var(--border); }
  .editor-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    background: var(--paper-warm);
    border-bottom: 1px solid var(--border);
  }
  .editor-tag {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--ink-muted);
  }
  .editor-actions { display: flex; gap: 4px; }
  .editor textarea {
    flex: 1;
    padding: 24px 20px;
    border: none;
    font-family: 'Lora', Georgia, serif;
    font-size: 15px;
    line-height: 1.8;
    color: var(--ink);
    background: var(--paper);
    resize: none;
    background-image:
      repeating-linear-gradient(transparent, transparent 27px, var(--border-light) 27px, var(--border-light) 28px);
    background-size: 100% 28px;
    background-position: 0 24px;
  }
  .editor textarea:focus { outline: none; }
  .editor textarea::placeholder { color: var(--ink-muted); font-style: italic; }
  .editor-foot {
    display: flex;
    justify-content: space-between;
    padding: 8px 20px;
    background: var(--paper-warm);
    border-top: 1px solid var(--border);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--ink-muted);
  }

  /* Stats - telegram/ticker style */
  .stats {
    display: flex;
    gap: 0;
    margin-bottom: 24px;
    border: 1px solid var(--border);
    background: var(--paper-warm);
  }
  .stat {
    flex: 1;
    padding: 16px 20px;
    text-align: center;
    border-right: 1px solid var(--border);
  }
  .stat:last-child { border-right: none; }
  .stat-val {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 20px;
    font-weight: 700;
    color: var(--ink);
    display: block;
  }
  .stat-lbl {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--ink-muted);
    margin-top: 4px;
    display: block;
  }

  /* Buttons - clean editorial */
  .btn {
    padding: 8px 14px;
    border: 1px solid var(--border);
    border-radius: 0;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
    background: var(--paper);
    color: var(--ink-light);
    transition: all 0.15s;
    letter-spacing: 0.5px;
  }
  .btn:hover {
    background: var(--ink);
    color: var(--paper);
    border-color: var(--ink);
  }
  .btn-sm { padding: 6px 10px; font-size: 10px; }
  .btn-ghost { border: none; background: none; }
  .btn-ghost:hover { background: var(--paper-dark); color: var(--ink); }

  /* Feature Sections - newspaper classified style */
  .features-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  .feature-card {
    border: 1px solid var(--border);
    background: var(--paper);
  }
  .feature-title {
    padding: 12px 16px;
    background: var(--paper-warm);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--ink-muted);
    user-select: none;
    border-bottom: 1px solid var(--border);
  }
  .feature-title:hover { background: var(--paper-dark); }
  .feature-content {
    display: none;
    padding: 16px;
  }
  .feature-content.open { display: block; }

  /* Progress - typewriter carriage */
  .progress-wrap {
    display: none;
    margin-bottom: 16px;
    border: 1px solid var(--border);
    background: var(--paper-warm);
    padding: 12px 16px;
  }
  .progress-bar {
    height: 2px;
    background: var(--border);
    overflow: hidden;
    margin-bottom: 8px;
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s;
    width: 0%;
  }
  .progress-text {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--ink-muted);
    letter-spacing: 1px;
  }

  /* Toast - wax seal style */
  .toast-box {
    position: fixed;
    top: 16px;
    right: 16px;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .toast {
    background: var(--paper);
    border: 1px solid var(--border);
    border-left: 4px solid var(--ink-muted);
    padding: 12px 16px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    box-shadow: var(--shadow-lg);
    animation: slideIn 0.3s ease;
    max-width: 320px;
  }
  .toast-ok { border-left-color: var(--success); }
  .toast-err { border-left-color: var(--error); }
  .toast-warn { border-left-color: var(--warning); }
  @keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }

  /* Typewriter cursor on output */
  .typewriter-cursor::after {
    content: '|';
    animation: blink 0.8s infinite;
    color: var(--accent);
    font-weight: 700;
  }
  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }

  /* Responsive */
  @media (max-width: 768px) {
    .editors, .features-grid { grid-template-columns: 1fr; }
    .editor:first-child { border-right: none; border-bottom: 1px solid var(--border); }
    .controls { flex-direction: column; }
    .btn-humanize { width: 100%; }
    .masthead-controls { position: static; transform: none; justify-content: center; margin-top: 12px; }
    .stat { padding: 12px; }
  }
</style>
</head>
<body>

<!-- Toast Container -->
<div class="toast-box" id="toastContainer"></div>

<!-- Top Bar -->
<!-- Masthead -->
<div class="masthead">
  <div class="masthead-title"><span>Humanize</span>AI</div>
  <div class="masthead-subtitle">Multi-pass text humanizer &mdash; bypass AI detection</div>
  <div class="masthead-rule"></div>
  <div class="masthead-controls">
    <button class="btn btn-ghost" onclick="toggleTheme()" title="Toggle theme">
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="3" stroke="currentColor" stroke-width="1.5"/><path d="M7 1v2M7 11v2M1 7h2M11 7h2M2.76 2.76l1.41 1.41M9.83 9.83l1.41 1.41M2.76 11.24l1.41-1.41M9.83 4.17l1.41-1.41" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
    </button>
    <button class="btn" onclick="showSettings()">Settings</button>
  </div>
</div>

<div class="main">

  <!-- Controls -->
  <div class="controls" style="position:relative;">
    <div class="control-group">
      <span class="control-label">Passes</span>
      <select id="passes">
        <option value="3">3 (Best)</option>
        <option value="2">2 (Faster)</option>
        <option value="1">1 (Quick)</option>
      </select>
    </div>
    <div class="control-group">
      <span class="control-label">Model</span>
      <select id="model">
        <option value="">Recommended</option>
        <option value="cx/gpt-5.4-mini">GPT-5.4 Mini</option>
        <option value="cx/gpt-5.4">GPT-5.4</option>
        <option value="ds/deepseek-v4-pro">DeepSeek V4</option>
        <option value="ag/claude-sonnet-4-6">Claude Sonnet</option>
        <option value="ag/gemini-3-flash">Gemini 3 Flash</option>
      </select>
    </div>
    <div class="control-group">
      <span class="control-label">Tone</span>
      <select id="tone">
        <option value="casual">Casual</option>
        <option value="academic">Academic</option>
        <option value="business">Business</option>
      </select>
    </div>
    <div class="control-group">
      <span class="control-label">Domain</span>
      <select id="domain">
        <option value="general">General</option>
        <option value="academic">Academic</option>
        <option value="tech">Tech</option>
        <option value="medical">Medical</option>
        <option value="legal">Legal</option>
      </select>
    </div>
    <button class="btn-humanize" id="humanizeBtn" onclick="humanize()">Humanize</button>
  </div>

  <!-- Progress -->
  <div class="progress-wrap" id="progressWrap">
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-text" id="progressText">Starting...</div>
  </div>

  <!-- Editors -->
  <div class="editors">
    <div class="editor">
      <div class="editor-head">
        <span>Input</span>
        <div class="editor-actions">
          <button class="btn btn-sm btn-ghost" onclick="clearAll()">Clear</button>
        </div>
      </div>
      <textarea id="input" placeholder="Paste your AI-generated text here..."></textarea>
      <div class="editor-foot">
        <span id="inputWords">0 words</span>
        <span id="inputChars">0 chars</span>
      </div>
    </div>
    <div class="editor">
      <div class="editor-head">
        <span>Output</span>
        <div class="editor-actions">
          <button class="btn btn-sm btn-ghost" onclick="copyOutput()">Copy</button>
          <button class="btn btn-sm btn-ghost" onclick="downloadDocx()">DOCX</button>
        </div>
      </div>
      <textarea id="output" placeholder="Humanized text will appear here..." readonly></textarea>
      <div class="editor-foot">
        <span id="outputWords">0 words</span>
        <span id="outputScore">Score: --</span>
      </div>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats">
    <div class="stat"><span class="stat-lbl">Words</span><span class="stat-val" id="sWords">0 → 0</span></div>
    <div class="stat"><span class="stat-lbl">Kept</span><span class="stat-val" id="sKept">0%</span></div>
    <div class="stat"><span class="stat-lbl">Score</span><span class="stat-val" id="sScore">--</span></div>
    <div class="stat"><span class="stat-lbl">Time</span><span class="stat-val" id="sTime">--</span></div>
    <div class="stat"><span class="stat-lbl">Grade</span><span class="stat-val" id="sGrade">--</span></div>
  </div>

  <!-- Feature Sections -->
  <div class="features-grid">
    <div class="feature-card">
      <div class="feature-title" onclick="this.nextElementSibling.classList.toggle('open');this.querySelector('.arrow').textContent=this.nextElementSibling.classList.contains('open')?'▾':'▸'">
        <span>Analysis</span><span class="arrow">▸</span>
      </div>
      <div class="feature-content">
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <button class="btn" onclick="checkGrammar()">Grammar</button>
          <button class="btn" onclick="showReadability()">Readability</button>
          <button class="btn" onclick="checkExternal()">ZeroGPT</button>
          <button class="btn" onclick="runVariants()">Variants</button>
          <button class="btn" onclick="showStatsTab()">Stats</button>
        </div>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-title" onclick="this.nextElementSibling.classList.toggle('open');this.querySelector('.arrow').textContent=this.nextElementSibling.classList.contains('open')?'▾':'▸'">
        <span>Advanced</span><span class="arrow">▸</span>
      </div>
      <div class="feature-content">
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <button class="btn" onclick="togglePanel('detectionScorePanel')">Detection</button>
          <button class="btn" onclick="togglePanel('plagiarismPanel')">Plagiarism</button>
          <button class="btn" onclick="startABTest()">A/B Test</button>
          <button class="btn" onclick="showCustomPrompts()">Custom Prompt</button>
          <button class="btn" onclick="togglePanel('contextPanel')">Context</button>
          <button class="btn" onclick="togglePanel('intensityStrategyPanel')">Intensity</button>
        </div>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-title" onclick="this.nextElementSibling.classList.toggle('open');this.querySelector('.arrow').textContent=this.nextElementSibling.classList.contains('open')?'▾':'▸'">
        <span>Export</span><span class="arrow">▸</span>
      </div>
      <div class="feature-content">
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <button class="btn" onclick="downloadDocx()">DOCX</button>
          <button class="btn" onclick="downloadTxt()">TXT</button>
          <button class="btn" onclick="downloadMd()">MD</button>
          <button class="btn" onclick="exportPDF()">PDF</button>
        </div>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-title" onclick="this.nextElementSibling.classList.toggle('open');this.querySelector('.arrow').textContent=this.nextElementSibling.classList.contains('open')?'▾':'▸'">
        <span>Security</span><span class="arrow">▸</span>
      </div>
      <div class="feature-content">
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <button class="btn" onclick="togglePanel('encryptionPanel')">Encryption</button>
          <button class="btn" onclick="scanWatermarks()">Watermark Scan</button>
          <button class="btn" onclick="removeWatermarks()">Remove WM</button>
          <button class="btn" onclick="showCustomLists()">Word Lists</button>
        </div>
      </div>
    </div>
  </div>

</div>

<script>
// Load history on page load
function togglePanel(id) {
  var el = document.getElementById(id);
  if (!el) {
    // Create panel dynamically if it doesn't exist
    el = document.createElement('div');
    el.id = id;
    el.style.cssText = 'display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);';
    el.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;"><h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">' + id.replace('Panel','').replace(/([A-Z])/g,' $1').trim() + '</h3><button class="btn btn-sm" onclick="document.getElementById(\''+id+'\').style.display=\'none\'">Close</button></div><div id="'+id+'_content">Loading...</div>';
    document.body.appendChild(el);
  }
  el.style.display = el.style.display === 'none' || el.style.display === '' ? 'block' : 'none';
}

async function checkGrammar() {
  var text = document.getElementById('input').value.trim();
  if (!text) { showToast('Paste some text first', 'warn'); return; }
  showToast('Checking grammar...', 'info');
  try {
    var resp = await fetch('/api/grammar-fix', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text})});
    var data = await resp.json();
    if (data.error) { showToast('Error: '+data.error, 'err'); return; }
    var changes = data.changes || [];
    if (changes.length === 0) {
      showToast('No grammar issues found!', 'ok');
    } else {
      var html = '<div style="max-height:400px;overflow-y:auto;">';
      changes.forEach(function(c) {
        html += '<div style="padding:8px;margin-bottom:6px;border-left:3px solid var(--accent);background:var(--bg-secondary);">';
        html += '<div style="font-size:12px;color:var(--text-muted);">'+escapeHtml(c.original||'')+'</div>';
        html += '<div style="font-size:13px;color:var(--success);margin-top:4px;">'+escapeHtml(c.corrected||c.suggestion||'')+'</div>';
        html += '</div>';
      });
      html += '</div>';
      togglePanel('grammarPanel');
      var content = document.getElementById('grammarPanel_content');
      if (content) content.innerHTML = html;
      showToast(changes.length + ' grammar issues found', 'warn');
    }
  } catch(e) { showToast('Grammar check failed: '+e.message, 'err'); }
}

async function showReadability() {
  var text = document.getElementById('output').value || document.getElementById('input').value;
  if (!text.trim()) { showToast('No text to analyze', 'warn'); return; }
  try {
    var resp = await fetch('/api/readability', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text})});
    var data = await resp.json();
    if (data.error) { showToast('Error: '+data.error, 'err'); return; }
    var html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">';
    html += '<div style="padding:12px;background:var(--bg-secondary);border:1px solid var(--border);"><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Grade</div><div style="font-size:18px;font-weight:700;">'+(data.grade||'--')+'</div></div>';
    html += '<div style="padding:12px;background:var(--bg-secondary);border:1px solid var(--border);"><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Reading Ease</div><div style="font-size:18px;font-weight:700;">'+(data.reading_ease||'--')+'</div></div>';
    html += '<div style="padding:12px;background:var(--bg-secondary);border:1px solid var(--border);"><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Level</div><div style="font-size:18px;font-weight:700;">'+(data.level||'--')+'</div></div>';
    html += '<div style="padding:12px;background:var(--bg-secondary);border:1px solid var(--border);"><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Avg Sentence</div><div style="font-size:18px;font-weight:700;">'+(data.avg_sentence_length||'--')+' words</div></div>';
    html += '</div>';
    togglePanel('readabilityPanel');
    var content = document.getElementById('readabilityPanel_content');
    if (content) content.innerHTML = html;
  } catch(e) { showToast('Readability check failed: '+e.message, 'err'); }
}

async function runDetectionScan() {
  var text = document.getElementById('output').value || document.getElementById('input').value;
  if (!text.trim()) { showToast('No text to scan', 'warn'); return; }
  showToast('Running detection scan...', 'info');
  try {
    var resp = await fetch('/api/external-check', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text})});
    var data = await resp.json();
    if (data.error) { showToast('Error: '+data.error, 'err'); return; }
    var score = data.ai_percentage || 0;
    var color = score < 30 ? 'var(--success)' : score < 60 ? 'var(--warning)' : 'var(--error)';
    var html = '<div style="text-align:center;padding:20px;">';
    html += '<div style="font-size:48px;font-weight:700;color:'+color+';">'+score+'%</div>';
    html += '<div style="font-size:12px;color:var(--text-muted);margin-top:4px;">AI Detection Score</div>';
    html += '<div style="margin-top:16px;font-size:13px;">'+(data.is_human ? 'Likely Human' : 'Likely AI')+'</div>';
    html += '<div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Sentences: '+(data.human_sentences||0)+' human, '+(data.ai_sentences||0)+' AI</div>';
    html += '</div>';
    togglePanel('detectionScanPanel');
    var content = document.getElementById('detectionScanPanel_content');
    if (content) content.innerHTML = html;
    showToast('Detection scan complete', 'ok');
  } catch(e) { showToast('Detection scan failed: '+e.message, 'err'); }
}

async function runPlagiarismCheck() {
  var text = document.getElementById('output').value || document.getElementById('input').value;
  if (!text.trim()) { showToast('No text to check', 'warn'); return; }
  showToast('Checking plagiarism...', 'info');
  try {
    var resp = await fetch('/api/plagiarism', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text})});
    var data = await resp.json();
    if (data.error) { showToast('Error: '+data.error, 'err'); return; }
    var score = data.score || 0;
    var color = score < 20 ? 'var(--success)' : score < 50 ? 'var(--warning)' : 'var(--error)';
    var html = '<div style="text-align:center;padding:20px;">';
    html += '<div style="font-size:48px;font-weight:700;color:'+color+';">'+score+'%</div>';
    html += '<div style="font-size:12px;color:var(--text-muted);margin-top:4px;">Plagiarism Score</div>';
    if (data.sources && data.sources.length > 0) {
      html += '<div style="margin-top:16px;text-align:left;">';
      data.sources.forEach(function(s) {
        html += '<div style="padding:8px;margin-bottom:4px;border-left:3px solid var(--border);font-size:12px;">'+escapeHtml(s.title||s.url||'Source')+'</div>';
      });
      html += '</div>';
    }
    html += '</div>';
    togglePanel('plagiarismResultPanel');
    var content = document.getElementById('plagiarismResultPanel_content');
    if (content) content.innerHTML = html;
    showToast('Plagiarism check complete', 'ok');
  } catch(e) { showToast('Plagiarism check failed: '+e.message, 'err'); }
}

function encryptText() {
  var text = document.getElementById('output').value || document.getElementById('input').value;
  var password = document.getElementById('encryptPassword').value;
  if (!text || !password) { showToast('Need text and password', 'warn'); return; }
  fetch('/api/encrypt', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text, password:password})})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) { showToast(d.error, 'err'); return; }
      document.getElementById('encryptResult').innerHTML = '<div style="padding:12px;background:var(--bg-secondary);border:1px solid var(--border);word-break:break-all;font-family:IBM Plex Mono,monospace;font-size:11px;">'+escapeHtml(d.encrypted||'')+'</div>';
      showToast('Text encrypted', 'ok');
    })
    .catch(function(e) { showToast('Encryption failed', 'err'); });
}
function decryptText() {
  var encrypted = document.getElementById('encryptResult').textContent;
  var password = document.getElementById('encryptPassword').value;
  if (!encrypted || !password) { showToast('Need encrypted text and password', 'warn'); return; }
  fetch('/api/decrypt', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({encrypted:encrypted, password:password})})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) { showToast(d.error, 'err'); return; }
      document.getElementById('output').value = d.text || '';
      showToast('Text decrypted', 'ok');
    })
    .catch(function(e) { showToast('Decryption failed', 'err'); });
}

async function loadHistory() {
  try {
    const resp = await fetch('/api/history');
    const data = await resp.json();
    const list = document.getElementById('historyList');
    if (!data.length) { list.innerHTML = '<div style="color:#444;font-size:12px;">No history yet</div>'; return; }
    list.innerHTML = data.map(h =>
      '<div class="history-item" onclick="loadFromHistory(' + h.id + ')">' +
      '<div class="preview">' + h.preview + '</div>' +
      '<div class="meta">' + h.input_words + ' &rarr; ' + h.output_words + ' words | ' +
      '<span class="score">' + h.grade_after + '</span> (' + h.score_before + '&rarr;' + h.score_after + ') | ' +
      h.tone + '</div></div>'
    ).join('');
  } catch(e) {}
}

let historyCache = [];
async function loadFromHistory(id) {
  // History items only store preview, not full text. Show info.
  const resp = await fetch('/api/history');
  historyCache = await resp.json();
  const item = historyCache.find(h => h.id === id);
  if (item) {
    document.getElementById('status').textContent = 'Loaded: ' + item.preview.substring(0, 60) + '... (preview only, full text not stored)';
  }
}

async function humanize() {
  const input = document.getElementById('input').value.trim();
  if (!input) { alert('Paste some text first'); return; }
  const wc = input.split(/\s+/).length;
  if(wc > 5000) {
    showToast('Text has '+wc+' words. Processing may take '+Math.round(wc/30)+' seconds. Consider splitting into smaller chunks.', 'warning');
  }
  // Show output textarea, hide empty state
  var outputEl = document.getElementById('output');
  var emptyState = document.getElementById('outputEmptyState');
  if(outputEl) outputEl.style.display = 'block';
  if(emptyState) emptyState.style.display = 'none';

  const passes = parseInt(document.getElementById('passes').value);
  const model = document.getElementById('model').value;
  const tone = document.getElementById('tone').value;
  const btn = document.getElementById('humanizeBtn');
  const status = document.getElementById('status');
  const output = document.getElementById('output');
  const progressBar = document.getElementById('progressBar');
  const progressFill = document.getElementById('progressFill');

  const words = input.split(/\s+/).length;
  const chunks = words <= 300 ? 1 : Math.ceil(words / 150);

  var startTime = Date.now();
  btn.disabled = true;
  output.value = '';
  progressBar.style.display = 'block';
  progressFill.style.width = '2%';
  output.classList.add('typewriter-cursor');
  status.innerHTML = 'Starting... ' + words + ' words (' + chunks + ' chunk' + (chunks>1?'s':'') + ', parallel)';

  try {
    // Start job (returns immediately with job_id)
    const startResp = await fetch('/api/humanize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text: input, 
        passes: passes, 
        model: model, 
        tone: tone, 
        domain: document.getElementById('domain').value, 
        ref_sample: document.getElementById('refSample').value,
        autoRetry: document.getElementById('autoRetry')?.checked || false,
        strictWordCount: document.getElementById('strictWordCount')?.checked || false
      })
    });
    const startData = await startResp.json();
    if (startData.error) {
      status.textContent = 'Error: ' + startData.error;
      progressBar.style.display = 'none';
      btn.disabled = false;
      return;
    }

    const jobId = startData.job_id;
    status.innerHTML = 'Processing... 0/' + startData.chunks + ' chunks done (parallel)';

    // Poll for progress
    let done = false;
    while (!done) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const progResp = await fetch('/api/progress/' + jobId);
        const prog = await progResp.json();

        if (prog.error && prog.status === 'error') {
          status.textContent = 'Error: ' + prog.error;
          progressBar.style.display = 'none';
          btn.disabled = false;
          return;
        }

        // Update progress bar with REAL progress
        progressFill.style.width = Math.max(2, prog.progress || 0) + '%';
        var cd = prog.chunks_done || 0;
        var ct = prog.chunks_total || '?';
        var timeStr = '';
        if(cd > 0 && typeof ct === 'number') {
          var elapsed = (Date.now() - startTime) / 1000;
          var avg = elapsed / cd;
          var rem = Math.round(avg * (ct - cd));
          timeStr = ' | ETA: ' + (rem > 60 ? Math.floor(rem/60) + 'm ' + (rem%60) + 's' : rem + 's');
        }
        status.innerHTML = 'Processing... ' + cd + '/' + ct + ' chunks (' + (prog.progress || 0) + '%)' + timeStr;
        var sp = document.getElementById('stepProgress');
        if(sp && typeof ct === 'number') {
          sp.style.display = 'flex';
          var h = '';
          for(var s=0;s<ct;s++) h += '<span class="' + (s<cd?'step done':(s===cd?'step active':'step')) + '">Chunk ' + (s+1) + '</span>';
          sp.innerHTML = h;
        }

        // Show partial results (typewriter streaming)
        if (prog.partial && prog.partial.length > output.value.length) {
          output.value = prog.partial;
          updateWordCount();
        }

        if (prog.status === 'done') {
          done = true;
          progressFill.style.width = '100%';
          var es1 = document.getElementById('outputEmptyState'); if(es1) es1.style.display='none'; output.style.display='block';
          document.getElementById('outputEmptyState').style.display='none';
          output.value = prog.result || prog.partial;
          output.classList.remove('typewriter-cursor');
          var sp2 = document.getElementById('stepProgress');
          if(sp2) sp2.style.display = 'none';

          const pct = Math.round((prog.output_words || 0) / (prog.input_words || 1) * 100);
          const pctColor = pct >= 80 ? '#00cc88' : pct >= 60 ? '#ffaa00' : '#ff4444';

          const inScore = prog.input_score || {};
          const outScore = prog.output_score || {};
          const outGrade = outScore.grade || 'N/A';
          const scoreColor = (g) => g && g.includes('HUMAN') ? '#00cc88' : g === 'MIXED' ? '#ffaa00' : '#ff4444';

          showToast('Humanization complete!', 'success');
    status.innerHTML = 'Done in ' + (prog.time || '?') + 's';
          document.getElementById('stats').innerHTML =
            '<div><div class="stat-value">' + (prog.input_words || 0) + '</div><div class="stat-label">Input Words</div></div>' +
            '<div><div class="stat-value">' + (prog.output_words || 0) + '</div><div class="stat-label">Output Words</div></div>' +
            '<div><div class="stat-value" style="color:' + pctColor + '">' + pct + '%</div><div class="stat-label">Length Kept</div></div>' +
            '<div><div class="stat-value" style="color:' + scoreColor(outGrade) + '">' + outGrade + '</div><div class="stat-label">AI Score: ' + (inScore.score||'?') + ' &rarr; ' + (outScore.score||'?') + '</div></div>' +
            '<div><div class="stat-value">' + (prog.chunks_total || 0) + '</div><div class="stat-label">Chunks (parallel)</div></div>';

          if (outScore.burstiness || outScore.ai_tells) {
            const burst = outScore.burstiness || {};
            const tells = outScore.ai_tells || {};
            document.getElementById('stats').innerHTML +=
              '<div style="margin-top:12px;width:100%;border-top:1px solid #222;padding-top:12px;font-size:12px;color:#888">' +
              '<b style="color:#aaa">Detection Breakdown:</b><br>' +
              'Burstiness CV: ' + (burst.cv || '?') + ' (' + (burst.score||'?') + ') &nbsp;|&nbsp; ' +
              'AI Words: ' + (tells.ai_words||0) + ' &nbsp;|&nbsp; ' +
              'No Contractions: ' + (tells.no_contractions||0) + ' &nbsp;|&nbsp; ' +
              'Avg Word Len: ' + (tells.avg_word_len||'?') + ' &nbsp;|&nbsp; ' +
              'Density: ' + (tells.density||0) + '%' +
              '</div>';
          }

          setTimeout(() => { progressBar.style.display = 'none'; progressFill.style.width = '0%'; var sp=document.getElementById('stepProgress'); if(sp)sp.style.display='none'; }, 2000);
          loadHistory();
          // Show diff and heatmap
          originalText = document.getElementById('input').value;
          showDiff(originalText, output.value);
          showHeatmap(output.value);
        }
      } catch (pollErr) {
        // Polling error, retry
        console.log('Poll error, retrying...', pollErr);
      }
    }
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
    progressBar.style.display = 'none';
  }
  btn.disabled = false;
}

function copyOutput() {
  const output = document.getElementById('output');
  if (output.value) { navigator.clipboard.writeText(output.value); document.getElementById('status').textContent = 'Copied!'; }
}

async function uploadFile(input) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  const status = document.getElementById('status');
  status.textContent = 'Uploading ' + file.name + '...';
  try {
    const resp = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.error) { status.textContent = 'Error: ' + data.error; return; }
    document.getElementById('input').value = data.text;
    status.textContent = 'Loaded ' + data.filename + ' (' + data.words + ' words)';
  } catch(e) { status.textContent = 'Error: ' + e.message; }
}

async function downloadDocx() {
  const text = document.getElementById('output').value;
  if (!text) { alert('No output to download'); return; }
  const status = document.getElementById('status');
  status.textContent = 'Generating .docx...';
  try {
    const resp = await fetch('/api/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: text})
    });
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'humanized.docx'; a.click();
    URL.revokeObjectURL(url);
    status.textContent = 'Downloaded humanized.docx';
  } catch(e) { status.textContent = 'Error: ' + e.message; }
}

function clearAll() {
  document.getElementById('input').value = '';
  document.getElementById('output').value = '';
  document.getElementById('status').textContent = 'Ready';
  document.getElementById('stats').innerHTML = '';
  document.getElementById('progressBar').style.display = 'none';
}

// Diff view functions
let originalText = '';
function showDiff(original, humanized) {
  const origSentences = original.match(/[^.!?]+[.!?]+/g) || [original];
  const humanSentences = humanized.match(/[^.!?]+[.!?]+/g) || [humanized];
  const diffBody = document.getElementById('diffBody');
  const container = document.getElementById('diffContainer');
  container.style.display = 'block';

  let html = '';
  const maxLen = Math.max(origSentences.length, humanSentences.length);
  for (let i = 0; i < maxLen; i++) {
    const orig = (origSentences[i] || '').trim();
    const human = (humanSentences[i] || '').trim();
    if (orig === human) {
      html += '<div class="diff-sentence diff-unchanged">' + escapeHtml(orig) + '</div>';
    } else {
      if (orig) html += '<div class="diff-sentence diff-removed">' + escapeHtml(orig) + '</div>';
      if (human) html += '<div class="diff-sentence diff-added">' + escapeHtml(human) + '</div>';
    }
  }
  diffBody.innerHTML = html;
}

function toggleDiff() {
  const container = document.getElementById('diffContainer');
  container.style.display = container.style.display === 'none' ? 'block' : 'none';
}

function showHeatmap(text) {
  const heatmapBody = document.getElementById('heatmapBody');
  const container = document.getElementById('heatmapContainer');
  container.style.display = 'block';

  fetch('/api/analyze', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: text})
  }).then(r => r.json()).then(data => {
    const paragraphs = text.split(/\n\n+/);
    let html = '';
    paragraphs.forEach((para, idx) => {
      if (para.trim().length < 20) return;
      const words = para.trim().split(/\s+/).length;
      // Estimate per-paragraph score from overall
      const paraScore = Math.round(data.score.score * (0.5 + Math.random()));
      const cls = paraScore <= 30 ? 'green' : paraScore <= 50 ? 'yellow' : 'red';
      const preview = para.trim().substring(0, 120) + (para.length > 120 ? '...' : '');
      html += '<div class="heatmap-paragraph heatmap-' + cls + '" onclick="reprocessParagraph(' + idx + ')">' +
        '<span class="heatmap-score">' + paraScore + '%</span> ' +
        escapeHtml(preview) + ' <span style="color:#555">(' + words + ' words)</span></div>';
    });
    heatmapBody.innerHTML = html;
  });
}

function reprocessParagraph(idx) {
  const text = document.getElementById('output').value;
  const paragraphs = text.split(/\n\n+/);
  if (idx >= paragraphs.length) return;
  const para = paragraphs[idx];
  document.getElementById('status').textContent = 'Re-processing paragraph ' + (idx + 1) + '...';
  fetch('/api/humanize', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: para, passes: 3, model: document.getElementById('model').value, tone: document.getElementById('tone').value})
  }).then(r => r.json()).then(data => {
    if (data.error) { document.getElementById('status').textContent = 'Error: ' + data.error; return; }
    const jobId = data.job_id;
    const poll = setInterval(() => {
      fetch('/api/progress/' + jobId).then(r => r.json()).then(prog => {
        if (prog.status === 'done') {
          clearInterval(poll);
          paragraphs[idx] = prog.result;
          document.getElementById('output').value = paragraphs.join('\n\n');
          document.getElementById('status').textContent = 'Paragraph ' + (idx + 1) + ' re-processed!';
          showHeatmap(document.getElementById('output').value);
        }
      });
    }, 2000);
  });
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Drag & drop support
document.addEventListener('DOMContentLoaded', () => {
  loadHistory();
  const zone = document.getElementById('uploadZone');
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.style.borderColor = '#00cc88'; });
  zone.addEventListener('dragleave', () => { zone.style.borderColor = '#333'; });
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.style.borderColor = '#333';
    const file = e.dataTransfer.files[0];
    if (file) {
      const input = document.getElementById('fileInput');
      const dt = new DataTransfer(); dt.items.add(file);
      input.files = dt.files;
      uploadFile(input);
    }
  });
});
// Theme toggle
let darkMode = true;
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('collapsed');
}
function toggleTheme() {
  darkMode = !darkMode;
  document.body.classList.toggle('light-mode', !darkMode);
}

// Stats panel
function showStatsTab() {
  const panel = document.getElementById('statsPanel');
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  if (panel.style.display === 'block') loadStats();
}

async function loadStats() {
  try {
    const resp = await fetch('/api/stats');
    const s = await resp.json();
    const grid = document.getElementById('statsGrid');
    const avgTime = s.total_jobs > 0 ? Math.round(s.total_time_seconds / s.total_jobs) : 0;
    const successRate = s.total_jobs > 0 ? Math.round(s.success_count / s.total_jobs * 100) : 0;
    const cacheRate = (s.cache_hits + s.cache_misses) > 0 ? Math.round(s.cache_hits / (s.cache_hits + s.cache_misses) * 100) : 0;
    grid.innerHTML = 
      '<div class="stat-card"><div class="value">' + s.total_jobs + '</div><div class="label">Total Jobs</div></div>' +
      '<div class="stat-card"><div class="value">' + s.total_input_words.toLocaleString() + '</div><div class="label">Words Processed</div></div>' +
      '<div class="stat-card"><div class="value">' + avgTime + 's</div><div class="label">Avg Time</div></div>' +
      '<div class="stat-card"><div class="value">' + successRate + '%</div><div class="label">Success Rate</div></div>' +
      '<div class="stat-card"><div class="value">' + cacheRate + '%</div><div class="label">Cache Hit Rate</div></div>' +
      '<div class="stat-card"><div class="value">' + Object.keys(s.models_used).length + '</div><div class="label">Models Used</div></div>';
  } catch(e) {}
}

// Custom word lists
function showCustomLists() {
  const panel = document.getElementById('customListsPanel');
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

async function saveCustomLists() {
  const preserve = document.getElementById('preserveList').value;
  const avoid = document.getElementById('avoidList').value;
  try {
    const resp = await fetch('/api/custom-lists', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({preserve: preserve, avoid: avoid})
    });
    const data = await resp.json();
    document.getElementById('status').textContent = 'Saved: ' + data.preserve_count + ' preserve, ' + data.avoid_count + ' avoid words';
  } catch(e) {
    document.getElementById('status').textContent = 'Error: ' + e.message;
  }
}

// Preview processing
async function runPreview() {
  const input = document.getElementById('input').value.trim();
  if (!input) { alert('Paste text first'); return; }
  const model = document.getElementById('model').value;
  const tone = document.getElementById('tone').value;
  const panel = document.getElementById('previewPanel');
  panel.style.display = 'block';
  document.getElementById('previewContent').innerHTML = '<span style="color:#666;">Processing preview...</span>';
  
  try {
    const resp = await fetch('/api/preview', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: input, model: model, tone: tone})
    });
    const data = await resp.json();
    if (data.error) { document.getElementById('previewContent').textContent = 'Error: ' + data.error; return; }
    
    const inScore = data.input_score?.score || '?';
    const outScore = data.output_score?.score || '?';
    const pct = Math.round(data.output_words / data.input_words * 100);
    document.getElementById('previewContent').innerHTML = 
      '<div style="margin-bottom:8px;"><b>Score:</b> ' + inScore + ' → ' + outScore + ' | <b>Words:</b> ' + data.input_words + ' → ' + data.output_words + ' (' + pct + '%) | <b>Time:</b> ' + data.time + 's | <b>Est full:</b> ~' + data.estimated_time + 's</div>' +
      '<div style="background:#111;padding:12px;border-radius:4px;font-size:12px;line-height:1.6;max-height:300px;overflow-y:auto;">' + escapeHtml(data.preview_output) + '</div>';
  } catch(e) {
    document.getElementById('previewContent').textContent = 'Error: ' + e.message;
  }
}

// External ZeroGPT check
async function checkExternal() {
  const text = document.getElementById('output').value || document.getElementById('input').value;
  if (!text) { alert('No text to check'); return; }
  document.getElementById('status').textContent = 'Checking ZeroGPT...';
  try {
    const resp = await fetch('/api/external-check', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: text.substring(0, 5000)})
    });
    const data = await resp.json();
    if (data.error) { document.getElementById('status').textContent = 'ZeroGPT: ' + data.error; return; }
    const ai = data.ai_percentage || 0;
    const grade = ai < 30 ? 'HUMAN' : ai < 60 ? 'MIXED' : 'AI';
    const color = ai < 30 ? '#00cc88' : ai < 60 ? '#ffaa00' : '#ff4444';
    document.getElementById('status').innerHTML = 'ZeroGPT: <span style="color:' + color + ';font-weight:700;">' + ai + '% AI (' + grade + ')</span> | ' + data.human_sentences + ' human / ' + data.ai_sentences + ' AI sentences';
  } catch(e) {
    document.getElementById('status').textContent = 'ZeroGPT error: ' + e.message;
  }
}

// Export functions
function downloadTxt() {
  const text = document.getElementById('output').value;
  if (!text) { alert('No output'); return; }
  fetch('/api/download/txt', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}) })
    .then(r => r.blob()).then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = 'humanized.txt'; a.click();
      URL.revokeObjectURL(url);
    });
}

function downloadMd() {
  const text = document.getElementById('output').value;
  if (!text) { alert('No output'); return; }
  fetch('/api/download/md', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}) })
    .then(r => r.blob()).then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = 'humanized.md'; a.click();
      URL.revokeObjectURL(url);
    });
}

// Version history
async function loadVersions() {
  try {
    const resp = await fetch('/api/versions');
    const versions = await resp.json();
    const list = document.getElementById('versionList');
    if (!versions.length) { list.innerHTML = '<div style="color:#444;font-size:12px;">No versions yet</div>'; return; }
    list.innerHTML = versions.map(v => 
      '<div class="version-item" onclick="loadVersion(' + v.id + ')">' +
      '<span style="color:#00cc88;">' + v.score + '</span> | ' + v.input_words + '→' + v.output_words + 'w | ' + v.tone +
      '</div>'
    ).join('');
  } catch(e) {}
}

async function loadVersion(id) {
  try {
    const resp = await fetch('/api/version/' + id);
    const v = await resp.json();
    if (v.output_text) {
      document.getElementById('output').value = v.output_text;
      document.getElementById('status').textContent = 'Loaded version ' + id + ' (' + v.output_words + ' words, score: ' + v.score + ')';
    }
  } catch(e) {}
}

// Multiple file drag & drop
document.addEventListener('DOMContentLoaded', () => {
  loadHistory();
  loadVersions();
  loadSettings();
  const savedTab = localStorage.getItem('activeTab') || 'input';
  const tabBtn = document.querySelector('.tab-btn[onclick*="'+savedTab+'"]');
  if(tabBtn) tabBtn.click();
  const savedAccent = localStorage.getItem('accentColor');
  if(savedAccent) setAccentColor(document.querySelector('.color-dot[data-color="'+savedAccent+'"]'));
  setupRealTimeDetection();
  const zone = document.getElementById('uploadZone');
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.style.borderColor = '#00cc88'; });
  zone.addEventListener('dragleave', () => { zone.style.borderColor = '#333'; });
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.style.borderColor = '#333';
    const files = e.dataTransfer.files;
    if (files.length === 1) {
      const input = document.getElementById('fileInput');
      const dt = new DataTransfer(); dt.items.add(files[0]);
      input.files = dt.files;
      uploadFile(input);
    } else if (files.length > 1) {
      batchUpload(files);
    }
  });
});

async function batchUpload(files) {
  const status = document.getElementById('status');
  status.textContent = 'Batch uploading ' + files.length + ' files...';
  for (let i = 0; i < files.length; i++) {
    const formData = new FormData();
    formData.append('file', files[i]);
    try {
      const resp = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await resp.json();
      if (data.text) {
        const current = document.getElementById('input').value;
        document.getElementById('input').value = current + (current ? '\n\n---\n\n' : '') + data.text;
      }
    } catch(e) {}
  }
  status.textContent = 'Loaded ' + files.length + ' files (' + document.getElementById('input').value.split(/\s+/).length + ' total words)';
}


// Live word count
function updateWordCount() {
  var inp = document.getElementById('input').value;
  var out = document.getElementById('output').value;
  var iw = inp.trim() ? inp.trim().split(/\s+/).length : 0;
  var ow = out.trim() ? out.trim().split(/\s+/).length : 0;
  var pct = iw > 0 ? Math.round(ow/iw*100) : 0;
  var color = Math.abs(ow-iw) < 20 ? '#00cc88' : '#ffaa00';
  var el = document.getElementById('liveWordCount');
  if(el) el.innerHTML = 'Input: <b>'+iw+'</b> | Output: <b>'+ow+'</b> | <span style="color:'+color+'">'+pct+'% kept</span>';
  var txt = out || inp;
  var chars = txt.length;
  var paras = txt.trim() ? txt.trim().split(/\n\s*\n/).length : 0;
  var sents = txt.trim() ? txt.trim().split(/[.!?]+/).filter(function(s){return s.trim().length>0}).length : 0;
  var avgLen = sents > 0 ? (txt.trim().split(/\s+/).length / sents).toFixed(1) : '0';
  var allWords = txt.trim().toLowerCase().split(/\s+/).filter(function(w){return w.length>0});
  var unique = {};
  for(var i=0;i<allWords.length;i++) unique[allWords[i]]=1;
  var uniqueCount = Object.keys(unique).length;
  var level = '--';
  if(allWords.length > 0) {
    var avgSentLen = allWords.length / Math.max(sents,1);
    var longW = allWords.filter(function(w){return w.replace(/[^a-z]/g,'').length > 6}).length;
    var syllableRatio = longW / Math.max(allWords.length,1);
    var fk = 206.835 - 1.015 * avgSentLen - 84.6 * syllableRatio;
    fk = Math.max(0, Math.min(100, fk));
    if(fk >= 80) level = 'Easy (Gr 5-6)';
    else if(fk >= 60) level = 'Standard (Gr 7-8)';
    else if(fk >= 40) level = 'Moderate (Gr 9-12)';
    else if(fk >= 20) level = 'College';
    else level = 'Graduate';
  }
  var cEl = document.getElementById('statChars'); if(cEl) cEl.textContent = chars;
  var pEl = document.getElementById('statParas'); if(pEl) pEl.textContent = paras;
  var sEl = document.getElementById('statSents'); if(sEl) sEl.textContent = sents;
  var aEl = document.getElementById('statAvgLen'); if(aEl) aEl.textContent = avgLen;
  var uEl = document.getElementById('statUnique'); if(uEl) uEl.textContent = uniqueCount;
  var lEl = document.getElementById('statLevel'); if(lEl) lEl.textContent = level;
}
document.getElementById('input').addEventListener('input', updateWordCount);
document.getElementById('output').addEventListener('input', updateWordCount);
// Toast notification system
function showToast(msg, type) {
  type = type || 'info';
  var colors = {success:'#00cc88', error:'#ff4444', warning:'#ffaa00', info:'#888'};
  var icons = {success:'✓', error:'✗', warning:'⚠', info:'ℹ'};
  var toast = document.createElement('div');
  toast.style.cssText = 'background:#1a1a1a;border:1px solid '+colors[type]+';border-left:3px solid '+colors[type]+';color:#e0e0e0;padding:10px 16px;border-radius:6px;font-size:13px;animation:slideInRight 0.3s ease;pointer-events:auto;max-width:320px;';
  toast.innerHTML = '<span style="color:'+colors[type]+';font-weight:700;margin-right:8px;">'+icons[type]+'</span>'+msg;
  var container = document.getElementById('toastContainer');
  if(!container) { container = document.createElement('div'); container.id = 'toastContainer'; container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;'; document.body.appendChild(container); }
  container.appendChild(toast);
  setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(function() { if(toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
  }, 4000);
}



function runVariants() {
  var text = document.getElementById('input').value.trim();
  if(!text) { alert('No text'); return; }
  document.getElementById('variantsPanel').style.display = 'block';
  document.getElementById('variantsResults').innerHTML = 'Generating 3 variants...';
  fetch('/api/variants', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text, num:3, model:document.getElementById('model').value, tone:document.getElementById('tone').value})})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if(d.error) { document.getElementById('variantsResults').textContent = 'Error: '+d.error; return; }
      if(d.variants && d.variants.length > 0) {
        var best = d.variants.reduce(function(a,b) { return (a.score||99) < (b.score||99) ? a : b; });
        var h = '<div style="margin-bottom:8px;color:#00cc88;">Best variant (score: '+(best.score||'?')+')</div>';
        d.variants.forEach(function(v,i) {
          var isBest = v === best;
          h += '<div style="padding:10px;border:1px solid '+(isBest?'#00cc88':'#222')+';border-radius:4px;margin-bottom:8px;cursor:pointer;" onclick="document.getElementById(\'output\').value=this.querySelector(\'.vtext\').textContent;updateWordCount();">';
          h += '<div style="font-size:11px;color:#888;margin-bottom:4px;">Variant '+(i+1)+' | Score: '+(v.score||'?')+' | '+(v.words||0)+' words'+(isBest?' ★':'')+'</div>';
          h += '<div class="vtext" style="font-size:12px;max-height:120px;overflow-y:auto;">'+(v.text||'').substring(0,300)+'...</div>';
          h += '</div>';
        });
        document.getElementById('variantsResults').innerHTML = h;
      }
    })
    .catch(function(e) { document.getElementById('variantsResults').textContent = 'Error: '+e.message; });
}
function showToneSlider() { document.getElementById('toneSliderPanel').style.display = 'block'; }
function applyToneSlider() {
  var text = document.getElementById('output').value || document.getElementById('input').value;
  if(!text) { alert('No text'); return; }
  var level = document.getElementById('toneLevel').value;
  document.getElementById('toneResult').textContent = 'Applying...';
  fetch('/api/tone-slider', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text, level:parseFloat(level)})})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if(d.text) { document.getElementById('output').value = d.text; updateWordCount(); document.getElementById('toneResult').innerHTML = '<span style="color:#00cc88;">Applied!</span>'; }
      else document.getElementById('toneResult').textContent = 'Error';
    })
    .catch(function(e) { document.getElementById('toneResult').textContent = 'Error: '+e.message; });
}
function showStyleTrain() { document.getElementById('styleTrainPanel').style.display = 'block'; }
function saveStyleSamples() {
  var samples = document.getElementById('styleSamples').value.trim();
  if(!samples) { alert('Paste samples first'); return; }
  localStorage.setItem('humanizerStyleProfile', samples);
  document.getElementById('styleStatus').innerHTML = '<span style="color:#00cc88;">Style profile saved! ('+samples.length+' chars)</span>';
}


// ═══════════════════════════════════════════════════════════════
// ADDON: 39 Features - Stats, Auto-save, Skeleton, Context Menu, etc.
// ═══════════════════════════════════════════════════════════════

// ── #61, #62, #63: Extended Stats (chars, paragraphs, sentences) ──
function updateExtendedStats() {
  var inp = document.getElementById('input').value;
  var out = document.getElementById('output').value;
  var statsEl = document.getElementById('extendedStats');
  if(!statsEl) return;
  
  var inChars = inp.length;
  var outChars = out.length;
  var inParas = inp.trim() ? inp.trim().split(/\n\s*\n/).length : 0;
  var outParas = out.trim() ? out.trim().split(/\n\s*\n/).length : 0;
  var inSents = inp.trim() ? (inp.match(/[.!?]+/g) || []).length : 0;
  var outSents = out.trim() ? (out.match(/[.!?]+/g) || []).length : 0;
  var inWords = inp.trim() ? inp.trim().split(/\s+/).length : 0;
  var outWords = out.trim() ? out.trim().split(/\s+/).length : 0;
  var avgInSent = inSents > 0 ? Math.round(inWords / inSents) : 0;
  var avgOutSent = outSents > 0 ? Math.round(outWords / outSents) : 0;
  
  var uniqueIn = new Set(inp.toLowerCase().match(/\b\w+\b/g) || []).size;
  var uniqueOut = new Set(out.toLowerCase().match(/\b\w+\b/g) || []).size;
  
  statsEl.innerHTML = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;font-family:JetBrains Mono,monospace;">' +
    '<div><span style="color:var(--muted);">Chars:</span> ' + inChars.toLocaleString() + ' → ' + outChars.toLocaleString() + '</div>' +
    '<div><span style="color:var(--muted);">Paras:</span> ' + inParas + ' → ' + outParas + '</div>' +
    '<div><span style="color:var(--muted);">Sents:</span> ' + inSents + ' → ' + outSents + '</div>' +
    '<div><span style="color:var(--muted);">Avg/Sent:</span> ' + avgInSent + ' → ' + avgOutSent + 'w</div>' +
    '<div><span style="color:var(--muted);">Unique:</span> ' + uniqueIn + ' → ' + uniqueOut + '</div>' +
    '<div><span style="color:var(--muted);">Vocab:</span> ' + (inWords > 0 ? Math.round(uniqueIn/inWords*100) : 0) + '% → ' + (outWords > 0 ? Math.round(uniqueOut/outWords*100) : 0) + '%</div>' +
  '</div>';
}

// ── #72: Auto-save drafts every 30s ──
var _autoSaveTimer = null;
function startAutoSave() {
  if(_autoSaveTimer) clearInterval(_autoSaveTimer);
  _autoSaveTimer = setInterval(function() {
    var inp = document.getElementById('input').value;
    if(inp && inp.length > 50) {
      localStorage.setItem('humanizer_draft', JSON.stringify({
        text: inp, saved: new Date().toISOString(), words: inp.split(/\s+/).length
      }));
    }
  }, 30000);
}
function loadDraft() {
  var draft = localStorage.getItem('humanizer_draft');
  if(draft) {
    try {
      var d = JSON.parse(draft);
      var input = document.getElementById('input');
      if(!input.value && d.text) {
        input.value = d.text;
        updateWordCount();
        showToast('Draft restored (' + d.words + ' words, saved ' + new Date(d.saved).toLocaleTimeString() + ')', 'info');
      }
    } catch(e) {}
  }
}

// ── #51: Skeleton Loader ──
function showSkeleton(outputEl) {
  if(!outputEl) return;
  outputEl.innerHTML = '<div class="skeleton-wrap">' +
    '<div class="skel-line" style="width:90%"></div>' +
    '<div class="skel-line" style="width:75%"></div>' +
    '<div class="skel-line" style="width:85%"></div>' +
    '<div class="skel-line" style="width:60%"></div>' +
    '<div class="skel-line" style="width:80%"></div>' +
    '<div class="skel-line" style="width:70%"></div>' +
    '<div class="skel-line" style="width:90%"></div>' +
    '<div class="skel-line" style="width:45%"></div>' +
  '</div>';
}

// ── #52: Empty State Illustration ──
function showEmptyState() {
  var output = document.getElementById('output');
  if(output && !output.value) {
    output.placeholder = '';
    var wrapper = output.parentElement;
    if(!wrapper.querySelector('.empty-state')) {
      var es = document.createElement('div');
      es.className = 'empty-state';
      es.innerHTML = '<svg viewBox="0 0 200 150" width="120" style="margin:40px auto;display:block;opacity:0.3;">' +
        '<rect x="30" y="20" width="140" height="110" rx="8" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
        '<line x1="50" y1="45" x2="150" y2="45" stroke="currentColor" stroke-width="1" opacity="0.5"/>' +
        '<line x1="50" y1="60" x2="130" y2="60" stroke="currentColor" stroke-width="1" opacity="0.5"/>' +
        '<line x1="50" y1="75" x2="145" y2="75" stroke="currentColor" stroke-width="1" opacity="0.5"/>' +
        '<line x1="50" y1="90" x2="110" y2="90" stroke="currentColor" stroke-width="1" opacity="0.5"/>' +
        '<circle cx="160" cy="110" r="20" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
        '<path d="M155 110 L165 110 M160 105 L160 115" stroke="currentColor" stroke-width="2"/>' +
      '</svg>' +
      '<p style="text-align:center;color:var(--muted);font-style:italic;font-family:Playfair Display,serif;">Paste AI-generated text to humanize</p>';
      output.style.opacity = '0';
      wrapper.insertBefore(es, output);
    }
  }
}
function hideEmptyState() {
  var es = document.querySelector('.empty-state');
  if(es) es.remove();
  var output = document.getElementById('output');
  if(output) output.style.opacity = '1';
}

// ── #55: Context Menu ──
var _ctxMenu = null;
function initContextMenu() {
  document.addEventListener('contextmenu', function(e) {
    var target = e.target;
    if(target.tagName === 'TEXTAREA' || target.closest('textarea')) {
      e.preventDefault();
      removeContextMenu();
      _ctxMenu = document.createElement('div');
      _ctxMenu.className = 'ctx-menu';
      var items = [
        {label: 'Humanize Selection', action: function() { humanizeSelection(); }},
        {label: 'Grammar Check', action: function() { if(typeof checkGrammar === 'function') checkGrammar(); }},
        {label: 'Check Readability', action: function() { if(typeof checkReadability === 'function') checkReadability(); }},
        {label: 'Copy', action: function() { navigator.clipboard.writeText(target.value || target.textContent); showToast('Copied','success'); }},
        {label: 'Paste', action: async function() { try { target.value = await navigator.clipboard.readText(); updateWordCount(); } catch(e){} }},
        {label: 'Clear', action: function() { target.value = ''; updateWordCount(); }},
        {label: 'Detect Jargon', action: function() { detectJargon(target); }},
      ];
      items.forEach(function(item) {
        var div = document.createElement('div');
        div.className = 'ctx-item';
        div.textContent = item.label;
        div.onclick = function() { item.action(); removeContextMenu(); };
        _ctxMenu.appendChild(div);
      });
      _ctxMenu.style.left = e.pageX + 'px';
      _ctxMenu.style.top = e.pageY + 'px';
      document.body.appendChild(_ctxMenu);
    }
  });
  document.addEventListener('click', removeContextMenu);
}
function removeContextMenu() {
  if(_ctxMenu) { _ctxMenu.remove(); _ctxMenu = null; }
}
function humanizeSelection() {
  var sel = window.getSelection().toString();
  if(!sel) { var inp = document.getElementById('input'); sel = inp.value; }
  if(sel) { document.getElementById('input').value = sel; if(typeof startHumanize === 'function') startHumanize(); }
}

// ── #56: Breadcrumb Navigation ──
function updateBreadcrumb(path) {
  var bc = document.getElementById('breadcrumb');
  if(!bc) return;
  bc.innerHTML = path.map(function(item, i) {
    if(i === path.length - 1) return '<span class="bc-current">' + item + '</span>';
    return '<span class="bc-link" onclick="navigateBreadcrumb(\'' + item + '\')">' + item + '</span><span class="bc-sep">›</span>';
  }).join('');
}
function navigateBreadcrumb(item) {
  if(item === 'Home') { updateBreadcrumb(['Home']); }
  else if(item === 'History') { updateBreadcrumb(['Home', 'History']); }
}

// ── #60: Sort History ──
var _historySortKey = 'date';
function sortHistory(key) {
  _historySortKey = key;
  fetch('/api/history').then(function(r) { return r.json(); }).then(function(hist) {
    if(key === 'date') hist.sort(function(a,b) { return new Date(b.timestamp) - new Date(a.timestamp); });
    else if(key === 'words') hist.sort(function(a,b) { return b.output_words - a.output_words; });
    else if(key === 'score') hist.sort(function(a,b) { return a.score_after - b.score_after; });
    else if(key === 'model') hist.sort(function(a,b) { return (a.model||'').localeCompare(b.model||''); });
    renderHistoryList(hist);
  });
}
function renderHistoryList(hist) {
  var el = document.getElementById('historyList');
  if(!el) return;
  el.innerHTML = hist.length === 0 ? '<p style="color:var(--muted);padding:12px;">No history yet</p>' :
    hist.slice(0, 50).map(function(h) {
      var grade = h.grade_after || '?';
      var gradeColor = grade === 'HUMAN' ? '#00cc88' : grade === 'LIKELY_HUMAN' ? '#4ade80' : grade === 'MIXED' ? '#fbbf24' : '#ef4444';
      return '<div class="history-item" onclick="loadVersion(' + h.id + ')" style="padding:8px 12px;border-bottom:1px solid var(--border);cursor:pointer;">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;">' +
        '<span style="font-size:12px;">' + (h.input_words||0) + '→' + (h.output_words||0) + 'w</span>' +
        '<span style="font-size:10px;color:' + gradeColor + ';font-weight:600;">' + grade + '</span>' +
        '</div>' +
        '<div style="font-size:10px;color:var(--muted);margin-top:2px;">' + new Date(h.timestamp).toLocaleString() + '</div>' +
      '</div>';
    }).join('');
}

// ── #70: Jargon Detector ──
function detectJargon(target) {
  var text = target.value || target.textContent;
  var jargonList = ['utilize','leverage','synergize','paradigm','holistic','scalable','robust','seamless',
    'cutting-edge','next-generation','disruptive','innovative','streamline','optimize','facilitate',
    'implement','infrastructure','methodology','framework','deliverable','stakeholder','bandwidth',
    'circle back','deep dive','move the needle','low-hanging fruit','boil the ocean','pivot',
    'ideate','actionable','granular','drill down','touch base','value-add','ecosystem'];
  var found = [];
  jargonList.forEach(function(j) {
    var regex = new RegExp('\\b' + j + '\\b', 'gi');
    var matches = text.match(regex);
    if(matches) found.push({word: j, count: matches.length});
  });
  if(found.length === 0) { showToast('No jargon detected', 'success'); return; }
  var msg = found.sort(function(a,b) { return b.count - a.count; })
    .map(function(f) { return f.word + ' (' + f.count + 'x)'; }).join(', ');
  showToast('Jargon found: ' + msg, 'warning');
}

// ── #126: A/B Testing ──
function startABTest() {
  var text = document.getElementById('input').value;
  if(!text || text.split(/\s+/).length < 10) { alert('Need at least 10 words for A/B test'); return; }
  var models = Object.keys(MODEL_OPTIONS || {});
  if(models.length < 2) { alert('Need at least 2 models'); return; }
  
  var modelA = models[0]; // Recommended
  var modelB = models[1]; // Best Quality
  
  document.getElementById('abTestPanel').style.display = 'block';
  document.getElementById('abStatus').textContent = 'Running A/B test...';
  
  // Run both in parallel
  Promise.all([
    fetch('/api/humanize', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text:text, passes:2, model:modelA, tone:'casual'})}).then(function(r){return r.json();}),
    fetch('/api/humanize', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text:text, passes:2, model:modelB, tone:'casual'})}).then(function(r){return r.json();})
  ]).then(function(results) {
    // Poll both jobs
    var jobA = results[0].job_id;
    var jobB = results[1].job_id;
    pollABJobs(jobA, jobB);
  }).catch(function(e) { document.getElementById('abStatus').textContent = 'Error: ' + e.message; });
}
function pollABJobs(jobA, jobB) {
  var doneA = null, doneB = null;
  function check(jobId, label) {
    return fetch('/api/progress/' + jobId).then(function(r) { return r.json(); }).then(function(d) {
      if(d.status === 'done') return d;
      if(d.status === 'error') throw new Error(d.error);
      return new Promise(function(resolve) { setTimeout(function() { check(jobId, label).then(resolve); }, 2000); });
    });
  }
  Promise.all([check(jobA, 'A'), check(jobB, 'B')]).then(function(results) {
    var a = results[0], b = results[1];
    document.getElementById('abStatus').textContent = 'Done! Vote for the better version:';
    document.getElementById('abResultA').innerHTML = '<div style="padding:12px;border:1px solid var(--border);border-radius:4px;cursor:pointer;" onclick="voteAB(\'A\')">' +
      '<div style="font-weight:600;margin-bottom:6px;">Version A <span style="font-size:10px;color:var(--muted);">(Score: ' + (a.output_score?.score||'?') + ')</span></div>' +
      '<div style="font-size:12px;max-height:150px;overflow-y:auto;">' + (a.result||'').substring(0,500) + '...</div></div>';
    document.getElementById('abResultB').innerHTML = '<div style="padding:12px;border:1px solid var(--border);border-radius:4px;cursor:pointer;" onclick="voteAB(\'B\')">' +
      '<div style="font-weight:600;margin-bottom:6px;">Version B <span style="font-size:10px;color:var(--muted);">(Score: ' + (b.output_score?.score||'?') + ')</span></div>' +
      '<div style="font-size:12px;max-height:150px;overflow-y:auto;">' + (b.result||'').substring(0,500) + '...</div></div>';
    window._abResults = {A: a, B: b};
  }).catch(function(e) { document.getElementById('abStatus').textContent = 'Error: ' + e.message; });
}
function voteAB(choice) {
  var r = window._abResults[choice];
  if(r) {
    document.getElementById('output').value = r.result;
    updateWordCount();
    document.getElementById('abTestPanel').style.display = 'none';
    showToast('Version ' + choice + ' applied!', 'success');
  }
}

// ── #128: Custom Prompts ──
function showCustomPrompts() {
  var panel = document.getElementById('customPromptPanel');
  if(!panel) return;
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  var saved = localStorage.getItem('humanizer_custom_prompt');
  if(saved) document.getElementById('customPromptText').value = saved;
}
function saveCustomPrompt() {
  var text = document.getElementById('customPromptText').value.trim();
  if(!text) { alert('Enter a prompt first'); return; }
  localStorage.setItem('humanizer_custom_prompt', text);
  showToast('Custom prompt saved!', 'success');
}

// ── #136: Model Uptime Monitor ──
var _modelStatus = {};
function checkModelStatus() {
  fetch('/api/model-status').then(function(r) { return r.json(); }).then(function(data) {
    _modelStatus = data;
    var el = document.getElementById('modelStatus');
    if(!el) return;
    var html = Object.entries(data).map(function(entry) {
      var model = entry[0], status = entry[1];
      var dot = status.ok ? '<span style="color:#00cc88;">●</span>' : '<span style="color:#ef4444;">●</span>';
      var latency = status.latency_ms ? status.latency_ms + 'ms' : 'unknown';
      return '<div style="font-size:11px;padding:2px 0;">' + dot + ' ' + model.split('/').pop() + ' <span style="color:var(--muted);">' + latency + '</span></div>';
    }).join('');
    el.innerHTML = html || '<span style="color:var(--muted);">No data</span>';
  }).catch(function() {});
}

// ── #102: Export as PDF ──
function exportPDF() {
  var text = document.getElementById('output').value;
  if(!text) { alert('No output to export'); return; }
  
  // Use print-to-PDF via hidden iframe
  var iframe = document.createElement('iframe');
  iframe.style.position = 'fixed';
  iframe.style.right = '0';
  iframe.style.bottom = '0';
  iframe.style.width = '0';
  iframe.style.height = '0';
  iframe.style.border = '0';
  document.body.appendChild(iframe);
  
  var doc = iframe.contentWindow.document;
  doc.open();
  doc.write('<!DOCTYPE html><html><head><title>Humanized Text</title>' +
    '<style>body{font-family:Georgia,serif;max-width:700px;margin:40px auto;padding:20px;line-height:1.8;color:#222;}' +
    'h1{font-size:18px;border-bottom:2px solid #222;padding-bottom:8px;margin-bottom:20px;}' +
    '.meta{font-size:11px;color:#666;margin-bottom:30px;font-family:monospace;}</style></head><body>' +
    '<h1>Humanized Text</h1>' +
    '<div class="meta">Generated: ' + new Date().toLocaleString() + ' | Words: ' + text.split(/\s+/).length + '</div>' +
    '<div>' + text.replace(/\n/g, '<br>') + '</div>
<!-- Floating Panel Container -->
<div id="grammarPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Grammar Check</h3>
    <button class="btn btn-sm" onclick="this.closest('div[id$=Panel]').style.display='none'">Close</button>
  </div>
  <div id="grammarPanel_content">No issues checked yet.</div>
</div>

<div id="readabilityPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Readability</h3>
    <button class="btn btn-sm" onclick="this.closest('div[id$=Panel]').style.display='none'">Close</button>
  </div>
  <div id="readabilityPanel_content">No text analyzed yet.</div>
</div>

<div id="detectionScanPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Detection Scores</h3>
    <button class="btn btn-sm" onclick="this.closest('div[id$=Panel]').style.display='none'">Close</button>
  </div>
  <div id="detectionScanPanel_content">No scan run yet.</div>
</div>

<div id="plagiarismResultPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Plagiarism Check</h3>
    <button class="btn btn-sm" onclick="this.closest('div[id$=Panel]').style.display='none'">Close</button>
  </div>
  <div id="plagiarismResultPanel_content">No check run yet.</div>
</div>

<div id="toneSliderPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:500px;width:90%;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Tone Slider</h3>
    <button class="btn btn-sm" onclick="this.style.display='none'">Close</button>
  </div>
  <div>
    <label style="font-size:12px;color:var(--text-muted);">Formality: <span id="toneValue">0.5</span></label>
    <input type="range" id="toneRange" min="0" max="1" step="0.1" value="0.5" style="width:100%;margin:8px 0;" oninput="document.getElementById('toneValue').textContent=this.value">
    <button class="btn" onclick="applyToneSlider()" style="margin-top:8px;">Apply</button>
  </div>
</div>

<div id="styleTrainPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:500px;width:90%;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Style Training</h3>
    <button class="btn btn-sm" onclick="this.style.display='none'">Close</button>
  </div>
  <div>
    <textarea id="styleSamples" placeholder="Paste your writing samples here (one per line)..." style="width:100%;height:120px;padding:12px;border:1px solid var(--border);font-family:Lora,serif;font-size:14px;background:var(--bg);color:var(--text);resize:vertical;"></textarea>
    <button class="btn" onclick="saveStyleSamples()" style="margin-top:8px;">Save Samples</button>
  </div>
</div>

<div id="contextPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Context Memory</h3>
    <button class="btn btn-sm" onclick="this.closest('div[id$=Panel]').style.display='none'">Close</button>
  </div>
  <div id="contextPanel_content">
    <textarea id="contextInput" placeholder="Paste reference text for context..." style="width:100%;height:100px;padding:12px;border:1px solid var(--border);font-family:Lora,serif;font-size:14px;background:var(--bg);color:var(--text);resize:vertical;"></textarea>
    <button class="btn" onclick="saveToContext(document.getElementById('contextInput').value, 'Manual')" style="margin-top:8px;">Add to Context</button>
    <div id="contextList" style="margin-top:12px;"></div>
  </div>
</div>

<div id="intensityStrategyPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:500px;width:90%;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Intensity & Strategy</h3>
    <button class="btn btn-sm" onclick="this.style.display='none'">Close</button>
  </div>
  <div>
    <label style="font-size:12px;color:var(--text-muted);">Intensity: <span id="intensityValue">0.5</span></label>
    <input type="range" id="intensityRange" min="0" max="1" step="0.1" value="0.5" style="width:100%;margin:8px 0;" oninput="document.getElementById('intensityValue').textContent=this.value;updateIntensityLabel()">
    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;">
      <button class="btn" onclick="setStrategy('conservative')">Conservative</button>
      <button class="btn" onclick="setStrategy('balanced')">Balanced</button>
      <button class="btn" onclick="setStrategy('aggressive')">Aggressive</button>
    </div>
  </div>
</div>

<div id="encryptionPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:500px;width:90%;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Encryption</h3>
    <button class="btn btn-sm" onclick="this.style.display='none'">Close</button>
  </div>
  <div>
    <input type="password" id="encryptPassword" placeholder="Encryption password" style="width:100%;padding:10px;border:1px solid var(--border);font-family:IBM Plex Mono,monospace;font-size:13px;background:var(--bg);color:var(--text);">
    <div style="display:flex;gap:8px;margin-top:12px;">
      <button class="btn" onclick="encryptText()">Encrypt</button>
      <button class="btn" onclick="decryptText()">Decrypt</button>
    </div>
    <div id="encryptResult" style="margin-top:12px;font-size:12px;"></div>
  </div>
</div>

<div id="customPromptPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:500px;width:90%;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">Custom Prompt</h3>
    <button class="btn btn-sm" onclick="this.closest('div[id$=Panel]').style.display='none'">Close</button>
  </div>
  <div>
    <textarea id="customPromptText" placeholder="Write your custom system prompt..." style="width:100%;height:120px;padding:12px;border:1px solid var(--border);font-family:IBM Plex Mono,monospace;font-size:13px;background:var(--bg);color:var(--text);resize:vertical;"></textarea>
    <button class="btn" onclick="saveCustomPrompt()" style="margin-top:8px;">Save Prompt</button>
  </div>
</div>

<div id="abTestPanel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:200;background:var(--paper);border:1px solid var(--border);padding:24px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <h3 style="font-family:IBM Plex Mono,monospace;font-size:14px;text-transform:uppercase;letter-spacing:2px;">A/B Test</h3>
    <button class="btn btn-sm" onclick="document.getElementById('abTestPanel').style.display='none'">Close</button>
  </div>
  <div id="abTestPanel_content">Running A/B test...</div>
</div>

</body></html>');
  doc.close();
  
  setTimeout(function() {
    iframe.contentWindow.print();
    setTimeout(function() { document.body.removeChild(iframe); }, 1000);
  }, 500);
}

// ── #7: Intensity Slider UI ──
function updateIntensityLabel() {
  var slider = document.getElementById('intensitySlider');
  var label = document.getElementById('intensityLabel');
  if(!slider || !label) return;
  var val = parseInt(slider.value);
  var names = {1:'Light Touch', 2:'Light', 3:'Moderate', 4:'Strong', 5:'Heavy Rewrite'};
  label.textContent = names[val] || 'Moderate';
}

// ── #15: Rewriting Strategy Selector ──
function setStrategy(strategy) {
  document.querySelectorAll('.strategy-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.strategy === strategy);
  });
  localStorage.setItem('humanizer_strategy', strategy);
}

// ── #10: Context Memory ──
var _contextDocs = [];
function saveToContext(text, label) {
  _contextDocs.push({text: text.substring(0, 2000), label: label, timestamp: Date.now()});
  if(_contextDocs.length > 10) _contextDocs.shift();
  localStorage.setItem('humanizer_context', JSON.stringify(_contextDocs));
  updateContextPanel();
}
function loadContext() {
  try { _contextDocs = JSON.parse(localStorage.getItem('humanizer_context') || '[]'); } catch(e) { _contextDocs = []; }
}
function updateContextPanel() {
  var el = document.getElementById('contextList');
  if(!el) return;
  el.innerHTML = _contextDocs.length === 0 ? '<span style="color:var(--muted);font-size:11px;">No context saved</span>' :
    _contextDocs.map(function(d, i) {
      return '<div style="font-size:11px;padding:3px 0;border-bottom:1px solid var(--border);">' +
        '<span style="color:var(--accent);">#' + (i+1) + '</span> ' + d.label + ' <span style="color:var(--muted);">(' + d.text.split(/\s+/).length + 'w)</span>' +
      '</div>';
    }).join('');
}

// ── #30: Readability Progression ──
var _readabilityHistory = [];
function trackReadability(score) {
  _readabilityHistory.push({score: score, timestamp: Date.now()});
  if(_readabilityHistory.length > 20) _readabilityHistory.shift();
  localStorage.setItem('humanizer_readability', JSON.stringify(_readabilityHistory));
  updateReadabilityChart();
}
function updateReadabilityChart() {
  var el = document.getElementById('readabilityChart');
  if(!el || _readabilityHistory.length < 2) return;
  var maxScore = Math.max.apply(null, _readabilityHistory.map(function(r) { return r.score; }));
  el.innerHTML = _readabilityHistory.map(function(r) {
    var pct = maxScore > 0 ? Math.round(r.score / maxScore * 100) : 0;
    var color = r.score < 40 ? '#00cc88' : r.score < 60 ? '#fbbf24' : '#ef4444';
    return '<div style="display:flex;align-items:center;gap:6px;font-size:10px;margin:2px 0;">' +
      '<span style="width:40px;color:var(--muted);">' + new Date(r.timestamp).toLocaleTimeString().substring(0,5) + '</span>' +
      '<div style="flex:1;height:8px;background:var(--surface);border-radius:4px;">' +
      '<div style="width:' + pct + '%;height:100%;background:' + color + ';border-radius:4px;transition:width 0.3s;"></div></div>' +
      '<span style="width:30px;text-align:right;">' + r.score + '</span></div>';
  }).join('');
}

// ── #44: Watermark Detection ──
function detectWatermarks(text) {
  // Check for common invisible watermarks
  var suspicious = [];
  // Zero-width characters
  var zwChars = text.match(/[\u200B\u200C\u200D\uFEFF\u2060]/g);
  if(zwChars) suspicious.push('Zero-width characters (' + zwChars.length + ')');
  // Homoglyphs (Cyrillic lookalikes)
  var cyrillic = text.match(/[\u0400-\u04FF]/g);
  if(cyrillic) suspicious.push('Cyrillic characters (' + cyrillic.length + ')');
  // Unusual whitespace
  var weirdSpace = text.match(/[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g);
  if(weirdSpace) suspicious.push('Unusual whitespace (' + weirdSpace.length + ')');
  return suspicious;
}
function scanWatermarks() {
  var text = document.getElementById('input').value || document.getElementById('output').value;
  var marks = detectWatermarks(text);
  if(marks.length === 0) { showToast('No watermarks detected', 'success'); return; }
  showToast('Found: ' + marks.join(', '), 'warning');
}
function removeWatermarks() {
  var el = document.getElementById('input');
  var text = el.value;
  text = text.replace(/[\u200B\u200C\u200D\uFEFF\u2060]/g, '');
  text = text.replace(/[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g, ' ');
  text = text.replace(/\u00AD/g, ''); // soft hyphen
  el.value = text;
  updateWordCount();
  showToast('Watermarks removed', 'success');
}

// ── #29: Keyword Density ──
function analyzeKeywords() {
  var text = (document.getElementById('output').value || document.getElementById('input').value).toLowerCase();
  var words = text.match(/\b[a-z]{4,}\b/g) || [];
  var stop = new Set(['this','that','with','from','have','been','were','will','would','could','should','their','there','they','them','what','when','where','which','about','after','before','between','through','during','each','other','some','such','only','than','into','over','also','just','very','much','more','most','these','those','then','because','while','although','however','therefore','furthermore','moreover','nevertheless','nonetheless','according','including','provide','provide','provides','provided','using','based','related','consider','important','understand','different','specific','general','example','particular','possible','available','individual','particular','significant','additional','following','previous','current','research','study','result','analysis','system','method','process','approach','problem','solution','development','information','technology','application','performance','management','experience','education','knowledge','community','government','development']);
  var freq = {};
  words.forEach(function(w) { if(!stop.has(w) && w.length > 3) freq[w] = (freq[w]||0) + 1; });
  var sorted = Object.entries(freq).sort(function(a,b) { return b[1] - a[1]; }).slice(0, 15);
  var total = words.length;
  
  var el = document.getElementById('keywordDensity');
  if(!el) return;
  el.innerHTML = '<div style="font-size:11px;font-weight:600;margin-bottom:6px;">Top Keywords</div>' +
    sorted.map(function(entry) {
      var word = entry[0], count = entry[1];
      var pct = (count / total * 100).toFixed(1);
      return '<div style="display:flex;align-items:center;gap:6px;font-size:10px;margin:2px 0;">' +
        '<span style="width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + word + '</span>' +
        '<div style="flex:1;height:6px;background:var(--surface);border-radius:3px;">' +
        '<div style="width:' + Math.min(parseFloat(pct) * 10, 100) + '%;height:100%;background:var(--accent);border-radius:3px;"></div></div>' +
        '<span style="width:40px;text-align:right;color:var(--muted);">' + count + ' (' + pct + '%)</span></div>';
    }).join('');
}

// ── Toast notification system ──
function showToast(msg, type) {
  type = type || 'info';
  var colors = {success:'#00cc88', error:'#ef4444', warning:'#fbbf24', info:'#3b82f6'};
  var toast = document.createElement('div');
  toast.className = 'toast-notification';
  toast.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;background:var(--card);border:1px solid ' + colors[type] + ';border-left:3px solid ' + colors[type] + ';color:var(--text);font-size:12px;z-index:10000;animation:slideIn 0.3s ease;max-width:400px;font-family:Inter,sans-serif;';
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(function() { toast.style.animation = 'slideOut 0.3s ease'; setTimeout(function() { toast.remove(); }, 300); }, 3000);
}

// ── Init all addon features ──
(function() {
  // CSS additions
  var style = document.createElement('style');
  style.textContent = 
    '.skel-line{height:12px;background:var(--surface);border-radius:4px;margin:8px 0;animation:shimmer 1.5s infinite;}' +
    '@keyframes shimmer{0%{opacity:0.5;}50%{opacity:1;}100%{opacity:0.5;}}' +
    '.skeleton-wrap{padding:16px;}' +
    '.ctx-menu{position:absolute;background:var(--card);border:1px solid var(--border);border-radius:4px;z-index:9999;min-width:160px;box-shadow:0 4px 12px rgba(0,0,0,0.15);}' +
    '.ctx-item{padding:8px 14px;font-size:12px;cursor:pointer;transition:background 0.15s;}' +
    '.ctx-item:hover{background:var(--surface);}' +
    '.bc-link{color:var(--accent);cursor:pointer;font-size:11px;}' +
    '.bc-current{color:var(--text);font-size:11px;font-weight:600;}' +
    '.bc-sep{color:var(--muted);margin:0 4px;font-size:11px;}' +
    '@keyframes slideIn{from{transform:translateX(100%);opacity:0;}to{transform:translateX(0);opacity:1;}}' +
    '@keyframes slideOut{from{transform:translateX(0);opacity:1;}to{transform:translateX(100%);opacity:0;}}' +
    '.strategy-btn{padding:6px 12px;font-size:11px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;border-radius:4px;transition:all 0.15s;}' +
    '.strategy-btn.active{background:var(--accent);border-color:var(--accent);color:#fff;}';
  document.head.appendChild(style);
  
  // Override updateWordCount to also update extended stats
  var _origUpdate = window.updateWordCount;
  window.updateWordCount = function() {
    if(_origUpdate) _origUpdate();
    updateExtendedStats();
    hideEmptyState();
  };
  
  // Init on DOM ready
  if(document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAddons);
  } else {
    initAddons();
  }
})();

function initAddons() {
  initContextMenu();
  startAutoSave();
  loadDraft();
  loadContext();
  showEmptyState();
  updateBreadcrumb(['Home']);
  
  // Add extended stats container
  var wcEl = document.querySelector('[id*="wordCount"], [class*="word-count"]');
  if(wcEl && !document.getElementById('extendedStats')) {
    var ext = document.createElement('div');
    ext.id = 'extendedStats';
    ext.style.cssText = 'margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:4px;';
    wcEl.parentElement.appendChild(ext);
  }
  
  // Add breadcrumb
  if(!document.getElementById('breadcrumb')) {
    var bc = document.createElement('div');
    bc.id = 'breadcrumb';
    bc.style.cssText = 'padding:4px 12px;font-size:11px;border-bottom:1px solid var(--border);';
    var main = document.querySelector('main, .main, #app, body > div:first-child');
    if(main) main.insertBefore(bc, main.firstChild);
  }
  
  // Check model status periodically
  checkModelStatus();
  setInterval(checkModelStatus, 60000);
  
  // Load saved strategy
  var savedStrategy = localStorage.getItem('humanizer_strategy');
  if(savedStrategy) setStrategy(savedStrategy);
  
  // Load readability history
  try { _readabilityHistory = JSON.parse(localStorage.getItem('humanizer_readability') || '[]'); } catch(e) {}
  updateReadabilityChart();
}

// ═══════════════════════════════════════════════════════════════
// ADDON: 39 Features - Stats, Auto-save, Skeleton, Context Menu, etc.
// ═══════════════════════════════════════════════════════════════

// ── #61, #62, #63: Extended Stats (chars, paragraphs, sentences) ──
function updateExtendedStats() {
  var inp = document.getElementById('input').value;
  var out = document.getElementById('output').value;
  var statsEl = document.getElementById('extendedStats');
  if(!statsEl) return;
  var inChars = inp.length, outChars = out.length;
  var inParas = inp.trim() ? inp.trim().split(/\n\s*\n/).length : 0;
  var outParas = out.trim() ? out.trim().split(/\n\s*\n/).length : 0;
  var inSents = inp.trim() ? (inp.match(/[.!?]+/g) || []).length : 0;
  var outSents = out.trim() ? (out.match(/[.!?]+/g) || []).length : 0;
  var inWords = inp.trim() ? inp.trim().split(/\s+/).length : 0;
  var outWords = out.trim() ? out.trim().split(/\s+/).length : 0;
  var avgIn = inSents > 0 ? Math.round(inWords / inSents) : 0;
  var avgOut = outSents > 0 ? Math.round(outWords / outSents) : 0;
  var uIn = new Set(inp.toLowerCase().match(/\b\w+\b/g) || []).size;
  var uOut = new Set(out.toLowerCase().match(/\b\w+\b/g) || []).size;
  statsEl.innerHTML = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;font-family:JetBrains Mono,monospace;">' +
    '<div><span style="color:var(--muted);">Chars:</span> '+inChars.toLocaleString()+' → '+outChars.toLocaleString()+'</div>' +
    '<div><span style="color:var(--muted);">Paras:</span> '+inParas+' → '+outParas+'</div>' +
    '<div><span style="color:var(--muted);">Sents:</span> '+inSents+' → '+outSents+'</div>' +
    '<div><span style="color:var(--muted);">Avg/Sent:</span> '+avgIn+' → '+avgOut+'w</div>' +
    '<div><span style="color:var(--muted);">Unique:</span> '+uIn+' → '+uOut+'</div>' +
    '<div><span style="color:var(--muted);">Vocab:</span> '+(inWords>0?Math.round(uIn/inWords*100):0)+'% → '+(outWords>0?Math.round(uOut/outWords*100):0)+'%</div></div>';
}

// ── #72: Auto-save drafts every 30s ──
var _autoSaveTimer = null;
function startAutoSave() {
  if(_autoSaveTimer) clearInterval(_autoSaveTimer);
  _autoSaveTimer = setInterval(function() {
    var inp = document.getElementById('input').value;
    if(inp && inp.length > 50) {
      localStorage.setItem('humanizer_draft', JSON.stringify({text:inp, saved:new Date().toISOString(), words:inp.split(/\s+/).length}));
    }
  }, 30000);
}
function loadDraft() {
  try {
    var d = JSON.parse(localStorage.getItem('humanizer_draft'));
    var input = document.getElementById('input');
    if(d && !input.value && d.text) {
      input.value = d.text; updateWordCount();
      showToast('Draft restored ('+d.words+' words)', 'info');
    }
  } catch(e) {}
}

// ── #51: Skeleton Loader ──
function showSkeleton(el) {
  if(!el) return;
  el.innerHTML = '<div class="skeleton-wrap"><div class="skel-line" style="width:90%"></div><div class="skel-line" style="width:75%"></div><div class="skel-line" style="width:85%"></div><div class="skel-line" style="width:60%"></div><div class="skel-line" style="width:80%"></div><div class="skel-line" style="width:70%"></div><div class="skel-line" style="width:90%"></div><div class="skel-line" style="width:45%"></div></div>';
}

// ── #52: Empty State ──
function showEmptyState() {
  var output = document.getElementById('output');
  if(output && !output.value) {
    var w = output.parentElement;
    if(!w.querySelector('.empty-state')) {
      var es = document.createElement('div');
      es.className = 'empty-state';
      es.innerHTML = '<svg viewBox="0 0 200 150" width="120" style="margin:40px auto;display:block;opacity:0.3;"><rect x="30" y="20" width="140" height="110" rx="8" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="50" y1="45" x2="150" y2="45" stroke="currentColor" stroke-width="1" opacity="0.5"/><line x1="50" y1="60" x2="130" y2="60" stroke="currentColor" stroke-width="1" opacity="0.5"/><line x1="50" y1="75" x2="145" y2="75" stroke="currentColor" stroke-width="1" opacity="0.5"/><line x1="50" y1="90" x2="110" y2="90" stroke="currentColor" stroke-width="1" opacity="0.5"/><circle cx="160" cy="110" r="20" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M155 110 L165 110 M160 105 L160 115" stroke="currentColor" stroke-width="2"/></svg><p style="text-align:center;color:var(--muted);font-style:italic;font-family:Playfair Display,serif;">Paste AI-generated text to humanize</p>';
      output.style.opacity = '0'; w.insertBefore(es, output);
    }
  }
}
function hideEmptyState() {
  var es = document.querySelector('.empty-state');
  if(es) es.remove();
  var o = document.getElementById('output');
  if(o) o.style.opacity = '1';
}

// ── #55: Context Menu ──
var _ctxMenu = null;
function initContextMenu() {
  document.addEventListener('contextmenu', function(e) {
    var t = e.target;
    if(t.tagName === 'TEXTAREA' || t.closest('textarea')) {
      e.preventDefault(); removeContextMenu();
      _ctxMenu = document.createElement('div');
      _ctxMenu.className = 'ctx-menu';
      [{l:'Humanize Selection',a:function(){humanizeSelection();}},
       {l:'Grammar Check',a:function(){if(typeof checkGrammar==='function')checkGrammar();}},
       {l:'Check Readability',a:function(){if(typeof checkReadability==='function')checkReadability();}},
       {l:'Copy',a:function(){navigator.clipboard.writeText(t.value||t.textContent);showToast('Copied','success');}},
       {l:'Paste',a:async function(){try{t.value=await navigator.clipboard.readText();updateWordCount();}catch(x){}}},
       {l:'Clear',a:function(){t.value='';updateWordCount();}},
       {l:'Detect Jargon',a:function(){detectJargon(t);}},
       {l:'Scan Watermarks',a:function(){scanWatermarks();}},
       {l:'Remove Watermarks',a:function(){removeWatermarks();}}
      ].forEach(function(item) {
        var d = document.createElement('div');
        d.className = 'ctx-item'; d.textContent = item.l;
        d.onclick = function() { item.a(); removeContextMenu(); };
        _ctxMenu.appendChild(d);
      });
      _ctxMenu.style.left = e.pageX+'px'; _ctxMenu.style.top = e.pageY+'px';
      document.body.appendChild(_ctxMenu);
    }
  });
  document.addEventListener('click', removeContextMenu);
}
function removeContextMenu() { if(_ctxMenu){_ctxMenu.remove();_ctxMenu=null;} }
function humanizeSelection() {
  var sel = window.getSelection().toString();
  if(!sel) sel = document.getElementById('input').value;
  if(sel) { document.getElementById('input').value = sel; if(typeof startHumanize==='function') startHumanize(); }
}

// ── #56: Breadcrumb ──
function updateBreadcrumb(path) {
  var bc = document.getElementById('breadcrumb');
  if(!bc) return;
  bc.innerHTML = path.map(function(item,i) {
    if(i===path.length-1) return '<span class="bc-current">'+item+'</span>';
    return '<span class="bc-link" onclick="navigateBreadcrumb(\''+item+'\')">'+item+'</span><span class="bc-sep">›</span>';
  }).join('');
}
function navigateBreadcrumb(item) {
  if(item==='Home') updateBreadcrumb(['Home']);
  else if(item==='History') updateBreadcrumb(['Home','History']);
}

// ── #60: Sort History ──
var _historySortKey = 'date';
function sortHistory(key) {
  _historySortKey = key;
  fetch('/api/history').then(function(r){return r.json();}).then(function(hist) {
    if(key==='date') hist.sort(function(a,b){return new Date(b.timestamp)-new Date(a.timestamp);});
    else if(key==='words') hist.sort(function(a,b){return b.output_words-a.output_words;});
    else if(key==='score') hist.sort(function(a,b){return a.score_after-b.score_after;});
    renderHistoryList(hist);
  });
}
function renderHistoryList(hist) {
  var el = document.getElementById('historyList');
  if(!el) return;
  el.innerHTML = hist.length===0 ? '<p style="color:var(--muted);padding:12px;">No history</p>' :
    hist.slice(0,50).map(function(h) {
      var g = h.grade_after||'?';
      var gc = g==='HUMAN'?'#00cc88':g==='LIKELY_HUMAN'?'#4ade80':g==='MIXED'?'#fbbf24':'#ef4444';
      return '<div class="history-item" onclick="loadVersion('+h.id+')" style="padding:8px 12px;border-bottom:1px solid var(--border);cursor:pointer;"><div style="display:flex;justify-content:space-between;"><span style="font-size:12px;">'+h.input_words+'→'+h.output_words+'w</span><span style="font-size:10px;color:'+gc+';font-weight:600;">'+g+'</span></div><div style="font-size:10px;color:var(--muted);margin-top:2px;">'+new Date(h.timestamp).toLocaleString()+'</div></div>';
    }).join('');
}

// ── #70: Jargon Detector ──
function detectJargon(target) {
  var text = target.value || target.textContent;
  var jargon = ['utilize','leverage','synergize','paradigm','holistic','scalable','robust','seamless','cutting-edge','next-generation','disruptive','innovative','streamline','optimize','facilitate','infrastructure','methodology','framework','deliverable','stakeholder','bandwidth','circle back','deep dive','move the needle','low-hanging fruit','boil the ocean','pivot','ideate','actionable','granular','drill down','touch base','value-add','ecosystem'];
  var found = [];
  jargon.forEach(function(j) { var m = text.match(new RegExp('\\b'+j+'\\b','gi')); if(m) found.push({w:j,c:m.length}); });
  if(!found.length) { showToast('No jargon detected','success'); return; }
  showToast('Jargon: '+found.sort(function(a,b){return b.c-a.c;}).map(function(f){return f.w+' ('+f.c+'x)';}).join(', '), 'warning');
}

// ── #126: A/B Testing ──
function startABTest() {
  var text = document.getElementById('input').value;
  if(!text || text.split(/\s+/).length<10) { alert('Need 10+ words'); return; }
  var panel = document.getElementById('abTestPanel');
  if(!panel) { alert('A/B panel not found'); return; }
  panel.style.display = 'block';
  document.getElementById('abStatus').textContent = 'Running A/B test...';
  var models = ['cx/gpt-5.5','ag/claude-sonnet-4-6'];
  Promise.all(models.map(function(m) {
    return fetch('/api/humanize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text,passes:2,model:m,tone:'casual'})}).then(function(r){return r.json();});
  })).then(function(results) {
    pollABJobs(results[0].job_id, results[1].job_id);
  }).catch(function(e){document.getElementById('abStatus').textContent='Error: '+e.message;});
}
function pollABJobs(jA, jB) {
  function poll(jid) {
    return fetch('/api/progress/'+jid).then(function(r){return r.json();}).then(function(d) {
      if(d.status==='done') return d;
      if(d.status==='error') throw new Error(d.error);
      return new Promise(function(r){setTimeout(function(){poll(jid).then(r);},2000);});
    });
  }
  Promise.all([poll(jA),poll(jB)]).then(function(res) {
    document.getElementById('abStatus').textContent = 'Vote for better version:';
    document.getElementById('abResultA').innerHTML = '<div style="padding:12px;border:1px solid var(--border);cursor:pointer;border-radius:4px;" onclick="voteAB(\'A\')"><b>Version A</b> <span style="font-size:10px;color:var(--muted);">(Score: '+(res[0].output_score?.score||'?')+')</span><div style="font-size:12px;max-height:150px;overflow-y:auto;margin-top:6px;">'+(res[0].result||'').substring(0,500)+'...</div></div>';
    document.getElementById('abResultB').innerHTML = '<div style="padding:12px;border:1px solid var(--border);cursor:pointer;border-radius:4px;" onclick="voteAB(\'B\')"><b>Version B</b> <span style="font-size:10px;color:var(--muted);">(Score: '+(res[1].output_score?.score||'?')+')</span><div style="font-size:12px;max-height:150px;overflow-y:auto;margin-top:6px;">'+(res[1].result||'').substring(0,500)+'...</div></div>';
    window._abResults = {A:res[0], B:res[1]};
  }).catch(function(e){document.getElementById('abStatus').textContent='Error: '+e.message;});
}
function voteAB(c) {
  var r = window._abResults[c];
  if(r) { document.getElementById('output').value=r.result; updateWordCount(); document.getElementById('abTestPanel').style.display='none'; showToast('Version '+c+' applied!','success'); }
}

// ── #128: Custom Prompts ──
function showCustomPrompts() {
  var p = document.getElementById('customPromptPanel');
  if(!p) return;
  p.style.display = p.style.display==='none' ? 'block' : 'none';
  var s = localStorage.getItem('humanizer_custom_prompt');
  if(s) document.getElementById('customPromptText').value = s;
}
function saveCustomPrompt() {
  var t = document.getElementById('customPromptText').value.trim();
  if(!t) { alert('Enter a prompt'); return; }
  localStorage.setItem('humanizer_custom_prompt', t);
  showToast('Custom prompt saved!','success');
}

// ── #136: Model Uptime ──
var _modelStatus = {};
function checkModelStatus() {
  fetch('/api/model-status').then(function(r){return r.json();}).then(function(data) {
    _modelStatus = data;
    var el = document.getElementById('modelStatus');
    if(!el) return;
    el.innerHTML = Object.entries(data).map(function(e) {
      var m=e[0],s=e[1]; var dot=s.ok?'<span style="color:#00cc88;">●</span>':'<span style="color:#ef4444;">●</span>';
      return '<div style="font-size:11px;padding:2px 0;">'+dot+' '+m.split('/').pop()+' <span style="color:var(--muted);">'+(s.latency_ms||'?')+'ms</span></div>';
    }).join('') || '<span style="color:var(--muted);">No data</span>';
  }).catch(function(){});
}

// ── #102: Export PDF ──
function exportPDF() {
  var text = document.getElementById('output').value;
  if(!text) { alert('No output'); return; }
  var iframe = document.createElement('iframe');
  iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
  document.body.appendChild(iframe);
  var d = iframe.contentWindow.document;
  d.open();
  d.write('<!DOCTYPE html><html><head><title>Humanized Text</title><style>body{font-family:Georgia,serif;max-width:700px;margin:40px auto;padding:20px;line-height:1.8;color:#222;}h1{font-size:18px;border-bottom:2px solid #222;padding-bottom:8px;}.meta{font-size:11px;color:#666;margin-bottom:30px;font-family:monospace;}</style></head><body><h1>Humanized Text</h1><div class="meta">Generated: '+new Date().toLocaleString()+' | Words: '+text.split(/\s+/).length+'</div><div>'+text.replace(/\n/g,'<br>')+'</div></body></html>');
  d.close();
  setTimeout(function(){iframe.contentWindow.print();setTimeout(function(){document.body.removeChild(iframe);},1000);},500);
}

// ── #7: Intensity Slider ──
function updateIntensityLabel() {
  var s = document.getElementById('intensitySlider'), l = document.getElementById('intensityLabel');
  if(!s||!l) return;
  var v = parseInt(s.value);
  l.textContent = {1:'Light Touch',2:'Light',3:'Moderate',4:'Strong',5:'Heavy Rewrite'}[v] || 'Moderate';
}

// ── #15: Strategy Selector ──
function setStrategy(s) {
  document.querySelectorAll('.strategy-btn').forEach(function(b){b.classList.toggle('active',b.dataset.strategy===s);});
  localStorage.setItem('humanizer_strategy', s);
}

// ── #10: Context Memory ──
var _contextDocs = [];
function saveToContext(text, label) {
  _contextDocs.push({text:text.substring(0,2000), label:label, ts:Date.now()});
  if(_contextDocs.length>10) _contextDocs.shift();
  localStorage.setItem('humanizer_context', JSON.stringify(_contextDocs));
  updateContextPanel();
}
function loadContext() { try{_contextDocs=JSON.parse(localStorage.getItem('humanizer_context')||'[]');}catch(e){_contextDocs=[];} }
function updateContextPanel() {
  var el = document.getElementById('contextList');
  if(!el) return;
  el.innerHTML = _contextDocs.length===0 ? '<span style="color:var(--muted);font-size:11px;">No context saved</span>' :
    _contextDocs.map(function(d,i) {
      return '<div style="font-size:11px;padding:3px 0;border-bottom:1px solid var(--border);"><span style="color:var(--accent);">#'+(i+1)+'</span> '+d.label+' <span style="color:var(--muted);">('+d.text.split(/\s+/).length+'w)</span></div>';
    }).join('');
}

// ── #30: Readability Progression ──
var _readabilityHistory = [];
function trackReadability(score) {
  _readabilityHistory.push({score:score, ts:Date.now()});
  if(_readabilityHistory.length>20) _readabilityHistory.shift();
  localStorage.setItem('humanizer_readability', JSON.stringify(_readabilityHistory));
  updateReadabilityChart();
}
function updateReadabilityChart() {
  var el = document.getElementById('readabilityChart');
  if(!el || _readabilityHistory.length<2) return;
  var mx = Math.max.apply(null, _readabilityHistory.map(function(r){return r.score;}));
  el.innerHTML = _readabilityHistory.map(function(r) {
    var p = mx>0?Math.round(r.score/mx*100):0;
    var c = r.score<40?'#00cc88':r.score<60?'#fbbf24':'#ef4444';
    return '<div style="display:flex;align-items:center;gap:6px;font-size:10px;margin:2px 0;"><span style="width:40px;color:var(--muted);">'+new Date(r.ts).toLocaleTimeString().substring(0,5)+'</span><div style="flex:1;height:8px;background:var(--surface);border-radius:4px;"><div style="width:'+p+'%;height:100%;background:'+c+';border-radius:4px;"></div></div><span style="width:30px;text-align:right;">'+r.score+'</span></div>';
  }).join('');
}

// ── #44: Watermark Detection ──
function detectWatermarks(text) {
  var s = [];
  var zw = text.match(/[\u200B\u200C\u200D\uFEFF\u2060]/g);
  if(zw) s.push('Zero-width chars ('+zw.length+')');
  var cy = text.match(/[\u0400-\u04FF]/g);
  if(cy) s.push('Cyrillic chars ('+cy.length+')');
  var ws = text.match(/[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g);
  if(ws) s.push('Unusual whitespace ('+ws.length+')');
  return s;
}
function scanWatermarks() {
  var text = document.getElementById('input').value || document.getElementById('output').value;
  var m = detectWatermarks(text);
  showToast(m.length===0 ? 'No watermarks detected' : 'Found: '+m.join(', '), m.length===0?'success':'warning');
}
function removeWatermarks() {
  var el = document.getElementById('input');
  el.value = el.value.replace(/[\u200B\u200C\u200D\uFEFF\u2060\u00AD]/g,'').replace(/[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g,' ');
  updateWordCount();
  showToast('Watermarks removed','success');
}

// ── #29: Keyword Density ──
function analyzeKeywords() {
  var text = (document.getElementById('output').value || document.getElementById('input').value).toLowerCase();
  var words = text.match(/\b[a-z]{4,}\b/g) || [];
  var stop = new Set(['this','that','with','from','have','been','were','will','would','could','should','their','there','they','them','what','when','where','which','about','after','before','between','through','during','each','other','some','such','only','than','into','over','also','just','very','much','more','most','these','those','then','because','while','although','however','therefore','furthermore','moreover','nevertheless','according','including','provide','provides','provided','using','based','related','consider','important','understand','different','specific','general','example','particular','possible','available','individual','significant','additional','following','previous','current','research','study','result','analysis','system','method','process','approach','problem','solution','development','information','technology','application','performance','management','experience','education','knowledge','community','government']);
  var freq = {};
  words.forEach(function(w){if(!stop.has(w)&&w.length>3) freq[w]=(freq[w]||0)+1;});
  var sorted = Object.entries(freq).sort(function(a,b){return b[1]-a[1];}).slice(0,15);
  var total = words.length;
  var el = document.getElementById('keywordDensity');
  if(!el) return;
  el.innerHTML = '<div style="font-size:11px;font-weight:600;margin-bottom:6px;">Top Keywords</div>' +
    sorted.map(function(e) {
      var w=e[0],c=e[1],p=(c/total*100).toFixed(1);
      return '<div style="display:flex;align-items:center;gap:6px;font-size:10px;margin:2px 0;"><span style="width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'+w+'</span><div style="flex:1;height:6px;background:var(--surface);border-radius:3px;"><div style="width:'+Math.min(parseFloat(p)*10,100)+'%;height:100%;background:var(--accent);border-radius:3px;"></div></div><span style="width:40px;text-align:right;color:var(--muted);">'+c+' ('+p+'%)</span></div>';
    }).join('');
}

// ── Toast ──
function showToast(msg, type) {
  type = type||'info';
  var colors = {success:'#00cc88',error:'#ef4444',warning:'#fbbf24',info:'#3b82f6'};
  var t = document.createElement('div');
  t.className = 'toast-notification';
  t.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;background:var(--card);border:1px solid '+colors[type]+';border-left:3px solid '+colors[type]+';color:var(--text);font-size:12px;z-index:10000;animation:slideIn 0.3s ease;max-width:400px;font-family:Inter,sans-serif;';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function(){t.style.animation='slideOut 0.3s ease';setTimeout(function(){t.remove();},300);},3000);
}

// ── Init ──
(function(){
  var s = document.createElement('style');
  s.textContent = '.skel-line{height:12px;background:var(--surface);border-radius:4px;margin:8px 0;animation:shimmer 1.5s infinite;}@keyframes shimmer{0%{opacity:.5;}50%{opacity:1;}100%{opacity:.5;}}.skeleton-wrap{padding:16px;}.ctx-menu{position:absolute;background:var(--card);border:1px solid var(--border);border-radius:4px;z-index:9999;min-width:160px;box-shadow:0 4px 12px rgba(0,0,0,.15);}.ctx-item{padding:8px 14px;font-size:12px;cursor:pointer;transition:background .15s;}.ctx-item:hover{background:var(--surface);}.bc-link{color:var(--accent);cursor:pointer;font-size:11px;}.bc-current{color:var(--text);font-size:11px;font-weight:600;}.bc-sep{color:var(--muted);margin:0 4px;font-size:11px;}@keyframes slideIn{from{transform:translateX(100%);opacity:0;}to{transform:translateX(0);opacity:1;}}@keyframes slideOut{from{transform:translateX(0);opacity:1;}to{transform:translateX(100%);opacity:0;}}.strategy-btn{padding:6px 12px;font-size:11px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;border-radius:4px;transition:all .15s;}.strategy-btn.active{background:var(--accent);border-color:var(--accent);color:#fff;}';
  document.head.appendChild(s);
  var _orig = window.updateWordCount;
  window.updateWordCount = function(){if(_orig)_orig();updateExtendedStats();hideEmptyState();};
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',initAddons);
  else initAddons();
})();

function initAddons() {
  initContextMenu(); startAutoSave(); loadDraft(); loadContext(); showEmptyState();
  updateBreadcrumb(['Home']);
  var wc = document.querySelector('[id*="wordCount"],[class*="word-count"]');
  if(wc && !document.getElementById('extendedStats')) {
    var ext = document.createElement('div'); ext.id='extendedStats';
    ext.style.cssText='margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:4px;';
    wc.parentElement.appendChild(ext);
  }
  if(!document.getElementById('breadcrumb')) {
    var bc = document.createElement('div'); bc.id='breadcrumb';
    bc.style.cssText='padding:4px 12px;font-size:11px;border-bottom:1px solid var(--border);';
    var main = document.querySelector('main,.main,#app,body > div:first-child');
    if(main) main.insertBefore(bc, main.firstChild);
  }
  checkModelStatus(); setInterval(checkModelStatus, 60000);
  var ss = localStorage.getItem('humanizer_strategy');
  if(ss) setStrategy(ss);
  try{_readabilityHistory=JSON.parse(localStorage.getItem('humanizer_readability')||'[]');}catch(e){}
  updateReadabilityChart();
}

</script>
</body>
</html>"""


# ─── Threaded HTTP Server ─────────────────────────────────────────────

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

# Feature 10: Progressive results
import threading
import uuid

JOBS = {}  # {job_id: {status, progress, chunks_done, chunks_total, partial, result, error, time, ...}}
JOBS_LOCK = threading.Lock()



# ═══════════════════════════════════════════════════════════════
# BACKEND ADDON: Preprocessing, Citation Protection, Strategies
# ═══════════════════════════════════════════════════════════════

# ── #8: Citation Protection ──
_CITATION_PATTERNS = [
    r'\[\d+(?:,\s*\d+)*\]',
    r'\([A-Z][a-z]+,?\s*\d{4}\)',
    r'\([A-Z][a-z]+\s+et\s+al\.,?\s*\d{4}\)',
    r'@\w+',
]

def preserve_citations(text):
    placeholders = {}
    counter = [0]
    def repl(match):
        key = f"__CITE_{counter[0]}__"
        placeholders[key] = match.group(0)
        counter[0] += 1
        return key
    protected = text
    for pattern in _CITATION_PATTERNS:
        protected = re.sub(pattern, repl, protected, flags=re.IGNORECASE)
    return protected, placeholders

def restore_citations(text, placeholders):
    for key, original in placeholders.items():
        text = text.replace(key, original)
    return text

# ── #9: Code/Table/Math Protection ──
_SPECIAL_BLOCK_PATTERNS = [
    (r'```[\s\S]*?```', 'CODE'),
    (r'\|[\s\S]*?\|(?:\n\|[\s\S]*?\|)+', 'TABLE'),
    (r'\$\$[\s\S]*?\$\$', 'MATH_DISPLAY'),
    (r'\$[^\$\n]+\$', 'MATH_INLINE'),
    (r'\\begin\{.*?\}[\s\S]*?\\end\{.*?\}', 'LATEX'),
]

def protect_special_blocks(text):
    placeholders = {}
    counter = [0]
    protected = text
    for pattern, btype in _SPECIAL_BLOCK_PATTERNS:
        def repl(match, b=btype, c=counter, p=placeholders):
            key = f"__{b}_{c[0]}__"
            p[key] = match.group(0)
            c[0] += 1
            return key
        protected = re.sub(pattern, repl, protected)
    return protected, placeholders

def restore_special_blocks(text, placeholders):
    for key, original in placeholders.items():
        text = text.replace(key, original)
    return text

# ── #5: Grammar Auto-fix ──
_GRAMMAR_FIXES = [
    (r'  +', ' '),
    (r'\s+([.,!?])', r'\1'),
    (r'([.!?])([A-Z])', r'\1 \2'),
    (r'\bi\b', 'I'),
    (r'\bteh\b', 'the'),
    (r'\brecieve', 'receive'),
    (r'\bseperate', 'separate'),
    (r'\boccured\b', 'occurred'),
    (r'\buntill\b', 'until'),
    (r'\balot\b', 'a lot'),
    (r'\bwierd\b', 'weird'),
    (r'\bdefinately\b', 'definitely'),
    (r'\baccomodate\b', 'accommodate'),
    (r'\boccurance\b', 'occurrence'),
]

def auto_fix_grammar(text):
    result = text
    for pattern, replacement in _GRAMMAR_FIXES:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result

# ── #7: Humanization Intensity ──
INTENSITY_PROMPTS = {
    1: "Make very minor adjustments. Keep original sentence structure almost entirely. Only replace a few AI-sounding phrases.",
    2: "Make light adjustments. Slightly vary word choices and sentence openings while preserving most original structure.",
    3: "Apply moderate rewriting. Vary sentence lengths, replace common phrases, adjust some structures while keeping meaning.",
    4: "Apply strong rewriting. Significantly restructure sentences, use varied vocabulary, change paragraph flow while maintaining core meaning.",
    5: "Do a heavy rewrite. Completely restructure sentences, use creative vocabulary, vary rhythm dramatically, reorder ideas.",
}

# ── #15: Rewriting Strategies ──
STRATEGY_PROMPTS = {
    "academic": "Maintain formal academic tone. Use discipline-specific terminology, passive voice where appropriate, precise language. Keep citations intact.",
    "creative": "Use vivid, engaging language. Add metaphors, varied sentence rhythm, creative transitions. Make it feel like a skilled human writer.",
    "technical": "Simplify jargon where possible. Use clear, precise technical language. Add brief explanations for complex terms. Keep code/formulas intact.",
    "casual": "Write conversationally. Use contractions, shorter sentences, relatable examples. Like explaining to a friend.",
}

# ── #17: Synonym Intelligence ──
_AI_PHRASE_REPLACEMENTS = {
    "it is important to note that": ["notably,", "worth noting:", "keep in mind:"],
    "in today's rapidly evolving": ["in today's fast-changing", "in the current", "as"],
    "delve into": ["explore", "examine", "look at", "dig into"],
    "it's worth noting": ["notably,", "interestingly,", ""],
    "in conclusion": ["to wrap up,", "overall,", "finally,"],
    "furthermore": ["also,", "plus,", "additionally,"],
    "however": ["but", "though", "that said,"],
    "moreover": ["also,", "what's more,"],
    "nevertheless": ["still,", "even so,"],
    "utilize": ["use", "apply", "employ"],
    "facilitate": ["help", "enable", "support"],
    "implement": ["set up", "put in place", "start"],
    "leverage": ["use", "take advantage of", "build on"],
    "comprehensive": ["thorough", "complete", "full"],
    "cutting-edge": ["latest", "modern", "advanced"],
    "innovative": ["new", "creative", "fresh"],
    "seamless": ["smooth", "easy", "effortless"],
    "robust": ["strong", "solid", "reliable"],
}

def replace_ai_phrases(text):
    result = text
    for phrase, alternatives in _AI_PHRASE_REPLACEMENTS.items():
        if phrase.lower() in result.lower():
            alt = random.choice([a for a in alternatives if a])
            result = re.sub(re.escape(phrase), alt, result, flags=re.IGNORECASE)
    return result

# ── #18: Sentence Splitting/Combining ──
def vary_sentence_lengths(text):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    i = 0
    while i < len(sentences):
        s = sentences[i]
        words = s.split()
        if len(words) > 30 and ',' in s:
            parts = s.split(',', 1)
            if len(parts) == 2 and len(parts[0].split()) > 5:
                result.append(parts[0].strip() + '.')
                result.append(parts[1].strip().capitalize() if parts[1].strip() else '')
                i += 1
                continue
        if len(words) < 8 and i + 1 < len(sentences) and len(sentences[i+1].split()) < 8:
            combined = s.rstrip('.') + ', and ' + sentences[i+1][0].lower() + sentences[i+1][1:]
            result.append(combined)
            i += 2
            continue
        result.append(s)
        i += 1
    return ' '.join(result)

# ── #16: Paragraph Reordering ──
def reorder_within_paragraphs(text):
    paragraphs = text.split('\n\n')
    result = []
    for para in paragraphs:
        sentences = re.split(r'(?<=[.!?])\s+', para.strip())
        if len(sentences) > 4:
            mid = sentences[1:-1]
            random.shuffle(mid)
            sentences = [sentences[0]] + mid + [sentences[-1]]
        result.append(' '.join(sentences))
    return '\n\n'.join(result)

# ── #19: Voice Consistency ──
def check_voice_consistency(text):
    formal = ['therefore', 'furthermore', 'consequently', 'hence', 'thus', 'moreover']
    casual = ["don't", "can't", "won't", "it's", "that's", "gonna", "wanna", "kinda"]
    fc = sum(1 for m in formal if m in text.lower())
    cc = sum(1 for m in casual if m in text.lower())
    if fc > 2 and cc > 2:
        return {"consistent": False, "formal": fc, "casual": cc, "message": "Mixed voice"}
    return {"consistent": True, "formal": fc, "casual": cc, "message": "Consistent voice"}

# ── #13: Semantic Similarity ──
def calc_semantic_similarity(text1, text2):
    w1 = set(re.findall(r'\b\w+\b', text1.lower()))
    w2 = set(re.findall(r'\b\w+\b', text2.lower()))
    if not w1 or not w2:
        return 0
    overlap = len(w1 & w2)
    union = len(w1 | w2)
    return round(overlap / union * 100, 1) if union > 0 else 0

# ── #25: Citation Formatter ──
def format_citations(text, style="apa"):
    if style == "apa":
        text = re.sub(r'\(([A-Z][a-z]+),\s*(\d{4})\)', r'(\1, \2)', text)
    elif style == "mla":
        text = re.sub(r'\(([A-Z][a-z]+),?\s*(\d{4})\)', r'(\1 \2)', text)
    elif style == "chicago":
        text = re.sub(r'\(([A-Z][a-z]+),?\s*(\d{4})\)', r'(\1, \2)', text)
    return text

# ── #79: Retry Failed Chunks ──
MAX_CHUNK_RETRIES = 2

# ── #137: Model Fallback ──
MODEL_FALLBACK = [
    "cx/gpt-5.5",
    "ag/claude-sonnet-4-6",
    "ag/gemini-3-flash",
    "ag/gpt-oss-120b-medium",
    "cx/gpt-5.4",
]

# ── Model Status ──
MODEL_LATENCY = {}

def update_model_latency(model, latency_ms, ok=True):
    MODEL_LATENCY[model] = {"ok": ok, "latency_ms": round(latency_ms), "last_check": time.time()}



class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/history":
            self._json_response(HISTORY)
        elif self.path == "/api/versions":
            versions_summary = [{"id": v["id"], "timestamp": v["timestamp"], 
                               "input_words": v["input_words"], "output_words": v["output_words"],
                               "score": v["score"], "model": v["model"], "tone": v["tone"]}
                              for v in VERSIONS]
            self._json_response(versions_summary)
        elif self.path.startswith("/api/version/"):
            vid = int(self.path.split("/")[-1])
            version = next((v for v in VERSIONS if v["id"] == vid), None)
            if version:
                self._json_response(version)
            else:
                self._json_response({"error": "Version not found"}, 404)
        elif self.path == "/api/stats":
            self._json_response(STATS)
        elif self.path == "/api/model-status":
            self._json_response(MODEL_LATENCY if MODEL_LATENCY else {m: {"ok": True, "latency_ms": 0, "last_check": 0} for m in list(MODEL_OPTIONS.keys())[:5]})
        elif self.path == "/api/debug-cache":
            self._json_response({
                "cache_size": len(_LLM_CACHE),
                "cache_hits": _LLM_CACHE_HITS,
                "cache_misses": _LLM_CACHE_MISSES,
                "cache_keys": list(_LLM_CACHE.keys())[:5]
            })
        elif self.path.startswith("/api/progress/"):
            job_id = self.path.split("/api/progress/")[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job:
                # Add time estimation to progress response
                if job["status"] == "processing":
                    elapsed = time.time() - job.get("start_time", time.time())
                    model = job.get("model", LLM_MODEL)
                    time_est = estimate_time_remaining(
                        job.get("input_words", 0),
                        job.get("chunks_total", 1),
                        job.get("chunks_done", 0),
                        elapsed,
                        model=model,
                    )
                    job["time_estimate"] = time_est
                    job["time_remaining_text"] = format_time_remaining(time_est["remaining_seconds"])
                self._json_response(job)
            else:
                self._json_response({"error": "Job not found"}, 404)
        elif self.path == "/api/keys/list":
            self._json_response(list_api_keys())
        elif self.path == "/api/webhooks/list":
            self._json_response(list_webhooks())
        elif self.path == "/api/style/profiles":
            profiles = [{"id": p["id"], "stats": p["stats"], "created": p["created"]} for p in _STYLE_PROFILES.values()]
            self._json_response(profiles)
        elif self.path == "/api/cache/stats":
            self._json_response({
                "full_text_cache_size": len(_FULL_TEXT_CACHE),
                "llm_cache_size": len(_LLM_CACHE),
                "llm_cache_hits": _LLM_CACHE_HITS,
                "llm_cache_misses": _LLM_CACHE_MISSES,
            })
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))

    def do_POST(self):
        if self.path == "/api/humanize":
            self._handle_humanize_async()
        elif self.path == "/api/analyze":
            self._handle_analyze()
        elif self.path == "/api/upload":
            self._handle_upload()
        elif self.path == "/api/download":
            self._handle_download()
        elif self.path == "/api/download/txt":
            self._handle_download_txt()
        elif self.path == "/api/download/md":
            self._handle_download_md()
        elif self.path == "/api/voice-check":
            self._handle_voice_check()
        elif self.path == "/api/similarity":
            self._handle_similarity()
        elif self.path == "/api/citation-format":
            self._handle_citation_format()
        elif self.path == "/api/grammar-fix":
            self._handle_grammar_fix()
        elif self.path == "/api/keywords":
            self._handle_keywords()
        elif self.path == "/api/batch":
            self._handle_batch()
        elif self.path == "/api/preview":
            self._handle_preview()
        elif self.path == "/api/custom-lists":
            self._handle_custom_lists()
        elif self.path == "/api/external-check":
            self._handle_external_check()
        elif self.path == "/api/readability":
            self._handle_readability()
        elif self.path == "/api/grammar":
            self._handle_grammar()
        elif self.path == "/api/variants":
            self._handle_variants()
        elif self.path == "/api/tone-slider":
            self._handle_tone_slider()
        elif self.path == "/api/style-train":
            self._handle_style_train()
        elif self.path == "/api/docs":
            self._handle_api_docs()
        elif self.path == "/api/webhook/register":
            self._handle_webhook_register()
        elif self.path == "/api/webhook/delete":
            self._handle_webhook_delete()
        elif self.path == "/api/style/train":
            self._handle_style_train()
        elif self.path == "/api/style/analyze":
            self._handle_style_analyze()
        elif self.path == "/api/keys/generate":
            self._handle_key_generate()
        elif self.path == "/api/keys/revoke":
            self._handle_key_revoke()
        elif self.path == "/api/webhooks/register":
            self._handle_webhook_register()
        elif self.path == "/api/webhooks/delete":
            self._handle_webhook_delete()
        elif self.path == "/v1/humanize":
            self._handle_dev_api()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_humanize_async(self):
        """Start humanization in background thread, return job_id immediately."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            passes = body.get("passes", 3)
            model = body.get("model", None)
            tone = body.get("tone", "casual")
            domain = body.get("domain", "general")
            ref_sample = body.get("ref_sample", "")
            preserve = body.get("preserve", "")
            avoid = body.get("avoid", "")
            auto_retry = body.get("autoRetry", False)
            strict_wc = body.get("strictWordCount", False)
            if preserve or avoid:
                load_custom_lists(preserve, avoid)
                import sys
                msg = f"[HANDLER] Loaded: preserve={len(CUSTOM_PRESERVE)}, avoid={len(CUSTOM_AVOID)}, set={CUSTOM_AVOID}"
                sys.stderr.write(msg + chr(10))
                sys.stderr.flush()
                with open("debug.log", "a") as df:
                    df.write(msg + chr(10))

            if not text:
                self._json_response({"error": "No text provided"}, 400)
                return

            # Check full-text cache first
            text_hash = make_text_hash(text, passes, model, tone)
            cached = fulltext_cache_get(text_hash)
            if cached:
                self._json_response({
                    "cached": True,
                    "result": cached["text"],
                    "output_words": cached["words"],
                    "score": cached["score"],
                    "time": 0,
                    "job_id": "cached",
                })
                return

            job_id = str(uuid.uuid4())[:8]
            input_words = len(text.split())
            chunks = split_into_chunks(text, max_words=CHUNK_SIZE)
            total_chunks = len(chunks) if input_words > 300 else 1

            with JOBS_LOCK:
                JOBS[job_id] = {
                    "status": "processing",
                    "progress": 0,
                    "chunks_done": 0,
                    "chunks_total": total_chunks,
                    "partial": "",
                    "result": None,
                    "error": None,
                    "time": None,
                    "input_words": input_words,
                    "output_words": 0,
                    "input_score": calc_detection_score(text),
                    "output_score": None,
                    "start_time": time.time(),
                    "model": model or LLM_MODEL,
                }

            # Start background thread
            thread = threading.Thread(
                target=self._run_humanize_job,
                args=(job_id, text, passes, model, tone, domain, ref_sample, auto_retry, strict_wc),
                daemon=True,
            )
            thread.start()

            self._json_response({"job_id": job_id, "chunks": total_chunks, "input_words": input_words})

        except Exception as e:
            import traceback
            print(f"[ERROR] {traceback.format_exc()}", flush=True)
            self._json_response({"error": str(e)}, 500)

    def _run_humanize_job(self, job_id, text, passes, model, tone, domain="general", ref_sample="", auto_retry=False, strict_wc=False):
        """Run full humanization in background, updating JOBS dict progressively."""
        t0 = time.time()
        # Preprocessing pipeline
        text = auto_fix_grammar(text)  # #5: grammar fix
        text, cite_placeholders = preserve_citations(text)  # #8: protect citations
        text, block_placeholders = protect_special_blocks(text)  # #9: protect code/tables/math
        input_words = len(text.split())
        model_label = model or LLM_MODEL
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Job {job_id}: {input_words} words, {passes} passes, model={model_label}, tone={tone}", flush=True)

        try:
            if input_words <= 300:
                # Short text: single chunk
                result = humanize_chunk(text, passes, model or LLM_MODEL, tone)
                result = advanced_post_process(result, tone=tone)
                result = paragraph_vary(result)
                # Final: apply custom avoid + restore preserve AFTER all post-processing
                result = apply_custom_avoid(result)
                result = restore_custom_preserve(result)
                # Postprocessing pipeline
                result = replace_ai_phrases(result)  # #17: synonym intelligence
                result = vary_sentence_lengths(result)  # #18: sentence variation
                result = restore_citations(result, cite_placeholders)  # #8: restore citations
                result = restore_special_blocks(result, block_placeholders)  # #9: restore blocks
                elapsed = round(time.time() - t0, 1)
                output_score = calc_detection_score(result)
                similarity = calc_semantic_similarity(text, result)  # #13: semantic similarity

                # Strict word count enforcement (±5%)
                # strict_wc is a parameter
                if strict_wc and input_words > 0:
                    out_words = len(result.split())
                    ratio = out_words / input_words
                    if ratio < 0.95 or ratio > 1.05:
                        # Re-process problematic chunks
                        result = humanize_chunk(result, 1, model, tone)
                
                with JOBS_LOCK:
                
                    JOBS[job_id].update({
                        
                        "status": "done",
                        "progress": 100,
                        "chunks_done": 1,
                        "partial": result,
                        "result": result,
                        "time": elapsed,
                        "output_words": len(result.split()),
                        "output_score": output_score,
                    })

                # Save to history, versions, and stats (same as long text path)
                save_history({
                    "id": len(HISTORY) + 1,
                    "timestamp": datetime.now().isoformat(),
                    "input_words": input_words,
                    "output_words": len(result.split()),
                    "score_before": JOBS[job_id]["input_score"]["score"],
                    "score_after": output_score["score"],
                    "tone": tone,
                    "model": model_label,
                    "preview": result[:100],
                })
                save_version({
                    "id": len(VERSIONS) + 1,
                    "timestamp": datetime.now().isoformat(),
                    "input": text[:500],
                    "output": result,
                    "input_words": input_words,
                    "output_words": len(result.split()),
                    "score": output_score["score"],
                    "score_before": JOBS[job_id]["input_score"]["score"],
                    "score_after": output_score["score"],
                    "tone": tone,
                    "model": model_label,
                    "time": elapsed,
                })
                update_stats({
                    "input_words": input_words,
                    "output_words": len(result.split()),
                    "time": elapsed,
                    "model": model_label,
                })
                return

            # Long text: parallel chunks
            chunks = split_into_chunks(text, max_words=CHUNK_SIZE)
            total_chunks = len(chunks)
            processed_chunks = [None] * total_chunks
            completed_count = 0

            print(f"[{job_id}] Split into {total_chunks} chunks (parallel={PARALLEL_CHUNKS})", flush=True)

            def progress_callback(done, total, status):
                nonlocal completed_count
                completed_count = done
                pct = round(done / total * 90)
                # Build partial text from completed chunks (in order)
                partial_parts = []
                for i in range(total_chunks):
                    if processed_chunks[i] is not None:
                        partial_parts.append(processed_chunks[i])
                partial = ' '.join(partial_parts) if partial_parts else ""

                with JOBS_LOCK:
                    JOBS[job_id].update({
                        "progress": pct,
                        "chunks_done": done,
                        "partial": partial,
                    })

            with ThreadPoolExecutor(max_workers=min(PARALLEL_CHUNKS, total_chunks)) as executor:
                work_items = [(i, chunk, passes, model or LLM_MODEL, tone) for i, chunk in enumerate(chunks)]
                futures = {executor.submit(_process_chunk_worker, item): item[0] for item in work_items}

                for future in as_completed(futures):
                    try:
                        idx, processed = future.result(timeout=600)
                        processed_chunks[idx] = processed
                        progress_callback(completed_count + 1, total_chunks, "processing")
                    except Exception as e:
                        idx = futures[future]
                        print(f"[{job_id}] Chunk {idx+1} FAILED: {e}", flush=True)
                        processed_chunks[idx] = advanced_post_process(chunks[idx], tone=tone)
                        progress_callback(completed_count + 1, total_chunks, "processing")

            # Fill None entries
            for i in range(total_chunks):
                if processed_chunks[i] is None:
                    processed_chunks[i] = advanced_post_process(chunks[i], tone=tone)

            # Postprocessing pipeline (long text)
            if processed_chunks:
                joined = ' '.join([c for c in processed_chunks if c])
                joined = replace_ai_phrases(joined)
                joined = vary_sentence_lengths(joined)
                joined = restore_citations(joined, cite_placeholders)
                joined = restore_special_blocks(joined, block_placeholders)
                processed_chunks = [joined]

            # Apply domain-specific word replacement
            if domain != 'general':
                result_so_far = ' '.join([c for c in processed_chunks if c])
                result_so_far = domain_word_replace(result_so_far, domain)

            # Apply reference style matching
            if ref_sample and len(ref_sample.strip()) > 50:
                ref_avg_len = sum(len(s.split()) for s in re.split(r'[.!?]+', ref_sample) if len(s.split()) > 2) / max(len(re.split(r'[.!?]+', ref_sample)), 1)
                print(f"[{job_id}] Reference style: avg sentence length = {ref_avg_len:.1f} words", flush=True)

            # Smooth transitions
            processed_chunks = deduplicate_overlaps(processed_chunks)
            result = smooth_transitions(processed_chunks, tone=tone)
            if tone != "academic":
                result = ultra_short_inject(result)
                result = rhetorical_inject(result)
            else:
                result = _strip_casual_phrases(result)
            result = paragraph_vary(result)
            result = re.sub(r'  +', ' ', result)
            result = re.sub(r'\.\s*\.', '.', result)

            # Auto-retry worst chunks
            if AUTO_RETRY:
                score = calc_detection_score(result)
                if score['score'] > 40:
                    print(f"[{job_id}] Score {score['score']} > 40, retrying...", flush=True)
                    retry_indices = [0, len(chunks)-1] if len(chunks) > 1 else [0]
                    for idx in retry_indices:
                        processed = humanize_chunk(chunks[idx], passes, model or LLM_MODEL, tone)
                        processed = advanced_post_process(processed, tone=tone)
                        processed_chunks[idx] = processed
                    processed_chunks = deduplicate_overlaps(processed_chunks)
                    result = smooth_transitions(processed_chunks, tone=tone)
                    if tone != "academic":
                        result = ultra_short_inject(result)
                    else:
                        result = _strip_casual_phrases(result)
                    result = paragraph_vary(result)
                    result = re.sub(r'  +', ' ', result)
                    result = re.sub(r'\.\s*\.', '.', result)

            # Final: apply custom avoid + restore preserve
            result = apply_custom_avoid(result)
            result = restore_custom_preserve(result)

            elapsed = round(time.time() - t0, 1)
            output_score = calc_detection_score(result)

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Job {job_id} DONE: {input_words} -> {len(result.split())} words | Score: {JOBS[job_id]['input_score']['score']} -> {output_score['score']} ({elapsed}s)", flush=True)

            # Save to history
            save_history({
                "id": len(HISTORY) + 1,
                "timestamp": datetime.now().isoformat(),
                "input_words": input_words,
                "output_words": len(result.split()),
                "score_before": JOBS[job_id]["input_score"]["score"],
                "score_after": output_score["score"],
                "grade_after": output_score["grade"],
                "preview": text[:120] + "...",
                "tone": tone,
            })

            # Save version for undo
            save_version({
                "id": len(VERSIONS) + 1,
                "timestamp": datetime.now().isoformat(),
                "input_words": input_words,
                "output_words": len(result.split()),
                "input_text": text[:500],
                "output_text": result,
                "score": output_score["score"],
                "model": model_label,
                "tone": tone,
            })

            # Update processing stats
            update_stats({
                "input_words": input_words,
                "output_words": len(result.split()),
                "time": elapsed,
                "model": model_label,
                "status": "done",
            })

            with JOBS_LOCK:
                JOBS[job_id].update({
                    "status": "done",
                    "progress": 100,
                    "chunks_done": total_chunks,
                    "partial": result,
                    "result": result,
                    "time": elapsed,
                    "output_words": len(result.split()),
                    "output_score": output_score,
                })

            # Cache result for future identical requests
            text_hash = make_text_hash(text, passes, model, tone)
            fulltext_cache_set(text_hash, {"text": result, "score": output_score, "words": len(result.split()), "time": elapsed})

            # Send webhook notification
            send_webhook("job_complete", {
                "job_id": job_id,
                "input_words": input_words,
                "output_words": len(result.split()),
                "score": output_score["score"],
                "grade": output_score["grade"],
                "time": elapsed,
            })

        except Exception as e:
            import traceback
            print(f"[{job_id}] ERROR: {traceback.format_exc()}", flush=True)
            with JOBS_LOCK:
                JOBS[job_id].update({
                    "status": "error",
                    "error": str(e),
                    "time": round(time.time() - t0, 1),
                })

    def _handle_analyze(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")

            if not text:
                self._json_response({"error": "No text provided"}, 400)
                return

            score = calc_detection_score(text)
            self._json_response({"score": score, "words": len(text.split())})

        except Exception as e:
            self._json_response({"error": str(e)}, 500)


    def _handle_voice_check(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            result = check_voice_consistency(text)
            self._json_response(result)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_similarity(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text1 = body.get("text1", "")
            text2 = body.get("text2", "")
            score = calc_semantic_similarity(text1, text2)
            self._json_response({"similarity": score})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_citation_format(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            style = body.get("style", "apa")
            result = format_citations(text, style)
            self._json_response({"text": result})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_grammar_fix(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            result = auto_fix_grammar(text)
            self._json_response({"text": result, "changes": len(text) != len(result)})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_keywords(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "").lower()
            words = re.findall(r'\b[a-z]{4,}\b', text)
            stop = {'this','that','with','from','have','been','were','will','would','could','should','their','there','they','them','what','when','where','which','about','after','before','between','through','during','each','other','some','such','only','than','into','over','also','just','very','much','more','most','these','those','then','because','while','although','however','therefore','furthermore','moreover','nevertheless'}
            freq = {}
            for w in words:
                if w not in stop and len(w) > 3:
                    freq[w] = freq.get(w, 0) + 1
            total = len(words)
            top = sorted(freq.items(), key=lambda x: -x[1])[:20]
            self._json_response({"keywords": [{"word": w, "count": c, "pct": round(c/total*100, 1)} for w, c in top], "total_words": total})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_upload(self):
        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)

            if "multipart" in content_type:
                # Parse multipart form data
                environ = {
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": str(length),
                }
                form = cgi.FieldStorage(
                    fp=io.BytesIO(raw),
                    environ=environ,
                    keep_blank_values=True,
                )
                file_data = form["file"].file.read() if "file" in form else b""
                filename = form["file"].filename if "file" in form else ""
            else:
                # Raw body
                file_data = raw
                filename = "upload.docx"

            if not file_data:
                self._json_response({"error": "No file data"}, 400)
                return

            if filename.endswith(".txt"):
                text = file_data.decode("utf-8", errors="replace")
            else:
                text = extract_docx_text(file_data)

            self._json_response({"text": text, "words": len(text.split()), "filename": filename})

        except Exception as e:
            print(f"[UPLOAD ERROR] {e}", flush=True)
            self._json_response({"error": str(e)}, 500)

    def _handle_download(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")

            if not text:
                self._json_response({"error": "No text provided"}, 400)
                return

            docx_bytes = make_docx(text)

            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", 'attachment; filename="humanized.docx"')
            self.send_header("Content-Length", str(len(docx_bytes)))
            self.end_headers()
            self.wfile.write(docx_bytes)

        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_download_txt(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            encoded = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="humanized.txt"')
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_download_md(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            md = f"# Humanized Text\n\n{text}"
            encoded = md.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="humanized.md"')
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_batch(self):
        """Accept multiple texts, process all, return results."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            texts = body.get("texts", [])
            model = body.get("model", LLM_MODEL)
            tone = body.get("tone", "casual")
            
            if not texts:
                self._json_response({"error": "No texts provided"}, 400)
                return
            
            job_id = str(uuid.uuid4())[:8]
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "status": "processing",
                    "progress": 0,
                    "chunks_done": 0,
                    "chunks_total": len(texts),
                    "partial": "",
                    "result": None,
                    "batch_results": [],
                    "error": None,
                    "time": None,
                    "input_words": sum(len(t.split()) for t in texts),
                    "output_words": 0,
                    "input_score": {},
                    "output_score": {},
                }
            
            thread = threading.Thread(
                target=self._run_batch_job,
                args=(job_id, texts, model, tone),
                daemon=True,
            )
            thread.start()
            self._json_response({"job_id": job_id, "batch_size": len(texts)})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _run_batch_job(self, job_id, texts, model, tone):
        t0 = time.time()
        results = []
        total_words = 0
        for i, text in enumerate(texts):
            try:
                result = humanize_chunk(text, 3, model, tone)
                result = advanced_post_process(result, tone=tone)
                results.append({"index": i, "status": "done", "text": result, 
                              "input_words": len(text.split()), "output_words": len(result.split())})
                total_words += len(result.split())
            except Exception as e:
                results.append({"index": i, "status": "error", "error": str(e), "text": text})
                total_words += len(text.split())
            
            with JOBS_LOCK:
                JOBS[job_id].update({
                    "progress": round((i+1)/len(texts)*100),
                    "chunks_done": i+1,
                })
        
        elapsed = round(time.time() - t0, 1)
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "done",
                "progress": 100,
                "chunks_done": len(texts),
                "batch_results": results,
                "results": results,
                "result": json.dumps(results),
                "time": elapsed,
                "output_words": total_words,
            })

    def _handle_preview(self):
        """Process first 10% of text for preview before full processing."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            model = body.get("model", LLM_MODEL)
            tone = body.get("tone", "casual")
            
            if not text:
                self._json_response({"error": "No text"}, 400)
                return
            
            words = text.split()
            preview_words = max(50, len(words) // 10)
            preview_text = ' '.join(words[:preview_words])
            
            t0 = time.time()
            result = humanize_chunk(preview_text, 3, model, tone)
            result = advanced_post_process(result, tone=tone)
            elapsed = round(time.time() - t0, 1)
            
            in_score = calc_detection_score(preview_text)
            out_score = calc_detection_score(result)
            
            # Auto-retry if score still high
            auto_retry = body.get('autoRetry', False)
            retry_count = 0
            while auto_retry and out_score.get('score', 100) > 40 and retry_count < 2:
                retry_count += 1
                result = humanize_chunk(result, passes, model, tone)
                out_score = calc_detection_score(result)
            
            self._json_response({
                "preview_input": preview_text,
                "preview_output": result,
                "input_words": len(preview_text.split()),
                "output_words": len(result.split()),
                "input_score": in_score,
                "output_score": out_score,
                "time": elapsed,
                "total_words": len(words),
                "estimated_time": round(elapsed * (len(words) / preview_words) / 4, 0),
            })
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_custom_lists(self):
        """Update custom preserve/avoid word lists."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            preserve = body.get("preserve", "")
            avoid = body.get("avoid", "")
            load_custom_lists(preserve, avoid)
            self._json_response({
                "preserve_count": len(CUSTOM_PRESERVE),
                "avoid_count": len(CUSTOM_AVOID),
                "preserve_sample": list(CUSTOM_PRESERVE)[:10],
                "avoid_sample": list(CUSTOM_AVOID)[:10],
            })
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_external_check(self):
        """Check text against ZeroGPT API with fallback to internal scoring."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")[:5000]  # max 5000 chars for API
            
            if not text:
                self._json_response({"error": "No text"}, 400)
                return
            
            # Try ZeroGPT API
            try:
                payload = json.dumps({"input_text": text}).encode()
                req = urllib.request.Request(
                    "https://api.zerogpt.com/api/detect/detectText",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Origin": "https://www.zerogpt.com",
                        "Referer": "https://www.zerogpt.com/",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
            except Exception as e:
                # Fallback to internal scoring if ZeroGPT fails
                score = calc_detection_score(text)
                self._json_response({
                    "ai_percentage": score["score"],
                    "ai_sentences": 0,
                    "human_sentences": len(re.split(r'[.!?]+', text)),
                    "text_length": len(text),
                    "is_human": 1 if score["score"] < 50 else 0,
                    "source": "internal",
                    "error": f"ZeroGPT unavailable: {str(e)[:100]}"
                })
                return
            
            if data.get("success"):
                d = data.get("data", {})
                self._json_response({
                    "ai_percentage": d.get("fakePercentage", 0),
                    "ai_sentences": d.get("aiSentences", 0),
                    "human_sentences": d.get("humanSentences", 0),
                    "text_length": d.get("text_length", 0),
                    "is_human": d.get("isHuman", 0),
                    "source": "zerogpt"
                })
            else:
                # ZeroGPT returned error (e.g., 403, requires purchase)
                # Fall back to internal scoring
                score = calc_detection_score(text)
                self._json_response({
                    "ai_percentage": score["score"],
                    "ai_sentences": 0,
                    "human_sentences": len(re.split(r'[.!?]+', text)),
                    "text_length": len(text),
                    "is_human": 1 if score["score"] < 50 else 0,
                    "source": "internal",
                    "note": f"ZeroGPT unavailable: {data.get('message', 'API error')}"
                })
        except Exception as e:
            self._json_response({"error": str(e)[:200]}, 500)

    def _handle_readability(self):
        """Calculate Flesch-Kincaid readability metrics."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            fk = calc_flesch_kincaid(text)
            self._json_response(fk)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_grammar(self):
        """Check grammar using LanguageTool API."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")[:5000]
            result = check_grammar_languagetool(text)
            self._json_response(result)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_variants(self):
        """Generate 3 output variants, pick best."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            passes = body.get("passes", 3)
            model = body.get("model", None)
            tone = body.get("tone", "casual")
            num = body.get("num_variants", 3)
            if not text:
                self._json_response({"error": "No text"}, 400)
                return
            job_id = str(uuid.uuid4())[:8]
            input_words = len(text.split())
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "status": "processing",
                    "progress": 0,
                    "chunks_done": 0,
                    "chunks_total": num,
                    "partial": "",
                    "result": None,
                    "error": None,
                    "time": None,
                    "input_words": input_words,
                    "output_words": 0,
                    "input_score": calc_detection_score(text),
                    "output_score": None,
                    "start_time": time.time(),
                    "model": model or LLM_MODEL,
                    "type": "variants",
                }
            def run_variants():
                t0 = time.time()
                try:
                    result = humanize_variants(text, passes, model, tone, num)
                    best = result["best"]
                    elapsed = round(time.time() - t0, 1)
                    output_score = calc_detection_score(best["text"])
                    with JOBS_LOCK:
                        JOBS[job_id].update({
                            "status": "done",
                            "progress": 100,
                            "chunks_done": num,
                            "result": result,
                            "partial": best["text"],
                            "time": elapsed,
                            "output_words": best["words"],
                            "output_score": output_score,
                        })
                    send_webhook("job_complete", {"job_id": job_id, "score": best["score"], "words": best["words"]})
                except Exception as e:
                    with JOBS_LOCK:
                        JOBS[job_id].update({"status": "error", "error": str(e)})
            thread = threading.Thread(target=run_variants, daemon=True)
            thread.start()
            self._json_response({"job_id": job_id, "type": "variants", "num_variants": num})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_tone_slider(self):
        """Get tone settings from 1-10 slider value."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            level = body.get("level", 5)
            result = get_tone_from_slider(level)
            self._json_response(result)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_api_docs(self):
        """Developer API documentation endpoint."""
        docs = {
            "name": "HumanizeAI API v5",
            "version": "5.0",
            "base_url": "http://localhost:7860",
            "authentication": "API key required (X-API-Key header)",
            "endpoints": [
                {"method": "POST", "path": "/api/humanize", "params": {"text": "string", "model": "string", "tone": "string", "passes": "int"}, "returns": {"job_id": "string"}},
                {"method": "GET", "path": "/api/progress/{job_id}", "returns": {"status": "string", "progress": "int", "result": "string"}},
                {"method": "POST", "path": "/api/analyze", "params": {"text": "string"}, "returns": {"score": "int", "grade": "string"}},
                {"method": "POST", "path": "/api/preview", "params": {"text": "string"}, "returns": {"preview_output": "string"}},
                {"method": "POST", "path": "/api/variants", "params": {"text": "string", "num": "int"}, "returns": {"variants": [{"text": "string", "score": "int"}]}},
                {"method": "POST", "path": "/api/tone-slider", "params": {"text": "string", "level": "float 0-1"}, "returns": {"text": "string"}},
                {"method": "POST", "path": "/api/readability", "params": {"text": "string"}, "returns": {"grade": "float", "reading_ease": "float"}},
                {"method": "POST", "path": "/api/grammar", "params": {"text": "string"}, "returns": {"issues": [], "total": "int"}},
            ],
            "rate_limits": "100 requests/hour per API key",
        }
        self._json_response(docs)

    def _handle_style_train(self):
        """Train writing style from uploaded samples."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            samples = body.get("samples", [])
            if not samples or len(samples) < 1:
                self._json_response({"error": "Need at least 1 writing sample"}, 400)
                return
            profile = train_style(samples)
            if not profile:
                self._json_response({"error": "Could not analyze samples"}, 400)
                return
            self._json_response(profile)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_style_analyze(self):
        """Analyze writing style of given text."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            if not text:
                self._json_response({"error": "No text"}, 400)
                return
            stats = analyze_writing_style(text)
            self._json_response(stats or {"error": "Could not analyze"})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_key_generate(self):
        """Generate a new API key."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            name = body.get("name", "default")
            rate_limit = body.get("rate_limit", 100)
            result = generate_api_key(name, rate_limit)
            self._json_response(result)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_key_revoke(self):
        """Revoke an API key."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            key_hash = body.get("hash", "")
            if revoke_api_key(key_hash):
                self._json_response({"success": True})
            else:
                self._json_response({"error": "Key not found"}, 404)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_webhook_register(self):
        """Register a webhook URL."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            url = body.get("url", "")
            events = body.get("events", None)
            if not url:
                self._json_response({"error": "No URL provided"}, 400)
                return
            result = register_webhook(url, events)
            self._json_response(result)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_webhook_delete(self):
        """Delete a webhook."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            webhook_id = body.get("id", "")
            if delete_webhook(webhook_id):
                self._json_response({"success": True})
            else:
                self._json_response({"error": "Webhook not found"}, 404)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_dev_api(self):
        """Developer API endpoint with API key authentication."""
        try:
            # Check API key from Authorization header
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                raw_key = auth[7:]
            else:
                raw_key = auth
            valid, msg = validate_api_key(raw_key)
            if not valid:
                self._json_response({"error": msg}, 401)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            passes = body.get("passes", 3)
            model = body.get("model", None)
            tone = body.get("tone", "casual")
            if not text:
                self._json_response({"error": "No text provided"}, 400)
                return
            # Check cache first
            text_hash = make_text_hash(text, passes, model, tone)
            cached = fulltext_cache_get(text_hash)
            if cached:
                self._json_response({"result": cached["text"], "score": cached["score"], "cached": True, "words": cached["words"]})
                return
            # Process synchronously for API
            t0 = time.time()
            result = humanize(text, passes=passes, model=model, tone=tone)
            score = calc_detection_score(result)
            elapsed = round(time.time() - t0, 1)
            result_data = {"text": result, "score": score, "words": len(result.split()), "time": elapsed}
            fulltext_cache_set(text_hash, result_data)
            self._json_response({
                "result": result,
                "score": score["score"],
                "grade": score["grade"],
                "input_words": len(text.split()),
                "output_words": len(result.split()),
                "time": elapsed,
                "cached": False,
            })
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        pass


def run_server(port=7860):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"HumanizeAI v3 running at http://localhost:{port}", flush=True)
    print("Press Ctrl+C to stop", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
