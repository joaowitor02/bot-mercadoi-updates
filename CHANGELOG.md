# Changelog — Bot Mercadoi

## [3.2] - 2026-04-21
### Melhorias
- Launcher unificado `Abrir Painel.vbs`: instala tudo automaticamente na primeira execução, sem terminal
- Filtro de status na aba Fila (Todos / Pendente / Erro) com contadores
- Contador de tentativas por item: badge laranja "2×" indica itens problemáticos
- Botão "Tentar novamente" agora atualiza a tabela imediatamente
- Mensagens de duplicata diferenciadas: "Já publicado", "Já na fila", "Já em erro"
- Ícone "Ver Imóvel" sempre inserido no anúncio (link do post do Instagram)
- URLs de WhatsApp e Instagram normalizadas automaticamente (wa.me sem https:// corrigido)
- Log informa quais ícones de contato foram inseridos em cada anúncio
- Atualizador redesenhado: baixa zip diretamente, sem necessidade de git no cliente
- Changelog exibido no painel antes de atualizar

### Corrigido
- Conversão de imagem: PNG aceito diretamente, apenas WEBP/BMP/TIFF/GIF convertidos para JPEG
- Reprocessar item (409 quando bot rodando tratado com mensagem clara)

## [3.1] - 2026-04-20
### Adicionado
- Inferência automática de cidade por bairro (mapa com +80 bairros da Paraíba)
- Suporte aos tipos: Cobertura, Sobrado, Kitnet
- Múltiplas estratégias de confirmação de upload de imagens
- Detecção de post privado ou inacessível com mensagem específica
- Notificação Telegram no início do bot e ao final de cada ciclo com links clicáveis
- Endpoint para limpar todos os pendentes + botão no painel
- Chrome scraper do Instagram via CDP (módulo de fallback)
- Botão "Tentar novamente" por item com erro na fila

### Corrigido
- Login com senha contendo caracteres especiais (acentos, ç)
- URL do rascunho Mercadoi apontava para página pública; agora abre no editor (wp-admin)
- Token do Telegram com dígito inicial ausente
- URL do tunnel não enviada mais para o Telegram do cliente
- Chrome: mata instância anterior antes de abrir nova

## [3.0] - 2026-04-16
### Adicionado
- Painel web completo com autenticação (usuário/admin)
- Banco de dados local SQLite (sem dependência do Google Sheets)
- Watch Mode: processamento automático em loop configurável
- Histórico com filtros, períodos e exportação CSV
- Sistema de licença com assinatura HMAC e data de expiração
- Tunnel Cloudflare para suporte remoto
- Hash de senhas (SHA-256) e comparação segura
- Auto-update via verificação de versão remota
- Notificações Telegram configuráveis pelo painel
