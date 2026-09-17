' ==============================================================
'  DeskPet launcher (silent, no console window)
'
'  IMPORTANT: keep this file ASCII-ONLY.
'  Windows Script Host reads .vbs as ANSI using the system
'  codepage. Non-ASCII bytes would be garbled, and a garbled
'  string literal can break the script. All Chinese messages
'  come from launch.py, which uses the Unicode MessageBoxW API.
'
'  This script only starts launch.py and, if that fails, shows
'  an error dialog instead of silently doing nothing.
' ==============================================================
Option Explicit

Dim fso, shell, root, py, cmd, rc

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)

' Prefer the project virtual environment; fall back to any python.
py = root & "\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then
    ' No venv yet: run the bootstrapper so the user can see progress.
    If fso.FileExists(root & "\start.bat") Then
        shell.CurrentDirectory = root
        rc = shell.Run("""" & root & "\start.bat""", 1, True)
        WScript.Quit rc
    End If
    MsgBox "Runtime not found." & vbCrLf & vbCrLf & _
           "Install Python 3.11+ from https://www.python.org/downloads/" & vbCrLf & _
           "then run start.bat once.", 48, "DeskPet"
    WScript.Quit 1
End If

If Not fso.FileExists(root & "\launch.py") Then
    MsgBox "launch.py is missing next to start.vbs.", 48, "DeskPet"
    WScript.Quit 1
End If

shell.CurrentDirectory = root
cmd = """" & py & """ """ & root & "\launch.py"""
' Window style 0 = hidden. launch.py starts the GUI with pythonw
' and pops up a dialog itself if anything goes wrong.
shell.Run cmd, 0, False
WScript.Quit 0
