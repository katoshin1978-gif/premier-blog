#!/bin/bash
# Xサーバーの cron から GitHub Actions「Premier Blog Auto Post」を起動する
# GitHub の schedule は数時間遅れるため、時刻どおりに動く Xサーバー cron で workflow_dispatch を叩く
#
# 設置先: ~/bin/trigger_autopost.sh（公開ディレクトリの外）
# トークン: ~/.github_dispatch_token に1行で保存（chmod 600）
#   Fine-grained PAT / 対象リポジトリ premier-blog のみ / 権限 Actions: Read and write
# cron 設定例（毎日 7:00 JST）:
#   0 7 * * * /bin/bash $HOME/bin/trigger_autopost.sh

REPO="katoshin1978-gif/premier-blog"
WORKFLOW="auto-post.yml"
TOKEN_FILE="$HOME/.github_dispatch_token"
LOG_FILE="$HOME/log/trigger_autopost.log"

mkdir -p "$(dirname "$LOG_FILE")"
now() { TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S'; }

if [ ! -r "$TOKEN_FILE" ]; then
  echo "$(now) NG トークンファイルなし: $TOKEN_FILE" >> "$LOG_FILE"
  exit 1
fi
TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE")

# 成功時は HTTP 204（本文なし）
code=$(curl -s -o /tmp/trigger_autopost_resp.$$ -w '%{http_code}' -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" \
  -d '{"ref":"master"}')

if [ "$code" = "204" ]; then
  echo "$(now) OK 起動成功" >> "$LOG_FILE"
else
  echo "$(now) NG HTTP $code: $(head -c 300 /tmp/trigger_autopost_resp.$$)" >> "$LOG_FILE"
fi
rm -f /tmp/trigger_autopost_resp.$$

# ログは直近200行だけ残す
tail -n 200 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
[ "$code" = "204" ]
