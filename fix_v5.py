"""Fix humanize_chunk function in app_v5.py"""
import re

with open(r"C:\Users\zafra\Desktop\humanizer\app_v5.py", "r", encoding="utf-8") as f:
    code = f.read()

# Find and replace the broken humanize_chunk function
old_func_start = 'def humanize_chunk(chunk, passes, model, tone="casual"):\n    """Humanize a single chunk with quality gate and model fallback."""'
old_func_end = '    # Unlock citations/references\n    result = _unlock_citations(result, placeholders)\n    return result\n\n\n# ─── Main pipeline'

# Find the exact boundaries
start_idx = code.find(old_func_start)
end_idx = code.find('# ─── Main pipeline', start_idx)

if start_idx == -1 or end_idx == -1:
    print(f"ERROR: Could not find function boundaries (start={start_idx}, end={end_idx})")
    # Try alternate search
    lines = code.split('\n')
    for i, line in enumerate(lines):
        if 'def humanize_chunk' in line:
            print(f"  Found def at line {i+1}")
        if 'Main pipeline' in line:
            print(f"  Found pipeline at line {i+1}")
else:
    new_func = '''def humanize_chunk(chunk, passes, model, tone="casual"):
    """Humanize a single chunk with quality gate and model fallback."""
    locked_chunk, placeholders = _lock_citations(chunk)
    
    models_to_try = [model] if model else [LLM_MODEL]
    for fb in MODEL_FALLBACK_CHAIN:
        if fb not in models_to_try:
            models_to_try.append(fb)
    
    result = None
    used_model = models_to_try[0]
    
    for try_model in models_to_try[:3]:
        try:
            candidate = pass1_rewrite(locked_chunk, model=try_model, tone=tone)
            if not candidate or not candidate.strip():
                continue
            
            ok, reason = check_output_quality(locked_chunk, candidate)
            if not ok:
                print(f"[quality] {try_model} failed: {reason}, trying next", flush=True)
                continue
            
            result = candidate
            used_model = try_model
            break
        except Exception as e:
            print(f"[fallback] {try_model} error: {e}", flush=True)
            continue
    
    if not result or not result.strip():
        return _unlock_citations(chunk, placeholders)
    
    if passes >= 2:
        temp = pass2_burstiness(result, model=used_model, tone=tone)
        if not temp or not temp.strip():
            temp = pass2_burstiness(result, model=used_model, tone=tone)
        if temp and temp.strip():
            result = temp

    if passes >= 3:
        temp = pass3_polish(result, model=used_model, tone=tone)
        if not temp or not temp.strip():
            temp = pass3_polish(result, model=used_model, tone=tone)
        if temp and temp.strip():
            result = temp

    result = _unlock_citations(result, placeholders)
    return result


'''
    
    code = code[:start_idx] + new_func + code[end_idx:]
    
    with open(r"C:\Users\zafra\Desktop\humanizer\app_v5.py", "w", encoding="utf-8") as f:
        f.write(code)
    
    print(f"Fixed humanize_chunk function")
