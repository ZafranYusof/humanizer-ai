"""
HumanizeAI - Multi-pass AI text humanizer
Bypasses GPTZero, Turnitin, ZeroGPT through:
- Multi-pass rewriting with different strategies
- Burstiness injection (vary sentence length)
- Perplexity boost (unexpected word choices)
- Personal touch (filler phrases, contractions)
"""

import json
import random
import re
import sys
import urllib.request
import urllib.error
from textwrap import dedent

# ─── Config ───────────────────────────────────────────────────────────
LLM_BASE = "http://localhost:20128/v1"
LLM_KEY = "123456"
LLM_MODEL = "QW/qwen3.7-max"

# ─── Humanization strategies ─────────────────────────────────────────

FILLER_PHRASES = [
    "honestly", "basically", "you know", "I mean", "to be fair",
    "look", "the thing is", "at the end of the day", "in my opinion",
    "I think", "personally", "from what I've seen", "it seems like",
    "the way I see it", "if you ask me", "truth be told",
    "I'd say", "let me put it this way", "here's the thing",
]

CONTRACTIONS = {
    "do not": "don't", "does not": "doesn't", "did not": "didn't",
    "is not": "isn't", "are not": "aren't", "was not": "wasn't",
    "were not": "weren't", "will not": "won't", "would not": "wouldn't",
    "could not": "couldn't", "should not": "shouldn't", "can not": "can't",
    "cannot": "can't", "have not": "haven't", "has not": "hasn't",
    "had not": "hadn't", "it is": "it's", "that is": "that's",
    "there is": "there's", "I am": "I'm", "I have": "I've",
    "I will": "I'll", "I would": "I'd", "we are": "we're",
    "we have": "we've", "we will": "we'll", "they are": "they're",
    "they have": "they've", "they will": "they'll",
    "let us": "let's", "who is": "who's", "what is": "what's",
}

TRANSITION_KILLERS = [
    ("Furthermore", "Also"),
    ("Moreover", "Plus"),
    ("Nevertheless", "Still"),
    ("Consequently", "So"),
    ("In conclusion", "To wrap up"),
    ("In addition", "On top of that"),
    ("Therefore", "So"),
    ("However", "But"),
    ("Additionally", "Also"),
    ("Subsequently", "Then"),
    ("Notwithstanding", "Even so"),
    ("Henceforth", "From now on"),
    ("Hence", "So"),
    ("Thus", "So"),
]

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
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {LLM_KEY}",
        },
        method="POST",
    )

    # Debug: print payload size and model
    print(f"[DEBUG] Model: {LLM_MODEL}, Payload size: {len(json.dumps(payload))} bytes", file=sys.stderr, flush=True)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            data = json.loads(raw)
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        error_body = ""
        if hasattr(e, 'read'):
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except:
                error_body = str(e)
        print(f"[ERROR] LLM returned {e.code}: {error_body[:500]}", file=sys.stderr, flush=True)
        raise RuntimeError(f"LLM returned {e.code}: {error_body[:300] if error_body else 'Bad Request'}")


# ─── Pass 1: Structure rewrite ────────────────────────────────────────

def pass1_rewrite(text):
    """Rewrite with varied sentence structure, high temperature."""
    system = dedent("""
        You are a human writer. Rewrite the text below to sound natural and human.
        Rules:
        - Vary sentence length dramatically. Mix very short sentences (3-8 words) with longer complex ones (25-40 words).
        - Use contractions freely (don't, it's, we're, etc.).
        - Add occasional filler phrases like "honestly", "basically", "you know", "I think", "the thing is".
        - Use casual transitions instead of formal ones ("so" instead of "therefore", "but" instead of "however", "also" instead of "furthermore").
        - Keep the exact same meaning and all key information.
        - Do NOT add new information or remove any facts.
        - Write as if explaining to a friend, not writing an essay.
        - Keep the same language as the original (if English, write English).
        - Output ONLY the rewritten text, no explanations.
    """).strip()

    return llm_call(text, system=system, temperature=0.92)


# ─── Pass 2: Burstiness injection ────────────────────────────────────

