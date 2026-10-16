"""短片制片工作台 — 5 表 schema + 基础 CRUD。

PRD §二表定义。SQLite 库文件位置：~/AichatStudio/<workspace>/studio.db
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    project_brief TEXT DEFAULT '',
    total_estimate_duration TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS project_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    asset_name TEXT NOT NULL,
    asset_type TEXT NOT NULL,  -- 角色 / 场景 / 道具
    asset_desc TEXT DEFAULT '',
    asset_keywords TEXT DEFAULT '',
    suggest_model TEXT DEFAULT '',
    suggest_prompt TEXT DEFAULT '',
    ref_file_path TEXT DEFAULT '',
    tts_config TEXT DEFAULT '{}',   -- JSON: {voice, speed, emotion}
    version_note TEXT DEFAULT '',
    UNIQUE(project_id, asset_name COLLATE NOCASE),  -- 项目内名称唯一，不区分大小写
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS asset_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    material_type TEXT NOT NULL,  -- 角色图/表情图/物品图/白模视频/配音/环境音/BGM
    path TEXT DEFAULT '',
    status TEXT DEFAULT '未创建',  -- 未创建/草稿/完成
    FOREIGN KEY (asset_id) REFERENCES project_assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS storyboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    shot_id TEXT NOT NULL,         -- 镜头ID（用户自定义，如 S01）
    shot_name TEXT DEFAULT '',
    duration TEXT DEFAULT '',      -- 预估时长
    camera_desc TEXT DEFAULT '',   -- 机位描述
    scene_text TEXT DEFAULT '',    -- 画面描述（支持 @资产）
    narration TEXT DEFAULT '',     -- 旁白/对白（支持 @角色）
    blender_feasibility_note TEXT DEFAULT '',
    status TEXT DEFAULT '草稿',    -- 草稿/原画完成/白模完成/音频完成/等待视频生成/镜头完成
    path_frame_png TEXT DEFAULT '',
    path_blender_mp4 TEXT DEFAULT '',
    path_audio_narration TEXT DEFAULT '',
    path_audio_sfx TEXT DEFAULT '',
    path_audio_bgm TEXT DEFAULT '',
    path_final_video TEXT DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shot_asset_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shot_id INTEGER NOT NULL,      -- 引用 storyboards.id
    asset_id INTEGER NOT NULL,     -- 引用 project_assets.id
    UNIQUE(shot_id, asset_id),     -- 同一分镜多次 @ 同一资产只留一条连线
    FOREIGN KEY (shot_id) REFERENCES storyboards(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES project_assets(id) ON DELETE CASCADE
);
"""


@dataclass
class Project:
    id: int = 0
    project_name: str = ""
    project_brief: str = ""
    total_estimate_duration: str = ""


@dataclass
class Asset:
    id: int = 0
    project_id: int = 0
    asset_name: str = ""
    asset_type: str = "角色"  # 角色/场景/道具
    asset_desc: str = ""
    asset_keywords: str = ""
    suggest_model: str = ""
    suggest_prompt: str = ""
    ref_file_path: str = ""
    tts_config: str = "{}"
    version_note: str = ""


@dataclass
class Storyboard:
    id: int = 0
    project_id: int = 0
    shot_id: str = ""
    shot_name: str = ""
    duration: str = ""
    camera_desc: str = ""
    scene_text: str = ""
    narration: str = ""
    blender_feasibility_note: str = ""
    status: str = "草稿"
    path_frame_png: str = ""
    path_blender_mp4: str = ""
    path_audio_narration: str = ""
    path_audio_sfx: str = ""
    path_audio_bgm: str = ""
    path_final_video: str = ""


