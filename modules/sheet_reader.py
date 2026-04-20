"""
Módulo de leitura e atualização da planilha Google Sheets.
Colunas esperadas (linha 1 = cabeçalho):
  url_instagram, status, titulo_gerado, tipo_midia, arquivo_midia,
  cidade_aplicada, bairro_aplicado, fim_processamento,
  resultado, mensagem_erro, id_execucao
"""

from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from modules.logger import Logger

logger = Logger("sheet_reader")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetReader:
    def __init__(self, credentials_path: str, spreadsheet_id: str, sheet_name: str = "Página1"):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name

        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        self.service = build("sheets", "v4", credentials=creds)
        self.sheet = self.service.spreadsheets()

        self._headers = None
        self._carregar_headers()

    def _carregar_headers(self):
        result = self.sheet.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!1:1"
        ).execute()
        values = result.get("values", [[]])
        self._headers = [h.strip().lower() for h in (values[0] if values else [])]
        logger.debug(f"Cabeçalhos carregados: {self._headers}")

    def _col_index(self, nome: str) -> int:
        try:
            return self._headers.index(nome.strip().lower())
        except ValueError:
            raise ValueError(f"Coluna '{nome}' não encontrada. Disponíveis: {self._headers}")

    def _col_letter(self, nome: str) -> str:
        idx = self._col_index(nome)
        result = ""
        while idx >= 0:
            result = chr(idx % 26 + ord("A")) + result
            idx = idx // 26 - 1
        return result

    def _todas_as_linhas(self) -> tuple[list[str], list[dict]]:
        """Retorna (headers, lista de row_dicts com _row_index)."""
        result = self.sheet.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A:Z"
        ).execute()
        all_rows = result.get("values", [])
        if len(all_rows) < 2:
            return [], []
        headers = [h.strip().lower() for h in all_rows[0]]
        rows = []
        for i, row in enumerate(all_rows[1:], start=2):
            while len(row) < len(headers):
                row.append("")
            rd = dict(zip(headers, row))
            rd["_row_index"] = i
            rows.append(rd)
        return headers, rows

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def listar_pendentes(self) -> list[dict]:
        """
        Retorna linhas com status 'pendente', sem duplicatas de URL.
        Se a mesma URL aparecer mais de uma vez, mantém apenas a primeira ocorrência.
        """
        _, rows = self._todas_as_linhas()
        pendentes = []
        urls_vistas: set[str] = set()

        for row in rows:
            status = row.get("status", "").strip().lower()
            url = row.get("url_instagram", "").strip()
            if not url or status != "pendente":
                continue
            if url in urls_vistas:
                logger.warning(f"URL duplicada ignorada (linha {row['_row_index']}): {url[:60]}")
                continue
            urls_vistas.add(url)
            pendentes.append(row)

        logger.info(f"Linhas pendentes encontradas: {len(pendentes)}")
        return pendentes

    def resetar_travados(self) -> int:
        """
        Reseta itens com status 'processando' para 'pendente'.
        Chamado no início de cada ciclo para recuperar execuções que travaram.
        """
        _, rows = self._todas_as_linhas()
        count = 0
        for row in rows:
            if row.get("status", "").strip().lower() == "processando":
                self.atualizar_campo(row["_row_index"], "status", "pendente")
                count += 1
        if count:
            logger.warning(f"{count} item(ns) travado(s) em 'processando' foram resetados para 'pendente'")
        return count

    def adicionar_pendente(self, url: str) -> int:
        """
        Adiciona nova linha com url_instagram e status='pendente'.
        Retorna o índice da linha criada.
        """
        result = self.sheet.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A:A"
        ).execute()
        next_row = len(result.get("values", [])) + 1

        n_cols = len(self._headers)
        row = [""] * n_cols

        try:
            row[self._col_index("url_instagram")] = url
        except ValueError:
            pass
        try:
            row[self._col_index("status")] = "pendente"
        except ValueError:
            pass

        self.sheet.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A{next_row}",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()
        logger.info(f"URL adicionada na linha {next_row}: {url[:60]}")
        return next_row

    def resetar_url(self, url: str) -> bool:
        """
        Reseta uma URL específica de volta para 'pendente'.
        Usado pelo painel para reprocessar itens com falha.
        Retorna True se encontrou e resetou, False se não encontrou.
        """
        _, rows = self._todas_as_linhas()
        for row in rows:
            if row.get("url_instagram", "").strip() == url.strip():
                status = row.get("status", "").strip().lower()
                if status in ("pendente", "processando"):
                    logger.info(f"URL já está como '{status}', nada a fazer: {url[:60]}")
                    return True
                self.atualizar_campo(row["_row_index"], "status", "pendente")
                self.atualizar_campo(row["_row_index"], "mensagem_erro", "")
                logger.info(f"URL resetada para pendente (linha {row['_row_index']}): {url[:60]}")
                return True
        logger.warning(f"URL não encontrada na planilha: {url[:60]}")
        return False

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    def atualizar_status(self, row_index: int, status: str):
        self.atualizar_campo(row_index, "status", status)

    def atualizar_campo(self, row_index: int, campo: str, valor: str):
        try:
            col = self._col_letter(campo)
            self.sheet.values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.sheet_name}!{col}{row_index}",
                valueInputOption="RAW",
                body={"values": [[valor]]},
            ).execute()
            logger.debug(f"Campo '{campo}' atualizado na linha {row_index}: {valor}")
        except ValueError as e:
            logger.debug(f"Coluna não encontrada, ignorando: {e}")
        except Exception as e:
            logger.error(f"Erro ao atualizar campo '{campo}' linha {row_index}: {e}")
