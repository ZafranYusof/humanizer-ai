with open('app_v5.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Add typewriter cursor when processing starts
old1 = "  progressFill.style.width = '2%';\n  status.innerHTML = 'Starting...'"
new1 = "  progressFill.style.width = '2%';\n  output.classList.add('typewriter-cursor');\n  status.innerHTML = 'Starting...'"
if old1 in content and "add('typewriter-cursor')" not in content:
    content = content.replace(old1, new1, 1)
    print('Fix 1: Added typewriter cursor on start')
else:
    print('Fix 1: skipped (already exists or pattern mismatch)')

# Fix 2: Add step progress indicators in polling loop
old2 = "status.innerHTML = 'Processing... ' + (prog.chunks_done || 0) + '/' + (prog.chunks_total || '?') + ' chunks done (' + (prog.progress || 0) + '%)';"
new2 = """var chunksDone = prog.chunks_done || 0;
        var chunksTotal = prog.chunks_total || '?';
        status.innerHTML = 'Processing... ' + chunksDone + '/' + chunksTotal + ' chunks (' + (prog.progress || 0) + '%)';
        // Step progress indicators
        var stepEl = document.getElementById('stepProgress');
        if(stepEl && typeof chunksTotal === 'number') {
          stepEl.style.display = 'flex';
          var sh = '';
          for(var s=0; s<chunksTotal; s++) {
            sh += '<span class="' + (s<chunksDone?'step done':(s===chunksDone?'step active':'step')) + '">Chunk ' + (s+1) + '</span>';
          }
          stepEl.innerHTML = sh;
        }
        // Stream partial result (typewriter)
        if(prog.partial && prog.partial.length > output.value.length) {
          output.value = prog.partial;
          updateWordCount();
        }"""
if old2 in content and 'stepProgress' not in content:
    content = content.replace(old2, new2, 1)
    print('Fix 2: Added step progress + typewriter streaming')
else:
    print('Fix 2: skipped')

# Fix 3: Hide step progress on complete
old3 = "progressFill.style.width = '0%'; }, 2000);"
new3 = "progressFill.style.width = '0%'; var sp=document.getElementById('stepProgress'); if(sp)sp.style.display='none'; }, 2000);"
if old3 in content and "sp=document.getElementById" not in content:
    content = content.replace(old3, new3, 1)
    print('Fix 3: Hide step progress on done')
else:
    print('Fix 3: skipped')

# Fix 4: Time remaining display
old4 = "status.innerHTML = 'Processing... ' + chunksDone"
time_est = """var elapsed = (Date.now() - startTime) / 1000;
        if(chunksDone > 0 && typeof chunksTotal === 'number') {
          var avgPerChunk = elapsed / chunksDone;
          var remaining = Math.round(avgPerChunk * (chunksTotal - chunksDone));
          var min = Math.floor(remaining / 60);
          var sec = remaining % 60;
          var timeStr = min > 0 ? min + 'm ' + sec + 's' : sec + 's';
          status.innerHTML = 'Processing... ' + chunksDone"""
if old4 in content and 'avgPerChunk' not in content:
    # Add startTime variable at humanize() start
    old_start = "  btn.disabled = true;\n  output.value = '';"
    new_start = "  var startTime = Date.now();\n  btn.disabled = true;\n  output.value = '';"
    if old_start in content and 'startTime' not in content:
        content = content.replace(old_start, new_start, 1)
        print('Fix 4a: Added startTime')
    
    content = content.replace(old4, time_est, 1)
    # Close the time remaining string properly
    old_close = "status.innerHTML = 'Processing... ' + chunksDone + '/' + chunksTotal + ' chunks (' + (prog.progress || 0) + '%)';"
    new_close = "status.innerHTML = 'Processing... ' + chunksDone + '/' + chunksTotal + ' chunks (' + (prog.progress || 0) + '%) | ETA: ' + timeStr;"
    if old_close in content:
        content = content.replace(old_close, new_close, 1)
    print('Fix 4b: Added time remaining')
else:
    print('Fix 4: skipped')

with open('app_v5.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Batch 7 done')
