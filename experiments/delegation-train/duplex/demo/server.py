"""Try a trained adapter from a browser: one session, one second of microphone audio per request.

The browser posts each second of 16 kHz audio to /unit and plays the model's own voice from the reply. Tool calls are
held back from speech and answered with simulated results: fast tools one second after the reply, the backend a few
seconds later. The server binds to localhost only; open it through an SSH tunnel. The waveform decoder needs onnx,
onnxruntime and a few others that the training env lacks, so they sit in their own folder on PYTHONPATH.

    PYTHONPATH=/local/data/xy2764/envs/demo-site python demo/server.py --src <snapshot> --lora runs/lc_1 \
        --tools ../data/tools_split.jsonl --allow ../duplex_full/allowed_banned_ids_v4.json --port 8765
"""
import argparse
import base64
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
    def __init__(self, duplex, tok, system, voice):
        self.duplex, self.tok, self.system, self.voice = duplex, tok, system, voice
        self.lock, self.cache, self.fresh = threading.Lock(), None, False
        self.reset()

    def reset(self):
        with self.lock:  # a second Start while the first is still preparing would corrupt the decoder
            if self.fresh:  # streaming the system prompt in takes 30 s, so it is done ahead of Start
                return
            d, cache = self.duplex, self.cache
            d.prepare(prefix_system_prompt=self.system, prompt_wav_path=None if cache else self.voice)
            if cache:
                d.flow_cache_base, d.hift_cache_base, d.token2wav_initialized = *cache, True
                d._reset_token2wav_for_new_turn()
            self.cache = d.flow_cache_base, d.hift_cache_base
            self.in_call, self.call, self.calls, self.queue, self.t, self.fresh = False, "", [], [], 0, True

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
            return {"text": "", "calls": [], "listen": True, "ended": False, "audio": None}
        self.duplex.generate_audio = not self.in_call  # the voice decoder has no business reading JSON
        got = self.duplex.streaming_generate(prompt_wav_path=self.voice, decode_mode="greedy")
        was_call, (text, calls, ended) = self.in_call, self.out()
        audio = None if got["is_listen"] or was_call or calls or self.in_call else got["audio_waveform"]
        return {"text": text, "calls": calls, "listen": got["is_listen"], "ended": ended, "audio": audio}

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
            t0, parts, delivered, self.fresh = time.time(), [], [], False
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
            voice = [p["audio"] for p in parts if p["audio"] is not None]
            audio = base64.b64encode(np.concatenate(voice).astype(np.float32).tobytes()).decode() if voice else ""
            return {"text": "".join(p["text"] for p in parts), "audio": audio, "calls": calls, "results": delivered, "listen": parts[-1]["listen"],
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
                                      max_memory={0: "2GiB", 1: "14GiB", 2: "14GiB"}).eval()  # the voice decoder takes card 0
    merge_lora(model.llm, args.lora)
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    model.init_tts(enable_float16=True, n_timesteps=5)  # 10 steps runs slower than real time on these cards
    duplex = model.as_duplex(generate_audio=True)
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
    serve(Session(duplex, tok, system, str(args.src / "assets/system_ref_audio.wav")), args.port)


if __name__ == "__main__":
    main()
