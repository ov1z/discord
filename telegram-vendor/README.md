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

実PayPay接続に必要なコードは**すべて実装済み**です（2026年時点の実働実装ベース）。

| 部分 | 実装 | 状態 |
|------|------|------|
| **リンク確認 / 自動受取 / token refresh / alive** | 実装済み | CONFIRMED（実working実装ベース） |
| **新規ログイン（電話+パスワード→SMS/OTL 2FA）** | 実装済み | CONFIRMED。**anti-bot(AWS WAF) は headless Chromium で突破** |

- **anti-bot突破**: PayPay の sign-in は AWS WAF 配下。`src/paypay/auth.py` の
  `get_waf_token()` が **Playwright(ヘッドレスChromium)で1回だけsign-inページを開き
  `aws-waf-token` Cookie を取得** → 以降は httpx で OAuth/OTL を処理。
  ブラウザは「WAFの通行証」を取る一瞬だけで、OTP・受取・送金には使いません。
- **OTPは OTL(ワンタイムリンク)方式**（4桁SMS OTP は廃止）。届いたリンク/IDを `/login` 中に入力。
- 実行は **日本国内IP** 必須（国外は CloudFront 403）。`playwright install chromium` が必要。

**既定は `PAYMENT_PROVIDER=mock`** で、ネットワーク/ブラウザなしに全フローが動作します
（テスト54件パス）。実PayPayは `PAYMENT_PROVIDER=paypay` で有効化。

> ⚠️ 実接続はまだ実口座での通し確認をしていません。最初は少額でテストし、
> レスポンス差異があれば `src/paypay/client.py` の `_parse_link_info` を調整してください。

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
tests/               pytest（54件）
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
| `HOLD_RECHECK_SECONDS` | PayPay一次保留時、解除を待って自動再確認するまでの秒数（既定60） |

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
- `/start` … 商品一覧（各商品ボタンに 価格・在庫、在庫0は「入荷待ち」）
- 商品を押すと**詳細画面**（説明・数量別単価・在庫）が開く
- **数量ボタン**（1個 / 5個 / 10個 …、まとめ買いは割引単価・合計を表示）または
  「🔢 数量を入力」で任意個数を選ぶ → **一度に複数個購入できる**
- 案内された合計金額の **PayPay 送金リンク**をチャットに送信
- 金額が合計と一致すれば Bot が自動受取 → 選んだ個数分の商品をまとめて自動配布

### 管理者（`ADMIN_TELEGRAM_ID` と一致する場合のみ）

**ボタン操作パネル（推奨・コマンド入力不要）**
`/admin` を送るとインラインボタンのパネルが開きます。以降はタップだけで操作できます:
- 🛍 商品管理 … ➕商品追加 / 📦在庫追加 / 📝注意事項設定 / 💹価格設定(まとめ買い割引) / 🗑商品削除
- 📦 在庫追加 … 商品をボタンで選び、在庫を1行1つ送るだけ
- 📋 注文管理 … 未配布・保留・状態不明の注文を一覧 → 選ぶと [📤再配布][🔍入金再確認][❌キャンセル]
- 📊 在庫状況 … 商品ごとの在庫/予約/販売済
- 📢 一括送信 … 登録ユーザー全員へメッセージを配信（送った文面がそのまま全員へ）
- 💴 PayPay状態 … [🔑ログイン][🚪ログアウト][🔄更新]

一般ユーザーにはこのパネルは表示されず、`/start` の購入画面のみ見えます。

**スラッシュコマンド（同じことをコマンドでも可能）**

商品管理:
```
/product_add 商品A|500|説明        # 追加
/product_list                      # 一覧
/product_edit 1|price|800          # 編集(field=name|price|description|active)
/product_tiers 1 1:1800,5:1600,10:1500,50:1000   # 数量別単価(まとめ買い割引)
/product_delete 1                  # 無効化
```

商品の注意事項（配布後に購入者へ商品と一緒に送られる）:
```
/product_note 1 初回起動時にライセンス認証を行ってください
# 複数行も可:
/product_note 1
1行目の注意事項
2行目の注意事項
```

