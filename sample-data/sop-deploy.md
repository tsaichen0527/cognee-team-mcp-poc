# SOP：正式環境部署流程

1. 在 GitLab 建立 release tag，格式為 `vYYYY.MM.DD-N`。
2. CI 通過後，由當週值班人員（On-call）在 Argo CD 按下 Sync。
3. 部署後 15 分鐘內觀察 Grafana 的錯誤率面板；若 5xx 比例超過 1%，立即執行 rollback。
4. Rollback 指令：`argocd app rollback order-service`。
5. 部署結果記錄到 #release 頻道，並附上 tag 與變更摘要。

聯絡窗口：SRE 組 Carol（分機 3210）。
