# Bot Mercadoi — Guia Completo de Instalação e Uso

## O que o bot faz

O Bot Mercadoi automatiza o cadastro de anúncios imobiliários no Mercadoi a partir de publicações do Instagram:

1. Você cola links do Instagram no painel
2. O bot acessa cada post, extrai os dados do imóvel com inteligência artificial (DeepSeek)
3. Baixa as imagens ou vídeo da publicação
4. Preenche e salva o anúncio como rascunho no Mercadoi automaticamente
5. Você recebe notificação no Telegram com o resultado

---

## Pré-requisitos

Instale antes de começar:

- **Python 3.10 ou superior** → [python.org/downloads](https://www.python.org/downloads/)
  - Durante a instalação, marque obrigatoriamente **"Add Python to PATH"**
- **Google Chrome** instalado normalmente no Windows
- **Conta ativa no Mercadoi** com permissão para criar anúncios
- **Conta no DeepSeek** (gratuita) → [chat.deepseek.com](https://chat.deepseek.com)

---

## Instalação (fazer apenas uma vez)

1. Copie a pasta do bot para qualquer lugar no computador (ex: `C:\Bot Mercadoi`)
2. Dê **duplo-clique** em `Abrir Painel.vbs`

O instalador automático irá:
- Verificar se o Python está instalado
- Instalar todos os pacotes necessários
- Instalar o navegador Chromium (usado internamente pelo bot)
- Iniciar o painel de controle e abrir no navegador

> Se aparecer erro de Python não encontrado, instale o Python conforme o pré-requisito acima e tente novamente.

---

## Primeiro acesso

### 1. Abrir o painel

Dê duplo-clique em **`Abrir Painel.vbs`**. O painel abre no navegador em `http://localhost:8000`.

### 2. Fazer login

Use as credenciais fornecidas pelo administrador. Na primeira configuração, o administrador define usuário e senha pela aba **Configurações**.

### 3. Configurar o Chrome do Mercadoi (apenas uma vez)

O bot usa um perfil de Chrome separado e dedicado para manter o login no Mercadoi. Para configurar:

1. No painel, clique no indicador **"Chrome offline"** no topo (ou aguarde aparecer)
2. O Chrome abrirá automaticamente na conta do Mercadoi
3. Faça login no Mercadoi normalmente
4. **Deixe o Chrome aberto** — o bot precisa dele para publicar os anúncios

> O Chrome do Mercadoi precisa estar aberto sempre que o bot for processar links.

### 4. Configurar o DeepSeek (apenas uma vez)

O bot usa o DeepSeek para ler os posts do Instagram e extrair os dados do imóvel:

1. Abra o Chrome normalmente (não o do bot, o seu Chrome pessoal)
2. Acesse [chat.deepseek.com](https://chat.deepseek.com)
3. Crie uma conta gratuita e faça login
4. Feche o Chrome

> O bot usará um perfil separado (`C:\chrome_bot_deepseek`) para acessar o DeepSeek automaticamente.

---

## Uso diário

### Adicionar links à fila

1. Abra o painel (`Abrir Painel.vbs`)
2. Vá na aba **Fila**
3. Cole um ou vários links do Instagram (um por linha) no campo de texto
4. Clique em **Adicionar à Fila**

Links duplicados ou inválidos são detectados automaticamente.

### Processar os links

Há duas formas de processar:

**Processar agora** — clique no botão laranja **"Processar Agora"** no topo. O bot processa todos os links pendentes uma vez e para.

**Watch Mode** — clique em **"Watch Mode"** para o bot rodar automaticamente em loop, verificando a fila a cada intervalo configurado (padrão: 5 minutos). Ideal para deixar rodando durante o dia.

> O Chrome do Mercadoi precisa estar aberto para o bot funcionar. Se estiver fechado, o painel avisa e envia notificação no Telegram.

### Acompanhar os resultados

- **Aba Histórico** — lista todos os anúncios processados com status, data, título extraído e link para o rascunho no Mercadoi
- **Log ao vivo** — aparece automaticamente durante o processamento
- **Telegram** — recebe resumo ao final de cada ciclo (quando configurado)
- **Rascunhos no Mercadoi** — acesse sua conta e revise antes de publicar

---

## Reprocessar erros

Se um link falhar, ele aparece na **aba Fila** com status de erro e a mensagem do problema. Para tentar novamente:

- Clique em **"Tentar novamente"** ao lado do item — ele volta para a fila de pendentes
- Ou adicione o mesmo link novamente — o bot detecta que falhou e reativa automaticamente

---

## Configurações (administrador)

Acesse a aba **Configurações** (visível apenas com login de administrador):

| Configuração | O que faz |
|---|---|
| **Usuário e senha** | Credenciais de acesso do cliente ao painel |
| **Senha do administrador** | Senha para a conta de administrador |
| **Telegram** | Token do bot e Chat ID para notificações |
| **Intervalo Watch** | Quantos minutos entre cada verificação automática |
| **Licença** | Data de expiração da licença do software |
| **Config Avançada** | URL do Mercadoi, pastas, chave API, WhatsApp padrão |

### Configurar notificações Telegram

1. Fale com [@BotFather](https://t.me/BotFather) no Telegram → crie um novo bot → copie o **token**
2. Envie uma mensagem para o bot criado
3. Acesse `https://api.telegram.org/bot<TOKEN>/getUpdates` → copie o `chat_id`
4. No painel → Configurações → preencha token e chat ID → **Testar**

---

## Suporte remoto (Tunnel)

O administrador pode acessar o painel do cliente remotamente sem precisar estar no mesmo local:

1. No painel do cliente, vá em **Configurações** (login de admin necessário)
2. Seção **Tunnel** → clique em **Baixar cloudflared** (apenas na primeira vez)
3. Clique em **Ativar Tunnel**
4. Uma URL pública (`https://xxxx.trycloudflare.com`) aparece no painel
5. O administrador acessa essa URL de qualquer lugar

> O tunnel é temporário — a URL muda a cada ativação. Desative quando não precisar mais.

---

## Solução de problemas

**Chrome aparece como "offline" no painel**
→ O Chrome do Mercadoi não está aberto. Clique no indicador para abrir automaticamente, faça login no Mercadoi e deixe aberto.

**"Não foi possível extrair dados do post"**
→ O post é privado, foi apagado ou a conta está com acesso restrito. Verifique se o link é público e tente novamente.

**Upload de imagens falhou (0 confirmadas)**
→ Verifique se o Chrome do Mercadoi está logado. Tente reprocessar o item.

**Porta 8000 ocupada**
→ O painel já pode estar rodando. Abra o navegador em `http://localhost:8000`. Se não carregar, reinicie o computador.

**Bot travou em "processando"**
→ Reinicie o painel. Itens travados em `processando` voltam para `pendente` automaticamente.

**Erro na primeira instalação**
→ Verifique se o Python está instalado com "Add to PATH" marcado. Veja o log em `logs\setup.log`.

---

## Estrutura dos arquivos

```
Bot Mercadoi/
├── Abrir Painel.vbs       ← Iniciador — dê duplo-clique aqui
├── config.example.json    ← Modelo de configuração (não editar)
├── config.json            ← Sua configuração (criada pelo painel)
├── main.py                ← Núcleo do bot
├── panel.py               ← Servidor do painel web
├── modules/               ← Módulos internos (não editar)
├── panel_static/          ← Interface do painel (não editar)
├── logs/                  ← Logs gerados automaticamente
└── botmercadoi.db         ← Banco de dados local (gerado automaticamente)
```

---

## Versão

Bot Mercadoi v3.1 — Desenvolvido sob medida.
Para suporte, entre em contato com o administrador do sistema.
