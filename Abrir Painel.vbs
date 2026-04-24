'==============================================================================
'  Bot Mercadoi — Iniciador
'  Duplo-clique para abrir o painel de controle no navegador.
'
'  Na primeira execucao instala automaticamente todas as dependencias.
'  Nas proximas, abre o painel em menos de 2 segundos.
'==============================================================================
Option Explicit

Dim objShell, objFSO, strDir, strLogs, strFlag

Set objShell = CreateObject("WScript.Shell")
Set objFSO   = CreateObject("Scripting.FileSystemObject")

' Diretorio do script (sem barra final)
strDir  = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
strLogs = strDir & "\logs"
strFlag = strDir & "\.installed"

' === 1. Painel ja esta no ar? Abre o navegador e sai =========================
If PainelRodando() Then
    objShell.Run "http://localhost:8000"
    WScript.Quit 0
End If

' === 2. Verifica Python e captura o executavel exato =========================
If objShell.Run("py --version", 0, True) <> 0 Then
    MsgBox "Python nao foi encontrado." & vbCrLf & vbCrLf & _
           "Instale o Python 3.10+ em:" & vbCrLf & _
           "  https://www.python.org/downloads/" & vbCrLf & vbCrLf & _
           "IMPORTANTE: marque 'Add Python to PATH' durante a instalacao.", _
           vbCritical, "Bot Mercadoi — Configuracao necessaria"
    WScript.Quit 1
End If

' Captura o caminho exato do Python que 'py' usa (garante consistencia pip/panel)
Dim oExec, strPython
Set oExec = objShell.Exec("py -c ""import sys; print(sys.executable)""")
strPython = Trim(oExec.StdOut.ReadAll())
If strPython = "" Then strPython = "py"

' === 3. Cria pasta de logs ====================================================
If Not objFSO.FolderExists(strLogs) Then objFSO.CreateFolder strLogs

