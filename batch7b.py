with open('app_v5.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add startTime variable at humanize start
old_start = "  btn.disabled = true;\n  output.value = '';\n  progressBar.style.display = 'block';\n  progressFill.style.width = '2%';"
new_start = "  var startTime = Date.now();\n  btn.disabled = true;\n  output.value = '';\n  progressBar.style.display = 'block';\n  progressFill.style.width = '2%';\n  output.classList.add('typewriter-cursor');"
if old_start in content and 'startTime' not in content:
    content = content.replace(old_start, new_start, 1)
    print('1: Added startTime + typewriter cursor')
else:
    print('1: skipped')

# 2. Replace polling status line with step progress + time remaining
old_poll = "status.innerHTML = 'Processing... ' + (prog.chunks_done || 0) + '/' + (prog.chunks_total || '?') + ' chunks done (' + (prog.progress || 0) + '%)';"
new_poll = """var cd = prog.chunks_done || 0;
        var ct = prog.chunks_total || '?';
        var timeStr = '';
        if(cd > 0 && typeof ct === 'number') {
          var elapsed = (Date.now() - startTime) / 1000;
          var avg = elapsed / cd;
          var rem = Math.round(avg * (ct - cd));
          timeStr = ' | ETA: ' + (rem > 60 ? Math.floor(rem/60) + 'm ' + (rem%60) + 's' : rem + 's');
        }
        status.innerHTML = 'Processing... ' + cd + '/' + ct + ' chunks (' + (prog.progress || 0) + '%)' + timeStr;
        // Step progress indicators
        var sp = document.getElementById('stepProgress');
        if(sp && typeof ct === 'number') {
          sp.style.display = 'flex';
          var h = '';
          for(var i=0;i<ct;i++) h += '<span class="' + (i<cd?'step done':(i===cd?'step active':'step')) + '">Chunk ' + (i+1) + '</span>';
          sp.innerHTML = h;
        }"""
if old_poll in content and 'stepProgress' not in content:
    content = content.replace(old_poll, new_poll, 1)
    print('2: Added step progress + time remaining')
else:
    print('2: skipped')

# 3. Add typewriter streaming (update partial if longer)
old_partial = """// Show partial results in output textarea
        if (prog.partial) {
          output.value = prog.partial;
        }"""
new_partial = """// Show partial results (typewriter streaming)
        if (prog.partial && prog.partial.length > output.value.length) {
          output.value = prog.partial;
          updateWordCount();
        }"""
if old_partial in content and 'typewriter streaming' not in content:
    content = content.replace(old_partial, new_partial, 1)
    print('3: Added typewriter streaming')
else:
    print('3: skipped')

# 4. Hide step progress + show completion animation on done
old_done_block = """if (prog.status === 'done') {
          done = true;
          progressFill.style.width = '100%';
          output.value = prog.result || prog.partial;
          output.classList.remove('typewriter-cursor');"""
new_done_block = """if (prog.status === 'done') {
          done = true;
          progressFill.style.width = '100%';
          output.value = prog.result || prog.partial;
          output.classList.remove('typewriter-cursor');
          var sp2 = document.getElementById('stepProgress');
          if(sp2) sp2.style.display = 'none';"""
if old_done_block in content and 'sp2' not in content:
    content = content.replace(old_done_block, new_done_block, 1)
    print('4: Added step progress hide on done')
else:
    print('4: skipped')

with open('app_v5.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Batch 7 final done')
