# PayPay 非公式API メモ

このドキュメントは、本Botが利用する PayPay モバイル非公式APIの調査結果です。
各項目には確度を付けています。

- **CONFIRMED**: 実トラフィック/複数実装で一致確認済み
- **LIKELY**: 公開実装（PayPaython-mobile 等）に記載があり整合的だが、当環境で実測未確認
- **UNKNOWN**: 仕様不明、または現在サーバー側の保護で再現できない

> **更新 (2026-07)**: 実働する Discord Bot 実装（2026年時点、`clientAppVersion 5.55.0`
> / Android16）を解析し、**anti-bot(AWS WAF) を headless Chromium で突破**する方式で
> 新規ログインを実装しました。これにより `begin_login`/`submit_otp`/`token_refresh`
> は CONFIRMED（実働実装ベース）へ更新。ブラウザ自動操作は WAF Cookie 取得の一瞬のみで、
> OTP・受取・送金はすべて httpx。

### anti-bot (AWS WAF) — CONFIRMED
- PayPay の sign-in ホスト(`www.paypay.ne.jp`)は AWS WAF 配下。
- **突破方法**: Playwright(headless Chromium)で
  `https://www.paypay.ne.jp/portal/oauth2/sign-in?client_id=pay2-mobile-app-client`
  を1回ロードし、`aws-waf-token` Cookie を取得。以降その Cookie を付けて httpx で全処理。
- 実装: `src/paypay/auth.py` `get_waf_token()`

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
- **後続**: `GET /portal/api/v2/oauth2/authorize` → `par/check` →
  `POST /portal/api/v2/oauth2/sign-in/password` (`{username, password, signInAttemptCount}`)
  - 既知デバイス → `payload.redirectUrl` に `code=` が入り OTP 不要
  - 未知デバイス → OTP(OTL) 必要
- **STATUS**: **CONFIRMED**（実働実装ベース。要 aws-waf-token Cookie）

## submit_otp / OTL 2FA — CONFIRMED

- **トリガー**: `code-grant/update`(空) → `code-grant/update`(SELECT_FLOW, `flow=OTL`,
  `sign_in_method=MOBILE`) → `.../side-channel/next-action-polling` で SMS 送信
- **完了**: OTLリンク `GET /portal/oauth2/l?id=<code>` を消費 →
  `POST /portal/api/v2/oauth2/extension/sign-in/2fa/otl/verify` (`{code}`) →
  `code-grant/update`(`COMPLETE_OTL`) もしくは polling で認可 `code` 取得 →
  `POST /bff/v2/oauth2/token` (`grantType=authorization_code, code, codeVerifier`)
- **RESPONSE**: `payload.accessToken`, `payload.refreshToken`（access は約90日有効）
- **注**: 4桁SMS OTP は廃止。現行は OTL(ワンタイムリンク)。ユーザーは届いたリンク/IDを入力
- **STATUS**: **CONFIRMED**

## token_refresh — CONFIRMED

- **METHOD**: POST
- **PATH**: `/bff/v2/oauth2/refresh`  ← ※ `/token` ではない
- **REQUEST**: `clientId, grantType=refresh_token, refreshToken, code=<refreshToken>, redirectUri`
- **RESPONSE**: `payload.accessToken, payload.refreshToken`
- **STATUS**: **CONFIRMED**（実働実装ベース）

## link_check (getP2PLinkInfo) — 金額確認

- **METHOD**: GET
- **PATH**: `/bff/v2/getP2PLinkInfo?verificationCode=<code>&payPayLang=ja`
- **PURPOSE**: 送金リンクの金額・状態・送信者を取得
- **AUTH**: Bearer
- **RESPONSE (抜粋)**:
  - `payload.pendingP2PInfo.amount`   … 金額(int)
  - `payload.pendingP2PInfo.senderName` … 送信者名
  - `payload.pendingP2PInfo.isSetPasscode` … パスコード有無
  - `payload.orderStatus` … `PENDING | SUCCESS | REJECTED | FAILED`
  - `payload.message.data.orderId`   … 受取に使う orderId
  - `payload.message.data.requestId` … 受取に使う requestId（再利用）
  - `payload.message.messageId / chatRoomId`
- **STATUS**: **CONFIRMED**（実働実装ベース。orderId/requestId は message.data 側が正）

## link_receive (acceptP2PSendMoneyLink) — 自動受取

- **METHOD**: POST
- **PATH**: `/bff/v2/acceptP2PSendMoneyLink`
- **PURPOSE**: 送金リンクを受け取る
- **AUTH**: Bearer
- **REQUEST**: `requestId(link_infoのrequestId), orderId, verificationCode, senderMessageId(messageId), senderChannelUrl(chatRoomId), passcode?`
- **RESPONSE**: `header.resultCode == S0000` で受取成功、`payload.orderId` 等
- **注意**: 受取APIの成功レスポンスだけで完了とせず、`getP2PLinkInfo` で
  `orderStatus == SUCCESS` を**再確認**してから商品を渡すこと（本Bot実装済み）
- **STATUS**: **CONFIRMED**（実働実装ベース）

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
