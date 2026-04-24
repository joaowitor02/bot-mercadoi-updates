# Changelog — Bot Mercadoi

## [3.3] - 2026-04-23
### Melhorias de velocidade
- Bot ~70% mais rápido: extração Deepseek e download de mídia rodando em paralelo
- Pipeline: enquanto publica o link atual, já extrai e baixa o próximo
- Preenchimento do formulário Mercadoi em lote (1 chamada JS em vez de 15 separadas)
- Tempos de espera reduzidos: TinyMCE, upload e MediaResolver mais ágeis

### Novas funcionalidades
- Renovação de licença pelo cliente: cola a chave enviada pelo suporte direto no painel, sem precisar de acesso remoto
- Geração de chave de licença pelo painel admin com 1 clique
- Link de download para novos clientes visível na aba Configurações
- Banner de atualização visível para todos os usuários (não só admin)
- Botão "Limpar erros" na aba Fila
- Reinício automático do painel após atualização — página recarrega sozinha

### Correções
- Tipos de imóvel: Flat, Chácara, Fazenda e Sítio agora selecionados corretamente
- Preço "R$ 350 mil" convertido para 350000 corretamente
- Preço "89.999,000" não virava mais 89 milhões
- Frames de vídeo: detector de face (Haar) + corpo (HOG) filtra frames com corretor
- Extrator de frames: até 20 por vídeo, nitidez e variedade maiores
- OCR de preço nas imagens quando a IA não encontrou no texto

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
