#!/usr/bin/env bash
# 收工守卫：把"改了忘提交 / 忘推送"从依赖记性变成机械检查。
# 起因与理由见 docs/ISSUES.md DP-050、docs/WORKFLOW.md §6。
# 收工、交接、换账号前必跑。任何一节非空 = 收工未完成。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0
sec() { printf '\n=== %s ===\n' "$1"; }

git fetch origin --quiet 2>/dev/null || echo '(fetch 失败，以下 ahead/behind 可能过期)'

sec '1 未提交的改动'
if [ -n "$(git diff --name-only)$(git diff --cached --name-only)" ]; then
  git status --short --untracked-files=no; fail=1
else echo '(无)'; fi

sec '2 未跟踪文件'
u=$(git ls-files --others --exclude-standard)
if [ -n "$u" ]; then printf '%s\n' "$u"; fail=1; else echo '(无)'; fi

sec '3 stash（git push 不带 stash，只在本机）'
sfail=0
if [ -z "$(git stash list)" ]; then echo '(无)'; else
  i=0
  while read -r line; do
    [ -z "$line" ] && continue
    sha=$(git rev-parse "stash@{$i}" 2>/dev/null)
    if [ -n "$sha" ] && [ -n "$(git branch -r --contains "$sha" 2>/dev/null)" ]; then
      echo "已备份到 remote  $line"
    else
      echo "未备份          $line"; sfail=1
    fi
    i=$((i+1))
  done <<< "$(git stash list)"
  [ "$sfail" -eq 1 ] && fail=1
fi

sec '4 未备份的分支（tip 不在任何 remote 上 ⇒ GitHub 上根本没有，机器一挂就没了）'
unbacked=''
while read -r b; do
  [ -z "$b" ] && continue
  if [ -z "$(git branch -r --contains "$b" 2>/dev/null)" ]; then unbacked="${unbacked}${b}
"; fi
done < <(git for-each-ref --format='%(refname:short)' refs/heads)
if [ -n "$unbacked" ]; then printf '%s' "$unbacked"; fail=1; else echo '(无)'; fi

sec '4b 没设 upstream 的分支（内容已备份，仅提示，不判失败）'
n=$(git for-each-ref --format='%(refname:short) %(upstream)' refs/heads | awk '$2==""{print $1}')
if [ -n "$n" ]; then printf '%s\n' "$n"; else echo '(无)'; fi

sec '5 领先 origin 的分支（有本地 commit 没推）'
a=$(git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads | grep 'ahead' || true)
if [ -n "$a" ]; then printf '%s\n' "$a"; fail=1; else echo '(无)'; fi

sec '6 tools/ 目录（人工工具改了最容易忘提交）'
t=$(git status --short -- tools/; git ls-files --others --exclude-standard -- tools/)
if [ -n "$t" ]; then printf '%s\n' "$t"; fail=1; else echo '(tools/ 干净)'; fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  echo '收工守卫：全部干净，可以收工。'
else
  echo '收工守卫：有未落地的东西，收工未完成。逐节处理后重跑。'
fi
exit "$fail"
