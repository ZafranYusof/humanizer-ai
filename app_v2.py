"""
HumanizeAI - Multi-pass AI text humanizer (v2 - simplified)
"""

import json
import re
import sys
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from textwrap import dedent
from datetime import datetime

# ─── Config ───────────────────────────────────────────────────────────
LLM_BASE = "http://localhost:20128/v1"
LLM_KEY = "123456"
LLM_MODEL = "ds/deepseek-chat"

# ─── LLM call ─────────────────────────────────────────────────────────

def llm_call(prompt, system="", temperature=0.9):
    """Call local LLM with given params."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
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
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        error_body = ""
        if hasattr(e, 'read'):
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except:
                error_body = str(e)
        raise RuntimeError(f"LLM returned {e.code}: {error_body[:300] if error_body else 'Bad Request'}")


# ─── Pass 1: Structure rewrite ────────────────────────────────────────

def pass1_rewrite(text):
    """Rewrite with varied sentence structure, high temperature."""
    system = """You are a human writer. Rewrite the text below to sound natural and human.
Rules:
- Vary sentence length dramatically. Mix very short sentences (3-8 words) with longer complex ones (25-40 words).
- Use contractions freely (don't, it's, we're, etc.).
- Add occasional filler phrases like "honestly", "basically", "you know", "I think", "the thing is".
- Use casual transitions instead of formal ones ("so" instead of "therefore", "but" instead of "however", "also" instead of "furthermore").
- Keep the exact same meaning and all key information.
- Do NOT add new information or remove any facts.
- Write as if explaining to a friend, not writing an essay.
- Keep the same language as the original.
- Output ONLY the rewritten text, no explanations."""

    return llm_call(text, system=system, temperature=0.92)


# ─── Pass 2: Burstiness injection ────────────────────────────────────

def pass2_burstiness(text):
    """Inject sentence length variation and imperfections."""
    system = """You are editing text to make it sound more human. Apply these changes:
1. Break some long sentences into two shorter ones.
2. Combine some short consecutive sentences into one longer sentence.
3. Add 2-3 casual phrases like "honestly", "I think", "the thing is", "you know".
4. Replace any remaining formal transitions with casual ones.
5. Add one incomplete thought or self-correction.
6. Make sure no two consecutive sentences are similar in length.
Keep all facts and meaning intact. Output ONLY the edited text."""

    return llm_call(text, system=system, temperature=0.85)


# ─── Pass 3: Final polish ─────────────────────────────────────────────

def pass3_polish(text):
    """Final pass: remove AI tells, add personality."""
    system = """You are a final editor. Remove any remaining AI-like patterns from this text:
- Remove any remaining formal/stiff phrases
- Ensure contractions are used throughout
- Add 1-2 personal touches
- Make sure the tone is conversational but still informative
- Do NOT use: "delve", "dive into", "explore", "landscape", "tapestry", "crucial", "pivotal", "leverage", "utilize", "facilitate", "comprehensive", "robust", "streamline"
- Replace those words with simpler alternatives
- Output ONLY the final text, no explanations."""

    return llm_call(text, system=system, temperature=0.78)


# ─── Post-processing ─────────────────────────────────────────────────

CONTRACTIONS = {
    "do not": "don't", "does not": "doesn't", "did not": "didn't",
    "is not": "isn't", "are not": "aren't", "was not": "wasn't",
    "will not": "won't", "would not": "wouldn't", "could not": "couldn't",
    "should not": "shouldn't", "cannot": "can't", "can not": "can't",
    "have not": "haven't", "has not": "hasn't", "had not": "hadn't",
    "it is": "it's", "that is": "that's", "there is": "there's",
    "I am": "I'm", "I have": "I've", "I will": "I'll", "I would": "I'd",
}

TRANSITION_KILLERS = [
    ("Furthermore", "Also"), ("Moreover", "Plus"), ("Nevertheless", "Still"),
    ("Consequently", "So"), ("In conclusion", "To wrap up"),
    ("In addition", "On top of that"), ("Therefore", "So"),
    ("However", "But"), ("Additionally", "Also"), ("Thus", "So"),
]

def post_process(text):
    """Apply mechanical humanization."""
    for full, short in CONTRACTIONS.items():
        pattern = re.compile(re.escape(full), re.IGNORECASE)
        text = pattern.sub(short, text)

    for formal, casual in TRANSITION_KILLERS:
        pattern = re.compile(r'(^|\.\s+)' + re.escape(formal) + r'\b', re.IGNORECASE)
        text = pattern.sub(lambda m: m.group(1) + casual, text)

    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─── Main pipeline ────────────────────────────────────────────────────

def humanize(text, passes=3):
    """Run full humanization pipeline."""
    result = pass1_rewrite(text)

    if passes >= 2:
        result = pass2_burstiness(result)

    if passes >= 3:
        result = pass3_polish(result)

    result = post_process(result)
    return result


# ─── HTML Template ────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HumanizeAI v2</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
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
  button {
    padding: 12px 24px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600;
    cursor: pointer; transition: all 0.15s;
  }
  .btn-primary { background: #00cc88; color: #000; }
  .btn-primary:hover { background: #00e099; }
  .btn-primary:disabled { background: #333; color: #666; cursor: not-allowed; }
  .btn-secondary { background: #222; color: #ccc; border: 1px solid #333; }
  .btn-secondary:hover { background: #2a2a2a; }
  select {
    padding: 10px 16px; background: #111; border: 1px solid #222; color: #e0e0e0;
    border-radius: 8px; font-size: 14px; cursor: pointer;
  }
  .status { color: #888; font-size: 13px; padding: 8px 0; }
  @media (max-width: 768px) { .panels { grid-template-columns: 1fr; } textarea { height: 250px; } }
</style>
</head>
<body>
<div class="container">
  <h1>HumanizeAI v2</h1>
  <p class="subtitle">Multi-pass text humanizer — bypass AI detection</p>

  <div class="controls">
    <button class="btn-primary" id="humanizeBtn" onclick="humanize()">Humanize</button>
    <select id="passes">
      <option value="3">3 Passes (Best)</option>
      <option value="2">2 Passes (Faster)</option>
      <option value="1">1 Pass (Quick)</option>
    </select>
    <button class="btn-secondary" onclick="copyOutput()">Copy Output</button>
    <button class="btn-secondary" onclick="clearAll()">Clear</button>
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
</div>

<script>
async function humanize() {
  const input = document.getElementById('input').value.trim();
  if (!input) { alert('Paste some text first'); return; }

  const passes = parseInt(document.getElementById('passes').value);
  const btn = document.getElementById('humanizeBtn');
  const status = document.getElementById('status');
  const output = document.getElementById('output');

  btn.disabled = true;
  output.value = '';
  status.textContent = 'Processing... (this may take 20-60 seconds)';

  try {
    const resp = await fetch('/api/humanize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: input, passes: passes})
    });

    const data = await resp.json();
    
    if (data.error) {
      status.textContent = 'Error: ' + data.error;
    } else {
      output.value = data.result;
      status.textContent = `Done! ${data.input_words} → ${data.output_words} words`;
    }
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
  }

  btn.disabled = false;
}

function copyOutput() {
  const output = document.getElementById('output');
  if (output.value) {
    navigator.clipboard.writeText(output.value);
    document.getElementById('status').textContent = 'Copied!';
  }
}

function clearAll() {
  document.getElementById('input').value = '';
  document.getElementById('output').value = '';
  document.getElementById('status').textContent = 'Ready';
}
</script>
</body>
</html>"""


# ─── HTTP Server ──────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    timeout = 300  # 5 minutes

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML.encode("utf-8"))

    def do_POST(self):
        if self.path != "/api/humanize":
            self.send_response(404)
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")
            passes = body.get("passes", 3)

            if not text:
                self._json_response({"error": "No text provided"}, 400)
                return

            print(f"[{datetime.now()}] Humanizing {len(text.split())} words, {passes} passes...", flush=True)
            result = humanize(text, passes)
            print(f"[{datetime.now()}] Done: {len(result.split())} words", flush=True)

            self._json_response({
                "result": result,
                "input_words": len(text.split()),
                "output_words": len(result.split()),
            })

        except Exception as e:
            import traceback
            print(f"[ERROR] {traceback.format_exc()}", flush=True)
            self._json_response({"error": str(e)}, 500)

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        pass


def run_server(port=7860):
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"HumanizeAI v2 running at http://localhost:{port}", flush=True)
    print("Press Ctrl+C to stop", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
