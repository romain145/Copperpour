#!/bin/bash
# Runs the kicad-session Linux tests under Xvfb. WM=openbox adds a reparenting window manager.
set -u
export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 &
sleep 2
if [ "${WM:-}" = openbox ]; then openbox >/tmp/wm.log 2>&1 & sleep 2; fi

mkdir -p ~/.config/kicad/10.0
cat > ~/.config/kicad/10.0/kicad_common.json <<'EOF'
{ "api": { "enable_server": true }, "session": { "remember_open_files": true },
  "do_not_show_again": { "update_check_prompt": true, "data_collection_prompt": true } }
EOF
printf '(design_block_lib_table\n  (version 7)\n)\n' > ~/.config/kicad/10.0/design-block-lib-table
rm -rf ~/w && cp -r /opt/fixtures ~/w
S=/opt/test/session.py
B=~/w/proj/demo.kicad_pcb
P=~/w/pp/amp.kicad_pro
run() { echo "### session.py $*"; python3 $S "$@"; echo "EXIT=$?"; }
mcp() { python3 /opt/test/mcp.py "$1"; }
has() { grep -q "$1" "$2" && echo "$1 in $(basename $2): yes" || echo "$1 in $(basename $2): no"; }

echo "======== WM=${WM:-none}"
echo "--- L1 board mode: open, status, IPC"
run open --board $B
run status --board $B
mcp '[{"name":"check_kicad_ui"},{"name":"add_board_text","arguments":{"board":"'$B'","text":"UNSAVED-EDIT","x":10,"y":10}}]'
echo "--- L2 unsaved: close refuses, --force discards"
run close --board $B
run close --board $B --force
has UNSAVED-EDIT $B; ls -a ~/w/proj
echo "--- L3 reopen, edit, save, close"
run open --board $B
mcp '[{"name":"add_board_text","arguments":{"board":"'$B'","text":"SAVED-EDIT","x":12,"y":12}},{"name":"save_project"}]'
run close --board $B
has SAVED-EDIT $B; ls -a ~/w/proj
echo "--- L4 a lock held by another user"
printf '{"hostname":"OTHERPC","username":"someone"}' > ~/w/proj/~demo.kicad_pcb.lck
run open --board $B
run close --board $B
run close --board $B --force
ls -a ~/w/proj; rm -f ~/w/proj/~demo.kicad_pcb.lck
echo "--- L5 project mode: open, other KiCad refusal, IPC, close, reopen, force"
run open --project $P
run open --board $B
run close --board $B
mcp '[{"name":"check_kicad_ui"},{"name":"add_board_text","arguments":{"board":"'${P%.kicad_pro}.kicad_pcb'","text":"PROJECT-EDIT","x":20,"y":20}},{"name":"save_project"}]'
run close --project $P
python3 -c "import json;print('prl open flags:', [(f['name'], f['open']) for f in json.load(open('${P%.kicad_pro}.kicad_prl'))['project']['files']])"
has PROJECT-EDIT ${P%.kicad_pro}.kicad_pcb
run open --project $P
mcp '[{"name":"add_board_text","arguments":{"board":"'${P%.kicad_pro}.kicad_pcb'","text":"LOST-EDIT","x":24,"y":24}}]'
run close --project $P
run close --project $P --force
has LOST-EDIT ${P%.kicad_pro}.kicad_pcb; ls -a ~/w/pp
run status
