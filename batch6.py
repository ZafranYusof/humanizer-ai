with open('app_v5.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 23. Update humanize() polling to show step progress + typewriter effect
# Find the polling section where it checks prog.chunks_done
old_poll = "status.innerHTML = 'Processing... ' + (prog.chunks_done || 0) + '/' + (prog.chunks_total || '?') + ' chunks done (' + (prog.progress || 0) + '%)';"
new_poll = """var chunksDone = prog.chunks_done || 0;
        var chunksTotal = prog.chunks_total || '?';
        status.innerHTML = 'Processing... ' + chunksDone + '/' + chunksTotal + ' chunks done (' + (prog.progress || 0) + '%)';
        // Update step progress indicators
        var stepEl = document.getElementById('stepProgress');
        if(stepEl) {
          stepEl.style.display = 'flex';
          var stepsHtml = '';
          var total = typeof chunksTotal === 'number' ? chunksTotal : 1;
          for(var s = 0; s < total; s++) {
            var cls = s < chunksDone ? 'step done' : (s === chunksDone ? 'step active' : 'step');
            stepsHtml += '<span class="' + cls + '">Chunk ' + (s+1) + '</span>';
          }
          stepEl.innerHTML = stepsHtml;
        }
        // Typewriter effect: stream partial result
        if(prog.partial && prog.partial.length > output.value.length) {
          output.value = prog.partial;
          updateWordCount();
        }"""
if old_poll in content and 'stepProgress' not in content:
    content = content.replace(old_poll, new_poll, 1)
    print('Wired: step progress indicators + typewriter streaming')

# 24. Hide step progress when done
old_done = "setTimeout(() => { progressBar.style.display = 'none'; progressFill.style.width = '0%'; }, 2000);"
new_done = """setTimeout(() => { progressBar.style.display = 'none'; progressFill.style.width = '0%'; var sp = document.getElementById('stepProgress'); if(sp) sp.style.display = 'none'; }, 2000);"""
if old_done in content and "stepProgress" not in content:
    content = content.replace(old_done, new_done, 1)
    print('Added: hide step progress on complete')

# 25. Add typewriter cursor on output during processing
old_start = "progressFill.style.width = '2%';"
new_start = """progressFill.style.width = '2%';
  output.classList.add('typewriter-cursor');"""
if old_start in content and 'typewriter-cursor' not in content:
    content = content.replace(old_start, new_start, 1)
    print('Added: typewriter cursor during processing')

# 26. Remove typewriter cursor when done
old_done2 = "output.value = prog.result || prog.partial;"
new_done2 = """output.value = prog.result || prog.partial;
          output.classList.remove('typewriter-cursor');"""
if old_done2 in content and "remove('typewriter-cursor')" not in content:
    content = content.replace(old_done2, new_done2, 1)
    print('Added: remove typewriter cursor on done')

with open('app_v5.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Batch 6: Step progress JS + typewriter streaming done')
