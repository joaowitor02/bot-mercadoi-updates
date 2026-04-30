"""
Diagnóstico rápido de uma URL do OLX.
Uso: python testar_olx.py "https://www.olx.com.br/..."
"""

import asyncio
import sys
import json

async def main():
    if len(sys.argv) < 2:
        print("Uso: python testar_olx.py <url_olx>")
        sys.exit(1)

    url = sys.argv[1]
    print(f"\n=== Testando OLX: {url} ===\n")

    from modules.olx_scraper import OlxScraper, url_valida, _extrair_next_data, _parse_ad

    # 1) Validação de URL
    if not url_valida(url):
        print("FALHA: URL não reconhecida como OLX válida")
        print("  Formato esperado: https://www.olx.com.br/...")
        sys.exit(1)
    print("OK: URL válida\n")

    scraper = OlxScraper()

    # 2) Busca HTTP
    print("Tentando httpx...")
    html = await scraper._fetch_httpx(url)
    if html == "404":
        print("FALHA: Anúncio não encontrado (404)")
        sys.exit(1)
    elif html in (None, "403"):
        print("AVISO: HTTP bloqueado (Cloudflare?) — tentando Playwright...")
        html = await scraper._fetch_playwright(url)
        if not html:
            print("FALHA: Playwright também bloqueado. Verifique conexão/VPS.")
            sys.exit(1)
        print("OK: Playwright funcionou\n")
    else:
        print(f"OK: HTTP retornou HTML ({len(html)} chars)\n")

    # 3) Extração __NEXT_DATA__
    next_data = _extrair_next_data(html)
    if not next_data:
        print("FALHA: __NEXT_DATA__ não encontrado no HTML")
        print("  O OLX pode ter mudado a estrutura da página")
        # Salva HTML para inspeção
        with open("olx_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("  HTML salvo em olx_debug.html para inspeção")
        sys.exit(1)
    print("OK: __NEXT_DATA__ encontrado\n")

    # 4) Parse do anúncio
    pp = next_data.get("props", {}).get("pageProps", {})
    print(f"Chaves em pageProps: {list(pp.keys()) if isinstance(pp, dict) else pp}\n")

    ad = _parse_ad(next_data)
    if not ad:
        print("FALHA: Objeto de anúncio não encontrado no JSON")
        print("  Salvando JSON em olx_debug.json para análise...")
        with open("olx_debug.json", "w", encoding="utf-8") as f:
            json.dump(next_data, f, ensure_ascii=False, indent=2)
        print("  Arquivo salvo. Envie olx_debug.json para suporte.")
        sys.exit(1)
    print(f"OK: Anúncio encontrado\n")
    print(f"  subject/title: {ad.get('subject') or ad.get('title')}")
    print(f"  body presente: {'body' in ad}")
    print(f"  price: {ad.get('price')}")
    print(f"  images: {len(ad.get('images') or ad.get('photos') or [])} foto(s)")

    # 5) Resultado final
    resultado = await scraper.extrair(url)
    if not resultado.get("ok"):
        print(f"\nFALHA na extração: {resultado.get('motivo')}")
        sys.exit(1)

    dados = resultado["dados"]
    print("\n=== DADOS EXTRAÍDOS ===")
    for k, v in dados.items():
        if v and k != "descricao_util":
            print(f"  {k}: {v}")
    print(f"  descricao_util: {str(dados.get('descricao_util',''))[:80]}...")
    print(f"\n  {len(resultado.get('imagens_urls', []))} URL(s) de imagem encontradas")
    print("\nSUCESSO! OLX funcionando corretamente.")

asyncio.run(main())
