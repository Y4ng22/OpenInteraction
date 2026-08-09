#!/usr/bin/env python
"""
InteractFormer Web Demo
========================
A browser-based demo showing:
- Real-time text chat with Qwen3-Omni
- Architecture visualization (S1/S2/Bridge)
- Micro-turn processing visualization
- Background Model delegation simulation

Usage:
    python scripts/web_demo.py --port 6006
    Then visit: http://localhost:6006
"""

import argparse
import json
import sys
import time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ================================================================
# HTML Template
# ================================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>InteractFormer — Real-Time Interaction Demo</title>
<style>
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --border: #30363d;
  --text: #e6edf3;
  --text-dim: #8b949e;
  --accent: #58a6ff;
  --green: #3fb950;
  --orange: #d2991d;
  --purple: #a371f7;
  --red: #f85149;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  display: flex;
  flex-direction: column;
}
header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 12px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
}
header h1 { font-size: 18px; font-weight: 600; }
header .badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  background: var(--accent);
  color: #000;
  font-weight: 600;
}
main {
  flex: 1;
  display: flex;
  overflow: hidden;
}
/* Chat panel */
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.message {
  padding: 10px 14px;
  border-radius: 8px;
  max-width: 85%;
  line-height: 1.5;
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.message.user {
  align-self: flex-end;
  background: var(--accent);
  color: #000;
}
.message.assistant {
  align-self: flex-start;
  background: var(--surface);
  border: 1px solid var(--border);
}
.message.system {
  align-self: center;
  background: transparent;
  color: var(--text-dim);
  font-size: 12px;
  border: none;
  font-style: italic;
}
.chat-input-area {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  background: var(--surface);
  display: flex;
  gap: 8px;
}
.chat-input-area textarea {
  flex: 1;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  resize: none;
  font-family: inherit;
  font-size: 14px;
  min-height: 44px;
  max-height: 120px;
}
.chat-input-area button {
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  background: var(--accent);
  color: #000;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}
.chat-input-area button:hover { opacity: 0.85; }
.chat-input-area button:disabled { opacity: 0.4; cursor: not-allowed; }
/* Side panel */
.side-panel {
  width: 320px;
  background: var(--surface);
  border-left: 1px solid var(--border);
  padding: 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.side-panel h3 {
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-dim);
  margin-bottom: 8px;
}
.arch-diagram {
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-size: 11px;
  line-height: 1.6;
  background: var(--bg);
  border-radius: 6px;
  padding: 12px;
  white-space: pre;
  color: var(--text-dim);
}
.arch-diagram .s1 { color: var(--accent); }
.arch-diagram .s2 { color: var(--purple); }
.arch-diagram .bridge { color: var(--green); }
.stat-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px solid var(--border);
}
.stat-label { color: var(--text-dim); }
.stat-value { color: var(--accent); font-weight: 600; }
.delegate-btn {
  width: 100%;
  padding: 8px;
  border: 1px solid var(--purple);
  border-radius: 6px;
  background: transparent;
  color: var(--purple);
  cursor: pointer;
  font-size: 12px;
}
.delegate-btn:hover { background: var(--purple); color: #fff; }
footer {
  padding: 8px 20px;
  border-top: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-dim);
  display: flex;
  justify-content: space-between;
}
.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--text-dim);
  border-top: 2px solid var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <h1>🔮 InteractFormer</h1>
  <span class="badge">S1 Active</span>
  <span style="font-size:12px;color:var(--text-dim);">Real-Time Multimodal Interaction Demo</span>
</header>

<main>
  <div class="chat-panel">
    <div class="chat-messages" id="messages">
      <div class="message system">
        🚀 InteractFormer 已就绪 · Qwen3-Omni 30B W4A16 · API: __API_URL__
      </div>
      <div class="message assistant">
        <strong>InteractFormer</strong><br>
        你好！我是 InteractFormer 交互模型 Demo。<br><br>
        我运行在 <strong>Qwen3-Omni 30B W4A16</strong> 量化模型上，展示了实时交互模型的核心概念：<br>
        • <span style="color:#58a6ff">Interaction Model (S1)</span> — 处理你的实时输入<br>
        • <span style="color:#a371f7">Background Model (S2)</span> — 异步深度推理<br>
        • <span style="color:#3fb950">Streaming Context Bridge</span> — S1 ↔ S2 通信<br><br>
        右侧面板展示架构详情。试试问我一些问题！
      </div>
    </div>
    <div class="chat-input-area">
      <textarea id="input" rows="1" placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
        onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage()}"></textarea>
      <button onclick="sendMessage()" id="sendBtn">发送</button>
    </div>
  </div>
  <div class="side-panel">
    <div>
      <h3>⚙️ 架构面板</h3>
      <div class="arch-diagram">
