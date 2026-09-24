# TODO: PayPay 実API 未実装・要確認事項

Bot本体（購入→金額照合→受取→配布）は `MockPaymentProvider` で**完全に動作**します。

## 2026-09-24 実口座での確認結果

実アカウントで **購入→受取→配布まで通しで成功**しました（1円で2件）。

- ✅ `/login`（電話+パスワード → OTL 2FA）… 成功
- ✅ セッションの暗号化保存と起動時復元 … 成功
- ✅ `getP2PLinkInfo` … `PENDING` / 金額 / 各IDを正しく取得
- ✅ `acceptP2PSendMoneyLink` … 成功（受取後の再確認込み）
- ✅ 金額不一致の拒否 … 2円のリンクを送って実際に弾かれることを確認
- ❌ **アカウント制限**: 別アカウントでは `S9999 / 現在ご利用を制限しています`
  で受取を拒否された。コードではなくアカウント側の問題。同じ操作でも
  制限のないアカウントなら通る。

残るのは下記の未確認項目です。`src/paypay/` 以外は変更不要。

## 1. 新規ログイン（電話番号+パスワード → 2FA） — **実装済み / 要実口座確認**

- 実装: `src/paypay/auth.py`
  - `get_waf_token()`: Playwright(ヘッドレスChromium)で `aws-waf-token` を取得（anti-bot突破）
  - `login_step1()`: PAR → authorize → password（既知デバイスはOTP不要でcode取得）
  - `login_step2()`: OTL verify → COMPLETE_OTL/polling → `/bff/v2/oauth2/token` でトークン交換
- `PayPayClient.begin_login()/submit_otp()` が上記を `asyncio.to_thread` で呼ぶ。
  `/login`（電話→OTL）と `/login_token`（トークン直接投入）の両方が使用可能。
- 前提: `playwright install chromium` / 日本国内IP。
- [x] 実口座で `/login` を通しで確認（OTL 2FA 経路）
- [ ] 既知デバイスで OTP 不要になる分岐の確認（デバイスUUID固定後）
- [ ] `login_step2` の verify/COMPLETE_OTL ペイロード分岐を実レスポンスで確定
- [ ] （任意）account_id をログイン後 `getProfileDisplayInfo` から取得して表示

## 2. token_refresh — **実装済み / 要実測**
- 実装: `/bff/v2/oauth2/refresh`（`grantType=refresh_token`）。`src/paypay/auth.py`
- [ ] `token_expires_at` を実レスポンスの値に置換（現在は90日固定の推定値）

## 3. account_id / プロフィール — **未取得**
- [ ] `/bff/v2/getProfileDisplayInfo` から `account_id` を取得して
      `PayPaySession.account_id` に格納（`/paypay_status` 表示用）

## 4. link_check / link_receive — **実装済み / 要実測**
- 実装済み（実働実装ベースのフィールド: amount=`pendingP2PInfo.amount`,
  orderId/requestId=`message.data.*`, messageId/chatRoomId=`message.*`,
  orderStatus=`payload.orderStatus`）。受取後は `get_payment_status` で再確認。
- [x] 実リンクで `_parse_link_info` を確定（修正不要だった）
- [ ] パスコード付きリンクの取り扱い（現在は購入フローでは非対応前提）

## 5. 請求リンク方式（`PAYMENT_FLOW=request`） — **実装済み / 未検証**
- 実装: `client.create_request_link()` (`/bff/v1/createP2PCode`),
  `client.payment_history()` (`/bff/v3/getPaymentHistory`),
  `payment_service.confirm_by_transaction()`
- 既定は `PAYMENT_FLOW=link`（従来の送金リンク受取）。
- [ ] `createP2PCode` が実口座で通るか未確認
- [ ] 取引履歴の `orderId` が購入者の見る「取引番号」と一致するか未確認
      （違えば `_parse_transaction` を実レスポンスに合わせる）

## 6. 運用上の注意（実API接続時）
- [ ] 日本国内IP / プロキシ（国外VMは403）
- [ ] `alive()` を定期実行してBot検知を緩和（未スケジュール、要cron/loop）
- [ ] ログイン3回失敗でアカウント一時ロックの可能性
- [ ] セッション作りすぎない（凍結リスク）

> 実装を確定できた項目は `docs/paypay-api.md` の STATUS を CONFIRMED に更新してください。
