try {
    $WshShell = New-Object -comObject WScript.Shell
    $DesktopPath = $WshShell.SpecialFolders.Item("Desktop")
    $ShortcutPath = Join-Path $DesktopPath "建筑业务管理.lnk"
    
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    
    # 指向我们刚刚创建的 run_app.bat
    $Shortcut.TargetPath = "$PSScriptRoot\run_app.bat"
    $Shortcut.WorkingDirectory = "$PSScriptRoot"
    
    # 窗口样式: 7 = 最小化, 1 = 正常, 3 = 最大化
    $Shortcut.WindowStyle = 1 
    
    $Shortcut.Description = "启动建筑效果图业务管理系统"
    
    # 查找 Edge 路径作为图标
    $EdgePath = (Get-Command msedge -ErrorAction SilentlyContinue).Source
    if ($EdgePath) {
        $Shortcut.IconLocation = $EdgePath + ",0"
    }
    else {
        $Shortcut.IconLocation = "shell32.dll,3"
    }
    
    $Shortcut.Save()
    
    Write-Host -ForegroundColor Green "成功！快捷方式已创建在桌面: $ShortcutPath"
    Write-Host "您可以直接双击它来启动系统。"
}
catch {
    Write-Host -ForegroundColor Red "创建快捷方式失败: $_"
    exit 1
}
