# telegram-vendor

Telegram 上で動作する**デジタル商品の自動販売Bot**です。
購入者が PayPay の送金リンクを送ると、Bot が金額を照合して自動で受け取り、
在庫を確保して商品を自動配布します。

- Telegram: **aiogram 3.x**（async）
- HTTP: **httpx** AsyncClient
- DB: **SQLAlchemy 2.x async**（SQLite → PostgreSQL 差し替え可）
- 設定: **pydantic-settings** / 秘密情報は `.env`
- 決済は `PaymentProvider` 抽象で分離。`mock` と `paypay` を切替可能

> **重要**: 本Botは *管理者本人が所有する PayPay アカウント* での利用を前提とします。

---

## ⚠️ 現在の状態（「実APIですぐ動く？」への正直な回答）

「実APIを探せばすぐ動く」わけでは **ありません**。部分ごとに状態が違います。

| 部分 | 実装 | 実APIを入れたら |
|------|------|-----------------|
| **リンク確認 / 自動受取 / token refresh / alive** | 実装済み(確度: LIKELY) | **有効なアクセストークンがあればほぼ動く見込み**。実レスポンスで数フィールドの微調整が要る可能性 |
| **新規ログイン（電話+パスワード→SMS/OTL 2FA）** | 未接続(確度: UNKNOWN) | **すぐには動かない**。PayPay の anti-bot 突破の解析・実装が必要（`TODO_PAYPAY.md` #1） |

要するに:

- **「受け取り側」はトークンさえ入れればすぐ動く設計**。
  → `PAYMENT_PROVIDER=paypay` にして `/login_token <access_token>` でトークンを投入すれば、
    購入→金額照合→自動受取→配布まで実PayPayで動作する見込み。
- **「ログイン側（電話番号+パスワードでの新規ログイン）」は未完**。
  → PayPay が 2025/11 以降ログインに Bot 検知を追加し、公開実装(PayPaython-mobile等)も
    停止中。4桁SMS OTP も廃止され OTL(ワンタイムリンク)方式に移行済み。
  → 完成させるには実機トラフィック解析が必要（手順は後述「実PayPay接続」）。

**既定は `PAYMENT_PROVIDER=mock`** で、ネットワークなしに全フローが動作します（テスト36件パス）。

---

## アーキテクチャ

```
Telegram handler  →  service  →  PaymentProvider  →  PayPayClient
   (bot/)            (services/)   (payments/)         (paypay/)
```

- ハンドラは PayPay API を直接叩きません。
- PayPay 仕様変更時は **`src/paypay/` だけ**直せば本体は動きます。

```
src/
  main.py            起動・DI・起動時復旧
  config.py          設定
  bot/               handlers / keyboards / states / container
  paypay/            client / auth / models / exceptions / session_store
  payments/          base(抽象) / paypay / mock
  services/          order / payment / inventory / product / paypay
  database/          engine / models / repository
  security/          crypto(Fernet) / redaction
tests/               pytest（36件）
docs/paypay-api.md   API調査(確度付き)
tools/               HAR/JSON/ログ解析
```

---

## セットアップ

### 1. Python 環境（3.11 以上）

```bash
cd telegram-vendor
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Telegram Bot 作成（BotFather）

1. Telegram で **@BotFather** に `/newbot`
2. 名前とユーザー名を設定 → **`TELEGRAM_BOT_TOKEN`** が発行される
3. 自分の数値 user ID を **@userinfobot** 等で確認 → **`ADMIN_TELEGRAM_ID`**

### 3. `.env` 作成

```bash
cp .env.example .env
```

`SESSION_ENCRYPTION_KEY` を生成して貼り付け:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`.env` の主な項目:

| 変数 | 説明 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | BotFather のトークン |
| `ADMIN_TELEGRAM_ID` | 管理者の数値 user ID |
| `DATABASE_URL` | 既定 `sqlite+aiosqlite:///shop.db` |
| `SESSION_ENCRYPTION_KEY` | PayPayセッション暗号化キー(Fernet) |
| `PAYPAY_SESSION_PATH` | 暗号化セッションの保存先 |
| `PAYMENT_PROVIDER` | `mock`（既定）/ `paypay` |
| `ORDER_TTL_SECONDS` | 注文有効時間（既定600=10分） |

### 4. DB 初期化

起動時に自動でテーブルを作成します（`create_all`）。手動確認したい場合:

```bash
PYTHONPATH=src python -c "import asyncio,database.engine as e; asyncio.run(e.create_all())"
```

### 5. Bot 起動

```bash
PYTHONPATH=src python src/main.py
```

---

## 使い方

### 一般ユーザー
- `/start` … 商品一覧を表示、`[購入する]` で注文開始
- 案内に従い、指定金額の **PayPay 送金リンク**をチャットに送信
- 金額が一致すれば Bot が自動受取 → 商品を自動配布

