Option Explicit
Dim shell, fso, appFolder, link
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appFolder = fso.GetParentFolderName(WScript.ScriptFullName)
Set link = shell.CreateShortcut(shell.SpecialFolders("Desktop") & "\Origin Template Studio.lnk")
link.TargetPath = appFolder & "\OriginTemplateStudio.exe"
link.WorkingDirectory = appFolder
link.IconLocation = appFolder & "\OriginTemplateStudio.exe,0"
link.Description = "Create editable Origin OPJU projects"
link.Save
MsgBox "Desktop shortcut created.", 64, "Origin Template Studio"
