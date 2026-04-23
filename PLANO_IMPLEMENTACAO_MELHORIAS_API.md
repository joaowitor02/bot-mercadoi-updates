# Plano de Implementacao - Evolucao do Bot Mercadoi

Data: 2026-04-23  
Objetivo: transformar o bot atual em uma automacao mais rapida, robusta e vendavel, reduzindo dependencia de navegador, FastDL visual e DeepSeek/browser.

## 1. Resumo Executivo

O bot atual funciona, mas ainda depende de tres pontos frageis:

- IA tentando entender link/post indiretamente.
- FastDL aberto via navegador para baixar imagens/videos.
- Cadastro no site Mercadoi por automacao visual do formulario.

A evolucao recomendada e dividir o fluxo em APIs especializadas:

```text
Link Instagram
  -> API de midia/scraping Instagram
  -> OCR em imagens/frames
  -> Nossa API de IA para extrair JSON do imovel
  -> API/plugin no site do cliente para criar rascunho
```

Resultado esperado:

- menos erros de upload e campos nao selecionados;
- processamento mais rapido;
- logs mais claros;
- menor dependencia de Chrome aberto;
- melhor escalabilidade;
- base tecnica mais profissional para vender como produto.

## 2. Arquitetura Alvo

### Fluxo atual

```text
Instagram link
  -> DeepSeek browser/API tenta interpretar
  -> FastDL visual baixa midia
  -> Bot abre Chrome Mercadoi
  -> Preenche formulario visualmente
  -> Salva rascunho
```

### Fluxo desejado

```text
Instagram link
  -> MediaProvider API baixa legenda, imagens, video
  -> OCRProvider extrai texto de imagens/frames
  -> AIExtractor API gera JSON estruturado
  -> SitePublisher API cria rascunho no site
  -> Painel registra status, erros e links
```

### Modulos novos/alterados

```text
modules/
  instagram_media_api.py      # cliente HikerAPI/Apify/BrightData + fallback
  ocr_extractor.py            # OCR de imagens e frames
  property_ai_client.py       # chama nossa API de IA
  mercadoi_api_client.py      # substitui automacao visual do Mercadoi
  validators.py               # validacoes de campos e schema

api_services/
  property_extractor_api/     # nossa API de IA
  wordpress_plugin/           # plugin/API do site do cliente
```

## 3. Melhorias Propostas

## 3.1 API no site do cliente

### Objetivo

Substituir `modules/mercadoi_driver.py`, que hoje usa Playwright/Chrome para preencher o formulario visual do site.

### Caminho recomendado

Se o site for WordPress, criar um plugin proprio:

```text
wp-content/plugins/bot-mercadoi-api/
```

Endpoints sugeridos:

```http
POST /wp-json/bot-mercadoi/v1/imoveis
POST /wp-json/bot-mercadoi/v1/imoveis/{id}/midias
GET  /wp-json/bot-mercadoi/v1/opcoes
GET  /wp-json/bot-mercadoi/v1/status/{id}
```

### Endpoint principal

```http
POST /wp-json/bot-mercadoi/v1/imoveis
Authorization: Bearer TOKEN_SECRETO
Content-Type: multipart/form-data
```

Payload esperado:

```json
{
  "titulo": "Apartamento Alto Padrao no Altiplano",
  "descricao": "Descricao completa...",
  "tipo_imovel": "Apartamento",
  "operacao": "Venda",
  "preco": "1100000",
  "cidade": "Joao Pessoa",
  "bairro": "Altiplano",
  "quartos": "3",
  "suites": "3",
  "banheiros": "3",
  "vagas": "2",
  "area_m2": "110",
  "url_publicacao": "https://instagram.com/...",
  "whatsapp_url": "https://wa.me/...",
  "instagram_url": "https://instagram.com/...",
  "status": "draft"
}
```

Resposta esperada:

