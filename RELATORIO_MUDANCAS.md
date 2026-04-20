# Relatorio de Mudancas do Projeto

Este arquivo serve como registro para humanos e outras IAs que mexerem no codigo.
Sempre que houver alteracao, adicione uma nova entrada no topo ou logo abaixo desta nota.

## 2026-04-20 - Correcao do crash ao publicar no Mercadoi

### Contexto do projeto

O projeto e um bot Python para cadastrar anuncios imobiliarios no Mercadoi a partir de links do Instagram.
O fluxo principal e:

1. Ler links pendentes no banco local SQLite.
2. Extrair texto do post do Instagram.
3. Gerar dados estruturados do imovel via DeepSeek API ou fallback no DeepSeek pelo navegador.
4. Baixar imagens/video via FastDL.
5. Preencher o formulario do Mercadoi e salvar como rascunho.
6. Atualizar status e logs em tempo real no painel local FastAPI.

### Problema observado

Ao chegar na etapa `Publicando no Mercadoi...`, o bot falhava com:

```text
BrowserType.launch_persistent_context: Target page, context or browser has been closed
ValueError: I/O operation on closed pipe
```

Os logs mostravam que a extracao pelo DeepSeek e o download das imagens funcionavam. A falha acontecia quando o `MercadoiDriver` tentava abrir Chromium com:

```text
--user-data-dir=C:\chrome_bot_mercadoi --remote-debugging-pipe
```

### Causa provavel

O proprio `main.py` exige que o Chrome do Mercadoi esteja aberto na porta `9222`, mas o `MercadoiDriver` tentava abrir outro Chromium com o mesmo perfil persistente `C:\chrome_bot_mercadoi`.

Isso cria conflito de perfil: dois processos tentando usar o mesmo `user-data-dir`. O Chromium encerrava logo apos abrir, e o Python/Playwright gerava os erros secundarios de pipe fechado.

### Mudanca feita

Arquivo alterado:

- `modules/mercadoi_driver.py`

Antes:

- Iniciava um novo contexto persistente com `launch_persistent_context(...)`.
- Usava o mesmo perfil do Chrome ja aberto.
- Fechava o contexto no final.

Depois:

- Conecta ao Chrome ja aberto via `connect_over_cdp("http://localhost:9222")`.
- Reutiliza o contexto existente do navegador logado.
- Nao fecha o contexto no `__aexit__`, pois ele pertence ao Chrome do usuario.
- Apenas para a instancia interna do Playwright.

### Validacoes realizadas

- `python -B -c "from modules.mercadoi_driver import MercadoiDriver; print('mercadoi driver import ok')"`
- `python -B -c "import main, panel; from modules.mercadoi_driver import MercadoiDriver; print('imports ok')"`
- `python -B main.py` com fila vazia.
- Chrome aberto com `--remote-debugging-port=9222`.
- Teste real de conexao do `MercadoiDriver` ao Chrome aberto retornou `connected True`.
- A porta `http://localhost:9222/json/version` continuou respondendo depois do teste, indicando que o driver nao derrubou o Chrome.

### Ambiente corrigido

Tambem foi instalado `python-multipart`, que ja estava em `requirements.txt`, mas faltava no ambiente atual. Sem ele, o `panel.py` falhava ao importar rotas com `Form(...)` do FastAPI.

Comando usado:

```powershell
pip install -r requirements.txt
```

### Problemas/riscos encontrados no codigo

- O projeto nao estava em Git antes desta intervencao.
- O arquivo `config.json` contem chave da API DeepSeek e nao deve ser versionado.
- `credentials.json`, banco SQLite, logs e screenshots podem conter dados sensiveis e nao devem ir para o Git.
- Ha arquivos estranhos na raiz (`=0.27.0` e `=1.30.0`), provavelmente artefatos acidentais de instalacao. Foram ignorados no Git.
- O `LEIA-ME.md` aparece com caracteres quebrados em algumas leituras de terminal, possivelmente por diferenca de encoding. Nao alterei isso para evitar mexer em documentacao fora do escopo.
- O fallback DeepSeek ainda usa `launch_persistent_context` com o perfil `C:\chrome_bot_deepseek`. Isso e aceitavel se nao houver outro Chrome usando esse perfil ao mesmo tempo, mas pode gerar problema parecido caso o perfil esteja aberto manualmente.
- `MediaResolver` abre Chromium separado para FastDL. Nao foi alterado porque nao usa o mesmo perfil persistente do Mercadoi.
- O painel filtra alguns ruidos de Playwright no log ao vivo, mas o erro real ainda fica no arquivo de log e no banco.

### Orientacao para a proxima IA

- Nao reverter a troca para `connect_over_cdp`; ela e coerente com o fluxo do painel e do `main.py`.
- Antes de publicar no Mercadoi, garantir que o Chrome esteja aberto na porta `9222`.
- Nao versionar `config.json`, `credentials.json`, banco, logs ou screenshots.
- Se alterar automacao de navegador, preservar a separacao:
  - Mercadoi: conectar no Chrome ja aberto/logado via CDP.
  - DeepSeek fallback: pode abrir perfil proprio, desde que ele nao esteja em uso.
  - FastDL: pode usar navegador temporario sem perfil persistente.

## Como registrar proximas alteracoes

Use este formato:

```markdown
## AAAA-MM-DD - Titulo curto da mudanca

### Mudanca feita
- Arquivos alterados.
- Resumo objetivo.

### Por que foi feito
- Problema ou necessidade.

### Validacoes
- Comandos/testes executados.

### Riscos ou pendencias
- O que ainda pode quebrar ou precisa de atencao.
```
