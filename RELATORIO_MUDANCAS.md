# Relatorio de Mudancas

## 2026-04-24 - Duplicados no painel

### Problema relatado
- O modal de links duplicados mostrava itens como "Ja publicado com sucesso", mas deixava o botao `Inserir mesmo assim` bloqueado.

### Causa encontrada
- O backend ja estava retornando `forcavel: true` para links publicados.
- O bloqueio restante estava no frontend: o painel dependia apenas da flag `forcavel` para habilitar o botao, entao qualquer resposta inconsistente ou estado antigo no navegador podia manter o modal travado.
- O HTML principal do painel tambem podia ser reaproveitado pelo navegador entre testes.

### Mudancas feitas
- Em `panel_static/index.html`, o calculo de duplicatas forcaveis passou a considerar tambem `status_existente`.
  - Se o status visivel nao for `pendente` nem `processando`, o painel trata o item como reinserivel.
- Em `panel.py`, a rota `/` agora responde com `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`.
  - Isso reduz a chance de o navegador manter uma versao antiga do painel.

### Validacoes executadas
- Subi o painel local novamente e confirmei `GET /api/health -> {\"ok\":true}`.
- Testei o backend de duplicados com URLs reais ja publicadas e ele retornou `forcavel: true`.
- Removi do banco uma linha de teste criada durante a validacao (`id=860`).

### Risco residual
- Se o navegador estiver com a aba aberta ha muito tempo, ainda vale recarregar a pagina uma vez para pegar o HTML atualizado.