```json
{
  "ok": true,
  "id": 14511,
  "status": "draft",
  "url_admin": "https://site.com/wp-admin/post.php?post=14511&action=edit",
  "url_publica": "https://site.com/?p=14511",
  "imagens_recebidas": 12
}
```

### Requisitos para o cliente fornecer

- Acesso admin ao site.
- Acesso FTP/SFTP ou gerenciador de arquivos da hospedagem.
- Preferencialmente ambiente staging/teste.
- Lista dos campos obrigatorios.
- 2 ou 3 imoveis ja cadastrados corretamente.
- Tema/plugin usado para imoveis.
- Permissao para instalar plugin proprio.

### Criterios de aceite

- Criar rascunho sem abrir Chrome.
- Subir de 1 a 20 imagens por imovel.
- Retornar link do rascunho.
- Validar token de API.
- Registrar erro claro se tipo/bairro/cidade nao existir.
- Nao publicar direto sem autorizacao; criar como rascunho.

## 3.2 API de midia do Instagram

### Objetivo

Substituir o FastDL visual como fonte principal de imagens/videos.

### Estrategia recomendada

Implementar uma interface unica:

```python
class InstagramMediaProvider:
    async def resolve(url: str) -> MediaResult:
        ...
```

Ordem de tentativa:

```text
1. HikerAPI ou provedor principal
2. Apify ou provedor secundario
3. FastDL atual como fallback
```

### Provedores sugeridos

#### HikerAPI

Indicado para primeira tentativa por ser API dedicada a Instagram, REST, com endpoints para posts/reels e custo por request. A propria documentacao/comparativos indicam preco de referencia de cerca de `US$0.0006/request`.

Fontes:

- https://hiker-doc.readthedocs.io/
- https://hikerapi.com/help/best-instagram-api-for-developers-2026

#### Apify

Bom como alternativa/fallback ou para prototipagem. O preco oficial atual informa `US$0.20` por compute unit, mas actors podem ter custo variavel por tempo, storage, proxy e taxa propria.

Fonte:

- https://apify.com/pricing

#### Bright Data

Mais corporativo/enterprise. Tem Scraper APIs para social media e Instagram, mas tende a ser mais caro. Referencias publicas citam planos na casa de centenas de dolares/mes ou pay-as-you-go por resultados.

Fontes:

- https://docs.brightdata.com/api-reference/scrapers/social-media-apis/overview
- https://brightdata.com/products/web-scraper/instagram

### Saida padrao esperada

```json
{
  "tipo_midia": "carrossel",
  "caption": "Texto do post...",
  "perfil_instagram": "@perfil",
  "midias": [
    {
      "tipo": "image",
      "url": "https://...",
      "filename": "image_01.jpg"
    },
    {
      "tipo": "video",
      "url": "https://...",
      "thumbnail_url": "https://..."
    }
  ]
}
```

### Criterios de aceite

- Post com 1 imagem: baixa 1 imagem.
- Carrossel: baixa todas as imagens disponiveis.
- Reel/video: baixa video ou thumbnail e permite extrair frames.
- Falha no provedor principal cai automaticamente no fallback.
- Log mostra qual provedor foi usado e quantas midias foram encontradas.

## 3.3 Nossa API de IA para extracao de dados do imovel

### Objetivo

Substituir o DeepSeek/browser e evitar depender de IA acessando links externos.

### Principio tecnico

A IA nao deve abrir o link. Ela deve receber conteudo ja coletado:

```text
caption + OCR das imagens + OCR dos frames + metadados
```

E devolver JSON.

### Endpoint sugerido

```http
POST /extract-property
Content-Type: application/json
```

Entrada:

```json
{
  "url_publicacao": "https://instagram.com/reel/ABC123/",
  "caption": "Apartamento no Altiplano...",
  "ocr_text": "110m2 | 3 suites | R$ 1.100.000",
  "frames_text": [
    "Altiplano Cabo Branco",
    "3 suites, 110m2",
    "R$ 1.100.000"
  ],
  "perfil_instagram": "@corretor",
  "tipo_midia": "video"
}
```

