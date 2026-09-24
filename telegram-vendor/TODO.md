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
- [x] `/restock <product_id>`（1行1在庫の一括追加。/stock_add と同義）
- [x] 商品ごとの注意事項 `/product_note`（配布時に商品と一緒に購入者へ送信）

### Phase 2 — PayPayログイン/セッション（実口座で確認済み 2026-09-24）
- [x] `PayPayClient`（httpx async, 秘密情報を保持/ログ/例外に出さない）
- [x] `/login` FSM（WAITING_CREDENTIALS → WAITING_OTP）、Private Chat限定、管理者限定
- [x] 認証情報メッセージの即時削除、DB/ログ/例外へ非出力
- [x] `PayPaySessionStore`（Fernet暗号化・平文保存なし）
- [x] 起動時セッション復元 + 期限切れ時 refresh 試行
- [x] `/logout`, `/paypay_status`（トークン本体は非表示）
- [x] `/login_token`（アクセストークン直接投入の代替経路）
- [x] **新規ログイン実装済み**: anti-bot(AWS WAF)を headless Chromium で突破 →
      PAR/password/OTL 2FA/token交換（`src/paypay/auth.py`）
- [x] 実口座での通し確認（2026-09-24、OTL 2FA 経路で成功）

### Phase 3 — PayPayリンク確認/受取（実口座で確認済み 2026-09-24）
- [x] `inspect_payment`（getP2PLinkInfo）→ `PaymentInfo` 正規化（実働実装フィールド）
- [x] 金額完全一致チェック、受取可能判定
- [x] `accept_payment`（acceptP2PSendMoneyLink）
- [x] 受取後 `get_payment_status` で最終状態を**再確認**してから PAID
- [x] 実リンクで `_parse_link_info` 確定（2026-09-24）

### Phase 4 — 異常系 / 復旧 / 冪等性（完了）
- [x] `PAYMENT_UNKNOWN`（timeout/通信断で FAILED にしない）
- [x] リンク二重利用防止（orders/payments に UNIQUE + 事前チェック）
- [x] 二重決済/二重受取/二重配布の防止（注文ごとのlock + DB状態ガード）
- [x] 商品送信失敗時も在庫を消失させず DELIVERING で保持 → /retry_delivery
- [x] Bot再起動時に未配布(PAID/DELIVERING)注文を検出し再配布
- [x] 秘密情報のログ・raw_response redaction

### Phase 5 — 販売UI / 運用性（2026-09-24 追加）
- [x] 個数ごとの合計金額で価格設定（`PAYMENT_FLOW` とは別の価格表）
- [x] まとめ買いボタンの表示切替（商品ごと）
- [x] 商品説明を後から編集（管理パネル / `/product_desc`、複数行対応）
- [x] 1メッセージ方式（画面を編集し続け、購入者の発言も削除）
- [x] 商品をタップでコピーできる形式で配布
- [x] 管理者への問い合わせボタン（`SUPPORT_CONTACT`）
- [x] `/start` で未払い注文をキャンセルして完全リセット
- [x] 未処理メッセージへのフォールバック応答 + ハンドラ例外の捕捉
- [x] デバイスUUIDをアカウントごとに固定（電話番号は保存しない）
- [x] ログイン全体のタイムアウト（240秒 / OTL 120秒）と進捗表示
- [x] PayPayの拒否理由（`displayErrorResponse`）を管理者へ通知

## 不足 / 今後
- [ ] **FSM状態がメモリ保持**。再起動で入力途中の操作が失われる
      （フォールバック応答は入れたが、状態自体は復元されない）
- [ ] 請求リンク方式の実口座確認（TODO_PAYPAY.md #5）
- [ ] token_refresh の実測確定（#2）／ account_id 取得（#3）
- [ ] `alive()` の定期実行スケジューリング（Bot検知緩和）
- [ ] 常時起動先の決定（自宅常設機 / 国内VPS）。日本IP必須、
      ログイン時のみ Chromium が必要（約1GB）

## テスト（pytest, 102件パス）
- 正常購入 / 金額不足 / 金額超過 / 無効リンク / 使用済みリンク
- 注文期限切れ / 在庫切れ / 二重送信 / accept timeout / accept後 timeout
- 受取成功 / 配布 / 送信失敗→再配布 / 起動復旧
- ログイン(セッション保存/復元/暗号化) / redaction / crypto
- 個数ごとの価格 / 按分 / 旧データ互換 / まとめ買いボタン切替
- リンク・OTL抽出（SMS本文からの取り出し） / 取引番号による入金確認
- PayPayの拒否シート伝搬 / 配布のコピー形式 / /start の完全リセット
