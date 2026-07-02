1|/**
2| * HumanizeAI Features Enhancement Module
3| * Adds 42 new UI features to the existing HumanizeAI page
4| * Loaded as a standalone JS file, enhances DOM after page load
5| */
6|(function() {
7|'use strict';
8|
9|/* ═══════════════════════════════════════════════════════════════════
10|   NAMESPACE & STATE
11|   ═══════════════════════════════════════════════════════════════════ */
12|var HF = window.HumanizeFeatures = {
13|  // Undo/Redo stacks
14|  undoStack: [], redoStack: [],
15|  // Job history
16|  jobHistory: JSON.parse(localStorage.getItem('hf_jobHistory') || '[]'),
17|  // Detection history
18|  detectionHistory: JSON.parse(localStorage.getItem('hf_detHistory') || '[]'),
19|  // Draft auto-save timer
20|  draftTimer: null,
21|  // Custom model endpoints
22|  customModels: JSON.parse(localStorage.getItem('hf_customModels') || '[]'),
23|  // API keys per provider
24|  apiKeys: JSON.parse(localStorage.getItem('hf_apiKeys') || '{}'),
25|  // Fallback chain
26|  fallbackChain: JSON.parse(localStorage.getItem('hf_fallbackChain') || '[]'),
27|  // Accent color
28|  accentColor: localStorage.getItem('hf_accentColor') || '#6366f1',
29|  // State flags
30|  autoRetrying: false,
31|  modelVoting: false,
32|  // Charts
33|  charts: {},
34|  // Selected history items for bulk delete
35|  selectedHistoryIds: new Set(),
36|};
37|
38|/* ═══════════════════════════════════════════════════════════════════
39|   UTILITY: API CALL HELPER
40|   ═══════════════════════════════════════════════════════════════════ */
41|HF.api = function api(endpoint, body) {
42|  return fetch(endpoint, {
43|    method: 'POST',
44|    headers: { 'Content-Type': 'application/json' },
45|    body: JSON.stringify(body)
46|  }).then(function(r) { return r.json(); });
47|};
48|
49|HF.apiGet = function apiGet(endpoint) {
50|  return fetch(endpoint).then(function(r) { return r.json(); });
51|};
52|
53|/* ═══════════════════════════════════════════════════════════════════
54|   UTILITY: TOAST NOTIFICATIONS (Feature 37)
55|   ═══════════════════════════════════════════════════════════════════ */
56|HF.toast = function toast(msg, type, duration) {
57|  type = type || 'info';
58|  duration = duration || 3500;
59|  var container = document.getElementById('hfToastContainer');
60|  if (!container) {
61|    container = document.createElement('div');
62|    container.id = 'hfToastContainer';
63|    container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
64|    document.body.appendChild(container);
65|  }
66|  var colors = { success:'#00cc88', error:'#ff4444', warning:'#ffaa00', info:'#6366f1', ok:'#00cc88', err:'#ff4444', warn:'#ffaa00' };
67|  var icons = { success:'✓', error:'✗', warning:'⚠', info:'ℹ', ok:'✓', err:'✗', warn:'⚠' };
68|  var el = document.createElement('div');
69|  el.style.cssText = 'pointer-events:auto;padding:10px 18px;border-radius:8px;font-size:13px;font-family:Inter,system-ui,sans-serif;color:#fff;background:' + (colors[type]||colors.info) + ';box-shadow:0 4px 16px rgba(0,0,0,0.25);display:flex;align-items:center;gap:8px;opacity:0;transform:translateX(40px);transition:all 0.3s ease;cursor:pointer;max-width:360px;word-break:break-word;';
70|  el.innerHTML = '<span style="font-weight:700;font-size:15px;">' + (icons[type]||'ℹ') + '</span><span>' + HF.esc(msg) + '</span>';
71|  el.onclick = function() { el.style.opacity = '0'; el.style.transform = 'translateX(40px)'; setTimeout(function() { el.remove(); }, 300); };
72|  container.appendChild(el);
73|  requestAnimationFrame(function() { el.style.opacity = '1'; el.style.transform = 'translateX(0)'; });
74|  setTimeout(function() { el.style.opacity = '0'; el.style.transform = 'translateX(40px)'; setTimeout(function() { el.remove(); }, 300); }, duration);
75|};
76|
77|/* ═══════════════════════════════════════════════════════════════════
78|   UTILITY: HELPERS
79|   ═══════════════════════════════════════════════════════════════════ */
80|HF.esc = function esc(text) {
81|  var d = document.createElement('div');
82|  d.textContent = text;
83|  return d.innerHTML;
84|};
85|
86|HF.qs = function qs(sel) { return document.querySelector(sel); };
87|HF.qsa = function qsa(sel) { return document.querySelectorAll(sel); };
88|HF.ce = function ce(tag, attrs, html) {
89|  var el = document.createElement(tag);
90|  if (attrs) Object.keys(attrs).forEach(function(k) { el.setAttribute(k, attrs[k]); });
91|  if (html) el.innerHTML = html;
92|  return el;
93|};
94|
95|HF.getInput = function() { return document.getElementById('input'); };
96|HF.getOutput = function() { return document.getElementById('output'); };
97|HF.getInputText = function() { var i = HF.getInput(); return i ? i.value : ''; };
98|HF.getOutputText = function() { var o = HF.getOutput(); return o ? (o.innerText || o.textContent || '') : ''; };
99|
100|/* ═══════════════════════════════════════════════════════════════════
101|   UTILITY: CANVAS CHART RENDERER
102|   ═══════════════════════════════════════════════════════════════════ */
103|HF.drawLineChart = function drawLineChart(canvas, data, opts) {
104|  opts = opts || {};
105|  var ctx = canvas.getContext('2d');
106|  var W = canvas.width = canvas.offsetWidth || 400;
107|  var H = canvas.height = canvas.offsetHeight || 200;
108|  var pad = { t: 30, r: 20, b: 40, l: 50 };
109|  ctx.clearRect(0, 0, W, H);
110|  if (!data.length) { ctx.fillStyle = '#666'; ctx.font = '12px Inter'; ctx.fillText('No data', W/2 - 20, H/2); return; }
111|  var maxVal = opts.max || Math.max.apply(null, data.map(function(d) { return d.y || d.value || 0; }));
112|  var minVal = opts.min || Math.min(0, Math.min.apply(null, data.map(function(d) { return d.y || d.value || 0; })));
113|  var range = maxVal - minVal || 1;
114|  // grid
115|  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
116|  for (var g = 0; g <= 4; g++) {
117|    var gy = pad.t + (H - pad.t - pad.b) * g / 4;
118|    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
119|    ctx.fillStyle = '#666'; ctx.font = '10px Inter'; ctx.textAlign = 'right';
120|    ctx.fillText(Math.round(maxVal - range * g / 4), pad.l - 8, gy + 3);
121|  }
122|  // line
123|  var color = opts.color || '#6366f1';
124|  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round';
125|  ctx.beginPath();
126|  data.forEach(function(d, i) {
127|    var x = pad.l + (W - pad.l - pad.r) * i / Math.max(data.length - 1, 1);
128|    var y = pad.t + (H - pad.t - pad.b) * (1 - ((d.y || d.value || 0) - minVal) / range);
129|    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
130|  });
131|  ctx.stroke();
132|  // dots
133|  data.forEach(function(d, i) {
134|    var x = pad.l + (W - pad.l - pad.r) * i / Math.max(data.length - 1, 1);
135|    var y = pad.t + (H - pad.t - pad.b) * (1 - ((d.y || d.value || 0) - minVal) / range);
136|    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
137|    ctx.fillStyle = '#999'; ctx.font = '9px Inter'; ctx.textAlign = 'center';
138|    ctx.fillText(d.label || '', x, H - pad.b + 14);
139|  });
140|  // title
141|  if (opts.title) { ctx.fillStyle = '#ccc'; ctx.font = '11px Inter'; ctx.textAlign = 'left'; ctx.fillText(opts.title, pad.l, 14); }
142|};
143|
144|HF.drawBarChart = function drawBarChart(canvas, data, opts) {
145|  opts = opts || {};
146|  var ctx = canvas.getContext('2d');
147|  var W = canvas.width = canvas.offsetWidth || 400;
148|  var H = canvas.height = canvas.offsetHeight || 200;
149|  var pad = { t: 30, r: 20, b: 50, l: 50 };
150|  ctx.clearRect(0, 0, W, H);
151|  if (!data.length) { ctx.fillStyle = '#666'; ctx.font = '12px Inter'; ctx.fillText('No data', W/2 - 20, H/2); return; }
152|  var maxVal = opts.max || Math.max.apply(null, data.map(function(d) { return d.y || d.value || 0; }));
153|  var barW = (W - pad.l - pad.r) / data.length * 0.7;
154|  var gap = (W - pad.l - pad.r) / data.length * 0.3;
155|  // grid
156|  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
157|  for (var g = 0; g <= 4; g++) {
158|    var gy = pad.t + (H - pad.t - pad.b) * g / 4;
159|    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
160|    ctx.fillStyle = '#666'; ctx.font = '10px Inter'; ctx.textAlign = 'right';
161|    ctx.fillText(Math.round(maxVal * (1 - g/4)), pad.l - 8, gy + 3);
162|  }
163|  var colors = opts.colors || ['#6366f1','#8b5cf6','#ec4899','#f59e0b','#10b981','#3b82f6','#ef4444','#14b8a6'];
164|  data.forEach(function(d, i) {
165|    var x = pad.l + (W - pad.l - pad.r) * i / data.length + gap / 2;
166|    var h = (H - pad.t - pad.b) * ((d.y || d.value || 0) / maxVal);
167|    var y = H - pad.b - h;
168|    ctx.fillStyle = colors[i % colors.length];
169|    ctx.fillRect(x, y, barW, h);
170|    ctx.fillStyle = '#999'; ctx.font = '9px Inter'; ctx.textAlign = 'center';
171|    ctx.save(); ctx.translate(x + barW / 2, H - pad.b + 14); ctx.rotate(-0.4);
172|    ctx.fillText(d.label || d.x || '', 0, 0); ctx.restore();
173|  });
174|  if (opts.title) { ctx.fillStyle = '#ccc'; ctx.font = '11px Inter'; ctx.textAlign = 'left'; ctx.fillText(opts.title, pad.l, 14); }
175|};
176|
177|HF.drawHistogram = function drawHistogram(canvas, values, opts) {
178|  opts = opts || {};
179|  var ctx = canvas.getContext('2d');
180|  var W = canvas.width = canvas.offsetWidth || 400;
181|  var H = canvas.height = canvas.offsetHeight || 200;
182|  var pad = { t: 30, r: 20, b: 40, l: 50 };
183|  ctx.clearRect(0, 0, W, H);
184|  if (!values.length) { ctx.fillStyle = '#666'; ctx.font = '12px Inter'; ctx.fillText('No data', W/2 - 20, H/2); return; }
185|  var bins = opts.bins || 10;
186|  var min = Math.min.apply(null, values);
187|  var max = Math.max.apply(null, values);
188|  var binW = (max - min) / bins || 1;
189|  var counts = new Array(bins).fill(0);
190|  values.forEach(function(v) {
191|    var idx = Math.min(Math.floor((v - min) / binW), bins - 1);
192|    counts[idx]++;
193|  });
194|  var maxCount = Math.max.apply(null, counts);
195|  var barW = (W - pad.l - pad.r) / bins * 0.85;
196|  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
197|  for (var g = 0; g <= 4; g++) {
198|    var gy = pad.t + (H - pad.t - pad.b) * g / 4;
199|    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
200|    ctx.fillStyle = '#666'; ctx.font = '10px Inter'; ctx.textAlign = 'right';
201|    ctx.fillText(Math.round(maxCount * (1 - g/4)), pad.l - 8, gy + 3);
202|  }
203|  counts.forEach(function(c, i) {
204|    var x = pad.l + (W - pad.l - pad.r) * i / bins;
205|    var h = (H - pad.t - pad.b) * (c / (maxCount || 1));
206|    ctx.fillStyle = '#8b5cf6';
207|    ctx.fillRect(x + 2, H - pad.b - h, barW, h);
208|    ctx.fillStyle = '#999'; ctx.font = '9px Inter'; ctx.textAlign = 'center';
209|    ctx.fillText(Math.round(min + binW * i) + '-' + Math.round(min + binW * (i+1)), x + barW/2 + 2, H - pad.b + 14);
210|  });
211|  if (opts.title) { ctx.fillStyle = '#ccc'; ctx.font = '11px Inter'; ctx.textAlign = 'left'; ctx.fillText(opts.title, pad.l, 14); }
212|};
213|
214|/* ═══════════════════════════════════════════════════════════════════
215|   UTILITY: WORD-LEVEL DIFF (Feature 3 helper)
216|   ═══════════════════════════════════════════════════════════════════ */
217|HF.wordDiff = function wordDiff(oldStr, newStr) {
218|  var oldW = oldStr.split(/\s+/), newW = newStr.split(/\s+/);
219|  // Simple LCS-based diff
220|  var m = oldW.length, n = newW.length;
221|  var dp = [];
222|  for (var i = 0; i <= m; i++) { dp[i] = []; for (var j = 0; j <= n; j++) { dp[i][j] = (i === 0 || j === 0) ? 0 : (oldW[i-1] === newW[j-1] ? dp[i-1][j-1] + 1 : Math.max(dp[i-1][j], dp[i][j-1])); } }
223|  var result = [];
224|  var ii = m, jj = n;
225|  while (ii > 0 || jj > 0) {
226|    if (ii > 0 && jj > 0 && oldW[ii-1] === newW[jj-1]) { result.unshift({ type: 'same', text: oldW[ii-1] }); ii--; jj--; }
227|    else if (jj > 0 && (ii === 0 || dp[ii][jj-1] >= dp[ii-1][jj])) { result.unshift({ type: 'add', text: newW[jj-1] }); jj--; }
228|    else { result.unshift({ type: 'del', text: oldW[ii-1] }); ii--; }
229|  }
230|  return result;
231|};
232|
233|HF.renderDiff = function renderDiff(diff) {
234|  return diff.map(function(d) {
235|    if (d.type === 'add') return '<span style="background:#1a3a2a;color:#4ade80;padding:1px 2px;border-radius:2px;">' + HF.esc(d.text) + '</span>';
236|    if (d.type === 'del') return '<span style="background:#3a1a1a;color:#f87171;padding:1px 2px;border-radius:2px;text-decoration:line-through;">' + HF.esc(d.text) + '</span>';
237|    return HF.esc(d.text);
238|  }).join(' ');
239|};
240|
241|/* ═══════════════════════════════════════════════════════════════════
242|   UTILITY: READABILITY SCORES (Feature 10 helper)
243|   ═══════════════════════════════════════════════════════════════════ */
244|HF.calcReadability = function calcReadability(text) {
245|  if (!text || !text.trim()) return { fk: 0, fog: 0 };
246|  var sentences = text.split(/[.!?]+/).filter(function(s) { return s.trim().length > 0; });
247|  var words = text.split(/\s+/).filter(function(w) { return w.length > 0; });
248|  var syllables = 0;
249|  words.forEach(function(w) {
250|    w = w.toLowerCase().replace(/[^a-z]/g, '');
251|    if (!w) return;
252|    var s = 0, prevVowel = false;
253|    var vowels = 'aeiouy';
254|    for (var i = 0; i < w.length; i++) {
255|      var isV = vowels.indexOf(w[i]) >= 0;
256|      if (isV && !prevVowel) s++;
257|      prevVowel = isV;
258|    }
259|    if (w.endsWith('e') && s > 1) s--;
260|    syllables += Math.max(s, 1);
261|  });
262|  var sw = 0;
263|  words.forEach(function(w) {
264|    w = w.toLowerCase().replace(/[^a-z]/g, '');
265|    var s = 0, prevV = false;
266|    var v = 'aeiouy';
267|    for (var i = 0; i < w.length; i++) { var isV = v.indexOf(w[i]) >= 0; if (isV && !prevV) s++; prevV = isV; }
268|    if (w.endsWith('e') && s > 1) s--;
269|    if (s >= 3) sw++;
270|  });
271|  var N = words.length, S = sentences.length || 1;
272|  var fk = 0.39 * (N / S) + 11.8 * (syllables / N) - 15.59;
273|  var fog = 0.4 * ((N / S) + 100 * (sw / N));
274|  return { fk: Math.round(fk * 10) / 10, fog: Math.round(fog * 10) / 10 };
275|};
276|
277|/* ═══════════════════════════════════════════════════════════════════
278|   UTILITY: LANGUAGE DETECTION (Feature 36 helper)
279|   ═══════════════════════════════════════════════════════════════════ */
280|HF.detectLanguage = function detectLanguage(text) {
281|  if (!text || text.length < 20) return { lang: 'unknown', flag: '🌐' };
282|  var sample = text.substring(0, 2000).toLowerCase();
283|  var patterns = {
284|    'en': { flag: '🇺🇸', re: /\b(the|is|are|was|were|have|has|been|with|this|that|for)\b/g },
285|    'es': { flag: '🇪🇸', re: /\b(el|la|los|las|es|son|están|con|por|para|una|que)\b/g },
286|    'fr': { flag: '🇫🇷', re: /\b(le|la|les|est|sont|avec|pour|des|une|que|pas)\b/g },
287|    'de': { flag: '🇩🇪', re: /\b(der|die|das|ist|sind|mit|für|ein|eine|und|nicht)\b/g },
288|    'pt': { flag: '🇧🇷', re: /\b(o|a|os|as|é|são|com|para|uma|que|não)\b/g },
289|    'it': { flag: '🇮🇹', re: /\b(il|la|lo|è|sono|con|per|una|che|non|del)\b/g },
290|    'zh': { flag: '🇨🇳', re: /[\u4e00-\u9fff]{3,}/g },
291|    'ja': { flag: '🇯🇵', re: /[\u3040-\u309f\u30a0-\u30ff]{3,}/g },
292|    'ko': { flag: '🇰🇷', re: /[\uac00-\ud7af]{3,}/g },
293|    'ar': { flag: '🇸🇦', re: /[\u0600-\u06ff]{3,}/g },
294|    'ru': { flag: '🇷🇺', re: /[\u0400-\u04ff]{3,}/g },
295|  };
296|  var best = 'en', bestCount = 0;
297|  Object.keys(patterns).forEach(function(lang) {
298|    var m = sample.match(patterns[lang].re);
299|    var c = m ? m.length : 0;
300|    if (c > bestCount) { bestCount = c; best = lang; }
301|  });
302|  return { lang: best, flag: patterns[best].flag };
303|};
304|
305|/* ═══════════════════════════════════════════════════════════════════
306|   UTILITY: PASSIVE VOICE DETECTION (Feature 9 helper)
307|   ═══════════════════════════════════════════════════════════════════ */
308|HF.convertPassiveToActive = function(text) {
309|  // Simple regex-based passive to active conversion
310|  var passiveRe = /\b(is|are|was|were|been|being|be)\s+(\w+ed)\b/gi;
311|  var result = text.replace(passiveRe, function(match, aux, pastPart) {
312|    // Capitalize first letter of past participle to make it active
313|    return pastPart.charAt(0).toUpperCase() + pastPart.slice(1) + ' (was ' + aux + ')';
314|  });
315|  return result;
316|};
317|
318|/* ═══════════════════════════════════════════════════════════════════
319|   UTILITY: CITATION DETECTION (Feature 6 helper)
320|   ═══════════════════════════════════════════════════════════════════ */
321|HF.findCitations = function(text) {
322|  var patterns = [
323|    /\(\w+,?\s*\d{4}\)/g,           // (Author, 2024)
324|    /\[\d+\]/g,                       // [1], [2]
325|    /\bet al\.\s*\(\d{4}\)/gi,       // et al. (2024)
326|    /(?:doi|DOI):\s*10\.\d{4,}\/[^\s]+/g,  // DOI
327|    /https?:\/\/[^\s<>"{}|\\^`\[\]]+/gi,    // URLs
328|    /\b(?:pp?\.?\s*\d+[-–]\d+)\b/g,        // pp. 12-15
329|    /"(?:[^"\\]|\\.)*"/g,                    // Quoted strings (short ones)
330|  ];
331|  var ranges = [];
332|  patterns.forEach(function(re) {
333|    var m;
334|    while ((m = re.exec(text)) !== null) {
335|      ranges.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
336|    }
337|  });
338|  // Deduplicate overlapping
339|  ranges.sort(function(a,b) { return a.start - b.start; });
340|  var merged = [];
341|  ranges.forEach(function(r) {
342|    if (merged.length && r.start < merged[merged.length-1].end) {
343|      if (r.end > merged[merged.length-1].end) merged[merged.length-1].end = r.end;
344|    } else merged.push(r);
345|  });
346|  return merged;
347|};
348|
349|/* ═══════════════════════════════════════════════════════════════════
350|   UTILITY: STORAGE HELPERS
351|   ═══════════════════════════════════════════════════════════════════ */
352|HF.saveJobs = function() { localStorage.setItem('hf_jobHistory', JSON.stringify(HF.jobHistory)); };
353|HF.saveDetHistory = function() { localStorage.setItem('hf_detHistory', JSON.stringify(HF.detectionHistory)); };
354|HF.saveCustomModels = function() { localStorage.setItem('hf_customModels', JSON.stringify(HF.customModels)); };
355|HF.saveApiKeys = function() { localStorage.setItem('hf_apiKeys', JSON.stringify(HF.apiKeys)); };
356|HF.saveFallbackChain = function() { localStorage.setItem('hf_fallbackChain', JSON.stringify(HF.fallbackChain)); };
357|
358|HF.addJob = function(job) {
359|  HF.jobHistory.unshift(job);
360|  if (HF.jobHistory.length > 200) HF.jobHistory.length = 200;
361|  HF.saveJobs();
362|};
363|
364|HF.addDetEntry = function(entry) {
365|  HF.detectionHistory.push(entry);
366|  if (HF.detectionHistory.length > 500) HF.detectionHistory.shift();
367|  HF.saveDetHistory();
368|};
369|
370|/* ═══════════════════════════════════════════════════════════════════
371|   INJECT CSS
372|   ═══════════════════════════════════════════════════════════════════ */
373|HF.injectCSS = function injectCSS() {
374|  var style = document.createElement('style');
375|  style.textContent = [
376|    '/* HumanizeFeatures enhanced styles */',
377|    '.hf-section { margin: 16px 0; padding: 16px; border: 1px solid var(--border, #333); border-radius: 10px; background: var(--paper, #1a1a1a); }',
378|    '.hf-section-title { font-family: IBM Plex Mono, monospace; font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-muted, #888); margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }',
379|    '.hf-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }',
380|    '.hf-row-between { display: flex; justify-content: space-between; align-items: center; margin: 8px 0; }',
381|    '.hf-btn { padding: 7px 14px; border: 1px solid var(--border, #444); border-radius: 6px; background: var(--bg-secondary, #222); color: var(--text, #eee); font-size: 12px; cursor: pointer; font-family: Inter, system-ui, sans-serif; transition: all 0.15s; }',
382|    '.hf-btn:hover { background: var(--accent, #6366f1); color: #fff; border-color: var(--accent, #6366f1); }',
383|    '.hf-btn:disabled { opacity: 0.5; cursor: not-allowed; }',
384|    '.hf-btn-primary { background: var(--accent, #6366f1); color: #fff; border-color: var(--accent, #6366f1); }',
385|    '.hf-btn-primary:hover { filter: brightness(1.2); }',
386|    '.hf-btn-danger { background: #ef4444; color: #fff; border-color: #ef4444; }',
387|    '.hf-btn-danger:hover { filter: brightness(1.2); }',
388|    '.hf-btn-success { background: #10b981; color: #fff; border-color: #10b981; }',
389|    '.hf-slider-wrap { display: flex; align-items: center; gap: 10px; width: 100%; }',
390|    '.hf-slider-wrap input[type=range] { flex: 1; accent-color: var(--accent, #6366f1); }',
391|    '.hf-slider-wrap .hf-slider-val { font-size: 12px; font-weight: 600; min-width: 60px; text-align: right; color: var(--text, #eee); }',
392|    '.hf-slider-label { font-size: 11px; color: var(--text-muted, #888); min-width: 80px; }',
393|    '.hf-select { padding: 6px 10px; border: 1px solid var(--border, #444); border-radius: 6px; background: var(--bg-secondary, #222); color: var(--text, #eee); font-size: 12px; font-family: Inter, system-ui, sans-serif; }',
394|    '.hf-checkbox { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text, #eee); cursor: pointer; }',
395|    '.hf-checkbox input { accent-color: var(--accent, #6366f1); }',
396|    '.hf-toggle { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border: 1px solid var(--border, #444); border-radius: 20px; font-size: 12px; cursor: pointer; transition: all 0.2s; color: var(--text-muted, #888); }',
397|    '.hf-toggle.active { background: var(--accent, #6366f1); color: #fff; border-color: var(--accent, #6366f1); }',
398|    '.hf-table { width: 100%; border-collapse: collapse; font-size: 12px; }',
399|    '.hf-table th { padding: 8px 10px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted, #888); border-bottom: 1px solid var(--border, #333); }',
400|    '.hf-table td { padding: 8px 10px; border-bottom: 1px solid var(--border, #222); color: var(--text, #eee); }',
401|    '.hf-table tr:hover td { background: var(--bg-secondary, #1e1e1e); }',
402|    '.hf-card { padding: 14px; border: 1px solid var(--border, #333); border-radius: 8px; background: var(--bg-secondary, #1e1e1e); }',
403|    '.hf-card-value { font-size: 24px; font-weight: 700; color: var(--text, #eee); }',
404|    '.hf-card-label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted, #888); margin-top: 4px; }',
405|    '.hf-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }',
406|    '.hf-grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }',
407|    '.hf-grid-4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 12px; }',
408|    '.hf-diff-container { padding: 12px; font-size: 13px; line-height: 1.8; border: 1px solid var(--border, #333); border-radius: 8px; background: var(--bg-secondary, #1a1a1a); max-height: 300px; overflow-y: auto; }',
409|    '.hf-undo-redo { display: flex; gap: 6px; margin: 8px 0; }',
410|    '.hf-toolbar { display: flex; gap: 4px; padding: 6px; border: 1px solid var(--border, #333); border-bottom: none; border-radius: 8px 8px 0 0; background: var(--bg-secondary, #1e1e1e); flex-wrap: wrap; }',
411|    '.hf-toolbar button { padding: 4px 8px; border: 1px solid var(--border, #444); border-radius: 4px; background: transparent; color: var(--text, #eee); font-size: 12px; cursor: pointer; }',
412|    '.hf-toolbar button:hover { background: var(--accent, #6366f1); color: #fff; }',
413|    '.hf-rich-editor { border: 1px solid var(--border, #333); border-radius: 0 0 8px 8px; padding: 12px; min-height: 150px; outline: none; font-family: Inter, system-ui, sans-serif; font-size: 14px; color: var(--text, #eee); background: var(--paper, #1a1a1a); }',
414|    '.hf-rich-editor:focus { border-color: var(--accent, #6366f1); }',
415|    '.hf-panel-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 300; display: flex; align-items: center; justify-content: center; }',
416|    '.hf-panel { background: var(--paper, #1a1a1a); border: 1px solid var(--border, #333); border-radius: 12px; padding: 24px; max-width: 700px; width: 92%; max-height: 85vh; overflow-y: auto; box-shadow: 0 12px 40px rgba(0,0,0,0.4); }',
417|    '.hf-panel-title { font-family: IBM Plex Mono, monospace; font-size: 13px; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }',
418|    '.hf-history-item { padding: 10px 12px; border-bottom: 1px solid var(--border, #222); cursor: pointer; transition: background 0.15s; }',
419|    '.hf-history-item:hover { background: var(--bg-secondary, #1e1e1e); }',
420|    '.hf-history-preview { font-size: 12px; color: var(--text, #eee); margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }',
421|    '.hf-history-meta { font-size: 10px; color: var(--text-muted, #666); }',
422|    '.hf-search { width: 100%; padding: 8px 12px; border: 1px solid var(--border, #444); border-radius: 6px; background: var(--bg-secondary, #222); color: var(--text, #eee); font-size: 13px; margin-bottom: 10px; outline: none; }',
423|    '.hf-search:focus { border-color: var(--accent, #6366f1); }',
424|    '.hf-canvas-wrap { border: 1px solid var(--border, #333); border-radius: 8px; padding: 8px; background: var(--bg-secondary, #111); }',
425|    '.hf-canvas-wrap canvas { width: 100%; height: 200px; display: block; }',
426|    '.hf-flagged { background: #fef3c7; color: #92400e; padding: 2px 4px; border-radius: 3px; }',
427|    '.hf-citation { background: #fde68a; color: #92400e; padding: 1px 2px; border-radius: 2px; }',
428|    '.hf-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; }',
429|    '.hf-badge-green { background: #065f46; color: #6ee7b7; }',
430|    '.hf-badge-yellow { background: #78350f; color: #fcd34d; }',
431|    '.hf-badge-red { background: #7f1d1d; color: #fca5a5; }',
432|    '.hf-lang-flag { font-size: 18px; margin-right: 6px; }',
433|    '.hf-domain-btns { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }',
434|    '.hf-quality-btns { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }',
435|    '.hf-footer-bar { display: flex; gap: 16px; align-items: center; padding: 8px 16px; font-size: 11px; color: var(--text-muted, #666); border-top: 1px solid var(--border, #222); flex-wrap: wrap; }',
436|    '.hf-footer-bar span { white-space: nowrap; }',
437|    '.hf-ptr-para { padding: 8px; margin-bottom: 6px; border: 1px solid var(--border, #333); border-radius: 6px; position: relative; }',
438|    '.hf-ptr-para-btn { position: absolute; top: 4px; right: 4px; }',
439|    '@media (max-width: 768px) { .hf-grid-2,.hf-grid-3,.hf-grid-4 { grid-template-columns: 1fr; } }',
440|  ].join('\n');
441|  document.head.appendChild(style);
442|};
443|
444|
445|/* ═══════════════════════════════════════════════════════════════════
446|   FEATURE IMPLEMENTATIONS
447|   ═══════════════════════════════════════════════════════════════════ */
448|
449|// ── UTILITY: Create element helper ──
450|HF.el = function(tag, attrs, children) {
451|  var e = document.createElement(tag);
452|  if (attrs) Object.keys(attrs).forEach(function(k) {
453|    if (k === 'style' && typeof attrs[k] === 'object') Object.assign(e.style, attrs[k]);
454|    else if (k === 'className') e.className = attrs[k];
455|    else if (k.startsWith('on')) e.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
456|    else e.setAttribute(k, attrs[k]);
457|  });
458|  if (children) {
459|    if (typeof children === 'string') e.textContent = children;
460|    else if (Array.isArray(children)) children.forEach(function(c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
461|    else e.appendChild(children);
462|  }
463|  return e;
464|};
465|
466|// ── UTILITY: Find insertion point ──
467|HF.findInsertPoint = function() {
468|  return document.querySelector('#output') || document.querySelector('.output-section') || document.querySelector('main') || document.body;
469|};
470|
471|HF.getInputText = function() {
472|  var el = document.querySelector('#input') || document.querySelector('textarea');
473|  return el ? el.value : '';
474|};
475|
476|HF.getOutputText = function() {
477|  var el = document.querySelector('#output');
478|  if (!el) return '';
479|  return el.innerText || el.textContent || '';
480|};
481|
482|HF.createSection = function(title, id) {
483|  var sec = HF.el('div', {id: id, className: 'hf-section'});
484|  var hdr = HF.el('div', {className: 'hf-section-title'}, title);
485|  sec.appendChild(hdr);
486|  return sec;
487|};
488|
489|HF.createButton = function(text, onClick, cls) {
490|  return HF.el('button', {className: 'hf-btn ' + (cls||''), onClick: onClick}, text);
491|};
492|
493|/* ═══════════════════════════════════════════════════════════════════
494|   GROUP 1: TEXT PROCESSING UI
495|   ═══════════════════════════════════════════════════════════════════ */
496|
497|HF.strengthLevel = 'medium';
498|HF.preserveFormatting = false;
499|
500|HF.initStrengthSlider = function() {
501|