<span class="s1">┌─ Interaction Model (S1) ─┐</span>
<span class="s1">│  Input → Encoder         │</span>
<span class="s1">│    ↓                     │</span>
<span class="s1">│  Temporal Grid (200ms)   │</span>
<span class="s1">│    ↓                     │</span>
<span class="s1">│  Thinker (MoE) → Talker  │</span>
<span class="s1">│    ↕                     │</span>
<span class="bridge">│  Cross-Attention Fusion ◄├──┐</span>
<span class="s1">└──────────────────────────┘  │</span>
<span class="bridge">        Streaming Context Bridge</span>
<span class="s2">┌─ Background Model (S2) ─┐  │</span>
<span class="s2">│  Reasoner (CoT)          ├──┘</span>
<span class="s2">│  Retriever (RAG)         │</span>
<span class="s2">│  Tool Executor           │</span>
<span class="s2">│    ↓                     │</span>
<span class="s2">│  Fusion Layer            │</span>
<span class="s2">└──────────────────────────┘</span>
      </div>
    </div>
    <div>
      <h3>📊 实时统计</h3>
      <div class="stat-row"><span class="stat-label">会话轮次</span><span class="stat-value" id="statTurns">0</span></div>
      <div class="stat-row"><span class="stat-label">静默时长</span><span class="stat-value" id="statSilence">0.0s</span></div>
      <div class="stat-row"><span class="stat-label">S2 委托次数</span><span class="stat-value" id="statDeleg">0</span></div>
      <div class="stat-row"><span class="stat-label">Bridge 注入次数</span><span class="stat-value" id="statBridge">0</span></div>
      <div class="stat-row"><span class="stat-label">中断次数</span><span class="stat-value" id="statInterrupt">0</span></div>
    </div>
    <button class="delegate-btn" onclick="simulateDelegation()">
      🧠 模拟 S2 委托 (Background Model)
    </button>
    <div style="font-size:11px;color:var(--text-dim);margin-top:8px;">
      点击上方按钮模拟 InteractFormer 的<br>
      S1 → S2 → S1 委托流程：<br>
      1. S1 检测到复杂查询<br>
      2. Context Packager 打包上下文<br>
      3. S2 Reasoner 异步推理<br>
      4. Bridge 将结果注入 S1
    </div>
  </div>
</main>

<footer>
  <span>InteractFormer v0.1.0 · Apache 2.0</span>
  <span>Inspired by TML Interaction Models · Based on DuplexOmni/Qwen3-Omni</span>
</footer>

<script>
const API_URL = "__API_URL__";
let stats = { turns: 0, silence: 0, delegations: 0, injections: 0, interruptions: 0 };
let silenceTimer = null;
let lastInputTime = Date.now();

function updateStats() {
  document.getElementById('statTurns').textContent = stats.turns;
  document.getElementById('statSilence').textContent = ((Date.now() - lastInputTime) / 1000).toFixed(1) + 's';
  document.getElementById('statDeleg').textContent = stats.delegations;
  document.getElementById('statBridge').textContent = stats.injections;
  document.getElementById('statInterrupt').textContent = stats.interruptions;
}

function addMessage(role, content) {
  const div = document.createElement('div');
  div.className = 'message ' + role;
  div.innerHTML = role === 'assistant'
    ? '<strong>🤖 InteractFormer</strong><br>' + content.replace(/\n/g, '<br>')
    : content.replace(/\n/g, '<br>');
  document.getElementById('messages').appendChild(div);
  document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
}

function addSystemMessage(content) {
  const div = document.createElement('div');
  div.className = 'message system';
  div.textContent = content;
  document.getElementById('messages').appendChild(div);
  document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
}

