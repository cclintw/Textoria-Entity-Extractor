---
name: textoria-entity-extractor
description: 使用者要求從文本檔、HTML 或單一網頁網址提取實體清單、建立實體詞表或實體參考 CSV，或將既有 Textoria CSV 轉成 MARKUS TXT／DocuSky XLSX 時使用。逐片辨識、依證據正名與身分整理後匯出預設 CSV，可另選平台格式。不因附加文本、出現實體名稱或討論本 Skill 就啟動提取。
---

# Textoria Entity Extractor

目前提供命名實體參考詞表的提取流程。明確要求提取時，先讀取 [完整提取流程與規則](references/extraction-workflow.md)，再依該文件完成來源確認、逐片閱讀、合併與匯出。單純上傳檔案、提供網址或討論本 Skill，不等於要求提取；其他工作依使用者實際意圖處理。

只要求將既有 Textoria CSV 轉成平台格式時，讀取 [平台轉檔規則](references/platform-exports.md)，直接轉檔，不重讀來源或重新提取。

## 目錄與資源

- Skill 安裝位置：`<PROJECT>/.agents/skills/textoria-entity-extractor/`，以此作為 `<SKILL_DIR>`。
- 使用者資料統一放在 `<PROJECT>/textoria-entity-extractor/`：`source/` 保存來源，`csv/` 保存成果。切片、回應與進度只放系統暫存目錄，CSV 成功匯出並驗證後自動清除。
- `<PROJECT>` 是使用者工作專案根目錄；傳給 `prepare_source.py --project` 時，不要傳入 Skill 目錄或 `textoria-entity-extractor/`。
- [scripts/prepare_source.py](scripts/prepare_source.py) 準備來源並回傳實際資料路徑；[scripts/entity_pipeline.py](scripts/entity_pipeline.py) 管理分片、驗證、進度與 CSV。語意辨識由 Agent 閱讀文本完成。
- [scripts/convert_exports.py](scripts/convert_exports.py) 將完成的 CSV 轉為選定的 MARKUS TXT、DocuSky XLSX；只使用 Python 標準函式庫。

## 交付要點

- 以單一 Agent 依序處理全文，不自行分派子代理。
- 正式成果預設為 UTF-8 BOM CSV，欄位為 `name,type,alias`；使用者只要兩欄時使用 `--no-alias`。
- Textoria CSV 每次提取都必須產生，不列為可取消的選項。來源確認後、開始提取前，若尚未指定附加格式，詢問：「除了預設的 Textoria CSV，還需要輸出 MARKUS TXT、DocuSky XLSX 嗎？可複選，也可以只保留 CSV。」提供「只保留 CSV」「另加 MARKUS TXT」「另加 DocuSky XLSX」「兩種都加」四種組合。等待回答；已有明確選擇時不重問。來源不明時可合併詢問，不提前建立資料。
- 分片辨識與合併後，先完成正名與身分審核；CSV 成功匯出後，依 [平台轉檔規則](references/platform-exports.md) 產生選定的附加檔案。轉檔失敗保留 CSV，從該 CSV 重試，不重新提取。
- MARKUS TXT 必須包含以 Tab 分隔的 `tagtype`、`tag`、`id` 表頭；資料列固定三欄，ID 無資料時第三欄留空。
- 正式表以有證據支持的正名為 name，已確認同一實體的兩字以上別名放 alias（以 `|` 分隔），不另建別名列。不同實體及同名異人保留區別，不按字串、包含關係或共同簡稱強行合併。
- person、place（含行政區）、office、official_title 的 name 與 alias 一律不得為單字，即使是完整名稱也不例外。其他類型仍排除單字簡稱；牛、馬等完整單字名稱經確認可作 name。無法確認正名時不臆補。
- 匯出前必須執行 `resolve` 審核全部候選；內部 JSON 只存暫存工作資料，不增加正式 JSON 成果。舊 CSV 不會自動整理或改寫。詞表仍可能在普通詞中誤匹配，需由下游檢查語境。
- 不將日期、時間或純數量作為實體；具名年號獨立收錄為 `era_name`。
- 中斷或失敗時沿用已回傳的暫存 `<WORK>` 重試；系統若已清除暫存，重新準備來源，不承諾永久續跑。
- 來源確認後才建立資料；全文未處理完不宣稱完成。每個來源保留最近三份 CSV，最後提供實際檔案連結及必要說明。

- Codex 完成後，CSV 連結文字使用「檢視《文本名稱》實體清單 CSV」，另列已選且成功產生的 MARKUS TXT／DocuSky XLSX 連結，全部連向實際絕對路徑，不稱為「下載」。
