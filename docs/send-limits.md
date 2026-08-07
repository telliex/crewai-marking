# 寄送額度與節奏規則

本文件完整列出系統內建的寄送限制／節奏規則，對應程式碼位置，以及釐清哪些是本專案自訂的保護機制、哪些是外部（法規／平台）真正的限制。

## 1. 每日寄送上限（per-campaign，24h rolling window）

| 規則 | 值 | 說明 |
|---|---|---|
| 絕對硬上限 | `hard_daily_cap = 100` | **每個 campaign** 一天的信件數天花板（見下方「常見疑問」——是按 campaign 算，不是按真實寄件網域聚合）。要更多量應開新 campaign／換網域，不建議直接調高 |
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

---

## 常見疑問澄清（實務上最容易誤解的點）

### Q1. 每日 100 是「按網域」還是「按 campaign」？多個 campaign 用同一個網域會加總嗎？
**按 campaign 算，程式完全不看真實寄件網域。** 計數只做一件事：數這個 campaign 過去 24 小時送出的 `sent` 事件數。

- 同一個 campaign 不論用幾個信箱/網域送 → 共用同一份 100。
- **兩個 campaign 各自算各自的 100，不會跨 campaign 加總。** 所以若你用多個 campaign 卻共用同一個真實寄件網域，該網域一天可能被送出 200、300 封，**系統不會擋**——要靠不同 campaign 綁不同網域/信箱自行分流。這也是「要更多量就開新 campaign／加網域」的原意。

### Q2. 一個 campaign 最多只能寄 100 個客戶嗎？
**不是。100 是「每 24 小時的信件數」，不是客戶數，而且所有步驟共用。**

- 一個 campaign 可有幾千個 leads，只是每天最多送 100 封、自動分天送出（drip）。**不需要自己排，系統會自動限速慢慢送。**
- **跟進信（sequence 第 2、3 步）也各算一封**，跟首封搶同一個 100 額度。
- 全部送完的時間下限 ≈ **（人數 × 步驟數）÷ 每日上限**。例：1000 人 × 3 步 = 3000 封 ÷ 100 ≈ **至少 30 天**。

### Q3. 多步驟：同一群人每步都會收到嗎？
**會，依序每步各收到一封，除非中途掉出寄送池**——對方回信、退訂、硬退信(bounce)、或連續送信失敗 3 次就不再收後續步驟。

### Q4. 步驟「間隔 2 小時」會準時 2 小時後寄嗎？
**不會保證。步驟間隔是「最少等這麼久」的下限，不是排定的寄送時刻。** 一封跟進信要送出必須同時滿足：①間隔到了 ②當天還有額度 ③在送信時段內。當池子遠大於每日額度時，卡住的是**額度**，實際間隔會被撐成好幾天。

- 另外：**第一步的延遲不會延後首封**——task 一啟動 leads 就「立即符合資格」，首封在下一個排程 tick 就送（受送信時段與額度限制）。延遲只對**第 2 步以後**有意義。
- 要控制「活動幾點開始寄」用 Task 的排程開始時間 `scheduled_start_at`，不是步驟延遲。

**實用建議**：先估「人數 × 步驟數」的總信量，讓每天疊起來不超過每日上限，跟進節奏才守得住。多步驟時人數要抓得比 100 更保守（例如 30–50 人／campaign）；間隔用「天」為單位（≥ 1 天），小時級間隔在冷觸達沒意義、也一定被限速吃掉；要放量就開多個 campaign 分網域。

### Q5. warmup 是什麼？怎麼設？「不作用」是設 0 嗎？
warmup（暖機）是逐步養新網域信譽的機制：從 `campaign.warmup_start` 起算，每日上限照曲線放寬（第 0 天 5 → 第 12 天起 100，約 2.5 週滿速），避免全新網域一開始就狂送被判垃圾信。

`warmup_start` 存的是一個**日期，不是數字，沒有「設 0」這回事**：

| 想要的效果 | `warmup_start` 設成 |
|---|---|
| 正常暖機（5 慢慢爬到 100） | 活動開始當天 |
| 直接滿速 100/天（略過暖機，僅限已養熟的網域） | **≥12 天前**的日期 |
| 最保守、固定 5 封/天 | 留空 `NULL` |

> ⚠️ **目前的功能缺口**：沒有任何 UI／CLI／建立流程會寫入 `warmup_start`，`create_campaign` 也不設它，所以**透過介面建立的 campaign 預設都是 `NULL` → 實際上每天只送得出 5 封**，除非直接改資料庫。要調整目前只能下 SQL，例如：
> ```sql
> -- 直接滿速 100/天（網域已養熟時）
> UPDATE campaign SET warmup_start = now() - interval '13 days' WHERE id = '<campaign_id>';
> -- 正常開始暖機
> UPDATE campaign SET warmup_start = now() WHERE id = '<campaign_id>';
> ```
> （未來可在 campaign 編輯頁加一個 warmup 設定，把這個缺口補上。）
