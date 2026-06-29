with open('app_v5.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 17. Drag & drop CSS
dragdrop_css = """
  /* Drag and drop zone */
  .drop-zone { position: relative; }
  .drop-zone.dragover { border-color: var(--accent) !important; background: rgba(0,204,136,0.05); }
  .drop-zone.dragover::after { content: 'Drop files here'; position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); color: var(--accent); font-size: 18px; font-weight: 600; pointer-events: none; }
  textarea.drop-zone { transition: border-color 0.2s, background 0.2s; }
"""
old_media = '  @media (max-width: 768px) {'
if old_media in content and '.drop-zone' not in content:
    content = content.replace(old_media, dragdrop_css + '\n  ' + old_media, 1)
    print('Added: drag and drop CSS')

# 18. Add drop-zone class to input textarea
old_input = '<textarea id="input" placeholder="Paste AI text here..."></textarea>'
new_input = '<textarea id="input" class="drop-zone" placeholder="Paste AI text here or drag and drop .txt/.docx files..."></textarea>'
if old_input in content and 'drop-zone' not in content:
    content = content.replace(old_input, new_input, 1)
    print('Added: drop-zone class to input')

# 19. Drag and drop JS
dragdrop_js = """
// Drag and drop for input textarea
(function() {
  var ta = document.getElementById('input');
  if(!ta) return;
  ta.addEventListener('dragover', function(e) { e.preventDefault(); ta.classList.add('dragover'); });
  ta.addEventListener('dragleave', function() { ta.classList.remove('dragover'); });
  ta.addEventListener('drop', function(e) {
    e.preventDefault();
    ta.classList.remove('dragover');
    var files = e.dataTransfer.files;
    if(files.length > 0) {
      var fd = new FormData();
      fd.append('file', files[0]);
      fetch('/api/upload', {method:'POST', body:fd})
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if(d.text) { ta.value = d.text; updateWordCount(); document.getElementById('status').textContent = 'Loaded ' + d.filename; }
          else if(d.error) document.getElementById('status').textContent = 'Error: ' + d.error;
        })
        .catch(function(e) { document.getElementById('status').textContent = 'Upload error: ' + e.message; });
    }
  });
})();
"""
if "addEventListener('drop'" not in content:
    pos = content.rfind('</script>')
    if pos > 0:
        content = content[:pos] + dragdrop_js + '\n' + content[pos:]
        print('Added: drag and drop JS')

# 20. Step progress CSS
step_css = """
  /* Step progress indicators */
  .step-progress { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; font-size: 11px; font-family: 'JetBrains Mono', monospace; }
  .step { padding: 3px 8px; border-radius: 4px; border: 1px solid var(--border); color: var(--text-muted); }
  .step.done { border-color: var(--accent); color: var(--accent); }
  .step.active { border-color: var(--accent); color: var(--accent); animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
  .step.error { border-color: #ff4444; color: #ff4444; }
"""
if old_media in content and '.step-progress' not in content:
    content = content.replace(old_media, step_css + '\n  ' + old_media, 1)
    print('Added: step progress CSS')

# 21. Step progress HTML
old_progress = '<div class="progress-bar" id="progressBar"'
new_progress = '<div class="step-progress" id="stepProgress" style="display:none;"></div>\n    <div class="progress-bar" id="progressBar"'
if old_progress in content and 'stepProgress' not in content:
    content = content.replace(old_progress, new_progress, 1)
    print('Added: step progress HTML')

# 22. Typewriter effect CSS
typewriter_css = """
  /* Typewriter effect */
  @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0; } }
  .typewriter-cursor::after { content: '|'; animation: blink 0.8s infinite; color: var(--accent); }
"""
if old_media in content and 'typewriter' not in content:
    content = content.replace(old_media, typewriter_css + '\n  ' + old_media, 1)
    print('Added: typewriter CSS')

with open('app_v5.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Batch 5: Drag and drop + step progress + typewriter CSS done')
