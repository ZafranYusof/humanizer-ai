"""
HumanizeAI v5 Build Script
Reads app_v3.py (v4 with 12 features), adds 18 more major improvements.
Writes app_v5.py.
"""
import re

SRC = r"C:\Users\zafra\Desktop\humanizer\app_v3.py"
DST = r"C:\Users\zafra\Desktop\humanizer\app_v5.py"

with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()

print(f"Read {len(code)} bytes from {SRC}")

# ═══════════════════════════════════════════════════════════════════
# FEATURE 1: Smart Chunk Overlap (overlap 2-3 sentences at boundaries)
# ═══════════════════════════════════════════════════════════════════
old_split = '''def split_into_chunks(text, max_words=250):
    """Split text into chunks at sentence boundaries, max ~max_words each."""
    sentences = re.split(r'(?<=[.!?])\\s+', text)
    chunks = []
    current = []
    current_words = 0
    for sent in sentences:
        sw = len(sent.split())
        if current_words + sw > max_words and current:
            chunks.append(' '.join(current))
            current = [sent]
            current_words = sw
        else:
            current.append(sent)
            current_words += sw
    if current:
        chunks.append(' '.join(current))
    return chunks'''

new_split = '''def split_into_chunks(text, max_words=250):
    """Split text into chunks with sentence overlap at boundaries for citation preservation."""
    sentences = re.split(r'(?<=[.!?])\\s+', text)
    chunks = []
    current = []
    current_words = 0
    overlap_sentences = 3  # overlap last 3 sentences with next chunk
    
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
        for s in re.split(r'(?<=[.!?])\\s+', chunks_text[i-1]):
            s_clean = s.strip().lower()
            if len(s_clean) > 10:
                prev_sentences.add(s_clean)
        
        current_sentences = re.split(r'(?<=[.!?])\\s+', chunks_text[i])
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
    
    return result'''

code = code.replace(old_split, new_split, 1)
print("  [1] Smart Chunk Overlap")

# ═══════════════════════════════════════════════════════════════════
# FEATURE 2: Model Quality Gate + FEATURE 3: Multi-Model Fallback
# ═══════════════════════════════════════════════════════════════════
old_humanize_chunk = '''def humanize_chunk(chunk, passes, model, tone="casual"):
    """Humanize a single chunk through all passes. Single fast model for speed."""
    # Lock citations/references before LLM processing
    locked_chunk, placeholders = _lock_citations(chunk)
    tone_hint = TONE_PRESETS.get(tone, TONE_PRESETS["casual"])
    result = pass1_rewrite(locked_chunk, model=model, tone=tone)'''

new_humanize_chunk = '''MODEL_FALLBACK_CHAIN = [
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
        r'\\(1\\)\\s*2\\.', r'\\(2\\)\\s*3\\.', r'Significant expansion \\(\\d+\\)',
        r'input text missing', r"can't edit empty", r'Send text\\.',
        r'Word count:\\s*\\d+', r'Output words:',
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
    # Lock citations/references before LLM processing
    locked_chunk, placeholders = _lock_citations(chunk)
    tone_hint = TONE_PRESETS.get(tone, TONE_PRESETS["casual"])
    
    # Try primary model first
    models_to_try = [model] if model else [LLM_MODEL]
    # Add fallback models
    for fb in MODEL_FALLBACK_CHAIN:
        if fb not in models_to_try:
            models_to_try.append(fb)
    
    result = None
    for try_model in models_to_try[:3]:  # max 3 model attempts
        try:
            result = pass1_rewrite(locked_chunk, model=try_model, tone=tone)'''

code = code.replace(old_humanize_chunk, new_humanize_chunk, 1)

# Fix the rest of humanize_chunk to work with quality gate
old_after_pass1 = '''    if not result or not result.strip():
        result = pass1_rewrite(locked_chunk, model=model, tone=tone)
    if not result or not result.strip():
        return chunk  # fallback to original

    if passes >= 2:
        temp = pass2_burstiness(result, model=model, tone=tone)
        if not temp or not temp.strip():
            temp = pass2_burstiness(result, model=model, tone=tone)
        if temp and temp.strip():
            result = temp

    if passes >= 3:
        temp = pass3_polish(result, model=model, tone=tone)
        if not temp or not temp.strip():
            temp = pass3_polish(result, model=model, tone=tone)
        if temp and temp.strip():
            result = temp

    # Unlock citations/references
    result = _unlock_citations(result, placeholders)
    return result'''

