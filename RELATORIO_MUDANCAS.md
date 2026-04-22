# Relatorio de Mudancas do Projeto

Este arquivo serve como registro para humanos e outras IAs que mexerem no codigo.
Sempre que houver alteracao, adicione uma nova entrada no topo ou logo abaixo desta nota.

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