' === 4. Primeira execucao: instala dependencias com janela de progresso ========
If Not objFSO.FileExists(strFlag) Then

    ' Cria batch de instalacao com progresso visivel para o cliente
    Dim strBatch
    strBatch = strLogs & "\_instalar.bat"
    Dim fBat : Set fBat = objFSO.CreateTextFile(strBatch, True)
    fBat.WriteLine "@echo off"
    fBat.WriteLine "title Bot Mercadoi - Configuracao inicial"
    fBat.WriteLine "color 0A"
    fBat.WriteLine "echo."
    fBat.WriteLine "echo  ======================================================"
    fBat.WriteLine "echo    Bot Mercadoi - Configuracao do sistema"
    fBat.WriteLine "echo  ======================================================"
    fBat.WriteLine "echo."
    fBat.WriteLine "echo  Bem-vindo! Esta e a primeira vez que o bot e iniciado."
    fBat.WriteLine "echo  As dependencias serao instaladas automaticamente."
    fBat.WriteLine "echo."
    fBat.WriteLine "echo  NAO feche esta janela - isso pode levar alguns minutos."
    fBat.WriteLine "echo."
    fBat.WriteLine "echo  [1/3] Atualizando instalador Python..."
    fBat.WriteLine """" & strPython & """ -m pip install --upgrade pip -q >> """ & strLogs & "\setup.log"" 2>&1"
    fBat.WriteLine "if errorlevel 1 (echo  AVISO: falha ao atualizar pip, continuando...) else (echo  [1/3] OK)"
    fBat.WriteLine "echo."
    fBat.WriteLine "echo  [2/3] Instalando dependencias do bot..."
    fBat.WriteLine """" & strPython & """ -m pip install -r """ & strDir & "\requirements.txt"" -q >> """ & strLogs & "\setup.log"" 2>&1"
    fBat.WriteLine "if errorlevel 1 (echo  ERRO: falha ao instalar dependencias. Veja logs\setup.log) else (echo  [2/3] OK)"
    fBat.WriteLine "echo."
    fBat.WriteLine "echo  [3/3] Baixando navegador interno (pode demorar 2-3 minutos)..."
    fBat.WriteLine """" & strPython & """ -m playwright install chromium >> """ & strLogs & "\setup.log"" 2>&1"
    fBat.WriteLine "if errorlevel 1 (echo  AVISO: falha ao instalar navegador) else (echo  [3/3] OK)"
    fBat.WriteLine "echo."
    fBat.WriteLine "echo  ======================================================"
    fBat.WriteLine "echo    Configuracao concluida! Abrindo o painel..."
    fBat.WriteLine "echo  ======================================================"
    fBat.WriteLine "timeout /t 3 /nobreak > nul"
    fBat.Close

    ' Roda o batch em janela visivel e aguarda terminar
    objShell.Run "cmd /c """ & strBatch & """", 1, True

    ' Remove o batch temporario
    If objFSO.FileExists(strBatch) Then objFSO.DeleteFile strBatch

    ' Marca como instalado
    Dim f : Set f = objFSO.CreateTextFile(strFlag, True)
    f.WriteLine Now()
    f.Close
End If

' === 5. Inicia o painel (sem janela de terminal) ==============================
Dim strCmd
strCmd = "cmd /c cd /d """ & strDir & """ && " & _
         """" & strPython & """ panel.py >> """ & strLogs & "\painel.log"" 2>&1"
objShell.Run strCmd, 0, False   ' 0 = sem janela, False = nao aguarda

' === 6. Aguarda o painel responder mostrando progresso =======================
Dim strBat2
strBat2 = strLogs & "\_aguardar.bat"
Dim fAg : Set fAg = objFSO.CreateTextFile(strBat2, True)
fAg.WriteLine "@echo off"
fAg.WriteLine "title Bot Mercadoi - Iniciando"
fAg.WriteLine "color 0B"
fAg.WriteLine "echo."
fAg.WriteLine "echo  ======================================================"
fAg.WriteLine "echo    Bot Mercadoi - Iniciando o painel"
fAg.WriteLine "echo  ======================================================"
fAg.WriteLine "echo."
fAg.WriteLine "echo  Aguarde enquanto o sistema carrega..."
fAg.WriteLine "echo  Esta janela fecha automaticamente."
fAg.WriteLine "echo."
fAg.WriteLine "echo  Log de inicializacao:"
fAg.WriteLine "echo  " & strLogs & "\painel.log"
fAg.WriteLine "echo."
fAg.Close
objShell.Run "cmd /c """ & strBat2 & """", 1, False  ' abre em paralelo, nao aguarda

Dim i, blnOk
blnOk = False
For i = 1 To 90
    WScript.Sleep 1000
    If PainelRodando() Then
        blnOk = True
        Exit For
    End If
Next

' Fecha a janela de espera
objShell.Run "taskkill /fi ""WINDOWTITLE eq Bot Mercadoi - Iniciando"" /f", 0, False
If objFSO.FileExists(strBat2) Then objFSO.DeleteFile strBat2

If Not blnOk Then
    ' Le ultimas linhas do log para ajudar no diagnostico
    Dim strUltimasLinhas : strUltimasLinhas = ""
    Dim strLogPath : strLogPath = strLogs & "\painel.log"
    If objFSO.FileExists(strLogPath) Then
        Dim ts : Set ts = objFSO.OpenTextFile(strLogPath, 1)
        Dim strTudo : strTudo = ts.ReadAll()
        ts.Close
        Dim arrL : arrL = Split(strTudo, vbNewLine)
        Dim nL : nL = UBound(arrL)
        Dim ini : ini = nL - 8
        If ini < 0 Then ini = 0
        Dim k
        For k = ini To nL
            If Trim(arrL(k)) <> "" Then
                strUltimasLinhas = strUltimasLinhas & arrL(k) & vbCrLf
            End If
        Next
    End If
    MsgBox "O painel nao respondeu a tempo." & vbCrLf & vbCrLf & _
           "Tente abrir novamente clicando duas vezes no arquivo." & vbCrLf & vbCrLf & _
           "Ultimas linhas do log:" & vbCrLf & _
           strUltimasLinhas, _
           vbExclamation, "Bot Mercadoi — Erro ao iniciar"
    WScript.Quit 1
End If

' === 7. Abre o navegador ======================================================
objShell.Run "http://localhost:8000"
WScript.Quit 0


'==============================================================================
' Funcoes auxiliares
'==============================================================================

Function PainelRodando()
    Dim obj
    On Error Resume Next
    Set obj = CreateObject("MSXML2.XMLHTTP")
    obj.open "GET", "http://localhost:8000/api/health", False
    obj.send
    PainelRodando = (Err.Number = 0 And obj.status = 200)
    On Error GoTo 0
End Function

Sub Executar(strComando)
    ' Roda o comando no diretorio do projeto e redireciona saida para o log
    Dim strFull
    strFull = "cmd /c cd /d """ & strDir & """ && " & strComando & _
              " >> """ & strLogs & "\setup.log"" 2>&1"
    objShell.Run strFull, 0, True   ' True = aguarda terminar
End Sub