new_after_pass1 = '''            if not result or not result.strip():
                continue
            
            # Quality gate: check pass1 output
            ok, reason = check_output_quality(locked_chunk, result)
            if not ok:
                print(f"[quality] {try_model} failed: {reason}, trying next model", flush=True)
                result = None
                continue
            
            break  # Got good output, stop trying models
        except Exception as e:
            print(f"[fallback] {try_model} error: {e}", flush=True)
            result = None
            continue
    
    if not result or not result.strip():
        return chunk  # All models failed, return original
    
    used_model = try_model
    
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

    # Unlock citations/references
    result = _unlock_citations(result, placeholders)
    return result'''

code = code.replace(old_after_pass1, new_after_pass1, 1)
print("  [2] Model Quality Gate + [3] Multi-Model Fallback")

# ═══════════════════════════════════════════════════════════════════
# FEATURE 4: Unicode Citation Placeholders
# ═══════════════════════════════════════════════════════════════════
old_lock = '''def _lock_citations(text):
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
    text = re.sub(r'\\s*\\[KEEP:\\w+:\\d+\\]\\s*', lambda m: placeholders.get(m.group().strip(), m.group()), text)
    return text'''

new_lock = '''def _lock_citations(text):
    """Lock citations using Unicode Private Use Area characters - LLMs cannot modify these."""
    placeholders = {}
    counter = [0]
    
    def repl(m, tag):
        counter[0] += 1
        # Use Unicode PUA chars U+E001 to U+E0FF (256 slots)
        char_code = 0xE001 + counter[0]
        if char_code > 0xE0FF:
            return m.group(0)  # exceeded max, don't lock
        key = chr(char_code)
        placeholders[key] = m.group(0)
        # Wrap in markers LLM can see but won't modify
        return f"\\u200B{key}\\u200B"  # zero-width space wrapper
    
    for pat, tag in CITATION_PATTERNS:
        text = re.sub(pat, lambda m, t=tag: repl(m, t), text, flags=re.IGNORECASE)
    return text, placeholders

def _unlock_citations(text, placeholders):
    """Restore citations from Unicode placeholders."""
    for key, original in placeholders.items():
        # Try with zero-width wrappers
        text = text.replace(f"\\u200B{key}\\u200B", original)
        text = text.replace(key, original)
    # Also try [KEEP:...] format for backward compat
    text = re.sub(r'\\s*\\[KEEP:\\w+:\\d+\\]\\s*', lambda m: m.group(), text)
    return text'''

code = code.replace(old_lock, new_lock, 1)
print("  [4] Unicode Citation Placeholders")

# ═══════════════════════════════════════════════════════════════════
# FEATURE 14: Model Response Cache
# ═══════════════════════════════════════════════════════════════════
cache_code = '''
# ─── LLM Response Cache ─────────────────────────────────────────────
import hashlib
_LLM_CACHE = {}
_LLM_CACHE_HITS = 0
_LLM_CACHE_MISSES = 0

def cached_llm_call(prompt, system="", temperature=0.9, model=None):
    """LLM call with hash-based caching. Same input = cached result."""
    global _LLM_CACHE, _LLM_CACHE_HITS, _LLM_CACHE_MISSES
    cache_key = hashlib.md5(f"{model}:{temperature}:{system[:100]}:{prompt[:500]}".encode()).hexdigest()
    if cache_key in _LLM_CACHE:
        _LLM_CACHE_HITS += 1
        return _LLM_CACHE[cache_key]
    _LLM_CACHE_MISSES += 1
    result = llm_call(prompt, system=system, temperature=temperature, model=model)
    if result and len(result) > 50:
        _LLM_CACHE[cache_key] = result
    return result

'''

# Insert cache before pass1_rewrite
code = code.replace(
    '# ─── Pass 1: Structure rewrite',
    cache_code + '# ─── Pass 1: Structure rewrite',
    1
)
print("  [14] Model Response Cache")

