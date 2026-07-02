"""
Quality checks for HumanizeAI output.

Provides grammar/spelling checks (via LanguageTool API),
fact preservation validation, tone consistency analysis,
repetition detection, and AI cliché detection.
"""

import re
import requests
from collections import Counter

LANGUAGETOOL_URL = "https://api.languagetool.org/v2/check"


def grammar_check(text):
    """
    Check grammar using LanguageTool API.
    Returns list of {offset, length, message, suggestions}.
    """
    if not text or not text.strip():
        return []
    try:
        resp = requests.post(
            LANGUAGETOOL_URL,
            data={"text": text, "language": "en-US"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for match in data.get("matches", []):
            suggestions = [r["value"] for r in match.get("replacements", [])[:5]]
            results.append({
                "offset": match["offset"],
                "length": match["length"],
                "message": match["message"],
                "suggestions": suggestions,
            })
        return results
    except Exception as e:
        return [{"offset": 0, "length": 0, "message": f"API Error: {e}", "suggestions": []}]


def spelling_check(text):
    """
    Filter grammar results for spelling-only issues (rule.issueType='misspelling').
    Returns list of {offset, length, message, suggestions}.
    """
    if not text or not text.strip():
        return []
    try:
        resp = requests.post(
            LANGUAGETOOL_URL,
            data={"text": text, "language": "en-US"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for match in data.get("matches", []):
            issue_type = match.get("rule", {}).get("issueType", "")
            if issue_type == "misspelling":
                suggestions = [r["value"] for r in match.get("replacements", [])[:5]]
                results.append({
                    "offset": match["offset"],
                    "length": match["length"],
                    "message": match["message"],
                    "suggestions": suggestions,
                })
        return results
    except Exception as e:
        return [{"offset": 0, "length": 0, "message": f"API Error: {e}", "suggestions": []}]


def _extract_named_terms(text):
    """Extract capitalized multi-word terms (names), numbers, dates."""
    terms = {}
    # Capitalized words (potential names) — 2+ consecutive capitalized words
    for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
        term = m.group(1)
        terms[term] = terms.get(term, 0) + 1
    # Single capitalized words (skip common sentence starters)
    common_starters = {"The", "A", "An", "In", "On", "At", "For", "To", "And", "But", "Or", "So", "Yet", "If", "It", "Is", "This", "That", "These", "Those", "He", "She", "We", "They", "You", "Not", "As", "By", "From", "With", "After", "Before", "Between", "During", "About", "Into", "Through"}
    for m in re.finditer(r'\b([A-Z][a-z]{2,})\b', text):
        word = m.group(1)
        if word not in common_starters:
            terms[word] = terms.get(word, 0) + 1
    # Numbers
    for m in re.finditer(r'\b\d[\d,.]*\b', text):
        term = m.group(0)
        terms[term] = terms.get(term, 0) + 1
    return terms


def consistency_check(original, humanized):
    """
    Check that names, numbers, and dates are preserved.
    Returns list of {term, original_count, humanized_count}.
    """
    orig_terms = _extract_named_terms(original)
    hum_terms = _extract_named_terms(humanized)
    results = []
    all_terms = set(orig_terms.keys()) | set(hum_terms.keys())
    for term in all_terms:
        o_count = orig_terms.get(term, 0)
        h_count = hum_terms.get(term, 0)
        if o_count != h_count:
            results.append({
                "term": term,
                "original_count": o_count,
                "humanized_count": h_count,
            })
    return results


def fact_preservation_check(original, humanized):
    """
    Extract numbers/dates/percentages via regex, compare preservation.
    Returns list of {fact_type, original, humanized, preserved}.
    """
    patterns = [
        ("percentage", r'\b\d[\d,.]*\s*%'),
        ("date_iso", r'\b\d{4}-\d{2}-\d{2}\b'),
        ("date_written", r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}\b'),
        ("year", r'\b(1[89]\d{2}|20\d{2})\b'),
        ("number_with_unit", r'\b\d[\d,.]*\s*(?:million|billion|trillion|thousand|hundred|kg|km|miles|meters|pounds|tons|hours|minutes|seconds|days|weeks|months|years)\b'),
        ("currency", r'\$\d[\d,.]*'),
        ("fraction", r'\b\d+/\d+\b'),
        ("decimal_number", r'\b\d+\.\d+\b'),
        ("standalone_number", r'\b\d{2,}\b'),
    ]
    results = []
    seen_original = set()
    for fact_type, pattern in patterns:
        for m in re.finditer(pattern, original, re.I):
            val = m.group(0).strip()
            if val in seen_original:
                continue
            seen_original.add(val)
            preserved = val.lower() in humanized.lower()
            results.append({
                "fact_type": fact_type,
                "original": val,
                "humanized": val if preserved else "[MISSING]",
                "preserved": preserved,
            })
    return results


def tone_consistency(text):
    """
    Detect tone sections and return consistency analysis.
    Returns {dominant_tone, consistency_score, mixed_sections}.
    """
    if not text or not text.strip():
        return {"dominant_tone": "unknown", "consistency_score": 1.0, "mixed_sections": []}

    # Split into paragraphs/sections
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if len(paragraphs) < 2:
        return {"dominant_tone": "uniform", "consistency_score": 1.0, "mixed_sections": []}

    # Tone keyword dictionaries
    tone_indicators = {
        "formal": ["furthermore", "moreover", "consequently", "therefore", "hereby", "notwithstanding", "pursuant", "aforementioned", "shall", "henceforth"],
        "casual": ["gonna", "wanna", "kinda", "sorta", "hey", "yeah", "cool", "awesome", "stuff", "thing", "pretty much", "a lot", "really", "totally"],
        "technical": ["implement", "utilize", "methodology", "framework", "architecture", "infrastructure", "parameter", "interface", "algorithm", "protocol"],
        "persuasive": ["must", "should", "essential", "crucial", "imperative", "vital", "unmissable", "transform", "revolutionize", "game-changer"],
        "analytical": ["however", "although", "whereas", "conversely", "contrast", "compare", "analysis", "evidence", "suggests", "indicates"],
    }

    section_tones = []
    for para in paragraphs:
        para_lower = para.lower()
        scores = {}
        for tone, keywords in tone_indicators.items():
            count = sum(1 for kw in keywords if kw in para_lower)
            scores[tone] = count
        best_tone = max(scores, key=scores.get)
        best_score = scores[best_tone]
        section_tones.append(best_tone if best_score > 0 else "neutral")

    # Compute consistency
    tone_counts = Counter(section_tones)
    dominant = tone_counts.most_common(1)[0][0]
    consistency_score = tone_counts[dominant] / len(section_tones) if section_tones else 1.0

    # Mixed sections = sections whose tone differs from dominant
    mixed = []
    for i, tone in enumerate(section_tones):
        if tone != dominant and tone != "neutral":
            mixed.append({"section_index": i, "tone": tone})

    return {
        "dominant_tone": dominant,
        "consistency_score": round(consistency_score, 2),
        "mixed_sections": mixed,
    }


def repetition_detector(text):
    """
    Find phrases repeated 3+ times using n-grams.
    Returns list of {phrase, count, locations}.
    """
    if not text or not text.strip():
        return []

    results = []
    text_lower = text.lower()
    # Use 2-gram through 5-gram
    for n in range(2, 6):
        words = text_lower.split()
        if len(words) < n:
            continue
        ngram_positions = {}
        for i in range(len(words) - n + 1):
            phrase = ' '.join(words[i:i + n])
            # Skip ngrams that are mostly stop words
            if phrase not in ngram_positions:
                ngram_positions[phrase] = []
            ngram_positions[phrase].append(i)

        for phrase, positions in ngram_positions.items():
            if len(positions) >= 3:
                # Find character offsets
                locations = []
                search_from = 0
                for pos_idx in range(len(positions)):
                    idx = text_lower.find(phrase, search_from)
                    if idx >= 0:
                        locations.append(idx)
                        search_from = idx + 1
                if len(locations) >= 3:
                    results.append({
                        "phrase": phrase,
                        "count": len(locations),
                        "locations": locations,
                    })

    # Deduplicate: if a longer ngram contains a shorter one with same count, keep only longer
    results.sort(key=lambda x: (-len(x["phrase"].split()), -x["count"]))
    final = []
    seen_phrases = set()
    for r in results:
        skip = False
        for seen in seen_phrases:
            if r["phrase"] in seen:
                skip = True
                break
        if not skip:
            final.append(r)
            seen_phrases.add(r["phrase"])

    return final[:50]  # cap output


# 100+ AI clichés with suggested alternatives
_CLICHE_LIST = [
    ("delve", "explore / examine"),
    ("tapestry", "mix / blend / combination"),
    ("navigate", "deal with / handle / work through"),
    ("landscape", "field / area / scene"),
    ("moreover", "also / plus / and"),
    ("furthermore", "also / what's more / besides"),
    ("in conclusion", "to sum up / finally / [omit]"),
    ("it is important to note", "[omit or rewrite]"),
    ("it should be noted", "[omit or rewrite]"),
    ("it is worth noting", "[omit or rewrite]"),
    ("in today's world", "[omit — filler]"),
    ("in this day and age", "[omit — filler]"),
    ("at the end of the day", "[omit — filler]"),
    ("a game changer", "a major shift / a turning point"),
    ("game-changing", "transformative / major"),
    ("cutting-edge", "latest / advanced / new"),
    ("state-of-the-art", "modern / advanced / top-quality"),
    ("paradigm shift", "big change / fundamental change"),
    ("leverage", "use / take advantage of"),
    ("utilize", "use"),
    ("synergy", "cooperation / teamwork / combined effort"),
    ("holistic", "comprehensive / whole / broad"),
    ("robust", "strong / solid / reliable"),
    ("seamless", "smooth / easy / effortless"),
    ("streamline", "simplify / speed up / tidy up"),
    ("foster", "build / encourage / support"),
    ("spearhead", "lead / drive / push"),
    ("spearheading", "leading / driving"),
    ("multifaceted", "complex / varied / many-sided"),
    ("nuanced", "subtle / detailed / layered"),
    ("underscores", "highlights / shows / emphasizes"),
    ("underscore", "highlight / show / emphasize"),
    ("a testament to", "proof of / evidence of"),
    ("embark", "start / begin / set out"),
    ("underscores the importance", "shows why [X] matters"),
    ("it cannot be denied", "[state directly]"),
    ("there is no denying", "[state directly]"),
    ("unprecedented", "unusual / rare / never seen before"),
    ("pivotal", "key / important / central"),
    ("myriad", "many / countless"),
    ("plethora", "too many / a flood of"),
    ("aforementioned", "mentioned above / this"),
    ("hereby", "[omit or rewrite]"),
    ("hence", "so / therefore / that's why"),
    ("henceforth", "from now on"),
    ("therein", "in that / there"),
    ("wherein", "in which / where"),
    ("whilst", "while"),
    ("amongst", "among"),
    ("bespoke", "custom / tailored / made-to-order"),
    ("curated", "picked / chosen / selected"),
    ("elevate", "raise / improve / boost"),
    ("empower", "enable / help / support"),
    ("impactful", "effective / meaningful / powerful"),
    ("innovative", "new / creative / fresh"),
    ("groundbreaking", "new / first-of-its-kind / bold"),
    ("transformative", "life-changing / big / significant"),
    ("dynamic", "lively / active / changing"),
    ("ecosystem", "network / system / community"),
    ("bandwidth", "time / capacity / resources"),
    ("deep dive", "close look / detailed review"),
    ("move the needle", "make a real difference"),
    ("low-hanging fruit", "easy wins / quick fixes"),
    ("boots on the ground", "people on site / local staff"),
    ("circle back", "follow up / come back to"),
    ("touch base", "check in / talk"),
    ("double down", "commit harder / focus more"),
    ("lean in", "engage / commit"),
    ("pivot", "change direction / shift focus"),
    ("disruption", "upheaval / shakeup / change"),
    ("at scale", "on a large scale / in large amounts"),
    ("value proposition", "what you offer / your pitch"),
    ("thought leadership", "expertise / authority"),
    ("best practices", "good methods / proven ways"),
    ("data-driven", "based on data / evidence-based"),
    ("actionable", "practical / useful / doable"),
    ("scalable", "can grow / adaptable"),
    ("mission-critical", "essential / must-have"),
    ("world-class", "top-quality / excellent"),
    ("next-generation", "new / latest / updated"),
    ("turnkey", "ready to use / out-of-the-box"),
    ("end-to-end", "full / complete / from start to finish"),
    ("cloud-native", "built for the cloud"),
    ("AI-powered", "uses AI / AI-driven"),
    ("blockchain-based", "uses blockchain"),
    ("quantum leap", "big jump / huge advance"),
    ("silver bullet", "simple fix / magic solution"),
    ("the devil is in the details", "[omit — cliché]"),
    ("only time will tell", "[omit — filler]"),
    ("last but not least", "finally / also"),
    ("needless to say", "[omit — filler]"),
    ("having said that", "but / however / still"),
    ("that said", "but / however / still"),
    ("with that being said", "but / however / still"),
    ("in light of", "because of / given"),
    ("in terms of", "for / when it comes to"),
    ("with respect to", "about / for / regarding"),
    ("in the realm of", "in / among"),
    ("on the other hand", "but / conversely"),
    ("conversely", "but / the opposite / on the flip side"),
    ("notwithstanding", "despite / regardless"),
    ("heretofore", "until now / previously"),
    ("insofar as", "as far as / to the extent that"),
    ("in order to", "to"),
    ("due to the fact that", "because"),
    ("for the purpose of", "to / for"),
    ("in the event that", "if"),
    ("a large number of", "many"),
    ("a significant number of", "many"),
    ("the vast majority of", "most"),
    ("it is clear that", "[omit — just state it]"),
    ("it goes without saying", "[omit — filler]"),
    ("the fact of the matter is", "[omit — filler]"),
    ("at this point in time", "now / currently"),
    ("in the near future", "soon"),
    ("on a regular basis", "regularly"),
    ("in a timely manner", "quickly / on time"),
    ("with regard to", "about / regarding"),
    ("it has come to my attention", "[omit — filler]"),
    ("pursuant to", "under / per"),
    ("in conjunction with", "with / along with"),
    ("cognizant of", "aware of"),
    ("juxtapose", "compare / put side by side"),
    ("catalyst", "trigger / spark / cause"),
    ("catalyze", "trigger / spark / speed up"),
    ("ameliorate", "improve / fix / ease"),
    ("commensurate", "proportional / matching"),
    ("exacerbate", "worsen / make worse"),
    ("facilitate", "help / enable / make easier"),
    ("optimize", "improve / fine-tune"),
    ("synthesize", "combine / blend / pull together"),
    ("ubiquitous", "everywhere / widespread"),
    ("unparalleled", "unmatched / unique / best"),
    ("venerable", "respected / old / established"),
]

# Pre-compile pattern for performance
_CLECHE_PATTERNS = [(cliche, re.compile(r'\b' + re.escape(cliche) + r'\b', re.I), suggestion)
                    for cliche, suggestion in _CLICHE_LIST]


def cliche_detector(text):
    """
    Flag AI clichés from a predefined list of 100+ entries.
    Returns list of {cliche, location, suggestion}.
    """
    if not text or not text.strip():
        return []
    results = []
    for cliche_word, pattern, suggestion in _CLECHE_PATTERNS:
        for m in pattern.finditer(text):
            results.append({
                "cliche": cliche_word,
                "location": m.start(),
                "suggestion": suggestion,
            })
    return results


def run_all_checks(original, humanized):
    """
    Run all quality checks on original vs humanized text.
    Returns combined report dict.
    """
    return {
        "grammar": grammar_check(humanized),
        "spelling": spelling_check(humanized),
        "consistency": consistency_check(original, humanized),
        "fact_preservation": fact_preservation_check(original, humanized),
        "tone_consistency": tone_consistency(humanized),
        "repetition": repetition_detector(humanized),
        "cliches": cliche_detector(humanized),
        "summary": {
            "grammar_issues": 0,  # filled below
            "spelling_issues": 0,
            "consistency_issues": 0,
            "facts_lost": 0,
            "tone_score": 0.0,
            "repetitions": 0,
            "cliches_found": 0,
        },
    }


def _fill_summary(report):
    """Fill the summary sub-dict with counts from each check."""
    s = report["summary"]
    s["grammar_issues"] = len(report.get("grammar", []))
    s["spelling_issues"] = len(report.get("spelling", []))
    s["consistency_issues"] = len(report.get("consistency", []))
    facts = report.get("fact_preservation", [])
    s["facts_lost"] = sum(1 for f in facts if not f.get("preserved", True))
    s["tone_score"] = report.get("tone_consistency", {}).get("consistency_score", 0.0)
    s["repetitions"] = len(report.get("repetition", []))
    s["cliches_found"] = len(report.get("cliches", []))
    return report


# Patch run_all_checks to include summary
_original_run_all = run_all_checks

def run_all_checks(original, humanized):
    """Run all quality checks, return combined report with summary."""
    report = _original_run_all(original, humanized)
    return _fill_summary(report)


if __name__ == "__main__":
    # --- Test block ---
    test_original = (
        "Artificial intelligence has revolutionized healthcare. According to Smith (2023), "
        "AI diagnostic tools achieved 95% accuracy in detecting early-stage cancers. "
        "The technology leverages cutting-edge algorithms to streamline the diagnostic process. "
        "Moreover, it is important to note that AI-powered systems are a game changer. "
        "The vast majority of hospitals plan to implement AI solutions by 2025."
    )
    test_humanized = (
        "AI has changed how healthcare works. Smith (2023) reported that AI tools "
        "reached 95% accuracy in spotting early-stage cancers. The technology uses "
        "advanced algorithms to speed up diagnosis. Also, AI systems are a major shift "
        "for the industry. Most hospitals plan to roll out AI by 2025."
    )

    print("=== Grammar Check ===")
    gc = grammar_check(test_humanized[:200])
    print(f"  Issues found: {len(gc)}")
    for g in gc[:3]:
        print(f"  - {g['message']} (offset {g['offset']})")

    print("\n=== Consistency Check ===")
    cc = consistency_check(test_original, test_humanized)
    for c in cc[:5]:
        print(f"  '{c['term']}': orig={c['original_count']}, hum={c['humanized_count']}")

    print("\n=== Fact Preservation ===")
    fp = fact_preservation_check(test_original, test_humanized)
    for f in fp:
        print(f"  {f['fact_type']}: '{f['original']}' preserved={f['preserved']}")

    print("\n=== Tone Consistency ===")
    tone = tone_consistency(test_humanized)
    print(f"  Dominant: {tone['dominant_tone']}, Score: {tone['consistency_score']}")

    print("\n=== Repetition Detector ===")
    rep_text = "This is a test phrase. " * 5 + "Different text here."
    reps = repetition_detector(rep_text)
    for r in reps[:3]:
        print(f"  '{r['phrase']}' x{r['count']}")

    print("\n=== Cliché Detector ===")
    cliches = cliche_detector(test_original)
    for c in cliches[:5]:
        print(f"  '{c['cliche']}' at {c['location']} → {c['suggestion']}")

    print("\n=== Run All Checks ===")
    report = run_all_checks(test_original, test_humanized)
    print(f"  Summary: {report['summary']}")
