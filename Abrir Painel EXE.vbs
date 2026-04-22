'==============================================================================
'  Bot Mercadoi — Iniciador do executavel protegido
'  Use este launcher para distribuicao com dist\BotMercadoi.exe.
'==============================================================================
Option Explicit

Dim objShell, objFSO, strDir, strExe, strLogs

Set objShell = CreateObject("WScript.Shell")
Set objFSO   = CreateObject("Scripting.FileSystemObject")

strDir  = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
strExe  = strDir & "\dist\BotMercadoi.exe"
strLogs = strDir & "\dist\logs"

If PainelRodando() Then
    objShell.Run "http://localhost:8000"
    WScript.Quit 0
End If

If Not objFSO.FileExists(strExe) Then
    MsgBox "Executavel nao encontrado:" & vbCrLf & strExe & vbCrLf & vbCrLf & _
           "Rode build_exe.ps1 primeiro.", vbCritical, "Bot Mercadoi"
    WScript.Quit 1
End If

If Not objFSO.FolderExists(strLogs) Then
    objFSO.CreateFolder strLogs
End If

objShell.Run """" & strExe & """", 0, False

Dim i, blnOk
blnOk = False
For i = 1 To 120
    WScript.Sleep 1000
    If PainelRodando() Then
        blnOk = True
        Exit For
    End If
Next

If Not blnOk Then
    MsgBox "O painel nao respondeu apos 25 segundos." & vbCrLf & _
           "Verifique se dist\config.json existe e se a porta 8000 esta livre.", _
           vbExclamation, "Bot Mercadoi"
    WScript.Quit 1
End If

objShell.Run "http://localhost:8000"
WScript.Quit 0

Function PainelRodando()
    Dim obj
    On Error Resume Next
    Set obj = CreateObject("MSXML2.XMLHTTP")
    obj.open "GET", "http://localhost:8000/login", False
    obj.send
    PainelRodando = (Err.Number = 0 And obj.status = 200)
    On Error GoTo 0
End Function
