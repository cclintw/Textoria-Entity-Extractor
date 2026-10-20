---
name: textoria-entity-extractor
description: 使用者要求從文本檔、HTML 或單一網頁網址提取實體清單、建立實體詞表或實體參考 CSV 時使用。以 LLM 逐片辨識名稱與類型，自動去重、以系統暫存目錄處理並匯出 CSV。不因附加文本、出現實體名稱或討論本 Skill 就啟動提取。
---

# Textoria Entity Extractor

目前提供命名實體參考詞表的提取流程。明確要求提取時，先讀取 [完整提取流程與規則](references/extraction-workflow.md)，再依該文件完成來源確認、逐片閱讀、合併與匯出。單純上傳檔案、提供網址或討論本 Skill，不等於要求提取；其他工作依使用者實際意圖處理。

## 目錄與資源

- Skill 安裝位置：`<PROJECT>/.agents/skills/textoria-entity-extractor/`，以此作為 `<SKILL_DIR>`。
- 使用者資料統一放在 `<PROJECT>/textoria-entity-extractor/`：`source/` 保存來源，`csv/` 保存成果。切片、回應與進度只放系統暫存目錄，CSV 成功匯出並驗證後自動清除。
- `<PROJECT>` 是使用者工作專案根目錄；傳給 `prepare_source.py --project` 時，不要傳入 Skill 目錄或 `textoria-entity-extractor/`。
- [scripts/prepare_source.py](scripts/prepare_source.py) 準備來源並回傳實際資料路徑；[scripts/entity_pipeline.py](scripts/entity_pipeline.py) 管理分片、驗證、進度與 CSV。語意辨識由 Agent 閱讀文本完成。

## 交付要點

- 以單一 Agent 依序處理全文，不自行分派子代理。
- 正式成果預設為 UTF-8 BOM CSV，欄位為 `name,type,alias`；使用者只要兩欄時使用 `--no-alias`。
- 保留原文名稱、簡稱與具獨立語意的巢狀名稱，以 `name,type` 去重；別名只表示來源語境中的稱呼關聯。
- 不將日期、時間或純數量作為實體；具名年號獨立收錄為 `era_name`。
- 中斷或失敗時沿用已回傳的暫存 `<WORK>` 重試；系統若已清除暫存，重新準備來源，不承諾永久續跑。
- 來源確認後才建立資料；全文未處理完不宣稱完成。每個來源保留最近三份 CSV，最後提供實際檔案連結及必要說明。

- Codex 完成後，成果連結文字使用「檢視《文本名稱》實體清單 CSV」，連向實際 CSV 的絕對路徑；這是供右側面板檢視的本機檔案，不稱為「下載」。
