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
> PayPay の新規ログインは現在サーバー側の Bot 検知により未接続の部分があります
> （`TODO_PAYPAY.md` 参照）。**そのため既定は `mock` プロバイダで全フローが動作**し、
> 実PayPayはアクセストークン投入または実装追補で有効化します。

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

> 現在 PayPay の新規ログイン2FAは anti-bot により未接続です（`TODO_PAYPAY.md`）。
> 実運用ではアクセストークン投入経路（`PayPayService.adopt_token`）を使ってください。

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

1. `.env` で `PAYMENT_PROVIDER=paypay`
2. 有効な PayPay セッションを用意（下記いずれか）
   - **推奨（当面）**: 取得済みアクセストークンを `PayPayService.adopt_token()` で投入
   - `/login` フロー: 2FA 完了部分は `TODO_PAYPAY.md` の追補実装が必要
3. 実行環境は **日本国内IP**（国外VMは PayPay 側 403。プロキシ利用）
4. 実レスポンスを取得したら `tools/analyze_har.py` 等で解析し
   `docs/paypay-api.md` を更新

安全設計:
- リンク金額は**完全一致**のみ受取
- 受取APIの成功だけで商品を渡さず、`get_payment_status` で**最終状態を再確認**
- 通信断は `FAILED` にせず **`PAYMENT_UNKNOWN`**（二重受取事故を防止）
- 同一リンクは UNIQUE 制約で**一度きり**
- 送信失敗時も在庫を失わず `DELIVERING` で保持 → `/retry_delivery`・起動時再配布

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
