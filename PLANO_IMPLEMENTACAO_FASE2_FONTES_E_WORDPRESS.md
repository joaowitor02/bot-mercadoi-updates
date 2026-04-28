# Plano de Implementação — Bot Mercadoi v4.0
**Data:** 2026-04-25
**Versão atual:** 3.4 (local, sequencial, FastDL, formulário browser)
**Versão alvo:** 4.0 (cloud, paralelo, API de mídia, WordPress REST API)

---

## Contexto e premissas

| Item | Valor |
|---|---|
| Volume médio | 180 links/dia |
| Volume pico | 300 links/dia |
| Clientes atuais | 1 |
| Alvo de clientes | 5–10 |
| Plataforma atual | Windows local |
| Plataforma alvo | Cloud (VPS Linux) |
| Custo manual substituído | R$ 3.600–6.000/mês |

**Restrições:**
- Não quebrar o fluxo atual durante a transição
- Cada fase deve ser testável com dados reais
- Cliente atual não pode ter interrupção de serviço

---

## Visão da arquitetura final

```
Cliente abre navegador → painel.seudominio.com.br
                                    ↓
                         Fila de processamento
                                    ↓
                    ┌───────────────┼───────────────┐
                Worker 1        Worker 2        Worker 3
                    ↓               ↓               ↓
              Instagram API   DeepSeek API    WordPress API
              (mídia paga)    (texto, cloud)  (publicação REST)
                    ↓               ↓               ↓
                         Resultado no painel
```

Sem browser para publicar. Sem browser para baixar mídia.
Só APIs. Seguro para paralelismo.

---

## Fase 1 — Plugin WordPress REST API
**Prioridade:** Crítica. Sem isso nada mais funciona em cloud.
**Dependências:** Nenhuma — pode começar agora.
**Duração estimada:** 3–4 semanas

### O que é

Um plugin WordPress próprio que expõe endpoints REST para o bot
criar imóveis, subir imagens e consultar configurações.
Elimina o Playwright preenchendo formulário — a parte mais frágil do sistema.

### Endpoints a construir

```
POST /wp-json/bot-mercadoi/v1/properties
     → cria rascunho ou publica imóvel
     → retorna { id, url_admin, url_publica }

POST /wp-json/bot-mercadoi/v1/properties/{id}/media
     → sobe imagens ao imóvel criado
     → retorna { sucesso, arquivos_enviados, erros }

GET  /wp-json/bot-mercadoi/v1/options
     → retorna cidades, bairros, tipos disponíveis no site
     → bot usa isso para validar antes de enviar

GET  /wp-json/bot-mercadoi/v1/properties/{id}
     → status e links do imóvel
```

### Campos mapeados pelo plugin

```
titulo          → post_title
descricao       → post_content + prop_des (TinyMCE)
preco           → prop_price
tipo_imovel     → prop_type[] (taxonomy)
operacao        → prop_status[]
cidade          → locality (select)
bairro          → neighborhood (select)
quartos         → prop_beds
suites          → prop_rooms
banheiros       → prop_baths
vagas           → prop_garage
area_m2         → prop_size
estagio         → estagio-da-obra-imóvel[]
faz_parceria    → faz-parceria (sempre "A combinar")
```

### Autenticação do plugin

API Key gerada no painel WordPress, salva no config.json do bot.
Cada request leva `Authorization: Bearer {api_key}`.

### O que muda no bot

`mercadoi_driver.py` (Playwright) é substituído por `wordpress_publisher.py`:

```python
class WordPressPublisher:
    async def publicar(self, dados, arquivos) -> dict:
        # 1. POST /properties → cria imóvel
        # 2. POST /properties/{id}/media → sobe imagens
        # 3. retorna url_admin e url_publica
```

O Chrome do Mercadoi deixa de ser necessário.

### Critérios de aceite

- [x] Plugin criado com boilerplate + autenticação Bearer (2026-04-28)
- [x] Endpoint de criação de imóvel implementado (2026-04-28)
- [x] Endpoint de upload de mídia implementado (2026-04-28)
- [x] Endpoint de opções (cidades/bairros/tipos) implementado (2026-04-28)
- [x] `wordpress_publisher.py` criado com mesma interface do `MercadoiDriver` (2026-04-28)
- [x] `main.py` atualizado — modo híbrido API/Playwright (2026-04-28)
- [ ] Cliente instala plugin e gera chave de API
- [ ] Criar rascunho sem abrir nenhum browser (teste real)
- [ ] Subir 10 imagens corretamente (teste real)
- [ ] Retornar url_admin e url_publica (teste real)
- [ ] Testar com 10 links reais do cliente

### Esforço estimado

| Tarefa | Status | Horas |
|---|---|---|
| Plugin boilerplate + autenticação | ✅ Feito | 4h |
| Endpoint de criação de imóvel | ✅ Feito | 6h |
| Endpoint de upload de mídia | ✅ Feito | 6h |
| Endpoint de opções (cidades/bairros/tipos) | ✅ Feito | 3h |
| wordpress_publisher.py no bot | ✅ Feito | 6h |
| Integração + testes com dados reais | 🔜 Aguardando cliente instalar | 8h |
| **Total** | | **~33h** |

