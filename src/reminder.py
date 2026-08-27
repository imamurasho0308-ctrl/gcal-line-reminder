#!/usr/bin/env python3
"""Google カレンダーの予定を LINE (Messaging API) にリマインドする。

GitHub Actions から cron 実行される想定。通知済みの予定は state/notified.json に
記録し、二重通知を防ぐ。状態ファイルはワークフローがリポジトリへコミットして永続化する。

必要な環境変数:
  GOOGLE_CLIENT_ID           OAuth クライアント ID
  GOOGLE_CLIENT_SECRET       OAuth クライアントシークレット
  GOOGLE_REFRESH_TOKEN       scripts/get_refresh_token.py で取得したリフレッシュトークン
  LINE_CHANNEL_ACCESS_TOKEN  LINE Messaging API のチャネルアクセストークン（長期）
  LINE_TO                    送信先の userId（自分の userId でも可）

任意の環境変数:
  CALENDAR_IDS           対象カレンダー ID をカンマ区切りで（既定: primary）
  REMIND_BEFORE_MINUTES  何分前に通知するか。カンマ区切りで複数可（既定: 30）
  WINDOW_MINUTES         先読みする追加の猶予分（既定: 60）
  TIMEZONE              メッセージ表示用の IANA タイムゾーン（既定: Asia/Tokyo）
  SKIP_ALL_DAY          終日予定を無視するか（既定: true）
  DRY_RUN              true なら LINE 送信せず内容を標準出力に表示（既定: false）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "notified.json"

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

JP_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"環境変数 {name} が未設定です")
    return val or ""


def get_access_token() -> str:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": env("GOOGLE_CLIENT_ID", required=True),
            "client_secret": env("GOOGLE_CLIENT_SECRET", required=True),
            "refresh_token": env("GOOGLE_REFRESH_TOKEN", required=True),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"アクセストークンの取得に失敗しました {resp.status_code}: {resp.text}")
    return resp.json()["access_token"]


def fetch_events(token: str, calendar_id: str, time_min: datetime, time_max: datetime) -> list[dict]:
    resp = requests.get(
        CALENDAR_API.format(cal=quote(calendar_id, safe="")),
        headers={"Authorization": f"Bearer {token}"},
        params={
            "timeMin": time_min.isoformat().replace("+00:00", "Z"),
            "timeMax": time_max.isoformat().replace("+00:00", "Z"),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "50",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def prune_state(state: dict, now: datetime) -> dict:
    cutoff = now - timedelta(days=2)
    out = {}
    for key, ts in state.items():
        parsed = _parse_iso(ts)
        if parsed and parsed > cutoff:
            out[key] = ts
    return out


def event_start_utc(event: dict, tz: ZoneInfo) -> tuple[datetime | None, bool]:
    start = event.get("start", {})
    raw = start.get("dateTime") or start.get("date")
    if not raw:
        return None, False
    all_day = "dateTime" not in start
    dt = _parse_iso(raw)
    if dt is None:
        return None, all_day
    if dt.tzinfo is None:  # 終日予定は naive な日付
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc), all_day


def format_message(event: dict, tz: ZoneInfo, minutes_until: float) -> str:
    summary = event.get("summary") or "(無題の予定)"
    start = event.get("start", {})
    end = event.get("end", {})
    all_day = "dateTime" not in start

    lines = [f"⏰ 約{max(0, round(minutes_until))}分後に予定", summary]

    if all_day:
        d = _parse_iso(start.get("date"))
        if d:
            lines.append(f"🗓 {d.month}/{d.day}({JP_WEEKDAYS[d.weekday()]}) 終日")
    else:
        s = _parse_iso(start["dateTime"]).astimezone(tz)
        text = f"🕒 {s.month}/{s.day}({JP_WEEKDAYS[s.weekday()]}) {s:%H:%M}"
        e_raw = end.get("dateTime")
        if e_raw:
            e = _parse_iso(e_raw).astimezone(tz)
            text += f"〜{e:%H:%M}"
        lines.append(text)

    if event.get("location"):
        lines.append(f"📍 {event['location']}")
    if event.get("hangoutLink"):
        lines.append(event["hangoutLink"])
    return "\n".join(lines)


def send_line(text: str, token: str, to: str) -> None:
    resp = requests.post(
        LINE_PUSH_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": to, "messages": [{"type": "text", "text": text}]},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LINE 送信失敗 {resp.status_code}: {resp.text}")


def main() -> int:
    tz = ZoneInfo(env("TIMEZONE", "Asia/Tokyo"))
    calendar_ids = [c.strip() for c in env("CALENDAR_IDS", "primary").split(",") if c.strip()]
    leads = sorted({int(x) for x in env("REMIND_BEFORE_MINUTES", "30").replace(" ", "").split(",") if x})
    window = int(env("WINDOW_MINUTES", "60"))
    skip_all_day = env("SKIP_ALL_DAY", "true").lower() == "true"
    dry_run = env("DRY_RUN", "false").lower() == "true"
    line_token = env("LINE_CHANNEL_ACCESS_TOKEN", required=not dry_run)
    line_to = env("LINE_TO", required=not dry_run)

    if not leads:
        sys.exit("REMIND_BEFORE_MINUTES が空です")

    now = datetime.now(timezone.utc)
    time_min = now
    time_max = now + timedelta(minutes=max(leads) + window)

    access_token = get_access_token()
    state = prune_state(load_state(), now)
    sent = 0

    for cal in calendar_ids:
        try:
            events = fetch_events(access_token, cal, time_min, time_max)
        except requests.HTTPError as exc:
            print(f"カレンダー {cal} の取得に失敗: {exc}", file=sys.stderr)
            continue

        for event in events:
            if event.get("status") == "cancelled":
                continue
            start_utc, all_day = event_start_utc(event, tz)
            if start_utc is None:
                continue
            if all_day and skip_all_day:
                continue

            minutes_until = (start_utc - now).total_seconds() / 60
            if minutes_until < -1:  # すでに始まった予定は対象外
                continue

            applicable = [lead for lead in leads if minutes_until <= lead]
            if not applicable:
                continue

            start_raw = event["start"].get("dateTime") or event["start"].get("date")
            keys = [f"{event['id']}:{start_raw}:{lead}" for lead in applicable]
            target_lead = applicable[0]  # 最も近いリード時間
            already_done = any(k in state for k in keys)

            if not already_done:
                message = format_message(event, tz, minutes_until)
                if dry_run:
                    print(f"[DRY_RUN] {cal}\n{message}\n")
                else:
                    try:
                        send_line(message, line_token, line_to)
                    except RuntimeError as exc:
                        print(exc, file=sys.stderr)
                        continue
                sent += 1
                print(f"通知: {event.get('summary')} (リード{target_lead}分 / 実際 約{round(minutes_until)}分前)")

            # 通過したリード時間はすべて処理済みにして、後追いの重複通知を防ぐ
            for k in keys:
                state.setdefault(k, now.isoformat().replace("+00:00", "Z"))

    save_state(state)
    print(f"完了: {sent} 件通知 / 対象カレンダー {len(calendar_ids)} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
