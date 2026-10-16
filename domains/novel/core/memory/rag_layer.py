"""
RAGLayer - 向量检索层：正文 chunk 的 embedding 存储与语义检索。

存储方案：SQLite + sqlite-vec 扩展。
三级降级：sqlite-vec -> numpy 暴力 cosine -> BM25 关键词检索。
"""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from collections import Counter
from typing import Optional

from domains.novel.core.llm_client import LLMClient
from domains.novel.utils.logger import logger
from domains.novel.utils.text import chunk_text, split_sentences

# 尝试加载 sqlite-vec
try:
    import sqlite_vec
    _HAS_SQLITE_VEC = True
except ImportError:
    _HAS_SQLITE_VEC = False

# 尝试加载 numpy
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


class BM25Index:
    """
    纯 Python BM25 实现，作为无 embedding API 时的兜底。
    不依赖任何外部库，使用 jieba 分词（如果可用）或简单字符分割。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._docs: list[dict] = []  # [{id, chapter_id, chunk_index, text, tokens}]
        self._idf: dict[str, float] = {}
        self._avg_len: float = 0
        self._term_freqs: list[Counter] = []

    def add_docs(self, docs: list[dict]) -> None:
        """批量添加文档并构建索引。"""
        for doc in docs:
            tokens = self._tokenize(doc["text"])
            self._docs.append({**doc, "tokens": tokens})
            self._term_freqs.append(Counter(tokens))

        # 重新计算 IDF
        N = len(self._docs)
        df: Counter = Counter()
        for tf in self._term_freqs:
            for term in tf:
                df[term] += 1

        self._idf = {
            term: math.log((N - freq + 0.5) / (freq + 0.5) + 1)
            for term, freq in df.items()
        }

        total_len = sum(len(d["tokens"]) for d in self._docs)
        self._avg_len = total_len / N if N > 0 else 0

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """BM25 检索，返回 top_k 结果。"""
        query_tokens = self._tokenize(query)
        if not query_tokens or not self._docs:
            return []

        scores: list[tuple[int, float]] = []
        for idx, (doc, tf) in enumerate(zip(self._docs, self._term_freqs)):
            score = 0.0
            doc_len = len(doc["tokens"])
            for term in query_tokens:
                if term not in self._idf:
                    continue
                tf_val = tf.get(term, 0)
                if tf_val == 0:
                    continue
                idf = self._idf[term]
                numerator = tf_val * (self._k1 + 1)
                denominator = tf_val + self._k1 * (1 - self._b + self._b * doc_len / max(self._avg_len, 1))
                score += idf * numerator / denominator
            if score > 0:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:top_k]:
            doc = self._docs[idx]
            results.append({
                "text": doc["text"],
                "chapter_id": doc["chapter_id"],
                "chunk_index": doc["chunk_index"],
                "score": score,
            })
        return results

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """分词：优先 jieba，不可用则按 2-gram。"""
        try:
            import jieba
            return [w for w in jieba.lcut(text) if len(w.strip()) > 0]
        except ImportError:
            # 中文 2-gram + 英文单词
            import re
            tokens = []
            # 英文单词
            tokens.extend(re.findall(r"[a-zA-Z]+", text))
            # 中文 2-gram
            chinese = re.findall(r"[\u4e00-\u9fa5]+", text)
            for seg in chinese:
                for i in range(len(seg) - 1):
                    tokens.append(seg[i:i + 2])
            return tokens


class RAGLayer:
    """
    RAG层：正文 chunk 的 embedding 存储与语义检索。

    三级降级策略：
    1. sqlite-vec + embedding API：向量余弦相似度检索（最佳）
    2. numpy 暴力 cosine + embedding API：全量加载计算（中等）
    3. BM25 关键词检索：无 embedding 时的兜底（基础）
    """

    def __init__(self, project_root: str, llm_client: LLMClient):
        self._project_root = project_root
        self._db_path = f"{project_root}/.novelforge/vectors.db"
        self._llm = llm_client
        self._conn: Optional[sqlite3.Connection] = None
        self._has_vec_ext = False
        self._has_embed_api = False
        self._embedding_dim: Optional[int] = None
        self._bm25 = BM25Index()
        self._init_db()

    def _init_db(self):
        """初始化数据库。"""
        from pathlib import Path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # 基础 chunks 表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_chapter ON chunks(chapter_id)"
        )

        # 尝试加载 sqlite-vec 扩展
        if _HAS_SQLITE_VEC:
            try:
                self._conn.enable_load_extension(True)
                sqlite_vec.load(self._conn)
                self._has_vec_ext = True
                logger.info("sqlite-vec 扩展加载成功")
            except Exception as e:
                logger.warning(f"sqlite-vec 加载失败，将使用 numpy fallback: {e}")
                self._has_vec_ext = False

        # 检查是否有 embedding 数据
        self._has_embed_api = bool(self._llm.config.default_embed_model)
        if not self._has_embed_api:
            logger.info("未配置 embedding 模型，RAG 层将使用 BM25 检索")

        # 初始化 BM25 索引
        self._rebuild_bm25()

        self._conn.commit()

    def _rebuild_bm25(self):
        """从数据库重建 BM25 索引。"""
        rows = self._conn.execute(
            "SELECT id, chapter_id, chunk_index, text FROM chunks ORDER BY id"
        ).fetchall()
        if rows:
            docs = [
                {"id": r["id"], "chapter_id": r["chapter_id"],
                 "chunk_index": r["chunk_index"], "text": r["text"]}
                for r in rows
            ]
            self._bm25.add_docs(docs)

    def add_chapter_chunks(
        self,
        chapter_id: int,
        text: str,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> int:
        """
        将章节正文分块、生成 embedding、入库。

        Returns: 入库的 chunk 数量
        """
        from datetime import datetime

        # 先删除该章节的旧 chunk（重写场景）
        self.delete_chapter(chapter_id)

        # 分块
        chunks = chunk_text(text, target_size=chunk_size, overlap=overlap)
        if not chunks:
            return 0

        now = datetime.now().isoformat()
        chunk_ids = []

        # 插入 chunks
        for i, chunk in enumerate(chunks):
            cursor = self._conn.execute(
                "INSERT INTO chunks (chapter_id, chunk_index, text, metadata, created_at) VALUES (?,?,?,?,?)",
                (chapter_id, i, chunk, json.dumps({"word_count": len(chunk)}), now)
            )
            chunk_ids.append(cursor.lastrowid)

        # 如果有 embedding API，生成并存储向量
        if self._has_embed_api:
            try:
                embeddings = self._llm.embed(chunks)
                self._store_embeddings(chunk_ids, embeddings)
                logger.info(f"第{chapter_id}章 {len(chunks)} 个 chunk embedding 入库")
            except Exception as e:
                logger.warning(f"Embedding 生成失败，仅存储文本: {e}")

        # 重建 BM25
        self._rebuild_bm25()

        self._conn.commit()
        return len(chunks)

    def _store_embeddings(self, chunk_ids: list[int], embeddings: list[list[float]]):
        """存储 embedding 向量。"""
        # 确定 embedding 维度
        if self._embedding_dim is None and embeddings:
            self._embedding_dim = len(embeddings[0])
            # 创建向量表
            if self._has_vec_ext:
                self._conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{self._embedding_dim}])"
                )
            else:
                # numpy fallback：创建普通表存储序列化的向量
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS vec_chunks_numpy (
                        chunk_id INTEGER PRIMARY KEY,
                        embedding BLOB
                    )
                """)
            self._conn.commit()

        for chunk_id, emb in zip(chunk_ids, embeddings):
            if self._has_vec_ext:
                # sqlite-vec：使用 serialize 格式
                emb_bytes = struct.pack(f"{len(emb)}f", *emb)
                self._conn.execute(
                    "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (chunk_id, emb_bytes)
                )
            else:
                # numpy fallback
                emb_bytes = struct.pack(f"{len(emb)}f", *emb)
                self._conn.execute(
                    "INSERT OR REPLACE INTO vec_chunks_numpy (chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, emb_bytes)
                )

    def search(
        self,
        query: str,
        top_k: int = 5,
        chapter_range: Optional[tuple[int, int]] = None,
    ) -> list[dict]:
        """
        语义检索：
        - 有 embedding API + vec 扩展：向量余弦相似度检索
        - 有 embedding API 无 vec 扩展：numpy 暴力 cosine
        - 无 embedding API：BM25 关键词检索

        返回 [{text, chapter_id, chunk_index, score}]
        """
        # 构建章节范围过滤 SQL
        range_clause = ""
        range_params: list = []
        if chapter_range:
            range_clause = "WHERE chapter_id BETWEEN ? AND ?"
            range_params = list(chapter_range)

        # 尝试向量检索
        if self._has_embed_api:
            try:
                query_emb = self._llm.embed([query])[0]

                if self._has_vec_ext and self._embedding_dim:
                    return self._search_vec_ext(query_emb, top_k, chapter_range)
                elif _HAS_NUMPY and self._embedding_dim:
                    return self._search_numpy(query_emb, top_k, chapter_range)
            except Exception as e:
                logger.warning(f"向量检索失败，降级到 BM25: {e}")

        # BM25 兜底
        return self._search_bm25(query, top_k, chapter_range)

    def _search_vec_ext(
        self, query_emb: list[float], top_k: int, chapter_range: Optional[tuple[int, int]]
    ) -> list[dict]:
        """sqlite-vec 向量检索。"""
        emb_bytes = struct.pack(f"{len(query_emb)}f", *query_emb)

        if chapter_range:
            sql = """
                SELECT c.text, c.chapter_id, c.chunk_index, v.distance
                FROM vec_chunks v
                JOIN chunks c ON c.id = v.rowid
                WHERE c.chapter_id BETWEEN ? AND ?
                ORDER BY v.distance
                LIMIT ?
            """
            rows = self._conn.execute(sql, (*chapter_range, top_k)).fetchall()
        else:
            sql = """
                SELECT c.text, c.chapter_id, c.chunk_index, v.distance
                FROM vec_chunks v
                JOIN chunks c ON c.id = v.rowid
                ORDER BY v.distance
                LIMIT ?
            """
            rows = self._conn.execute(sql, (top_k,)).fetchall()

        # distance 越小越好，转换为 score（越大越好）
        max_dist = max((r["distance"] for r in rows), default=1.0)
        return [
            {
                "text": r["text"],
                "chapter_id": r["chapter_id"],
                "chunk_index": r["chunk_index"],
                "score": 1.0 - (r["distance"] / max_dist) if max_dist > 0 else 1.0,
            }
            for r in rows
        ]

    def _search_numpy(
        self, query_emb: list[float], top_k: int, chapter_range: Optional[tuple[int, int]]
    ) -> list[dict]:
        """numpy 暴力 cosine 相似度检索。"""
        query_arr = np.array(query_emb, dtype=np.float32)
        query_norm = np.linalg.norm(query_arr)
        if query_norm == 0:
            return []
        query_arr = query_arr / query_norm

        # 加载所有向量
        if chapter_range:
            rows = self._conn.execute(
                "SELECT v.chunk_id, v.embedding, c.text, c.chapter_id, c.chunk_index "
                "FROM vec_chunks_numpy v JOIN chunks c ON c.id = v.chunk_id "
                "WHERE c.chapter_id BETWEEN ? AND ?",
                chapter_range
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT v.chunk_id, v.embedding, c.text, c.chapter_id, c.chunk_index "
                "FROM vec_chunks_numpy v JOIN chunks c ON c.id = v.chunk_id"
            ).fetchall()

        if not rows:
            return []

        # 计算相似度
        scores = []
        for r in rows:
            emb = np.array(struct.unpack(f"{len(query_emb)}f", r["embedding"]), dtype=np.float32)
            norm = np.linalg.norm(emb)
            if norm == 0:
                continue
            sim = np.dot(query_arr, emb / norm)
            scores.append((r, float(sim)))

        scores.sort(key=lambda x: x[1], reverse=True)

        return [
            {
                "text": r[0]["text"],
                "chapter_id": r[0]["chapter_id"],
                "chunk_index": r[0]["chunk_index"],
                "score": r[1],
            }
            for r in scores[:top_k]
        ]

    def _search_bm25(
        self, query: str, top_k: int, chapter_range: Optional[tuple[int, int]]
    ) -> list[dict]:
        """BM25 关键词检索。"""
        results = self._bm25.search(query, top_k=top_k * 2)  # 多取一些再过滤
        if chapter_range:
            results = [r for r in results if chapter_range[0] <= r["chapter_id"] <= chapter_range[1]]
        return results[:top_k]

    def delete_chapter(self, chapter_id: int):
        """删除指定章节的所有 chunk（重写场景）。"""
        # 获取要删除的 chunk ids
        rows = self._conn.execute(
            "SELECT id FROM chunks WHERE chapter_id = ?", (chapter_id,)
        ).fetchall()
        chunk_ids = [r["id"] for r in rows]

        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            self._conn.execute(f"DELETE FROM chunks WHERE id IN ({placeholders})", chunk_ids)
            # 向量表可能因本进程 vec 扩展/embedding 状态不同而未创建，容错跳过
            try:
                if self._has_vec_ext:
                    self._conn.execute(f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", chunk_ids)
                else:
                    self._conn.execute(f"DELETE FROM vec_chunks_numpy WHERE chunk_id IN ({placeholders})", chunk_ids)
            except Exception as e:
                logger.warning(f"删除向量索引失败(可忽略): {e}")

        self._conn.commit()

    def clear_all(self):
        """清空全部 chunk 与向量索引（全书重写前重置派生记忆，防膨胀）。

        删除文本分块、向量表，并重置维度标记与 BM25 索引，
        使后续逐章重新入库能干净重建，避免重复提交导致库文件无限增长。
        """
        try:
            self._conn.execute("DELETE FROM chunks")
            # 向量表若存在则一并删除（虚拟表也支持 DROP TABLE）
            self._conn.execute("DROP TABLE IF EXISTS vec_chunks")
            self._conn.execute("DROP TABLE IF EXISTS vec_chunks_numpy")
            self._embedding_dim = None
            self._conn.commit()
            self._bm25 = BM25Index()
            logger.info("RAG 派生记忆已清空")
        except Exception as e:
            self._conn.rollback()
            logger.error(f"清空 RAG 派生记忆失败: {e}")
            raise

    def get_chapter_chunks(self, chapter_id: int) -> list[dict]:
        """获取指定章节的所有 chunk。"""
        rows = self._conn.execute(
            "SELECT * FROM chunks WHERE chapter_id = ? ORDER BY chunk_index", (chapter_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """获取 RAG 层统计信息。"""
        row = self._conn.execute("SELECT COUNT(*) as c, COUNT(DISTINCT chapter_id) as ch FROM chunks").fetchone()
        return {
            "total_chunks": row["c"],
            "total_chapters": row["ch"],
            "has_vec_ext": self._has_vec_ext,
            "has_embed_api": self._has_embed_api,
            "embedding_dim": self._embedding_dim,
        }

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
