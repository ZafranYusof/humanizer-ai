"""
Detection API module for HumanizeAI.
Wraps ZeroGPT, GPTZero, and CopyLeaks AI-detection APIs.

Usage:
    from features.detection import multi_detect, auto_retry_until_target

All functions return dicts with at minimum {score}. On failure they
return {score: None, error: "..."} so callers never crash.

ZeroGPT – free, no key needed.
GPTZero – needs api_key (free tier 10k words/mo).
CopyLeaks – needs email + api_key.
"""

import json
import re
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Any

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests


# ─── helpers ────────────────────────────────────────────────────────────

def _split_sentences(text: str) -> List[str]:
    """Rough sentence splitter."""
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s for s in parts if s.strip()]


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


# ─── 1. ZeroGPT ─────────────────────────────────────────────────────────

def detect_zerotgpt(text: str) -> Dict[str, Any]:
    """POST to ZeroGPT.  Returns {score, is_human, sentences, ...}."""
    try:
        check_text = text[:5000]
        resp = requests.post(
            "https://api.zerogpt.com/api/detect/detectText",
            json={"input_text": check_text},
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36",
                "Origin": "https://www.zerogpt.com",
                "Referer": "https://www.zerogpt.com/",
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("success"):
            d = data.get("data", {})
            score = d.get("fakePercentage", 0)
            return {
                "score": score,
                "is_human": score < 50,
                "sentences": d.get("aiSentences", []),
                "ai_sentences": d.get("aiSentences", 0),
                "human_sentences": d.get("humanSentences", 0),
                "error": None,
            }
        return {"score": None, "is_human": None, "sentences": [],
                "error": data.get("message", "API error")}
    except Exception as e:
        return {"score": None, "is_human": None, "sentences": [],
                "error": str(e)[:200]}


# ─── 2. GPTZero ─────────────────────────────────────────────────────────

def detect_gptzero(text: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """POST to GPTZero.  Needs api_key.  Returns {score, sentences, ...}."""
    if not api_key:
        return {"score": None, "sentences": [],
                "error": "GPTZero requires api_key"}
    try:
        check_text = text[:5000]
        resp = requests.post(
            "https://api.gptzero.me/v2/predict/text",
            json={"document": check_text},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=15,
        )
        data = resp.json()
        # GPTZero v2 returns documents[0].class_probabilities.ai / overall
        doc = (data.get("documents") or [{}])[0]
        cls = doc.get("class_probabilities", {})
        ai_prob = cls.get("ai", 0)
        score = round(ai_prob * 100, 1)
        sentences_info = doc.get("sentences", [])
        return {
            "score": score,
            "sentences": sentences_info,
            "error": None,
        }
    except Exception as e:
        return {"score": None, "sentences": [], "error": str(e)[:200]}


# ─── 3. CopyLeaks ────────────────────────────────────────────────────────

def detect_copyLeaks(text: str, email: Optional[str] = None,
                     api_key: Optional[str] = None) -> Dict[str, Any]:
    """POST to CopyLeaks Writer Detector.  Needs email+api_key.  Returns {score}."""
    if not email or not api_key:
        return {"score": None, "error": "CopyLeaks requires email and api_key"}
    try:
        # Step 1: get bearer token
        token_resp = requests.post(
            "https://api.copyleaks.com/v3/account/login/api",
            json={"email": email, "key": api_key},
            timeout=15,
        )
        if token_resp.status_code != 200:
            return {"score": None, "error": f"CopyLeaks auth failed: "
                    f"{token_resp.status_code}"}
        bearer = token_resp.json().get("access_token")

        # Step 2: scan
        scan_resp = requests.post(
            "https://api.copyleaks.com/v2/writer-detector",
            json={"text": text[:10000]},
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        data = scan_resp.json()
        score = data.get("summary", {}).get("ai", 0)
        return {"score": round(score * 100, 1) if score <= 1 else score,
                "error": None}
    except Exception as e:
        return {"score": None, "error": str(e)[:200]}


# ─── 4. multi_detect (parallel) ──────────────────────────────────────────

def multi_detect(text: str,
                 gptzero_key: Optional[str] = None,
                 copyleaks_email: Optional[str] = None,
                 copyleaks_key: Optional[str] = None) -> Dict[str, Any]:
    """Run all 3 detectors in parallel.  Returns per-detector + consensus avg."""
    results: Dict[str, Any] = {}

    def _run(name, fn):
        return name, fn()

    tasks = {
        "zerogpt": lambda: detect_zerotgpt(text),
        "gptzero": lambda: detect_gptzero(text, gptzero_key),
        "copyLeaks": lambda: detect_copyLeaks(text, copyleaks_email,
                                               copyleaks_key),
    }

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_run, n, fn): n for n, fn in tasks.items()}
        for fut in as_completed(futures):
            name, res = fut.result()
            results[name] = res

    # consensus: average of available scores
    scores = [r["score"] for r in results.values()
              if r.get("score") is not None]
    results["consensus"] = round(sum(scores) / len(scores), 1) if scores else None
    return results


# ─── 5. per_detector_breakdown ───────────────────────────────────────────

def per_detector_breakdown(text: str) -> List[Dict[str, Any]]:
    """Run ZeroGPT (free) and return structured breakdown."""
    zg = detect_zerotgpt(text)
    row = {
        "name": "ZeroGPT",
        "score": zg.get("score"),
        "confidence": "high" if zg.get("score") is not None else "unavailable",
        "status": "ok" if zg.get("error") is None else zg["error"],
    }
    return [row]


# ─── 6. compare_detectors ────────────────────────────────────────────────

def compare_detectors(text: str) -> List[Dict[str, Any]]:
    """Compare available detectors.  Returns table of {detector, score, ai_prob, human_prob}."""
    zg = detect_zerotgpt(text)
    table = []
    for name, res in [("ZeroGPT", zg)]:
        sc = res.get("score")
        ai = sc if sc is not None else None
        human = round(100 - ai, 1) if ai is not None else None
        table.append({
            "detector": name,
            "score": sc,
            "ai_prob": ai,
            "human_prob": human,
        })
    return table


# ─── 7. highlight_flagged_sentences ──────────────────────────────────────

def highlight_flagged_sentences(
    text: str,
    detector_results: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Mark sentences flagged as AI by detectors."""
    sentences = _split_sentences(text)
    if detector_results is None:
        detector_results = detect_zerotgpt(text)

    flagged = []
    # ZeroGPT may return a list of flagged sentence strings
    zg_flagged = set()
    zg_data = detector_results.get("zerogpt", detector_results)
    if isinstance(zg_data.get("sentences"), list):
        for s in zg_data["sentences"]:
            if isinstance(s, str):
                zg_flagged.add(s.strip()[:80])

    for sent in sentences:
        is_flagged = sent.strip()[:80] in zg_flagged if zg_flagged else False
        detectors = []
        if is_flagged:
            detectors.append("zerogpt")
        flagged.append({
            "sentence": sent,
            "is_flagged": is_flagged,
            "detectors_flagged": detectors,
        })
    return flagged


# ─── 8. predict_score (heuristic) ────────────────────────────────────────

def predict_score(text: str) -> Dict[str, Any]:
    """Heuristic AI-detection score without API calls.
    Based on avg sentence length, vocabulary diversity, passive voice ratio.
    Returns {score, details}.
    """
    sentences = _split_sentences(text)
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    n_words = len(words)
    n_sents = max(len(sentences), 1)

    # avg sentence length (AI text tends 15-25, human 10-30 with more variance)
    avg_len = n_words / n_sents

    # vocabulary diversity (type-token ratio, sampling 200 words)
    sample = words[:200]
    diversity = len(set(sample)) / max(len(sample), 1)

    # passive voice heuristic (presence of "was/were/been/being + past participle")
    passive_patterns = re.findall(
        r'\b(was|were|been|being|is|are|am)\s+\w+ed\b', text.lower())
    passive_ratio = len(passive_patterns) / max(n_sents, 1)

    # blend into 0-100 score
    # high avg_len, low diversity, high passive → more AI-like
    length_score = _clamp((avg_len - 10) * 4, 0, 100)       # 10→0, 35→100
    diversity_score = _clamp((1 - diversity) * 200, 0, 100)   # 1.0→0, 0.5→100
    passive_score = _clamp(passive_ratio * 150, 0, 100)

    raw = length_score * 0.35 + diversity_score * 0.45 + passive_score * 0.20
    score = round(_clamp(raw), 1)

    return {
        "score": score,
        "details": {
            "avg_sentence_length": round(avg_len, 2),
            "vocabulary_diversity": round(diversity, 3),
            "passive_ratio": round(passive_ratio, 3),
            "length_component": round(length_score, 1),
            "diversity_component": round(diversity_score, 1),
            "passive_component": round(passive_score, 1),
        },
    }


# ─── 9. auto_retry_until_target ──────────────────────────────────────────

def auto_retry_until_target(text: str,
                            humanize_fn: Callable[[str], str],
                            target_score: float = 30,
                            max_retries: int = 5) -> Dict[str, Any]:
    """Keep calling humanize_fn until ZeroGPT score < target_score or
    max_retries exhausted.  Returns {final_text, final_score, attempts, history}."""
    current = text
    history = []

    for i in range(max_retries):
        result = detect_zerotgpt(current)
        score = result.get("score")
        history.append({"attempt": i, "score": score, "text_preview": current[:120]})

        if score is not None and score < target_score:
            return {
                "final_text": current,
                "final_score": score,
                "attempts": i + 1,
                "success": True,
                "history": history,
            }

        current = humanize_fn(current)

    # final check after last humanize
    result = detect_zerotgpt(current)
    score = result.get("score")
    history.append({"attempt": max_retries, "score": score, "text_preview": current[:120]})

    return {
        "final_text": current,
        "final_score": score,
        "attempts": max_retries,
        "success": score is not None and score < target_score,
        "history": history,
    }


# ─── test block ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = (
        "Artificial intelligence has transformed the modern world in ways "
        "that were previously unimaginable. Machine learning algorithms "
        "are being deployed across industries to optimize processes and "
        "enhance decision-making capabilities. The rapid advancement of "
        "natural language processing has enabled chatbots and virtual "
        "assistants to understand and respond to human queries with "
        "remarkable accuracy. Furthermore, computer vision technologies "
        "are revolutionizing fields such as healthcare diagnostics, "
        "autonomous vehicles, and security surveillance systems."
    )

    print("=" * 60)
    print("1. detect_zerotgpt")
    zg = detect_zerotgpt(sample)
    print(json.dumps(zg, indent=2, default=str))

    print("\n2. detect_gptzero (no key)")
    gz = detect_gptzero(sample)
    print(json.dumps(gz, indent=2, default=str))

    print("\n3. detect_copyLeaks (no creds)")
    cl = detect_copyLeaks(sample)
    print(json.dumps(cl, indent=2, default=str))

    print("\n4. multi_detect (ZeroGPT only, no keys)")
    md = multi_detect(sample)
    print(json.dumps(md, indent=2, default=str))

    print("\n5. per_detector_breakdown")
    pdb = per_detector_breakdown(sample)
    print(json.dumps(pdb, indent=2, default=str))

    print("\n6. compare_detectors")
    cd = compare_detectors(sample)
    print(json.dumps(cd, indent=2, default=str))

    print("\n7. highlight_flagged_sentences")
    hf = highlight_flagged_sentences(sample)
    for row in hf:
        flag = "FLAGGED" if row["is_flagged"] else "ok"
        print(f"  [{flag}] {row['sentence'][:70]}")

    print("\n8. predict_score (heuristic)")
    ps = predict_score(sample)
    print(json.dumps(ps, indent=2, default=str))

    print("\n9. auto_retry_until_target (demo with identity humanizer)")
    ar = auto_retry_until_target(sample, lambda t: t, target_score=30,
                                  max_retries=1)
    print(json.dumps({k: v for k, v in ar.items() if k != "history"},
                     indent=2, default=str))
