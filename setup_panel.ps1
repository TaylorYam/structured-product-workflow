$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonExe = $pythonCommand.Source; $pythonArgs = @('-3') }
    else { $pythonExe = (Get-Command python -ErrorAction Stop).Source; $pythonArgs = @() }
    & $pythonExe @pythonArgs -c "import sys, tkinter; assert sys.version_info >= (3,11), 'Python 3.11 or later required'"
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11 以上及 Tcl/Tk。' }
    $pythonVersion = (& $pythonExe @pythonArgs -c "import sys, platform; print(sys.version.split()[0], platform.machine(), sys.executable)" | Out-String).Trim()
    Write-Host ('使用 Python ' + $pythonVersion)
    # 顯示裝的是哪個版本的程式（#136）：公司電腦停在功能 branch 時，git pull 拉的不是 main，重裝也看不到新功能。
    # 找不到 git、不是 git repo（例如用 ZIP 複製）或沒有 origin/main 時整段略過；只提醒、不中止，也不自己 fetch／pull／checkout。
    try {
        if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.git'))) {
            $branch = (& git rev-parse --abbrev-ref HEAD | Out-String).Trim()
            $commit = (& git log -1 --format=%h | Out-String).Trim()
            $commitDate = (& git log -1 --format=%cs | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $branch -and $commit) {
                Write-Host ('程式版本：' + $branch + ' ' + $commit + '（' + $commitDate + '）')
                if ($branch -ne 'main') {
                    Write-Host ('提醒：目前不在 main（在 ' + $branch + '），裝的不是最新版。要更新請先在這個資料夾執行 git checkout main、git pull，再重跑安裝。')
                } else {
                    & git rev-parse --verify --quiet origin/main | Out-Null
                    if ($LASTEXITCODE -eq 0) {
                        $behind = (& git rev-list --count HEAD..origin/main | Out-String).Trim()
                        if ($LASTEXITCODE -eq 0 -and $behind -match '^\d+$' -and [int]$behind -gt 0) {
                            Write-Host ('提醒：本機 main 落後 origin/main ' + $behind + ' 個 commit，請先 git pull 再重跑安裝。')
                        }
                    }
                }
            }
        }
    } catch {
        # 版本提示只是提醒，失敗不影響安裝
    }
    # FCN Workflow 開著時 .venv 的 DLL 被鎖住，--clear 會刪到一半失敗（#110）。
    # .venv\Scripts\python(w).exe 會再開系統 Python 的子程序，子程序的命令列仍以 .venv 路徑開頭，所以兩者都要比對
    $venvPrefix = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.venv')).TrimEnd('\') + '\'
    $venvInUse = @(Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($venvPrefix, [StringComparison]::OrdinalIgnoreCase)) -or
        ($_.Name -like 'python*' -and $_.CommandLine -and $_.CommandLine.TrimStart('"', ' ').StartsWith($venvPrefix, [StringComparison]::OrdinalIgnoreCase))
    })
    if ($venvInUse.Count -gt 0) {
        throw ('FCN Workflow（或其他用本專案 .venv 執行的 Python）還開著，請先關閉視窗再重新執行安裝。若視窗都已關閉仍出現此訊息，請到工作管理員「詳細資料」結束以下程序：' + (($venvInUse | ForEach-Object { $_.Name + ' (PID ' + $_.ProcessId + ')' }) -join '、'))
    }
    & $pythonExe @pythonArgs -m venv --clear .venv  # 重跑安裝時重建，避免新舊 Python 版本混在一起
    if ($LASTEXITCODE -ne 0) { throw '無法建立本機虛擬環境。' }
    & '.\.venv\Scripts\python.exe' -m pip install -c constraints.txt .
    if ($LASTEXITCODE -ne 0) { throw ('套件安裝失敗（Python ' + $pythonVersion + '）。請確認公司網路或套件來源；若訊息寫某個套件沒有適合這個環境的版本，請把整個畫面交給維護人員。') }
    & '.\.venv\Scripts\python.exe' -c "import tkinter, sp_workflow.app, sp_workflow.word_worker, win32com.client, psycopg, fcn_checker.panel"
    if ($LASTEXITCODE -ne 0) { throw '安裝驗證失敗。' }
    # Match the app's install-local settings instead of the packaged CLI's home-directory default.
    & '.\.venv\Scripts\spw-db-migrate.exe' --env-file (Join-Path $PSScriptRoot '.env') --admin-user postgres
    if ($LASTEXITCODE -ne 0) { throw '資料庫遷移失敗；請確認安裝資料夾的 .env、PostgreSQL 連線及管理者權限後重試。' }
    # 核對紀錄記下程式版本用（.local\installed.json）
    & '.\.venv\Scripts\python.exe' -m fcn_checker.version --record-installation $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw '安裝版本紀錄失敗。' }
    # 只建一個「FCN Workflow」捷徑，開合併後的視窗（#77）；沿用核對的圖示
    $openWith = 'launch_panel.cmd'
    try {
        # 桌面與專案資料夾各放一個捷徑；捷徑記錄本機完整路徑，不進 Git
        $shell = New-Object -ComObject WScript.Shell
        $projectPrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
        foreach ($folder in @([Environment]::GetFolderPath('Desktop'), $PSScriptRoot)) {
            $shortcut = $shell.CreateShortcut((Join-Path $folder 'FCN Workflow.lnk'))
            $shortcut.TargetPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
            $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'launch_panel.pyw') + '"'
            $shortcut.WorkingDirectory = $PSScriptRoot
            $shortcut.Description = 'FCN Workflow'
            $shortcut.IconLocation = (Join-Path $PSScriptRoot 'src\fcn_checker\assets\panel.ico') + ',0'
            $shortcut.Save()
            # 合併前另建的「TS IIS 準備與放回」：只刪指向本專案資料夾的那一個，指向別處的不動；刪不掉不影響新捷徑
            $oldShortcut = Join-Path $folder 'TS IIS 準備與放回.lnk'
            try {
                if (Test-Path -LiteralPath $oldShortcut) {
                    $target = $shell.CreateShortcut($oldShortcut).TargetPath
                    if ($target -and [IO.Path]::GetFullPath($target).StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                        Remove-Item -LiteralPath $oldShortcut
                        Write-Host ('已刪除舊捷徑：' + $oldShortcut)
                    }
                }
            } catch {
                Write-Host ('舊捷徑沒有刪掉，請手動刪除：' + $oldShortcut + '（' + $_.Exception.Message + '）')
            }
        }
        $openWith = '桌面或專案資料夾裡的「FCN Workflow」捷徑'
    } catch {
        Write-Host ('捷徑建立失敗（不影響安裝）：' + $_.Exception.Message)
    }
    Write-Host ('安裝完成。請雙擊 ' + $openWith + ' 開啟視窗。')
    exit 0
} catch {
    Write-Host ('安裝未完成：' + $_.Exception.Message)
    exit 1
}