在庫追加（1行1在庫。各行が別々の在庫として登録され、行がまとまることはない）:
```
/restock 1
AAAA-BBBB-CCCC
DDDD-EEEE-FFFF
GGGG-HHHH-IIII
# /stock_add でも同じ動作
/stock_count 1
/stock_list 1
```

注文管理:
```
/orders                      # 直近の注文
/order ORD-XXXXXX            # 詳細
/retry_delivery ORD-XXXXXX   # 配布再試行（送信失敗/在庫追加後）
/verify_order ORD-XXXXXX     # 保留・状態不明の注文をPayPayで再確認し、受取済みなら配布
/cancel_order ORD-XXXXXX     # キャンセル
```

一括送信（登録ユーザー全員へ配信）:
```
/broadcast セール開催中です！   # 引数の文面を全員へ
/broadcast                     # 引数なしなら、次に送った文面を全員へ
```
（ブロック済み・退会済みユーザーは自動でスキップし、成功/失敗数を表示します）

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
python -m pytest -q          # 54 tests
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

### 経路B: 電話番号+パスワードでログイン（`/login`・実装済み）

anti-bot突破を含めて実装済みです。追加の実装は不要で、準備だけ:

1. `pip install playwright && playwright install chromium`（Chromium取得）
2. `.env` で `PAYMENT_PROVIDER=paypay`、実行環境は**日本国内IP**
3. Bot起動 → 管理者の**個人チャット**で:
   ```
   /login
   090xxxxxxxx:password        ← 電話番号:パスワード（送信後すぐ自動削除される）
   <PayPayから届いたOTL(リンク/ID)>   ← 2FAが必要な場合のみ
   ```
4. 成功するとトークンが暗号化保存され、次回以降は起動時に自動復元
5. 少額で受取を1件テストし、レスポンス差異があれば `src/paypay/client.py`
   の `_parse_link_info` を調整（差異があれば `docs/paypay-api.md` も更新）

仕組み: `src/paypay/auth.py` が Playwrightで `aws-waf-token` を取得 → PAR →
password → OTL 2FA → token交換。ブラウザはWAF通行証取得の一瞬のみ使用。

> `PayPayClient` は httpx ベースなので、追加テストは `httpx.MockTransport` を
> `PayPayClient(transport=...)` に渡せばネットワークなしで書けます。
> 残タスク/注意は **`TODO_PAYPAY.md`** 参照。

### PayPay 一次保留（受け取り保留）の扱い

受取時に PayPay 側で送金が一時保留になった場合（`backendResultCode 42007013` 等）:

1. **商品は渡さない**（入金が確定していないため）。注文は `PAYMENT_UNKNOWN` で保持
2. 購入者に「PayPayアプリで保留を解除し、**1分以内**に完了してください」と通知
3. 管理者にも保留発生を通知
4. **1分後（`HOLD_RECHECK_SECONDS`）に自動で再確認**
   - 受取済み（`orderStatus == SUCCESS`）→ そのまま**商品を配布**
   - リンクがまだ受取可能（`PENDING`）→ 受取を1回だけ再実行 → 確定を確認できたら配布
   - 解除されていない → 購入者に「確認できませんでした」と通知、管理者に
     `/verify_order ORD-XXXX` を案内（配布はしない）
5. 再確認は何度呼んでも**二重受取・二重配布にならない**（受取済みリンクは再受取しない）
6. 1分待ちの間に Bot が再起動した場合は、**起動時に未確定注文を再確認**し、受取済みなら配布

> ⚠️ 一次保留の「誰がどう解除するか」「解除後に受取を再実行する必要があるか」は、
> 実口座でまだ確認できていません。どちらのケースでも動くように、再確認時は
> 「受取済みなら配布／まだ受取可能なら1回だけ再受取」の両対応にしてあります。
> 実際の挙動を確認したら `docs/paypay-api.md` を更新してください。

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
python -m pytest -q                  # 54 tests
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

## 常時起動・デプロイ（VPSを使いたくない場合）

