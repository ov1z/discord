# TODO: PayPay 実API 未実装・要確認事項

Bot本体（購入→金額照合→受取→配布）は `MockPaymentProvider` で**完全に動作**します。
以下は実PayPayへ接続する際に残っている作業です。`src/paypay/` 以外は変更不要です。

## 1. 新規ログイン（電話番号+パスワード → 2FA） — **未完 / UNKNOWN**

- 現状: `PayPayClient.begin_login()` は OAuth2 PAR までは投げるが、
  その後の sign-in と 2FA(OTL) 完了は **PayPay側のBot検知**により公開実装でも
  再現できないため、`submit_otp()` は `PayPayOTPRequired` を返して停止する。
- 影響: `/login` のフルフロー（電話番号→SMS/OTL→トークン取得）は未完。
- 参考: `PayPaython-mobile` READMEに「2025/11にBot検知追加、公開停止」明記。
  4桁SMS OTPは廃止され OTL方式に移行。
- 対応方針（いずれか）:
  1. **推奨・当面の運用**: 別途取得済みの `access_token` を投入する経路を使う。
     管理コマンド **`/login_token <access_token>|<refresh_token>|<device_uuid>`**
     を実装済み（個人チャット・管理者限定、メッセージ即削除、暗号化保存）。
     `PayPayService.adopt_token()` 経由でログイン作業なしに稼働できる（受取APIは動作見込み）。
  2. 実機トラフィックを `tools/analyze_har.py` で解析し、現行の sign-in /
     OTL / token 交換のヘッダ・ペイロード・anti-bot トークンを
     `src/paypay/auth.py` と `client.py` に実装する。

### 必要な追加実装（経路2を採る場合）
- [ ] `www.paypay.ne.jp` の sign-in ページ取得と anti-bot チャレンジ処理
- [ ] `/portal/api/v2/oauth2/sign-in/password` へのPOST（現行ヘッダ）
- [ ] OTL 2FA: `.../2fa/otl/verify` → `code-grant/update(COMPLETE_OTL)`
- [ ] `/bff/v2/oauth2/token` でトークン交換（`codeVerifier`）
- [ ] `submit_otp()` を上記に接続し `LoginStatus.SUCCESS` を返す

## 2. token_refresh — **要実測 / LIKELY**
- [ ] `/bff/v2/oauth2/token` の refresh ペイロード形状を実レスポンスで確認
- [ ] `token_expires_at` を実レスポンスの値に置換（現在は90日固定の推定値）

## 3. account_id / プロフィール — **未取得**
- [ ] `/bff/v2/getProfileDisplayInfo` から `account_id` を取得して
      `PayPaySession.account_id` に格納（`/paypay_status` 表示用）

## 4. link_check / link_receive — **要実測 / LIKELY**
- [ ] `getP2PLinkInfo` / `acceptP2PSendMoneyLink` の実レスポンスで
      `orderStatus` 値と受取後の最終状態を確認し、`_parse_link_info` を確定
- [ ] パスコード付きリンクの取り扱い（現在は購入フローでは非対応前提）

## 5. 運用上の注意（実API接続時）
- [ ] 日本国内IP / プロキシ（国外VMは403）
- [ ] `alive()` を定期実行してBot検知を緩和（未スケジュール、要cron/loop）
- [ ] ログイン3回失敗でアカウント一時ロックの可能性
- [ ] セッション作りすぎない（凍結リスク）

> 実装を確定できた項目は `docs/paypay-api.md` の STATUS を CONFIRMED に更新してください。