async function sendMessage() {
  const input = document.getElementById('input');
  const btn = document.getElementById('sendBtn');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.disabled = true;
  btn.disabled = true;
  lastInputTime = Date.now();
  stats.turns++;
  updateStats();

  addMessage('user', text);

  try {
    // Call vLLM API with streaming
    const resp = await fetch(API_URL + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'interactformer',
        messages: [{role:'user',content:text}],
        stream: true,
        temperature: 0.7,
        max_tokens: 1024
      })
    });

    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant';
    msgDiv.innerHTML = '<strong>🤖 InteractFormer</strong><br><span class="spinner"></span> 思考中...';
    document.getElementById('messages').appendChild(msgDiv);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let fullResponse = '';
    msgDiv.innerHTML = '<strong>🤖 InteractFormer</strong><br>';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value);
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ') && line !== 'data: [DONE]') {
          try {
            const data = JSON.parse(line.slice(6));
            const content = data.choices?.[0]?.delta?.content || '';
            if (content) {
              fullResponse += content;
              msgDiv.innerHTML = '<strong>🤖 InteractFormer</strong><br>' +
                fullResponse.replace(/\n/g, '<br>');
              document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
            }
          } catch(e) {}
        }
      }
    }
    if (!fullResponse) msgDiv.innerHTML = '<strong>🤖 InteractFormer</strong><br>(无响应)';
  } catch(e) {
    addSystemMessage('⚠️ API 连接失败: ' + e.message);
  }

  input.disabled = false;
  btn.disabled = false;
  input.focus();
  updateStats();
}

async function simulateDelegation() {
  stats.delegations++;
  stats.injections++;
  updateStats();
  addSystemMessage('📤 [S1 → Bridge] 委托 Background Model 处理复杂查询...');
  await sleep(500);
  addSystemMessage('🧠 [S2 Reasoner] 正在深度推理 (Step 1/3)...');
  await sleep(800);
  addSystemMessage('🧠 [S2 Reasoner] 中间结论: 这是一个关于架构设计的复杂问题...');
  await sleep(600);
  addSystemMessage('📥 [Bridge → S1] 注入 S2 推理结果到 Temporal Grid cell...');
  addSystemMessage('✅ S2 委托完成！结果已通过 Cross-Attention 融合回 S1');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Update stats every second
setInterval(updateStats, 1000);

// Focus input on load
document.getElementById('input').focus();
</script>
</body>
</html>"""


class DemoHandler(BaseHTTPRequestHandler):
    """HTTP handler for the InteractFormer web demo."""

    api_url: str = "http://localhost:6006/v1"

    def log_message(self, format, *args):
        pass  # Suppress logs

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            html = HTML_TEMPLATE.replace("__API_URL__", self.api_url)
            self._respond(200, html, "text/html; charset=utf-8")
        elif path == "/health":
            self._respond(200, json.dumps({"status": "ok"}))
        else:
            self._respond(404, "Not Found")

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/proxy":
            # Proxy to vLLM
            try:
                content_length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._respond(400, json.dumps({"error": "invalid Content-Length"}))
                return
            if content_length <= 0 or content_length > 1024 * 1024:
                self._respond(413, json.dumps({"error": "request body too large or empty"}))
                return
            body = self.rfile.read(content_length)

            try:
                req = Request(
                    f"{self.api_url}/chat/completions",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                resp = urlopen(req, timeout=120)
                self._respond(resp.status, resp.read())
            except Exception as e:
                self._respond(500, json.dumps({"error": str(e)}))
        else:
            self._respond(404, "Not Found")

    def do_OPTIONS(self):
        self._respond(405, "Method Not Allowed", "text/plain; charset=utf-8")

    def _respond(self, status, body, content_type="application/json"):
        body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="InteractFormer Web Demo")
    parser.add_argument("--host", default="127.0.0.1", help="Server bind host")
    parser.add_argument("--port", type=int, default=6006, help="Server port")
    parser.add_argument("--api", type=str, default="http://localhost:6006/v1",
                        help="vLLM API endpoint")
    args = parser.parse_args()

    DemoHandler.api_url = args.api

    print("=" * 60)
    print("  InteractFormer Web Demo")
    print("=" * 60)
    print()
    print(f"  Local:    http://localhost:{args.port}")
    print(f"  API:      {args.api}")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    server = HTTPServer((args.host, args.port), DemoHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
