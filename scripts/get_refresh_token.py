#!/usr/bin/env python3
"""OAuth 同意フローを一度だけローカルで実行し、リフレッシュトークンを取得する。

使い方:
  GOOGLE_CLIENT_ID=xxxxx GOOGLE_CLIENT_SECRET=yyyyy python scripts/get_refresh_token.py

OAuth クライアントは「デスクトップアプリ」タイプで作成し、
承認済みリダイレクト URI に http://localhost:8765/ が含まれることを確認する
（デスクトップアプリタイプなら loopback は自動で許可される）。
"""
from __future__ import annotations

import http.server
import os
import sys
import urllib.parse
import webbrowser

import requests

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/"

result: dict[str, str] = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            result["code"] = params["code"][0]
        if "error" in params:
            result["error"] = params["error"][0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in result
        body = "認証が完了しました。このタブを閉じてターミナルに戻ってください。" if ok else "認証に失敗しました。"
        self.wfile.write(f"<html><body style='font-family:sans-serif'><h3>{body}</h3></body></html>".encode())

    def log_message(self, *_args):  # サーバーログを抑制
        pass


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        sys.exit("GOOGLE_CLIENT_ID と GOOGLE_CLIENT_SECRET を環境変数で渡してください")

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )

    server = http.server.HTTPServer(("localhost", PORT), Handler)
    print("ブラウザで次の URL を開いて認証してください:\n")
    print(auth_url, "\n")
    try:
        webbrowser.open(auth_url)
    except Exception:  # noqa: BLE001
        pass

    while "code" not in result and "error" not in result:
        server.handle_request()

    if "code" not in result:
        sys.exit(f"認証に失敗しました: {result.get('error')}")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": result["code"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    data = token_resp.json()
    refresh_token = data.get("refresh_token")

    if not refresh_token:
        sys.exit(
            "refresh_token が返りませんでした。\n"
            "https://myaccount.google.com/permissions から該当アプリのアクセスを削除し、"
            "もう一度実行してください。\n"
            f"応答: {data}"
        )

    print("\n===== GitHub Secrets に登録する値 =====")
    print(f"GOOGLE_REFRESH_TOKEN = {refresh_token}")
    print("======================================")


if __name__ == "__main__":
    main()
