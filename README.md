# gcal-line-reminder

Google カレンダーの予定を、開始前に LINE へ push 通知するツール。
GitHub Actions の cron で定期実行する。サーバー不要・無料枠内で動く。

- カレンダー取得: Google Calendar API (OAuth / リフレッシュトークン)
- 通知: LINE Messaging API の push メッセージ
- 実行: GitHub Actions (`.github/workflows/reminder.yml`, 15 分ごと)
- 重複防止: `state/notified.json` をワークフローがコミットして永続化

---

## 全体の流れ

1. Google 側で OAuth クライアントを作り、リフレッシュトークンを取得
2. LINE 側で Messaging API チャネルを作り、アクセストークンと送信先 userId を取得
3. GitHub リポジトリの Secrets に 5 つの値を登録
4. リポジトリを push すると、以後 15 分ごとに自動実行

---

## 1. Google の設定

### 1-1. プロジェクトと API

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成（既存でも可）
2. 「API とサービス」→「ライブラリ」で **Google Calendar API** を有効化

### 1-2. OAuth 同意画面

1. 「API とサービス」→「OAuth 同意画面」
2. User Type は **External** を選択
3. アプリ名・サポートメール・デベロッパー連絡先を入力
4. スコープ追加で `.../auth/calendar.readonly` を追加
5. **公開ステータスを「本番環境」にする**
   （「テスト」のままだとリフレッシュトークンが 7 日で失効する。
   個人利用なら未審査でも本番公開でき、自分のアカウントでは問題なく使える）

### 1-3. OAuth クライアント ID

1. 「API とサービス」→「認証情報」→「認証情報を作成」→「OAuth クライアント ID」
2. アプリケーションの種類: **デスクトップアプリ**
3. 作成後に表示される **クライアント ID** と **クライアントシークレット** を控える

### 1-4. リフレッシュトークンの取得（ローカルで一度だけ）

```bash
cd gcal-line-reminder
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

GOOGLE_CLIENT_ID=さっきのID \
GOOGLE_CLIENT_SECRET=さっきのシークレット \
python scripts/get_refresh_token.py
```

ブラウザが開くので、通知に使いたい Google アカウントで許可する。
（未審査アプリの警告が出たら「詳細」→「（アプリ名）に移動」で進む）
ターミナルに出る `GOOGLE_REFRESH_TOKEN = ...` を控える。

---

## 2. LINE の設定

1. [LINE Developers Console](https://developers.line.biz/console/) にログイン
2. プロバイダーを作成 → 「新規チャネル作成」で **Messaging API** チャネルを作成
3. 「Messaging API」タブ:
   - **チャネルアクセストークン（長期）** を発行 → 控える（= `LINE_CHANNEL_ACCESS_TOKEN`）
   - **あなたのユーザー ID (Your user ID)** を控える（= `LINE_TO`）
4. 同じタブの QR コードから、その公式アカウントを **自分の LINE で友だち追加**
   （友だちでないと push が届かない）
5. 応答設定で「あいさつメッセージ」「応答メッセージ」はオフにしておくと静か

> グループに送りたい場合は `LINE_TO` にグループ ID を入れる（取得には Webhook が必要）。

---

## 3. GitHub の設定

1. このディレクトリを GitHub リポジトリとして push（**private 推奨**）
2. リポジトリの Settings → Secrets and variables → Actions → **New repository secret** で以下を登録

   | Secret 名 | 値 |
   |---|---|
   | `GOOGLE_CLIENT_ID` | 1-3 のクライアント ID |
   | `GOOGLE_CLIENT_SECRET` | 1-3 のクライアントシークレット |
   | `GOOGLE_REFRESH_TOKEN` | 1-4 で取得したトークン |
   | `LINE_CHANNEL_ACCESS_TOKEN` | 2-3 のアクセストークン |
   | `LINE_TO` | 2-3 の userId |

3. Actions タブで `Calendar LINE Reminder` を選び、**Run workflow**（`dry_run` にチェック）で動作確認
   → ログに通知内容が出れば OK。チェックを外して再実行すると実際に LINE が届く

---

## 動作の調整

`.github/workflows/reminder.yml` の `env:` を編集する。

| 変数 | 既定 | 説明 |
|---|---|---|
| `CALENDAR_IDS` | `primary` | 対象カレンダー。カンマ区切りで複数可（例: `primary,xxx@group.calendar.google.com`） |
| `REMIND_BEFORE_MINUTES` | `30` | 何分前に通知するか。`30,10` のように複数指定するとその各タイミングで通知 |
| `WINDOW_MINUTES` | `60` | 先読みの猶予。実行遅延に対する保険 |
| `TIMEZONE` | `Asia/Tokyo` | メッセージの日時表示に使う |
| `SKIP_ALL_DAY` | `true` | 終日予定を無視。`false` にすると前日など指定分前に通知 |

cron の間隔を変えるなら `schedule.cron` を編集（`*/10 * * * *` など）。

---

## 通知メッセージの例

```
⏰ 約30分後に予定
りおさんご飯
🕒 8/30(日) 19:30〜21:30
📍 渋谷
```

---

## 注意点

- **LINE 無料プラン**は push メッセージが月 200 通まで。通知タイミングを増やすと超えやすい。
- **GitHub の scheduled workflow** はリポジトリが 60 日間更新されないと自動停止する。
  本ツールは state をコミットし続けるので通常は止まらない。
- cron は必ずしも定刻に走らない（数分〜十数分遅延、まれにスキップ）。
  `WINDOW_MINUTES` と重複防止で取りこぼしをカバーしている。
- リフレッシュトークンを失効させた場合（パスワード変更、アクセス取り消しなど）は
  1-4 をやり直して Secret を更新する。

---

## ローカルでの手動実行

```bash
cp .env.example .env      # 値を埋める（DRY_RUN=true のままなら送信しない）
set -a; source .env; set +a
python src/reminder.py
```
