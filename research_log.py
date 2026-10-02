"""Append-only research registry + data-snooping controls (AUDIT 2026-09-28).
  python3 research_log.py check          fail if any committed registry line was edited or deleted (append-only)
  python3 research_log.py burned DATASET START END   list registered experiments that already used that window
  python3 research_log.py fdr            Benjamini-Hochberg across ALL confirmatory edge tests ever registered
  python3 research_log.py summary        counts: hypotheses, confirmatory tests, exploratory cells, parameters searched
Rules: every hypothesis (failed ones too) gets a line before it runs; results are appended as NEW lines ("event": "result"),
never by editing old ones. A future edge claim must survive BH at q=0.10 over every confirmatory test in this file."""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).parent
REG = ROOT / "research" / "registry.jsonl"
Q_FDR = 0.10


def entries():
    return [json.loads(l) for l in REG.read_text().splitlines() if l.strip()]


def check():
    committed = subprocess.run(["git", "show", f"HEAD:{REG.relative_to(ROOT)}"], cwd=ROOT, capture_output=True, text=True)
    if committed.returncode:
        return print("registry not committed yet: nothing to compare")
    old, new = committed.stdout.splitlines(), REG.read_text().splitlines()
    if new[: len(old)] != old:
        raise SystemExit("REGISTRY VIOLATION: committed lines were modified or deleted (append-only)")
    print(f"registry OK: {len(old)} committed lines intact, {len(new) - len(old)} new appended")


def burned(dataset, start, end):
    """Experiments that already used [start, end] of `dataset` for DISCOVERY or TESTING: that window is not fresh confirmation data."""
    hits = []
    for e in entries():
        for w in e.get("data_windows", []):
            if w["dataset"] == dataset and not (w["end"] < start or w["start"] > end):
                hits.append((e["id"], w["role"], w["start"], w["end"]))
    return hits


def require_fresh(experiment_id, dataset, start, end):
    """Called at the top of every experiment script. Refuses to run unless `experiment_id` is registered AND every other
    experiment that already used this window is refused. Re-running a registered experiment on its own window is allowed."""
    ids = {e["id"] for e in entries() if e.get("event") == "registered"}
    if experiment_id not in ids:
        raise SystemExit(f"REFUSED: {experiment_id} is not registered in research/registry.jsonl (register before running)")
    others = [h for h in burned(dataset, start, end) if h[0] != experiment_id]
    mine = [h for h in burned(dataset, start, end) if h[0] == experiment_id]
    if others and not mine:
        raise SystemExit(f"REFUSED: {dataset} {start}..{end} already used by {sorted({h[0] for h in others})}; use fresh (forward) data")


def fdr():
    tests = [(e["id"], e["p_one_sided"]) for e in entries() if e.get("event") == "result" and e.get("p_one_sided") is not None]
    m = len(tests)
    ranked = sorted(tests, key=lambda t: t[1])
    passed, k_max = set(), 0
    for k, (i, p) in enumerate(ranked, 1):
        if p <= k / m * Q_FDR:
            k_max = k
    passed = {i for i, _ in ranked[:k_max]}
    print(f"Benjamini-Hochberg at q={Q_FDR} over {m} confirmatory edge tests:")
    for i, p in ranked:
        print(f"  {i:28} p={p:.4f}  {'SURVIVES' if i in passed else 'no'}")
    return passed


def summary():
    es = entries()
    hyp = [e for e in es if e.get("event") == "registered"]
    res = [e for e in es if e.get("event") == "result"]
    print(f"hypotheses registered {len(hyp)} | results {len(res)} | confirmatory tests {sum(e.get('n_confirmatory', 0) for e in hyp)} | "
          f"exploratory cells looked at {sum(e.get('n_exploratory_cells', 0) for e in hyp)} | parameter settings searched "
          f"{sum(e.get('n_params_searched', 0) for e in hyp)} | open {sum(1 for e in hyp if e['id'] not in {r['id'] for r in res})}")


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "check":
        check()
    elif c == "burned":
        for h in burned(*sys.argv[2:5]):
            print(*h)
    elif c == "fdr":
        fdr()
    else:
        summary()
