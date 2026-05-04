"""
Módulo de logging com timestamp e suporte a UTF-8 no terminal Windows.
"""

import logging
import os
import sys
from datetime import datetime


def _configurar_utf8():
    """Força UTF-8 no stdout/stderr do Windows para evitar caracteres �."""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


_configurar_utf8()


class Logger:
    def __init__(self, nome: str):
        data_dir = os.environ.get("BOT_DATA_DIR", "")
        logs_dir = os.path.join(data_dir, "logs") if data_dir else "logs"
        os.makedirs(logs_dir, exist_ok=True)
        log_file = os.path.join(logs_dir, f"{datetime.now().strftime('%Y-%m-%d')}.log")

        self.logger = logging.getLogger(nome)
        self.logger.setLevel(logging.DEBUG)

        if not self.logger.handlers:
            fmt = "[%(asctime)s] %(levelname)s | %(name)s | %(message)s"

            # Console — UTF-8 explícito no Windows
            ch = logging.StreamHandler(stream=sys.stdout)
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter(fmt, "%H:%M:%S"))
            self.logger.addHandler(ch)

            # Arquivo — sempre UTF-8
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(fmt))
            self.logger.addHandler(fh)

    def info(self, msg): self.logger.info(msg)
    def error(self, msg): self.logger.error(msg)
    def debug(self, msg): self.logger.debug(msg)
    def warning(self, msg): self.logger.warning(msg)
