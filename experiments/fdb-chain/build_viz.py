"""Build the report page: one module per experiment, switched by tabs, with the run_chain.py traces as one module.

Modules are Markdown files in --modules (default report/modules). Each starts with a front-matter block:

    ---
    title: Tool timing        # tab name
    order: 3                  # tab position
    date: 2026-10-26
    status: done              # done | running | planned
    kind: traces              # only for the traces module; its body becomes the intro
    ---

A module written in two languages is two files, <id>.en.md and <id>.zh.md (the second needs only `lang:`), and
gets a language switch. Supported Markdown: #/##/### headings, paragraphs, pipe tables, - and 1. lists, **bold**,
*italic*, `code`, [links](#other-module), and a line {{arch}} for the architecture sketch. To add an experiment,
add a file and rebuild.

The traces module: for each variant, pass rates from score_chain.py's reports, and for each item a timeline of the
user's turn, the frontend's replies and calls, each handoff with the backend's calls and tool results, and (when a
stage-2 file is given) what the frontend said once the result came back. No prompts are embedded.

    python experiments/fdb-chain/build_viz.py --results results --fdb-v3-dir <v3> --out viz.html \
        --variants fdb-native backend-direct fdb-handoff fdb-delegate fdb-delegate-strict ...
"""
import argparse
import html
import json
import re
from pathlib import Path

from fdb_tools import load_scenarios
from run_chain import TOOL_CALL, clean, parse_calls

LABELS = {
    "fdb-native": "Frontend only",
    "backend-direct": "Backend only (script text)",
    "backend-asr": "Backend only (frontend's transcript)",
    "fdb-handoff": "Both, frontend also has the tools",
    "fdb-delegate": "Hand-off only",
    "fdb-delegate-strict": "Hand-off only, strict prompt",
    "fdb-delegate-words": "Hand-off only, strict prompt + user's words",
    "fdb-delegate-manifest": "Hand-off only, strict prompt + tool list",
    "funaudio-fdb-native": "Fun-Audio-Chat: Frontend only",
    "funaudio-fdb-handoff": "Fun-Audio-Chat: Both, frontend also has the tools",
    "funaudio-fdb-delegate": "Fun-Audio-Chat: Hand-off only",
    "funaudio-fdb-delegate-tid": "Fun-Audio-Chat: Hand-off only",
}
NOTES = {
    "fdb-native": "MiniCPM-o alone with the 12 FDB tools",
    "backend-direct": "Qwen3-14B alone, reading the benchmark's written text",
    "backend-asr": "Qwen3-14B alone, reading what MiniCPM-o transcribed",
    "fdb-handoff": "Frontend has the 12 tools and spawn_thinking; backend has the 12 tools",
    "fdb-delegate": "Frontend has only spawn_thinking; backend has the 12 tools",
    "fdb-delegate-strict": "As above, plus: never say you can't, never promise without handing off",
    "fdb-delegate-words": "As above, and the backend also gets the user's words",
    "fdb-delegate-manifest": "As above, and the frontend sees the backend's tool list",
    "funaudio-fdb-native": "Fun-Audio-Chat-8B alone with the 12 FDB tools",
    "funaudio-fdb-handoff": "Fun-Audio-Chat has the 12 tools and spawn_thinking; backend has the 12 tools",
    "funaudio-fdb-delegate": "Fun-Audio-Chat has only spawn_thinking; backend has the 12 tools",
    "funaudio-fdb-delegate-tid": "Fun-Audio-Chat has only spawn_thinking, which replies with a task id and says the task "
                                 "is not done until a result arrives; backend has the 12 tools",
}


def report(path):
    """Pass count, per-item failure reasons, and the pass count with benchmark errors counted as passed (when the
    report has it), from a score_chain.py report."""
    if not path.exists():
        return None, {}, None
    text = path.read_text(encoding="utf-8")
    m = re.search(r"pass rate \(FDB-v3, strict\) \| (\d+)/(\d+)", text)
    adj = re.search(r"benchmark errors counted as passed \| (\d+)/", text)
    fails = dict(re.findall(r"^- (\S+): (.*)$", text, re.M))
    return (int(m.group(1)) if m else None), fails, (int(adj.group(1)) if adj else None)


def timeline(rec, asr):
    """Ordered steps: user, frontend speech / calls, handoffs with backend steps, results."""
    steps = []
    front_calls = [c for c in rec.get("calls", []) if c["by"] == "frontend"]
    back_calls = [c for c in rec.get("calls", []) if c["by"] == "backend"]
    handoffs = list(rec.get("handoffs", []))
    if rec.get("variant", "").startswith("backend-"):  # no frontend: the backend reads the dialogue directly
        steps.append({"who": "backend-in", "text": asr or "(benchmark text of the user's turn)"})
        handoffs = handoffs[:1]
        for turn in handoffs[0]["turns"] if handoffs else []:
            for call in parse_calls(turn["raw"]):
                c = back_calls.pop(0) if back_calls else None
                steps.append({"who": "backend", "call": c["function"] if c else (call[0] if call else "?"),
                              "args": c["args"] if c else (call[1] if call else {}), "result": c["result"] if c else None})
        if handoffs and handoffs[0]["summary"]:
            steps.append({"who": "backend-sum", "text": handoffs[0]["summary"]})
        return steps
    for turn in rec.get("frontend", []):
        said = clean(TOOL_CALL.sub("", turn["raw"] or ""))
        for call in parse_calls(turn["raw"]):
            name, args = (call or ("(unreadable call)", {}))
            if name == "spawn_thinking" and handoffs:
                h = handoffs.pop(0)
                steps.append({"who": "handoff", "text": h["objective"]})
                for bt in h["turns"]:
                    for bcall in parse_calls(bt["raw"]):
                        c = back_calls.pop(0) if back_calls and bcall else None
                        steps.append({"who": "backend", "call": c["function"] if c else (bcall[0] if bcall else "?"),
                                      "args": c["args"] if c else (bcall[1] if bcall else {}),
                                      "result": c["result"] if c else {"status": "not executed"}})
                steps.append({"who": "backend-sum", "text": h["summary"] or "(no summary: step limit reached)"})
                steps.append({"who": "accepted", "text": json.dumps(h.get("reply", {"status": "accepted"}))})
            elif front_calls and front_calls[0]["function"] == name:
                c = front_calls.pop(0)
                steps.append({"who": "front-call", "call": name, "args": c["args"], "result": c["result"]})
            else:
                steps.append({"who": "bad-call", "call": name, "args": args})
        if said:
            steps.append({"who": "front", "text": said})
    return steps


