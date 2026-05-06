"""
Script de teste: publica um imóvel fake com TODOS os campos de detalhes preenchidos.
Serve para validar quais campos são salvos corretamente via XML-RPC.

Uso:
    python testar_imovel_fake.py
"""

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from modules.mercadoi_driver import MercadoiDriver
from modules.logger import Logger

logger = Logger("teste_fake")


DADOS_FAKE = {
    # Identificação da fonte
    "_fonte": "orulo",
    "_forcar_rascunho": True,          # sempre rascunho no teste

    # Básicos
    "titulo": "TESTE BOT - Apartamento Completo Fake",
    "descricao_util": (
        "Imóvel de teste gerado automaticamente pelo bot para validar "
        "o preenchimento de todos os campos de detalhes. "
        "NÃO PUBLICAR. Por favor deletar após verificação."
    ),
    "tipo_imovel": "Apartamento",
    "operacao": "A Venda",
    "preco": "850000",

    # Dimensões
    "quartos": "3",
    "suites": "1",
    "banheiros": "2",
    "vagas": "2",
    "area_m2": "120",
    "area_terreno": "",

    # Localização
    "bairro_extraido": "Manaíra",
    "cidade_extraida": "João Pessoa",
    "endereco": "Av. Flávio Ribeiro Coutinho, 800",
    "cep": "58038000",
    "latitude": "-7.0897",
    "longitude": "-34.8472",

    # --- Campos de detalhes (o que estamos testando) ---
    "estagio_imovel": "Em Construção",           # → "3- Em Contrução"
    "elevador": "Sim",                            # → "Sim"
    "mobiliado": "Semi-mobiliado",                # → "Semi-mobiliado"
    "escriturado": "Sim",                         # → "Sim"
    "aceita_permuta": "Não",                      # → "Não"
    "aceita_financiamento": "Sim",                # → "Sim"
    "aceita_airbnb": "Sim",                       # → "Sim"
    "posicao_solar": "Sol da manhã",              # → "Sol da manhã"
    "posicao_predio": "Frente",                   # → "Frente"
    "andar": "5",                                  # → "5" no select Andar
    "perto_do_mar": "Próximo ao mar",             # → "Próximo ao mar"
    "ano_construcao": "2026",
    "condominio": "R$ 800",
    "iptu": "",
    "proximidades": "Próximo ao Shopping Manaíra",

    # Corretor Orulo
    "_mercadoi_agent_id": "37016",

    # Sem mídia no teste
    "url_publicacao": "https://www.orulo.com.br/building/99999",
    "whatsapp_url": "",
    "instagram_url": "",
    "caracteristicas": [
        "Elevador", "Piscina", "Academia", "Portaria", "Churrasqueira"
    ],
}


async def main():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    base_url   = (config.get("mercadoi_url") or config.get("wordpress_url") or config.get("wordpress_xmlrpc_url", "")).rstrip("/") + "/"
    wp_user    = config.get("wordpress_xmlrpc_user", "")
    wp_pass    = config.get("wordpress_xmlrpc_password", "")

    print(f"\n{'='*60}")
    print("TESTE: Imóvel fake com todos os campos de detalhes")
    print(f"{'='*60}")
    print(f"URL: {base_url}")
    print(f"Usuário WP: {wp_user}")
    print()

    async with MercadoiDriver(base_url, wp_user=wp_user, wp_pass=wp_pass) as driver:
        # Sem arquivo de mídia — vai salvar rascunho sem imagem
        resultado = await driver.preencher_e_salvar(DADOS_FAKE, "imagem", [])

    print(f"\n{'='*60}")
    print("RESULTADO:")
    print(f"  sucesso      : {resultado.get('sucesso')}")
    print(f"  mensagem     : {resultado.get('mensagem')}")
    print(f"  url          : {resultado.get('mercadoi_url')}")
    print(f"  status_erro  : {resultado.get('status_erro', '-')}")
    print(f"{'='*60}\n")

    if resultado.get("sucesso"):
        print("✓ Rascunho criado. Abra o link acima no WP Admin e verifique:")
        print("  [ ] Estágio do Imóvel   → 3- Em Contrução")
        print("  [ ] Elevador            → Sim")
        print("  [ ] Mobiliado           → Semi-mobiliado")
        print("  [ ] Escriturado         → Sim")
        print("  [ ] Aceita Permuta      → Não")
        print("  [ ] Aceita Financiamento→ Sim")
        print("  [ ] Aceita Airbnb       → Sim")
        print("  [ ] Posição Solar       → Sol da manhã")
        print("  [ ] Posição no Prédio   → Frente")
        print("  [ ] Andar               → 5
  [ ] Perto do Mar        → Próximo ao mar")
        print("  [ ] Ano de construção   → 2026")
        print("  [ ] Condomínio          → R$ 800")
        print("  [ ] Corretor            → Agustin Machado")
    else:
        print("✗ Falhou. Veja o log para detalhes.")


if __name__ == "__main__":
    asyncio.run(main())
