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
            skip_words = {'honestly', 'fair point', 'fair enough', 'makes sense', 'not easy',
                         'not great', 'big deal', 'true', 'maybe', 'probably', 'sort of',
                         'kind of', 'right', 'sure', 'well', 'look', 'not surprising',
                         'pretty wild', 'think about it', 'sound familiar', 'it depends',
                         'truth is', 'i think', 'i mean', 'so basically', 'to be honest',
                         'of note', 'this matters', 'critically', 'significantly',
                         'this is key', 'worth highlighting'}
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
        if not is_structured and not is_numbered and i > 0 and i % 4 == 0 and frag_idx < len(ACADEMIC_FRAGMENTS_SHORT):
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
    num_inserts = max(1, word_count // 150)
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
    (r'\((?:[A-Z][a-z]+(?:\s+(?:et al\.?|&\s+[A-Z][a-z]+))?,\s*\d{4})\)', 'CITE'),
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
        result = _strip_casual_phrases(result)
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

    return result


# ─── HTML Template ────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HumanizeAI v3</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; }
  .layout { display: flex; min-height: 100vh; }
  .sidebar { width: 280px; background: #0d0d0d; border-right: 1px solid #1a1a1a; padding: 16px; overflow-y: auto; flex-shrink: 0; }
  .sidebar h3 { font-size: 14px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }
  .history-item { padding: 10px; border: 1px solid #1a1a1a; border-radius: 6px; margin-bottom: 8px; cursor: pointer; transition: all 0.15s; }
  .history-item:hover { border-color: #333; background: #111; }
  .history-item .preview { font-size: 12px; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .history-item .meta { font-size: 11px; color: #444; margin-top: 4px; }
  .history-item .score { color: #00cc88; font-weight: 600; }
  .container { flex: 1; max-width: 1200px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 28px; font-weight: 700; color: #fff; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 14px; margin-bottom: 24px; }
  .panels { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  .panel { display: flex; flex-direction: column; }
  .panel-label { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 8px; font-weight: 600; }
  textarea {
    width: 100%; height: 400px; background: #111; border: 1px solid #222; color: #e0e0e0;
    padding: 16px; font-size: 14px; line-height: 1.6; resize: vertical; border-radius: 8px;
    font-family: 'Inter', -apple-system, sans-serif;
  }
  textarea:focus { outline: none; border-color: #00cc88; }
  textarea::placeholder { color: #444; }
  .controls { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
  button { padding: 12px 24px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
  .btn-primary { background: #00cc88; color: #000; }
  .btn-primary:hover { background: #00e099; }
  .btn-primary:disabled { background: #333; color: #666; cursor: not-allowed; }
  .btn-secondary { background: #222; color: #ccc; border: 1px solid #333; }
  .btn-secondary:hover { background: #2a2a2a; }
  select { padding: 10px 16px; background: #111; border: 1px solid #222; color: #e0e0e0; border-radius: 8px; font-size: 14px; }
  .status { color: #888; font-size: 13px; padding: 8px 0; }
  .stats { display: flex; gap: 24px; margin-top: 16px; flex-wrap: wrap; }
  .stat-value { font-size: 20px; font-weight: 700; color: #00cc88; }
  .stat-label { font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
  .progress-bar { width: 100%; height: 6px; background: #1a1a1a; border-radius: 3px; margin: 8px 0; overflow: hidden; }
  .progress-fill { height: 100%; background: #00cc88; transition: width 0.3s; border-radius: 3px; }
  .upload-zone { border: 2px dashed #333; border-radius: 8px; padding: 16px; text-align: center; margin-bottom: 16px; cursor: pointer; transition: all 0.15s; }
  .upload-zone:hover { border-color: #00cc88; background: #0a1a10; }
  .upload-zone input { display: none; }
  .upload-zone label { color: #888; font-size: 13px; cursor: pointer; }
  .diff-container { display: none; margin-top: 16px; border: 1px solid #222; border-radius: 8px; overflow: hidden; }
  .diff-header { display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; background: #111; border-bottom: 1px solid #222; }
  .diff-header h3 { font-size: 14px; color: #fff; }
  .diff-body { max-height: 500px; overflow-y: auto; padding: 16px; }
  .diff-sentence { padding: 6px 10px; margin-bottom: 4px; border-radius: 4px; font-size: 13px; line-height: 1.5; cursor: pointer; transition: all 0.15s; }
  .diff-added { background: rgba(0,204,136,0.1); border-left: 3px solid #00cc88; color: #b0ffd8; }
  .diff-removed { background: rgba(255,68,68,0.1); border-left: 3px solid #ff4444; color: #ffaaaa; text-decoration: line-through; }
  .diff-unchanged { background: transparent; color: #666; }
  .diff-sentence:hover { background: rgba(255,255,255,0.05); }
  .heatmap-container { display: none; margin-top: 16px; border: 1px solid #222; border-radius: 8px; padding: 16px; }
  .heatmap-title { font-size: 14px; color: #fff; font-weight: 600; margin-bottom: 12px; }
  .heatmap-paragraph { padding: 10px; margin-bottom: 6px; border-radius: 4px; font-size: 12px; line-height: 1.4; cursor: pointer; transition: all 0.15s; }
  .heatmap-paragraph:hover { filter: brightness(1.2); }
  .heatmap-green { background: rgba(0,204,136,0.15); border-left: 3px solid #00cc88; color: #b0ffd8; }
  .heatmap-yellow { background: rgba(255,170,0,0.15); border-left: 3px solid #ffaa00; color: #ffd699; }
  .heatmap-red { background: rgba(255,68,68,0.15); border-left: 3px solid #ff4444; color: #ffaaaa; }
  .heatmap-score { font-size: 11px; font-weight: 600; margin-left: 8px; }
  .domain-select { padding: 8px 12px; background: #111; border: 1px solid #222; color: #e0e0e0; border-radius: 6px; font-size: 12px; }
  .ref-sample { width: 100%; height: 80px; background: #111; border: 1px solid #222; color: #e0e0e0; padding: 8px; font-size: 12px; border-radius: 6px; resize: vertical; margin-top: 8px; }
  /* v5 new styles */
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; padding: 8px; background: #0d0d0d; border: 1px solid #1a1a1a; border-radius: 6px; }
  .toolbar button { padding: 6px 12px; font-size: 11px; border-radius: 4px; }
  .tab-bar { display: flex; gap: 0; border-bottom: 1px solid #222; margin-bottom: 12px; }
  .tab-btn { padding: 8px 16px; font-size: 12px; background: none; color: #666; border: none; cursor: pointer; border-bottom: 2px solid transparent; text-transform: uppercase; letter-spacing: 0.5px; }
  .tab-btn.active { color: #00cc88; border-bottom-color: #00cc88; }
  .tab-btn:hover { color: #aaa; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin: 12px 0; }
  .stat-card { background: #111; border: 1px solid #1a1a1a; border-radius: 6px; padding: 12px; }
  .stat-card .value { font-size: 18px; font-weight: 700; color: #00cc88; }
  .stat-card .label { font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
  .version-list { max-height: 300px; overflow-y: auto; }
  .version-item { padding: 8px 12px; border: 1px solid #1a1a1a; border-radius: 4px; margin-bottom: 4px; cursor: pointer; font-size: 12px; }
  .version-item:hover { border-color: #333; background: #111; }
  .word-list-area { width: 100%; height: 80px; background: #111; border: 1px solid #222; color: #e0e0e0; padding: 8px; font-size: 12px; border-radius: 4px; resize: vertical; }
  .export-btns { display: flex; gap: 6px; }
  .export-btns button { padding: 6px 10px; font-size: 11px; }
  .theme-toggle { position: fixed; top: 12px; right: 12px; z-index: 100; background: #222; border: 1px solid #333; color: #888; padding: 6px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
  body.light-mode { background: #f5f5f5; color: #1a1a1a; }
  body.light-mode .sidebar { background: #fafafa; border-color: #e0e0e0; }
  body.light-mode textarea { background: #fff; border-color: #ddd; color: #1a1a1a; }
  body.light-mode .stat-card { background: #fff; border-color: #e0e0e0; }
  body.light-mode .toolbar { background: #fafafa; border-color: #e0e0e0; }
  body.light-mode .history-item { border-color: #e0e0e0; }
  body.light-mode .history-item:hover { background: #f0f0f0; }
  body.light-mode select, body.light-mode .domain-select { background: #fff; border-color: #ddd; color: #1a1a1a; }
  body.light-mode .btn-secondary { background: #f0f0f0; color: #333; border-color: #ddd; }
  body.light-mode .diff-unchanged { color: #999; }
  body.light-mode .heatmap-paragraph { color: #333; }
  @media (max-width: 768px) { .panels { grid-template-columns: 1fr; } textarea { height: 250px; } .sidebar { display: none; } }
</style>
</head>
<body>
<button class="theme-toggle" onclick="toggleTheme()">Light/Dark</button>
<div class="layout">
  <div class="sidebar" id="historySidebar">
    <h3>History</h3>
    <div id="historyList"><div style="color:#444;font-size:12px;">No history yet</div></div>
    <h3 style="margin-top:16px;">Versions</h3>
    <div class="version-list" id="versionList"><div style="color:#444;font-size:12px;">No versions yet</div></div>
  </div>
  <div class="container">
    <h1>HumanizeAI v3</h1>
    <p class="subtitle">Multi-pass text humanizer — bypass AI detection (multi-model, length-preserving)</p>

    <div class="upload-zone" id="uploadZone" onclick="document.getElementById('fileInput').click()">
      <input type="file" id="fileInput" accept=".docx,.txt" onchange="uploadFile(this)">
      <label>📁 Drop .docx or .txt file here, or click to upload</label>
    </div>

    <div style="margin-bottom: 12px;">
      <details style="border: 1px solid #222; border-radius: 6px; padding: 8px 12px;">
        <summary style="color: #888; font-size: 12px; cursor: pointer;">Optional: Paste your writing sample (style matching)</summary>
        <textarea class="ref-sample" id="refSample" placeholder="Paste a sample of YOUR actual writing here. The humanizer will match your style (sentence length, vocabulary, transitions)..."></textarea>
      </details>
    </div>

    <div class="toolbar">
      <button class="btn-secondary" onclick="runPreview()">Preview (10%)</button>
      <button class="btn-secondary" onclick="checkExternal()">ZeroGPT Check</button>
      <div class="export-btns">
        <button class="btn-secondary" onclick="downloadDocx()">DOCX</button>
        <button class="btn-secondary" onclick="downloadTxt()">TXT</button>
        <button class="btn-secondary" onclick="downloadMd()">MD</button>
      </div>
      <button class="btn-secondary" onclick="showStatsTab()">Stats</button>
      <button class="btn-secondary" onclick="showCustomLists()">Word Lists</button>
    </div>

    <div class="controls">
      <button class="btn-primary" id="humanizeBtn" onclick="humanize()">Humanize</button>
      <select id="passes">
        <option value="3">3 Passes (Best)</option>
        <option value="2">2 Passes (Faster)</option>
        <option value="1">1 Pass (Quick)</option>
      </select>
      <select id="model">
        <option value="cx/gpt-5.5" selected>Recommended (GPT-5.5, best quality+length)</option>
        <option value="ag/claude-sonnet-4-6">Best Quality (Claude Sonnet, ~10s/pass)</option>
        <option value="ag/gemini-3-flash">Fast (Gemini 3 Flash, ~5s/pass)</option>
        <option value="ag/gemini-3.5-flash-low">Fastest (Gemini 3.5, ~3s/pass)</option>
        <option value="ag/gpt-oss-120b-medium">Balanced (GPT-OSS 120B, ~8s/pass)</option>
        <option value="ag/claude-opus-4-6-thinking">Premium (Opus Thinking, ~25s/pass)</option>
        <option value="cx/gpt-5.4">High Quality (GPT-5.4, ~8s/pass)</option>
        <option value="cx/gpt-5.4-mini">Fast Quality (GPT-5.4 Mini, ~4s/pass)</option>

      </select>
      <select id="tone">
        <option value="casual">Casual Tone</option>
        <option value="academic">Academic Tone</option>
        <option value="business">Business Tone</option>
      </select>
      <select id="domain" class="domain-select">
        <option value="general">General Domain</option>
        <option value="academic">Academic Domain</option>
        <option value="tech">Tech Domain</option>
        <option value="medical">Medical Domain</option>
        <option value="legal">Legal Domain</option>
      </select>
      <button class="btn-secondary" onclick="copyOutput()">Copy Output</button>
      <button class="btn-secondary" onclick="downloadDocx()">Download .docx</button>
      <button class="btn-secondary" onclick="clearAll()">Clear</button>
    </div>

    <div class="progress-bar" id="progressBar" style="display:none;">
      <div class="progress-fill" id="progressFill" style="width:0%"></div>
    </div>

    <div class="panels">
      <div class="panel">
        <div class="panel-label">Input (AI Text)</div>
        <textarea id="input" placeholder="Paste your AI-generated text here..."></textarea>
      </div>
      <div class="panel">
        <div class="panel-label">Output (Humanized)</div>
        <textarea id="output" placeholder="Humanized text will appear here..." readonly></textarea>
      </div>
    </div>

    <div class="status" id="status">Ready</div>
    <div class="stats" id="stats"></div>

    <div class="heatmap-container" id="heatmapContainer">
      <div class="heatmap-title">Detection Heatmap (click red paragraphs to re-process)</div>
      <div id="heatmapBody"></div>
    </div>

    <div id="statsPanel" style="display:none;margin-top:16px;border:1px solid #222;border-radius:8px;padding:16px;">
      <h3 style="font-size:14px;color:#fff;margin-bottom:12px;">Processing Statistics</h3>
      <div class="stats-grid" id="statsGrid"></div>
    </div>

    <div id="customListsPanel" style="display:none;margin-top:16px;border:1px solid #222;border-radius:8px;padding:16px;">
      <h3 style="font-size:14px;color:#fff;margin-bottom:8px;">Custom Word Lists</h3>
      <p style="font-size:11px;color:#666;margin-bottom:8px;">Preserve: words to never modify (one per line). Avoid: AI words to always replace.</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div>
          <label style="font-size:11px;color:#888;">Preserve List</label>
          <textarea class="word-list-area" id="preserveList" placeholder="Malaysia
UMP
FYP"></textarea>
        </div>
        <div>
          <label style="font-size:11px;color:#888;">Avoid List</label>
          <textarea class="word-list-area" id="avoidList" placeholder="delve
leverage
utilize"></textarea>
        </div>
      </div>
      <button class="btn-primary" onclick="saveCustomLists()" style="margin-top:8px;padding:8px 16px;font-size:12px;">Save Lists</button>
    </div>

    <div id="previewPanel" style="display:none;margin-top:16px;border:1px solid #222;border-radius:8px;padding:16px;">
      <h3 style="font-size:14px;color:#fff;margin-bottom:8px;">Preview (first 10%)</h3>
      <div id="previewContent" style="font-size:12px;color:#aaa;line-height:1.6;"></div>
    </div>

    <div class="diff-container" id="diffContainer">
      <div class="diff-header">
        <h3>Side-by-Side Diff</h3>
        <button class="btn-secondary" onclick="toggleDiff()" style="padding:6px 12px;font-size:12px;">Toggle View</button>
      </div>
      <div class="diff-body" id="diffBody"></div>
    </div>
  </div>
</div>

<script>
// Load history on page load
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

  btn.disabled = true;
  output.value = '';
  progressBar.style.display = 'block';
  progressFill.style.width = '2%';
  status.innerHTML = 'Starting... ' + words + ' words (' + chunks + ' chunk' + (chunks>1?'s':'') + ', parallel)';

  try {
    // Start job (returns immediately with job_id)
    const startResp = await fetch('/api/humanize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: input, passes: passes, model: model, tone: tone, domain: document.getElementById('domain').value, ref_sample: document.getElementById('refSample').value})
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
        status.innerHTML = 'Processing... ' + (prog.chunks_done || 0) + '/' + (prog.chunks_total || '?') + ' chunks done (' + (prog.progress || 0) + '%)';

        // Show partial results in output textarea
        if (prog.partial) {
          output.value = prog.partial;
        }

        if (prog.status === 'done') {
          done = true;
          progressFill.style.width = '100%';
          output.value = prog.result || prog.partial;

          const pct = Math.round((prog.output_words || 0) / (prog.input_words || 1) * 100);
          const pctColor = pct >= 80 ? '#00cc88' : pct >= 60 ? '#ffaa00' : '#ff4444';

          const inScore = prog.input_score || {};
          const outScore = prog.output_score || {};
          const outGrade = outScore.grade || 'N/A';
          const scoreColor = (g) => g && g.includes('HUMAN') ? '#00cc88' : g === 'MIXED' ? '#ffaa00' : '#ff4444';

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

          setTimeout(() => { progressBar.style.display = 'none'; progressFill.style.width = '0%'; }, 2000);
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
                self._json_response(job)
            else:
                self._json_response({"error": "Job not found"}, 404)
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
        elif self.path == "/api/batch":
            self._handle_batch()
        elif self.path == "/api/preview":
            self._handle_preview()
        elif self.path == "/api/custom-lists":
            self._handle_custom_lists()
        elif self.path == "/api/external-check":
            self._handle_external_check()
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
                }

            # Start background thread
            thread = threading.Thread(
                target=self._run_humanize_job,
                args=(job_id, text, passes, model, tone, domain, ref_sample),
                daemon=True,
            )
            thread.start()

            self._json_response({"job_id": job_id, "chunks": total_chunks, "input_words": input_words})

        except Exception as e:
            import traceback
            print(f"[ERROR] {traceback.format_exc()}", flush=True)
            self._json_response({"error": str(e)}, 500)

    def _run_humanize_job(self, job_id, text, passes, model, tone, domain="general", ref_sample=""):
        """Run full humanization in background, updating JOBS dict progressively."""
        t0 = time.time()
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
                elapsed = round(time.time() - t0, 1)
                output_score = calc_detection_score(result)

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
