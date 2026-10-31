"""批次匯入文件到共享 dataset，跑 cognify，然後做一次查詢驗證。

給「知識庫管理員」用：大量文件走 REST 批次匯入，比透過 MCP 一筆一筆 remember 有效率。

執行（在專案根目錄）：
    docker compose exec cognee python /poc-scripts/import_docs.py                 # 匯入 sample-data/
    docker compose exec cognee python /poc-scripts/import_docs.py /some/dir "問題"  # 指定容器內目錄與查詢
"""

import os
import sys
import time
from pathlib import Path

from _api import login, request, require_env

DEFAULT_DIR = "/poc-sample-data"
DEFAULT_QUERY = "訂單服務為什麼改用 PostgreSQL？後來發生過什麼事故？"


def main():
    source_dir = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR)
    query = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_QUERY
    dataset_name = os.getenv("KB_DATASET", "team_kb")

    token = login(require_env("KB_SERVICE_EMAIL"), require_env("KB_SERVICE_PASSWORD"))

    files = [("data", p.name, p.read_bytes()) for p in sorted(source_dir.glob("*")) if p.is_file()]
    if not files:
        sys.exit(f"[x] {source_dir} 裡沒有檔案")

    request("POST", "/api/v1/add", token=token, form={"datasetName": dataset_name}, files=files, timeout=300)
    print(f"[+] 已上傳 {len(files)} 個檔案到 '{dataset_name}'：{', '.join(f[1] for f in files)}")

    started = time.time()
    print("[*] cognify 中（抽取實體/關係、建立圖譜與向量；本機 LLM 可能要數分鐘）...")
    request(
        "POST",
        "/api/v1/cognify",
        token=token,
        json_body={"datasets": [dataset_name], "run_in_background": False},
        timeout=3600,
    )
    print(f"[+] cognify 完成，耗時 {time.time() - started:.0f}s")

    for search_type in ("CHUNKS", "GRAPH_COMPLETION"):
        result = request(
            "POST",
            "/api/v1/search",
            token=token,
            json_body={"search_type": search_type, "query": query, "datasets": [dataset_name], "top_k": 5},
            timeout=600,
        )
        print(f"\n[{search_type}] {query}")
        print(str(result)[:1500])


if __name__ == "__main__":
    main()
