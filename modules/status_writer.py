"""
Módulo para persistir status, tempos e mensagens de erro na planilha.
"""

from datetime import datetime
from modules.logger import Logger

logger = Logger("status_writer")


class StatusWriter:
    def __init__(self, sheet, row_index: int):
        self.sheet = sheet
        self.row_index = row_index

    def sucesso(self, status: str, mensagem: str = ""):
        """Grava status de sucesso e fim do processamento."""
        try:
            self.sheet.atualizar_campo(self.row_index, "status", status)
            self.sheet.atualizar_campo(self.row_index, "resultado", mensagem)
            self.sheet.atualizar_campo(self.row_index, "fim_processamento", datetime.now().isoformat())
            self.sheet.atualizar_campo(self.row_index, "mensagem_erro", "")
            logger.info(f"Status gravado: {status} — {mensagem}")
        except Exception as e:
            logger.error(f"Erro ao gravar sucesso: {e}")

    def erro(self, status: str, mensagem: str):
        """Grava status de erro e detalhes."""
        try:
            self.sheet.atualizar_campo(self.row_index, "status", status)
            self.sheet.atualizar_campo(self.row_index, "resultado", "Falha")
            self.sheet.atualizar_campo(self.row_index, "mensagem_erro", mensagem)
            self.sheet.atualizar_campo(self.row_index, "fim_processamento", datetime.now().isoformat())
            logger.error(f"Status de erro gravado: {status} — {mensagem}")
        except Exception as e:
            logger.error(f"Erro ao gravar status de erro: {e}")
