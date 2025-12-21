# WOS Redeemer – Delivery Plan

Status: in progress (scaffold + models)

1) Scaffold package + DB models (alliances, users, gift_codes, redemptions, attempts) – in progress
2) Refactor redeem logic into `wos_redeem.api` and the local ONNX solver (`wos_redeem.captcha_solver`)
3) RSS scraper (wosgiftcodes.com/rss.php) every 5 minutes → insert new codes
4) Redemption worker: assign oldest active un-applied codes to active users; 3 attempts with logging
5) Admin UI (Bootstrap): alliances, quotas, R4/R5 managers, user registration; drill-down + monitoring
6) Auth: admin and alliance managers; per-alliance quota enforcement
7) Smoke tests and docs
