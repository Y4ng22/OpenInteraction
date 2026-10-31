"""Try a trained adapter from a browser: one session, one second of microphone audio per request.

The browser posts each second of 16 kHz audio to /unit and speaks the text it gets back with its own speech synthesis,
because the model's TTS weights are not on the server. Tool calls are held back from speech and answered with
simulated results: fast tools one second after the reply, the backend a few seconds later. The server binds to
localhost only; open it through an SSH tunnel.

    python demo/server.py --src <snapshot> --lora runs/lc_1 --tools ../data/tools_split.jsonl \
        --allow ../duplex_full/allowed_banned_ids_v4.json --port 8765
"""
import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_sequences import system_prompt
from duplex_test import CLOSE, OPEN, merge_lora

TTS_BOS = 151703  # appears inside replies and breaks call JSON; never useful in text
FRONTEND = ["hourlyWeatherSnapshot", "make_todo_list", "getChildVaccinationStatus", "Body Mass Index Calculator"]
RESULTS = {"hourlyWeatherSnapshot": {"temperature_f": 58, "condition": "partly cloudy", "wind_mph": 9},
           "make_todo_list": {"status": "created", "items": 3},
           "getChildVaccinationStatus": {"status": "up to date", "next_due": "2027-03-01"}}
BACKEND_DELAY = 6  # units


class Session:
    def __init__(self, duplex, tok, system):
        self.duplex, self.tok, self.system = duplex, tok, system
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.duplex.prepare(prefix_system_prompt=self.system)
        self.in_call, self.call, self.calls, self.queue, self.t = False, "", [], [], 0

    def out(self):
        """Text since the last call, split into what is spoken and the finished tool calls."""
        ids = [i for i in self.duplex.res_ids[self.before:] if i != TTS_BOS]
        raw, spoken, calls, ended = self.tok.decode(ids, skip_special_tokens=False), "", [], False
        ended = "<|turn_eos|>" in raw
        raw = re.sub(r"<\|(?!turn_eos)[^|]*\|>", "", raw).replace("<|turn_eos|>", "")
        while raw:
            if self.in_call:
                head, sep, raw = raw.partition(CLOSE)
                self.call += head
                if sep:
                    calls.append(self.call)
                    self.call, self.in_call = "", False
            else:
                head, sep, raw = raw.partition(OPEN)
                spoken += head
                self.in_call = bool(sep)
        return spoken, calls, ended

    def step(self, **feed):
        self.before = len(self.duplex.res_ids)
        if not self.duplex.streaming_prefill(**feed)["success"]:
            return {"text": "", "calls": [], "listen": True, "ended": False}
        got = self.duplex.streaming_generate(decode_mode="greedy")
        text, calls, ended = self.out()
        return {"text": text, "calls": calls, "listen": got["is_listen"], "ended": ended}

    def answer(self, call):
        try:
            c = json.loads(call)
        except json.JSONDecodeError:
            return None
        if c["name"] == "spawn_thinking":
            return self.t + BACKEND_DELAY, {"status": "completed", "result": "Simulated backend result. Nothing was really done for: "
                                            + str(c["arguments"].get("objective"))}
        return self.t + 2, RESULTS.get(c["name"], {"status": "ok", "note": "simulated result"})

    def unit(self, pcm):
        with self.lock:
            t0, parts, delivered = time.time(), [], []
            for item in [q for q in self.queue if q[0] <= self.t]:
                self.queue.remove(item)
                text = "<tool_response>" + json.dumps(item[1], ensure_ascii=False) + "</tool_response>"
                delivered.append(item[1])
                parts.append(self.step(text_list=[text]))
            parts.append(self.step(audio_waveform=pcm))
            calls = [c for p in parts for c in p["calls"]]
            self.calls += calls
            if any(p["ended"] for p in parts):
                self.queue += [a for a in map(self.answer, self.calls) if a]
                self.calls = []
            self.t += 1
            return {"text": "".join(p["text"] for p in parts), "calls": calls, "results": delivered, "listen": parts[-1]["listen"],
                    "ended": any(p["ended"] for p in parts), "ms": round(1000 * (time.time() - t0))}


def serve(session, port):
    page = (Path(__file__).parent / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body, kind="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(page, "text/html; charset=utf-8")

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/reset":
                session.reset()
                return self.reply(b"{}")
            pcm = np.frombuffer(body, np.float32).copy()
            self.reply(json.dumps(session.unit(pcm), ensure_ascii=False).encode())

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--lora", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--allow", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    model = AutoModel.from_pretrained(args.src, trust_remote_code=True, attn_implementation="sdpa", torch_dtype=torch.bfloat16,
                                      init_vision=False, init_audio=True, init_tts=True, device_map="auto",
                                      max_memory={g: "14GiB" for g in range(torch.cuda.device_count())}).eval()
    merge_lora(model.llm, args.lora)
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    model.init_tts = lambda *a, **k: None
    duplex = model.as_duplex(generate_audio=False)
    allow = set(json.loads(args.allow.read_text()))
    duplex.decoder.forbidden_token_ids[:] = [i for i in duplex.decoder.forbidden_token_ids if i not in allow] + [TTS_BOS]
    tools = {}
    for line in args.tools.read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        if t["name"] in FRONTEND:
            tools.setdefault(t["id"], t)
    system = system_prompt({"frontend_tools": list(tools)}, tools)
    print("tools:", [t["name"] for t in tools.values()], flush=True)
    print("ready", flush=True)
    serve(Session(duplex, tok, system), args.port)


if __name__ == "__main__":
    main()
