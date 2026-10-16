#!/bin/bash
# pack/sync-branch.sh — 把 main 的更新合到 chat-warehouse 分支
#
# 用途：公司机/工作机最小部署分支（只含 core + shell + chat + warehouse）
# 场景：个人机用 main（全量 5 域），公司机用 chat-warehouse（仅 chat + warehouse）
#
# 工作原理：
#   1. checkout main 拉远程最新
#   2. checkout chat-warehouse
#   3. git merge main -X theirs（接收 main 对 chat/warehouse 域的所有修改）
#   4. 强制删除 domains/{novel,studio,analysis} 目录（保持分支纯净）
#   5. commit + push
#
# 何时跑：main 有更新后，个人想同步到工作分支时手动执行
# 注意：必须在 main 分支干净、chat-warehouse 分支干净的情况下跑

set -e

# 切到 main 并拉远程
git checkout main
echo "→ 拉取 main 最新..."
git pull origin main

# 切到 chat-warehouse
git checkout chat-warehouse 2>/dev/null || {
  echo "→ chat-warehouse 分支不存在，从 main 创建..."
  git checkout -b chat-warehouse
  # 首次创建：直接删除非工作域
  rm -rf domains/novel domains/studio domains/analysis
  git add -A
  git commit -m "init chat-warehouse branch (chat + warehouse only)" || echo "nothing to commit on init"
  git push -u origin chat-warehouse
  git checkout main
  echo "✓ chat-warehouse 分支创建完成"
  exit 0
}

echo "→ 合并 main 的更新（-X theirs 接收所有修改）..."
git merge main --no-commit -X theirs || {
  echo "⚠️  合并冲突，请手动解决后继续"
  git status
  exit 1
}

# 强制删除非工作域目录
echo "→ 清除非工作域目录..."
rm -rf domains/novel domains/studio domains/analysis
git add -A

# 提交 + push
if git diff --cached --quiet; then
  echo "✓ 无变更需要同步"
else
  git commit -m "sync from main + drop unused domains (novel/studio/analysis)"
  echo "→ 推送到 origin..."
  git push origin chat-warehouse
fi

# 回到 main
git checkout main
echo "✓ chat-warehouse 分支同步完成"
