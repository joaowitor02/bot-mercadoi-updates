"""
Gerenciador de banco de dados local (SQLite).
Substitui o Google Sheets como camada de persistência.
O banco é criado automaticamente em botmercadoi.db na pasta do projeto.
"""

import sqlite3
import threading
from modules.logger import Logger

logger = Logger("database_manager")

_CAMPOS_VALIDOS = frozenset({
    "url_instagram", "status", "titulo_gerado", "tipo_midia",
    "arquivo_midia", "cidade_aplicada", "bairro_aplicado",
    "fim_processamento", "resultado", "mensagem_erro", "id_execucao",
    "mercadoi_url", "tentativas",
})

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS imoveis (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url_instagram     TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'pendente',
    titulo_gerado     TEXT    DEFAULT '',
    tipo_midia        TEXT    DEFAULT '',
    arquivo_midia     TEXT    DEFAULT '',
    cidade_aplicada   TEXT    DEFAULT '',
    bairro_aplicado   TEXT    DEFAULT '',
    fim_processamento TEXT    DEFAULT '',
    resultado         TEXT    DEFAULT '',
    mensagem_erro     TEXT    DEFAULT '',
    id_execucao       TEXT    DEFAULT '',
    mercadoi_url      TEXT    DEFAULT '',
    tentativas        INTEGER DEFAULT 0,
    criado_em         TEXT    DEFAULT (datetime('now', 'localtime')),
    atualizado_em     TEXT    DEFAULT (datetime('now', 'localtime'))
)
"""


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        logger.debug(f"Banco de dados iniciado: {db_path}")

    # ------------------------------------------------------------------
    # Conexão
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock:
            with self._conn() as conn:
                conn.execute(_CREATE_TABLE)
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(imoveis)").fetchall()}
                if "mercadoi_url" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN mercadoi_url TEXT DEFAULT ''")
                if "tentativas" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN tentativas INTEGER DEFAULT 0")
                conn.commit()

    def _to_dict(self, row) -> dict:
        d = dict(row)
        d["_row_index"] = d.pop("id")
        return d

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def _todas_as_linhas(self) -> tuple[list[str], list[dict]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM imoveis ORDER BY id DESC").fetchall()
        headers = list(_CAMPOS_VALIDOS)
        return headers, [self._to_dict(r) for r in rows]

    def listar_pendentes(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM imoveis WHERE status='pendente' ORDER BY id"
            ).fetchall()

        pendentes = []
        urls_vistas: set[str] = set()
        for row in rows:
            d = self._to_dict(row)
            url = d.get("url_instagram", "").strip()
            if not url:
                continue
            if url in urls_vistas:
                logger.info(f"URL duplicada ignorada (id {d['id']}): {url[:60]}")
                continue
            urls_vistas.add(url)
            pendentes.append(d)

        if pendentes:
            logger.info(f"Pendentes encontrados: {len(pendentes)}")
        else:
            logger.debug("Pendentes encontrados: 0")
        return pendentes

    def listar_todos(self, limite: int = 200) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM imoveis ORDER BY id DESC LIMIT ?", (limite,)
            ).fetchall()
        return [self._to_dict(r) for r in rows]

    def limpar_pendentes(self) -> int:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute("DELETE FROM imoveis WHERE status='pendente'")
                conn.commit()
        count = cur.rowcount
        if count:
            logger.info(f"{count} item(ns) pendente(s) removido(s)")
        return count

    def limpar_erros(self) -> int:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute("DELETE FROM imoveis WHERE status LIKE 'erro%'")
                conn.commit()
        count = cur.rowcount
        if count:
            logger.info(f"{count} erro(s) removido(s)")
        return count

    def resetar_travados(self) -> int:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "UPDATE imoveis SET status='erro_interrompido', "
                    "resultado='Falha', "
                    "mensagem_erro='Processamento anterior foi interrompido antes de concluir', "
                    "fim_processamento=datetime('now','localtime'), "
                    "atualizado_em=datetime('now','localtime') "
                    "WHERE status='processando'"
                )
                conn.commit()
        count = cur.rowcount
        if count:
            logger.warning(f"{count} item(ns) travado(s) marcados como erro_interrompido")
        return count

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    def adicionar_pendente(self, url: str) -> int:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "INSERT INTO imoveis (url_instagram, status) VALUES (?, 'pendente')",
                    (url.strip(),),
                )
                conn.commit()
                row_id = cur.lastrowid
        logger.info(f"URL adicionada (id {row_id}): {url[:60]}")
        return row_id

    def resetar_url(self, url: str) -> bool:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT id, status FROM imoveis WHERE url_instagram=? ORDER BY id DESC LIMIT 1",
                    (url.strip(),),
                ).fetchone()
                if not row:
                    logger.warning(f"URL não encontrada: {url[:60]}")
                    return False
                if row["status"].lower() in ("pendente", "processando"):
                    logger.info(f"URL já está como '{row['status']}': {url[:60]}")
                    return True
                conn.execute(
                    "UPDATE imoveis SET status='pendente', mensagem_erro='', "
                    "tentativas=tentativas+1, "
                    "atualizado_em=datetime('now','localtime') WHERE id=?",
                    (row["id"],),
                )
                conn.commit()
        logger.info(f"URL resetada para pendente: {url[:60]}")
        return True

    def atualizar_status(self, row_index: int, status: str):
        self.atualizar_campo(row_index, "status", status)

    def atualizar_campo(self, row_index: int, campo: str, valor: str):
        if campo not in _CAMPOS_VALIDOS:
            logger.debug(f"Campo '{campo}' não existe, ignorando")
            return
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE imoveis SET {campo}=?, "
                    f"atualizado_em=datetime('now','localtime') WHERE id=?",
                    (valor, row_index),
                )
                conn.commit()
        logger.debug(f"Campo '{campo}' atualizado (id {row_index}): {valor}")
