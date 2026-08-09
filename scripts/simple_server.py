#!/usr/bin/env python3
"""Simple chat server using Qwen3-Omni AWQ with Transformers + FastAPI.
Bypasses vLLM compatibility issues entirely.
"""
import sys, os, json, time, torch
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

MODEL_PATH = '/root/autodl-tmp/models/qwen3-omni-awq'
PORT = 6006

print("Loading model...")
from transformers import AutoModelForCausalLM, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH, torch_dtype=torch.float16, device_map="auto",
    trust_remote_code=True
)
print(f"Model loaded! VRAM: {torch.cuda.memory_allocated()/1e9:.1f}GB")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            self._json({"status": "ok"})
        elif path == '/v1/models':
            self._json({"object": "list", "data": [{"id": "interactformer", "object": "model"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/v1/chat/completions':
            self._handle_chat()

    def _handle_chat(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length))
        messages = body.get('messages', [])

        # Build conversation
        text = ""
        for msg in messages:
            role = msg['role']
            content = msg['content']
            if isinstance(content, list):
                content = ' '.join(p.get('text', '') for p in content if p.get('type') == 'text')
            tag = {'user': 'user', 'assistant': 'assistant', 'system': 'system'}.get(role, 'user')
            text += f"<|im_start|>{tag}\n{content}<|im_end|>\n"
        text += "<|im_start|>assistant\n"

        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs, max_new_tokens=1024, temperature=0.7,
                do_sample=True, top_p=0.9,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id
            )

        response = tokenizer.decode(
            outputs[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True
        )

        self._json({
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "model": "interactformer",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": response}, "finish_reason": "stop"}]
        })

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args): pass

print(f"Starting server on port {PORT}...")
HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
