
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

