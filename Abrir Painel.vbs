Dim objShell, strDir, objHTTP, blnRodando

Set objShell = CreateObject("WScript.Shell")
strDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

' Verifica se o painel já está rodando
blnRodando = False
On Error Resume Next
Set objHTTP = CreateObject("MSXML2.XMLHTTP")
objHTTP.open "GET", "http://localhost:8000/api/status", False
objHTTP.send
blnRodando = (Err.Number = 0 And objHTTP.status = 200)
On Error GoTo 0

If Not blnRodando Then
    ' Instala dependências e inicia o painel sem mostrar terminal
    objShell.Run "cmd /c cd /d """ & strDir & """ && py -m pip install -r requirements.txt -q && py panel.py", 0, False
    WScript.Sleep 4000
End If

' Abre o painel no navegador padrão
objShell.Run "http://localhost:8000"
