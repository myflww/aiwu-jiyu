Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Environment("PROCESS")("DAFEIYU_HOME") = root
sh.Run Chr(34) & fso.BuildPath(root, "dist\AiwuJiyu.exe") & Chr(34) & " --tray", 0, False
