# 從 Textoria CSV 產生平台檔案

Textoria CSV 是每次提取必備的主檔，格式為 `name,type,alias`（明確不要別名時為 `name,type`）。MARKUS TXT、DocuSky XLSX 是可複選的附加檔。提取前先確認附加格式；使用者已指定時不重問。只要求轉換既有 CSV 時，直接使用指定檔案，不必詢問未要求的平台。

## 執行

原流程仍為 prepare → init → next → merge → resolve → export。export 成功並清除暫存後，從它回傳的實際 CSV 路徑轉檔，不推測路徑、不重新 prepare：

```bash
<PYTHON> "<SKILL_DIR>/scripts/convert_exports.py" --input "<CSV_PATH>" --formats markus docusky
```

只加一種時，`--formats markus` 或 `--formats docusky`。只要 CSV 時不呼叫轉檔。沿用已驗證的 Python 3.9 以上執行環境，不需 pip、Node 或額外套件，也不需要 LLM 再次辨識文本。

程式回傳 JSON，其中 `csv` 是主檔，`outputs` 是附加檔的絕對路徑。附加檔與 CSV 在同一資料夾，檔名為 `markus-<CSV原檔名去副檔名>.txt`、`docusky-<CSV原檔名去副檔名>.xlsx`，沿用主檔的來源識別碼與時間。維持既有 CSV 命名，不重新命名主檔。

不修改 CSV，不覆寫內容不同的既有附加檔；相同內容可重試。轉檔失敗時回報實際成功的檔案與失敗項目，修正後仍從同一 CSV 轉檔，不重做全文提取。若使用者修改過 CSV 且原附加檔存在，另存新的 CSV 檔名再轉，保留舊成果。主 CSV 沿用每來源最近三份的保留規則；附加 TXT／XLSX 不自動輪替或刪除，需保留主檔者應另行備份。

## 名稱與格式

- 接受兩欄或三欄、已經正名整理的 Textoria CSV。正式 CSV 不展開 alias；平台檔為標記詞表，會將已確認的兩字以上 alias 作為比對詞，並對相同名稱／平台類型去重，不推導身分或新名稱。平台詞表無身分欄，無法保存同名異人的差別，必須由下游檢查語境。
- MARKUS：UTF-8 純文字，**第一列必須是以 Tab 分隔的 `tagtype<TAB>tag<TAB>id` 表頭**。資料列也固定三欄，ID 無資料時保留第二個 Tab，第三欄留空。不加結尾換行或空白列。貼入 Batch Markup／批量標記時只複製第二列起的資料，因官方頁面的貼上處理器不略過表頭或空白列。`person → fullName`、`place → placeName`、`office／official_title → officialTitle`、`era_name → nianhao`；其他類型保留英文 type 作為自訂標籤，可在 MARKUS 管理標記中設定顏色與按鈕。`id` 留空：MARKUS 將 fullName 的 id 當成 CBDB ID，因此不得捏造 P001 等本地流水號，也不把不同名稱共用 ID。空詞表無法用於 Batch Markup，會報錯並保留 CSV。
- DocuSky：實際 `.xlsx` 活頁簿，單一 `entities` 工作表，第一列 `tagName,tagVal,@Term`。`person → PersonName`、`place → LocName`，其餘類型為 `Udef_<type>`。所有儲存格都是文字，`@Term` 留空，完整名稱與別名各自標記，不把來源語境的 alias 升格為全域統一名稱。
- 兩種平台檔都將較長名稱排前面；映射到相同平台標籤的同名項去重。平台的字串比對與同名多義仍需使用者檢視，不保證匯入後的全文標記完全正確。
- 單字 alias 一律拒絕；單字 name 預設也拒絕，因固定 CSV 欄位無法判定它是完整名稱或簡稱。經審核的完整單字名稱（例如牛、馬）以 `--full-single-names 牛 馬` 明確傳入才保留，這是語意確認，不是簡稱豁免，不得將經、漳、泉等縮稱傳入。任何拒絕均發生於寫出平台檔之前，不靜默刪字或補全名稱。
- 舊 CSV 不自動正名或改寫。缺乏原文／身分證據時不能可靠整理，說明限制；有證據時另存經審核的新 CSV 再轉，不自動重提文本。
- 正名與兩字以上別名也可能在普通詞中誤匹配，平台詞表不保證所有語境正確。
- 轉檔程式拒絕格式不符的主檔、未知 type、含 Tab／換行／XML 不支援字元的名稱及超過 Excel 限制的資料，不靜默刪除名稱。

## 驗證與交付

確認回傳路徑的檔案存在，TXT 可按 Tab 拆成三欄；XLSX 可讀回，表頭、文字儲存格及列內容符合上述規則。除預設 CSV 外，只交付使用者選定且成功產生的附加檔案，連結文字分別為「檢視《文本名稱》MARKUS TXT」「檢視《文本名稱》DocuSky XLSX」。不以暫存紀錄充當成果，不列實體數量。

格式依據：

- [MARKUS Batch Markup 頁面與該頁匯入程式](https://dh.chinese-empires.eu/markus/beta/fileManagement.html)
- [DocuSky 文本批次標記工具規格](https://docusky.org.tw/DocuSky/docuTools/ContentTaggingTool/index.html)

本轉檔針對上述工具的詞表輸入，不是已標記的 MARKUS 全文或 DocuXml 建庫檔。