Saida:

```json
{
  "titulo": "Apartamento Alto Padrao 3 Suites no Altiplano",
  "descricao_util": "Apartamento com 110m2, 3 suites, sala ampla...",
  "tipo_imovel": "Apartamento",
  "operacao": "Venda",
  "preco": "1100000",
  "cidade_extraida": "Joao Pessoa",
  "bairro_extraido": "Altiplano",
  "quartos": "3",
  "suites": "3",
  "banheiros": "",
  "vagas": "2",
  "area_m2": "110",
  "andar": "",
  "elevador": "",
  "estagio_imovel": "",
  "confianca": 0.91,
  "campos_duvidosos": ["banheiros"]
}
```

### Modelos sugeridos

Comecar simples:

- Ollama + Qwen2.5/Qwen3 Instruct 7B ou 14B.
- Saida com JSON schema/structured output.

Evoluir se necessario:

- vLLM com Qwen 14B/32B ou Llama 8B/70B.
- GPU em servidor seu.

Fontes tecnicas:

- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- vLLM OpenAI-compatible server: https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html

### Regras fundamentais do prompt

O modelo deve obedecer:

```text
Se a informacao nao estiver explicitamente no texto, retorne string vazia.
Nao invente preco, bairro, metragem, quartos, vagas ou cidade.
Retorne apenas JSON valido no schema.
Use valores normalizados: Venda, Aluguel, Apartamento, Casa, Terreno.
```

### Validacao pos-IA

Obrigatorio validar no codigo:

- `suites <= quartos`
- `quartos <= 30`
- `banheiros <= 15`
- `vagas <= 30`
- `area_m2 <= 100000`
- preco numerico e plausivel
- cidade/bairro dentro de lista conhecida quando possivel
- tipo do imovel dentro de opcoes aceitas

### OCR

Criar `modules/ocr_extractor.py`.

Opcoes:

- Tesseract: mais simples, mas pode exigir instalacao.
- PaddleOCR: melhor para texto em imagens, mas pacote maior.
- OCR via modelo vision-language: melhor qualidade, maior custo.

Recomendacao inicial:

```text
Fase piloto: Tesseract ou PaddleOCR local
Fase produto: avaliar OCR por API/modelo visual se a acuracia for baixa
```

## 4. Plano de Implementacao por Fases

## Fase 0 - Preparacao e dataset de teste

Prazo estimado: 1 a 2 dias.

Tarefas:

- Selecionar 50 a 100 links reais do banco.
- Marcar manualmente o resultado esperado para 20 deles.
- Separar casos:
  - imagem unica;
  - carrossel;
  - reel/video;
  - post sem acesso;
  - post com texto so na imagem;
  - post com informacao incompleta.

Entregaveis:

- `tests/fixtures/posts_amostra.json`
- `tests/fixtures/resultado_esperado.json`

## Fase 1 - API do site do cliente

Prazo estimado: 5 a 10 dias uteis, dependendo do acesso e complexidade do WordPress/tema.

Tarefas:

- Mapear campos internos do cadastro de imoveis.
- Criar plugin WordPress com endpoint REST.
- Criar autenticacao por token.
- Criar rascunho com campos basicos.
- Fazer upload de imagens.
- Retornar links do rascunho.
- Criar logs de erro no plugin.
- Criar `modules/mercadoi_api_client.py`.
- Trocar fluxo por feature flag:

```json
{
  "publicacao_modo": "api"
}
```

Fallback:

```json
{
  "publicacao_modo": "browser"
}
```

Critérios de aceite:

- 10 rascunhos criados via API em staging.
- 0 uso de Chrome para cadastrar no site.
- Galeria preserva todas as imagens.
- Retorno salva `mercadoi_url` no banco.

## Fase 2 - API de midia Instagram

Prazo estimado: 3 a 6 dias uteis.