def pass2_burstiness(text):
    """Inject sentence length variation and imperfections."""
    system = dedent("""
        You are editing text to make it sound more human. Apply these changes:
        1. Break some long sentences into two shorter ones.
        2. Combine some short consecutive sentences into one longer sentence.
        3. Add 2-3 casual phrases like "honestly", "I think", "the thing is", "you know".
        4. Replace any remaining formal transitions with casual ones.
        5. Add one incomplete thought or self-correction (e.g., "well, it's not exactly that simple but..." or "actually, let me rephrase that").
        6. Make sure no two consecutive sentences are similar in length.
        Keep all facts and meaning intact. Output ONLY the edited text.
    """).strip()

    return llm_call(text, system=system, temperature=0.85)


# ─── Pass 3: Final polish ─────────────────────────────────────────────

def pass3_polish(text):
    """Final pass: remove AI tells, add personality."""
    system = dedent("""
        You are a final editor. Remove any remaining AI-like patterns from this text:
        - Remove any remaining formal/stiff phrases
        - Ensure contractions are used throughout
        - Add 1-2 personal touches (e.g., "from my experience", "in my view", "I've noticed that")
        - Make sure the tone is conversational but still informative
        - Keep paragraph structure reasonable
        - Do NOT use: "delve", "dive into", "explore", "landscape", "tapestry", "crucial", "pivotal", "leverage", "utilize", "facilitate", "comprehensive", "robust", "streamline"
        - Replace those words with simpler alternatives
        - Output ONLY the final text, no explanations or notes.
    """).strip()

    return llm_call(text, system=system, temperature=0.78)


# ─── Post-processing (no LLM) ────────────────────────────────────────

def post_process(text):
    """Apply mechanical humanization."""
    # Apply contractions
    for full, short in CONTRACTIONS.items():
        pattern = re.compile(re.escape(full), re.IGNORECASE)
        text = pattern.sub(short, text)

    # Kill formal transitions
    for formal, casual in TRANSITION_KILLERS:
        # Case-insensitive at sentence start
        pattern = re.compile(r'(^|\.\s+)' + re.escape(formal) + r'\b', re.IGNORECASE)
        text = pattern.sub(lambda m: m.group(1) + casual, text)

    # Remove double spaces
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# ─── Main pipeline ────────────────────────────────────────────────────

def humanize(text, passes=3):
    """Run full humanization pipeline."""
    print("[1/4] Pass 1: Structure rewrite...")
    result = pass1_rewrite(text)

    if passes >= 2:
        print("[2/4] Pass 2: Burstiness injection...")
        result = pass2_burstiness(result)

    if passes >= 3:
        print("[3/4] Pass 3: Final polish...")
        result = pass3_polish(result)

    print("[4/4] Post-processing...")
    result = post_process(result)

    return result


# ─── Stats ────────────────────────────────────────────────────────────

def text_stats(text):
    """Simple text statistics."""
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    words = text.split()

    if not sentences:
        return {"words": 0, "sentences": 0, "avg_sentence_len": 0}

    lengths = [len(s.split()) for s in sentences]
    return {
        "words": len(words),
        "sentences": len(sentences),
        "avg_sentence_len": round(sum(lengths) / len(lengths), 1),
        "min_sentence": min(lengths),
        "max_sentence": max(lengths),
        "unique_words": len(set(w.lower() for w in words)),
    }


