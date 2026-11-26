#!/bin/bash
# 双击卸载 MagnetSearch：停服务 → 删 app（需管理员密码）→ 清偏好/授权状态 → 忘记安装包收据
#
# 也可以命令行跑：
#   bash "/Applications/卸载 MagnetSearch.command"          # 正常卸载（会弹密码框）
#   bash "/Applications/卸载 MagnetSearch.command" --dry-run # 只打印将要做什么
#   KEEP_LICENSE=1 bash "/Applications/卸载 MagnetSearch.command"   # 保留本机授权状态
set -u

APP="/Applications/MagnetSearch.app"
SELF="/Applications/卸载 MagnetSearch.command"
PLIST="$HOME/Library/LaunchAgents/com.magnetsearch.backend.plist"
LOGDIR="$HOME/Library/Logs"
LICDIR="$HOME/Library/Application Support/MagnetSearchLicense"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say() { printf '%s\n' "$*"; }
run() { if [ "$DRY" = "1" ]; then say "  [dry-run] $*"; else eval "$*"; fi; }

say "== 1/5 停止搜索服务与进程 =="
if [ "$DRY" = "0" ]; then
    launchctl unload "$PLIST" 2>/dev/null && say "  已停止 LaunchAgent" || say "  LaunchAgent 未在运行"
    launchctl remove com.magnetsearch.backend 2>/dev/null
    pkill -f "MagnetSearch.app/Contents/MacOS/MagnetSearch" 2>/dev/null
    pkill -f "MagnetSearch.app/Contents/Resources/backend/magnetsearch-server" 2>/dev/null
    rm -f "$PLIST" && say "  已删除开机自启项"
else
    say "  [dry-run] 停止 LaunchAgent / 结束进程 / 删除 $PLIST"
fi

say "== 2/5 删除 app（需要管理员密码）=="
if [ -d "$APP" ]; then
    if [ "$DRY" = "1" ]; then
        say "  [dry-run] 用管理员权限删除 $APP"
    else
        # 关键：/Applications 下的 app 属于 root，必须提权；原来直接 rm -rf 会静默失败
        if osascript <<'OSA' >/dev/null 2>&1
do shell script "/bin/rm -rf " & quoted form of "/Applications/MagnetSearch.app" with administrator privileges
OSA
        then
            say "  已删除 $APP"
        else
            say "  ❌ 删除失败（可能取消了密码框）。手动执行："
            say "     sudo rm -rf \"$APP\""
        fi
    fi
else
    say "  app 不存在，跳过"
fi

say "== 3/5 清理用户级文件 =="
run "defaults delete com.magnetsearch.app >/dev/null 2>&1; say '  已清除偏好设置'"
run "defaults delete com.magnetsearch.license.state >/dev/null 2>&1; say '  已清除本地授权缓存(默认值)'"
run "rm -f \"$LOGDIR/MagnetSearchBackend.log\" && say '  已删除搜索服务日志'"

say "== 4/5 授权状态 =="
if [ "${KEEP_LICENSE:-0}" = "1" ]; then
    say "  保留 $LICDIR（KEEP_LICENSE=1）"
    run "say '  如需彻底清除：rm -rf \"$LICDIR\"'"
else
    run "rm -rf \"$LICDIR\" && say '  已删除本机授权缓存（云端授权记录不受影响，重装后仍可自动恢复）'"
fi

say "== 5/5 忘记安装包收据 =="
if [ "$DRY" = "1" ]; then
    say "  [dry-run] pkgutil --forget com.magnetsearch.pkg"
else
    osascript <<'OSA' >/dev/null 2>&1
do shell script "/usr/sbin/pkgutil --forget com.magnetsearch.pkg >/dev/null 2>&1; /bin/rm -f " & quoted form of "/Applications/卸载 MagnetSearch.command" with administrator privileges
OSA
    say "  已忘记 com.magnetsearch.pkg，并移除本卸载脚本"
fi

say ""
say "完成。重新安装：双击 MagnetSearch-3.3.pkg 即可。"
if [ "$DRY" = "0" ] && [ -t 0 ]; then
    read -n 1 -s -r -p "按任意键关闭本窗口"
    echo
fi
