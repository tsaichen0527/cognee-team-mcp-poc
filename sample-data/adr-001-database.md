# ADR-001：交易資料庫選型

- 狀態：已採納
- 日期：2026-09-15
- 決策者：平台組 Alice、後端組 Bob

## 背景

訂單服務原本使用 MySQL 5.7，即將停止支援，而且缺乏 JSONB 與 row-level security。

## 決策

訂單服務改用 PostgreSQL 17 儲存交易資料；Redis 7 只作為快取，不存放唯一資料來源。

## 影響

- 所有 schema 變更必須經過 Code Review，並附上 migration rollback 腳本。
- 每日凌晨 2 點由 SRE 執行 pg_dump 備份，保留 14 天。