### 管理者（`ADMIN_TELEGRAM_ID` と一致する場合のみ）
- `/admin` … メニュー（商品/在庫/注文/PayPay状態/ログイン/ログアウト）

商品管理:
```
/product_add 商品A|500|説明        # 追加
/product_list                      # 一覧
/product_edit 1|price|800          # 編集(field=name|price|description|active)
/product_delete 1                  # 無効化
```

在庫管理（複数行で一括追加）:
```
/stock_add 1
AAAA-BBBB-CCCC
DDDD-EEEE-FFFF
GGGG-HHHH-IIII
/stock_count 1
/stock_list 1
```

注文管理:
```
/orders                      # 直近の注文
/order ORD-XXXXXX            # 詳細
/retry_delivery ORD-XXXXXX   # 配布再試行（送信失敗/在庫追加後）
/cancel_order ORD-XXXXXX     # キャンセル
```

### PayPay `/login`（管理者・個人チャット限定）
```
/login
```
- 既にログイン済みなら「ログイン済み」と安全な情報のみ表示（トークン本体は非表示）
- 未ログインなら `電話番号:パスワード` の入力を要求（例: `090xxxxxxxx:password`）
- 認証情報メッセージは処理後すぐ削除、DB/ログ/例外に一切残しません
- SMS/ワンタイム認証が必要な場合は続けてコードを入力
- `/logout` でメモリ・保存セッションを削除
- `/paypay_status` で状態確認

アクセストークン投入（新規ログインの代替・当面の推奨）:
```
/login_token <access_token>|<refresh_token(任意)>|<device_uuid(任意)>
```
- 個人チャット・管理者限定。メッセージは即削除、トークンは暗号化保存
- 次回起動時に自動復元されます

> 現在 PayPay の新規ログイン2FA（`/login` の最後）は anti-bot により未接続です
> （`TODO_PAYPAY.md` #1）。実運用では上記 `/login_token` を使ってください。

---

## Mock 決済でのテスト

`.env` の `PAYMENT_PROVIDER=mock`（既定）で、ネットワークなしに全フローを試せます。

Mock のリンク形式:
```
https://example.local/pay/<金額>/<任意ID>
例: https://example.local/pay/500/TEST001   → 金額500円・受取可能
```

手順:
1. `/product_add テスト商品|500|test`
2. `/stock_add <product_id>` に続けて在庫を複数行
3. `/start` → 購入 → 上記 Mock リンクを送信
4. 金額一致で受取成功 → 商品が届く

自動テスト:
```bash
python -m pytest -q          # 36 tests
```

---

## 実 PayPay 接続方法

### 経路A: アクセストークン投入で「受け取り」を動かす（最短・推奨）

新規ログインの実装なしで、受け取りフローを実PayPayで動かせます。

1. `.env` で `PAYMENT_PROVIDER=paypay`
2. 有効な **access_token** を用意する（下記「トークンの入手」）
3. Bot を起動し、管理者の**個人チャット**で:
   ```
   /login_token <access_token>|<refresh_token(任意)>|<device_uuid(任意)>
   ```
   - メッセージは即削除され、トークンは **Fernet 暗号化**で保存されます
   - 次回以降は起動時に自動復元（`/paypay_status` で確認）
4. `/paypay_status` が「ログイン済み / PaymentProvider: 利用可能」になれば準備完了
5. 実行環境は **日本国内IP**（国外VMは PayPay 側 CloudFront 403。日本のプロキシ必須）

> これで購入→金額照合→自動受取→配布まで実PayPayで動作する見込みです。
> ただし `getP2PLinkInfo` / `acceptP2PSendMoneyLink` の実レスポンスは未実測(LIKELY)なので、
> 最初の1件は少額でテストし、レスポンス差異があれば下記「解析」で `src/paypay/client.py`
> の `_parse_link_info` を調整してください。

#### トークンの入手（access_token）
アクセストークンは約90日有効です。入手方法は環境により異なります:
- 既存の PayPay 非公式ツール／自分で取得した OAuth トークンを流用
- 実機トラフィックを解析して `/bff/v2/oauth2/token` のレスポンスから取得
  （下記「経路B」の解析手順と同じ）

### 経路B: 新規ログイン（/login フロー）を完成させる

`電話番号:パスワード → SMS/OTL` の完全自動ログインを実装する場合。**要リバースエンジニアリング**。

作業対象は `src/paypay/` のみ（Bot本体は変更不要）:

1. 実機（Android/iOS）＋ mitmproxy + Frida(SSL unpin) で PayPay アプリの
   ログイン通信をキャプチャ → `capture.har` を保存
