# 協作與發布

1. 建立功能分支，例如 `fix/xrd-reader`。
2. 修改原始碼，補上能驗證實際資料規則的測試。
3. 執行 `python -m unittest discover -s tests -v` 及 `build.ps1`。
4. 建立 Pull Request，說明修改的行為、測試結果與相容性。
5. 由維護者檢查後合併。更動 Origin 寫入流程時，須在已安裝 Origin 的 Windows 電腦重新輸出並重開 OPJU 驗證。

私人儲存庫僅供受邀者存取。有寫入權限的合作者可以推送分支；請使用 Pull Request 合併。此文件本身不會強制限制主分支，維護者可依帳號方案與需求另設分支規則。

## 發布版本

更新 `versions.json` 的版本號後建置。將 `dist/` 中的 ZIP 上傳為 GitHub Release 附件，並描述這一版的修改及已執行的實機檢查。

請勿將 `runtime/`、實驗輸入、OPJU 測試成果、登入憑證或私人電子郵件提交到 Git。Git 提交身分建議使用 GitHub 的 `noreply` 電子郵件。

目前下載版不會自動更新。新的程式碼合併後，仍須建置並發布新版 ZIP，使用者再下載使用。
