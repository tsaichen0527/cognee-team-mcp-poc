"""建立知識庫服務帳號、共享 dataset，並產生給 cognee-mcp 使用的 API key。

執行（在專案根目錄）：
    docker compose exec cognee python /poc-scripts/init_kb.py

可重複執行：帳號與 dataset 已存在時會沿用，但每次都會產生一把新的 API key。
"""

import os

from _api import ApiError, login, request, require_env


def ensure_user(email, password):
    try:
        request("POST", "/api/v1/auth/register", json_body={"email": email, "password": password})
        print(f"[+] 已建立服務帳號 {email}")
    except ApiError as err:
        if "ALREADY_EXISTS" not in err.body:
            raise
        print(f"[=] 服務帳號 {email} 已存在，沿用")


def ensure_dataset(token, name):
    for dataset in request("GET", "/api/v1/datasets", token=token):
        if dataset["name"] == name:
            print(f"[=] dataset '{name}' 已存在 ({dataset['id']})")
            return dataset["id"]

    dataset = request("POST", "/api/v1/datasets", token=token, json_body={"name": name})
    print(f"[+] 已建立 dataset '{name}' ({dataset['id']})")
    return dataset["id"]


def create_api_key(token, name):
    result = request("POST", "/api/v1/auth/api-keys", token=token, json_body={"name": name})
    return result["key"] if isinstance(result, dict) else result


def main():
    email = require_env("KB_SERVICE_EMAIL")
    password = require_env("KB_SERVICE_PASSWORD")
    dataset_name = os.getenv("KB_DATASET", "team_kb")

    ensure_user(email, password)
    token = login(email, password)
    ensure_dataset(token, dataset_name)
    api_key = create_api_key(token, "cognee-mcp-shared")

    print()
    print("把下面這行貼進 .env，然後執行 docker compose up -d cognee-mcp")
    print(f"COGNEE_MCP_API_KEY={api_key}")


if __name__ == "__main__":
    main()