Tarefas:

- Criar `modules/instagram_media_api.py`.
- Implementar HikerAPI como primeiro provider.
- Implementar fallback FastDL atual.
- Normalizar retorno para `MediaResult`.
- Baixar imagens diretamente por URL.
- Baixar video e reaproveitar `frame_extractor.py`.
- Criar logs por provider.

Config:

```json
{
  "instagram_media_provider": "hikerapi",
  "hikerapi_key": "",
  "media_fallback_fastdl": true
}
```

Criterios de aceite:

- 20 links processados com carrossel baixando todas as midias.
- Video/reel continua extraindo frames.
- Se HikerAPI falhar, FastDL entra automaticamente.

## Fase 3 - Nossa API de IA

Prazo estimado: 5 a 12 dias uteis.

Tarefas:

- Criar API FastAPI `property_extractor_api`.
- Rodar Ollama local/servidor.
- Criar schema JSON de saida.
- Criar prompt com exemplos.
- Criar validacao de resposta.
- Criar `modules/property_ai_client.py`.
- Substituir DeepSeek por feature flag:

```json
{
  "extracao_ia_provider": "property_api"
}
```

Fallback:

```json
{
  "extracao_ia_provider": "deepseek_browser"
}
```

Criterios de aceite:

- JSON valido em 100% das respostas.
- Pelo menos 85% de acerto nos campos principais em amostra inicial:
  - tipo;
  - operacao;
  - preco;
  - cidade;
  - bairro;
  - quartos;
  - area.
- Quando faltar dado, retornar vazio em vez de inventar.

## Fase 4 - OCR e frames inteligentes

Prazo estimado: 4 a 8 dias uteis.

Tarefas:

- Criar `modules/ocr_extractor.py`.
- Aplicar OCR nas imagens.
- Aplicar OCR nos frames extraidos de video.
- Deduplicar texto repetido.
- Enviar `ocr_text` para API de IA.
- Medir melhora de acuracia.

Criterios de aceite:

- OCR extrai texto util de pelo menos 70% das artes com texto.
- Dados de preco/area/quartos melhoram em posts onde a legenda e pobre.

## Fase 5 - Observabilidade, custos e painel

Prazo estimado: 3 a 5 dias uteis.

Tarefas:

- Painel mostrar provider usado:
  - media: HikerAPI/Apify/FastDL;
  - IA: property_api/deepseek;
  - publicacao: API/browser.
- Registrar tempo por etapa.
- Registrar custo estimado por item.
- Exportar relatorio CSV com:
  - URL;
  - status;
  - tempo;
  - provider;
  - custo estimado;
  - erro.

## 5. Estimativa de Custos Operacionais

Cotacao usada apenas para simulacao: `US$1 ~= R$5,20`. Recalcular na data da proposta.

## 5.1 Custos por item processado

### Midia Instagram

Opcao HikerAPI:

- referencia: `US$0.0006/request`;
- estimativa por post: 1 a 3 requests;
- custo por post: `US$0.0006` a `US$0.0018`;
- em BRL: cerca de `R$0,003` a `R$0,009` por post.

Opcao Apify:

- referencia oficial: `US$0.20/CU`;
- custo varia por actor, tempo, proxy e storage;
- estimativa conservadora para pequenos volumes: `US$0.005` a `US$0.05` por post.

Opcao Bright Data:

- tende a ser enterprise;
- usar se cliente exigir alta robustez/SLA ou volume maior;
- estimativa de referencia: pode partir de pay-as-you-go por registros ou planos mensais maiores.

### IA propria

Opcao local no computador do operador:

- custo direto de API: `R$0`;
- custo indireto: maquina mais pesada, instalacao e manutencao;
- melhor para piloto.

Opcao servidor GPU sob demanda:

- RunPod e similares variam por GPU; referencias publicas recentes colocam RTX 4090 na faixa aproximada de `US$0.34/h` a `US$0.70/h`, dependendo de plano/provedor/disponibilidade.
- Se a API processar 300 posts/hora, custo de GPU fica aproximadamente:
  - `US$0.0011` a `US$0.0023` por post;
  - `R$0,006` a `R$0,012` por post.

Fontes de referencia:

- https://www.runpod.io/pricing
- https://deploybase.ai/articles/runpod-gpu-pricing
- https://deploybase.ai/articles/rtx-4090-cloud

### API do site do cliente

Se a API rodar no proprio WordPress do cliente:

- custo operacional adicional: provavelmente `R$0`;
- custo real: desenvolvimento, testes e suporte.

Se precisar de servidor intermediario:

- VPS simples: `US$5` a `US$20/mes`;
- em BRL: cerca de `R$26` a `R$104/mes`.

## 5.2 Simulacao mensal por volume

Assumindo:

- HikerAPI: 2 requests por post (`US$0.0012/post`);
- IA em GPU compartilhada: `US$0.002/post`;
- VPS/API/logs: `US$10/mes`;
- dolar simulado: R$5,20.

| Volume mensal | Custo midia | Custo IA | Infra fixa | Total USD | Total BRL aprox. |
|---:|---:|---:|---:|---:|---:|
| 500 posts | US$0.60 | US$1.00 | US$10 | US$11.60 | R$60 |
| 1.000 posts | US$1.20 | US$2.00 | US$10 | US$13.20 | R$69 |
| 3.000 posts | US$3.60 | US$6.00 | US$10 | US$19.60 | R$102 |
| 10.000 posts | US$12.00 | US$20.00 | US$10 | US$42.00 | R$218 |

Observacao: esses numeros sao estimativas tecnicas para operacao. Nao incluem suporte, manutencao, margem, impostos, tempo de desenvolvimento, custo de licenca comercial, atendimento ao cliente nem risco de APIs externas.

## 6. Precificacao do Produto

## 6.1 Custos que devem entrar no preco

Para nao vender barato demais, considerar:

- custo de APIs externas;
- servidor/infra;
- manutencao mensal;
- suporte ao cliente;
- risco de Instagram/API mudar;
- ajustes no site do cliente;
- acompanhamento de falhas;
- margem de lucro;
- impostos;
- tempo de implantacao.

## 6.2 Modelo recomendado de cobranca

### Setup inicial

Cobrar implantacao separada:

```text
Setup API/site + configuracao + testes: R$2.500 a R$8.000
```

Faixa sugerida:

- Cliente pequeno, site simples: `R$2.500 a R$4.000`
- WordPress complexo/tema dificil: `R$4.000 a R$6.000`
- Sem staging, campos confusos, muita regra: `R$6.000 a R$8.000+`

### Mensalidade

Modelo por plano:

| Plano | Volume sugerido | Preco sugerido |
|---|---:|---:|
| Starter | ate 500 posts/mes | R$497 a R$797/mes |
| Pro | ate 2.000 posts/mes | R$997 a R$1.497/mes |
| Business | ate 5.000 posts/mes | R$1.997 a R$2.997/mes |
| Enterprise | acima disso | sob consulta |

### Excedente

Cobrar por item adicional:

```text
R$0,50 a R$2,00 por post adicional processado
```

Mesmo que o custo tecnico por post seja baixo, o excedente cobre suporte, risco e margem.

## 7. Roadmap Tecnico para Claude Implementar

## Sprint 1 - Preparacao

Tarefas:

- Criar branch `feature/api-evolution`.
- Criar fixtures com amostras reais.
- Criar schema Pydantic para `PropertyData`.
- Criar schema Pydantic para `MediaResult`.
- Adicionar campos de configuracao sem quebrar fluxo atual.

Arquivos esperados:

```text
modules/schemas.py
tests/fixtures/
```

## Sprint 2 - Media Provider

Tarefas:

