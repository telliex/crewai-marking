# Awkns Outreach 操作說明書

這是一份給**實際使用這個系統寄送 cold email 的人**看的操作手冊（例如業務、行銷）。
如果你要處理的是伺服器、資料庫、部署相關的問題，請找工程窗口，不在本文件範圍內。

---

## 1. 這個服務在做什麼

Awkns Outreach 是一套「多步驟 cold email 自動寄送」工具：

1. 你匯入一批目標名單（公司 + email）
2. 系統依照你設定好的「幾封信、間隔多久」腳本（Sequence），自動依序寄出
3. 有內建的寄信保護機制（每日上限、對方當地上班時間才寄、退信/取消訂閱名單自動排除），降低被判定為垃圾郵件或違規的風險

---

## 2. 登入

打開系統網址（詢問工程窗口取得，例如 `http://<伺服器位址>:8000`），會跳出瀏覽器內建的登入視窗：

- **Username**：隨便填，系統不檢查（例如填 `admin` 即可）
- **Password**：跟工程窗口要目前的管理密碼

---

## 3. 核心概念

在動手操作前，先認識幾個名詞，因為畫面上會一直出現：

| 名詞 | 說明 |
|---|---|
| **Campaign（活動）** | 一批目標名單的容器，例如「JP 動畫工作室名單」。有自己的名稱、目標職稱、名單來源。 |
| **Lead（名單/收件人）** | 一個具體的收件人（公司 + email + 聯絡人）。屬於某個 Campaign。 |
| **Tier（分級 A/B/C）** | 每個 Lead 會被分到 A（最優先）、B（一般）、C（較低優先）。沒分類的視同 B。可以手動設定，也可以用 AI 自動分類。 |
| **Sequence（信件腳本）** | 一組「第 1 封信寄什麼、隔多久寄第 2 封信、寄什麼」的模板組合，跟特定名單無關，可以重複套用在不同 Campaign 上。 |
| **Task（任務）** | 把一個 Campaign 跟「每個 Tier 要用哪個 Sequence」綁在一起，並且掌控**何時開始寄、暫停、停止**。**真正負責寄信的就是 Task**，Campaign 本身不會主動寄信。 |
| **Template（範本庫）** | 可重複使用的信件內容片段，在編輯 Sequence 的每一步時可以直接套用，不用每次重打。 |
| **Mailbox（信箱）** | 選用功能：可以連接一個 Gmail 信箱來寄信，取代預設用 Resend 服務寄送。 |

---

## 4. 完整操作流程

### Step 1 — 建立 Campaign

1. 上方選單點 **New campaign**
2. 填 Name（活動名稱）、Target titles（想接觸的職稱，一行一個）
3. Seed companies（名單來源）可以：
   - 上傳 CSV 或 JSON 檔（欄位：name, website, country, category, tier, angle, email, contact_name, contact_title，都是選填，只有 `website` 是查 Apollo 用的）
   - 或直接貼 JSON/CSV 文字進文字框
   - 也可以先跳過，之後在 Campaign 頁面再補
4. 按 **Create campaign**

### Step 2 — 把名單變成 Lead（兩種方式擇一）

- **方式 A：用 Apollo 查詢補齊資料**（畫面上「Apollo enrich」卡片）
  設定 Limit（查幾筆），若要花費點數解鎖 email/電話，勾選 **reveal**，按 Run。
- **方式 B：直接轉換（跳過 Apollo）**（畫面上「Convert seed companies to leads」卡片）
  適用於你上傳的名單**已經有 email** 的情況，不需要再查 Apollo，直接按 **Convert** 把 seed companies 轉成正式 Lead。

### Step 3 — （選用）AI 分級

Campaign 頁面「AI classify」卡片，設定 Limit，按 **AI classify**，系統會依照設定的邏輯把每個 Lead 標成 A/B/C。也可以在 Lead 列表手動改。

### Step 4 — 準備 Sequence（信件腳本）

上方選單 **Sequences → New sequence**：

1. 幫這組腳本取名字
2. 每個 Step 填 Subject、Body（可以用右側 **Insert template** 直接套用範本庫內容）
3. 如果不是第一封信，上面會有一個「⏱ 時間膠囊」按鈕（connector pill），點開可以設定「這一步要等上一步過多久才寄」，單位可選分鐘 / 小時 / 天（**最短可以設 1 分鐘**）
4. 右側有 Live preview 即時預覽，也可以送測試信給自己確認排版
5. 存檔

同一個 Sequence 可以重複用在不同的 Campaign / Tier 上。

### Step 5 — 建立 Task（正式綁定「寄給誰、寄什麼」）

上方選單 **Tasks → New task**：

1. 選一個 Campaign
2. 幫 Tier A / B / C 各自指派一個 Sequence（至少指派一個 tier）
3. 存檔

> ⚠️ Campaign 頁面如果出現「This campaign has no active task」的提示，代表這個活動**還沒有任何 Task 在運作，不會寄出任何信**，一定要建立並啟動 Task。

