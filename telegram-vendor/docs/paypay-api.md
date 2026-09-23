# PayPay 非公式API メモ

このドキュメントは、本Botが利用する PayPay モバイル非公式APIの調査結果です。
各項目には確度を付けています。

- **CONFIRMED**: 実トラフィック/複数実装で一致確認済み
- **LIKELY**: 公開実装（PayPaython-mobile 等）に記載があり整合的だが、当環境で実測未確認
- **UNKNOWN**: 仕様不明、または現在サーバー側の保護で再現できない

> 出典の一つである `PayPaython-mobile` は 2025/11 以降ログイン部分にBot検知が入り、
> 公開コードのログインは動作停止と明記されています。したがって本Botの**新規ログインは
> UNKNOWN 扱い**とし、`MockPaymentProvider` と「アクセストークン直接投入」経路で
> 完成させています（TODO_PAYPAY.md 参照）。

共通事項:
- ホスト: `https://app4.paypay.ne.jp`（アプリAPI） / `https://www.paypay.ne.jp`（Web/OAuth）
- 認証: `Authorization: Bearer <access_token>`（アプリAPI）
- 共通レスポンス: `{"header": {"resultCode": "S0000", ...}, "payload": {...}}`
- 日本国内IPからのみアクセス可（国外は CloudFront 403）

## resultCode 一覧 (LIKELY)

| code | 意味 |
|------|------|
| S0000 | 成功 |
| S0001 | アクセストークン失効/取消（要再ログイン or refresh） |
| S1005 | 残高不足 |
| S2205 | P2P失敗（支払い手段なし） |
| S5000 | サーバー側予期せぬエラー |
| S9999 | 汎用エラー |

---

## begin_login (PAR + password sign-in)

- **METHOD**: POST
- **PATH**: `/bff/v2/oauth2/par`（app4） → `www.paypay.ne.jp` 側でsign-in
- **PURPOSE**: OAuth2 PKCE/PAR でログインを開始
- **AUTH**: なし（ログイン前）
- **REQUEST**: `clientId, clientAppVersion, redirectUri, responseType=code, codeChallenge, codeChallengeMethod=S256, scope=REGULAR, tokenVersion=v2`
- **RESPONSE**: `payload.requestUri`
- **ERRORS**: header.resultCode != S0000
- **STATUS**: **UNKNOWN**（sign-in 以降が anti-bot 保護。4桁SMS OTP は廃止され OTL(ワンタイムリンク)方式へ）

## submit_otp / OTL 確認

- **PATH**: `/portal/api/v2/oauth2/extension/sign-in/2fa/otl/verify` 他（www）
  → `/bff/v2/oauth2/token`（app4）でトークン交換
- **PURPOSE**: 2FA(OTL)完了とトークン取得
- **RESPONSE**: `payload.accessToken`, `payload.refreshToken`（access は約90日有効）
- **STATUS**: **UNKNOWN**（現行保護のため未実装。TODO_PAYPAY.md）

## token_refresh

- **METHOD**: POST
- **PATH**: `/bff/v2/oauth2/token`
- **PURPOSE**: refresh_token でアクセストークン更新
- **REQUEST**: `clientId, refreshToken, grantType=refresh_token`
- **RESPONSE**: `payload.accessToken, payload.refreshToken`
- **STATUS**: **LIKELY**（公開実装の記述に基づく。仕様変更の可能性あり）

## link_check (getP2PLinkInfo) — 金額確認

- **METHOD**: GET
- **PATH**: `/bff/v2/getP2PLinkInfo?verificationCode=<code>&payPayLang=ja`
- **PURPOSE**: 送金リンクの金額・状態・送信者を取得
- **AUTH**: Bearer
- **RESPONSE (抜粋)**:
  - `payload.pendingP2PInfo.orderId`  … PayPay内部の注文ID
  - `payload.pendingP2PInfo.amount`   … 金額(int)
  - `payload.pendingP2PInfo.isSetPasscode` … パスコード有無
  - `payload.orderStatus` … `PENDING | SUCCESS | REJECTED | FAILED`
  - `payload.sender.displayName / externalId`
  - `payload.message.messageId / chatRoomId`
- **STATUS**: **LIKELY**（PayPaython-mobile 4.x と一致）

## link_receive (acceptP2PSendMoneyLink) — 自動受取

- **METHOD**: POST
- **PATH**: `/bff/v2/acceptP2PSendMoneyLink`
- **PURPOSE**: 送金リンクを受け取る
- **AUTH**: Bearer
- **REQUEST**: `requestId(uuid), orderId, verificationCode, passcode?, senderMessageId, senderChannelUrl`
- **RESPONSE**: `header.resultCode == S0000` で受取成功、`payload.orderId` 等
- **注意**: 受取APIの成功レスポンスだけで完了とせず、`getP2PLinkInfo` で
  `orderStatus == SUCCESS` を**再確認**してから商品を渡すこと（本Bot実装済み）
- **STATUS**: **LIKELY**

## alive（Bot検知回避のダミーリクエスト）

- **PATH**: `/bff/v1/getGlobalServiceStatus`, `/bff/v4/getHomeDisplayInfo`, `/bff/v1/getFeatureFlagInfo`
- **PURPOSE**: セッション生存確認 & 人間らしいトラフィック
- **STATUS**: **LIKELY**

---

## 旧仕様との差分

| 項目 | 旧仕様 (PayPaython Web / 旧mobile) | 現在確認できる仕様 |
|------|-----------------------------------|--------------------|
| ログイン | 電話+パスワード → 4桁SMS OTP | OTL(ワンタイムリンク)2FA、4桁OTPは廃止 |
| link情報 | `/app/v2/p2p-api/getP2PLinkInfo`(web) | `/bff/v2/getP2PLinkInfo`(app4) |
| access token 寿命 | 短い(web) | 約90日(mobile) |
| Bot検知 | なし | 2025/11以降ログインに追加、以後更新 |

> 実HAR/JSONを取得したら `tools/analyze_har.py` / `analyze_json.py` で
> 構造を確認し、この表を CONFIRMED に更新してください。
