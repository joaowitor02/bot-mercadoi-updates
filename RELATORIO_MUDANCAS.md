# Relatorio de Mudancas do Projeto

Este arquivo serve como registro para humanos e outras IAs que mexerem no codigo.
Sempre que houver alteracao, adicione uma nova entrada no topo ou logo abaixo desta nota.

## 2026-04-20 - Validacao de upload e tipo Apartamento no Mercadoi

### Mudancas feitas

Arquivo alterado:

- `modules/mercadoi_driver.py`

Resumo:

- O tipo do imovel no Mercadoi agora e selecionado por `value` conhecido quando possivel:
  - `Apartamento` -> `16`
  - `Casa` -> `53`
  - `Terreno` -> `103`
  - `Sala Comercial` -> `98`
- O upload de midia agora retorna sucesso/falha real para o fluxo principal.
- Se o bot nao conseguir anexar a midia, o item vira `erro_upload` e nao e mais salvo como rascunho sem imagem.
- O upload ganhou fallback: se o botao do Mercadoi nao abrir o seletor de arquivos, o driver tenta enviar direto pelo `input[type="file"]`.
- Antes de salvar o rascunho, o driver passa a esperar a contagem de IDs de imagens do Mercadoi chegar ao total esperado.

### Por que foi feito

- No teste com 5 links, um video chegou a extrair 12 frames, mas o Mercadoi nao abriu o seletor de arquivos naquele momento.
- Mesmo com erro de upload, o fluxo antigo continuava e salvava o rascunho como sucesso, podendo gerar publicacao sem imagem.
- O campo `APARTAMENTO` tambem aparecia como nao selecionado em alguns logs, apesar de existir no formulario.

### Validacoes

- `python -B -m py_compile .\modules\mercadoi_driver.py`
- Foram recolocados 5 links reais na fila: IDs `3`, `53`, `54`, `60` e `61`.
- Resultado do teste:
  - ID `3`: `erro_extracao`, link/reel informado como inacessivel pela IA/browser.
  - ID `53`: `rascunho_salvo`, video processado, `12/12` imagens anexadas, rascunho `https://mercadoi.com.br/?p=14498`.
  - ID `54`: `erro_extracao`, nao foi possivel extrair dados do imovel.
  - ID `60`: `rascunho_salvo`, video processado, `12/12` imagens anexadas, rascunho `https://mercadoi.com.br/?p=14511`.
  - ID `61`: `erro_extracao`, link/post informado como inacessivel pela IA/browser.
- Ao final do teste:
  - `pendente`: `0`
  - `processando`: `0`

### Problemas/riscos encontrados no codigo

- O select `role` ainda mostra aviso `Nao selecionou 'Corretor'`; aparentemente a opcao nao existe com esse texto no formulario atual.
- Em um teste, `Altiplano` nao foi encontrado no select de bairro, embora a cidade tenha sido selecionada. Pode precisar de mapa de bairros/apelidos ou melhora na espera do AJAX.
- Links privados/removidos do Instagram dependem do retorno do navegador/DeepSeek e continuarao entrando como `erro_extracao`, o que e o comportamento correto para nao criar rascunho falso.

## 2026-04-20 - Ajustes apos teste operacional em massa

### Problemas levantados no teste

- API DeepSeek estava atrapalhando mais do que ajudando.
- Alguns rascunhos eram salvos mesmo quando a IA/browser dizia que o link nao era acessivel.
- Alguns rascunhos eram salvos sem midia.
- Itens interrompidos podiam ficar como `processando`.
- Algumas imagens vinham em formato nao ideal para upload, como HEIC/HEIF.
- Frames de video podiam ficar repetidos, borrados, brancos ou em transicao.
- O painel nao deixava claro o historico real do banco e nao mostrava link do rascunho Mercadoi.
- Registros recentes precisavam aparecer primeiro.
- Logs do painel estavam poluidos por consultas repetidas de banco/fila vazia.

### Mudancas feitas

Arquivos alterados:

- `main.py`
- `modules/database_manager.py`
- `modules/media_resolver.py`
- `modules/frame_extractor.py`
- `modules/mercadoi_driver.py`
- `panel.py`
- `panel_static/index.html`
- `config.example.json`
- `requirements.txt`

Resumo:

- A API DeepSeek agora fica desligada por padrao. O bot so usa API se `usar_deepseek_api: true` estiver no `config.json`.
- O bot rejeita dados extraidos com sinais de erro, como `[Erro: Link nao acessivel]`, antes de tentar criar rascunho.
- Se nenhuma midia for baixada, o item vira `erro_download` e nao segue para publicacao.
- Itens travados em `processando` passam a virar `erro_interrompido`, em vez de voltar silenciosamente para `pendente`.
- O banco ganhou campo `mercadoi_url` para guardar o link/id do rascunho/anuncio criado.
- O driver do Mercadoi normaliza melhor `tipo_imovel`, garantindo casos como `APARTAMENTO`, `studio` e textos parecidos.
- O retorno AJAX do Mercadoi agora captura `property_id` e monta URL no formato `https://mercadoi.com.br/?p=ID`.
- Imagens baixadas em HEIC/HEIF/WebP/PNG sao convertidas para JPG antes do upload.
- Frames de video foram reduzidos para ate 12 e passam por filtros de brilho, nitidez, similaridade e transicao.
- O painel agora usa o banco como fonte principal do historico, mantendo os mais recentes no topo e exibindo hora de inicio/fim.
- O historico ganhou filtros de periodo mais claros: Hoje, Semana, Mes e 90d.
- A tabela do historico ganhou botao `Mercadoi` quando houver link salvo.
- O painel foi ajustado para abrir detalhes corretos mesmo quando a tabela esta filtrada.
- Logs repetitivos de banco/fila vazia foram rebaixados para debug.