def inline(text):
    """Escaped text with `code`, [links](url), **bold** and *italic*."""
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", html.unescape(text))
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        s = html.escape(part, quote=False)
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{m.group(2)}"'
                   + ("" if m.group(2).startswith("#") else ' target="_blank" rel="noopener"') + f">{m.group(1)}</a>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", s)
        out.append(s)
    return "".join(out)


def md_to_html(text, arch):
    """The Markdown subset described at the top of this file. # becomes h2, since the page owns h1."""
    blocks, para, lines = [], [], text.splitlines()

    def flush():
        if para:
            blocks.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            flush()
        elif line == "{{arch}}":
            flush()
            blocks.append(f'<div class="arch">{arch}</div>')
        elif m := re.match(r"(#{1,3}) (.*)", line):
            flush()
            level = len(m.group(1)) + 1
            blocks.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
        elif line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            blocks.append(f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
            continue
        elif m := re.match(r"(-|\d+\.) (.*)", line):
            flush()
            tag = "ul" if m.group(1) == "-" else "ol"
            items = []
            while i < len(lines) and (m := re.match(r"\s*(-|\d+\.) (.*)", lines[i])):
                items.append(f"<li>{inline(m.group(2))}</li>")
                i += 1
            blocks.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        else:
            para.append(line)
        i += 1
    flush()
    return "\n".join(blocks)


def front_matter(text):
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    meta = dict(re.findall(r"^(\w+):\s*(.*?)\s*(?:#.*)?$", m.group(1), re.M))
    return meta, text[m.end():]


def arch_svg(suffix):
    """The architecture sketch with its marker ids made unique, so several copies can sit on one page."""
    return ARCH_SVG.replace('id="ah', f'id="{suffix}-ah').replace("url(#ah", f"url(#{suffix}-ah")


def load_modules(folder):
    """Modules in tab order: {id, title, date, status, kind, langs: [(label, html)]}."""
    by_id = {}
    for path in sorted(folder.glob("*.md")):
        mid, _, lang = path.stem.partition(".")
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        mod = by_id.setdefault(mid, {"id": mid, "langs": []})
        for key in ("title", "order", "date", "status", "kind"):
            if key in meta:
                mod[key] = meta[key]
        mod["langs"].append((meta.get("lang", lang or "English"), md_to_html(body, arch_svg(f"{mid}-{lang or 'x'}")),
                             0 if lang in ("", "en") else 1))
    for mod in by_id.values():
        mod["langs"] = [(label, body) for label, body, _ in sorted(mod["langs"], key=lambda x: x[2])]
    return sorted(by_id.values(), key=lambda m: int(m.get("order", 99)))


STATUS = {"done": "Done", "running": "Running", "planned": "Planned"}


def render_modules(modules, traces_html, progress=None):
    """Tab bar and module sections."""
    nav, sections = [], []
    for mod in modules:
        mid, status = mod["id"], mod.get("status", "done")
        date = mod.get("date", "")
        nav.append(f'<a href="#{mid}" data-m="{mid}"><span class="tname">{html.escape(mod.get("title", mid))}</span>'
                   f'<span class="tmeta"><span class="st {status}">{STATUS.get(status, status)}</span> {html.escape(date)}</span></a>')
        if mod.get("kind") == "progress":
            intro = mod["langs"][0][1] if mod["langs"] else ""
            snapshot = json.dumps(progress or {"steps": []}, ensure_ascii=False).replace("</", "<\\/")
            body = (f'<div class="prose intro">{intro}</div><div class="card" id="progress"></div>'
                    f'<script type="application/json" id="progress-data">{snapshot}</script>')
        elif mod.get("kind") == "traces":
            intro = mod["langs"][0][1] if mod["langs"] else ""
            body = f'<div class="prose intro">{intro}</div>{traces_html}'
        elif len(mod["langs"]) > 1:
            switch = "".join(f'<button type="button" data-lang="{i}">{html.escape(label)}</button>'
                             for i, (label, _) in enumerate(mod["langs"]))
            panes = "".join(f'<div class="prose" data-pane="{i}"{"" if i == 0 else " hidden"}>{body}</div>'
                            for i, (_, body) in enumerate(mod["langs"]))
            body = f'<div class="seg" role="group" aria-label="Language">{switch}</div>{panes}'
        else:
            body = f'<div class="prose">{mod["langs"][0][1]}</div>'
        sections.append(f'<section class="module" id="m-{mid}" data-module="{mid}" hidden>{body}</section>')
    return "".join(nav), "".join(sections)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--fdb-v3-dir", type=Path, required=True)
    ap.add_argument("--variants", nargs="+", required=True)
    ap.add_argument("--stage2", nargs="+", default=[],
                    help="VARIANT=PATH pairs: announce_stage2.py jsonl files, attached to that variant's items")
    ap.add_argument("--manual-pre", type=Path,
                    help="JSON {variant: [item id prefixes]}: hand-checked made-up outcomes before the result")
    ap.add_argument("--asr", type=Path)
    ap.add_argument("--extra", type=Path, help="markdown summary (stage 2 / duplex reports) shown as text")
    ap.add_argument("--judge-report", default=".judge.md",
                    help="suffix of the judge reports to read, e.g. .judge.refs.md for score_chain.py --resolve-refs")
    ap.add_argument("--keep-from", type=Path,
                    help="an earlier build of this page; listed variants with no result file here are copied from it")
    ap.add_argument("--benchmark-errors", type=Path, default=Path(__file__).parent / "report" / "benchmark_errors.json",
                    help="JSON {scenario_id: reason}: items whose gold answer is wrong, flagged in the traces")
    ap.add_argument("--progress", type=Path, default=Path(__file__).parent / "report" / "progress.json",
                    help="progress steps shown until the page's live store answers (the store's `progress` collection "
                         "holds the same steps, one document each)")
    ap.add_argument("--modules", type=Path, default=Path(__file__).parent / "report" / "modules",
                    help="folder of Markdown modules (see the top of this file)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    scenarios = load_scenarios(args.fdb_v3_dir / "benchmark_data_v2.json")
    asr = {}
    if args.asr and args.asr.exists():
        asr = {r["id"]: r["asr"] for r in map(json.loads, args.asr.read_text(encoding="utf-8").splitlines())}
    stage2 = {}  # variant -> item id -> stage-2 row
    for pair in args.stage2:
        variant, path = pair.split("=", 1)
        rows = map(json.loads, Path(path).read_text(encoding="utf-8").splitlines())
        stage2[variant] = {r["id"]: r for r in rows}

    manual = json.loads(args.manual_pre.read_text(encoding="utf-8")) if args.manual_pre else {}
    bench_errors = json.loads(args.benchmark_errors.read_text(encoding="utf-8")) if args.benchmark_errors.exists() else {}
    data = {"variants": [], "items": {}}
    for v in args.variants:
        path = args.results / f"{v}.jsonl"
        if not path.exists():
            continue
        recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        judge_pass, judge_fails, judge_adj = report(args.results / f"{v}{args.judge_report}")
        exact_pass, _, _ = report(args.results / f"{v}{args.judge_report.replace('.judge', '.exact', 1)}")
        no_handoff = v.startswith("backend-") or v.endswith("fdb-native")  # one model only
        handoffs = None if no_handoff else sum(1 for r in recs if r.get("handoffs"))
        data["variants"].append({"key": v, "label": LABELS.get(v, v), "note": NOTES.get(v, ""), "n": len(recs), "judge": judge_pass,
                                 "adjusted": judge_adj, "exact": exact_pass, "handoffs": handoffs})
        for r in recs:
            sid = r["scenario_id"]
            s = scenarios.get(sid, {})
            item = data["items"].setdefault(r["id"], {
                "scenario": sid, "difficulty": s.get("difficulty"),
                "user": s.get("dialogue", [{}])[-1].get("user", ""), "asr": asr.get(r["id"]),
                "context": [t for d in s.get("dialogue", [])[:-1] for t in (d.get("user"), d.get("ai"))],
                "gold": s.get("expected_tool_calls", []), "bench_error": bench_errors.get(sid), "runs": {}})
            run = {"pass": r["id"] not in judge_fails and "error" not in r, "why": judge_fails.get(r["id"], ""),
                   "steps": timeline(r, asr.get(r["id"]) if v == "backend-asr" else None)}
            if r["id"] in stage2.get(v, {}):
                s2 = stage2[v][r["id"]]
                run["steps"].append({"who": "result-in", "text": s2["result"]})
                calls = parse_calls(s2.get("after_raw") or "")
                for c in calls:
                    run["steps"].append({"who": "announce-call", "call": c[0] if c else "?", "args": c[1] if c else {}})
                run["steps"].append({"who": "announce", "text": s2["after"], "judge": s2["post"]})
                if v in manual:
                    made_up = any(r["id"].startswith(p) for p in manual[v])
                    run["pre"] = {"fabricated": made_up, "reason": "checked by hand", "manual": True}
                else:
                    run["pre"] = s2["pre"]
            item["runs"][v] = run
    if args.keep_from:  # variants whose result files aren't here: take them from an earlier build of this page
        old = json.loads(re.search(r"const D = (.*?);\nD\.variants", args.keep_from.read_text(encoding="utf-8"), re.S)
                         .group(1).replace("<\\/", "</"))
        have = {v["key"] for v in data["variants"]}
        for v in old["variants"]:
            if v["key"] in args.variants and v["key"] not in have:
                data["variants"].append(v)
                for item_id, item in old["items"].items():
                    if v["key"] in item["runs"]:
                        data["items"].setdefault(item_id, {**item, "runs": {}})["runs"][v["key"]] = item["runs"][v["key"]]
    data["extra"] = args.extra.read_text(encoding="utf-8") if args.extra and args.extra.exists() else ""
    modules = load_modules(args.modules)
    progress = json.loads(args.progress.read_text(encoding="utf-8")) if args.progress.exists() else None
    nav, sections = render_modules(modules, TRACES.replace("__ARCH__", arch_svg("traces")), progress)
    page = (TEMPLATE.replace("__NAV__", nav).replace("__MODULES__", sections)
            .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out} ({len(page) // 1024} KB, {len(modules)} modules, {len(data['items'])} items, "
          f"{len(data['variants'])} variants)")


TEMPLATE = r"""<title>Voice Handoff Notebook</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@600&family=Patrick+Hand&family=Kalam:wght@700&display=swap">
<style>
/* Layout: summary table on top, then a two-pane trace browser (item list | timeline) that stacks on phones. */
:root{
  --bg:#f6f7f9; --panel:#ffffff; --ink:#1b2430; --muted:#5b6676; --line:#dfe3ea;
  --user:#4b5563; --front:#0f766e; --back:#4338ca; --tool:#6b7280; --warn:#b45309; --ok:#15803d; --bad:#b91c1c;
  --front-bg:#e8f5f3; --back-bg:#eceefe; --user-bg:#f0f1f4; --tool-bg:#f7f7f8; --warn-bg:#fdf3e5;
  --edge:#8c8a84; --mine:#2a6fd6; --matt:#d9622b; --quiet:#5d6068;
  --display:"IBM Plex Sans Condensed","Arial Narrow",system-ui,sans-serif;
  --body:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#12161c; --panel:#1a2029; --ink:#e4e8ee; --muted:#9aa5b4; --line:#2c3440;
  --user:#c0c7d2; --front:#5eead4; --back:#a5b4fc; --tool:#a3aab5; --warn:#fbbf24; --ok:#4ade80; --bad:#f87171;
  --front-bg:#12302d; --back-bg:#1f2347; --user-bg:#232a34; --tool-bg:#1e242d; --warn-bg:#3a2a12;
  --edge:#8f8c86; --mine:#6ea2f2; --matt:#f08a56; --quiet:#a9a69e; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#12161c; --panel:#1a2029; --ink:#e4e8ee; --muted:#9aa5b4; --line:#2c3440;
  --user:#c0c7d2; --front:#5eead4; --back:#a5b4fc; --tool:#a3aab5; --warn:#fbbf24; --ok:#4ade80; --bad:#f87171;
  --front-bg:#12302d; --back-bg:#1f2347; --user-bg:#232a34; --tool-bg:#1e242d; --warn-bg:#3a2a12;
  --edge:#8f8c86; --mine:#6ea2f2; --matt:#f08a56; --quiet:#a9a69e; color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:14px;line-height:1.5}
.wrap{max-width:1240px;margin:0 auto;padding-block:20px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:20px}
h1{font-family:var(--display);font-size:26px;margin:0;text-wrap:balance;letter-spacing:.01em}
h2{font-family:var(--display);font-size:17px;margin:0 0 8px;letter-spacing:.02em}
.sub{color:var(--muted);margin:4px 0 0;max-width:72ch}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:top}
#sum td:first-child{white-space:normal;min-width:200px}
th{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
tr.sel td{background:var(--front-bg)}
tbody tr{cursor:pointer}
tbody tr:focus-visible{outline:2px solid var(--front);outline-offset:-2px}
.bar{display:inline-block;height:8px;border-radius:2px;background:var(--front);vertical-align:middle;margin-right:8px}
.legend{display:flex;flex-wrap:wrap;gap:12px;color:var(--muted);font-size:12px}
.legend span::before{content:"";display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px;background:var(--c)}
.browser{display:grid;grid-template-columns:300px 1fr;gap:16px;align-items:start}
@media (max-width:820px){.browser{grid-template-columns:1fr}}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
select,input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;min-width:0}
.list{max-height:70vh;overflow:auto;display:grid;gap:2px}
.list button{all:unset;display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center;padding:6px 8px;border-radius:6px;cursor:pointer;font-family:var(--mono);font-size:12px}
.list button:hover,.list button[aria-current="true"]{background:var(--user-bg)}
.list button:focus-visible{outline:2px solid var(--front)}
.dot{width:8px;height:8px;border-radius:50%}
.dot.p{background:var(--ok)} .dot.f{background:var(--bad)}
.tag{font-size:11px;color:var(--muted)}
.trace{display:grid;gap:10px;min-width:0}
.req{display:grid;gap:6px}
.req .lbl{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.gold{font-family:var(--mono);font-size:12px;background:var(--tool-bg);border:1px solid var(--line);border-radius:6px;padding:8px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
.steps{display:grid;gap:8px;counter-reset:s;margin:0;padding:0;list-style:none}
.step{display:grid;grid-template-columns:28px 1fr;gap:10px;align-items:start}
.step::before{counter-increment:s;content:counter(s);font-family:var(--mono);font-size:11px;color:var(--muted);text-align:right;padding-top:9px}
.bub{border-radius:8px;padding:8px 10px;min-width:0;border:1px solid var(--line)}
.who{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}
.user .bub{background:var(--user-bg)} .user .who{color:var(--user)}
.front .bub{background:var(--front-bg)} .front .who{color:var(--front)}
.back .bub{background:var(--back-bg);margin-left:22px} .back .who{color:var(--back)}
.tool .bub{background:var(--tool-bg);margin-left:22px} .tool .who{color:var(--tool)}
.bad .bub{background:var(--warn-bg)} .bad .who{color:var(--warn)}
code,.mono{font-family:var(--mono);font-size:12px;white-space:pre-wrap;word-break:break-word}
.verdict{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pill{font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;border:1px solid currentColor}
.pill.p{color:var(--ok)} .pill.f{color:var(--bad)} .pill.n{color:var(--muted)}
.arch{overflow-x:auto}
.arch svg{display:block;width:100%;min-width:640px;height:auto}
.arch svg text{font-family:'Patrick Hand','Bradley Hand','Comic Sans MS',cursive}
.arch-cap{color:var(--muted);margin:8px 0 0;max-width:80ch}
.extra{white-space:pre-wrap;font-family:var(--mono);font-size:12px;overflow-x:auto}
@media (prefers-reduced-motion:no-preference){.bub{transition:background .15s}}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--bg)}
.wrap.head{padding-block:28px 14px;gap:0}
.modnav{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
.wrap.navrow{display:flex;gap:2px;padding-block:0;overflow-x:auto;scrollbar-width:thin}
.navrow a{display:grid;gap:1px;padding:10px 14px 8px;text-decoration:none;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap;margin-bottom:-1px}
.navrow a:hover{color:var(--ink)}
.navrow a:focus-visible{outline:2px solid var(--front);outline-offset:-2px}
.navrow a[aria-current="page"]{color:var(--ink);border-bottom-color:var(--front)}
.tname{font-family:var(--display);font-size:15px;letter-spacing:.02em}
.tmeta{font-family:var(--mono);font-size:11px;color:var(--muted)}
.st{font-weight:500}.st.done{color:var(--ok)}.st.running{color:var(--warn)}.st.planned{color:var(--back)}
.module{display:grid;gap:20px;min-width:0}
.prose{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:20px clamp(14px,3vw,28px) 14px;min-width:0}
.prose>*{max-width:80ch}
.prose>.tablewrap,.prose>.arch{max-width:none}
.prose h2{font-family:var(--display);font-size:24px;line-height:1.2;margin:0 0 10px;text-wrap:balance}
.prose h3{font-family:var(--display);font-size:17px;margin:26px 0 8px;letter-spacing:.02em;text-wrap:balance}
.prose h4{font-size:14px;margin:18px 0 6px}
.prose p,.prose ul,.prose ol{margin:0 0 12px}
.prose li{margin-bottom:4px}
.prose .tablewrap{margin:4px 0 16px}
.prose th,.prose td{white-space:normal;min-width:64px}
.prose td:first-child{min-width:170px}
.prose a{color:var(--back);text-underline-offset:2px}
.prose .arch{margin:6px 0 16px}
.prose.intro{background:none;border:0;padding:0;color:var(--muted)}
.prose.intro p{margin:0}
.seg{display:inline-flex;justify-self:start;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{font:inherit;padding:6px 16px;border:0;background:var(--panel);color:var(--muted);cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button[aria-pressed="true"]{background:var(--front-bg);color:var(--ink);font-weight:600}
.seg button:focus-visible{outline:2px solid var(--front);outline-offset:-2px}
#progress{display:grid;gap:14px}
.phead{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}
.pnum{font-family:var(--display);font-size:34px;line-height:1;font-variant-numeric:tabular-nums}
.psegs{display:flex;gap:3px;height:14px}
.psegs span{flex:1;border-radius:2px;background:var(--line);overflow:hidden;position:relative}
.psegs span.done{background:var(--ok)}
.psegs span.blocked{background:var(--bad)}
.psegs span i{position:absolute;inset:0 auto 0 0;background:var(--warn)}
.psegs span.running{background:var(--warn-bg)}
.pphase{display:grid;gap:4px}
.pphase h3{font-family:var(--display);font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:6px 0 2px}
.pstep{display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:10px;align-items:start;padding:7px 0;border-top:1px solid var(--line)}
.pstep .mark{font-family:var(--mono);font-size:12px;color:var(--muted);text-align:right;padding-top:1px}
.pstep .ptitle{min-width:0}
.pstep .pnote{color:var(--muted);font-size:12.5px;overflow-wrap:anywhere}
.pstep .mini{height:5px;border-radius:3px;background:var(--warn-bg);margin-top:5px;max-width:260px;overflow:hidden}
.pstep .mini i{display:block;height:100%;background:var(--warn)}
.pill.r{color:var(--warn)} .pill.b{color:var(--bad)}
</style>
<header class="wrap head">
  <h1>Voice Handoff Notebook</h1>
  <p class="sub">The talk script, the Full-Duplex-Bench v3 runs, and every experiment since, one tab each.</p>
</header>
<nav class="modnav" aria-label="Modules"><div class="wrap navrow">__NAV__</div></nav>
<main class="wrap">
__MODULES__
</main>
<script>
const D = __DATA__;
D.variants.sort((a, b) => (b.judge ?? -1) - (a.judge ?? -1) || (b.exact ?? -1) - (a.exact ?? -1));
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let cur = {v: D.variants[0]?.key, id: null};
try { const saved = JSON.parse(localStorage.getItem("fdbviz") || "{}"); if (D.variants.some(x => x.key === saved.v)) cur.v = saved.v; } catch (e) {}
function save(){ try { localStorage.setItem("fdbviz", JSON.stringify({v: cur.v})); } catch (e) {} }
function pct(x, n){ return x == null ? "—" : `${x}/${n}`; }
function renderSum(){
  $("#sum").innerHTML = D.variants.map(v => `<tr tabindex="0" data-v="${v.key}" class="${v.key===cur.v?"sel":""}">
    <td><div>${esc(v.label)}</div><div class="tag">${esc(v.note)}</div></td><td><span class="bar" style="width:${(v.judge||0)*1.2}px"></span>${pct(v.judge, v.n)}</td>
    <td class="adjcol">${pct(v.adjusted, v.n)}</td><td>${pct(v.exact, v.n)}</td><td>${v.handoffs == null ? "—" : v.handoffs}</td></tr>`).join("");
  document.querySelectorAll(".adjcol").forEach(c => { c.hidden = !D.variants.some(v => v.adjusted != null); });
  document.querySelectorAll("#sum tr").forEach(tr => { const go = () => pick(tr.dataset.v); tr.onclick = go; tr.onkeydown = e => { if (e.key === "Enter") go(); }; });
  $("#variant").innerHTML = D.variants.map(v => `<option value="${v.key}" ${v.key===cur.v?"selected":""}>${esc(v.label)}</option>`).join("");
}
function pick(v){ cur.v = v; save(); renderSum(); renderList(); }
function ids(){
  const f = $("#filter").value, q = $("#q").value.trim().toLowerCase();
  return Object.keys(D.items).sort().filter(id => {
    const r = D.items[id].runs[cur.v]; if (!r) return false;
    if (q && !id.toLowerCase().includes(q)) return false;
    if (f === "p") return r.pass; if (f === "f") return !r.pass;
    if (f === "h") return r.steps.some(s => s.who === "handoff");
    if (f === "early") return r.pre?.fabricated === true;
    if (f === "progress") return r.pre?.fabricated === false;
    return true; });
}
function renderList(){
  const list = ids();
  if (!list.includes(cur.id)) cur.id = list[0] || null;
  $("#list").innerHTML = list.map(id => { const r = D.items[id].runs[cur.v];
    return `<button data-id="${id}" aria-current="${id===cur.id}"><span class="dot ${r.pass?"p":"f"}"></span><span>${esc(id.split("_").slice(0,2).join("_"))}<span class="tag"> ${esc(id.split("_")[2]?.slice(0,6)||"")}</span></span><span class="tag">${esc(D.items[id].difficulty||"")}</span></button>`; }).join("") || `<p class="tag">No items match.</p>`;
  document.querySelectorAll("#list button").forEach(b => b.onclick = () => { cur.id = b.dataset.id; renderList(); });
  renderTrace();
}
function call(s){ return `<code>${esc(s.call)}(${esc(JSON.stringify(s.args))})</code>`; }
function step(cls, who, body){ return `<li class="step ${cls}"><div class="bub"><div class="who">${who}</div>${body}</div></li>`; }
function renderTrace(){
  const el = $("#trace");
  if (!cur.id) { el.innerHTML = ""; return; }
  const it = D.items[cur.id], r = it.runs[cur.v];
  const ctx = it.context.filter(Boolean);
  let h = `<div class="verdict"><strong class="mono">${esc(cur.id)}</strong><span class="pill ${r.pass?"p":"f"}">${r.pass?"Pass":"Fail"}</span>${r.why?`<span class="tag">${esc(r.why)}</span>`:""}</div>`;
  if (it.bench_error) h += `<div class="verdict"><span class="pill n">Benchmark error</span><span class="tag">${esc(it.bench_error)}; counted as passed in the adjusted rate</span></div>`;
  h += `<div class="req"><span class="lbl">Expected calls</span><div class="gold">${it.gold.map(g => esc(g.function + "(" + JSON.stringify(g.args) + ")")).join("\n")}</div></div>`;
  let s = "";
  if (ctx.length) s += step("user", "Earlier turns (given as text)", `<div class="mono">${esc(ctx.join("\n"))}</div>`);
  s += step("user", "User (audio)", `${esc(it.user)}${it.asr?`<div class="tag" style="margin-top:4px">MiniCPM-o heard: ${esc(it.asr)}</div>`:""}`);
  for (const x of r.steps) {
    if (x.who === "front") s += step("front", "Frontend says", esc(x.text));
    else if (x.who === "front-call") s += step("front", "Frontend calls", call(x)) + step("tool", "Tool result", `<code>${esc(JSON.stringify(x.result))}</code>`);
    else if (x.who === "bad-call") s += step("bad", "Frontend calls a tool it doesn't have (not executed)", call(x));
    else if (x.who === "handoff") s += step("front", "Frontend hands off (spawn_thinking)", `<span class="tag">objective</span><div>${esc(x.text)}</div>`);
    else if (x.who === "backend-in") s += step("back", "Backend reads the user's turn", esc(x.text));
    else if (x.who === "backend") s += step("back", "Backend calls", call(x)) + step("tool", "Tool result", `<code>${esc(JSON.stringify(x.result))}</code>`);
    else if (x.who === "backend-sum") s += step("back", "Backend reports", esc(x.text));
    else if (x.who === "accepted") s += step("tool", "Frontend gets", `<code>${esc(x.text)}</code>`);
    else if (x.who === "result-in") s += step("tool", "Stage 2: result handed back to the frontend", esc(x.text));
    else if (x.who === "announce-call") s += step("front", "Stage 2: instead of answering, frontend calls", call(x));
    else if (x.who === "announce") s += step("front", "Stage 2: frontend announces", `${esc(x.text)}<div class="verdict" style="margin-top:6px">${pill("conveys", x.judge?.conveys)}${pill("faithful", x.judge?.faithful)}</div>`);
  }
  if (r.pre) s += step("tool", r.pre.manual ? "Stage 2, speech before the result (checked by hand)" : "Stage 2 judge, speech before the result", pill("made-up outcome", r.pre.fabricated, true) + ` <span class="tag">${esc(r.pre.reason||"")}</span>`);
  el.innerHTML = h + `<ol class="steps">${s}</ol>`;
}
function pill(name, val, invert){ if (val == null) return `<span class="pill n">${name}: ?</span>`; const good = invert ? !val : val; return `<span class="pill ${good?"p":"f"}">${name}: ${val?"yes":"no"}</span>`; }
$("#variant").onchange = e => pick(e.target.value);
$("#filter").onchange = renderList; $("#q").oninput = renderList;
if (D.extra) { $("#extraCard").hidden = false; $("#extra").textContent = D.extra; }
renderSum(); renderList();
const MODS = [...document.querySelectorAll(".module")].map(s => s.dataset.module);
function showModule(m, scroll){
  if (!MODS.includes(m)) m = MODS[0];
  document.querySelectorAll(".module").forEach(s => { s.hidden = s.dataset.module !== m; });
  document.querySelectorAll(".navrow a").forEach(a => a.setAttribute("aria-current", a.dataset.m === m ? "page" : "false"));
  try { localStorage.setItem("notebook-module", m); } catch (e) {}
  if (scroll) window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => showModule(location.hash.slice(1), true));
let startModule = location.hash.slice(1);
if (!MODS.includes(startModule)) { try { startModule = localStorage.getItem("notebook-module"); } catch (e) {} }
showModule(startModule, false);
const PSTATE = {done: ["p", "Done"], running: ["r", "Running"], blocked: ["b", "Blocked"], todo: ["n", "To do"]};
function renderProgress(steps, updated, live){
  const el = $("#progress"); if (!el) return;
  steps = [...steps].sort((a, b) => (a.order ?? 99) - (b.order ?? 99));
  const total = steps.length, done = steps.filter(s => s.status === "done").length;
  const part = s => s.status === "done" ? 1 : s.status === "running" ? Math.min(Math.max(s.pct || 0, 0), 100) / 100 : 0;
  const pctAll = total ? Math.round(100 * steps.reduce((a, s) => a + part(s), 0) / total) : 0;
  const now = steps.find(s => s.status === "running" || s.status === "blocked") || steps.find(s => s.status === "todo");
  const last = steps.map(s => s.updated).filter(Boolean).sort().pop() || updated;
  const when = last ? new Date(last).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "";
  const segs = steps.map(s => `<span class="${esc(s.status)}" title="${esc(s.title)}">${s.status === "running" ? `<i style="width:${part(s) * 100}%"></i>` : ""}</span>`).join("");
  const phases = [...new Set(steps.map(s => s.phase))];
  let h = `<div class="phead"><span class="pnum">${pctAll}%</span><span>${done} of ${total} steps done</span>${now ? `<span class="tag">Now: ${esc(now.title)}</span>` : ""}</div>
    <div class="psegs" role="progressbar" aria-label="Delegation training progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pctAll}">${segs}</div>
    <p class="tag" style="margin:0">${live ? "Live" : "Snapshot"}${when ? " · last update " + esc(when) : ""}</p>`;
  for (const ph of phases) {
    h += `<div class="pphase"><h3>${esc(ph)}</h3>` + steps.filter(s => s.phase === ph).map(s => {
      const [cls, label] = PSTATE[s.status] || PSTATE.todo;
      return `<div class="pstep"><span class="mark">${s.order ?? ""}</span><div class="ptitle"><div>${esc(s.title)}</div>${s.note ? `<div class="pnote">${esc(s.note)}</div>` : ""}${s.status === "running" && s.pct != null ? `<div class="mini"><i style="width:${part(s) * 100}%"></i></div>` : ""}</div><span class="pill ${cls}">${label}${s.status === "running" && s.pct != null ? " " + Math.round(s.pct) + "%" : ""}</span></div>`;
    }).join("") + `</div>`;
  }
  el.innerHTML = h;
}
const PROG = (() => { try { return JSON.parse($("#progress-data")?.textContent || "null"); } catch (e) { return null; } })();
if (PROG) renderProgress(PROG.steps || [], PROG.updated, false);
(async () => {
  const db = await window.claude?.use?.("db");
  if (!db) return;
  db.collection("progress").onSnapshot(snap => {
    if (!snap.empty) renderProgress(snap.docs.map(d => ({id: d.id, ...d.data()})), null, true);
  }, () => {});
})();
document.querySelectorAll(".seg").forEach(seg => {
  const mod = seg.closest(".module"), key = "notebook-lang-" + mod.dataset.module;
  const set = i => {
    seg.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.lang === i)));
    mod.querySelectorAll("[data-pane]").forEach(p => { p.hidden = p.dataset.pane !== i; });
    try { localStorage.setItem(key, i); } catch (e) {}
  };
  seg.querySelectorAll("button").forEach(b => { b.onclick = () => set(b.dataset.lang); });
  let i = "0"; try { i = localStorage.getItem(key) || "0"; } catch (e) {}
  set(seg.querySelector(`[data-lang="${i}"]`) ? i : "0");
});
</script>
"""

TRACES = r"""  <section class="card">
    <h2>Architecture</h2>
    <div class="arch">__ARCH__</div>
    <p class="arch-cap">The voice model calls spawn_thinking; the harness replies with a task id and passes the task on; the result comes back along the bottom. The voice model always decides the handoff; the full-duplex test is still to do, since Fun-Audio-Chat's duplex weights aren't public and MiniCPM-o makes no tool calls in full duplex.</p>
  </section>
  <section class="card">
    <h2>Setups</h2>
    <div class="tablewrap"><table><thead><tr><th>Setup (by pass rate)</th><th>Pass (judge)</th><th class="adjcol">Pass, benchmark errors counted</th><th>Pass (exact)</th><th>Handoffs</th></tr></thead><tbody id="sum"></tbody></table></div>
  </section>
  <section class="card">
    <h2>Trace</h2>
    <div class="legend"><span style="--c:var(--user)">User</span><span style="--c:var(--front)">Frontend (MiniCPM-o)</span><span style="--c:var(--back)">Backend (Qwen3-14B)</span><span style="--c:var(--tool)">Tool result</span><span style="--c:var(--warn)">Call not executed</span></div>
    <div class="browser" style="margin-top:12px">
      <div>
        <div class="controls">
          <select id="variant" aria-label="Setup"></select>
          <select id="filter" aria-label="Filter"><option value="all">All items</option><option value="f">Failed</option><option value="p">Passed</option><option value="h">With a handoff</option><option value="early">Made up a result before it came back</option><option value="progress">Only gave progress before the result</option></select>
          <input id="q" type="search" placeholder="Filter by id" aria-label="Filter by id">
        </div>
        <div class="list" id="list"></div>
      </div>
      <div class="trace" id="trace"></div>
    </div>
  </section>
  <section class="card" id="extraCard" hidden><h2>Notes: stage 2, duplex probe, Fun-Audio-Chat</h2><div class="extra" id="extra"></div></section>
"""

ARCH_SVG = r"""<svg viewBox="0 125 880 345" role="img" aria-label="The voice model talks and decides to hand off; the backend does the work">
    <defs>
      <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 1L10 5L0 9" fill="none" stroke="var(--edge)" stroke-width="1.6" stroke-linecap="round"/></marker>
      <marker id="ah-mine" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 1L10 5L0 9" fill="none" stroke="var(--mine)" stroke-width="1.6" stroke-linecap="round"/></marker>
      <marker id="ah-matt" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 1L10 5L0 9" fill="none" stroke="var(--matt)" stroke-width="1.6" stroke-linecap="round"/></marker>
    </defs>

    <!-- User -->
    <g fill="none" stroke="var(--edge)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M26 151 L123 149 L125 264 L23 267 Z"/>
      <path d="M23 149 Q74 153 125 151 Q122 208 123 267 Q74 263 25 265 Q27 208 23 149" stroke-opacity="0.55"/>
    </g>
    <text x="74" y="186" text-anchor="middle" font-size="22" fill="var(--ink)" font-family="Kalam, Patrick Hand, cursive" font-weight="700">User</text>
    <text x="74" y="214" text-anchor="middle" font-size="18" fill="var(--quiet)">speaks,</text>
    <text x="74" y="236" text-anchor="middle" font-size="18" fill="var(--quiet)">interrupts</text>

    <!-- S1 -->
    <g fill="none" stroke="var(--matt)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <path d="M174 151 L371 149 L373 264 L171 267 Z"/>
      <path d="M171 149 Q272 153 373 151 Q370 208 371 267 Q272 263 173 265 Q175 208 171 149" stroke-opacity="0.55"/>
    </g>
    <text x="272" y="180" text-anchor="middle" font-size="22" fill="var(--ink)" font-family="Kalam, Patrick Hand, cursive" font-weight="700">S1 · Voice frontend</text>
    <text x="272" y="206" text-anchor="middle" font-size="18" fill="var(--ink)">MiniCPM-o 4.5 / Fun-Audio-Chat-8B</text>
    <text x="272" y="230" text-anchor="middle" font-size="18" fill="var(--ink)">listens and talks</text>
    <text x="272" y="254" text-anchor="middle" font-size="18" fill="var(--matt)">decides to hand off</text>

    <!-- Harness -->
    <g fill="none" stroke="var(--mine)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M422 151 L609 149 L611 264 L419 267 Z"/>
      <path d="M419 149 Q515 153 611 151 Q608 208 609 267 Q515 263 421 265 Q423 208 419 149" stroke-opacity="0.55"/>
    </g>
    <text x="515" y="180" text-anchor="middle" font-size="22" fill="var(--ink)" font-family="Kalam, Patrick Hand, cursive" font-weight="700">Harness</text>
    <text x="515" y="206" text-anchor="middle" font-size="18" fill="var(--ink)">receives spawn_thinking</text>
    <text x="515" y="230" text-anchor="middle" font-size="18" fill="var(--ink)">replies with a task id</text>
    <text x="515" y="254" text-anchor="middle" font-size="18" fill="var(--ink)">passes the task on</text>

    <!-- S2 -->
    <g fill="none" stroke="var(--edge)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M660 151 L847 149 L849 264 L657 267 Z"/>
      <path d="M657 149 Q753 153 849 151 Q846 208 847 267 Q753 263 659 265 Q661 208 657 149" stroke-opacity="0.55"/>
    </g>
    <text x="753" y="180" text-anchor="middle" font-size="22" fill="var(--ink)" font-family="Kalam, Patrick Hand, cursive" font-weight="700">S2 · Backend</text>
    <text x="753" y="206" text-anchor="middle" font-size="18" fill="var(--ink)">Qwen3-14B</text>
    <text x="753" y="230" text-anchor="middle" font-size="18" fill="var(--ink)">12 FDB tools</text>
    <text x="753" y="254" text-anchor="middle" font-size="18" fill="var(--quiet)">does the work</text>

    <!-- handoff arrows -->
    <g fill="none" stroke-width="1.7" stroke-linecap="round">
      <path d="M127 208 Q148 204 168 208" stroke="var(--edge)" marker-start="url(#ah)" marker-end="url(#ah)"/>
      <path d="M375 208 Q396 203 416 208" stroke="var(--mine)" marker-end="url(#ah-mine)"/>
      <path d="M613 208 Q634 203 654 208" stroke="var(--mine)" marker-end="url(#ah-mine)"/>
    </g>

    <!-- return path -->
    <path d="M753 270 Q755 300 751 326 L274 328 Q270 300 272 272" fill="none" stroke="var(--matt)" stroke-width="1.7" stroke-linecap="round" marker-end="url(#ah-matt)"/>
    <text x="512" y="352" text-anchor="middle" font-size="18" fill="var(--quiet)">result goes back into the voice: a system note or verbatim, written into the KV cache</text>

    <!-- key -->
    <path d="M26 402 q9 -5 18 0 t18 0" fill="none" stroke="var(--mine)" stroke-width="2.4" stroke-linecap="round"/>
    <text x="72" y="408" font-size="19" fill="var(--ink)">me: harness, handoff, FDB-v3 chain</text>
    <path d="M450 402 q9 -5 18 0 t18 0" fill="none" stroke="var(--matt)" stroke-width="2.4" stroke-linecap="round"/>
    <text x="496" y="408" font-size="19" fill="var(--ink)">Matthew: live duplex service, return path</text>

    <text x="26" y="448" font-size="21" fill="var(--ink)" font-family="Kalam, Patrick Hand, cursive" font-weight="700">Next: latent communication sends internal states along both arrows instead of text</text>
  </svg>"""

if __name__ == "__main__":
    main()
