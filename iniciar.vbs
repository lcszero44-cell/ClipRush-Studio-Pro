Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strBase = "D:\ClipRush_Pro_V2"
strPyW = strBase & "\.venv\Scripts\pythonw.exe"
strLauncher = strBase & "\tray_launcher.py"
strBat = strBase & "\iniciar.bat"

objShell.CurrentDirectory = strBase

If objFSO.FileExists(strPyW) And objFSO.FileExists(strLauncher) Then
    objShell.Run Chr(34) & strPyW & Chr(34) & " " & Chr(34) & strLauncher & Chr(34), 0, False
Else
    objShell.Run Chr(34) & strBat & Chr(34), 0, False
End If