# ─── Web UI ───────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HumanizeAI</title>
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
  .stats { display: flex; gap: 24px; flex-wrap: wrap; }
  .stat { text-align: center; }
  .stat-value { font-size: 20px; font-weight: 700; color: #00cc88; }
  .stat-label { font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
  .progress-bar { width: 200px; height: 4px; background: #222; border-radius: 2px; overflow: hidden; }
  .progress-fill { height: 100%; background: #00cc88; transition: width 0.3s; border-radius: 2px; }
  @media (max-width: 768px) { .panels { grid-template-columns: 1fr; } textarea { height: 250px; } }
</style>
</head>
<body>
<div class="container">
  <h1>HumanizeAI</h1>
  <p class="subtitle">Multi-pass text humanizer — paste AI text, get human-sounding output</p>

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

  <div class="controls">
    <div class="status" id="status">Ready</div>
    <div class="progress-bar" id="progressBar" style="display:none">
      <div class="progress-fill" id="progressFill" style="width:0%"></div>
    </div>
  </div>

  <div class="stats" id="stats"></div>
</div>

<script>
async function humanize() {
  const input = document.getElementById('input').value.trim();
  if (!input) { alert('Paste some text first'); return; }

  const passes = parseInt(document.getElementById('passes').value);
  const btn = document.getElementById('humanizeBtn');
  const status = document.getElementById('status');
  const progressBar = document.getElementById('progressBar');
  const progressFill = document.getElementById('progressFill');
  const output = document.getElementById('output');

  btn.disabled = true;
  progressBar.style.display = 'block';
  output.value = '';

  const steps = ['Structure rewrite...', 'Burstiness injection...', 'Final polish...', 'Post-processing...'];
  const totalSteps = passes + 1;

  try {
    const resp = await fetch('/api/humanize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: input, passes: passes})
    });

    // Stream progress
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let stepIdx = 0;

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});

      const lines = buffer.split('\\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6));
          if (data.type === 'step') {
            stepIdx = data.step;
            status.textContent = `[${stepIdx}/${totalSteps}] ${steps[Math.min(stepIdx-1, steps.length-1)]}`;
            progressFill.style.width = `${(stepIdx / totalSteps) * 100}%`;
          } else if (data.type === 'done') {
            output.value = data.text;
            status.textContent = 'Done!';
            progressFill.style.width = '100%';
            updateStats(data.input_words, data.output_words);
          } else if (data.type === 'error') {
            status.textContent = 'Error: ' + data.message;
          }
        }
      }
    }
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
  }

  btn.disabled = false;
  setTimeout(() => { progressBar.style.display = 'none'; }, 2000);
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
  document.getElementById('stats').innerHTML = '';
}

function updateStats(inWords, outWords) {
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="stat-value">${inWords}</div><div class="stat-label">Input Words</div></div>
    <div class="stat"><div class="stat-value">${outWords}</div><div class="stat-label">Output Words</div></div>
  `;
}
</script>
</body>
</html>"""


# ─── Flask Server ─────────────────────────────────────────────────────

def run_server(port=7860):
    """Run web UI server."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode())

        def do_POST(self):
            if self.path == "/api/humanize":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if length == 0:
                        self._send_error("No content received")
                        return
                    
                    body_raw = self.rfile.read(length)
                    body = json.loads(body_raw.decode("utf-8"))
                    text = body.get("text", "")
                    passes = body.get("passes", 3)
                    
                    if not text:
                        self._send_error("No text provided")
                        return
                except Exception as e:
                    self._send_error(f"Invalid request: {str(e)}")
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                try:
                    # Pass 1
                    self._send_event({"type": "step", "step": 1})
                    result = pass1_rewrite(text)

                    if passes >= 2:
                        self._send_event({"type": "step", "step": 2})
                        result = pass2_burstiness(result)

                    if passes >= 3:
                        self._send_event({"type": "step", "step": 3})
                        result = pass3_polish(result)

                    self._send_event({"type": "step", "step": passes + 1})
                    result = post_process(result)

                    self._send_event({
                        "type": "done",
                        "text": result,
                        "input_words": len(text.split()),
                        "output_words": len(result.split()),
                    })

                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    print(f"[SERVER ERROR] {type(e).__name__}: {str(e)}\n{tb}", flush=True)
                    self._send_event({"type": "error", "message": str(e)})

        def _send_event(self, data):
            chunk = f"data: {json.dumps(data)}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

        def _send_error(self, message):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": message}).encode("utf-8"))

        def log_message(self, format, *args):
            import sys
            sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"HumanizeAI running at http://localhost:{port}")
    print("Press Ctrl+C to stop")
    server.serve_forever()


# ─── CLI ──────────────────────────────────────────────────────────────

def cli():
    """Command-line interface."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python app.py                  # Start web UI")
        print("  python app.py file.txt         # Humanize file")
        print("  python app.py --text 'hello'   # Humanize inline text")
        return

    if sys.argv[1] == "--text":
        text = " ".join(sys.argv[2:])
        result = humanize(text)
        print("\n" + "=" * 60)
        print(result)
        print("=" * 60)
        return

    # File mode
    filepath = sys.argv[1]
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    stats_before = text_stats(text)
    print(f"Input: {stats_before['words']} words, {stats_before['sentences']} sentences")

    result = humanize(text)

    stats_after = text_stats(result)
    print(f"Output: {stats_after['words']} words, {stats_after['sentences']} sentences")

    outpath = filepath.rsplit(".", 1)[0] + "_humanized.txt"
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"Saved to: {outpath}")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run_server()
    else:
        cli()
