# 寄送額度與節奏規則

本文件完整列出系統內建的寄送限制／節奏規則，對應程式碼位置，以及釐清哪些是本專案自訂的保護機制、哪些是外部（法規／平台）真正的限制。

## 1. 每日寄送上限（per-campaign，24h rolling window）

| 規則 | 值 | 說明 |
|---|---|---|
| 絕對硬上限 | `hard_daily_cap = 100` | 單一寄送網域的天花板，超過應該加新網域，不建議直接調高 |
| Warmup 曲線 | `(5,10,15,20,25,30,40,50,60,70,80,90,100)` | 從 `campaign.warmup_start` 那天起算，第 0 天 5 封 → 第 12 天起 100 封／天（約 2.5 週爬升） |
| `warmup_start = None` | 停在 `ramp[0] = 5` | 一直沒設定就永遠卡在 5 封/天 |
| 實際 cap | `min(hard_daily_cap, warmup_cap(...))` | 兩者取小 |
| 計算窗口 | rolling 24h，不是日曆天 | 從 `outreach_event` 表算 `type='sent'` 且 `created_at >= now - 24h`，即時滑動 |
| 額度歸屬 | 掛在 Campaign，不是 Task | **同一 campaign 底下所有 task 共用同一份每日額度，會互相搶** |

程式位置：[`sequencer/limits.py`](../src/awkns_outreach/sequencer/limits.py) `SendLimits` / `warmup_cap()`，套用於 [`sequencer/engine.py:139`](../src/awkns_outreach/sequencer/engine.py#L139)。

## 2. 收件人本地送信時段（per-lead）

| 規則 | 值 |
|---|---|
| 星期 | Mon–Fri（Python weekday 0–4） |
| 時段 | 09:00–17:00（收件人當地時間，`[start, end)`） |
| 時區判定 | 依 `lead.country` 對照表：JP/KR/TW/US/CN/HK/SG，其餘一律預設 `Asia/Taipei` |
| Task 層級 override | `ignore_business_hours` / `ignore_workdays`，各自獨立開關，由「Schedule…」「Start now…」設定 |
| 不符合時段 | lead 標成 `skipped:hours`，**不算用掉每日額度**，下次 tick 再試 |

程式位置：[`sequencer/limits.py`](../src/awkns_outreach/sequencer/limits.py) `in_send_window()`。

## 3. 退訂／黑名單（Suppression list）

檢查順序在時段之前：email 在 `Suppression` 表 → 直接把 lead 標成 `suppressed`（永久退出寄送池），計入 `summary.suppressed`。每封信都會自動附上取消訂閱連結，一旦對方點了即自動進入此名單。

## 4. Tier → Sequence 對應

Task 啟動時把每個 tier 指定的 sequence 快照進 `task.steps_by_tier`。若某個 lead 的 tier 沒有對應的 sequence（部分指派，或啟動後 tier 改了）→ lead 標成 `paused`，`skipped:no-tier-sequence`。

## 5. 併發防重複寄送（Claim / compare-and-swap）

- 正式寄送前先 CAS：`status: active → sending`，搶輸的（rowcount=0）本輪跳過，`skipped:claimed`。
- 若某次執行中途當掉，lead 卡在 `sending` 超過 **10 分鐘**（`STALE_CLAIM_SECONDS = 10*60`）→ 下次執行自動撿回 `active`。

程式位置：[`sequencer/engine.py:120-128, 212-222`](../src/awkns_outreach/sequencer/engine.py#L120)。

## 6. 寄送間隔節奏（human-scale pacing）

- 同一輪次裡兩封真寄之間：`min_gap_ms = 90_000`（90 秒）+ 隨機 jitter 最多 `jitter_ms = 150_000`（150 秒）→ 實際間隔 **90–240 秒**。
- Cron 模式會把 `gap_ms=0` 傳進去（因為 cron 每分鐘 tick 一次、`--max` 本身就是節奏），改由 tick 間隔提供間距；CLI batch 模式才會真的 `time.sleep`。

## 7. 寄送失敗重試上限（per-lead per-step）

`MAX_SEND_ERRORS = 3`。每次失敗記一筆 `Event(type='error')`，同一 lead 同一 step 累積到 3 次 → lead 標成 `failed`，永久離開寄送池；未達 3 次則放回 `active` 讓下一輪重試。

程式位置：[`sequencer/engine.py:37, 260-282`](../src/awkns_outreach/sequencer/engine.py#L37)。

## 8. 法遵閘門（僅正式寄送，dry-run 略過）

`can_send_legally()`：identity 必須有 `postal_address`（CAN-SPAM 要求商業郵件附實體地址），沒有就整個 campaign 直接 `blocked`。

程式位置：[`compliance.py:141`](../src/awkns_outreach/compliance.py#L141)。

## 9. Campaign 狀態閘門（僅正式寄送）

正式寄送要求 `campaign.status == "active"`，paused/archived 會被 `blocked`（dry-run 預覽不受此限）。

## 每個 lead 的檢查順序

daily cap（整批只算一次）→ tier 有無對應 sequence → 序列是否已跑完 → 是否在 suppression list → 是否在本地送信時段 → CAS 搶 claim → pacing 間隔 → 實際送信 → 依成功/失敗更新狀態

---

## 這些規則是誰訂的？自訂 vs 第三方限制

**上述第 1–7、9 條全部是本專案自己訂的內部保護機制，不是 Gmail API 強制要求的。**

判斷依據：

1. **程式碼是自訂 config，不是讀 Gmail 配額**：[`sequencer/limits.py`](../src/awkns_outreach/sequencer/limits.py) 開頭註明是「Port of the non-copy bits of yoh's config.ts」——從姊妹專案 `yoh` 整套搬過來的自訂 dataclass，數字（5/10/15…/100、9–17:00、90–240 秒）都是寫死的常數，不是從 Gmail API 回傳的任何配額欄位。
2. **User guide 明講是刻意設計**：[`docs/user-guide.md`](./user-guide.md) 有一整節「寄信規則與保護機制（重要，不是 bug）」，說明這些限制是為了保護寄送網域信譽、避免觸發垃圾信判定而**主動放慢**，不是 Gmail 卡住。
3. **程式碼完全沒有處理 Gmail 配額錯誤**：`gmail/api.py`、`send/mailer.py` 裡沒有任何 429 / rate-limit / quota-exceeded 的錯誤處理邏輯——代表系統設計是「自己先踩煞車」，根本不會撞到 Gmail 真實的牆。

**唯一非自訂的規則**是第 8 條（法遵地址檢查）：來自 CAN-SPAM 法案的商業郵件規定，是法律要求，不是 Gmail 的技術限制。

**Gmail 自己真正的限制**（一般帳號約 500 封收件人/天，Workspace 約 2000 封/天，外加 Google 動態的垃圾信/信譽演算法）是額外一層，程式完全沒去讀取或對接。目前的 warmup 曲線（5→100）刻意設計得遠低於 Gmail 官方上限，用意就是主動避開，讓 Google 的演算法根本不會出手限流。**兩者是獨立的兩層，調整本文件列出的任何規則都是內部決策，沒有外部依賴會擋。**
