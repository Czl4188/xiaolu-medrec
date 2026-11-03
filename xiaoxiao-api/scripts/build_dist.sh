#!/usr/bin/env bash
# 重新生成发布包：只带运行时需要的东西
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist
mkdir -p dist
cp -r app web requirements.txt dist/
cp ../xiaoxiao-ai/app.html ../xiaoxiao-ai/index.html ../xiaoxiao-ai/choose.html dist/web/ 2>/dev/null || true
cp -r ../xiaoxiao-ai/styles ../xiaoxiao-ai/scripts dist/web/ 2>/dev/null || true
cp README.md dist/README.dist.md 2>/dev/null || true

# 固定的签名密钥：多进程/重启后令牌才不会立刻失效。
# 已存在就复用；这是部署产物，不进仓库（.gitignore 已忽略 dist/）。
# 密钥随包发布：所有进程读到同一份，不受工作目录影响
if [ -f dist.secret.key ]; then
  cp dist.secret.key dist/secret.key
else
  python -c "import secrets;print(secrets.token_urlsafe(48),end='')" > dist/secret.key
  cp dist/secret.key dist.secret.key
fi
# 只留 secret.key 一个来源；APP_ENV/DATABASE_URL 用代码默认值（dev + ./data/app.db）
echo "dist 已生成：$(du -sh dist | cut -f1)"