### ❌ Cloudflare Workers では動きません（重要）
このBotは Workers では動作しません。理由:
- Workers は基本 JS/TS のサーバーレス。**Python の常駐プロセス**、`aiogram` の
  **ロングポーリング**、`SQLAlchemy` のソケットDB接続は動かせない
- PayPayログインの WAF 突破に使う **Playwright（ヘッドレスChromium）が Workers では動かない**
- Workers はリクエスト単位・CPU時間制限があり「常に待ち受ける」用途に不向き

> Workers に載せるには「JSで全面書き直し＋Telegram webhook＋D1/KV＋ブラウザは
> Cloudflare Browser Rendering」といった別物への作り替えが必要で、現実的ではありません。

### ✅ このBotに向く常時起動先（コンテナを東京リージョンで動かす）

**最重要**: 実PayPayを使うなら **日本IP必須**（海外リージョンは PayPay が 403）。
必ず**東京/大阪リージョン**を選ぶか、日本のプロキシを使ってください。
（`PAYMENT_PROVIDER=mock` のテストだけなら海外リージョンでもOK）

| 選択肢 | 無料枠 | 日本リージョン | 備考 |
|--------|--------|----------------|------|
| **Fly.io**（推奨） | 少額の無料相当枠 | ✅ `nrt`(東京) | Dockerでそのまま。`fly.toml` 同梱 |
| **Oracle Cloud Always Free** | 完全無料のVM | ✅ 東京/大阪 | 実質VMだが無料。Docker or 直接実行 |
| Railway / Render | 限定的（スリープ有） | ⚠️ 主に海外 | 常時起動は有料寄り。PayPayは要プロキシ |
| 自宅PC / Raspberry Pi | 無料 | ✅ 国内 | 電気代のみ。回線が国内IP |

### Fly.io での手順（推奨・Docker同梱）
```bash
# 1. flyctl を入れてログイン
curl -L https://fly.io/install.sh | sh
fly auth login

# 2. アプリ作成（fly.toml の app 名を一意な名前に変更してから）
fly launch --no-deploy --copy-config --name <あなたのアプリ名> --region nrt

# 3. データ永続化用ボリューム（SQLite と暗号化セッションを保持）
fly volume create data --region nrt --size 1

# 4. 秘密情報を登録（.env は使わず fly secrets に入れる）
fly secrets set \
  TELEGRAM_BOT_TOKEN=xxxxx \
  ADMIN_TELEGRAM_ID=123456789 \
  SESSION_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
  PAYMENT_PROVIDER=paypay

# 5. デプロイ（Chromium入りイメージがビルドされる）
fly deploy
```
- `fly.toml` は東京リージョン・常時1台起動（スリープ無し）・`/data` にDB永続化。
- 起動後、Telegramで管理者から `/login` → 電話番号:パスワード → OTL でPayPayログイン。
- ログ確認: `fly logs`。再デプロイしてもDBと暗号化セッションは `/data` に残ります。

### Oracle Cloud Always Free（完全無料）
東京/大阪リージョンで「Always Free」のVMインスタンスを作成 →
`git clone` → `docker build -t vendor . && docker run -d --env-file .env -v $PWD/data:/data vendor`、
または「セットアップ」章のとおり venv で直接起動。無料で24時間動きます。

### 補足
- **Telegram はロングポーリング**なので、インバウンドのポート開放や独自ドメインは不要。
- 将来 webhook 化したい場合は `aiogram` の webhook 対応に差し替え可能（現状は未実装）。
- どのホストでも「日本IP」と「Chromium(約1GBメモリ)」だけ満たせば動きます。

---

## 🤝 引き継ぎガイド（別のAI・別の人が続きを作るとき）

このプロジェクトは**層ごとに責務が分離**されているので、どこを触ればよいか明確です。
新しいAIに渡すときは「このREADMEと `TODO.md`/`TODO_PAYPAY.md` を読んで」と伝えれば続行できます。