- Criar `modules/instagram_media_api.py`.
- Implementar HikerAPI.
- Implementar fallback FastDL chamando `MediaResolver` atual.
- Criar testes com mocks HTTP.

Nao remover o FastDL atual nesta sprint.

## Sprint 3 - Property AI API

Tarefas:

- Criar `api_services/property_extractor_api`.
- Criar FastAPI com `/extract-property`.
- Integrar Ollama/vLLM por config.
- Criar prompt e schema.
- Criar testes de contrato.

## Sprint 4 - OCR

Tarefas:

- Criar `modules/ocr_extractor.py`.
- Rodar OCR em imagens e frames.
- Juntar texto de OCR com caption.
- Enviar para `property_ai_client`.

## Sprint 5 - Site API

Tarefas:

- Criar plugin WordPress.
- Criar endpoint de opcoes.
- Criar endpoint de rascunho.
- Criar upload de midias.
- Criar `modules/mercadoi_api_client.py`.
- Adicionar feature flag para escolher API/browser.

## Sprint 6 - Painel e Custos

Tarefas:

- Mostrar provider usado por item.
- Mostrar tempo por etapa.
- Mostrar custo estimado.
- Melhorar relatorio CSV.
- Criar tela de configuracao de providers.

## 8. Feature Flags Obrigatorias

Para implementar sem quebrar o bot atual:

```json
{
  "instagram_media_provider": "fastdl",
  "media_fallback_fastdl": true,
  "extracao_ia_provider": "deepseek_browser",
  "property_api_url": "",
  "property_api_key": "",
  "publicacao_modo": "browser",
  "mercadoi_api_url": "",
  "mercadoi_api_token": ""
}
```

Valores futuros:

```text
instagram_media_provider: fastdl | hikerapi | apify | brightdata
extracao_ia_provider: deepseek_browser | property_api | ollama_local
publicacao_modo: browser | api
```

## 9. Regras para nao quebrar funcionalidades existentes

- Nao remover `modules/mercadoi_driver.py` ate API do site estar validada.
- Nao remover `modules/media_resolver.py` ate provider de midia estar validado.
- Manter DeepSeek/browser como fallback ate nossa API de IA atingir acuracia minima.
- Toda mudanca deve ser ativada por config.
- Cada item processado deve registrar provider usado.
- Se qualquer provider novo falhar, usar fallback antigo quando possivel.

## 10. Riscos

### Instagram

- APIs de scraping podem mudar, bloquear ou encarecer.
- Conteudo privado/removido continuara falhando.
- Deve haver permissao comercial para reutilizar imagens.

### Site do cliente

- Tema/plugin pode salvar campos de forma nao padronizada.
- Sem staging, testes em producao sao mais arriscados.
- Atualizacoes do WordPress/plugin podem exigir manutencao.

### IA

- Modelo pode inventar dados se prompt/validacao forem fracos.
- OCR pode errar numeros em artes ruins.
- Fine-tuning nao deve ser primeira etapa; primeiro medir acuracia com prompt e schema.

## 11. Checklist para iniciar com cliente

Pedir:

- acesso admin ao site;
- FTP/SFTP ou gerenciador de arquivos;
- staging/teste;
- 2 ou 3 imoveis exemplos;
- lista de campos obrigatorios;
- plugin/tema usado para imoveis;
- autorizacao para instalar plugin;
- volume esperado por mes;
- se o cliente tem permissao de reutilizar as imagens dos posts.

## 12. Recomendacao Final

Melhor caminho:

```text
1. Implementar API do site do cliente.
2. Implementar API de midia Instagram com fallback FastDL.
3. Implementar nossa API de IA com caption + OCR.
4. Medir custos/tempo/acuracia no painel.
5. Precificar por setup + mensalidade + excedente.
```

Essa ordem reduz risco porque a API do site remove a parte mais fragil primeiro: o cadastro visual no Mercadoi.

Depois disso, a troca da midia e da IA aumenta velocidade e confiabilidade sem comprometer o fluxo atual.
