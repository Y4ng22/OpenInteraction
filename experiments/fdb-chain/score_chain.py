"""Score run_chain.py logs with FDB-v3's own scorer.

Only FDB tool calls count, whoever made them (frontend or backend); harness tools such as spawn_thinking are
left out, so a handoff is neither right nor wrong by itself. FDB-v3's functions do the scoring:

  pass rate        evaluate_pass_rate.evaluate_scenario_pass: every expected tool called, no extra calls,
                   all arguments right
  tool selection   evaluate_tool_calls.evaluate_tool_selection: F1 over tool names (extra calls lower it)
  arguments        evaluate_tool_calls.evaluate_argument_accuracy

Arguments are matched exactly by default (FDB-v3's fallback). With --judge-url, FDB-v3's own judging prompt
runs on that OpenAI-compatible endpoint (e.g. Qwen3-14B, as in Matthew's return-path runs) instead of gpt-4o.

    python experiments/fdb-chain/score_chain.py --fdb-v3-dir <Full-Duplex-Bench/v3> experiments/fdb-chain/results/*.jsonl
"""
import argparse
import json
import statistics
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from fdb_tools import FDB_TOOL_NAMES, load_scenarios


class JudgeClient:
    """Enough of the OpenAI client for FDB-v3's judge: chat.completions.create, with the model name and
    endpoint replaced."""

    def __init__(self, url, model):
        self.url, self.model = url.rstrip("/") + "/chat/completions", model
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, temperature=0, max_tokens=200, **_):
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            content = json.loads(r.read())["choices"][0]["message"]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def fdb_modules(fdb_v3_dir, judge):
    sys.path.insert(0, str(fdb_v3_dir))
    import evaluate_pass_rate as epr
    import evaluate_tool_calls as etc
    if judge:
        for mod in (epr, etc):
            mod._get_openai_client = lambda: judge
    return epr, etc


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def scalars(value):
    """Every string and number inside a JSON value."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in scalars(v)]
    if isinstance(value, list):
        return [s for v in value for s in scalars(v)]
    return [value] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else []


def resolve_references(expected, log):
    """FDB-v3's judging rule accepts any value for a "$RESULT_..." argument that plausibly came from an earlier
    call, but the judge sees only the reference text and rejects real ids such as PROD1. Where the run's first call
    of that function used a value an earlier call in the same run returned, that value replaces the reference, so
    the judge compares like with like; a value no earlier call returned keeps the reference."""
    expected = json.loads(json.dumps(expected))
    for exp in expected:
        refs = [k for k, v in exp.get("args", {}).items() if isinstance(v, str) and v.startswith("$RESULT")]
        for i, call in enumerate(log):
            if refs and call["function"] == exp["function"]:
                earlier = {str(v) for c in log[:i] for v in scalars(c.get("result"))}
                for k in refs:
                    if k in call["args"] and str(call["args"][k]) in earlier:
                        exp["args"][k] = call["args"][k]
                break
    return expected


def score_file(path, scenarios, epr, etc, use_llm, resolve_refs=False):
    records = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for r in records:
        s = scenarios[r["scenario_id"]]
        if resolve_refs:
            log = [c for c in r.get("calls", []) if c["function"] in FDB_TOOL_NAMES]
            s = dict(s, expected_tool_calls=resolve_references(s["expected_tool_calls"], log))
        calls = [{"function": c["function"], "args": c["args"]} for c in r.get("calls", []) if c["function"] in FDB_TOOL_NAMES]
        passed = epr.evaluate_scenario_pass(s, calls, use_llm=use_llm)
        rows.append({
            "id": r["id"], "scenario": r["scenario_id"], "difficulty": s["difficulty"], "rollback": s["state_rollback_test"],
            "passed": passed["passed"], "reason": passed["failure_reason"],
            "f1": etc.evaluate_tool_selection(s["expected_tool_calls"], calls)["score"],
            "args": etc.evaluate_argument_accuracy(s["expected_tool_calls"], calls, use_llm=use_llm)["score"],
            "by": Counter(c["by"] for c in r.get("calls", [])),
            "handoffs": len(r.get("handoffs", [])), "harness_calls": len(r.get("harness_calls", [])),
            "error": r.get("error"),
            "first_token": [t["first_token_s"] for t in r.get("frontend", []) if t.get("first_token_s") is not None],
            "seconds": sum(t["latency_s"] for t in r.get("frontend", [])) + sum(h["seconds"] for h in r.get("handoffs", [])),
        })
    return rows


def report(name, rows, benchmark_errors=None):
    n = len(rows)
    by = sum((r["by"] for r in rows), Counter())
    first = [r["first_token"][0] for r in rows if r["first_token"]]
    lines = [f"## {name}", "", "| metric | value |", "| --- | --- |",
             f"| pass rate (FDB-v3, strict) | {pct(sum(r['passed'] for r in rows), n)} |"]
    if benchmark_errors:
        lines.append(f"| pass rate, benchmark errors counted as passed | "
                     f"{pct(sum(r['passed'] or r['scenario'] in benchmark_errors for r in rows), n)} |")
    lines += [f"| tool selection F1 (mean) | {statistics.mean(r['f1'] for r in rows):.3f} |",
              f"| argument accuracy (mean) | {statistics.mean(r['args'] for r in rows):.3f} |",
              f"| items with a handoff | {pct(sum(r['handoffs'] > 0 for r in rows), n)} |",
              f"| FDB calls made by frontend / backend | {by['frontend']} / {by['backend']} |",
              f"| unrelated harness tool calls | {sum(r['harness_calls'] for r in rows)} |",
              f"| errors | {sum(bool(r['error']) for r in rows)} |"]
    if first:
        lines.append(f"| frontend first token, median (s) | {statistics.median(first):.2f} |")
    if any(r["seconds"] for r in rows):
        lines.append(f"| end to end, median (s) | {statistics.median(r['seconds'] for r in rows):.2f} |")
    lines += ["", "| difficulty | pass rate |", "| --- | --- |"]
    groups = defaultdict(list)
    for r in rows:
        groups[r["difficulty"]].append(r["passed"])
    for d in ("easy", "medium", "hard"):
        lines.append(f"| {d} | {pct(sum(groups[d]), len(groups[d]))} |")
    rb = [r["passed"] for r in rows if r["rollback"]]
    lines.append(f"| self-correction items | {pct(sum(rb), len(rb))} |")
    lines += ["", "Failures:", ""] + [f"- {r['id']}: {r['reason'] or r['error']}" for r in rows if not r["passed"]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", type=Path)
    ap.add_argument("--fdb-v3-dir", type=Path, required=True)
    ap.add_argument("--judge-url", help="OpenAI-compatible endpoint for FDB-v3's argument judge")
    ap.add_argument("--judge-model", default="qwen3-14b")
    ap.add_argument("--count-as-pass", type=Path,
                    help="JSON {scenario_id: reason}: items whose gold answer is wrong; the report adds a pass rate "
                         "that counts them as passed")
    ap.add_argument("--resolve-refs", action="store_true",
                    help="put the run's own earlier results in place of $RESULT references (see resolve_references); "
                         "reports get a .refs suffix")
    args = ap.parse_args()

    judge = JudgeClient(args.judge_url, args.judge_model) if args.judge_url else None
    epr, etc = fdb_modules(args.fdb_v3_dir, judge)
    scenarios = load_scenarios(args.fdb_v3_dir / "benchmark_data_v2.json")
    benchmark_errors = json.loads(args.count_as_pass.read_text(encoding="utf-8")) if args.count_as_pass else None
    for path in args.results:
        rows = score_file(path, scenarios, epr, etc, use_llm=bool(judge), resolve_refs=args.resolve_refs)
        text = report(path.stem + (" (LLM judge)" if judge else " (exact match)")
                      + (", $RESULT references resolved" if args.resolve_refs else ""), rows, benchmark_errors)
        out = path.with_suffix((".judge" if judge else ".exact") + (".refs" if args.resolve_refs else "") + ".md")
        out.write_text(text, encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