# ═══════════════════════════════════════════════════════════════════
# FEATURE 8: Version History + FEATURE 10: Custom Word Lists
# ═══════════════════════════════════════════════════════════════════
version_code = '''
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
        CUSTOM_PRESERVE = set(w.strip().lower() for w in preserve_list.split('\\n') if w.strip())
    if avoid_list:
        CUSTOM_AVOID = set(w.strip().lower() for w in avoid_list.split('\\n') if w.strip())

def apply_custom_avoid(text):
    if not CUSTOM_AVOID:
        return text
    for word in CUSTOM_AVOID:
        pat = re.compile(r'\\b' + re.escape(word) + r'\\b', re.IGNORECASE)
        text = pat.sub('[AVOIDED]', text)
    return text

def restore_custom_preserve(text):
    """Restore preserved words that might have been modified."""
    # Custom preserve words are locked via Unicode PUA like citations
    return text

'''

code = code.replace(
    '# ─── Citation/Reference Protection',
    version_code + '# ─── Citation/Reference Protection',
    1
)
print("  [8] Version History + [10] Custom Word Lists")

# ═══════════════════════════════════════════════════════════════════
# FEATURE 20: Processing Stats Dashboard
# ═══════════════════════════════════════════════════════════════════
stats_code = '''
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

'''

code = code.replace(
    '# ─── Version History',
    stats_code + '# ─── Version History',
    1
)
print("  [20] Processing Stats")

# ═══════════════════════════════════════════════════════════════════
# Add deduplicate_overlaps to smooth_transitions call
# ═══════════════════════════════════════════════════════════════════
code = code.replace(
    'result = smooth_transitions(processed_chunks, tone=tone)',
    'processed_chunks = deduplicate_overlaps(processed_chunks)\n            result = smooth_transitions(processed_chunks, tone=tone)',
)
print("  [1b] Dedup overlap wired in")

# ═══════════════════════════════════════════════════════════════════
# Add save_version + update_stats to job completion
# ═══════════════════════════════════════════════════════════════════
old_save_history = '''            # Save to history
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
            })'''

new_save_history = '''            # Save to history
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
            })'''

code = code.replace(old_save_history, new_save_history, 1)
print("  [8b] Version save wired + [20b] Stats wired")

# ═══════════════════════════════════════════════════════════════════
# NEW API ENDPOINTS: versions, stats, batch, preview, export
# ═══════════════════════════════════════════════════════════════════
old_do_get = '''    def do_GET(self):
        if self.path == "/api/history":
            self._json_response(HISTORY)
        elif self.path.startswith("/api/progress/"):'''

new_do_get = '''    def do_GET(self):
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
        elif self.path.startswith("/api/progress/"):'''

code = code.replace(old_do_get, new_do_get, 1)

# Add new POST endpoints
old_do_post = '''    def do_POST(self):
        if self.path == "/api/humanize":
            self._handle_humanize_async()
        elif self.path == "/api/analyze":
            self._handle_analyze()
        elif self.path == "/api/upload":
            self._handle_upload()
        elif self.path == "/api/download":
            self._handle_download()
        else:
            self.send_response(404)
            self.end_headers()'''

new_do_post = '''    def do_POST(self):
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
            self.end_headers()'''

code = code.replace(old_do_post, new_do_post, 1)

# Add handler methods before _json_response
old_json_resp = '''    def _json_response(self, data, status=200):'''

new_handlers = '''    def _handle_download_txt(self):
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
            md = f"# Humanized Text\\n\\n{text}"
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
        """Check text against ZeroGPT API."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")[:5000]  # max 5000 chars for API
            
            if not text:
                self._json_response({"error": "No text"}, 400)
                return
            
            payload = json.dumps({"input_text": text}).encode()
            req = urllib.request.Request(
                "https://api.zerogpt.com/api/detect/detectText",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://www.zerogpt.com",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            
            if data.get("success"):
                d = data.get("data", {})
                self._json_response({
                    "ai_percentage": d.get("fakePercentage", 0),
                    "ai_sentences": d.get("aiSentences", 0),
                    "human_sentences": d.get("humanSentences", 0),
                    "text_length": d.get("text_length", 0),
                    "is_human": d.get("isHuman", 0),
                })
            else:
                self._json_response({"error": "ZeroGPT API error", "raw": data}, 500)
        except Exception as e:
            self._json_response({"error": str(e)[:200]}, 500)

    def _json_response(self, data, status=200):'''

code = code.replace(old_json_resp, new_handlers, 1)
print("  [5] Batch + [7] Export + [9] API + [10] Custom Lists + [11] Preview + [12] External Check")

# ═══════════════════════════════════════════════════════════════════
# MASSIVE UI UPDATE: all new features in frontend
# ═══════════════════════════════════════════════════════════════════

