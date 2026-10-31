# Cognee 多人共享知識庫 POC：完整建置與操作指南

> 驗證日期：2026-10-31
> 驗證環境：Windows 11 Home、Docker Desktop 29.4.3（WSL2，VM 記憶體 8 GB）、NVIDIA RTX 3060 Laptop 6 GB
> 驗證版本：cognee 1.6.6（`cognee/cognee:main@sha256:56e99ff6…`）、cognee-mcp 0.5.7（`cognee/cognee-mcp:main@sha256:c3a62501…`）、Ollama 0.40.13
>
> 本文所有指令都在上述環境實際跑過；第 9 節「踩坑紀錄」是建置過程中真的遇到的問題。

---

## 目錄

1. [目標與架構](#1-目標與架構)
2. [為什麼這樣設計（重要限制）](#2-為什麼這樣設計重要限制)
3. [免費模型選擇](#3-免費模型選擇)
4. [前置需求](#4-前置需求)
5. [建置步驟](#5-建置步驟)
6. [團隊成員連線（Claude Code / Cursor / 其他 MCP client）](#6-團隊成員連線)
7. [日常操作：MCP 工具怎麼用](#7-日常操作mcp-工具怎麼用)
8. [管理員操作：批次匯入、備份、還原](#8-管理員操作)
9. [踩坑紀錄與故障排除](#9-踩坑紀錄與故障排除)
10. [安全性與已知限制](#10-安全性與已知限制)
11. [POC 驗收清單](#11-poc-驗收清單)
12. [下一步（從 POC 到正式）](#12-下一步)
13. [參考資料](#13-參考資料)

---

## 1. 目標與架構

**目標**：一台主機用 Docker 自建 Cognee，團隊多人透過 **MCP** 從自己的 AI 工具（Claude Code、Cursor…）共同寫入、查詢**同一個知識庫**；LLM 與 Embedding 全部使用**免費**方案。

```text
                         ┌───────────────────── 主機（Windows + Docker Desktop）─────────────────────┐
                         │                                                                            │
 Alice (Claude Code) ─┐  │  ┌──────────────┐  X-Api-Key   ┌──────────────┐     ┌───────────────────┐ │
 Bob   (Cursor)      ─┼──┼─▶│ cognee-mcp   │─────────────▶│ cognee (API) │────▶│ postgres+pgvector │ │
 Carol (Claude Code) ─┘  │  │ :8001 /mcp   │  服務帳號    │ 127.0.0.1    │     │ 帳號/權限/向量    │ │
     區網 / VPN           │  │ API mode     │              │ :8000        │     └───────────────────┘ │
                         │  └──────────────┘              │              │     ┌───────────────────┐ │
                         │                                │  FastEmbed   │     │ Ladybug graph     │ │
                         │                                │  (CPU, 免費) │     │ (volume 檔案)     │ │
                         │                                └──────┬───────┘     └───────────────────┘ │
                         │                                       │ LLM                               │
                         │                         ┌─────────────▼─────────────┐                     │
                         │                         │ ollama（GPU, 免費）        │  或 Gemini 免費額度 │
                         │                         └───────────────────────────┘                     │
                         └────────────────────────────────────────────────────────────────────────────┘
```

| 容器 | 映像 | 角色 |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg17` | 使用者、權限、dataset metadata、向量（pgvector） |
| `cognee` | `cognee/cognee:main` | Cognee 後端 API，**唯一**開啟 graph 檔案的 process；只綁 `127.0.0.1:8000` |
| `cognee-mcp` | `cognee/cognee-mcp:main` | 對外的共用 MCP endpoint（`http://主機:8001/mcp`），以 API mode 轉送到後端 |
| `ollama` | `ollama/ollama` | 本機 LLM（profile `ollama`，選用） |

---

## 2. 為什麼這樣設計（重要限制）

在動手前要先理解 cognee-mcp 目前（0.5.7）的行為，否則很容易架出「看起來能用、其實沒有共享」的系統。

### 2.1 MCP 工具只剩 `remember` / `recall` / `forget`

舊文章常提到的 `cognify`、`search`、`add`、`list_data`、`prune` 等 MCP 工具**已經不對外提供**。目前工具：

| 工具 | 用途 |
|---|---|
| `remember` | 寫入知識（內部自動跑 add → cognify → improve） |
| `recall` | 查詢（可選 `search_type`：`GRAPH_COMPLETION`、`CHUNKS`、`RAG_COMPLETION`、`SUMMARIES`…） |
| `forget` | 刪除 dataset 或單筆資料 |
| `code_search` | 程式碼查詢 |
| `cognify_status` | 背景處理狀態（不在 tools/list，需用名稱呼叫） |

### 2.2 為什麼「所有人共用一個 MCP + 一個服務帳號」

Cognee 後端有完整的多使用者 / dataset ACL，但 **MCP 工具只接受 dataset「名稱」**，而**名稱只會在「呼叫者自己擁有的」dataset 裡解析**（官方 issue #5619，維護者表示是刻意設計）。

所以如果每個人用自己的帳號 token 跑 MCP：

- Bob 呼叫 `remember(dataset_name="team_kb")` → 會**默默建立 Bob 自己的 `team_kb`**，不是 Alice 那個
- Bob 呼叫 `recall(datasets="team_kb")` → **查不到** Alice 分享給他的 dataset

**結論**：在 MCP 層要「共同維護同一個知識庫」，目前可行的做法是 **一個共用 MCP endpoint，以一個服務帳號的 API key 連到後端**。代價是：

- 知識庫裡看不出「是誰寫的」（可在內容中自行註明作者）
- MCP endpoint 本身**沒有驗證**，能連到 port 的人都能讀寫刪 → 只能放在可信任的區網 / VPN

> ChatGPT 版指南建議「每人在自己電腦跑一個個人 MCP sidecar」—— 在目前版本會因為上述名稱解析規則而**無法共享**，所以本 POC 不採用。

### 2.3 為什麼要經過後端（API mode），不讓 MCP 直接讀寫資料庫

官方 compose 裡的 `cognee-mcp` 預設是 direct mode，和後端**同時開啟同一份 Ladybug / LanceDB 檔案**，多 process 寫入會出現 `Could not set lock on file`（issue #5541、#5254）。本 POC 讓 MCP 設 `API_URL=http://cognee:8000`，只有後端一個 process 碰資料庫。

### 2.4 `COGNEE_MCP_AGENT_SCOPED=false`

cognee-mcp 預設會依 client 種類分開寫入（Claude Code → `claude_code_memory`、Cursor → `cursor_vscode_memory`）。關掉之後，所有人預設寫同一個 dataset；實務上仍建議呼叫時明確帶 `dataset_name=team_kb`。

---

## 3. 免費模型選擇

### 3.1 Embedding：FastEmbed（已驗證）

| 項目 | 值 |
|---|---|
| Provider | `fastembed`（cognee 內建，容器內 CPU 推論） |
| 模型 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| 維度 | 384 |
| 費用 | 免費、免 API key、資料不出主機 |
| 備註 | 多語言（中英文可用）；單段最多約 126 tokens，cognee 會自動把 chunk 切小 |

> ⚠ Embedding 模型一旦寫入資料就**不能換**，換了會 `EmbeddingDimensionMismatchError`，必須清空重建。

### 3.2 LLM：二選一

| | 選項 A：本機 Ollama（已驗證） | 選項 B：Gemini 免費額度（未實測） |
|---|---|---|
| 費用 | 完全免費 | 免費額度內免費，超過回 429 |
| 資料 | 不出主機 | 送到 Google（免費層可能被用於改善產品） |
| 品質 | 8B 模型：可用但較弱（見下） | 明顯較好 |
| 速度 | 3 份短文件 cognify ≈ 205 秒 | 快 |
| 硬體 | 需 NVIDIA GPU（≥6 GB VRAM） | 不需要 |
| 設定 | `LLM_PROVIDER=ollama`、`LLM_MODEL=llama3.1:8b`、`LLM_ENDPOINT=http://ollama:11434` | `LLM_PROVIDER=gemini`、`LLM_MODEL=gemini/gemini-2.5-flash`、`LLM_API_KEY=...` |

**選項 A 實測觀察（llama3.1:8b，6 GB VRAM）**：

- 模型 4.7 GB，約 89% 在 GPU、11% offload 到 CPU（context 8192）
- `remember` 一段短文字 ≈ 70 秒；`recall` GRAPH_COMPLETION 幾秒
- 會出現中文問題回英文答案、漏掉英文文件內容的情況 —— POC 驗證流程夠用，正式使用建議換 Gemini 或更大的模型（cognee 官方建議 qwen2.5 14B 以上；qwen2.5 7B 以下、phi3、mistral 7B 列為容易失敗）

**選項 B 注意**：

- Key 要放 `LLM_API_KEY`（只設 `GEMINI_API_KEY` 會報 `LLMAPIKeyNotSetError`）
- 免費額度與可用模型以 [Google AI Studio](https://aistudio.google.com/api-keys) 和 [官方 rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) 為準
- Embedding 仍用 FastEmbed，不消耗 Gemini 額度

---

## 4. 前置需求

| 項目 | 需求 |
|---|---|
| OS | Windows 10/11（Linux / macOS 亦可，指令相同） |
| Docker | Docker Desktop（WSL2 backend、Linux containers） |
| 記憶體 | 實測 4 容器合計約 4.5 GB；WSL2 VM 給 8 GB 足夠 |
| 磁碟 | 映像約 8 GB（cognee 0.9 GB、ollama + llama3.1:8b 約 6 GB）+ 資料 |
| GPU（選項 A） | NVIDIA，安裝最新驅動；Docker Desktop 會自動支援 `--gpus` |
| 網路 | 第一次需下載映像與模型（HuggingFace、Ollama registry） |
| 工具 | git、PowerShell 或 Git Bash |

確認 GPU 能進容器（選項 A）：

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

看得到顯卡名稱即可。

---

## 5. 建置步驟

### 5.1 取得專案

```powershell
git clone https://github.com/<your-account>/cognee-team-mcp-poc.git
cd cognee-team-mcp-poc
```

### 5.2 建立 `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

需要修改的欄位：

| 欄位 | 說明 |
|---|---|
| `DB_PASSWORD` | Postgres 密碼，**用 hex 字串**（避免 `@ : / %` 破壞連線 URL） |
| `FASTAPI_USERS_JWT_SECRET` 等 3 個 | 登入 token 簽章用；**不設的話每次重啟都會隨機產生，所有 token 失效** |
| `KB_SERVICE_EMAIL` / `KB_SERVICE_PASSWORD` | 知識庫服務帳號 |
| `KB_DATASET` | 共享 dataset 名稱，預設 `team_kb` |
| LLM 區塊 | 選項 A 或 B 二選一 |

產生隨機 hex（PowerShell）：

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) })
```

或 Git Bash / Linux：`openssl rand -hex 32`

### 5.3 啟動資料庫與後端

```powershell
docker compose up -d postgres cognee
docker compose ps
```

等 `cognee` 顯示 `(healthy)`（第一次約 1–2 分鐘，會跑 DB migration 並下載 FastEmbed 模型約 220 MB）：

```powershell
docker compose logs -f cognee     # 看到 "Application startup complete" 與 /health 200 即可，Ctrl+C 離開
curl.exe -i http://127.0.0.1:8000/health
```

### 5.4 啟動 Ollama 並下載模型（選項 A）

```powershell
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull llama3.1:8b
```

**暖機**（必要，原因見 [坑 #5](#坑-5llm-connection-test-timed-out-after-30s)）：

```powershell
docker compose exec cognee curl -s http://ollama:11434/api/chat -d '{\"model\":\"llama3.1:8b\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"stream\":false}'
docker compose exec ollama ollama ps
```

`ollama ps` 的 `UNTIL` 顯示 `Forever` 代表模型已常駐 GPU。

> 用 Gemini（選項 B）的話跳過這一步。

### 5.5 初始化知識庫（服務帳號、dataset、API key）

```powershell
docker compose exec cognee python /poc-scripts/init_kb.py
```

> Git Bash 使用者要加 `MSYS_NO_PATHCONV=1`，見 [坑 #3](#坑-3git-bash-把容器路徑改成-windows-路徑)：
> `MSYS_NO_PATHCONV=1 docker compose exec cognee python /poc-scripts/init_kb.py`

輸出範例：

```text
[+] 已建立服務帳號 kb-service@example.com
[+] 已建立 dataset 'team_kb' (edeb4404-48c0-570c-9ac3-e9242d34864d)

把下面這行貼進 .env，然後執行 docker compose up -d cognee-mcp
COGNEE_MCP_API_KEY=xxxxxxxxxxxxxxxx
```

把 `COGNEE_MCP_API_KEY=...` 貼進 `.env`。

### 5.6 設定區網存取並啟動 MCP

查主機區網 IP（`ipconfig`），寫進 `.env`（`:*` 不可省略）：

```dotenv
MCP_ALLOWED_HOSTS=192.168.0.98:*,kb-host:*
```

啟動：

```powershell
docker compose up -d --no-deps cognee-mcp
docker compose logs --tail=20 cognee-mcp
```

> `--no-deps` 很重要，見 [坑 #6](#坑-6改-env-後重啟-mcp結果後端被重建cognify-被中斷)。

log 應出現：

```text
Transport mode: http
API mode enabled: http://cognee:8000
auth posture: authentication=required, multi_tenant=enabled
```

### 5.7 端到端驗證（模擬兩位同事）

```powershell
Get-Content scripts\mcp_smoke_test.py | docker compose exec -T cognee-mcp python -
```

（Git Bash：`docker compose exec -T cognee-mcp python - < scripts/mcp_smoke_test.py`）

實測輸出：

```text
[+] 已連上 Cognee 0.5.7
[+] 可用工具：remember, recall, code_search, forget, search_tools, call_tool
[*] Alice remember 中（會跑 add + cognify，本機 LLM 可能要數分鐘）...
Stored permanently in knowledge graph (dataset=team_kb, status=completed).
[+] remember 完成，耗時 70s
[*] Bob recall（CHUNKS）：訂單服務為什麼改用 PostgreSQL？誰做的決策？
1 memory found (1 from team_kb)
[graph] ADR-007（2026-10）：訂單服務改用 PostgreSQL 17 儲存交易資料，Redis 只做快取。決策人是平台組的 Alice，原因是需要 JSONB 與 row-level security。

[*] Bob recall（GRAPH_COMPLETION）：訂單服務為什麼改用 PostgreSQL？誰做的決策？
1 memory found (1 from team_kb)
[graph] 改用 PostgreSQL 的原因是需要 JSONB 與 row-level security。決策人是平台組的 Alice。
```

Alice 與 Bob 是兩個獨立的 MCP session，Bob 查得到 Alice 寫入的內容 → **共享知識庫成立**。

從另一台電腦驗證網路可達（應回 200；回 421 見 [坑 #7](#坑-7同事用區網-ip-連-mcp-得到-421-misdirected-request)）：

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" -X POST http://192.168.0.98:8001/mcp `
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" `
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl\",\"version\":\"0\"}}}'
```

### 5.8 Windows 防火牆

同事連不到 8001 時，在主機以系統管理員 PowerShell 開放（僅限私人網路）：

```powershell
New-NetFirewallRule -DisplayName "Cognee MCP 8001" -Direction Inbound -Protocol TCP -LocalPort 8001 -Action Allow -Profile Private
```

---

## 6. 團隊成員連線

每位成員**只需要 MCP URL**：`http://<主機IP>:8001/mcp`，不需要任何金鑰、不需要裝 Docker。

### Claude Code

```bash
claude mcp add --transport http cognee http://192.168.0.98:8001/mcp --scope user
claude mcp list
```

或放在專案的 `.mcp.json`（範本：[`clients/claude-code.mcp.json`](../clients/claude-code.mcp.json)），整個專案的成員共用：

```json
{
  "mcpServers": {
    "cognee": { "type": "http", "url": "http://192.168.0.98:8001/mcp" }
  }
}
```

### Cursor

`~/.cursor/mcp.json`（範本：[`clients/cursor.mcp.json`](../clients/cursor.mcp.json)）：

```json
{
  "mcpServers": {
    "cognee": { "url": "http://192.168.0.98:8001/mcp" }
  }
}
```

### Claude Desktop

Claude Desktop 的設定檔只支援 stdio server，需用 `mcp-remote` 橋接（需 Node.js；此項未實測）：

```json
{
  "mcpServers": {
    "cognee": { "command": "npx", "args": ["-y", "mcp-remote", "http://192.168.0.98:8001/mcp", "--allow-http"] }
  }
}
```

> 實測範圍：MCP 協定層（initialize / tools/list / tools/call）已用腳本從 localhost 與區網 IP 驗證；Claude Code / Cursor 的設定格式取自 cognee-mcp 官方 README。

---

## 7. 日常操作：MCP 工具怎麼用

在 Claude Code / Cursor 裡直接用自然語言，AI 會自己呼叫工具。建議在團隊的 `CLAUDE.md` 或 Cursor rules 寫一段約定：

```markdown
## 團隊知識庫（cognee MCP）
- 查詢技術決策、SOP、事故紀錄時，先用 cognee 的 recall 工具，datasets 一律填 team_kb。
- 寫入知識時用 remember，dataset_name 一律填 team_kb，內容開頭註明「作者：<你的名字>，日期：YYYY-MM-DD」。
- 不要寫入密碼、金鑰、客戶個資。
- 不要呼叫 forget，刪除請找知識庫管理員。
```

範例提示：

| 想做的事 | 對 AI 說 |
|---|---|
| 寫入 | 「把這次 incident 的根因與修正方式 remember 到 team_kb，作者 Bob」 |
| 查詢 | 「用 recall 查 team_kb：部署後 5xx 太高要怎麼處理？」 |
| 只找原文段落 | 「用 recall 的 CHUNKS 模式查 team_kb 裡跟 pg_dump 有關的段落」 |
| 長文件背景處理 | 「remember 這份文件，background=true」，之後問「cognify_status 是什麼狀態」 |

`recall` 常用 `search_type`：

| 類型 | 說明 | 是否耗 LLM |
|---|---|---|
| `GRAPH_COMPLETION`（預設） | 結合知識圖譜 + LLM 生成答案 | 是 |
| `CHUNKS` | 回傳最相關的原文段落 | 否 |
| `RAG_COMPLETION` | 傳統 RAG | 是 |
| `SUMMARIES` | 回傳摘要 | 否 |

---

## 8. 管理員操作

### 8.1 批次匯入文件（REST）

大量文件用 REST 一次上傳 + 一次 cognify，比透過 MCP 逐筆 `remember` 有效率。把檔案放進 `sample-data/`（或修改 compose 掛載其他目錄），然後：

```powershell
docker compose exec cognee python /poc-scripts/import_docs.py
# 指定容器內目錄與驗證問題：
docker compose exec cognee python /poc-scripts/import_docs.py /poc-sample-data "部署失敗怎麼 rollback？"
```

實測（3 份短 Markdown、llama3.1:8b）：上傳 1 秒、cognify 205 秒。

### 8.2 Swagger / REST API

後端只綁 `127.0.0.1`，在主機上開 <http://127.0.0.1:8000/docs>。常用 API：

| 用途 | API |
|---|---|
| 登入（form-urlencoded） | `POST /api/v1/auth/login`（`username`=email、`password`） |
| API key | `GET/POST/DELETE /api/v1/auth/api-keys` |
| dataset | `GET/POST /api/v1/datasets` |
| 上傳 | `POST /api/v1/add`（multipart：`data`=檔案、`datasetName` 或 `datasetId`） |
| 建圖 | `POST /api/v1/cognify`（`{"datasets":["team_kb"],"run_in_background":false}`） |
| 查詢 | `POST /api/v1/search`（`{"search_type":"CHUNKS","query":"...","datasets":["team_kb"]}`） |

### 8.3 撤換 MCP 的 API key（例如懷疑外洩）

```powershell
docker compose exec cognee python /poc-scripts/init_kb.py   # 產生新 key
# 更新 .env 的 COGNEE_MCP_API_KEY 後：
docker compose up -d --no-deps cognee-mcp
```

舊 key 可在 Swagger 用 `GET /api/v1/auth/api-keys` 查 id，再 `DELETE /api/v1/auth/api-keys/{id}` 刪除。

### 8.4 備份

狀態分散在**兩個地方**，缺一不可：

| Volume | 內容 |
|---|---|
| `cognee-poc_pgdata` | 帳號、權限、dataset、向量 |
| `cognee-poc_cognee_system` / `cognee-poc_cognee_data` | Ladybug 圖資料庫、原始檔案、模型快取 |

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker compose stop cognee-mcp cognee
docker compose exec -T postgres pg_dump -U cognee -d cognee_db -Fc -f /tmp/pg.dump
docker compose cp postgres:/tmp/pg.dump backups\postgres.dump
docker run --rm -v cognee-poc_cognee_system:/s -v cognee-poc_cognee_data:/d -v ${PWD}\backups:/b alpine tar czf /b/cognee-storage.tgz -C / s d
docker compose up -d cognee
docker compose up -d --no-deps cognee-mcp
```

> 不要寫成 `pg_dump ... > backups\postgres.dump`：PowerShell 5.1 的 `>` 會轉成 UTF-16，二進位 dump 就壞了。所以先在容器內 `-f` 寫檔，再 `docker compose cp` 出來。

實測：3 份文件的 POC，`postgres.dump` 約 440 KB，`cognee-storage.tgz` 約 214 MB（大部分是 FastEmbed 模型快取）。

`.env` 另外加密保存，不要和備份放一起。

### 8.5 還原（破壞性，先在測試機演練）

以下流程已實測：還原後服務帳號、API key、`team_kb` 都在，MCP `recall` 仍可正確回答。

```powershell
docker compose stop cognee-mcp cognee
docker compose cp backups\postgres.dump postgres:/tmp/pg.dump
docker compose exec -T postgres pg_restore -U cognee -d cognee_db --clean --if-exists /tmp/pg.dump
docker run --rm -v cognee-poc_cognee_system:/s -v cognee-poc_cognee_data:/d -v ${PWD}\backups:/b alpine sh -c "rm -rf /s/* /d/* && tar xzf /b/cognee-storage.tgz -C / && chown -R 1000:1000 /s /d"
docker compose up -d cognee
docker compose up -d --no-deps cognee-mcp
```

### 8.6 停止 / 重置

```powershell
docker compose --profile ollama stop      # 停止（保留資料）
docker compose --profile ollama down      # 移除容器（保留 volume）
docker compose --profile ollama down -v   # ⚠ 連同所有 volume 刪除 = 刪掉整個知識庫
```

---

## 9. 踩坑紀錄與故障排除

以下 1–8 是本次建置**實際遇到**的問題，依發生順序排列。

### 坑 #1：Docker Desktop 啟動後 `docker` 指令仍連不上

**現象**

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ...
```

`wsl -l -v` 顯示 `docker-desktop  Stopped`。

**原因**：Docker Desktop 第一次啟動沒有成功把 WSL VM 帶起來（程序存在但 engine 沒起）。

**處理**：再啟動一次 Docker Desktop，等 1–2 分鐘，直到 `docker info` 成功、`wsl -l -v` 顯示 `Running`。仍失敗時執行 `wsl --update`、重新開機。

### 坑 #2：compose 必填變數擋住「所有」服務

**現象**：只想 `docker compose up -d postgres`，卻報：

```text
required variable COGNEE_MCP_API_KEY is missing a value
```

**原因**：compose 的 `${VAR:?msg}` 在**解析整份檔案時**就檢查，不管你啟動哪個服務。而 API key 必須等後端起來後才產生得出來 → 雞生蛋問題。

**處理**：本專案改成 `${COGNEE_MCP_API_KEY:-}`，並以啟動順序（5.3 → 5.5 → 5.6）保證。

### 坑 #3：Git Bash 把容器路徑改成 Windows 路徑

**現象**

```text
python: can't open file '/app/C:/Program Files/Git/poc-scripts/init_kb.py'
```

**原因**：Git Bash（MSYS）會自動把 `/poc-scripts/...` 這種看起來像 Unix 絕對路徑的參數轉成 Windows 路徑。

**處理**：指令前加 `MSYS_NO_PATHCONV=1`，或改用 PowerShell。

### 坑 #4：FastEmbed 下載模型 `Permission denied`

**現象**

```text
fastembed model cache /cognee-storage/hf-cache/hub is unusable ([Errno 13] Permission denied ...)
ValueError: Could not load model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 from any source.
```

**原因**：cognee 容器以 uid 1000（`cognee`）執行。新增一個 named volume 掛到映像裡**不存在**的路徑時，Docker 會用 root 建立該目錄 → uid 1000 寫不進去。

**處理**：把 `HF_HOME` 指到映像內已屬於 uid 1000 的 `/cognee-storage/system/hf-cache`（隨 `cognee_system` volume 持久化）。通則：**掛 volume 前先確認映像內該路徑存在且擁有者正確**：

```powershell
docker run --rm --entrypoint sh cognee/cognee:main -c "id; ls -ld /cognee-storage/*"
```

### 坑 #5：`LLM connection test timed out after 30s`

**現象**：第一次 `remember`，MCP 回：

```text
Error: Remember failed: Client error '409 Conflict' for url 'http://cognee:8000/api/v1/remember'
```

後端 log：

```text
LLM connection test timed out after 30s ... Set COGNEE_SKIP_CONNECTION_TEST=true to bypass this check.
```

接著 `recall` 回 `404 Not Found`（`knowledge graph is empty; run cognify`）—— 這是連帶結果，因為 cognify 沒跑成。

**原因**：cognee 在每個 process 第一次跑 pipeline 時會先測試 LLM（30 秒逾時）。Ollama 第一次要把 4.7 GB 模型載入 GPU，實測冷啟動 **70–100 秒**，熱的時候 0.16 秒。

**處理**：

1. `OLLAMA_KEEP_ALIVE=-1`（本專案已設定），模型載入後常駐 GPU
2. 每次 ollama 容器重啟後先暖機（5.4 節的 curl）
3. 替代方案：`.env` 加 `COGNEE_SKIP_CONNECTION_TEST=true`（此時**一定要**明確設定 `EMBEDDING_DIMENSIONS`，否則維度自動偵測也會被跳過）

### 坑 #6：改 `.env` 後重啟 MCP，結果後端被重建、cognify 被中斷

**現象**：批次匯入 cognify 跑到一半，`import_docs.py` 沒有輸出就結束了。

**原因**：`cognee` 服務用 `env_file: .env`。修改 `.env`（例如加 `MCP_ALLOWED_HOSTS`）後執行 `docker compose up -d cognee-mcp`，compose 會因為 `depends_on` 檢查並**重建設定有變的 `cognee`**，正在跑的工作就被砍掉。

**處理**：只想動 MCP 時用 `docker compose up -d --no-deps cognee-mcp`；要改後端設定時，挑沒有人在匯入的時段。

### 坑 #7：同事用區網 IP 連 MCP 得到 `421 Misdirected Request`

**現象**：主機上 `http://localhost:8001/mcp` 正常（200），同事用 `http://192.168.0.98:8001/mcp` 得到 421。

**原因**：cognee-mcp 的 DNS rebinding 防護只接受 localhost 的 Host header。

**處理**：`.env` 設 `MCP_ALLOWED_HOSTS=192.168.0.98:*,kb-host:*`（`:*` 不可省略），再 `docker compose up -d --no-deps cognee-mcp`。實測修正後回 200。
也可以 `MCP_DISABLE_DNS_REBINDING_PROTECTION=true`，但不建議。

### 坑 #8：MCP 工具失敗卻「看起來成功」

**現象**：`remember` 實際失敗，但 MCP 回應的 `isError` 是 `false`，內容是一段 `Error: Remember failed: ...` 文字。

**原因**：cognee-mcp 把後端錯誤包成一般文字回傳。

**處理**：自己寫自動化腳本時要檢查文字是否以 `Error:` 開頭（`scripts/mcp_smoke_test.py` 已處理）；AI client 通常看得懂這段文字，但使用者要留意 AI 有沒有回報「寫入失敗」。

### 其他常見問題（來自官方 issue 與文件，非本次實測）

| 現象 | 原因 / 處理 |
|---|---|
| `Missing required pgvector credentials` | `ENABLE_BACKEND_ACCESS_CONTROL=true` 時 pgvector **不會**沿用 `DB_*`，要另外設 `VECTOR_DB_HOST/PORT/USERNAME/PASSWORD/NAME`（本專案 compose 已設定） |
| `LLMAPIKeyNotSetError` | `LLM_API_KEY` 不能空；Ollama 隨便填，Gemini 要填真的 key |
| Ollama 回 404 | `LLM_ENDPOINT` 不要加 `/v1`（預設 litellm_native 框架） |
| `EmbeddingDimensionMismatchError` | 換了 embedding 模型；要清空 volume 重建（issue #6092：連 `forget` 也會被擋） |
| 重啟後登入 token 全部失效 | 沒設 `FASTAPI_USERS_JWT_SECRET`，每次啟動隨機產生 |
| `Could not set lock on file` | 多個 process 同時開 Ladybug 檔案；確認 MCP 是 API mode（`API_URL` 有設） |
| Gemini `429` | 免費額度或速率用完；降低 `LLM_RATE_LIMIT_REQUESTS`、分批匯入 |
| 上傳 >1 MiB 檔案名稱遺失（Windows） | issue #6344 |
| `main` 標籤被舊版覆蓋 | issue #6075；正式使用請釘 `main-<sha>` 或 digest |
| MCP 連線 `403` | CORS / Origin 檢查；瀏覽器型 client 需設 `MCP_CORS_ALLOW_ORIGINS` |

### 除錯指令速查

```powershell
docker compose ps
docker compose logs --tail=100 cognee
docker compose logs --tail=50 cognee-mcp
docker compose exec ollama ollama ps
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8001/health
docker stats --no-stream
```

---

## 10. 安全性與已知限制

| 項目 | 現況 | POC 對策 |
|---|---|---|
| MCP endpoint 無驗證 | 能連到 8001 的人可讀、寫、`forget` | 只開在可信任區網 / VPN（如 Tailscale）；**不要**開到公網或做 port forwarding |
| 後端註冊 API 無法關閉 | `POST /api/v1/auth/register` 一律開放 | 後端只綁 `127.0.0.1:8000`，不對外 |
| 無法追蹤作者 | 所有 MCP 寫入都是同一個服務帳號 | 寫入內容註明作者；重要知識走 Git PR 再匯入 |
| recall 會把問答寫回 dataset | issue #6375 | POC 可接受；留意查詢內容也會被保存 |
| Ladybug 是檔案型 graph | 單一 process、大量文件有穩定性問題（#6170 約 1,650 份） | POC 夠用；正式評估 Neo4j / Postgres graph |
| pgvector 無 ANN 索引 | 全表掃描（#4362） | 資料量大時再評估 |
| 資料外送 | 選項 B（Gemini）會把內容送到 Google | 機密資料只用選項 A |
| 浮動標籤 | `main` 每次 push 都會變 | 驗證後在 `.env` 設 `COGNEE_TAG=main-<sha>` |

---

## 11. POC 驗收清單

- [ ] `docker compose ps`：postgres / cognee / cognee-mcp 皆 healthy
- [ ] `curl http://127.0.0.1:8000/api/v1/datasets` 未帶驗證回 **401**
- [ ] 從其他電腦連不到主機的 8000 port
- [ ] `init_kb.py` 成功建立服務帳號、`team_kb`、API key
- [ ] `mcp_smoke_test.py`：Alice remember 成功、Bob recall 查得到
- [ ] 至少 2 位同事從自己電腦的 Claude Code / Cursor 連上 MCP，互相查到對方寫入的內容
- [ ] `import_docs.py` 批次匯入 + CHUNKS / GRAPH_COMPLETION 查詢正常
- [ ] 中英文查詢品質可接受（不行就換 Gemini 或更大的 LLM）
- [ ] 重啟主機 → Docker 自動起來 → 資料還在（記得 Ollama 暖機）
- [ ] 完成一次備份與還原演練
- [ ] 團隊約定（寫入格式、禁止敏感資料、禁止 forget）已公告

---

## 12. 下一步

1. **LLM 品質**：改用 Gemini 免費額度（選項 B），或在有更大 GPU 的機器跑 qwen2.5:14b 以上
2. **每人身分 / 權限**：等 cognee-mcp 支援以 dataset id 指定（issue #5619），或自建「逐請求驗證使用者 → 轉送對應 API key」的 MCP gateway
3. **正式環境**：移到 24×7 的 Linux 主機、釘選映像版本、反向代理 + TLS、定期備份與還原演練
4. **圖資料庫**：資料量大時評估 Neo4j（多租戶需 `neo4j_community` handler）或 `postgres` graph
5. **治理流程**：重大知識（ADR、SOP）先走 Git PR 審核，再由管理員批次匯入；MCP 寫入當作「草稿 / 經驗」層

---

## 13. 參考資料

- Cognee：<https://github.com/topoteretes/cognee>
- cognee-mcp README：<https://github.com/topoteretes/cognee/blob/main/cognee-mcp/README.md>
- `.env.template`（所有設定的權威來源）：<https://github.com/topoteretes/cognee/blob/main/.env.template>
- Ollama 模型建議：<https://github.com/topoteretes/cognee/blob/main/docs/ollama_models.md>
- FastEmbed 支援模型：<https://qdrant.github.io/fastembed/examples/Supported_Models/>
- Gemini API 免費額度：<https://ai.google.dev/gemini-api/docs/pricing>、<https://ai.google.dev/gemini-api/docs/rate-limits>
- 相關 issue：
  [#5619](https://github.com/topoteretes/cognee/issues/5619) MCP 只能用 dataset 名稱 ·
  [#5541](https://github.com/topoteretes/cognee/issues/5541) / [#5254](https://github.com/topoteretes/cognee/issues/5254) 檔案型 DB 多 process 鎖 ·
  [#6375](https://github.com/topoteretes/cognee/issues/6375) recall 寫回 dataset ·
  [#6075](https://github.com/topoteretes/cognee/issues/6075) Docker 標籤覆蓋 ·
  [#6092](https://github.com/topoteretes/cognee/issues/6092) 維度不符擋住 forget ·
  [#6170](https://github.com/topoteretes/cognee/issues/6170) Ladybug 大量文件 ·
  [#4362](https://github.com/topoteretes/cognee/issues/4362) pgvector 無 ANN 索引
