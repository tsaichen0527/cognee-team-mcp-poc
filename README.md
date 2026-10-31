# Cognee 多人共享知識庫 POC（Docker + MCP + 免費模型）

用 Docker 自建 [Cognee](https://github.com/topoteretes/cognee)，讓團隊成員從各自的 Claude Code / Cursor 透過 **MCP** 共同寫入、查詢同一個知識圖譜。LLM 與 Embedding 都用**免費**方案：本機 Ollama 或 Gemini 免費額度，加上 FastEmbed。

> 2026-10-31 在 Windows 11 + Docker Desktop + RTX 3060 6GB 上實測可用（cognee 1.6.6 / cognee-mcp 0.5.7）。
> 📖 **完整步驟、設計理由、踩坑紀錄：[docs/GUIDE.md](docs/GUIDE.md)**

## 架構

```text
Alice (Claude Code) ─┐                 ┌────────────── 主機 Docker ───────────────┐
Bob   (Cursor)      ─┼── 區網/VPN ──▶  │ cognee-mcp :8001 ──X-Api-Key──▶ cognee API │
Carol (Claude Code) ─┘                 │  (共用 endpoint)     (127.0.0.1:8000)       │
                                       │                        │  FastEmbed (CPU)  │
                                       │  postgres+pgvector ◀───┤  Ladybug graph    │
                                       │  ollama (GPU) ◀────────┘  或 Gemini        │
                                       └───────────────────────────────────────────┘
```

| 元件 | 免費方案 |
|---|---|
| Embedding | FastEmbed `paraphrase-multilingual-MiniLM-L12-v2`（384 維，中英文可用） |
| LLM | 選項 A：Ollama `llama3.1:8b`（本機 GPU）／選項 B：Gemini `gemini-2.5-flash` 免費額度 |
| 資料庫 | PostgreSQL 17 + pgvector、Ladybug（檔案型 graph） |

## 快速開始

```powershell
git clone https://github.com/tsaichen0527/cognee-team-mcp-poc.git
cd cognee-team-mcp-poc
Copy-Item .env.example .env          # 修改密碼、secret、LLM 選項
docker compose up -d postgres cognee

# 選項 A：本機 LLM
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull llama3.1:8b

# 建立服務帳號 + dataset + API key，把印出的 COGNEE_MCP_API_KEY 貼進 .env
docker compose exec cognee python /poc-scripts/init_kb.py

docker compose up -d --no-deps cognee-mcp

# 驗證：模擬 Alice 寫入、Bob 查詢
Get-Content scripts\mcp_smoke_test.py | docker compose exec -T cognee-mcp python -
```

團隊成員連線（只需要 URL）：

```bash
claude mcp add --transport http cognee http://<主機IP>:8001/mcp --scope user
```

## 專案結構

```text
├── docker-compose.yml        postgres / cognee / cognee-mcp / ollama
├── .env.example              所有設定（LLM 二選一、embedding、secret）
├── docs/GUIDE.md             完整指南：設計理由、步驟、8 個實際踩到的坑、備份還原
├── scripts/
│   ├── init_kb.py            建立服務帳號、共享 dataset、MCP 用的 API key
│   ├── import_docs.py        管理員批次匯入文件 + cognify + 查詢驗證
│   ├── mcp_smoke_test.py     兩個 MCP session 模擬兩位同事共同維護
│   └── _api.py               stdlib REST client（腳本在容器內執行，不用另裝套件）
├── sample-data/              範例 ADR / SOP / 事故報告
└── clients/                  Claude Code、Cursor 的 MCP 設定範本
```

## 重要限制（POC 前必讀）

- **MCP endpoint 沒有驗證**：只能放在可信任的區網 / VPN，不要開到公網
- **所有人透過同一個服務帳號寫入**：cognee-mcp 目前只能用 dataset 名稱，而名稱只在擁有者的 dataset 內解析（[#5619](https://github.com/topoteretes/cognee/issues/5619)），每人各用自己的帳號就無法共享
- **8B 本機模型品質有限**：有時中文問題會回英文答案。正式使用建議換 Gemini 或更大的模型

詳見 [docs/GUIDE.md §2、§10](docs/GUIDE.md#2-為什麼這樣設計重要限制)。

## License

MIT
