"""模擬兩位同事透過同一個 MCP endpoint 共同維護知識庫。

  Alice（MCP session 1）呼叫 remember 寫入一筆知識
  Bob  （MCP session 2）呼叫 recall 查到 Alice 寫的內容

執行（在專案根目錄，腳本由 stdin 送進 cognee-mcp 容器）：
    docker compose exec -T cognee-mcp python - < scripts/mcp_smoke_test.py

也可以從任何裝了 Python 3 的電腦執行：
    MCP_URL=http://<主機IP>:8001/mcp python scripts/mcp_smoke_test.py
"""

import json
import os
import time
import urllib.request

MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")
DATASET = os.getenv("KB_DATASET", "team_kb")


class McpSession:
    def __init__(self, client_name):
        self.client_name = client_name
        self.session_id = None
        self.next_id = 1

    def _post(self, payload, timeout=1800):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        req = urllib.request.Request(MCP_URL, json.dumps(payload).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            self.session_id = resp.headers.get("mcp-session-id") or self.session_id
            body = resp.read().decode()

        # streamable HTTP 可能回 JSON，也可能回 SSE（data: {...}）
        messages = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        for raw in messages or [body]:
            if raw:
                message = json.loads(raw)
                if message.get("id") == payload.get("id"):
                    return message
        return None

    def call(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.next_id += 1
        message = self._post(payload)
        if "error" in message:
            raise RuntimeError(f"{method} failed: {message['error']}")
        return message["result"]

    def initialize(self):
        result = self.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "0.1"},
            },
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def tool(self, name, arguments):
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(c.get("text", "") for c in result.get("content", []))
        # cognee-mcp 把後端錯誤包成一般文字回傳（isError 仍是 false），所以也檢查 "Error:" 開頭
        if result.get("isError") or text.lstrip().startswith("Error:"):
            raise RuntimeError(f"{name} returned error: {text}")
        return text


def main():
    alice = McpSession("alice-claude-code")
    info = alice.initialize()
    print(f"[+] 已連上 {info['serverInfo']['name']} {info['serverInfo'].get('version', '')}")

    tools = [t["name"] for t in alice.call("tools/list")["tools"]]
    print(f"[+] 可用工具：{', '.join(tools)}")

    fact = (
        "ADR-007（2026-10）：訂單服務改用 PostgreSQL 17 儲存交易資料，Redis 只做快取。"
        "決策人是平台組的 Alice，原因是需要 JSONB 與 row-level security。"
    )
    started = time.time()
    print("[*] Alice remember 中（會跑 add + cognify，本機 LLM 可能要數分鐘）...")
    print(alice.tool("remember", {"data": fact, "dataset_name": DATASET})[:500])
    print(f"[+] remember 完成，耗時 {time.time() - started:.0f}s")

    bob = McpSession("bob-cursor")
    bob.initialize()
    question = "訂單服務為什麼改用 PostgreSQL？誰做的決策？"
    for search_type in ("CHUNKS", "GRAPH_COMPLETION"):
        print(f"[*] Bob recall（{search_type}）：{question}")
        answer = bob.tool(
            "recall", {"query": question, "datasets": DATASET, "search_type": search_type, "top_k": 5}
        )
        print(answer[:1200])
        print()


if __name__ == "__main__":
    main()
