# Bot Mercadoi — Guia de Instalação e Uso (v3.2)

## O que o bot faz

1. Lê links do Instagram da fila interna (banco de dados local)
2. Extrai o texto da publicação via scraping (sem login)
3. Envia o texto para a **API oficial do DeepSeek** e obtém os dados estruturados do imóvel
4. Baixa as imagens/vídeo do post
5. Preenche o formulário do Mercadoi e salva como rascunho
6. Atualiza o status no banco de dados em tempo real

> **Fallback automático:** se a API falhar, o bot usa o DeepSeek no navegador como alternativa.

---

## Pré-requisitos

- **Python 3.11 ou superior** → https://www.python.org/downloads/
  - Durante a instalação, marque **"Add Python to PATH"**
- **Google Chrome** instalado
- **Chave da API DeepSeek** → https://platform.deepseek.com/ (crie uma conta e gere uma chave)

---

## 1. Instalação (fazer apenas uma vez)

Abra o **Prompt de Comando** (`Win + R` → `cmd` → Enter):

```
cd C:\caminho\para\mercadoi_bot
pip install -r requirements.txt
python -m playwright install chromium
```

> O banco de dados (`botmercadoi.db`) é criado automaticamente na primeira execução. Não precisa configurar nada.

---

## 2. Configurar o bot

1. Duplique o arquivo `config.example.json`
2. Renomeie a cópia para `config.json`
3. Abra com o Bloco de Notas e preencha:

```json
{
  "mercadoi_url": "https://www.mercadoi.com.br",
  "downloads_path": "C:\\Users\\SeuNome\\Downloads",
  "deepseek_api_key": "SUA_CHAVE_API_DEEPSEEK",
  "deepseek_profile_path": "C:\\chrome_bot_deepseek",
  "mercadoi_profile_path": "C:\\chrome_bot_mercadoi",
  "watch_intervalo_minutos": 5,
  "panel_senha": "sua_senha_aqui",
  "telegram_bot_token": "",
  "telegram_chat_id": ""
}
```

**downloads_path:** troque `SeuNome` pelo seu usuário do Windows.

**deepseek_api_key:** obtenha em https://platform.deepseek.com/ → API Keys.

**panel_senha:** senha para proteger o painel web. Deixe vazio para acesso livre.

---

## 3. Login no Mercadoi (apenas uma vez)

O bot usa um perfil de Chrome separado para manter o login:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome_bot_mercadoi"
```

Na janela que abrir → acesse o Mercadoi → faça login → **deixe o Chrome aberto**.

> O DeepSeek browser (fallback) também precisa de login, caso queira usá-lo:
> ```
> "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\chrome_bot_deepseek"
> ```
> Acesse https://chat.deepseek.com → faça login → feche o Chrome.

---

## 4. Rodar o bot

### Opção A — Painel Web (recomendado)

Dê duplo clique em **`Iniciar Painel.bat`**.

O painel abrirá em `http://localhost:8000` e permite:
- Adicionar links do Instagram à fila (um ou vários de uma vez)
- Ver o histórico de execuções dos últimos 7 dias
- Reprocessar itens que falharam com um clique
- Acompanhar o log em tempo real
- Disparar o bot manualmente

### Opção B — Linha de comando

Com o Chrome do Mercadoi aberto e logado:

```
python main.py
```

**Modo watch** (roda em loop a cada N minutos):

```
python main.py --watch 5
```

Ou dê duplo clique em **`Iniciar Bot.bat`** e escolha a opção no menu.

---

## 5. Acompanhar os resultados

- **Painel web:** `http://localhost:8000` — histórico visual, logs ao vivo, fila de pendentes
- **Banco de dados:** arquivo `botmercadoi.db` na pasta do bot (criado automaticamente)
- **Logs:** pasta `logs/` com um arquivo `.log` por dia
- **Screenshots de erro:** pasta `logs/screenshots/` — capturada automaticamente em caso de falha
- **Mercadoi:** rascunhos aparecem na seção de anúncios

---

## 6. Notificações Telegram (opcional)

1. Fale com [@BotFather](https://t.me/BotFather) no Telegram e crie um bot
2. Copie o token gerado para `telegram_bot_token` no `config.json`
3. Envie uma mensagem para o seu novo bot
4. Acesse `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o `chat_id`
5. Cole em `telegram_chat_id` no `config.json`

O bot enviará uma mensagem ao fim de cada ciclo informando sucessos e falhas.

---

## Códigos de status

| Status | Significado |
|--------|-------------|
| `pendente` | Aguardando processamento |
| `processando` | Em execução agora |
| `rascunho_salvo` | Sucesso — rascunho salvo com mídia |
| `rascunho_salvo_sem_midia_video` | Sucesso — mídia era vídeo, salvo sem ela |
| `erro_extracao` | Falha ao extrair dados do post |
| `erro_download` | Falha ao baixar a mídia |
| `erro_preenchimento` | Falha ao preencher o formulário |
| `erro_salvamento` | Falha ao salvar o rascunho |

Para reprocessar: clique em **"Tentar novamente"** no painel, ou o bot reseta automaticamente na próxima execução.

---

## Comportamento inteligente

- **Banco local:** sem dependência de Google Sheets — funciona offline, sem credenciais externas
- **Recuperação automática:** itens travados em `processando` voltam para `pendente` no próximo ciclo
- **Deduplicação:** URLs duplicadas são ignoradas automaticamente
- **Retry no Mercadoi:** até 3 tentativas com 10s de espera entre elas
- **Retry na mídia:** 2 tentativas de download antes de desistir
- **Validação da IA:** suítes, quartos, banheiros e área são verificados contra limites razoáveis
- **Erros classificados:** link inválido, post privado e acesso restrito têm mensagens específicas

---

## Problemas comuns

**"deepseek_api_key inválida"** → Confirme a chave em https://platform.deepseek.com/ → API Keys. O bot usará o navegador como fallback.

**Bot não encontra o Chrome do Mercadoi** → Certifique-se de abrir o Chrome com `--remote-debugging-port=9222` antes de rodar o bot.

**Porta 8000 ocupada** → Outro processo está usando a porta. Feche-o ou reinicie o computador.

**Bairro não encontrado** → O bairro extraído pode não existir no cadastro do Mercadoi. O campo será deixado em branco.