### Step 6 — 啟動 Task

回到 **Tasks** 列表，剛建立的 Task 是 `draft` 狀態，有兩個選擇：

- **Schedule**：設定一個未來的開始時間（+ 選填結束時間），到時間系統會自動開始
- **Start now**：立刻開始

啟動後狀態變成 `running`，畫面會出現操作按鈕：

| 按鈕 | 作用 |
|---|---|
| **Pause** | 暫停，之後可以 Resume 繼續（進度不會遺失） |
| **Stop** | 徹底停止，**無法復原**，會跳確認視窗 |
| **Run（Max + 勾選 send for real）** | 立刻手動觸發一次寄信檢查。**Max** 是這次最多處理幾筆。**不勾 "send for real" = 只是模擬（dry-run），不會真的寄信**，用來確認邏輯正常；勾選之後才會**真的寄出**。 |

正常情況下，Task 一旦是 `running`，背景會**自動**依照排程持續寄信，不需要每次手動按 Run —— 但這需要伺服器上的背景排程程式持續在跑（見下方第 6 節），如果那個程式沒開，就必須靠手動按 Run 才會有動作。

---

## 5. 監控與管理

### Campaign 詳情頁

每個 Campaign 頁面上方會顯示：

- **Total / Active / Completed / Suppressed** 人數統計
- **Sent last 24h / Daily cap / Remaining today**：今天還能寄幾封（見下方每日上限說明）
- **Sequence steps**：目前套用的腳本共幾步
- 下方 Lead 列表可依 Tier 篩選，每筆會顯示目前走到第幾步（Step）、狀態（Status）、下次動作時間（Next action）

### 暫停 / 恢復單一個 Lead

Lead 列表的 Status 欄位，狀態是 `active` 時可以點 **pause** 單獨暫停這一個人（其他人不受影響）；`paused` 時可以點 **resume** 恢復。其他狀態（sending / completed / replied / bounced / suppressed / failed）是系統自動管理，不能手動改。

### 暫停 / 封存 Campaign

Campaign 列表可以：
- **Pause** 整個活動（暫停寄信，可 Resume）
- **Archive** 封存（名單和歷史紀錄都保留，只是停止寄信；封存後要先 Unarchive 才能再編輯）

---

## 6. 寄信規則與保護機制（重要，不是 bug）

系統內建幾個「看起來卡住、其實是刻意設計」的保護機制：

1. **合規地址未設定 = 完全不能真的寄信**
   法規（CAN-SPAM）要求商業郵件要附實體地址，這個地址由工程窗口在系統設定裡配置，若未設定，即使按了「send for real」也會被擋下，只能維持 dry-run。

2. **每日寄送上限（會逐週爬升，不是一開始就全速）**
   新的寄送身分一開始每天上限較低（暖機期），之後才逐步提高，避免新網域/新帳號一開始就大量寄信觸發垃圾信判定。Campaign 頁面的 **Cap / left** 會顯示目前上限跟今天還剩多少額度。

3. **只在收件人當地「上班時間」寄信**
   系統會依收件人所在國家判斷當地時間，只在平日 9:00–17:00 才會寄出。如果某封信「明明排到了，卻沒寄出」，很常見的原因就是那個時間點是對方的半夜或週末，系統會自動延後，不是壞掉。

4. **收件人可以自己取消訂閱**
   每封信都會自動附上取消訂閱連結，一旦對方點了，會自動進入 suppression（排除）名單，之後所有 Campaign 都不會再寄給他。

---

## 7. 「排好的信沒有準時寄出」該怎麼看

如果 Task 是 `running`、時間也到了，但信沒有寄出，依序檢查：

1. **是不是卡在第 6 節的上班時間規則？**（最常見）— 看收件人所在國家的當地時間是否在平日 9–17 點
2. **今天的每日上限是不是已經用完？** — 看 Campaign 頁面的 Remaining today
3. **背景排程程式是否還在跑？** — 這是系統自動觸發寄信的引擎，如果它掛掉，Task 即使是 running 狀態也不會自動有動作，需要工程窗口確認伺服器上的排程服務狀態；此時可以先用 Tasks 頁面的 **Run**（勾 send for real）手動補寄一次應急
4. **收件人是不是已經在 suppression 名單裡？**（例如之前取消訂閱過）

如果以上都排除了信還是沒寄出，或者對方回報收到了但你系統上沒看到已寄出的紀錄，請聯絡工程窗口，附上 Campaign 名稱、Lead email、大概時間，方便查後端紀錄。

---

## 8. 有問題找誰

本文件只涵蓋「怎麼用這個網站」。如果是：
- 網站打不開 / 登入密碼要重設
- 想調整每日寄送上限、上班時間規則、合規地址等系統參數
- 伺服器 / 資料庫相關問題

請聯絡工程窗口，不要自行嘗試調整伺服器設定。