---

## Fase 2 — Extração de mídia via API paga
**Prioridade:** Alta. Necessária para 180–300 links/dia.
**Dependências:** Independente — pode rodar em paralelo com Fase 1.
**Duração estimada:** 1–2 semanas

### Por que API paga é necessária

| Método | 180/dia | 300/dia |
|---|---|---|
| FastDL (atual) | Lento, instável | Muito lento |
| yt-dlp local | Bloqueado em dias | Bloqueado em horas |
| API paga | ✅ 3–8s, estável | ✅ 3–8s, estável |

### Serviço recomendado

**Apify — Instagram Post Scraper**
- Custo: ~R$ 150–250/mês para 180/dia
- Custo: ~R$ 250–400/mês para 300/dia
- API REST simples, retorna URLs das mídias
- Mantido por comunidade ativa

### Arquitetura com fallback

```
Link Instagram
      ↓
  API Apify (tentativa 1) ← 3-8s
      ↓ falhou?
  yt-dlp local (tentativa 2) ← 5-15s
      ↓ falhou?
  FastDL browser (tentativa 3) ← 30-60s
      ↓ falhou?
  Marca erro_download
```

### Critérios de aceite

- [ ] Baixar todas as imagens de carrossel com 10 posts reais
- [ ] Baixar vídeo e extrair frames corretamente
- [ ] Fallback para FastDL quando API falha
- [ ] Tempo médio abaixo de 10s por post

### Esforço estimado

| Tarefa | Horas |
|---|---|
| Pesquisar e contratar API | 2h |
| instagram_media_api.py | 5h |
| Lógica de fallback | 3h |
| Testes com 20 links reais | 4h |
| **Total** | **~14h** |

---

## Fase 3 — Processamento paralelo
**Prioridade:** Alta. Sem isso, 300 links/dia levam 10 horas.
**Dependências:** Fase 1 (WordPress API elimina conflito de browser).
**Duração estimada:** 1 semana

### Por que agora é seguro

O pipeline foi removido antes porque múltiplos Playwrights
conflitavam no mesmo Chrome. Com WordPress API não há mais
browser para publicação. Com API de mídia não há mais
browser para download. Paralelismo fica seguro.

### Arquitetura de workers

```python
MAX_WORKERS = 3  # configurável no painel

semaforo = asyncio.Semaphore(MAX_WORKERS)

async def processar_com_semaforo(link):
    async with semaforo:
        await processar_link(link)

await asyncio.gather(*[processar_com_semaforo(l) for l in pendentes])
```

### Tempo esperado com paralelismo

| Links | Workers | Tempo (35s/link) |
|---|---|---|
| 180/dia | 3 | ~35 min |
| 300/dia | 3 | ~58 min |
| 300/dia | 5 | ~35 min |

### Critérios de aceite

- [ ] 3 links processados simultaneamente sem erro
- [ ] Banco sem conflito de escrita
- [ ] Log identifica cada worker pelo execution_id
- [ ] Testar com 30 links reais simultâneos

### Esforço estimado

| Tarefa | Horas |
|---|---|
| Semáforo em executar_ciclo | 3h |
| Ajustes no banco (locks) | 2h |
| Config max_workers no painel | 2h |
| Testes de carga | 4h |
| **Total** | **~11h** |

---

## Fase 4 — Cloud (VPS + Docker)
**Prioridade:** Alta para produto, opcional para cliente atual.
**Dependências:** Fases 1, 2 e 3 completas.
**Duração estimada:** 2 semanas

### Infraestrutura

```
VPS 4GB RAM — R$ 80–120/mês
├── Nginx (proxy reverso + SSL gratuito)
├── Container Cliente A → clienteA.seudominio.com.br
└── Container Cliente B → clienteB.seudominio.com.br
```

Cada cliente é um container isolado com seu próprio banco,
configuração e workers.

### O que muda no código

| Item | Hoje | Na cloud |
|---|---|---|
| Caminhos | C:\Users\... | /data/cliente/ via env var |
| Chrome Mercadoi | Obrigatório | Eliminado (WordPress API) |
| Chrome DeepSeek | Visível, Windows | Headless, Linux |
| Chrome FastDL | Visível, Windows | Headless, Linux (fallback) |
| Painel | localhost:8000 | HTTPS via nginx |

### Critérios de aceite

- [ ] Painel acessível via HTTPS sem instalação local
- [ ] DeepSeek funciona headless no Linux
- [ ] 2 clientes rodando isolados no mesmo VPS
- [ ] Deploy de novo cliente em menos de 30 minutos

### Esforço estimado

