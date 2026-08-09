#!/usr/bin/env python3
"""Simple chat server using Qwen3-Omni AWQ with Transformers + FastAPI.
Bypasses vLLM compatibility issues entirely.
"""
import sys, os, json, time, torch, hmac, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

MODEL_PATH = '/root/autodl-tmp/models/qwen3-omni-awq'
HOST = os.environ.get('INTERACTFORMER_HOST', '127.0.0.1')
PORT = int(os.environ.get('INTERACTFORMER_PORT', '6006'))
API_KEY = os.environ.get('INTERACTFORMER_API_KEY', '')
MAX_REQUEST_BYTES = int(os.environ.get('INTERACTFORMER_MAX_REQUEST_BYTES', str(1024 * 1024)))
TRUST_REMOTE_CODE = os.environ.get('INTERACTFORMER_TRUST_REMOTE_CODE', '').lower() in ('1', 'true', 'yes')

if HOST not in ('127.0.0.1', 'localhost', '::1') and not API_KEY:
    raise RuntimeError('INTERACTFORMER_API_KEY is required for non-loopback binding')

print("Loading model...")
from transformers import AutoModelForCausalLM, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=TRUST_REMOTE_CODE)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH, torch_dtype=torch.float16, device_map="auto",
    trust_remote_code=TRUST_REMOTE_CODE
)
model.eval()
MODEL_LOCK = threading.Lock()
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
        if not self._authorized():
            self._json({"error": "unauthorized"}, 401)
            return
        path = urlparse(self.path).path
        if path == '/v1/chat/completions':
            self._handle_chat()
        else:
            self._json({"error": "not found"}, 404)

    def _handle_chat(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            self._json({"error": "invalid Content-Length"}, 400)
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json({"error": "request body too large or empty"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "invalid JSON"}, 400)
            return

        raw_messages = body.get('messages', [])
        if not isinstance(raw_messages, list) or len(raw_messages) > 64:
            self._json({"error": "messages must be a list with at most 64 items"}, 400)
            return
        messages = []
        for msg in raw_messages:
            if not isinstance(msg, dict) or msg.get('role') not in ('user', 'assistant', 'system'):
                self._json({"error": "invalid message"}, 400)
                return
            content = msg.get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    str(part.get('text', '')) for part in content
                    if isinstance(part, dict) and part.get('type') == 'text'
                )
            if not isinstance(content, str) or len(content) > 32768:
                self._json({"error": "message content is too large"}, 400)
                return
            messages.append({'role': msg['role'], 'content': content})

        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=32768).to(model.device)

        try:
            max_new_tokens = max(1, min(int(body.get('max_tokens', 1024)), 2048))
            temperature = max(0.01, min(float(body.get('temperature', 0.7)), 2.0))
            top_p = max(0.01, min(float(body.get('top_p', 0.9)), 1.0))
        except (TypeError, ValueError):
            self._json({"error": "invalid generation parameters"}, 400)
            return

        with MODEL_LOCK, torch.inference_mode():
            outputs = model.generate(
                **inputs, max_new_tokens=max_new_tokens,
                temperature=temperature,
                do_sample=True, top_p=top_p,
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

    def _authorized(self):
        if not API_KEY:
            return True
        supplied = self.headers.get('Authorization', '')
        expected = f'Bearer {API_KEY}'
        return hmac.compare_digest(supplied, expected)

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args): pass

print(f"Starting server on {HOST}:{PORT}...")
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