2. 解析:
   ```bash
   python tools/analyze_har.py capture.har --host paypay.ne.jp   # 各リクエストの構造
   python tools/analyze_json.py response.json --schema           # レスポンス型
   ```
   （出力は秘密情報が自動 redact されます）
3. `src/paypay/auth.py` / `src/paypay/client.py` を実装:
   - `begin_login()`: PAR → sign-in ページ → password POST → 2FA(OTL)開始
   - `submit_otp()`: OTL verify → `code-grant/update(COMPLETE_OTL)` →
     `/bff/v2/oauth2/token` でトークン交換 → `PayPaySession` を返す
   - anti-bot チャレンジのトークン生成をここに実装
4. `docs/paypay-api.md` の該当項目を **UNKNOWN → CONFIRMED** に更新
5. `PayPayClient` は httpx なので、テストは `httpx.MockTransport` を
   `PayPayClient(transport=...)` に渡せばネットワークなしで書けます

> 詳細な残タスクは **`TODO_PAYPAY.md`** に列挙してあります（#1〜#5）。

### 安全設計（実API接続時に効く保護）
- リンク金額は**完全一致**のみ受取（過不足はどちらも拒否）
- 受取APIの成功だけで商品を渡さず、`get_payment_status` で**最終状態を再確認**
- 通信断/timeout は `FAILED` にせず **`PAYMENT_UNKNOWN`**（二重受取事故を防止）
- 同一リンクは UNIQUE 制約で**一度きり**
- 送信失敗時も在庫を失わず `DELIVERING` で保持 → `/retry_delivery`・起動時再配布
- Bot 検知緩和のため `PayPayClient.alive()` を用意（定期実行のスケジューリングは未設定=要追加）

---

## PC の Claude Code へ移行して続きを作る

このリポジトリはそのまま PC に持っていけます。

### 1. 取得
```bash
git clone <このリポジトリのURL>
cd discord/telegram-vendor
git checkout claude/telegram-paypay-vending-bot-1k18zf
```

### 2. 環境
```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# cryptography が _cffi_backend で失敗する環境では:
pip install cffi
```

### 3. 設定
```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 出力を .env の SESSION_ENCRYPTION_KEY に貼る
# TELEGRAM_BOT_TOKEN と ADMIN_TELEGRAM_ID も設定
```

### 4. 動作確認
```bash
python -m pytest -q                  # 36 tests
PYTHONPATH=src python src/main.py    # 起動（既定は mock プロバイダ）
```

### 5. Claude Code への依頼例（続きの実装）
PC の Claude Code に、次のように頼めばそのまま続行できます:
- 「`TODO_PAYPAY.md` の #1（新規ログイン2FA）を実装して。作業は `src/paypay/` だけ。
  実機の `capture.har` を置くので `tools/analyze_har.py` で解析してから実装して」
- 「実PayPayの `getP2PLinkInfo` レスポンス例（`response.json`）を渡すので、
  `src/paypay/client.py` の `_parse_link_info` を実レスポンスに合わせて。
  `docs/paypay-api.md` も CONFIRMED に更新して」
- 「`alive()` を定期実行するスケジューラを追加して（Bot検知緩和）」

### 参照すべきファイル（続きの起点）
- `TODO.md` … 全体の実装状況
- `TODO_PAYPAY.md` … PayPay実接続の残タスク（#1〜#5）
- `docs/paypay-api.md` … 判明済みAPI仕様（確度付き）
- `src/paypay/` … ここだけ直せば実API対応が完結する層
- `tests/` … 挙動の仕様書も兼ねる（実装変更時の回帰確認に）

---

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `TELEGRAM_BOT_TOKEN is not set` | `.env` を作成・設定 |
| `SESSION_ENCRYPTION_KEY is empty` | Fernet キーを生成して設定 |
| `Failed to decrypt session` | 暗号化キーを変えた/破損。`/logout` 後に再ログイン |
| PayPay 403 (国外IP) | 日本国内IP または日本のプロキシを使用 |
| PayPay 新規ログインが進まない | anti-bot のため未接続。`TODO_PAYPAY.md` 参照、トークン投入で運用 |
| `_cffi_backend` エラー | `pip install cffi` を実行 |
| 入金済みだが未配布 | 在庫追加後 `/retry_delivery ORD-XXXX`、または再起動で自動再配布 |
| PostgreSQL へ移行 | `DATABASE_URL=postgresql+asyncpg://...` に変更（コード変更不要、`asyncpg` を追加） |

---

## セキュリティ方針

- PayPay パスワード・SMS OTP は**保存しない**
- 認証情報メッセージは処理後に削除
- PayPay ログインは**個人チャット限定・管理者限定**
- PayPay セッション/トークンは **Fernet 暗号化**で保存（平文なし）
- Telegram Bot Token は `.env`
- ログ・`payments.raw_response` は秘密情報を **redact** してから出力/保存