class StudioDB:
    """短片域 SQLite 数据库封装。

    库文件位置：workspace_root/studio.db（由 shell 工作区管理决定）
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    # ===== Project CRUD =====
    def list_projects(self) -> list[Project]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
            return [Project(**dict(r)) for r in rows]

    def get_project(self, pid: int) -> Optional[Project]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            return Project(**dict(r)) if r else None

    def insert_project(self, p: Project) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO projects(project_name, project_brief, total_estimate_duration) VALUES(?,?,?)",
                (p.project_name, p.project_brief, p.total_estimate_duration),
            )
            return cur.lastrowid

    def update_project(self, p: Project) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE projects SET project_name=?, project_brief=?, total_estimate_duration=? WHERE id=?",
                (p.project_name, p.project_brief, p.total_estimate_duration, p.id),
            )

    def delete_project(self, pid: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM projects WHERE id=?", (pid,))

    # ===== Asset CRUD =====
    def list_assets(self, project_id: int) -> list[Asset]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM project_assets WHERE project_id=? ORDER BY id", (project_id,)
            ).fetchall()
            return [Asset(**dict(r)) for r in rows]

    def get_asset(self, aid: int) -> Optional[Asset]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM project_assets WHERE id=?", (aid,)).fetchone()
            return Asset(**dict(r)) if r else None

    def find_asset_by_name(self, project_id: int, name: str) -> Optional[Asset]:
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM project_assets WHERE project_id=? AND asset_name=? COLLATE NOCASE",
                (project_id, name),
            ).fetchone()
            return Asset(**dict(r)) if r else None

    def insert_asset(self, a: Asset) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO project_assets
                (project_id, asset_name, asset_type, asset_desc, asset_keywords,
                 suggest_model, suggest_prompt, ref_file_path, tts_config, version_note)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (a.project_id, a.asset_name, a.asset_type, a.asset_desc, a.asset_keywords,
                 a.suggest_model, a.suggest_prompt, a.ref_file_path, a.tts_config, a.version_note),
            )
            return cur.lastrowid

    def update_asset(self, a: Asset) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE project_assets SET
                asset_name=?, asset_type=?, asset_desc=?, asset_keywords=?,
                suggest_model=?, suggest_prompt=?, ref_file_path=?, tts_config=?, version_note=?
                WHERE id=?""",
                (a.asset_name, a.asset_type, a.asset_desc, a.asset_keywords,
                 a.suggest_model, a.suggest_prompt, a.ref_file_path, a.tts_config, a.version_note, a.id),
            )

    def delete_asset(self, aid: int) -> None:
        """删资产时自动清理 shot_asset_link（FK ON DELETE CASCADE）"""
        with self._conn() as c:
            c.execute("DELETE FROM project_assets WHERE id=?", (aid,))

    # ===== Storyboard CRUD =====
    def list_storyboards(self, project_id: int) -> list[Storyboard]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM storyboards WHERE project_id=? ORDER BY id", (project_id,)
            ).fetchall()
            return [Storyboard(**dict(r)) for r in rows]

    def insert_storyboard(self, s: Storyboard) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO storyboards
                (project_id, shot_id, shot_name, duration, camera_desc, scene_text, narration,
                 blender_feasibility_note, status, path_frame_png, path_blender_mp4,
                 path_audio_narration, path_audio_sfx, path_audio_bgm, path_final_video)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.project_id, s.shot_id, s.shot_name, s.duration, s.camera_desc, s.scene_text,
                 s.narration, s.blender_feasibility_note, s.status, s.path_frame_png,
                 s.path_blender_mp4, s.path_audio_narration, s.path_audio_sfx, s.path_audio_bgm,
                 s.path_final_video),
            )
            return cur.lastrowid

    def update_storyboard(self, s: Storyboard) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE storyboards SET
                shot_id=?, shot_name=?, duration=?, camera_desc=?, scene_text=?, narration=?,
                blender_feasibility_note=?, status=?, path_frame_png=?, path_blender_mp4=?,
                path_audio_narration=?, path_audio_sfx=?, path_audio_bgm=?, path_final_video=?
                WHERE id=?""",
                (s.shot_id, s.shot_name, s.duration, s.camera_desc, s.scene_text, s.narration,
                 s.blender_feasibility_note, s.status, s.path_frame_png, s.path_blender_mp4,
                 s.path_audio_narration, s.path_audio_sfx, s.path_audio_bgm, s.path_final_video, s.id),
            )

    def delete_storyboard(self, sid: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM storyboards WHERE id=?", (sid,))

    # ===== @资产 连线 =====
    def sync_shot_links(self, shot_db_id: int, project_id: int, asset_names: list[str]) -> None:
        """根据分镜文本里解析出的 @资产名列表，同步 shot_asset_link 连线表。

        - 新出现的 @资产 → INSERT（已存在的跳过，UNIQUE 约束）
        - 文本里不再引用的旧连线 → DELETE
        """
        with self._conn() as c:
            # 找出资产 id（资产必须存在；不存在的让上层弹窗就地新建）
            asset_ids: list[int] = []
            for name in asset_names:
                r = c.execute(
                    "SELECT id FROM project_assets WHERE project_id=? AND asset_name=? COLLATE NOCASE",
                    (project_id, name),
                ).fetchone()
                if r:
                    asset_ids.append(r["id"])

            # 当前的连线
            existing = {r["asset_id"] for r in c.execute(
                "SELECT asset_id FROM shot_asset_link WHERE shot_id=?", (shot_db_id,)
            ).fetchall()}
            new_set = set(asset_ids)

            # 删多余
            to_remove = existing - new_set
            for aid in to_remove:
                c.execute(
                    "DELETE FROM shot_asset_link WHERE shot_id=? AND asset_id=?",
                    (shot_db_id, aid),
                )

            # 加新的
            to_add = new_set - existing
            for aid in to_add:
                c.execute(
                    "INSERT OR IGNORE INTO shot_asset_link(shot_id, asset_id) VALUES(?,?)",
                    (shot_db_id, aid),
                )

    def list_links_for_shot(self, shot_db_id: int) -> list[dict]:
        """本镜头引用的资产清单（带资产基本信息）"""
        with self._conn() as c:
            rows = c.execute(
                """SELECT a.id, a.asset_name, a.asset_type, a.asset_desc, a.asset_keywords
                FROM shot_asset_link l
                JOIN project_assets a ON l.asset_id = a.id
                WHERE l.shot_id=? ORDER BY a.asset_type, a.asset_name""",
                (shot_db_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_reverse_refs(self, asset_id: int) -> list[dict]:
        """反向引用：哪些分镜 @ 引用了本资产"""
        with self._conn() as c:
            rows = c.execute(
                """SELECT s.id, s.shot_id, s.shot_name
                FROM shot_asset_link l
                JOIN storyboards s ON l.shot_id = s.id
                WHERE l.asset_id=? ORDER BY s.shot_id""",
                (asset_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ===== 资产素材子表 =====
    def list_materials(self, asset_id: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM asset_materials WHERE asset_id=? ORDER BY id", (asset_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_material(self, asset_id: int, mtype: str, path: str = "", status: str = "未创建") -> int:
        with self._conn() as c:
            existing = c.execute(
                "SELECT id FROM asset_materials WHERE asset_id=? AND material_type=?",
                (asset_id, mtype),
            ).fetchone()
            if existing:
                c.execute(
                    "UPDATE asset_materials SET path=?, status=? WHERE id=?",
                    (path, status, existing["id"]),
                )
                return existing["id"]
            cur = c.execute(
                "INSERT INTO asset_materials(asset_id, material_type, path, status) VALUES(?,?,?,?)",
                (asset_id, mtype, path, status),
            )
            return cur.lastrowid

    # ===== 项目统计（PRD §一 统计面板） =====
    def project_stats(self, project_id: int) -> dict:
        with self._conn() as c:
            shots = c.execute(
                "SELECT COUNT(*) as n, SUM(CASE WHEN status='镜头完成' THEN 1 ELSE 0 END) as done FROM storyboards WHERE project_id=?",
                (project_id,),
            ).fetchone()
            assets = c.execute(
                "SELECT COUNT(*) as n FROM project_assets WHERE project_id=?", (project_id,)
            ).fetchone()
            narration_total = c.execute(
                "SELECT COALESCE(SUM(LENGTH(narration)),0) as chars FROM storyboards WHERE project_id=?",
                (project_id,),
            ).fetchone()
            materials = c.execute(
                """SELECT COUNT(*) as total,
                SUM(CASE WHEN status='完成' THEN 1 ELSE 0 END) as done
                FROM asset_materials am
                JOIN project_assets pa ON am.asset_id = pa.id
                WHERE pa.project_id=?""",
                (project_id,),
            ).fetchone()
            return {
                "shots_total": shots["n"],
                "shots_done": shots["done"] or 0,
                "assets_total": assets["n"],
                "narration_chars": narration_total["chars"],
                "materials_total": materials["total"],
                "materials_done": materials["done"] or 0,
            }


# 默认 DB 路径（由 shell 工作区决定，这里兜底）
_default_db: Optional[StudioDB] = None


def get_default_db() -> StudioDB:
    """获取当前工作区的 StudioDB 单例。

    路径：~/AichatStudio/<workspace>/studio.db
    """
    global _default_db
    if _default_db is not None:
        return _default_db
    from core.workspace import current
    ws = current()
    if ws is None:
        # 兜底：~/AichatStudio/default/studio.db
        db_path = Path("~/AichatStudio/default/studio.db").expanduser()
    else:
        db_path = Path(ws.root) / "studio.db"
    _default_db = StudioDB(db_path)
    return _default_db
