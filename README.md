# Origin Template Studio

Windows 應用程式，將實驗資料寫入 11 種 Origin 母版並儲存為可編輯的 `.opju`。

## 使用應用程式

從本儲存庫的 **Releases** 下載 Windows x64 ZIP，完整解壓縮後執行 `OriginTemplateStudio.exe`。可用 `CreateDesktopShortcut.vbs` 建立桌面捷徑。

需要已安裝且可正常啟用的 **Origin 64 位元**；本機已以 Origin 2021 驗證。發行包內附 Python，不需安裝 Codex。請保留整個資料夾。

目前沒有自動更新功能。發布新版後，使用者須下載新 ZIP；GitHub 上的程式碼更新不會直接修改已下載的應用程式。

## 功能

- XRD1、XRD2、XRD3、Charge and Discharge、C-rate、Cycling、CV、Excitation、Emission、FTIR、Particle Size。
- 以檔名作為樣品名稱預設值，可手動修改。
- 預設將 OPJU 儲存至目前使用者桌面。
- 所有頁面文字與刻度為粗體，圖頁與圖層為實心白底。
- XRD 支援 Rigaku RAS_RAW TXT；PDF 圖譜使用第一欄 `2-Theta` 及第三欄 `I(f)`。
- 充放電使用一個檔案及一張工作表，以 `Cycle_Index` 選前三圈，以 Current 正負判斷方向，排除 0 電流資料。
- 充放電 X 軸為比容量、Y 軸為電壓。重量輸入使用 mg，鋁箔預設 7.4 mg。
- 詳細操作與計算規則見 [使用說明](docs/README.txt)。

## 從原始碼建置

需要 Windows、Python 3.12 x64 與 .NET Framework 4.8。

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
powershell -NoProfile -File .\build.ps1
```

應用程式與 ZIP 會放在 `dist/`。建置不需要 Origin；實際輸出及完整實機驗證需要 Origin。

## 開發與更新

```text
src/OriginTemplateStudio.cs       Windows Forms 介面
python_origin_plotter/            Origin 引擎、資料解析與圖型設定
templates/                       已清理來源路徑的 OPJ 母版
tools/package.py                 可攜式應用程式打包
tests/                          離線資料解析測試
```

合作者可以透過分支及 Pull Request 提出修改。維護流程見 [CONTRIBUTING.md](CONTRIBUTING.md)。CI 會檢查離線測試並建置 Windows 版本；Origin 的實機測試仍須在已安裝 Origin 的電腦執行。

## 隱私與檔案範圍

儲存庫只包含程式碼、清理後母版、建置工具與說明。使用者實驗輸入、測試輸出、私人路徑及憑證不應提交。母版仍有原有範例曲線，並非使用者後來匯入的新實驗資料。

第三方套件保留原授權及公開作者資訊；Origin 本體及授權檔不隨程式提供。詳見 [隱私檢查說明](docs/PRIVACY_CHECK.txt)。
