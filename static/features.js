/**
 * HumanizeAI Features Enhancement Module
 * Adds 42 new UI features to the existing HumanizeAI page
 * Loaded as a standalone JS file, enhances DOM after page load
 */
(function() {
'use strict';

/* ═══════════════════════════════════════════════════════════════════
   NAMESPACE & STATE
   ═══════════════════════════════════════════════════════════════════ */
var HF = window.HumanizeFeatures = {
  // Undo/Redo stacks
  undoStack: [], redoStack: [],
  // Job history
  jobHistory: JSON.parse(localStorage.getItem('hf_jobHistory') || '[]'),
  // Detection history
  detectionHistory: JSON.parse(localStorage.getItem('hf_detHistory') || '[]'),
  // Draft auto-save timer
  draftTimer: null,
  // Custom model endpoints
  customModels: JSON.parse(localStorage.getItem('hf_customModels') || '[]'),
  // API keys per provider
  apiKeys: JSON.parse(localStorage.getItem('hf_apiKeys') || '{}'),
  // Fallback chain
  fallbackChain: JSON.parse(localStorage.getItem('hf_fallbackChain') || '[]'),
  // Accent color
  accentColor: localStorage.getItem('hf_accentColor') || '#6366f1',
  // State flags
  autoRetrying: false,
  modelVoting: false,
  // Charts
  charts: {},
  // Selected history items for bulk delete
  selectedHistoryIds: new Set(),
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: API CALL HELPER
   ═══════════════════════════════════════════════════════════════════ */
HF.api = function api(endpoint, body) {
  return fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).then(function(r) { return r.json(); });
};

HF.apiGet = function apiGet(endpoint) {
  return fetch(endpoint).then(function(r) { return r.json(); });
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: TOAST NOTIFICATIONS (Feature 37)
   ═══════════════════════════════════════════════════════════════════ */
HF.toast = function toast(msg, type, duration) {
  type = type || 'info';
  duration = duration || 3500;
  var container = document.getElementById('hfToastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'hfToastContainer';
    container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
    document.body.appendChild(container);
  }
  var colors = { success:'#00cc88', error:'#ff4444', warning:'#ffaa00', info:'#6366f1', ok:'#00cc88', err:'#ff4444', warn:'#ffaa00' };
  var icons = { success:'✓', error:'✗', warning:'⚠', info:'ℹ', ok:'✓', err:'✗', warn:'⚠' };
  var el = document.createElement('div');
  el.style.cssText = 'pointer-events:auto;padding:10px 18px;border-radius:8px;font-size:13px;font-family:Inter,system-ui,sans-serif;color:#fff;background:' + (colors[type]||colors.info) + ';box-shadow:0 4px 16px rgba(0,0,0,0.25);display:flex;align-items:center;gap:8px;opacity:0;transform:translateX(40px);transition:all 0.3s ease;cursor:pointer;max-width:360px;word-break:break-word;';
  el.innerHTML = '<span style="font-weight:700;font-size:15px;">' + (icons[type]||'ℹ') + '</span><span>' + HF.esc(msg) + '</span>';
  el.onclick = function() { el.style.opacity = '0'; el.style.transform = 'translateX(40px)'; setTimeout(function() { el.remove(); }, 300); };
  container.appendChild(el);
  requestAnimationFrame(function() { el.style.opacity = '1'; el.style.transform = 'translateX(0)'; });
  setTimeout(function() { el.style.opacity = '0'; el.style.transform = 'translateX(40px)'; setTimeout(function() { el.remove(); }, 300); }, duration);
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: HELPERS
   ═══════════════════════════════════════════════════════════════════ */
HF.esc = function esc(text) {
  var d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
};

HF.qs = function qs(sel) { return document.querySelector(sel); };
HF.qsa = function qsa(sel) { return document.querySelectorAll(sel); };
HF.ce = function ce(tag, attrs, html) {
  var el = document.createElement(tag);
  if (attrs) Object.keys(attrs).forEach(function(k) { el.setAttribute(k, attrs[k]); });
  if (html) el.innerHTML = html;
  return el;
};

HF.getInput = function() { return document.getElementById('input'); };
HF.getOutput = function() { return document.getElementById('output'); };
HF.getInputText = function() { var i = HF.getInput(); return i ? i.value : ''; };
HF.getOutputText = function() { var o = HF.getOutput(); return o ? (o.innerText || o.textContent || '') : ''; };

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: CANVAS CHART RENDERER
   ═══════════════════════════════════════════════════════════════════ */
HF.drawLineChart = function drawLineChart(canvas, data, opts) {
  opts = opts || {};
  var ctx = canvas.getContext('2d');
  var W = canvas.width = canvas.offsetWidth || 400;
  var H = canvas.height = canvas.offsetHeight || 200;
  var pad = { t: 30, r: 20, b: 40, l: 50 };
  ctx.clearRect(0, 0, W, H);
  if (!data.length) { ctx.fillStyle = '#666'; ctx.font = '12px Inter'; ctx.fillText('No data', W/2 - 20, H/2); return; }
  var maxVal = opts.max || Math.max.apply(null, data.map(function(d) { return d.y || d.value || 0; }));
  var minVal = opts.min || Math.min(0, Math.min.apply(null, data.map(function(d) { return d.y || d.value || 0; })));
  var range = maxVal - minVal || 1;
  // grid
  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
  for (var g = 0; g <= 4; g++) {
    var gy = pad.t + (H - pad.t - pad.b) * g / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
    ctx.fillStyle = '#666'; ctx.font = '10px Inter'; ctx.textAlign = 'right';
    ctx.fillText(Math.round(maxVal - range * g / 4), pad.l - 8, gy + 3);
  }
  // line
  var color = opts.color || '#6366f1';
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round';
  ctx.beginPath();
  data.forEach(function(d, i) {
    var x = pad.l + (W - pad.l - pad.r) * i / Math.max(data.length - 1, 1);
    var y = pad.t + (H - pad.t - pad.b) * (1 - ((d.y || d.value || 0) - minVal) / range);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  // dots
  data.forEach(function(d, i) {
    var x = pad.l + (W - pad.l - pad.r) * i / Math.max(data.length - 1, 1);
    var y = pad.t + (H - pad.t - pad.b) * (1 - ((d.y || d.value || 0) - minVal) / range);
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#999'; ctx.font = '9px Inter'; ctx.textAlign = 'center';
    ctx.fillText(d.label || '', x, H - pad.b + 14);
  });
  // title
  if (opts.title) { ctx.fillStyle = '#ccc'; ctx.font = '11px Inter'; ctx.textAlign = 'left'; ctx.fillText(opts.title, pad.l, 14); }
};

HF.drawBarChart = function drawBarChart(canvas, data, opts) {
  opts = opts || {};
  var ctx = canvas.getContext('2d');
  var W = canvas.width = canvas.offsetWidth || 400;
  var H = canvas.height = canvas.offsetHeight || 200;
  var pad = { t: 30, r: 20, b: 50, l: 50 };
  ctx.clearRect(0, 0, W, H);
  if (!data.length) { ctx.fillStyle = '#666'; ctx.font = '12px Inter'; ctx.fillText('No data', W/2 - 20, H/2); return; }
  var maxVal = opts.max || Math.max.apply(null, data.map(function(d) { return d.y || d.value || 0; }));
  var barW = (W - pad.l - pad.r) / data.length * 0.7;
  var gap = (W - pad.l - pad.r) / data.length * 0.3;
  // grid
  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
  for (var g = 0; g <= 4; g++) {
    var gy = pad.t + (H - pad.t - pad.b) * g / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
    ctx.fillStyle = '#666'; ctx.font = '10px Inter'; ctx.textAlign = 'right';
    ctx.fillText(Math.round(maxVal * (1 - g/4)), pad.l - 8, gy + 3);
  }
  var colors = opts.colors || ['#6366f1','#8b5cf6','#ec4899','#f59e0b','#10b981','#3b82f6','#ef4444','#14b8a6'];
  data.forEach(function(d, i) {
    var x = pad.l + (W - pad.l - pad.r) * i / data.length + gap / 2;
    var h = (H - pad.t - pad.b) * ((d.y || d.value || 0) / maxVal);
    var y = H - pad.b - h;
    ctx.fillStyle = colors[i % colors.length];
    ctx.fillRect(x, y, barW, h);
    ctx.fillStyle = '#999'; ctx.font = '9px Inter'; ctx.textAlign = 'center';
    ctx.save(); ctx.translate(x + barW / 2, H - pad.b + 14); ctx.rotate(-0.4);
    ctx.fillText(d.label || d.x || '', 0, 0); ctx.restore();
  });
  if (opts.title) { ctx.fillStyle = '#ccc'; ctx.font = '11px Inter'; ctx.textAlign = 'left'; ctx.fillText(opts.title, pad.l, 14); }
};

HF.drawHistogram = function drawHistogram(canvas, values, opts) {
  opts = opts || {};
  var ctx = canvas.getContext('2d');
  var W = canvas.width = canvas.offsetWidth || 400;
  var H = canvas.height = canvas.offsetHeight || 200;
  var pad = { t: 30, r: 20, b: 40, l: 50 };
  ctx.clearRect(0, 0, W, H);
  if (!values.length) { ctx.fillStyle = '#666'; ctx.font = '12px Inter'; ctx.fillText('No data', W/2 - 20, H/2); return; }
  var bins = opts.bins || 10;
  var min = Math.min.apply(null, values);
  var max = Math.max.apply(null, values);
  var binW = (max - min) / bins || 1;
  var counts = new Array(bins).fill(0);
  values.forEach(function(v) {
    var idx = Math.min(Math.floor((v - min) / binW), bins - 1);
    counts[idx]++;
  });
  var maxCount = Math.max.apply(null, counts);
  var barW = (W - pad.l - pad.r) / bins * 0.85;
  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
  for (var g = 0; g <= 4; g++) {
    var gy = pad.t + (H - pad.t - pad.b) * g / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
    ctx.fillStyle = '#666'; ctx.font = '10px Inter'; ctx.textAlign = 'right';
    ctx.fillText(Math.round(maxCount * (1 - g/4)), pad.l - 8, gy + 3);
  }
  counts.forEach(function(c, i) {
    var x = pad.l + (W - pad.l - pad.r) * i / bins;
    var h = (H - pad.t - pad.b) * (c / (maxCount || 1));
    ctx.fillStyle = '#8b5cf6';
    ctx.fillRect(x + 2, H - pad.b - h, barW, h);
    ctx.fillStyle = '#999'; ctx.font = '9px Inter'; ctx.textAlign = 'center';
    ctx.fillText(Math.round(min + binW * i) + '-' + Math.round(min + binW * (i+1)), x + barW/2 + 2, H - pad.b + 14);
  });
  if (opts.title) { ctx.fillStyle = '#ccc'; ctx.font = '11px Inter'; ctx.textAlign = 'left'; ctx.fillText(opts.title, pad.l, 14); }
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: WORD-LEVEL DIFF (Feature 3 helper)
   ═══════════════════════════════════════════════════════════════════ */
HF.wordDiff = function wordDiff(oldStr, newStr) {
  var oldW = oldStr.split(/\s+/), newW = newStr.split(/\s+/);
  // Simple LCS-based diff
  var m = oldW.length, n = newW.length;
  var dp = [];
  for (var i = 0; i <= m; i++) { dp[i] = []; for (var j = 0; j <= n; j++) { dp[i][j] = (i === 0 || j === 0) ? 0 : (oldW[i-1] === newW[j-1] ? dp[i-1][j-1] + 1 : Math.max(dp[i-1][j], dp[i][j-1])); } }
  var result = [];
  var ii = m, jj = n;
  while (ii > 0 || jj > 0) {
    if (ii > 0 && jj > 0 && oldW[ii-1] === newW[jj-1]) { result.unshift({ type: 'same', text: oldW[ii-1] }); ii--; jj--; }
    else if (jj > 0 && (ii === 0 || dp[ii][jj-1] >= dp[ii-1][jj])) { result.unshift({ type: 'add', text: newW[jj-1] }); jj--; }
    else { result.unshift({ type: 'del', text: oldW[ii-1] }); ii--; }
  }
  return result;
};

HF.renderDiff = function renderDiff(diff) {
  return diff.map(function(d) {
    if (d.type === 'add') return '<span style="background:#1a3a2a;color:#4ade80;padding:1px 2px;border-radius:2px;">' + HF.esc(d.text) + '</span>';
    if (d.type === 'del') return '<span style="background:#3a1a1a;color:#f87171;padding:1px 2px;border-radius:2px;text-decoration:line-through;">' + HF.esc(d.text) + '</span>';
    return HF.esc(d.text);
  }).join(' ');
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: READABILITY SCORES (Feature 10 helper)
   ═══════════════════════════════════════════════════════════════════ */
HF.calcReadability = function calcReadability(text) {
  if (!text || !text.trim()) return { fk: 0, fog: 0 };
  var sentences = text.split(/[.!?]+/).filter(function(s) { return s.trim().length > 0; });
  var words = text.split(/\s+/).filter(function(w) { return w.length > 0; });
  var syllables = 0;
  words.forEach(function(w) {
    w = w.toLowerCase().replace(/[^a-z]/g, '');
    if (!w) return;
    var s = 0, prevVowel = false;
    var vowels = 'aeiouy';
    for (var i = 0; i < w.length; i++) {
      var isV = vowels.indexOf(w[i]) >= 0;
      if (isV && !prevVowel) s++;
      prevVowel = isV;
    }
    if (w.endsWith('e') && s > 1) s--;
    syllables += Math.max(s, 1);
  });
  var sw = 0;
  words.forEach(function(w) {
    w = w.toLowerCase().replace(/[^a-z]/g, '');
    var s = 0, prevV = false;
    var v = 'aeiouy';
    for (var i = 0; i < w.length; i++) { var isV = v.indexOf(w[i]) >= 0; if (isV && !prevV) s++; prevV = isV; }
    if (w.endsWith('e') && s > 1) s--;
    if (s >= 3) sw++;
  });
  var N = words.length, S = sentences.length || 1;
  var fk = 0.39 * (N / S) + 11.8 * (syllables / N) - 15.59;
  var fog = 0.4 * ((N / S) + 100 * (sw / N));
  return { fk: Math.round(fk * 10) / 10, fog: Math.round(fog * 10) / 10 };
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: LANGUAGE DETECTION (Feature 36 helper)
   ═══════════════════════════════════════════════════════════════════ */
HF.detectLanguage = function detectLanguage(text) {
  if (!text || text.length < 20) return { lang: 'unknown', flag: '🌐' };
  var sample = text.substring(0, 2000).toLowerCase();
  var patterns = {
    'en': { flag: '🇺🇸', re: /\b(the|is|are|was|were|have|has|been|with|this|that|for)\b/g },
    'es': { flag: '🇪🇸', re: /\b(el|la|los|las|es|son|están|con|por|para|una|que)\b/g },
    'fr': { flag: '🇫🇷', re: /\b(le|la|les|est|sont|avec|pour|des|une|que|pas)\b/g },
    'de': { flag: '🇩🇪', re: /\b(der|die|das|ist|sind|mit|für|ein|eine|und|nicht)\b/g },
    'pt': { flag: '🇧🇷', re: /\b(o|a|os|as|é|são|com|para|uma|que|não)\b/g },
    'it': { flag: '🇮🇹', re: /\b(il|la|lo|è|sono|con|per|una|che|non|del)\b/g },
    'zh': { flag: '🇨🇳', re: /[\u4e00-\u9fff]{3,}/g },
    'ja': { flag: '🇯🇵', re: /[\u3040-\u309f\u30a0-\u30ff]{3,}/g },
    'ko': { flag: '🇰🇷', re: /[\uac00-\ud7af]{3,}/g },
    'ar': { flag: '🇸🇦', re: /[\u0600-\u06ff]{3,}/g },
    'ru': { flag: '🇷🇺', re: /[\u0400-\u04ff]{3,}/g },
  };
  var best = 'en', bestCount = 0;
  Object.keys(patterns).forEach(function(lang) {
    var m = sample.match(patterns[lang].re);
    var c = m ? m.length : 0;
    if (c > bestCount) { bestCount = c; best = lang; }
  });
  return { lang: best, flag: patterns[best].flag };
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: PASSIVE VOICE DETECTION (Feature 9 helper)
   ═══════════════════════════════════════════════════════════════════ */
HF.convertPassiveToActive = function(text) {
  // Simple regex-based passive to active conversion
  var passiveRe = /\b(is|are|was|were|been|being|be)\s+(\w+ed)\b/gi;
  var result = text.replace(passiveRe, function(match, aux, pastPart) {
    // Capitalize first letter of past participle to make it active
    return pastPart.charAt(0).toUpperCase() + pastPart.slice(1) + ' (was ' + aux + ')';
  });
  return result;
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: CITATION DETECTION (Feature 6 helper)
   ═══════════════════════════════════════════════════════════════════ */
HF.findCitations = function(text) {
  var patterns = [
    /\(\w+,?\s*\d{4}\)/g,           // (Author, 2024)
    /\[\d+\]/g,                       // [1], [2]
    /\bet al\.\s*\(\d{4}\)/gi,       // et al. (2024)
    /(?:doi|DOI):\s*10\.\d{4,}\/[^\s]+/g,  // DOI
    /https?:\/\/[^\s<>"{}|\\^`\[\]]+/gi,    // URLs
    /\b(?:pp?\.?\s*\d+[-–]\d+)\b/g,        // pp. 12-15
    /"(?:[^"\\]|\\.)*"/g,                    // Quoted strings (short ones)
  ];
  var ranges = [];
  patterns.forEach(function(re) {
    var m;
    while ((m = re.exec(text)) !== null) {
      ranges.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
    }
  });
  // Deduplicate overlapping
  ranges.sort(function(a,b) { return a.start - b.start; });
  var merged = [];
  ranges.forEach(function(r) {
    if (merged.length && r.start < merged[merged.length-1].end) {
      if (r.end > merged[merged.length-1].end) merged[merged.length-1].end = r.end;
    } else merged.push(r);
  });
  return merged;
};

/* ═══════════════════════════════════════════════════════════════════
   UTILITY: STORAGE HELPERS
   ═══════════════════════════════════════════════════════════════════ */
HF.saveJobs = function() { localStorage.setItem('hf_jobHistory', JSON.stringify(HF.jobHistory)); };
HF.saveDetHistory = function() { localStorage.setItem('hf_detHistory', JSON.stringify(HF.detectionHistory)); };
HF.saveCustomModels = function() { localStorage.setItem('hf_customModels', JSON.stringify(HF.customModels)); };
HF.saveApiKeys = function() { localStorage.setItem('hf_apiKeys', JSON.stringify(HF.apiKeys)); };
HF.saveFallbackChain = function() { localStorage.setItem('hf_fallbackChain', JSON.stringify(HF.fallbackChain)); };

HF.addJob = function(job) {
  HF.jobHistory.unshift(job);
  if (HF.jobHistory.length > 200) HF.jobHistory.length = 200;
  HF.saveJobs();
};

HF.addDetEntry = function(entry) {
  HF.detectionHistory.push(entry);
  if (HF.detectionHistory.length > 500) HF.detectionHistory.shift();
  HF.saveDetHistory();
};

/* ═══════════════════════════════════════════════════════════════════
   INJECT CSS
   ═══════════════════════════════════════════════════════════════════ */
HF.injectCSS = function injectCSS() {
  var style = document.createElement('style');
  style.textContent = [
    '/* HumanizeFeatures enhanced styles */',
    '.hf-section { margin: 16px 0; padding: 16px; border: 1px solid var(--border, #333); border-radius: 10px; background: var(--paper, #1a1a1a); }',
    '.hf-section-title { font-family: IBM Plex Mono, monospace; font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-muted, #888); margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }',
    '.hf-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }',
    '.hf-row-between { display: flex; justify-content: space-between; align-items: center; margin: 8px 0; }',
    '.hf-btn { padding: 7px 14px; border: 1px solid var(--border, #444); border-radius: 6px; background: var(--bg-secondary, #222); color: var(--text, #eee); font-size: 12px; cursor: pointer; font-family: Inter, system-ui, sans-serif; transition: all 0.15s; }',
    '.hf-btn:hover { background: var(--accent, #6366f1); color: #fff; border-color: var(--accent, #6366f1); }',
    '.hf-btn:disabled { opacity: 0.5; cursor: not-allowed; }',
    '.hf-btn-primary { background: var(--accent, #6366f1); color: #fff; border-color: var(--accent, #6366f1); }',
    '.hf-btn-primary:hover { filter: brightness(1.2); }',
    '.hf-btn-danger { background: #ef4444; color: #fff; border-color: #ef4444; }',
    '.hf-btn-danger:hover { filter: brightness(1.2); }',
    '.hf-btn-success { background: #10b981; color: #fff; border-color: #10b981; }',
    '.hf-slider-wrap { display: flex; align-items: center; gap: 10px; width: 100%; }',
    '.hf-slider-wrap input[type=range] { flex: 1; accent-color: var(--accent, #6366f1); }',
    '.hf-slider-wrap .hf-slider-val { font-size: 12px; font-weight: 600; min-width: 60px; text-align: right; color: var(--text, #eee); }',
    '.hf-slider-label { font-size: 11px; color: var(--text-muted, #888); min-width: 80px; }',
    '.hf-select { padding: 6px 10px; border: 1px solid var(--border, #444); border-radius: 6px; background: var(--bg-secondary, #222); color: var(--text, #eee); font-size: 12px; font-family: Inter, system-ui, sans-serif; }',
    '.hf-checkbox { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text, #eee); cursor: pointer; }',
    '.hf-checkbox input { accent-color: var(--accent, #6366f1); }',
    '.hf-toggle { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border: 1px solid var(--border, #444); border-radius: 20px; font-size: 12px; cursor: pointer; transition: all 0.2s; color: var(--text-muted, #888); }',
    '.hf-toggle.active { background: var(--accent, #6366f1); color: #fff; border-color: var(--accent, #6366f1); }',
    '.hf-table { width: 100%; border-collapse: collapse; font-size: 12px; }',
    '.hf-table th { padding: 8px 10px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted, #888); border-bottom: 1px solid var(--border, #333); }',
    '.hf-table td { padding: 8px 10px; border-bottom: 1px solid var(--border, #222); color: var(--text, #eee); }',
    '.hf-table tr:hover td { background: var(--bg-secondary, #1e1e1e); }',
    '.hf-card { padding: 14px; border: 1px solid var(--border, #333); border-radius: 8px; background: var(--bg-secondary, #1e1e1e); }',
    '.hf-card-value { font-size: 24px; font-weight: 700; color: var(--text, #eee); }',
    '.hf-card-label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted, #888); margin-top: 4px; }',
    '.hf-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }',
    '.hf-grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }',
    '.hf-grid-4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 12px; }',
    '.hf-diff-container { padding: 12px; font-size: 13px; line-height: 1.8; border: 1px solid var(--border, #333); border-radius: 8px; background: var(--bg-secondary, #1a1a1a); max-height: 300px; overflow-y: auto; }',
    '.hf-undo-redo { display: flex; gap: 6px; margin: 8px 0; }',
    '.hf-toolbar { display: flex; gap: 4px; padding: 6px; border: 1px solid var(--border, #333); border-bottom: none; border-radius: 8px 8px 0 0; background: var(--bg-secondary, #1e1e1e); flex-wrap: wrap; }',
    '.hf-toolbar button { padding: 4px 8px; border: 1px solid var(--border, #444); border-radius: 4px; background: transparent; color: var(--text, #eee); font-size: 12px; cursor: pointer; }',
    '.hf-toolbar button:hover { background: var(--accent, #6366f1); color: #fff; }',
    '.hf-rich-editor { border: 1px solid var(--border, #333); border-radius: 0 0 8px 8px; padding: 12px; min-height: 150px; outline: none; font-family: Inter, system-ui, sans-serif; font-size: 14px; color: var(--text, #eee); background: var(--paper, #1a1a1a); }',
    '.hf-rich-editor:focus { border-color: var(--accent, #6366f1); }',
    '.hf-panel-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 300; display: flex; align-items: center; justify-content: center; }',
    '.hf-panel { background: var(--paper, #1a1a1a); border: 1px solid var(--border, #333); border-radius: 12px; padding: 24px; max-width: 700px; width: 92%; max-height: 85vh; overflow-y: auto; box-shadow: 0 12px 40px rgba(0,0,0,0.4); }',
    '.hf-panel-title { font-family: IBM Plex Mono, monospace; font-size: 13px; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }',
    '.hf-history-item { padding: 10px 12px; border-bottom: 1px solid var(--border, #222); cursor: pointer; transition: background 0.15s; }',
    '.hf-history-item:hover { background: var(--bg-secondary, #1e1e1e); }',
    '.hf-history-preview { font-size: 12px; color: var(--text, #eee); margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }',
    '.hf-history-meta { font-size: 10px; color: var(--text-muted, #666); }',
    '.hf-search { width: 100%; padding: 8px 12px; border: 1px solid var(--border, #444); border-radius: 6px; background: var(--bg-secondary, #222); color: var(--text, #eee); font-size: 13px; margin-bottom: 10px; outline: none; }',
    '.hf-search:focus { border-color: var(--accent, #6366f1); }',
    '.hf-canvas-wrap { border: 1px solid var(--border, #333); border-radius: 8px; padding: 8px; background: var(--bg-secondary, #111); }',
    '.hf-canvas-wrap canvas { width: 100%; height: 200px; display: block; }',
    '.hf-flagged { background: #fef3c7; color: #92400e; padding: 2px 4px; border-radius: 3px; }',
    '.hf-citation { background: #fde68a; color: #92400e; padding: 1px 2px; border-radius: 2px; }',
    '.hf-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; }',
    '.hf-badge-green { background: #065f46; color: #6ee7b7; }',
    '.hf-badge-yellow { background: #78350f; color: #fcd34d; }',
    '.hf-badge-red { background: #7f1d1d; color: #fca5a5; }',
    '.hf-lang-flag { font-size: 18px; margin-right: 6px; }',
    '.hf-domain-btns { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }',
    '.hf-quality-btns { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }',
    '.hf-footer-bar { display: flex; gap: 16px; align-items: center; padding: 8px 16px; font-size: 11px; color: var(--text-muted, #666); border-top: 1px solid var(--border, #222); flex-wrap: wrap; }',
    '.hf-footer-bar span { white-space: nowrap; }',
    '.hf-ptr-para { padding: 8px; margin-bottom: 6px; border: 1px solid var(--border, #333); border-radius: 6px; position: relative; }',
    '.hf-ptr-para-btn { position: absolute; top: 4px; right: 4px; }',
    '@media (max-width: 768px) { .hf-grid-2,.hf-grid-3,.hf-grid-4 { grid-template-columns: 1fr; } }',
  ].join('\n');
  document.head.appendChild(style);
};


/* ═══════════════════════════════════════════════════════════════════
   FEATURE IMPLEMENTATIONS
   ═══════════════════════════════════════════════════════════════════ */

// ── UTILITY: Create element helper ──
HF.el = function(tag, attrs, children) {
  var e = document.createElement(tag);
  if (attrs) Object.keys(attrs).forEach(function(k) {
    if (k === 'style' && typeof attrs[k] === 'object') Object.assign(e.style, attrs[k]);
    else if (k === 'className') e.className = attrs[k];
    else if (k.startsWith('on')) e.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
    else e.setAttribute(k, attrs[k]);
  });
  if (children) {
    if (typeof children === 'string') e.textContent = children;
    else if (Array.isArray(children)) children.forEach(function(c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    else e.appendChild(children);
  }
  return e;
};

// ── UTILITY: Find insertion point ──
HF.findInsertPoint = function() {
  return document.querySelector('#output') || document.querySelector('.output-section') || document.querySelector('main') || document.body;
};

HF.getInputText = function() {
  var el = document.querySelector('#input') || document.querySelector('textarea');
  return el ? el.value : '';
};

HF.getOutputText = function() {
  var el = document.querySelector('#output');
  if (!el) return '';
  return el.innerText || el.textContent || '';
};

HF.createSection = function(title, id) {
  var sec = HF.el('div', {id: id, className: 'hf-section'});
  var hdr = HF.el('div', {className: 'hf-section-title'}, title);
  sec.appendChild(hdr);
  return sec;
};

HF.createButton = function(text, onClick, cls) {
  return HF.el('button', {className: 'hf-btn ' + (cls||''), onClick: onClick}, text);
};

/* ═══════════════════════════════════════════════════════════════════
   GROUP 1: TEXT PROCESSING UI
   ═══════════════════════════════════════════════════════════════════ */

HF.strengthLevel = 'medium';
HF.preserveFormatting = false;

HF.initStrengthSlider = function() {
  var wrap = HF.el('div', {id: 'hf-strength-wrap', className: 'hf-control-row'});
  var lbl = HF.el('label', {}, 'Strength: ');
  var val = HF.el('span', {id: 'hf-strength-val'}, 'Medium');
  var slider = HF.el('input', {type: 'range', min: '0', max: '2', value: '1', id: 'hf-strength-slider', style: {width: '120px'}});
  slider.addEventListener('input', function() {
    var levels = ['Light', 'Medium', 'Aggressive'];
    HF.strengthLevel = levels[slider.value].toLowerCase();
    val.textContent = levels[slider.value];
  });
  wrap.append(lbl, slider, val);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.insertBefore(wrap, ta.nextSibling);
};

HF.initPerParagraph = function() {
  var btn = HF.createButton('¶ Per-Paragraph', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text to split', 'warn'); return; }
    var paras = text.split(/\n\s*\n/).filter(function(p) { return p.trim(); });
    var wrap = document.getElementById('hf-para-wrap') || HF.el('div', {id: 'hf-para-wrap'});
    wrap.innerHTML = '';
    paras.forEach(function(p, i) {
      var row = HF.el('div', {className: 'hf-ptr-para'});
      row.appendChild(HF.el('span', {}, p.trim().substring(0, 120) + (p.length > 120 ? '...' : '')));
      var pbtn = HF.createButton('Humanize', function() {
        HF.api('/api/text-processing/paragraph', {text: text, paragraph: p.trim(), idx: i}).then(function(r) {
          if (r.result) { HF.toast('Paragraph ' + (i+1) + ' humanized'); row.querySelector('span').textContent = r.result.substring(0, 120); }
        }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
      }, 'hf-btn-sm');
      row.appendChild(pbtn);
      wrap.appendChild(row);
    });
    var output = HF.findInsertPoint();
    if (!document.getElementById('hf-para-wrap')) output.parentNode.insertBefore(wrap, output.nextSibling);
  });
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta) { var p = ta.parentNode; p.insertBefore(btn, ta.nextSibling); }
};

HF.initDiffView = function() {
  var diffDiv = HF.el('div', {id: 'hf-diff-view', className: 'hf-diff-container', style: {display: 'none'}});
  var btn = HF.createButton('⇄ Show Diff', function() {
    var orig = HF.getInputText();
    var human = HF.getOutputText();
    if (!orig || !human) { HF.toast('Need both input and output', 'warn'); return; }
    HF.api('/api/text-processing/diff', {original: orig, humanized: human}).then(function(r) {
      if (!r.diff) { HF.toast('No diff data', 'warn'); return; }
      diffDiv.innerHTML = '';
      r.diff.forEach(function(d) {
        var sp = HF.el('span', {}, d.text);
        if (d.type === 'added') { sp.style.color = '#4ade80'; sp.style.fontWeight = '600'; }
        else if (d.type === 'removed') { sp.style.color = '#fb7185'; sp.style.textDecoration = 'line-through'; }
        diffDiv.appendChild(sp);
        diffDiv.appendChild(document.createTextNode(' '));
      });
      diffDiv.style.display = 'block';
    }).catch(function(e) { HF.toast('Diff error: ' + e.message, 'error'); });
  });
  var output = document.getElementById('output');
  if (output) { output.parentNode.insertBefore(btn, output); output.parentNode.insertBefore(diffDiv, output.nextSibling); }
};

HF.initUndoRedo = function() {
  var wrap = HF.el('div', {id: 'hf-undo-redo', className: 'hf-undo-redo'});
  var undoBtn = HF.createButton('↩ Undo', function() {
    if (HF.undoStack.length < 2) { HF.toast('Nothing to undo', 'warn'); return; }
    HF.redoStack.push(HF.undoStack.pop());
    var prev = HF.undoStack[HF.undoStack.length - 1];
    var out = document.getElementById('output');
    if (out) out.innerHTML = prev;
  }, 'hf-btn-sm');
  var redoBtn = HF.createButton('↪ Redo', function() {
    if (!HF.redoStack.length) { HF.toast('Nothing to redo', 'warn'); return; }
    var next = HF.redoStack.pop();
    HF.undoStack.push(next);
    var out = document.getElementById('output');
    if (out) out.innerHTML = next;
  }, 'hf-btn-sm');
  wrap.append(undoBtn, redoBtn);
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(wrap, output);
  // Hook into humanize to track changes
  var origBtn = document.getElementById('humanizeBtn');
  if (origBtn) {
    origBtn.addEventListener('click', function() {
      setTimeout(function() {
        var out = document.getElementById('output');
        if (out) HF.undoStack.push(out.innerHTML);
      }, 3000);
    });
  }
};

HF.initPreserveFormatting = function() {
  var wrap = HF.el('div', {id: 'hf-preserve-wrap', className: 'hf-control-row'});
  var cb = HF.el('input', {type: 'checkbox', id: 'hf-preserve-cb'});
  cb.addEventListener('change', function() { HF.preserveFormatting = cb.checked; });
  var lbl = HF.el('label', {htmlFor: 'hf-preserve-cb'}, ' Preserve formatting (bullets, lists, headers)');
  wrap.append(cb, lbl);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.insertBefore(wrap, ta.parentNode.querySelector('.hf-control-row') || ta.nextSibling);
};

HF.initCitationDetector = function() {
  var btn = HF.createButton('📝 Detect Citations', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.apiGet('/api/text-processing/citations?text=' + encodeURIComponent(text)).then(function(r) {
      if (!r.citations || !r.citations.length) { HF.toast('No citations found'); return; }
      var ta = document.querySelector('#input') || document.querySelector('textarea');
      var overlay = HF.el('div', {id: 'hf-citation-overlay', style: {padding: '12px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: '8px', marginTop: '8px', maxHeight: '200px', overflowY: 'auto'}});
      overlay.innerHTML = '<strong>Found ' + r.citations.length + ' citations:</strong><br>';
      r.citations.forEach(function(c) {
        overlay.innerHTML += '<span class="hf-citation">' + c.text + '</span> (pos ' + c.start + ')<br>';
      });
      var old = document.getElementById('hf-citation-overlay');
      if (old) old.remove();
      if (ta && ta.parentNode) ta.parentNode.insertBefore(overlay, ta.nextSibling);
      HF.toast(r.citations.length + ' citations found');
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  });
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta) ta.parentNode.insertBefore(btn, ta.nextSibling);
};

HF.initCodeBlockProtection = function() {
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (!ta) return;
  var warn = HF.el('div', {id: 'hf-code-warn', style: {display: 'none', padding: '8px', background: '#78350f', color: '#fcd34d', borderRadius: '6px', fontSize: '12px', marginTop: '6px'}});
  warn.textContent = '⚠ Code blocks detected — they will be skipped during humanization';
  ta.addEventListener('input', function() {
    var blocks = ta.value.match(/```[\s\S]*?```/g) || [];
    warn.style.display = blocks.length ? 'block' : 'none';
    warn.textContent = '⚠ ' + blocks.length + ' code block(s) detected — will be skipped';
  });
  ta.parentNode.insertBefore(warn, ta.nextSibling);
};

HF.initParagraphReorder = function() {
  var btn = HF.createButton('🔀 Reorder Paragraphs', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.api('/api/text-processing/reorder', {text: text}).then(function(r) {
      if (r.result) {
        var ta = document.querySelector('#input') || document.querySelector('textarea');
        if (ta) ta.value = r.result;
        HF.toast('Paragraphs reordered');
      }
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  });
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta) ta.parentNode.insertBefore(btn, ta.nextSibling);
};

HF.initPassiveActive = function() {
  var btn = HF.createButton('🔄 Passive→Active', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.toast('Converting passive voice...');
    HF.api('/api/text-processing/passive-to-active', {text: text}).then(function(r) {
      if (r.result) {
        var ta = document.querySelector('#input') || document.querySelector('textarea');
        if (ta) ta.value = r.result;
        HF.toast('Passive voice converted');
      }
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  });
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta) ta.parentNode.insertBefore(btn, ta.nextSibling);
};

HF.initReadabilityDisplay = function() {
  var div = HF.el('div', {id: 'hf-readability', className: 'hf-card', style: {display: 'none', marginTop: '12px'}});
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(div, output.nextSibling);
  // Auto-trigger on humanize complete
  var observer = new MutationObserver(function() {
    var out = document.getElementById('output');
    if (out && out.textContent.trim().length > 50) {
      HF.apiGet('/api/text-processing/readability?text=' + encodeURIComponent(out.textContent)).then(function(r) {
        div.innerHTML = '<div class="hf-grid-3">' +
          '<div><div class="hf-card-value">' + (r.flesch_kincaid_grade || '--') + '</div><div class="hf-card-label">Flesch-Kincaid</div></div>' +
          '<div><div class="hf-card-value">' + (r.gunning_fog || '--') + '</div><div class="hf-card-label">Gunning Fog</div></div>' +
          '<div><div class="hf-card-value">' + (r.coleman_liau_index || '--') + '</div><div class="hf-card-label">Coleman-Liau</div></div>' +
          '</div>';
        div.style.display = 'block';
      }).catch(function() {});
    }
  });
  if (output) observer.observe(output, {childList: true, subtree: true});
};

HF.initToneMix = function() {
  var wrap = HF.el('div', {id: 'hf-tonemix', className: 'hf-control-row', style: {marginTop: '8px'}});
  var tones = ['formal', 'casual', 'academic', 'creative', 'persuasive', 'technical'];
  var sel1 = HF.el('select', {id: 'hf-tone1', style: {padding: '4px', borderRadius: '4px', background: 'var(--bg-secondary)', color: 'var(--text)', border: '1px solid var(--border)'}});
  var sel2 = HF.el('select', {id: 'hf-tone2', style: {padding: '4px', borderRadius: '4px', background: 'var(--bg-secondary)', color: 'var(--text)', border: '1px solid var(--border)'}});
  tones.forEach(function(t) { sel1.appendChild(HF.el('option', {value: t}, t)); sel2.appendChild(HF.el('option', {value: t}, t)); });
  sel2.selectedIndex = 1;
  var ratio = HF.el('input', {type: 'range', min: '0', max: '100', value: '50', id: 'hf-tone-ratio', style: {width: '80px'}});
  var ratioVal = HF.el('span', {}, '50%');
  ratio.addEventListener('input', function() { ratioVal.textContent = ratio.value + '%'; });
  var btn = HF.createButton('Mix Tones', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.api('/api/text-processing/tone-mix', {text: text, primary: sel1.value, secondary: sel2.value, ratio: parseInt(ratio.value)/100}).then(function(r) {
      if (r.result) { var out = document.getElementById('output'); if (out) out.textContent = r.result; HF.toast('Tone mixed'); }
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  }, 'hf-btn-sm');
  wrap.append('Tone: ', sel1, ' + ', sel2, ' ', ratio, ratioVal, ' ', btn);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.appendChild(wrap);
};

HF.initFormalityDial = function() {
  var wrap = HF.el('div', {id: 'hf-formality', className: 'hf-control-row', style: {marginTop: '8px'}});
  var lbl = HF.el('label', {}, 'Formality: ');
  var val = HF.el('span', {id: 'hf-formality-val'}, '50%');
  var slider = HF.el('input', {type: 'range', min: '0', max: '100', value: '50', id: 'hf-formality-slider', style: {width: '120px'}});
  slider.addEventListener('input', function() { val.textContent = slider.value + '%'; });
  var btn = HF.createButton('Apply', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.api('/api/text-processing/formality', {text: text, level: parseInt(slider.value)}).then(function(r) {
      if (r.result) { var out = document.getElementById('output'); if (out) out.textContent = r.result; HF.toast('Formality adjusted'); }
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  }, 'hf-btn-sm');
  wrap.append(lbl, slider, val, ' ', btn);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.appendChild(wrap);
};

/* ═══════════════════════════════════════════════════════════════════
   GROUP 2: DETECTION & SCORING
   ═══════════════════════════════════════════════════════════════════ */

HF.initMultiDetector = function() {
  var btn = HF.createButton('🔍 Multi-Detect', function() {
    var text = HF.getOutputText() || HF.getInputText();
    if (!text.trim()) { HF.toast('No text to detect', 'warn'); return; }
    HF.toast('Running multi-detector...');
    HF.api('/api/detection/multi', {text: text}).then(function(r) {
      var div = document.getElementById('hf-multi-detector') || HF.el('div', {id: 'hf-multi-detector', className: 'hf-card', style: {marginTop: '12px'}});
      var rows = '';
      ['zerogpt', 'gptzero', 'copyLeaks'].forEach(function(d) {
        var det = r[d] || {};
        var score = det.score != null ? det.score + '%' : 'N/A';
        var status = det.error ? '❌ ' + det.error : (det.score < 30 ? '✅ Human' : det.score < 60 ? '⚠️ Mixed' : '🤖 AI');
        rows += '<tr><td>' + d + '</td><td>' + score + '</td><td>' + status + '</td></tr>';
      });
      div.innerHTML = '<strong>Multi-Detector Results</strong><table class="hf-table"><tr><th>Detector</th><th>Score</th><th>Status</th></tr>' + rows + '</table>' +
        '<div style="margin-top:8px;font-size:13px">Consensus: <strong>' + (r.consensus != null ? r.consensus.toFixed(1) + '%' : 'N/A') + '</strong></div>';
      var output = document.getElementById('output');
      if (output) output.parentNode.insertBefore(div, output.nextSibling);
    }).catch(function(e) { HF.toast('Detection error: ' + e.message, 'error'); });
  });
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(btn, output);
};

HF.initDetectorComparison = function() {
  var btn = HF.createButton('📊 Compare Detectors', function() {
    var text = HF.getOutputText() || HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.apiGet('/api/detection/compare?text=' + encodeURIComponent(text)).then(function(r) {
      var div = document.getElementById('hf-det-compare') || HF.el('div', {id: 'hf-det-compare', className: 'hf-card', style: {marginTop: '12px'}});
      var rows = '';
      (r || []).forEach(function(d) {
        rows += '<tr><td>' + d.detector + '</td><td>' + (d.ai_prob || '--') + '%</td><td>' + (d.human_prob || '--') + '%</td></tr>';
      });
      div.innerHTML = '<strong>Detector Comparison</strong><table class="hf-table"><tr><th>Detector</th><th>AI %</th><th>Human %</th></tr>' + rows + '</table>';
      var output = document.getElementById('output');
      if (output) output.parentNode.insertBefore(div, output.nextSibling);
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  });
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(btn, output);
};

HF.initDetectionHistory = function() {
  var wrap = HF.el('div', {id: 'hf-det-history', className: 'hf-canvas-wrap', style: {marginTop: '12px', display: 'none'}});
  var canvas = HF.el('canvas', {width: '600', height: '200'});
  wrap.appendChild(canvas);
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(wrap, output.nextSibling);
};

HF.initAutoRetry = function() {
  var wrap = HF.el('div', {id: 'hf-autoretry', className: 'hf-control-row', style: {marginTop: '8px'}});
  var targetInput = HF.el('input', {type: 'number', value: '20', min: '5', max: '80', style: {width: '50px', padding: '4px', borderRadius: '4px', background: 'var(--bg-secondary)', color: 'var(--text)', border: '1px solid var(--border)'}});
  var btn = HF.createButton('🔁 Auto-Retry', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.autoRetrying = true;
    HF.toast('Auto-retrying until score < ' + targetInput.value + '%...');
    HF.api('/api/detection/auto-retry', {text: text, target_score: parseInt(targetInput.value), max_retries: 5}).then(function(r) {
      HF.autoRetrying = false;
      if (r.result) {
        var out = document.getElementById('output');
        if (out) out.textContent = r.result;
        HF.toast('Achieved score: ' + (r.score || '?') + '% after ' + (r.attempts || '?') + ' attempts');
      } else { HF.toast('Could not reach target score', 'warn'); }
    }).catch(function(e) { HF.autoRetrying = false; HF.toast('Error: ' + e.message, 'error'); });
  }, 'hf-btn-sm');
  wrap.append('Target < ', targetInput, '% ', btn);
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(wrap, output);
};

HF.initScorePrediction = function() {
  var btn = HF.createButton('🎯 Predict Score', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.apiGet('/api/detection/predict?text=' + encodeURIComponent(text)).then(function(r) {
      var div = document.getElementById('hf-score-pred') || HF.el('div', {id: 'hf-score-pred', className: 'hf-card', style: {marginTop: '8px'}});
      div.innerHTML = 'Predicted AI score: <strong>' + (r.predicted_score != null ? r.predicted_score.toFixed(1) + '%' : 'N/A') + '</strong>';
      var ta = document.querySelector('#input') || document.querySelector('textarea');
      if (ta) { var old = document.getElementById('hf-score-pred'); if (old) old.remove(); ta.parentNode.insertBefore(div, ta.nextSibling); }
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  });
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta) ta.parentNode.insertBefore(btn, ta.nextSibling);
};

HF.initFlaggedSentences = function() {
  var div = HF.el('div', {id: 'hf-flagged', style: {display: 'none', marginTop: '8px'}});
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(div, output.nextSibling);
  var btn = HF.createButton('🚩 Flagged Sentences', function() {
    var text = HF.getOutputText();
    if (!text.trim()) { HF.toast('No output text', 'warn'); return; }
    HF.api('/api/detection/flagged', {text: text, detector_results: {}}).then(function(r) {
      if (!r.sentences || !r.sentences.length) { HF.toast('No flagged sentences'); return; }
      div.innerHTML = '<strong>Flagged Sentences:</strong><br>';
      r.sentences.forEach(function(s) {
        if (s.is_flagged) div.innerHTML += '<div class="hf-flagged" style="margin:4px 0">' + s.sentence + '</div>';
      });
      div.style.display = 'block';
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  });
  var output2 = document.getElementById('output');
  if (output2) output2.parentNode.insertBefore(btn, output2);
};

/* ═══════════════════════════════════════════════════════════════════
   GROUP 3: HISTORY & ANALYTICS
   ═══════════════════════════════════════════════════════════════════ */

HF.initHistoryPanel = function() {
  var btn = HF.createButton('📋 History', function() {
    var panel = document.getElementById('hf-history-panel');
    if (panel) { panel.style.display = panel.style.display === 'none' ? 'block' : 'none'; return; }
    panel = HF.el('div', {id: 'hf-history-panel', className: 'hf-panel-overlay'});
    var inner = HF.el('div', {className: 'hf-panel'});
    inner.innerHTML = '<div class="hf-panel-title"><span>JOB HISTORY</span><button onclick="this.closest(\'.hf-panel-overlay\').remove()" style="background:none;border:none;color:var(--text);cursor:pointer;font-size:18px">✕</button></div>';
    var search = HF.el('input', {className: 'hf-search', placeholder: 'Search history...', type: 'text'});
    inner.appendChild(search);
    var list = HF.el('div', {id: 'hf-history-list'});
    inner.appendChild(list);
    var bulkBtn = HF.createButton('🗑 Delete Selected', function() {
      var ids = Array.from(HF.selectedHistoryIds);
      if (!ids.length) { HF.toast('No items selected', 'warn'); return; }
      HF.api('/api/history/bulk-delete', {ids: ids}).then(function(r) { HF.toast('Deleted ' + ids.length + ' items'); HF.selectedHistoryIds.clear(); loadHistory(); }).catch(function(e) { HF.toast('Error', 'error'); });
    }, 'hf-btn-sm');
    var exportBtn = HF.createButton('📥 Export CSV', function() { window.open('/api/history/export-csv', '_blank'); }, 'hf-btn-sm');
    var btns = HF.el('div', {style: {marginBottom: '10px'}});
    btns.append(bulkBtn, ' ', exportBtn);
    inner.appendChild(btns);
    panel.appendChild(inner);
    document.body.appendChild(panel);
    function loadHistory(q) {
      var url = q ? '/api/history/search?q=' + encodeURIComponent(q) : '/api/history';
      HF.apiGet(url).then(function(r) {
        var jobs = r.jobs || r || [];
        list.innerHTML = '';
        if (!jobs.length) { list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">No history found</div>'; return; }
        jobs.slice(0, 50).forEach(function(j) {
          var item = HF.el('div', {className: 'hf-history-item'});
          var cb = HF.el('input', {type: 'checkbox', style: {marginRight: '8px'}});
          cb.addEventListener('change', function() { if (cb.checked) HF.selectedHistoryIds.add(j.id); else HF.selectedHistoryIds.delete(j.id); });
          var preview = HF.el('div', {className: 'hf-history-preview'}, (j.input_text || '').substring(0, 80) + '...');
          var meta = HF.el('div', {className: 'hf-history-meta'}, (j.model || '?') + ' | Score: ' + (j.score_before || '?') + '→' + (j.score_after || '?') + ' | ' + (j.timestamp || ''));
          var star = HF.el('span', {style: {cursor: 'pointer', marginRight: '6px'}, onClick: function() {
            HF.api('/api/history/star', {id: j.id}).then(function() { loadHistory(); });
          }}, j.starred ? '⭐' : '☆');
          item.append(cb, star, preview, meta);
          list.appendChild(item);
        });
      }).catch(function(e) { list.innerHTML = '<div style="padding:20px;color:#fb7185">Error loading history</div>'; });
    }
    search.addEventListener('input', function() { loadHistory(search.value); });
    loadHistory();
  });
  var header = document.querySelector('header') || document.querySelector('.header') || document.body;
  header.appendChild(btn);
};

HF.initAutoSaveDrafts = function() {
  HF.draftTimer = setInterval(function() {
    var ta = document.querySelector('#input') || document.querySelector('textarea');
    if (ta && ta.value.trim().length > 50) {
      var drafts = JSON.parse(localStorage.getItem('hf_drafts') || '{}');
      drafts['auto_' + Date.now()] = {text: ta.value, saved: new Date().toISOString()};
      // Keep only last 5 drafts
      var keys = Object.keys(drafts).sort();
      while (keys.length > 5) { delete drafts[keys.shift()]; }
      localStorage.setItem('hf_drafts', JSON.stringify(drafts));
    }
  }, 30000);
};

HF.initUsageDashboard = function() {
  HF.apiGet('/api/analytics/dashboard').then(function(r) {
    if (!r || r.error) return;
    var sec = HF.createSection('USAGE DASHBOARD', 'hf-usage-dash');
    sec.innerHTML += '<div class="hf-grid-4">' +
      '<div class="hf-card"><div class="hf-card-value">' + (r.total_words || 0).toLocaleString() + '</div><div class="hf-card-label">Words Processed</div></div>' +
      '<div class="hf-card"><div class="hf-card-value">' + (r.total_jobs || 0) + '</div><div class="hf-card-label">Total Jobs</div></div>' +
      '<div class="hf-card"><div class="hf-card-value">' + (r.avg_score_after || 0).toFixed(1) + '%</div><div class="hf-card-label">Avg Score After</div></div>' +
      '<div class="hf-card"><div class="hf-card-value">' + (r.avg_improvement || 0).toFixed(1) + '%</div><div class="hf-card-label">Avg Improvement</div></div>' +
      '</div>';
    var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
    if (sidebar) sidebar.appendChild(sec);
    else document.body.appendChild(sec);
  }).catch(function() {});
};

HF.initModelLeaderboard = function() {
  HF.apiGet('/api/analytics/leaderboard').then(function(r) {
    if (!r || !r.length) return;
    var sec = HF.createSection('MODEL LEADERBOARD', 'hf-leaderboard');
    var rows = '';
    r.forEach(function(m, i) {
      rows += '<tr><td>' + (i+1) + '</td><td>' + m.model + '</td><td>' + (m.avg_score||0).toFixed(1) + '%</td><td>' + (m.total_jobs||0) + '</td><td>' + (m.avg_time||0).toFixed(1) + 's</td></tr>';
    });
    sec.innerHTML += '<table class="hf-table"><tr><th>#</th><th>Model</th><th>Avg Score</th><th>Jobs</th><th>Avg Time</th></tr>' + rows + '</table>';
    var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
    if (sidebar) sidebar.appendChild(sec);
  }).catch(function() {});
};

HF.initProcessingChart = function() {
  HF.apiGet('/api/analytics/time-chart').then(function(r) {
    if (!r || !r.labels) return;
    var sec = HF.createSection('PROCESSING TIME', 'hf-proc-chart');
    var canvas = HF.el('canvas', {width: '400', height: '200'});
    sec.appendChild(canvas);
    var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
    if (sidebar) sidebar.appendChild(sec);
    setTimeout(function() {
      var ctx = canvas.getContext('2d');
      var w = canvas.width, h = canvas.height;
      ctx.fillStyle = '#111'; ctx.fillRect(0, 0, w, h);
      var datasets = r.datasets || [];
      var colors = ['#6366f1', '#4ade80', '#fb7185', '#fbbf24', '#22d3ee'];
      datasets.forEach(function(ds, di) {
        ctx.strokeStyle = colors[di % colors.length]; ctx.lineWidth = 2; ctx.beginPath();
        (ds.times || []).forEach(function(t, i) {
          var x = 40 + i * ((w-60) / Math.max(1, (ds.times.length-1)));
          var y = h - 30 - (t / Math.max.apply(null, ds.times) * (h-50));
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
    }, 100);
  }).catch(function() {});
};

HF.initWordDistribution = function() {
  HF.apiGet('/api/analytics/word-distribution').then(function(r) {
    if (!r || !r.bins) return;
    var sec = HF.createSection('WORD DISTRIBUTION', 'hf-word-dist');
    var canvas = HF.el('canvas', {width: '400', height: '200'});
    sec.appendChild(canvas);
    var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
    if (sidebar) sidebar.appendChild(sec);
    setTimeout(function() {
      var ctx = canvas.getContext('2d');
      var w = canvas.width, h = canvas.height;
      ctx.fillStyle = '#111'; ctx.fillRect(0, 0, w, h);
      var max = Math.max.apply(null, r.counts) || 1;
      var bw = (w - 60) / r.bins.length;
      r.bins.forEach(function(b, i) {
        var bh = (r.counts[i] / max) * (h - 50);
        ctx.fillStyle = '#6366f1';
        ctx.fillRect(40 + i * bw + 4, h - 30 - bh, bw - 8, bh);
        ctx.fillStyle = '#888'; ctx.font = '10px sans-serif';
        ctx.fillText(b, 40 + i * bw, h - 10);
        ctx.fillText(r.counts[i], 40 + i * bw + bw/2 - 8, h - 35 - bh);
      });
    }, 100);
  }).catch(function() {});
};

HF.initSuccessRate = function() {
  HF.apiGet('/api/analytics/success-rate').then(function(r) {
    if (!r) return;
    var sec = HF.createSection('SUCCESS RATE', 'hf-success');
    sec.innerHTML += '<div class="hf-card"><div class="hf-card-value">' + (r.rate || 0).toFixed(1) + '%</div><div class="hf-card-label">' + (r.successful || 0) + '/' + (r.total || 0) + ' jobs below 30% score</div></div>';
    var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
    if (sidebar) sidebar.appendChild(sec);
  }).catch(function() {});
};

/* ═══════════════════════════════════════════════════════════════════
   GROUP 4: MODELS & LLM
   ═══════════════════════════════════════════════════════════════════ */

HF.initCustomModel = function() {
  var sec = HF.createSection('CUSTOM MODEL', 'hf-custom-model');
  var form = HF.el('div', {style: {display: 'flex', flexDirection: 'column', gap: '6px'}});
  var nameIn = HF.el('input', {placeholder: 'Model name', className: 'hf-search', style: {marginBottom: '0'}});
  var urlIn = HF.el('input', {placeholder: 'Base URL (e.g. http://localhost:11434/v1)', className: 'hf-search', style: {marginBottom: '0'}});
  var keyIn = HF.el('input', {placeholder: 'API Key (optional)', className: 'hf-search', style: {marginBottom: '0'}, type: 'password'});
  var modelIn = HF.el('input', {placeholder: 'Model name (e.g. llama3)', className: 'hf-search', style: {marginBottom: '0'}});
  var btn = HF.createButton('Add Model', function() {
    if (!nameIn.value || !urlIn.value) { HF.toast('Name and URL required', 'warn'); return; }
    HF.api('/api/models/add', {name: nameIn.value, base_url: urlIn.value, api_key: keyIn.value, model_name: modelIn.value}).then(function(r) {
      HF.toast('Model added: ' + nameIn.value); nameIn.value = ''; urlIn.value = ''; keyIn.value = ''; modelIn.value = '';
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  }, 'hf-btn-sm');
  form.append(nameIn, urlIn, keyIn, modelIn, btn);
  sec.appendChild(form);
  var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
  if (sidebar) sidebar.appendChild(sec);
};

HF.initModelVoting = function() {
  var wrap = HF.el('div', {id: 'hf-model-vote', className: 'hf-control-row', style: {marginTop: '8px'}});
  var cb = HF.el('input', {type: 'checkbox', id: 'hf-vote-cb'});
  var lbl = HF.el('label', {htmlFor: 'hf-vote-cb'}, ' Model Voting (run 3 models, pick best)');
  wrap.append(cb, lbl);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.appendChild(wrap);
};

HF.initTemperature = function() {
  var wrap = HF.el('div', {id: 'hf-temp', className: 'hf-control-row', style: {marginTop: '8px'}});
  var lbl = HF.el('label', {}, 'Temperature: ');
  var val = HF.el('span', {id: 'hf-temp-val'}, '0.7');
  var slider = HF.el('input', {type: 'range', min: '1', max: '15', value: '7', id: 'hf-temp-slider', style: {width: '120px'}});
  slider.addEventListener('input', function() { val.textContent = (slider.value / 10).toFixed(1); });
  wrap.append(lbl, slider, val);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.appendChild(wrap);
};

HF.initCustomPrompt = function() {
  var sec = HF.createSection('CUSTOM PROMPT', 'hf-custom-prompt');
  var ta = HF.el('textarea', {id: 'hf-custom-sys-prompt', placeholder: 'Enter custom system prompt for humanization...', rows: '4', style: {width: '100%', padding: '8px', borderRadius: '6px', background: 'var(--bg-secondary)', color: 'var(--text)', border: '1px solid var(--border)', resize: 'vertical', fontSize: '12px'}});
  var btn = HF.createButton('Save Prompt', function() {
    localStorage.setItem('hf_custom_prompt', ta.value);
    HF.toast('Custom prompt saved');
  }, 'hf-btn-sm');
  ta.value = localStorage.getItem('hf_custom_prompt') || '';
  sec.append(ta, btn);
  var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
  if (sidebar) sidebar.appendChild(sec);
};

HF.initFallbackEditor = function() {
  var sec = HF.createSection('FALLBACK CHAIN', 'hf-fallback');
  var list = HF.el('div', {id: 'hf-fallback-list'});
  sec.appendChild(list);
  HF.apiGet('/api/models/fallback-chain').then(function(r) {
    var chain = r.chain || r || [];
    chain.forEach(function(m, i) {
      var item = HF.el('div', {draggable: 'true', style: {padding: '6px 10px', margin: '4px 0', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: '6px', cursor: 'grab', fontSize: '12px'}}, (i+1) + '. ' + m);
      list.appendChild(item);
    });
  }).catch(function() {});
  var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
  if (sidebar) sidebar.appendChild(sec);
};

HF.initApiKeyManager = function() {
  var sec = HF.createSection('API KEYS', 'hf-apikeys');
  var providerIn = HF.el('input', {placeholder: 'Provider name', className: 'hf-search', style: {marginBottom: '4px'}});
  var keyIn = HF.el('input', {placeholder: 'API Key', className: 'hf-search', style: {marginBottom: '4px'}, type: 'password'});
  var btn = HF.createButton('Set Key', function() {
    if (!providerIn.value || !keyIn.value) { HF.toast('Both fields required', 'warn'); return; }
    HF.api('/api/models/set-key', {provider: providerIn.value, key: keyIn.value}).then(function() {
      HF.toast('Key set for ' + providerIn.value); providerIn.value = ''; keyIn.value = '';
    }).catch(function(e) { HF.toast('Error', 'error'); });
  }, 'hf-btn-sm');
  var rotBtn = HF.createButton('Rotate Key', function() {
    if (!providerIn.value) { HF.toast('Enter provider name', 'warn'); return; }
    HF.api('/api/models/rotate-key', {provider: providerIn.value}).then(function(r) { HF.toast('Key rotated'); }).catch(function(e) { HF.toast('Error', 'error'); });
  }, 'hf-btn-sm');
  sec.append(providerIn, keyIn, btn, ' ', rotBtn);
  var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
  if (sidebar) sidebar.appendChild(sec);
};

/* ═══════════════════════════════════════════════════════════════════
   GROUP 5: UI POLISH
   ═══════════════════════════════════════════════════════════════════ */

HF.initRichEditor = function() {
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (!ta) return;
  var toolbar = HF.el('div', {className: 'hf-toolbar'});
  var cmds = [
    {label: 'B', cmd: 'bold'}, {label: 'I', cmd: 'italic'}, {label: 'U', cmd: 'underline'},
    {label: 'H1', cmd: 'formatBlock', val: 'h1'}, {label: 'H2', cmd: 'formatBlock', val: 'h2'},
    {label: '•', cmd: 'insertUnorderedList'}, {label: '1.', cmd: 'insertOrderedList'},
    {label: '—', cmd: 'insertHorizontalRule'}
  ];
  cmds.forEach(function(c) {
    var b = HF.el('button', {onClick: function() {
      if (c.val) document.execCommand(c.cmd, false, c.val);
      else document.execCommand(c.cmd, false, null);
    }}, c.label);
    toolbar.appendChild(b);
  });
  // Toggle rich/plain mode
  var toggle = HF.el('button', {onClick: function() {
    var editor = document.getElementById('hf-rich-editor');
    if (editor) {
      // Switch back to textarea
      ta.value = editor.innerText;
      ta.style.display = '';
      editor.remove();
      toolbar.style.display = 'none';
      toggle.textContent = '✏️ Rich Edit';
    } else {
      // Switch to contenteditable
      editor = HF.el('div', {id: 'hf-rich-editor', className: 'hf-rich-editor', contentEditable: 'true'});
      editor.innerHTML = ta.value.replace(/\n/g, '<br>');
      ta.style.display = 'none';
      ta.parentNode.insertBefore(editor, ta);
      toolbar.style.display = 'flex';
      toggle.textContent = '📝 Plain Edit';
    }
  }}, '✏️ Rich Edit');
  toolbar.appendChild(toggle);
  toolbar.style.display = 'none';
  ta.parentNode.insertBefore(toolbar, ta);
};

HF.initWordCharCount = function() {
  var footer = HF.el('div', {id: 'hf-footer-count', className: 'hf-footer-bar'});
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (!ta) return;
  function update() {
    var text = ta.value;
    var words = text.trim() ? text.trim().split(/\s+/).length : 0;
    var chars = text.length;
    var lines = text.split('\n').length;
    var sents = text.split(/[.!?]+/).filter(function(s) { return s.trim(); }).length;
    footer.innerHTML = '<span>📝 ' + words + ' words</span><span>🔤 ' + chars + ' chars</span><span>📄 ' + lines + ' lines</span><span>💬 ' + sents + ' sentences</span>';
  }
  ta.addEventListener('input', update);
  update();
  ta.parentNode.appendChild(footer);
};

HF.initLanguageDetection = function() {
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (!ta) return;
  var indicator = HF.el('div', {id: 'hf-lang-indicator', style: {fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px'}});
  ta.parentNode.insertBefore(indicator, ta.nextSibling);
  var debounce;
  ta.addEventListener('input', function() {
    clearTimeout(debounce);
    debounce = setTimeout(function() {
      if (ta.value.length < 20) { indicator.textContent = ''; return; }
      HF.apiGet('/api/domains/detect-language?text=' + encodeURIComponent(ta.value.substring(0, 500))).then(function(r) {
        var flags = {en: '🇬🇧', ms: '🇲🇾', zh: '🇨🇳', ar: '🇸🇦', es: '🇪🇸', ja: '🇯🇵', ko: '🇰🇷', fr: '🇫🇷', de: '🇩🇪', id: '🇮🇩'};
        var code = r.language || r.code || 'en';
        indicator.textContent = (flags[code] || '🌐') + ' ' + (r.language_name || code);
      }).catch(function() {});
    }, 1000);
  });
};

HF.initAccentPicker = function() {
  var wrap = HF.el('div', {id: 'hf-accent', className: 'hf-control-row', style: {marginTop: '8px'}});
  var lbl = HF.el('label', {}, 'Accent: ');
  var picker = HF.el('input', {type: 'color', value: HF.accentColor, id: 'hf-accent-color', style: {width: '32px', height: '24px', border: 'none', cursor: 'pointer'}});
  picker.addEventListener('input', function() {
    HF.accentColor = picker.value;
    document.documentElement.style.setProperty('--accent', picker.value);
    localStorage.setItem('hf_accentColor', picker.value);
  });
  wrap.append(lbl, picker);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.appendChild(wrap);
};

HF.initCustomCSS = function() {
  var sec = HF.createSection('CUSTOM CSS', 'hf-custom-css');
  var ta = HF.el('textarea', {id: 'hf-custom-css-input', placeholder: '/* Enter custom CSS */', rows: '4', style: {width: '100%', padding: '8px', borderRadius: '6px', background: 'var(--bg-secondary)', color: 'var(--text)', border: '1px solid var(--border)', fontSize: '11px', fontFamily: 'monospace'}});
  var btn = HF.createButton('Apply CSS', function() {
    var style = document.getElementById('hf-custom-style') || HF.el('style', {id: 'hf-custom-style'});
    style.textContent = ta.value;
    document.head.appendChild(style);
    localStorage.setItem('hf_customCSS', ta.value);
    HF.toast('Custom CSS applied');
  }, 'hf-btn-sm');
  ta.value = localStorage.getItem('hf_customCSS') || '';
  sec.append(ta, btn);
  // Apply saved CSS on load
  if (ta.value) {
    var style = HF.el('style', {id: 'hf-custom-style'});
    style.textContent = ta.value;
    document.head.appendChild(style);
  }
  var sidebar = document.querySelector('.sidebar') || document.querySelector('nav') || document.querySelector('#sidebar');
  if (sidebar) sidebar.appendChild(sec);
};

/* ═══════════════════════════════════════════════════════════════════
   GROUP 6: DOMAIN PRESETS & QUALITY CHECKS
   ═══════════════════════════════════════════════════════════════════ */

HF.initDomainPresets = function() {
  var wrap = HF.el('div', {id: 'hf-domain', className: 'hf-control-row', style: {marginTop: '8px'}});
  var lbl = HF.el('label', {}, 'Domain: ');
  var sel = HF.el('select', {id: 'hf-domain-sel', style: {padding: '4px', borderRadius: '4px', background: 'var(--bg-secondary)', color: 'var(--text)', border: '1px solid var(--border)'}});
  var domains = ['General', 'Medical', 'Legal', 'Technical', 'Creative', 'Academic', 'Business', 'Casual', 'Scientific'];
  domains.forEach(function(d) { sel.appendChild(HF.el('option', {value: d.toLowerCase()}, d)); });
  var btn = HF.createButton('Apply Domain', function() {
    var text = HF.getInputText();
    if (!text.trim()) { HF.toast('No text', 'warn'); return; }
    HF.toast('Applying ' + sel.value + ' domain...');
    HF.api('/api/domains/apply', {text: text, domain_name: sel.value}).then(function(r) {
      if (r.result) { var out = document.getElementById('output'); if (out) out.textContent = r.result; HF.toast('Domain applied'); }
    }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
  }, 'hf-btn-sm');
  wrap.append(lbl, sel, ' ', btn);
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.appendChild(wrap);
};

HF.initDomainModes = function() {
  var wrap = HF.el('div', {id: 'hf-domain-modes', className: 'hf-domain-btns', style: {marginTop: '8px'}});
  var modes = [
    {label: '📚 Academic', endpoint: '/api/domains/academic'},
    {label: '🔍 SEO', endpoint: '/api/domains/seo'},
    {label: '📝 Summary', endpoint: '/api/domains/summary'},
    {label: '📈 Expand', endpoint: '/api/domains/expand'},
    {label: '🎯 Simplify', endpoint: '/api/domains/simplify'},
    {label: '💼 Professional', endpoint: '/api/domains/professional'},
    {label: '📖 Storytelling', endpoint: '/api/domains/storytelling'}
  ];
  modes.forEach(function(m) {
    var btn = HF.createButton(m.label, function() {
      var text = HF.getInputText();
      if (!text.trim()) { HF.toast('No text', 'warn'); return; }
      HF.toast('Applying ' + m.label + '...');
      HF.api(m.endpoint, {text: text}).then(function(r) {
        if (r.result) { var out = document.getElementById('output'); if (out) out.textContent = r.result; HF.toast(m.label + ' applied'); }
      }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
    }, 'hf-btn-sm');
    wrap.appendChild(btn);
  });
  var ta = document.querySelector('#input') || document.querySelector('textarea');
  if (ta && ta.parentNode) ta.parentNode.appendChild(wrap);
};

HF.initQualityChecks = function() {
  var sec = HF.createSection('QUALITY CHECKS', 'hf-quality');
  var btns = HF.el('div', {className: 'hf-quality-btns'});
  var checks = [
    {label: '📝 Grammar', endpoint: '/api/quality/grammar'},
    {label: '🔤 Spelling', endpoint: '/api/quality/spelling'},
    {label: '📊 Consistency', endpoint: '/api/quality/consistency'},
    {label: '📋 Facts', endpoint: '/api/quality/facts'},
    {label: '🎭 Tone', endpoint: '/api/quality/tone'},
    {label: '🔄 Repetition', endpoint: '/api/quality/repetition'},
    {label: '💬 Cliché', endpoint: '/api/quality/cliche'},
    {label: '✅ All Checks', endpoint: '/api/quality/all'}
  ];
  var results = HF.el('div', {id: 'hf-quality-results', style: {marginTop: '10px', maxHeight: '300px', overflowY: 'auto'}});
  checks.forEach(function(c) {
    var btn = HF.createButton(c.label, function() {
      var text = HF.getOutputText() || HF.getInputText();
      if (!text.trim()) { HF.toast('No text', 'warn'); return; }
      HF.toast('Running ' + c.label + '...');
      var body = c.endpoint === '/api/quality/all' ? {original: HF.getInputText(), humanized: text} : {text: text};
      HF.api(c.endpoint, body).then(function(r) {
        results.innerHTML = '<strong>' + c.label + ' Results:</strong><pre style="font-size:11px;white-space:pre-wrap;color:var(--text)">' + JSON.stringify(r, null, 2) + '</pre>';
      }).catch(function(e) { HF.toast('Error: ' + e.message, 'error'); });
    }, 'hf-btn-sm');
    btns.appendChild(btn);
  });
  sec.append(btns, results);
  var output = document.getElementById('output');
  if (output) output.parentNode.insertBefore(sec, output.nextSibling);
};

/* ═══════════════════════════════════════════════════════════════════
   INIT: Call all feature initializers
   ═══════════════════════════════════════════════════════════════════ */

HF.init = function() {
  console.log('[HumanizeFeatures] Initializing 42 features...');
  try { HF.initStrengthSlider(); } catch(e) { console.warn('Strength:', e); }
  try { HF.initPerParagraph(); } catch(e) { console.warn('PerPara:', e); }
  try { HF.initDiffView(); } catch(e) { console.warn('Diff:', e); }
  try { HF.initUndoRedo(); } catch(e) { console.warn('UndoRedo:', e); }
  try { HF.initPreserveFormatting(); } catch(e) { console.warn('Preserve:', e); }
  try { HF.initCitationDetector(); } catch(e) { console.warn('Citation:', e); }
  try { HF.initCodeBlockProtection(); } catch(e) { console.warn('CodeBlock:', e); }
  try { HF.initParagraphReorder(); } catch(e) { console.warn('Reorder:', e); }
  try { HF.initPassiveActive(); } catch(e) { console.warn('Passive:', e); }
  try { HF.initReadabilityDisplay(); } catch(e) { console.warn('Readability:', e); }
  try { HF.initToneMix(); } catch(e) { console.warn('ToneMix:', e); }
  try { HF.initFormalityDial(); } catch(e) { console.warn('Formality:', e); }
  try { HF.initMultiDetector(); } catch(e) { console.warn('MultiDet:', e); }
  try { HF.initDetectorComparison(); } catch(e) { console.warn('DetCompare:', e); }
  try { HF.initDetectionHistory(); } catch(e) { console.warn('DetHistory:', e); }
  try { HF.initAutoRetry(); } catch(e) { console.warn('AutoRetry:', e); }
  try { HF.initScorePrediction(); } catch(e) { console.warn('ScorePred:', e); }
  try { HF.initFlaggedSentences(); } catch(e) { console.warn('Flagged:', e); }
  try { HF.initHistoryPanel(); } catch(e) { console.warn('History:', e); }
  try { HF.initAutoSaveDrafts(); } catch(e) { console.warn('AutoSave:', e); }
  try { HF.initUsageDashboard(); } catch(e) { console.warn('Dashboard:', e); }
  try { HF.initModelLeaderboard(); } catch(e) { console.warn('Leaderboard:', e); }
  try { HF.initProcessingChart(); } catch(e) { console.warn('ProcChart:', e); }
  try { HF.initWordDistribution(); } catch(e) { console.warn('WordDist:', e); }
  try { HF.initSuccessRate(); } catch(e) { console.warn('SuccessRate:', e); }
  try { HF.initCustomModel(); } catch(e) { console.warn('CustomModel:', e); }
  try { HF.initModelVoting(); } catch(e) { console.warn('ModelVote:', e); }
  try { HF.initTemperature(); } catch(e) { console.warn('Temperature:', e); }
  try { HF.initCustomPrompt(); } catch(e) { console.warn('CustomPrompt:', e); }
  try { HF.initFallbackEditor(); } catch(e) { console.warn('Fallback:', e); }
  try { HF.initApiKeyManager(); } catch(e) { console.warn('ApiKeys:', e); }
  try { HF.initRichEditor(); } catch(e) { console.warn('RichEditor:', e); }
  try { HF.initWordCharCount(); } catch(e) { console.warn('WordCount:', e); }
  try { HF.initLanguageDetection(); } catch(e) { console.warn('LangDetect:', e); }
  try { HF.initAccentPicker(); } catch(e) { console.warn('Accent:', e); }
  try { HF.initCustomCSS(); } catch(e) { console.warn('CustomCSS:', e); }
  try { HF.initDomainPresets(); } catch(e) { console.warn('Domain:', e); }
  try { HF.initDomainModes(); } catch(e) { console.warn('DomainModes:', e); }
  try { HF.initQualityChecks(); } catch(e) { console.warn('Quality:', e); }
  console.log('[HumanizeFeatures] All features initialized');
};

document.addEventListener('DOMContentLoaded', function() {
  // Small delay to let existing page JS load first
  setTimeout(HF.init, 500);
});

})(); // End IIFE
