"""设定版本管理工具：每当 world_settings（设定库）发生重大变更后调用。

问题背景：讨论 agent 的结论是基于「当时设定库内容」推出来的快照，设定一改，
旧对话里的结论就失效了，但聊天流不会自动标红。本项目通过 settings_revision
版本号把「设定真源」与「讨论结论快照」绑定，解决「旧结论被当真」的问题。

用法：
    python bump_settings_revision.py <project_root> ["变更说明"]

效果：
    1. settings_revision 表 revision +1（并记录 updated_at / note）。
    2. 打印一条「设定已更新」系统提示文本，UI 层应在对话流顶部插入该提示，
       告知用户：此前基于旧版设定的讨论结论可能已失效，请以新版本为准。

注意：本脚本只负责「+1 并产出提示文本」，不负责改写历史消息——历史消息的
过期标注由 context_builder 注入的【设定版本】提示 + memory.format_injection
的 rev 比对共同完成。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import datetime


def bump(project_root: str, note: str = "") -> tuple[int, str]:
    db_path = os.path.join(project_root, ".novelforge", "archive.db")
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"找不到 archive.db: {db_path}")

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS settings_revision ("
        "id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, "
        "updated_at TEXT, note TEXT)"
    )
    row = cur.execute("SELECT revision FROM settings_revision WHERE id=1").fetchone()
    old = int(row[0]) if row else 0
    new = old + 1
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute(
        "INSERT OR REPLACE INTO settings_revision(id, revision, updated_at, note) "
        "VALUES(1, ?, ?, ?)",
        (new, ts, note or f"设定变更 #{new}"),
    )
    # 同步把 world_settings 全表 revision 标记为最新（若列存在）
    try:
        cur.execute("UPDATE world_settings SET revision=? WHERE revision<? OR revision IS NULL OR revision=0", (new, new))
    except Exception:
        pass
    con.commit()
    con.close()

    prompt = (
        f"【系统提示·设定已更新】本书设定库 world_settings 已于 {ts} 更新至 v{new}"
        f"（上一版本 v{old}）。此前的对话中若涉及旧版设定（如旧『双轨战力』"
        f"『觉醒度%单独成体系』等表述），相关结论可能已失效，请以当前 v{new} 为准，"
        f"必要时重新确认。"
    )
    return new, prompt


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python bump_settings_revision.py <project_root> [\"变更说明\"]")
        return 2
    project_root = sys.argv[1]
    note = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        new, prompt = bump(project_root, note)
    except Exception as e:
        print(f"失败: {e}")
        return 1
    print(f"✅ 设定版本已 bump 至 v{new}")
    print("---- 请将以下系统提示插入对话流顶部 ----")
    print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
