"""Minimal Cognee REST client (stdlib only, so it runs inside the cognee container)."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_URL = os.getenv("COGNEE_BASE_URL", "http://localhost:8000")


class ApiError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def request(method, path, *, token=None, json_body=None, form=None, files=None, timeout=60):
    headers = {"Accept": "application/json"}
    data = None

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif files is not None:
        data, content_type = _encode_multipart(form or {}, files)
        headers["Content-Type"] = content_type
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as err:
        raise ApiError(err.code, err.read().decode()) from None

    return json.loads(body) if body else None


def _encode_multipart(fields, files):
    """fields: {name: str | list[str]}, files: [(field, filename, bytes)]."""
    boundary = uuid.uuid4().hex
    parts = []

    for name, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{item}\r\n'.encode()
            )

    for field, filename, content in files:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )

    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def login(email, password):
    result = request("POST", "/api/v1/auth/login", form={"username": email, "password": password})
    return result["access_token"]


def require_env(name):
    value = os.getenv(name)
    if not value or value.startswith("change-me"):
        sys.exit(f"[x] 請先在 .env 設定 {name}")
    return value
