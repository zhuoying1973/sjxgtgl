Set WshShell = CreateObject("WScript.Shell")
strDesktop = WshShell.SpecialFolders("Desktop")
' Use English filename to avoid encoding issues, user can rename it later
strShortcut = strDesktop & "\ArchVizManager.lnk"
Set oUrlLink = WshShell.CreateShortcut(strShortcut)

Set fso = CreateObject("Scripting.FileSystemObject")
' Get directory where this VBS is located
strPath = fso.GetParentFolderName(WScript.ScriptFullName)

oUrlLink.TargetPath = strPath & "\run_app.bat"
oUrlLink.WorkingDirectory = strPath
oUrlLink.WindowStyle = 1
oUrlLink.Description = "Start ArchViz Business Manager"
oUrlLink.IconLocation = "shell32.dll,3"
oUrlLink.Save()

WScript.Echo "Shortcut created: ArchVizManager.lnk"