| Tarefa | Horas |
|---|---|
| Remover dependências Windows | 5h |
| Headless Chrome no Linux | 4h |
| Dockerfile + docker-compose | 5h |
| Nginx + SSL + domínio | 4h |
| Script de onboarding | 4h |
| Deploy e testes | 6h |
| **Total** | **~28h** |

---

## Fase 5 — Novas fontes
**Prioridade:** Média — só após fases 1–4 estáveis.
**Dependências:** Fase 1 (WordPress API) + PropertyModel.
**Duração estimada:** 3–4 semanas

### PropertyModel unificado

```python
@dataclass
class PropertyModel:
    source_type: str        # "instagram" | "olx" | "orulo"
    source_url: str
    title: str
    description: str
    property_type: str
    business_type: str
    city: str
    neighborhood: str
    price: int | None
    bedrooms: int | None
    suites: int | None
    bathrooms: int | None
    parking_spots: int | None
    area_m2: float | None
    images: list[str]
    raw_text: str
```

### 5.1 OLX

Extração via HTTP puro — OLX embute JSON estruturado no HTML.

```python
class OlxSource:
    async def fetch(self, url: str) -> PropertyModel:
        html = await httpx.get(url, headers={...})
        dados = json.loads(re.search(r'__NEXT_DATA__.*?({.*})', html))
        return PropertyModel(...)
```

Sem browser. Tempo estimado: 2–3s por anúncio.
**Esforço:** ~18h

### 5.2 Orulo

Só implementar com credenciais de API confirmadas.
Não assumir sem acesso real em mãos.
**Esforço:** ~12h (com API documentada)

---

## Fase 6 — Painel e operação
**Prioridade:** Média. Vai sendo melhorado junto com outras fases.
**Duração estimada:** 1–2 semanas

### Melhorias

- [ ] Coluna "Origem" na fila (Instagram / OLX / Orulo)
- [ ] Status granulares: capturando, baixando_midia, enviando_wordpress
- [ ] Timeout automático: preso em processando por mais de 10 min vira erro
- [ ] Filtro por origem no histórico
- [ ] Link da publicação pública além do wp-admin
- [ ] Contador de workers ativos em tempo real
- [ ] Configuração de max_workers no painel

---

## Resumo total

| Fase | Descrição | Horas |
|---|---|---|
| 1 | WordPress REST API Plugin | ~33h |
| 2 | API de mídia Instagram | ~14h |
| 3 | Processamento paralelo | ~11h |
| 4 | Cloud (VPS + Docker) | ~28h |
| 5 | OLX + Orulo | ~30h |
| 6 | Painel e operação | ~15h |
| **Total** | | **~131h** |

Com IA assistindo: **8–10 semanas** em ritmo real.

---

## Ordem de entrega

```
Semanas 1–3  → Fase 1: Plugin WordPress (mais crítico)
Semana 4     → Fase 2: API mídia + Fase 3: Paralelo
Semanas 5–6  → Fase 4: Cloud
Semanas 7–8  → Fase 5: OLX
Contínuo     → Fase 6: Painel (evolui junto)
```

Cliente atual continua no modelo local sem interrupção
durante as fases 1–3. Migra para cloud na fase 4
com período de teste paralelo de 1 semana.

---

## Custo operacional mensal por cliente (cloud)

| Item | Custo/mês |
|---|---|
| VPS compartilhado (por cliente) | R$ 30–50 |
| API mídia Instagram (180/dia) | R$ 150–250 |
| Domínio + SSL | R$ 5 |
| **Total seu custo** | **R$ 185–305/mês** |

---

## Como cobrar

### Setup

| Escopo | Valor sugerido |
|---|---|
| Fases 1 + 2 + 3 (core) | R$ 8.000–10.000 |
| + Fase 4 (cloud) | + R$ 4.000–6.000 |
| + OLX | + R$ 3.000–4.000 |
| + Orulo (com credenciais) | + R$ 2.000–3.000 |
| **Fase 2 completa** | **R$ 17.000–23.000** |

### Mensal

| Item | Valor |
|---|---|
| Licença + suporte + cloud | R$ 800–1.200 |
| API de mídia (repassada ao custo) | R$ 150–400 |
| **Total cliente** | **R$ 950–1.600/mês** |

ROI do cliente: economiza R$ 3.600–6.000/mês em mão de obra.
Retorno líquido para ele: R$ 2.000–4.400/mês.

---

## Riscos

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Instagram bloqueia API paga | Baixa | SLA Apify, fallback FastDL |
| Orulo sem credenciais | Alta | Não incluir sem acesso real |
| Plugin conflita com tema WordPress | Média | Testar antes de cobrar |
| Custo API maior que estimado | Média | Monitorar e repassar |
| Cliente recusa migração para cloud | Baixa | Manter opção local como legado |

---

## Fora do escopo desta fase

- Substituir DeepSeek por outra IA
- SaaS multi-tenant com billing automatizado
- App mobile
- Publicar em OLX ou Orulo como destino
- Reescrever do zero
