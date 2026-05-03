"""
Gerenciador de banco de dados local (SQLite).
Substitui o Google Sheets como camada de persistência.
O banco é criado automaticamente em botmercadoi.db na pasta do projeto.
"""

import sqlite3
import threading
import json
from contextlib import contextmanager
from modules.logger import Logger

logger = Logger("database_manager")

_CAMPOS_VALIDOS = frozenset({
    "url_instagram", "status", "titulo_gerado", "tipo_midia",
    "arquivo_midia", "cidade_aplicada", "bairro_aplicado",
    "fim_processamento", "resultado", "mensagem_erro", "id_execucao",
    "mercadoi_url", "tentativas", "tempo_seg", "url_publica", "origem",
    "captura_seg", "midia_seg", "publicacao_seg", "prioridade",
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
    tempo_seg         INTEGER DEFAULT 0,
    captura_seg       INTEGER DEFAULT 0,
    midia_seg         INTEGER DEFAULT 0,
    publicacao_seg    INTEGER DEFAULT 0,
    url_publica       TEXT    DEFAULT '',
    origem            TEXT    DEFAULT '',
    prioridade        INTEGER DEFAULT 0,
    criado_em         TEXT    DEFAULT (datetime('now', 'localtime')),
    atualizado_em     TEXT    DEFAULT (datetime('now', 'localtime'))
)
"""

_CREATE_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS extracao_cache (
    url          TEXT PRIMARY KEY,
    origem       TEXT DEFAULT '',
    dados_json   TEXT NOT NULL,
    imagens_json TEXT DEFAULT '[]',
    criado_em    TEXT DEFAULT (datetime('now', 'localtime')),
    atualizado_em TEXT DEFAULT (datetime('now', 'localtime'))
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

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._lock:
            with self._conn() as conn:
                conn.execute(_CREATE_TABLE)
                conn.execute(_CREATE_CACHE_TABLE)
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(imoveis)").fetchall()}
                if "mercadoi_url" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN mercadoi_url TEXT DEFAULT ''")
                if "tentativas" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN tentativas INTEGER DEFAULT 0")
                if "tempo_seg" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN tempo_seg INTEGER DEFAULT 0")
                if "captura_seg" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN captura_seg INTEGER DEFAULT 0")
                if "midia_seg" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN midia_seg INTEGER DEFAULT 0")
                if "publicacao_seg" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN publicacao_seg INTEGER DEFAULT 0")
                if "url_publica" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN url_publica TEXT DEFAULT ''")
                if "origem" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN origem TEXT DEFAULT ''")
                if "prioridade" not in cols:
                    conn.execute("ALTER TABLE imoveis ADD COLUMN prioridade INTEGER DEFAULT 0")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_imoveis_status_id ON imoveis(status, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_imoveis_url ON imoveis(url_instagram)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_imoveis_atualizado ON imoveis(atualizado_em)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_imoveis_fila ON imoveis(status, prioridade DESC, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_atualizado ON extracao_cache(atualizado_em)")
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
                "SELECT * FROM imoveis WHERE status='pendente' ORDER BY prioridade DESC, id"
            ).fetchall()

        pendentes = []
        urls_vistas: set[str] = set()
        for row in rows:
            d = self._to_dict(row)
            url = d.get("url_instagram", "").strip()
            if not url:
                continue
            if url in urls_vistas:
                logger.info(f"URL duplicada ignorada (id {d['_row_index']}): {url[:60]}")
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
                    "WHERE status IN ('processando','capturando','baixando_midia','enviando_wp')"
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
                    "UPDATE imoveis SET "
                    "status='pendente', titulo_gerado='', tipo_midia='', arquivo_midia='', "
                    "cidade_aplicada='', bairro_aplicado='', fim_processamento='', resultado='', "
                    "mensagem_erro='', id_execucao='', mercadoi_url='', url_publica='', "
                    "tempo_seg=0, captura_seg=0, midia_seg=0, publicacao_seg=0, "
                    "tentativas=tentativas+1, "
                    "atualizado_em=datetime('now','localtime') WHERE id=?",
                    (row["id"],),
                )
                conn.commit()
        logger.info(f"URL resetada para pendente: {url[:60]}")
        return True

    def priorizar_item(self, row_index: int) -> bool:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT id, status FROM imoveis WHERE id=?", (row_index,)).fetchone()
                if not row:
                    return False
                if row["status"] in ("processando", "capturando", "baixando_midia", "enviando_wp"):
                    return False
                prioridade = conn.execute("SELECT COALESCE(MAX(prioridade), 0) + 1 AS p FROM imoveis").fetchone()["p"]
                conn.execute(
                    "UPDATE imoveis SET status='pendente', prioridade=?, "
                    "mensagem_erro='', resultado='', atualizado_em=datetime('now','localtime') "
                    "WHERE id=?",
                    (prioridade, row_index),
                )
                conn.commit()
        logger.info(f"Item priorizado (id {row_index})")
        return True

    def atualizar_status(self, row_index: int, status: str):
        self.atualizar_campo(row_index, "status", status)

    def resetar_timeout(self, minutos: int = 10) -> int:
        """Marca como erro itens presos em 'processando' por mais de N minutos."""
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "UPDATE imoveis SET "
                    "status='erro_timeout', "
                    "mensagem_erro='Processamento excedeu o tempo limite', "
                    "resultado='Falha', "
                    "fim_processamento=datetime('now','localtime'), "
                    "atualizado_em=datetime('now','localtime') "
                    "WHERE status IN ('processando','capturando','baixando_midia','enviando_wp') "
                    "AND atualizado_em < datetime('now', ?, 'localtime')",
                    (f"-{minutos} minutes",),
                )
                conn.commit()
        count = cur.rowcount
        if count:
            logger.warning(f"Timeout: {count} item(ns) presos em processamento marcados como erro_timeout")
        return count

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

    def registrar_publicacao_extra(
        self,
        base_row_index: int,
        dados: dict,
        resultado: dict,
        status: str,
        execution_id: str,
        origem: str = "Órulo",
        tipo_midia: str = "",
        arquivo_midia: str = "",
        tempo_seg: int = 0,
        captura_seg: int = 0,
        midia_seg: int = 0,
        publicacao_seg: int = 0,
    ) -> int:
        """Cria uma linha concluida no historico para publicacoes extras do mesmo link."""
        with self._lock:
            with self._conn() as conn:
                base = conn.execute(
                    "SELECT url_instagram FROM imoveis WHERE id=?",
                    (base_row_index,),
                ).fetchone()
                url = (base["url_instagram"] if base else "") or dados.get("url_publicacao", "")
                cur = conn.execute(
                    "INSERT INTO imoveis ("
                    "url_instagram, status, titulo_gerado, tipo_midia, arquivo_midia, "
                    "cidade_aplicada, bairro_aplicado, fim_processamento, resultado, "
                    "mensagem_erro, id_execucao, mercadoi_url, url_publica, origem, "
                    "tempo_seg, captura_seg, midia_seg, publicacao_seg"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'), ?, '', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        url,
                        status,
                        dados.get("titulo", ""),
                        tipo_midia or "",
                        arquivo_midia or "",
                        resultado.get("cidade_aplicada", "") or dados.get("cidade_extraida", ""),
                        resultado.get("bairro_aplicado", "") or dados.get("bairro_extraido", ""),
                        resultado.get("mensagem", ""),
                        execution_id,
                        resultado.get("mercadoi_url", ""),
                        resultado.get("url_publica", ""),
                        origem,
                        int(tempo_seg or 0),
                        int(captura_seg or 0),
                        int(midia_seg or 0),
                        int(publicacao_seg or 0),
                    ),
                )
                conn.commit()
                row_id = cur.lastrowid
        logger.info(f"Publicacao extra registrada no historico (id {row_id})")
        return row_id

    # ------------------------------------------------------------------
    # Cache leve de extracao
    # ------------------------------------------------------------------

    def obter_cache(self, url: str, ttl_horas: int = 12) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM extracao_cache "
                "WHERE url=? AND atualizado_em >= datetime('now', ?, 'localtime')",
                (url.strip(), f"-{int(ttl_horas)} hours"),
            ).fetchone()
        if not row:
            return None
        try:
            return {
                "dados": json.loads(row["dados_json"] or "{}"),
                "imagens_urls": json.loads(row["imagens_json"] or "[]"),
                "origem": row["origem"] or "",
                "atualizado_em": row["atualizado_em"] or "",
            }
        except Exception as e:
            logger.warning(f"Cache invalido para {url[:60]}: {e}")
            return None

    def salvar_cache(self, url: str, origem: str, dados: dict, imagens_urls: list | None = None):
        if not dados:
            return
        payload = json.dumps(dados, ensure_ascii=False)
        imagens = json.dumps(imagens_urls or [], ensure_ascii=False)
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO extracao_cache (url, origem, dados_json, imagens_json) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(url) DO UPDATE SET "
                    "origem=excluded.origem, dados_json=excluded.dados_json, "
                    "imagens_json=excluded.imagens_json, atualizado_em=datetime('now','localtime')",
                    (url.strip(), origem, payload, imagens),
                )
                conn.commit()

    def limpar_cache_expirado(self, dias: int = 3) -> int:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM extracao_cache WHERE atualizado_em < datetime('now', ?, 'localtime')",
                    (f"-{int(dias)} days",),
                )
                conn.commit()
        return cur.rowcount

