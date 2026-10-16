Origin Template Studio 1.0
==========================

使用方式
1. 將 ZIP 完整解壓縮，保留整個資料夾結構。
2. 雙擊 OriginTemplateStudio.exe。
3. 選擇第 1–11 頁圖型、輸入資料及樣品名稱，儲存為 .opju。
4. 如需桌面捷徑，雙擊 CreateDesktopShortcut.vbs。

系統需求
Windows 64 位元、.NET Framework 4.8，以及已安裝並可正常啟用的 64 位元 Origin。
已附獨立 Python 3.12 與必要套件，不需另外安裝 Python 或 Codex。
請勿單獨移動 EXE；移動程式時請搬移整個資料夾。
本版本已在 Origin 2021 64 位元環境驗證。

主要功能
- 儲存可編輯的 Origin .opju 專案。
- 所有樣品名稱預設為檔案名稱（不含副檔名），可手動修改。
- 預設儲存至目前使用者的桌面。
- 第 1–11 頁文字粗體、圖頁與圖層背景為實心白色。
- XRD 支援 Rigaku RAS_RAW TXT；參考圖譜支援 2-Theta / d(A) / I(f) TXT。
- XRD1、XRD2、XRD3 的 X 軸固定為 10–80°。
- 第四頁使用單一資料檔，從選定工作表擷取前三圈。
- Current > 0 為充電，Current < 0 為放電，Current = 0 排除。
- 不加入非零 Current 前後的 0 電流資料，不將起點容量歸零。
- 第四頁 X 軸為比容量 (mAh/g)，Y 軸為電壓 (V)。
- 重量輸入單位為 mg，鋁箔預設為 7.4 mg。
- 活性物質重量(g) = (極片重量mg - 鋁箔重量mg) × 0.8 / 1000。
- 容量單位選 Ah 時：比容量(mAh/g) = capacity × 1000 / 活性物質重量(g)。
- 極片重量必須大於鋁箔重量。

資料及隱私
交付包沒有附使用者的實驗輸入檔、個人測試輸出、舊版程式或操作紀錄。
模板保留原有範例曲線與數值，僅用於保留版型，不代表使用者的新實驗結果。
使用者自行匯入的數據及樣品名稱會保存在輸出的 OPJU 中，分享前請自行確認。
應用程式使用本機檔案與本機 Origin；不含遙測、雲端上傳或帳號登入功能。
第三方套件的公開作者名稱、聯絡方式、版權與授權資訊均保留。
詳見 PRIVACY_CHECK.txt 及 licenses 資料夾。

授權與相依套件
Origin 需另外安裝並使用有效授權，本包不包含 Origin 本體或授權檔。
第三方 Python 套件的授權位於 runtime/Lib/site-packages 各套件的 dist-info 目錄。
