# Wire preprocessing into humanize flow
import re

with open('app_v5.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add preprocessing at start of _run_humanize_job
old_start = '''    def _run_humanize_job(self, job_id, text, passes, model, tone, domain="general", ref_sample="", auto_retry=False, strict_wc=False):
        """Run full humanization in background, updating JOBS dict progressively."""
        t0 = time.time()
        input_words = len(text.split())
        model_label = model or LLM_MODEL'''

new_start = '''    def _run_humanize_job(self, job_id, text, passes, model, tone, domain="general", ref_sample="", auto_retry=False, strict_wc=False):
        """Run full humanization in background, updating JOBS dict progressively."""
        t0 = time.time()
        # Preprocessing pipeline
        text = auto_fix_grammar(text)  # #5: grammar fix
        text, cite_placeholders = preserve_citations(text)  # #8: protect citations
        text, block_placeholders = protect_special_blocks(text)  # #9: protect code/tables/math
        input_words = len(text.split())
        model_label = model or LLM_MODEL'''

if old_start in content:
    content = content.replace(old_start, new_start, 1)
    print("1. Added preprocessing at start")
else:
    print("1. SKIP: start marker not found")

# 2. Add postprocessing after result for short text path (after restore_custom_preserve)
old_post_short = '''                result = apply_custom_avoid(result)
                result = restore_custom_preserve(result)
                elapsed = round(time.time() - t0, 1)
                output_score = calc_detection_score(result)'''

new_post_short = '''                result = apply_custom_avoid(result)
                result = restore_custom_preserve(result)
                # Postprocessing pipeline
                result = replace_ai_phrases(result)  # #17: synonym intelligence
                result = vary_sentence_lengths(result)  # #18: sentence variation
                result = restore_citations(result, cite_placeholders)  # #8: restore citations
                result = restore_special_blocks(result, block_placeholders)  # #9: restore blocks
                elapsed = round(time.time() - t0, 1)
                output_score = calc_detection_score(result)
                similarity = calc_semantic_similarity(text, result)  # #13: semantic similarity'''

if old_post_short in content:
    content = content.replace(old_post_short, new_post_short, 1)
    print("2. Added postprocessing (short path)")
else:
    print("2. SKIP: short path marker not found")

# 3. Find the long text result finalization and add postprocessing there too
# Look for where result is finalized after chunks are joined
old_long_result = '            # Apply domain-specific word replacement'
new_long_result = '''            # Postprocessing pipeline (long text)
            if processed_chunks:
                joined = ' '.join([c for c in processed_chunks if c])
                joined = replace_ai_phrases(joined)
                joined = vary_sentence_lengths(joined)
                joined = restore_citations(joined, cite_placeholders)
                joined = restore_special_blocks(joined, block_placeholders)
                processed_chunks = [joined]

            # Apply domain-specific word replacement'''

if old_long_result in content:
    content = content.replace(old_long_result, new_long_result, 1)
    print("3. Added postprocessing (long path)")
else:
    print("3. SKIP: long path marker not found")

# 4. Add model fallback to the LLM call function
# Find the humanize_chunk function and add fallback
old_llm = 'def humanize_chunk(text, passes, model, tone):'
new_llm = '''def humanize_chunk(text, passes, model, tone):
    """Humanize with model fallback (#137) and retry (#79)."""
    for attempt_model in [model] + MODEL_FALLBACK[:3]:
        try:
            return _humanize_chunk_inner(text, passes, attempt_model, tone)
        except Exception as e:
            print(f"Model {attempt_model} failed: {e}, trying fallback...", flush=True)
            continue
    raise Exception("All models failed")

def _humanize_chunk_inner(text, passes, model, tone):'''

if old_llm in content:
    content = content.replace(old_llm, new_llm, 1)
    print("4. Added model fallback")
else:
    print("4. SKIP: humanize_chunk marker not found")

# 5. Update model latency tracking in LLM calls
# Find where LLM response time is measured
old_perf = 'update_model_latency'
if old_perf not in content:
    # Add latency tracking wrapper
    print("5. Model latency tracking will use MODEL_LATENCY dict")
else:
    print("5. Model latency already present")

with open('app_v5.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nDone! File size: {len(content)} bytes")
