# Distribuicao do Bot Mercadoi em EXE

## Gerar o executavel

No PowerShell, dentro da pasta do projeto:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean
```

Saida principal:

```text
dist\BotMercadoi.exe
dist\config.json
```

## Configurar antes de entregar

Edite `dist\config.json` e configure pelo menos:

```json
{
  "licenciamento_habilitado": true,
  "licenca_chave": "CHAVE_DO_CLIENTE",
  "licenca_servidor_url": "https://seu-servidor-de-licenca.com"
}
```

Nao coloque sua chave real de API ou arquivos sensiveis no pacote do cliente sem necessidade.

## Abrir o painel

Use:

```text
Abrir Painel EXE.vbs
```

O launcher aguarda ate 120 segundos porque o executavel onefile precisa extrair dependencias antes de subir o painel.

## O que entregar ao cliente

Modelo recomendado:

```text
dist\BotMercadoi.exe
dist\config.json
Abrir Painel EXE.vbs
```

O banco `botmercadoi.db`, `license_cache.json` e logs sao gerados localmente no computador do cliente.

## Observacoes de protecao

- O `.exe` dificulta copia/leitura do codigo, mas nao e inviolavel.
- A protecao comercial principal continua sendo a licenca online.
- Para uma proxima camada, considere assinar digitalmente o executavel e migrar de PyInstaller para Nuitka.