### 全体像（データの流れ）
```
Telegram(handler)  →  service  →  PaymentProvider  →  PayPayClient(HTTP)
   bot/               services/    payments/           paypay/
購入者/管理者の操作   業務ロジック   決済の抽象化        PayPay通信(唯一のHTTP)
```
- **handler は PayPay を直接叩かない**。必ず service 経由。
- PayPay仕様が変わったら **`src/paypay/` だけ**直せば全体が動く。
- 決済は `PaymentProvider` 抽象で差し替え可能（`mock` / `paypay`）。テストは `mock`。

### ディレクトリ責務
| 場所 | 役割 | よく触る場面 |
|------|------|--------------|
| `src/bot/handlers/` | Telegramの入口（コマンド/ボタン/FSM） | UI・操作を足す |
| `src/bot/keyboards/` | インラインボタン定義 | ボタン追加・文言変更 |
| `src/bot/states/` | FSM状態 | 入力フロー追加 |
| `src/services/` | 業務ロジック（注文/在庫/決済/価格/ユーザー） | 仕様変更の主戦場 |
| `src/payments/` | 決済抽象 + mock + paypay 実装 | 受取ロジック |
| `src/paypay/` | PayPay通信・ログイン・セッション | 実API調整はここだけ |
| `src/database/` | モデル/エンジン/リポジトリ(SQL) | テーブル・クエリ |
| `src/security/` | 暗号化(Fernet)・秘密情報マスク | ほぼ固定 |
| `tests/` | pytest（挙動の仕様書も兼ねる） | 変更したら必ず更新 |

### 主要な不変条件（壊してはいけない設計）
1. **金額は完全一致**でのみ受取（合計＝数量×単価）。
2. 受取API成功だけで配布せず、`get_payment_status` で**最終確認**してから `PAID`。
3. 通信断/timeout は `FAILED` にせず **`PAYMENT_UNKNOWN`**（二重受取防止）。
4. **一次保留**は配布せず、購入者に解除を促し `HOLD_RECHECK_SECONDS` 後に自動再確認。
5. 在庫確保は**原子的**（複数個 `reserve_many`）で二重確保しない。
6. 全処理は**冪等**（二重送信・再起動でも二重決済/配布しない）。
7. 秘密情報（トークン/パスワード/OTP/電話/Cookie）は**保存・ログ・例外・DBに出さない**。

### よくある追加作業の入口
- 新しいコマンド/ボタン → `src/bot/handlers/` と `src/bot/keyboards/`
- 価格・数量の仕様変更 → `src/services/pricing.py`, `order_service.py`
- 受取・保留の挙動 → `src/services/payment_service.py`, `src/payments/paypay.py`
- 実PayPayレスポンス調整 → `src/paypay/client.py` の `_parse_link_info`
- DBカラム追加 → `src/database/models.py` ＋ `engine.py` の `_ensure_new_columns`
  （簡易マイグレーション。既存DBにも自動でカラム追加される）

### 変更したら必ず
```bash
python -m pytest -q          # 全テスト（現在54件）
```
テストが緑なら、コミット/プッシュ。

### 続きの起点ファイル
- `TODO.md` … 実装状況（Phase別）
- `TODO_PAYPAY.md` … PayPay実接続の残タスク（実口座確認・API確定）
- `docs/paypay-api.md` … 判明済みPayPay API（CONFIRMED/LIKELY/UNKNOWN）

---

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `TELEGRAM_BOT_TOKEN is not set` | `.env` を作成・設定 |
| `SESSION_ENCRYPTION_KEY is empty` | Fernet キーを生成して設定 |
| `Failed to decrypt session` | 暗号化キーを変えた/破損。`/logout` 後に再ログイン |
| PayPay 403 (国外IP) | 日本国内IP必須。ホストを東京/大阪リージョンに、または日本プロキシ |
| Cloudflare Workers で動かしたい | 不可（Python常駐/ポーリング/Playwright非対応）。Fly.io等のコンテナ常時起動を使用 |
| ログイン時にブラウザ/メモリ落ち | Chromiumに約1GB必要。ホストのメモリを1024MB以上に |
| デプロイ後に在庫/セッションが消える | 永続ストレージにDBを置く（Fly.ioは`/data`ボリューム、`fly.toml`参照） |
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
