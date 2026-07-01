# Inject new API endpoints into app_v5.py
import re

with open('app_v5.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the do_GET section and add new GET endpoints
# Find "elif self.path == \"/api/debug-cache\":" and add before it
get_endpoints = '''        elif self.path == "/api/model-status":
            self._json_response(MODEL_LATENCY if MODEL_LATENCY else {m: {"ok": True, "latency_ms": 0, "last_check": 0} for m in list(MODEL_OPTIONS.keys())[:5]})
'''

marker_get = '        elif self.path == "/api/debug-cache":'
if marker_get in content:
    content = content.replace(marker_get, get_endpoints + marker_get, 1)
    print("Added GET /api/model-status")

# Find do_POST section and add new POST endpoints
# Insert before "elif self.path == \"/api/batch\":"
post_endpoints = '''        elif self.path == "/api/voice-check":
            self._handle_voice_check()
        elif self.path == "/api/similarity":
            self._handle_similarity()
        elif self.path == "/api/citation-format":
            self._handle_citation_format()
        elif self.path == "/api/grammar-fix":
            self._handle_grammar_fix()
        elif self.path == "/api/keywords":
            self._handle_keywords()
'''

marker_post = '        elif self.path == "/api/batch":'
if marker_post in content:
    content = content.replace(marker_post, post_endpoints + marker_post, 1)
    print("Added POST endpoints")

# Now add the handler methods. Find _handle_analyze and add new methods after it.
handler_methods = '''
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
            words = re.findall(r'\\b[a-z]{4,}\\b', text)
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

'''

# Find _handle_analyze end and insert after
# Look for the method that starts with "    def _handle_analyze"
# Find the next method definition after it
analyze_end = content.find('    def _handle_upload(self):')
if analyze_end > 0:
    # Actually insert before _handle_upload for cleanliness
    content = content[:analyze_end] + handler_methods + content[analyze_end:]
    print("Added handler methods")

with open('app_v5.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done! File size:", len(content))
