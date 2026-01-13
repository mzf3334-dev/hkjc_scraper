# GitHub Actions 自動化設定指南

按照以下步驟將爬蟲部署到 GitHub，實現每日自動抓取：

### 第一步：建立 GitHub 倉庫
1. 在 GitHub 上建立一個新的私有或公開倉庫（例如：`hkjc-scraper`）。
2. 在本地或通過 GitHub 網頁介面，將以下文件上傳到倉庫根目錄：
   - `hkjc_scraper_github.py`
   - `.github/workflows/hkjc_workflow.yml` (注意資料夾結構)

### 第二步：設定權限
為了讓 GitHub Actions 能夠將抓取到的 CSV 文件推送到您的倉庫，您需要確保它有寫入權限：
1. 進入倉庫的 **Settings** > **Actions** > **General**。
2. 捲動到 **Workflow permissions**。
3. 選擇 **Read and write permissions**。
4. 點擊 **Save**。

### 第三步：手動測試
1. 點擊倉庫頂部的 **Actions** 標籤。
2. 在左側選擇 **HKJC Race Results Scraper**。
3. 點擊 **Run workflow** 按鈕手動執行一次。
4. 執行完成後，您應該會在倉庫中看到一個名為 `data/` 的資料夾，裡面存放著 CSV 文件。

### 自動執行說明
- **定時執行**：工作流已設定為每天香港時間 **23:30** 自動運行。
- **自動儲存**：每次運行後，如果有新數據，GitHub Actions 會自動提交 (Commit) 並推送 (Push) 到您的倉庫中。
