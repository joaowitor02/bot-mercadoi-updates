"""
Script de patch para aplicar correções de VPS.
Execute na VPS: python3 patch_vps.py
"""
import os, sys, re

BASE = os.path.dirname(os.path.abspath(__file__))
erros = []

def patch(arquivo, antigo, novo, descricao):
    caminho = os.path.join(BASE, arquivo)
    try:
        texto = open(caminho, encoding="utf-8").read()
        if novo.strip() in texto:
            print(f"  [OK] {descricao} (já aplicado)")
            return
        if antigo not in texto:
            print(f"  [AVISO] {descricao} — trecho não encontrado, pulando")
            erros.append(descricao)
            return
        open(caminho, "w", encoding="utf-8").write(texto.replace(antigo, novo, 1))
        print(f"  [✓] {descricao}")
    except Exception as e:
        print(f"  [ERRO] {descricao}: {e}")
        erros.append(descricao)


print("=== Patch VPS Bot Mercadoi ===\n")

# --- Fix 1: main.py — auto-fix VPS na executar_ciclo ---
patch(
    "main.py",
    '''    db_path   = config.get("db_path", os.path.join(data_dir, "botmercadoi.db"))
    db = DatabaseManager(db_path)''',
    '''    # Correções automáticas para VPS (Linux) — sem alterar o config.json em disco
    if sys.platform != "win32":
        config = dict(config)
        # Força modo DeepSeek API se a chave estiver configurada mas browser mode estiver ativo
        if not config.get("usar_deepseek_api") and config.get("deepseek_api_key", "").strip():
            config["usar_deepseek_api"] = True
            print("[VPS] DeepSeek API ativado automaticamente (Chrome nao disponivel no Linux)")
        # Corrige downloads_path com caminho Windows (C:\\...) ou vazio
        dp = config.get("downloads_path", "")
        if not dp or not dp.startswith("/"):
            config["downloads_path"] = os.path.join(data_dir, "downloads")
            os.makedirs(config["downloads_path"], exist_ok=True)
            print(f"[VPS] downloads_path corrigido para {config['downloads_path']}")

    db_path   = config.get("db_path", os.path.join(data_dir, "botmercadoi.db"))
    db = DatabaseManager(db_path)''',
    "main.py — auto-fix VPS (DeepSeek API + downloads_path)"
)

# --- Fix 2: mercadoi_driver.py — _garantir_login sem exigir credenciais para cookies ---
patch(
    "modules/mercadoi_driver.py",
    '''        if sys.platform != "win32" and self._wp_user and self._wp_pass:
            if await self._carregar_sessao(page):
                return
            try:
                await self._fazer_login_httpx(page)
            except Exception as e:
                logger.warning(f"Login via httpx falhou, tentando formulario do wp-login.php: {e}")
                await self._fazer_login_formulario(page)
            return

        if sys.platform != "win32":
            raise Exception(
                "Mercadoi nao autenticado no VPS. Use wordpress_xmlrpc/Application Password, "
                "ou mantenha um perfil/cookies validos em /data para o Chromium headless."
            )''',
    '''        if sys.platform != "win32":
            # Tenta cookies salvos independente de ter credenciais
            if await self._carregar_sessao(page):
                return
            # Login automático se credenciais estiverem configuradas
            if self._wp_user and self._wp_pass:
                try:
                    await self._fazer_login_httpx(page)
                except Exception as e:
                    logger.warning(f"Login via httpx falhou, tentando formulario do wp-login.php: {e}")
                    await self._fazer_login_formulario(page)
                return
            raise Exception(
                "Mercadoi nao autenticado no VPS. Opcoes: "
                "(1) configure wordpress_xmlrpc_user/password para login automatico, ou "
                "(2) exporte a sessao do Chrome Windows pelo painel e transfira mercadoi_session.json para /data/."
            )''',
    "mercadoi_driver.py — login VPS sem exigir credenciais para cookies"
)

print()
if erros:
    print(f"ATENÇÃO: {len(erros)} patch(es) não aplicado(s): {erros}")
    sys.exit(1)
else:
    print("Todos os patches aplicados com sucesso!")
    print("Reinicie o painel para que as mudanças tenham efeito.")