### Correcao de dados locais

Foram revisados 3 registros antigos que estavam como sucesso apesar de problemas:

- IDs 54 e 61: alterados para `erro_extracao` porque o titulo indicava link inacessivel.
- ID 3: alterado para `erro_download` porque estava sem midia baixada.

### Validacoes

- Importacao geral:
  - `python -B -c "import main, panel; ...; print('imports ok')"`
- Compilacao:
  - `main.py`, `panel.py`, `modules/media_resolver.py`, `modules/frame_extractor.py`, `modules/mercadoi_driver.py`
- Migracao do banco:
  - Confirmado campo `mercadoi_url` na tabela `imoveis`.
- Historico do painel:
  - `_execucoes_db(1)` retornou registros ordenados e os IDs revisados como `falha`.
- Tipo de imovel:
  - `APARTAMENTO 2 quartos` e `studio` normalizam para `Apartamento`.
- Conversao:
  - Helper de conversao preserva JPG existente.

### Riscos ou pendencias

- O link `https://mercadoi.com.br/?p=ID` depende do Mercadoi aceitar acesso ao rascunho/anuncio pelo `property_id`. Se o site usar outra rota privada para edicao/visualizacao, sera preciso ajustar o formato.
- A conversao HEIC depende de `pillow-heif`, instalado nesta rodada e adicionado ao `requirements.txt`.
- A instalacao atual atualizou `pillow` para 12.2.0; isso gerou aviso de conflito com `streamlit`, que nao faz parte deste projeto. Se alguma ferramenta externa usar Streamlit neste mesmo Python, pode ser melhor isolar o bot em ambiente virtual.
- O painel ainda pode ganhar uma tela mais detalhada de etapas por item no futuro; nesta rodada ele passou a exibir o historico real do banco com menos ruido.
- A qualidade dos frames de video foi melhorada por heuristica. Pode exigir ajuste fino depois de mais testes com reels diferentes.

## 2026-04-20 - Baixar todas as imagens detectadas no FastDL

### Problema observado

A publicacao de teste tinha 13 imagens disponiveis no FastDL, mas a execucao das 11:05 detectou somente 6 botoes de imagem:

```text
media_resolver | Encontrados 6 botoes de imagem
mercadoi_driver | 6 arquivo(s) enviado(s) via file_chooser
```

Com isso, o rascunho era salvo corretamente, mas com apenas parte da galeria.

### Causa provavel

O `MediaResolver` fazia apenas uma varredura curta depois de rolar a pagina uma vez. O FastDL pode carregar os cards de download em etapas; em alguns momentos aparecem primeiro 6 imagens e as demais surgem alguns segundos depois.

Tambem havia risco de capturar links falsos de download de outras partes da pagina quando o filtro era amplo demais.

### Mudanca feita

Arquivo alterado:

- `modules/media_resolver.py`

Alteracoes principais:

- A coleta de links agora espera a lista de botoes de imagem crescer, fazendo rolagens progressivas pela pagina.
- A deteccao nao encerra cedo quando a contagem ainda esta baixa.
- Quando encontra um conjunto grande de imagens, inicia o download imediatamente para evitar que a pagina expire/feche.
- O filtro de links foi restringido para downloads reais de imagem, evitando link falso como `pinterest-video-downloader.com`.
- A remocao de anuncios/modais foi centralizada em `_remover_bloqueios`.
- O navegador temporario do FastDL agora tambem e fechado em `finally` se houver erro.

### Validacoes

- `python -B -c "from modules.media_resolver import MediaResolver; print('media resolver import ok')"`
- `python -B -c "import compileall; ok=compileall.compile_file('modules/media_resolver.py', quiet=1); print('compile', ok)"`
- Teste real com o link do Instagram que antes tinha baixado 6 imagens, salvando em `logs/media_test_3`.
- Resultado do teste real:

```text
FastDL: 13 botao(oes) de imagem detectado(s)
Encontrados 13 botoes de imagem
Imagem 13/13 baixada
RESULT imagem 13
```

### Problemas/riscos encontrados no codigo

- O FastDL e instavel/dinamico: a mesma URL pode inicialmente mostrar 6 botoes e depois carregar 13.
- Se uma publicacao tiver poucas imagens de verdade, o bot pode esperar um pouco mais antes de concluir que aquela e a lista completa.
- Nos logs desta execucao tambem apareceu uma extracao ruim de cidade (`cidade_extraida: 1Âº PAVIMENTO  Priva`), causada pelo parser/IA, nao pelo download de imagens. Nao foi corrigido nesta alteracao.

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
