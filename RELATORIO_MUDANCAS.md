# Relatorio de Mudancas do Projeto

Este arquivo serve como registro para humanos e outras IAs que mexerem no codigo.
Sempre que houver alteracao, adicione uma nova entrada no topo ou logo abaixo desta nota.

## 2026-04-21 - Empacotamento em executavel Windows

### Mudancas feitas

Arquivos alterados/criados:

- `panel.py`
- `main.py`
- `modules/licensing.py`
- `botmercadoi.spec`
- `build_exe.ps1`
- `build_hooks/hook-panel.py`
- `Abrir Painel EXE.vbs`
- `DISTRIBUICAO_EXE.md`
- `.gitignore`

Resumo:

- Criado build PyInstaller para gerar `dist\BotMercadoi.exe`.
- O executavel usa o proprio binario como painel e como worker do bot:
  - painel: `BotMercadoi.exe`
  - processamento interno: `BotMercadoi.exe --bot-main`
- Quando congelado em `.exe`, `config.json`, banco, logs e cache de licenca ficam ao lado do executavel, nao dentro da pasta temporaria do PyInstaller.
- Os arquivos HTML do painel passam a ser carregados de `sys._MEIPASS` no executavel e da pasta do projeto no modo desenvolvimento.
- Criado `Abrir Painel EXE.vbs` para abrir o executavel escondido e aguardar o painel subir.
- Criado `build_exe.ps1` para instalar PyInstaller e gerar o executavel.
- Criado `DISTRIBUICAO_EXE.md` com orientacao do que entregar ao cliente.
- Criado hook local `build_hooks/hook-panel.py` para evitar conflito com o hook da biblioteca externa `panel`.

### Validacoes

- `python -B -m py_compile .\main.py .\panel.py .\modules\licensing.py`
- `python -B panel.py --bot-main`
- `powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean`
- `dist\BotMercadoi.exe --bot-main`
- Inicializacao real de `dist\BotMercadoi.exe` e validacao de resposta em `http://127.0.0.1:8000/login` com status `200`.

### Problemas/riscos encontrados no codigo

- O executavel gerado ficou grande, cerca de 338 MB, porque o ambiente Python atual tem muitas bibliotecas instaladas e o PyInstaller ainda puxou dependencias pesadas.
- O modo `onefile` demora perto de 1 minuto para iniciar porque precisa extrair o pacote antes de subir o painel.
- Para distribuicao comercial mais refinada, o ideal e criar um ambiente virtual limpo so com as dependencias do projeto e refazer o build.
- Uma proxima camada de protecao seria usar Nuitka e assinar digitalmente o executavel.

## 2026-04-21 - Licenciamento online por chave e maquina

### Mudancas feitas

Arquivos alterados/criados:

- `main.py`
- `panel.py`
- `modules/licensing.py`
- `license_server/app.py`
- `license_server/licenses.example.json`
- `config.example.json`
- `.gitignore`

Resumo:

- Criada camada de licenciamento online em `modules/licensing.py`.
- O bot agora verifica a licenca no inicio de cada ciclo, antes de processar links.
- A validacao usa:
  - `licenciamento_habilitado`
  - `licenca_chave`
  - `licenca_servidor_url`
  - identificador hash da maquina
- Se a licenca estiver invalida, expirada, sem servidor ou em maquina nao autorizada, o bot nao processa a fila.
- Quando o licenciamento esta desligado, o comportamento atual permanece igual para desenvolvimento/testes.
- Criado servidor simples de licencas em `license_server/app.py`.
- O servidor:
  - cria chaves via rota admin;
  - valida chaves em `/validate`;
  - amarra automaticamente a primeira maquina autorizada;
  - bloqueia maquinas adicionais acima do limite;
  - respeita data de expiracao.
- O painel ganhou endpoint admin `/api/licenca/status` para consultar status, cliente, origem da validacao e ID curto da maquina.
- `config.example.json` ganhou os campos de licenciamento.
- `.gitignore` passou a ignorar `license_cache.json` e `license_server/licenses.json`, pois sao dados locais/sensiveis.

### Por que foi feito

- Entregar o projeto Python aberto para cliente permite copia facil dos arquivos.
- Marcar arquivos como ocultos no Windows nao protege contra copia.
- A nova camada dificulta uso nao autorizado porque o bot precisa consultar um servidor controlado pelo dono antes de processar.

### Validacoes

- `python -B -m py_compile .\main.py .\panel.py .\modules\licensing.py .\license_server\app.py`
- `python -B -c "import json; json.load(open('config.example.json', encoding='utf-8')); print('config example ok')"`
- `python -B -c "from modules.licensing import machine_id; print(machine_id()[:12])"`
- `python -B main.py` com licenciamento desligado, confirmando que o ambiente atual continua rodando.
- Teste via `fastapi.testclient`:
  - criar licenca admin retornou `200 True`;
  - validar primeira maquina retornou `active=True`;
  - validar segunda maquina com limite `1` retornou `Limite de maquinas atingido`.

### Problemas/riscos encontrados no codigo

- Esta protecao ainda nao substitui empacotamento em `.exe`; o proximo passo recomendado e gerar build com Nuitka ou PyInstaller.
- O cache local de licenca serve para tolerar instabilidade temporaria do servidor, mas nao deve ser tratado como protecao inviolavel.
- A protecao mais forte vem da combinacao: servidor online + executavel compilado/ofuscado + remocao de segredos do pacote entregue.
- O painel ainda possui uma licenca local antiga por data/assinatura; ela foi preservada para nao quebrar compatibilidade, mas a camada comercial principal deve ser a nova licenca online.