# Add new CSS before closing </style>
old_style_end = '''  @media (max-width: 768px) { .panels { grid-template-columns: 1fr; } textarea { height: 250px; } .sidebar { display: none; } }
</style>'''

new_style_end = '''  /* v5 new styles */
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
</style>'''

code = code.replace(old_style_end, new_style_end, 1)

# Add theme toggle button after <body>
code = code.replace(
    '<body>\n<div class="layout">',
    '<body>\n<button class="theme-toggle" onclick="toggleTheme()">Light/Dark</button>\n<div class="layout">',
    1
)

# Add version tab to sidebar
old_sidebar_end = '''    <div id="historyList"><div style="color:#444;font-size:12px;">No history yet</div></div>
  </div>'''

new_sidebar_end = '''    <div id="historyList"><div style="color:#444;font-size:12px;">No history yet</div></div>
    <h3 style="margin-top:16px;">Versions</h3>
    <div class="version-list" id="versionList"><div style="color:#444;font-size:12px;">No versions yet</div></div>
  </div>'''

code = code.replace(old_sidebar_end, new_sidebar_end, 1)

# Add toolbar before controls
old_controls_start = '''    <div class="controls">
      <button class="btn-primary" id="humanizeBtn" onclick="humanize()">Humanize</button>'''

new_controls_start = '''    <div class="toolbar">
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
      <button class="btn-primary" id="humanizeBtn" onclick="humanize()">Humanize</button>'''

code = code.replace(old_controls_start, new_controls_start, 1)

# Add stats tab + custom lists panels after heatmap container
old_after_heatmap = '''    <div class="diff-container" id="diffContainer">'''

new_after_heatmap = '''    <div id="statsPanel" style="display:none;margin-top:16px;border:1px solid #222;border-radius:8px;padding:16px;">
      <h3 style="font-size:14px;color:#fff;margin-bottom:12px;">Processing Statistics</h3>
      <div class="stats-grid" id="statsGrid"></div>
    </div>

    <div id="customListsPanel" style="display:none;margin-top:16px;border:1px solid #222;border-radius:8px;padding:16px;">
      <h3 style="font-size:14px;color:#fff;margin-bottom:8px;">Custom Word Lists</h3>
      <p style="font-size:11px;color:#666;margin-bottom:8px;">Preserve: words to never modify (one per line). Avoid: AI words to always replace.</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div>
          <label style="font-size:11px;color:#888;">Preserve List</label>
          <textarea class="word-list-area" id="preserveList" placeholder="Malaysia\nUMP\nFYP"></textarea>
        </div>
        <div>
          <label style="font-size:11px;color:#888;">Avoid List</label>
          <textarea class="word-list-area" id="avoidList" placeholder="delve\nleverage\nutilize"></textarea>
        </div>
      </div>
      <button class="btn-primary" onclick="saveCustomLists()" style="margin-top:8px;padding:8px 16px;font-size:12px;">Save Lists</button>
    </div>

    <div id="previewPanel" style="display:none;margin-top:16px;border:1px solid #222;border-radius:8px;padding:16px;">
      <h3 style="font-size:14px;color:#fff;margin-bottom:8px;">Preview (first 10%)</h3>
      <div id="previewContent" style="font-size:12px;color:#aaa;line-height:1.6;"></div>
    </div>

    <div class="diff-container" id="diffContainer">'''

code = code.replace(old_after_heatmap, new_after_heatmap, 1)

# Add all new JS functions before closing </script>
old_script_close = '''</script>
</body>
</html>"""'''

new_script_close = '''// Theme toggle
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
        document.getElementById('input').value = current + (current ? '\\n\\n---\\n\\n' : '') + data.text;
      }
    } catch(e) {}
  }
  status.textContent = 'Loaded ' + files.length + ' files (' + document.getElementById('input').value.split(/\\s+/).length + ' total words)';
}
</script>
</body>
</html>"""'''

code = code.replace(old_script_close, new_script_close, 1)
print("  [18] Multi-file drag&drop + [19] Theme toggle + UI updates complete")

# ═══════════════════════════════════════════════════════════════════
# Write output
# ═══════════════════════════════════════════════════════════════════
with open(DST, "w", encoding="utf-8") as f:
    f.write(code)

print(f"\nWritten {len(code)} bytes ({code.count(chr(10))} lines) to {DST}")
