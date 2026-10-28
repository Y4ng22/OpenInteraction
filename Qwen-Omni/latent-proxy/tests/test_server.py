import json
import os
import tempfile
import time
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from latent_proxy.extractors import StubExtractor
from latent_proxy.server import ProxyConfig, create_server
from latent_proxy.store import FileLatentStore


def sse(*chunks):
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return "".join(lines) + "data: [DONE]\n\n"


class FakeUpstream:
    """OpenAI-compatible model server that records what it receives."""

    def __init__(self):
        self.requests = []
        self.reply = None  # (status, content_type, body)
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self):
                status, content_type, body = upstream.reply
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                upstream.requests.append({"path": self.path, "headers": dict(self.headers)})
                self._reply()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                upstream.requests.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(self.rfile.read(length)),
                })
                self._reply()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/compatible-mode/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.upstream = FakeUpstream()
        self.config = config = ProxyConfig(
            upstream=self.upstream.base,
            store=FileLatentStore(self.directory.name),
            extractor=StubExtractor(tokens=2, dim=4),
        )
        self.proxy = create_server(config, "127.0.0.1", 0)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.proxy.server_address[1]}/v1"

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.upstream.close()
        self.directory.cleanup()

    def post(self, body):
        request = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def manifests(self, task_id):
        directory = os.path.join(self.directory.name, task_id)
        if not os.path.isdir(directory):
            return []
        return sorted(name for name in os.listdir(directory) if name.endswith(".json"))

    def marked_messages(self, task_id="task_7"):
        return [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": f'<latent_task id="{task_id}"/>\n今天天气怎么样'},
        ]

    def test_streamed_final_answer_is_relayed_unchanged_and_writes_a_latent(self):
        stream = sse(
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "晴，"}}]},
            {"choices": [{"index": 0, "delta": {"content": "25 度"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 10}},
        )
        self.upstream.reply = (200, "text/event-stream", stream)
        status, body = self.post({"model": "qwen-plus", "stream": True, "messages": self.marked_messages()})

        self.assertEqual(status, 200)
        self.assertEqual(body, stream)
        sent = self.upstream.requests[0]
        self.assertEqual(sent["path"], "/compatible-mode/v1/chat/completions")
        self.assertEqual(sent["headers"].get("Authorization"), "Bearer test-key")
        self.assertNotIn("latent_task", json.dumps(sent["body"], ensure_ascii=False))
        self.assertEqual(sent["body"]["messages"][1]["content"], "今天天气怎么样")
        self.assertEqual(self.manifests("task_7"), ["1.json"])
        with open(os.path.join(self.directory.name, "task_7", "1.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["final"], True)
        self.assertEqual((manifest["tokens"], manifest["dim"]), (2, 4))

    def test_tool_call_rounds_do_not_write_a_latent(self):
        self.upstream.reply = (200, "text/event-stream", sse(
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        ))
        self.post({"model": "qwen-plus", "stream": True, "messages": self.marked_messages()})
        self.assertEqual(self.manifests("task_7"), [])

    def test_non_streamed_final_answer_writes_a_latent(self):
        reply = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "晴"}, "finish_reason": "stop"}]}
        self.upstream.reply = (200, "application/json", json.dumps(reply))
        status, body = self.post({"model": "qwen-plus", "messages": self.marked_messages("task_8")})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), reply)
        self.assertEqual(self.manifests("task_8"), ["1.json"])

    def test_requests_without_a_marker_are_forwarded_and_write_nothing(self):
        reply = {"choices": [{"index": 0, "message": {"content": "hi"}, "finish_reason": "stop"}]}
        self.upstream.reply = (200, "application/json", json.dumps(reply))
        self.post({"model": "qwen-plus", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(os.listdir(self.directory.name), [])

    def test_upstream_errors_are_relayed_with_their_status(self):
        self.upstream.reply = (401, "application/json", '{"error":"bad key"}')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post({"model": "qwen-plus", "messages": self.marked_messages()})
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(json.loads(caught.exception.read()), {"error": "bad key"})
        self.assertEqual(self.manifests("task_7"), [])

    def test_final_delay_holds_only_final_task_answers(self):
        config = self.config
        config.final_delay_s = 0.4
        reply = {"choices": [{"index": 0, "message": {"content": "晴"}, "finish_reason": "stop"}]}
        self.upstream.reply = (200, "application/json", json.dumps(reply))
        started = time.monotonic()
        self.post({"model": "qwen-plus", "messages": self.marked_messages("task_9")})
        self.assertGreaterEqual(time.monotonic() - started, 0.4)

        started = time.monotonic()
        self.post({"model": "qwen-plus", "messages": [{"role": "user", "content": "hi"}]})
        self.upstream.reply = (200, "text/event-stream", sse(
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]},
        ))
        self.post({"model": "qwen-plus", "stream": True, "messages": self.marked_messages("task_9")})
        self.assertLess(time.monotonic() - started, 0.4)

    def test_models_listing_is_forwarded(self):
        self.upstream.reply = (200, "application/json", '{"data":[{"id":"qwen-plus"}]}')
        with urllib.request.urlopen(f"{self.base}/models", timeout=10) as response:
            self.assertEqual(json.loads(response.read()), {"data": [{"id": "qwen-plus"}]})
        self.assertEqual(self.upstream.requests[0]["path"], "/compatible-mode/v1/models")


if __name__ == "__main__":
    unittest.main()
