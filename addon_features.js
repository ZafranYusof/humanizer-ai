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
