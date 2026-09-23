# TODO / 実装状況

仕様書の Phase 構成に沿った進捗です。

## 現在存在する機能（実装済み）

### Phase 1 — Bot基本 + Mock決済（完了）
- [x] プロジェクト構成 / 設定(pydantic-settings) / .env.example / .gitignore
- [x] DB (SQLAlchemy 2.x async, SQLite→PostgreSQL 差し替え可)
  - users / products / inventory / orders / payments
- [x] 商品サービス・在庫サービス（複数行一括追加、状態別カウント）
- [x] 注文サービス（ORD-XXXXXX 生成、10分TTL、二重押下は同一注文再利用）
- [x] `PaymentProvider` 抽象 + `MockPaymentProvider`
- [x] 購入フロー（商品選択→注文→リンク入力→金額照合→受取→自動配布）
- [x] 在庫の原子的確保（同時購入で二重確保しない）
- [x] 管理者機能（/admin, 商品/在庫/注文コマンド）

### Phase 2 — PayPayログイン/セッション（構造完成・実接続は一部UNKNOWN）
- [x] `PayPayClient`（httpx async, 秘密情報を保持/ログ/例外に出さない）
- [x] `/login` FSM（WAITING_CREDENTIALS → WAITING_OTP）、Private Chat限定、管理者限定
- [x] 認証情報メッセージの即時削除、DB/ログ/例外へ非出力
- [x] `PayPaySessionStore`（Fernet暗号化・平文保存なし）
- [x] 起動時セッション復元 + 期限切れ時 refresh 試行
- [x] `/logout`, `/paypay_status`（トークン本体は非表示）
- [~] 新規ログインの2FA完了は PayPay の anti-bot により **UNKNOWN**
      （`adopt_token` によるアクセストークン投入で運用可能）→ TODO_PAYPAY.md

### Phase 3 — PayPayリンク確認/受取（構造完成・実接続は LIKELY）
- [x] `inspect_payment`（getP2PLinkInfo）→ `PaymentInfo` 正規化
- [x] 金額完全一致チェック、受取可能判定
- [x] `accept_payment`（acceptP2PSendMoneyLink）
- [x] 受取後 `get_payment_status` で最終状態を**再確認**してから PAID
- [~] 実レスポンス実測は未（LIKELY）→ docs/paypay-api.md

### Phase 4 — 異常系 / 復旧 / 冪等性（完了）
- [x] `PAYMENT_UNKNOWN`（timeout/通信断で FAILED にしない）
- [x] リンク二重利用防止（orders/payments に UNIQUE + 事前チェック）
- [x] 二重決済/二重受取/二重配布の防止（注文ごとのlock + DB状態ガード）
- [x] 商品送信失敗時も在庫を消失させず DELIVERING で保持 → /retry_delivery
- [x] Bot再起動時に未配布(PAID/DELIVERING)注文を検出し再配布
- [x] 秘密情報のログ・raw_response redaction

## 不足 / 今後（PayPay実接続）
- [ ] 実ログイン2FA完了（TODO_PAYPAY.md #1）
- [ ] token_refresh / link系レスポンスの実測確定（TODO_PAYPAY.md #2,#4）
- [ ] account_id 取得（#3）
- [ ] `alive()` の定期実行スケジューリング（Bot検知緩和）

## テスト（pytest, 36件パス）
- 正常購入 / 金額不足 / 金額超過 / 無効リンク / 使用済みリンク
- 注文期限切れ / 在庫切れ / 二重送信 / accept timeout / accept後 timeout
- 受取成功 / 配布 / 送信失敗→再配布 / 起動復旧
- ログイン(セッション保存/復元/暗号化) / redaction / crypto
