"""
HumanizeAI — History, Analytics & Drafts module.

Provides 20 functions:
  1-10  : job history CRUD, search, star, tag, export
  11-17 : analytics / chart data
  18-20 : draft helpers (client-side localStorage mirror)
"""

import json
import os
import csv
import io
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
_JOBS_PATH = os.path.join(_DIR, "..", "jobs.json")
_DRAFTS_PATH = os.path.join(_DIR, "..", "drafts.json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _load_jobs() -> list[dict]:
    """Load jobs list from jobs.json, return [] on any error."""
    try:
        with open(_JOBS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_jobs(jobs: list[dict]) -> None:
    """Persist jobs list to jobs.json."""
    with open(_JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)


def _load_drafts() -> dict:
    try:
        with open(_DRAFTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_drafts(drafts: dict) -> None:
    with open(_DRAFTS_PATH, "w", encoding="utf-8") as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)


# ===================================================================
# 1-10  HISTORY
# ===================================================================

def get_job_history(limit: int = 50, offset: int = 0) -> list[dict]:
    """Return paginated job list (newest first)."""
    jobs = _load_jobs()
    jobs.sort(key=lambda j: j.get("timestamp", ""), reverse=True)
    return jobs[offset: offset + limit]


def search_history(query: str) -> list[dict]:
    """Search input_text and output_text for *query* (case-insensitive)."""
    q = query.lower()
    return [
        j for j in _load_jobs()
        if q in (j.get("input_text") or "").lower()
        or q in (j.get("output_text") or "").lower()
    ]


def get_job(job_id: str) -> dict | None:
    """Return single job by id, or None."""
    for j in _load_jobs():
        if j.get("id") == job_id:
            return j
    return None


def delete_job(job_id: str) -> bool:
    """Delete one job. Returns True if found & removed."""
    jobs = _load_jobs()
    before = len(jobs)
    jobs = [j for j in jobs if j.get("id") != job_id]
    if len(jobs) < before:
        _save_jobs(jobs)
        return True
    return False


def bulk_delete_jobs(job_ids: list[str]) -> int:
    """Delete multiple jobs. Returns count removed."""
    ids = set(job_ids)
    jobs = _load_jobs()
    kept = [j for j in jobs if j.get("id") not in ids]
    removed = len(jobs) - len(kept)
    if removed:
        _save_jobs(kept)
    return removed


def bulk_delete_by_date(start_date: str, end_date: str) -> int:
    """Delete jobs whose timestamp falls within [start_date, end_date].

    Dates should be ISO-8601 strings (e.g. '2025-01-01').
    Returns count removed.
    """
    sd = start_date[:10]
    ed = end_date[:10]
    jobs = _load_jobs()
    kept = []
    removed = 0
    for j in jobs:
        ts = (j.get("timestamp") or "")[:10]
        if sd <= ts <= ed:
            removed += 1
        else:
            kept.append(j)
    if removed:
        _save_jobs(kept)
    return removed


def star_job(job_id: str) -> dict | None:
    """Toggle starred flag on a job. Returns updated job or None."""
    jobs = _load_jobs()
    for j in jobs:
        if j.get("id") == job_id:
            j["starred"] = not j.get("starred", False)
            _save_jobs(jobs)
            return j
    return None


def tag_job(job_id: str, tag: str) -> dict | None:
    """Append *tag* to job's tags list (no duplicates). Returns job or None."""
    jobs = _load_jobs()
    for j in jobs:
        if j.get("id") == job_id:
            tags = j.setdefault("tags", [])
            if tag not in tags:
                tags.append(tag)
            _save_jobs(jobs)
            return j
    return None


def get_starred_jobs() -> list[dict]:
    """Return all starred jobs."""
    return [j for j in _load_jobs() if j.get("starred")]


def export_history_csv() -> str:
    """Return CSV string of all jobs."""
    jobs = _load_jobs()
    default_fields = ["id", "input_text", "output_text", "score_before", "score_after",
                      "words_before", "words_after", "retention", "status", "model", "timestamp", "processing_time"]
    if not jobs:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=default_fields, extrasaction="ignore")
        writer.writeheader()
        return buf.getvalue()
    # Collect all keys across jobs
    fieldnames = []
    seen = set()
    for j in jobs:
        for k in j:
            if k not in seen:
                fieldnames.append(k)
                seen.add(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for j in jobs:
        row = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in j.items()}
        writer.writerow(row)
    return buf.getvalue()


# ===================================================================
# 11-17  ANALYTICS
# ===================================================================

def get_usage_dashboard() -> dict:
    jobs = _load_jobs()
    if not jobs:
        return {
            "total_words": 0, "total_jobs": 0,
            "avg_score_before": 0, "avg_score_after": 0,
            "avg_improvement": 0, "model_usage": {},
        }
    total_words = sum(j.get("words_before", 0) or 0 for j in jobs)
    sb = [j.get("score_before", 0) or 0 for j in jobs]
    sa = [j.get("score_after", 0) or 0 for j in jobs]
    model_usage: dict[str, int] = defaultdict(int)
    for j in jobs:
        model_usage[j.get("model", "unknown")] += 1
    return {
        "total_words": total_words,
        "total_jobs": len(jobs),
        "avg_score_before": round(sum(sb) / len(sb), 2),
        "avg_score_after": round(sum(sa) / len(sa), 2),
        "avg_improvement": round((sum(sb) - sum(sa)) / len(sb), 2),
        "model_usage": dict(model_usage),
    }


def get_model_leaderboard() -> list[dict]:
    """Return per-model stats sorted by avg_score (ascending = better)."""
    jobs = _load_jobs()
    groups: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        groups[j.get("model", "unknown")].append(j)
    result = []
    for model, jj in groups.items():
        scores_after = [j.get("score_after", 0) or 0 for j in jj]
        times = [j.get("processing_time", 0) or 0 for j in jj]
        successful = sum(1 for s in scores_after if s < 30)
        result.append({
            "model": model,
            "avg_score": round(sum(scores_after) / len(scores_after), 2) if scores_after else 0,
            "total_jobs": len(jj),
            "avg_time": round(sum(times) / len(times), 2) if times else 0,
            "success_rate": round(successful / len(jj) * 100, 2) if jj else 0,
        })
    result.sort(key=lambda r: r["avg_score"])
    return result


def get_processing_time_chart() -> dict:
    """Chart data: avg processing time per model per date."""
    jobs = _load_jobs()
    # {(date, model): [times]}
    bucket: dict[tuple[str, str], list[float]] = defaultdict(list)
    for j in jobs:
        date = (j.get("timestamp") or "")[:10]
        model = j.get("model", "unknown")
        bucket[(date, model)].append(j.get("processing_time", 0) or 0)
    dates = sorted({d for d, _ in bucket})
    models = sorted({m for _, m in bucket})
    datasets = []
    for m in models:
        datasets.append({
            "model": m,
            "times": [round(sum(bucket.get((d, m), [0])) / max(len(bucket.get((d, m), [0])), 1), 2) for d in dates],
        })
    return {"labels": dates, "datasets": datasets}


def get_word_count_distribution() -> dict:
    bins = ["0-100", "100-500", "500-1000", "1000-5000", "5000+"]
    counts = [0, 0, 0, 0, 0]
    for j in _load_jobs():
        w = j.get("words_before", 0) or 0
        if w < 100:
            counts[0] += 1
        elif w < 500:
            counts[1] += 1
        elif w < 1000:
            counts[2] += 1
        elif w < 5000:
            counts[3] += 1
        else:
            counts[4] += 1
    return {"bins": bins, "counts": counts}


def get_success_rate() -> dict:
    jobs = _load_jobs()
    total = len(jobs)
    successful = sum(1 for j in jobs if (j.get("score_after", 100) or 100) < 30)
    failed = total - successful
    return {
        "total": total,
        "successful": successful,
        "failed": failed,
        "rate": round(successful / total * 100, 2) if total else 0,
    }


def get_score_trend() -> dict:
    """Line chart: avg score_before & score_after per date."""
    jobs = _load_jobs()
    bucket_before: dict[str, list[float]] = defaultdict(list)
    bucket_after: dict[str, list[float]] = defaultdict(list)
    for j in jobs:
        date = (j.get("timestamp") or "")[:10]
        bucket_before[date].append(j.get("score_before", 0) or 0)
        bucket_after[date].append(j.get("score_after", 0) or 0)
    dates = sorted(set(bucket_before) | set(bucket_after))
    return {
        "labels": dates,
        "before": [round(sum(bucket_before[d]) / len(bucket_before[d]), 2) if d in bucket_before else 0 for d in dates],
        "after": [round(sum(bucket_after[d]) / len(bucket_after[d]), 2) if d in bucket_after else 0 for d in dates],
    }


def get_improvement_histogram() -> dict:
    improvements = []
    for j in _load_jobs():
        sb = j.get("score_before", 0) or 0
        sa = j.get("score_after", 0) or 0
        improvements.append(sb - sa)
    avg = round(sum(improvements) / len(improvements), 2) if improvements else 0
    return {"improvements": improvements, "avg_improvement": avg}


# ===================================================================
# 18-20  DRAFTS
# ===================================================================

def save_draft_to_json(text: str, name: str = "untitled") -> None:
    drafts = _load_drafts()
    drafts[name] = {"text": text, "saved": __import__('datetime').datetime.now().isoformat()}
    _save_drafts(drafts)


def load_drafts_from_json() -> dict:
    return _load_drafts()


def delete_draft_from_json(name: str) -> bool:
    drafts = _load_drafts()
    if name in drafts:
        del drafts[name]
        _save_drafts(drafts)
        return True
    return False


# ===================================================================
# TEST BLOCK
# ===================================================================
if __name__ == "__main__":
    import tempfile, shutil

    print("=== history_analytics self-test ===")
    tmp = tempfile.mkdtemp()
    # Patch paths
    orig_jobs, orig_drafts = _JOBS_PATH, _DRAFTS_PATH
    _JOBS_PATH = os.path.join(tmp, "jobs.json")
    _DRAFTS_PATH = os.path.join(tmp, "drafts.json")

    # Seed test data
    _save_jobs([
        {"id": "1", "input_text": "hello world", "output_text": "hi earth",
         "score_before": 80, "score_after": 10, "words_before": 50, "words_after": 48,
         "retention": 96, "status": "completed", "model": "gpt-4o",
         "timestamp": "2025-06-01T12:00:00", "processing_time": 2.1},
        {"id": "2", "input_text": "test input", "output_text": "test output",
         "score_before": 60, "score_after": 25, "words_before": 200, "words_after": 190,
         "retention": 95, "status": "completed", "model": "claude-3",
         "timestamp": "2025-06-02T15:30:00", "processing_time": 1.5},
        {"id": "3", "input_text": "long text here", "output_text": "refined text",
         "score_before": 70, "score_after": 35, "words_before": 1200, "words_after": 1100,
         "retention": 92, "status": "completed", "model": "gpt-4o",
         "timestamp": "2025-06-03T09:00:00", "processing_time": 3.0},
    ])

    # History tests
    assert len(get_job_history()) == 3
    assert len(get_job_history(limit=1)) == 1
    assert get_job_history(limit=1, offset=2)[0]["id"] == "1"  # sorted desc
    print("  get_job_history OK")

    assert len(search_history("hello")) == 1
    assert len(search_history("test")) == 1
    print("  search_history OK")

    assert get_job("2")["id"] == "2"
    assert get_job("nope") is None
    print("  get_job OK")

    assert star_job("2")["starred"] is True
    assert star_job("2")["starred"] is False  # toggle
    print("  star_job OK")

    tagged = tag_job("1", "important")
    assert "important" in tagged["tags"]
    assert tag_job("1", "important")["tags"].count("important") == 1  # no dupe
    print("  tag_job OK")

    tag_job("2", "review")
    assert len(get_starred_jobs()) == 0
    star_job("1")
    assert len(get_starred_jobs()) == 1
    print("  get_starred_jobs OK")

    csv_str = export_history_csv()
    assert "id" in csv_str and "hello world" in csv_str
    print("  export_history_csv OK")

    assert delete_job("3") is True
    assert delete_job("3") is False
    assert len(get_job_history(limit=99)) == 2
    print("  delete_job OK")

    # Re-add for bulk tests
    _save_jobs([
        {"id": "a"}, {"id": "b"}, {"id": "c"},
        {"id": "d", "timestamp": "2025-07-01T00:00:00"},
    ])
    assert bulk_delete_jobs(["a", "c"]) == 2
    assert len(_load_jobs()) == 2
    print("  bulk_delete_jobs OK")

    assert bulk_delete_by_date("2025-07-01", "2025-07-31") == 1
    print("  bulk_delete_by_date OK")

    # Analytics tests (restore richer data)
    _save_jobs([
        {"id": "1", "input_text": "a", "output_text": "b", "score_before": 80, "score_after": 10,
         "words_before": 50, "words_after": 48, "model": "gpt-4o", "timestamp": "2025-06-01T00:00:00", "processing_time": 2},
        {"id": "2", "input_text": "c", "output_text": "d", "score_before": 60, "score_after": 25,
         "words_before": 200, "words_after": 190, "model": "claude-3", "timestamp": "2025-06-02T00:00:00", "processing_time": 1},
        {"id": "3", "input_text": "e", "output_text": "f", "score_before": 70, "score_after": 35,
         "words_before": 1200, "words_after": 1100, "model": "gpt-4o", "timestamp": "2025-06-01T00:00:00", "processing_time": 3},
    ])
    d = get_usage_dashboard()
    assert d["total_jobs"] == 3
    assert d["total_words"] == 1450
    assert d["model_usage"]["gpt-4o"] == 2
    print("  get_usage_dashboard OK")

    lb = get_model_leaderboard()
    assert lb[0]["model"] == "gpt-4o"  # lower avg_score first
    print("  get_model_leaderboard OK")

    ptc = get_processing_time_chart()
    assert len(ptc["labels"]) == 2
    print("  get_processing_time_chart OK")

    wd = get_word_count_distribution()
    assert sum(wd["counts"]) == 3
    print("  get_word_count_distribution OK")

    sr = get_success_rate()
    assert sr["successful"] == 2  # score_after 10 and 25 < 30
    assert sr["rate"] == round(2/3*100, 2)
    print("  get_success_rate OK")

    st = get_score_trend()
    assert len(st["labels"]) == 2
    print("  get_score_trend OK")

    ih = get_improvement_histogram()
    assert len(ih["improvements"]) == 3
    assert ih["avg_improvement"] == round((70+35+35)/3, 2)
    print("  get_improvement_histogram OK")

    # Drafts
    save_draft_to_json({"my_draft": {"text": "hello"}})
    assert load_drafts_from_json()["my_draft"]["text"] == "hello"
    assert delete_draft_from_json("my_draft") is True
    assert delete_draft_from_json("my_draft") is False
    print("  drafts OK")

    # Cleanup
    shutil.rmtree(tmp)
    _JOBS_PATH, _DRAFTS_PATH = orig_jobs, orig_drafts

    print("=== ALL TESTS PASSED ===")
