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
    & $pythonExe @pythonArgs -m venv --clear .venv  # 重跑安裝時重建，避免新舊 Python 版本混在一起
    if ($LASTEXITCODE -ne 0) { throw '無法建立本機虛擬環境。' }
    & '.\.venv\Scripts\python.exe' -m pip install -c constraints.txt .
    if ($LASTEXITCODE -ne 0) { throw ('套件安裝失敗（Python ' + $pythonVersion + '）。請確認公司網路或套件來源；若訊息寫某個套件沒有適合這個環境的版本，請把整個畫面交給維護人員。') }
    & '.\.venv\Scripts\python.exe' -c "import tkinter, sp_workflow.app, sp_workflow.word_worker, win32com.client, psycopg, fcn_checker.panel"
    if ($LASTEXITCODE -ne 0) { throw '安裝驗證失敗。' }
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
