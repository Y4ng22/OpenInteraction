"""Step 1 of the delegation data: one pool of tool definitions from When2Call and ToolACE.

Schemas are normalized to JSON-schema style (object / string / integer / number / boolean / array, `required` inside
`parameters`). Tools are deduplicated by name (case and punctuation ignored, first source wins), and any tool whose
name touches an FDB-v3 tool or domain word (flight, exchange rate, apartment, autopay, ...) is removed, so no
FDB-v3 tool, or a near copy of one, reaches training.

    python collect_tools.py --when2call <dir> --toolace <data.json> --out tools.jsonl
"""
import argparse
import collections
import json
import re
from pathlib import Path

# FDB-v3's 12 tools and the domain words around them; a tool whose normalized name contains one is left out.
FDB_WORDS = ["flight", "exchangerate", "apartment", "autopay", "commute", "identitydoc", "cardbenefit", "trackorder",
             "addtocart", "searchproducts", "searchfilter"]
TYPES = {"str": "string", "string": "string", "int": "integer", "integer": "integer", "float": "number",
         "number": "number", "bool": "boolean", "boolean": "boolean", "list": "array", "array": "array",
         "dict": "object", "object": "object"}


def norm_name(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def norm_type(t):
    base = str(t or "string").split(",")[0].strip().lower()
    base = re.sub(r"\[.*", "", base)
    return TYPES.get(base, "string")


def normalize(tool):
    """{name, description, parameters: {type, properties, required}} or None if the definition is unusable."""
    name, desc = tool.get("name"), (tool.get("description") or "").strip()
    params = tool.get("parameters") or {}
    if not name or not desc or not isinstance(params, dict):
        return None
    props = {}
    for k, v in (params.get("properties") or {}).items():
        if not isinstance(v, dict):
            continue
        p = {"type": norm_type(v.get("type")), "description": (v.get("description") or "").strip()}
        if isinstance(v.get("enum"), list):
            p["enum"] = v["enum"]
        props[k] = p
    required = params.get("required") or tool.get("required") or []
    required = [r for r in required if r in props] if isinstance(required, list) else []
    return {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required}}


def when2call_tools(folder):
    for f in ("train/when2call_train_sft.jsonl", "train/when2call_train_pref.jsonl"):
        for line in (folder / f).read_text(encoding="utf-8").splitlines():
            for t in json.loads(line)["tools"]:
                yield json.loads(t) if isinstance(t, str) else t


def toolace_tools(path):
    for row in json.loads(path.read_text(encoding="utf-8")):
        m = re.search(r"\[\{.*\}\]", row["system"], re.S)
        try:
            tools = json.loads(m.group(0)) if m else []
        except json.JSONDecodeError:
            continue
        yield from (t for t in tools if isinstance(t, dict))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--when2call", type=Path, required=True)
    ap.add_argument("--toolace", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    seen, pool, counts = set(), [], collections.Counter()
    for source, tools in (("When2Call", when2call_tools(args.when2call)), ("ToolACE", toolace_tools(args.toolace))):
        for raw in tools:
            tool = normalize(raw)
            if tool is None:
                counts[source, "unusable"] += 1
                continue
            key = norm_name(tool["name"])
            if key in seen:
                continue
            seen.add(key)
            if any(w in key for w in FDB_WORDS):
                counts[source, "FDB-like, removed"] += 1
                continue
            pool.append({"id": f"t{len(pool):05d}", "source": source, **tool})
            counts[source, "kept"] += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in pool), encoding="utf-8")
    for (source, what), n in sorted(counts.items()):
        print(f"{source:10s} {what:20s} {n}")
    print(f"wrote {len(pool)} tools to {args.out}")


if __name__ == "__main__":
    main()
