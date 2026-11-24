#!/bin/bash
# r3-0735 / r3-0815 #903 ablation driver (Ultron). Never kills or signals anything.
# Every exit path: detach any RAM disk it attached, resume factory (remove our pause file),
# re-check config/uv.lock shas, write results.md and DONE or FAILED, commit + ff push.
set -u
R=/Users/mike/tmp/r3-0735; TREE=$R/wt; PY=$TREE/.venv/bin/python; H=$R/harness
RES=$R/results-r3-0815; STAGE=$R/stage-r3-0815
WT903=/Users/mike/factories/factory/.factory/wt-903; REL=experiments/baseline-903/raw/ablation-r3-0815
PAUSE=/Users/mike/.local/state/factory-guard/factory.pause
CFG=/Users/mike/.config/factory/config.toml
CFG_SHA=e071ebe909c9b2c1fea62bad01545406307952ae664416029dea9361444f026b
LOCK_SHA=ac6661ba52ea87d7bf27c9b8d2c5c167eb7c4f73573b35b402d6e367775a7651
BUDGET=$((5*3600)); START=$(date +%s); STATUS=running; RAMDEV=""; MAXRUN=1800
SEQ=(control-1:control ram:ram control-2:control git-cache:git-cache control-3:control scratch-throttle:scratch-throttle control-4:control)
BASEPATH=/Users/mike/.bun/bin:/Users/mike/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PATH=$BASEPATH; unset GIT_CACHE_DIR PYTHONPATH R3_0735_THROTTLE
exec >> "$R/driver.log" 2>&1
log() { echo "$(date '+%Y-%m-%dT%H:%M:%S%z') $*"; }
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

vm_sample() {  # mean of 3 ps %CPU samples of the VMware Fusion VM, 5 s apart (0 if absent)
  local pid s total=0 n
  pid=$(pgrep -f 'vmware-vmx' | head -1)
  for n in 1 2 3; do
    s=$([ -n "$pid" ] && ps -o %cpu= -p "$pid" | tr -d ' ' || echo 0); s=${s:-0}
    total=$(echo "$total + $s" | bc -l); [ $n -lt 3 ] && sleep 5
  done
  echo "scale=2; $total / 3" | bc -l
}

detach_ram() {
  [ -n "$RAMDEV" ] || return 0
  local n forced=false
  for n in $(seq 1 40); do
    lsof +D /Volumes/RamDisk0735 > "$RES/ramdisk-lsof-$n.txt" 2>/dev/null
    hdiutil detach "$RAMDEV" && break
    log "detach $RAMDEV busy (try $n), waiting 15s"; sleep 15
  done
  if diskutil info "$RAMDEV" > /dev/null 2>&1; then hdiutil detach -force "$RAMDEV"; forced=true; fi
  printf '{"action":"detach","device":"%s","mount":"/Volumes/RamDisk0735","time":"%s","forced":%s,"still_present":%s}\n' \
    "$RAMDEV" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$forced" "$(diskutil info "$RAMDEV" >/dev/null 2>&1 && echo true || echo false)" \
    >> "$RES/ramdisk-receipts.jsonl"
  log "detached $RAMDEV forced=$forced"; RAMDEV=""
}

attach_ram() {
  RAMDEV=$(hdiutil attach -nomount ram://8388608 | xargs)   # 4 GiB
  [ -n "$RAMDEV" ] || return 1
  diskutil erasevolume HFS+ RamDisk0735 "$RAMDEV" || return 1
  printf '{"action":"attach","device":"%s","mount":"/Volumes/RamDisk0735","time":"%s","bytes":4294967296}\n' \
    "$RAMDEV" "$(date '+%Y-%m-%dT%H:%M:%S%z')" >> "$RES/ramdisk-receipts.jsonl"
  log "attached $RAMDEV"
}

commit_push() {  # commit_push <message> <files or dirs...>: copy into wt-903/$REL, commit those only, ff push
  local msg=$1; shift
  mkdir -p "$WT903/$REL"; cp -R "$@" "$WT903/$REL/" 2>/dev/null
  git -C "$WT903" add -- "$REL" && git -C "$WT903" \
    -c user.name=mikeroysoft-agent -c user.email=339795455+mikeroysoft-agent@users.noreply.github.com -c commit.gpgsign=false \
    commit -q -m "docs(#903): r3-0815 ablation $msg" -m "Raw timings and metadata only; harness-results files, no code." -- "$REL" || { log "nothing to commit ($msg)"; return 0; }
  local head origin; head=$(git -C "$WT903" rev-parse HEAD)
  git -C "$WT903" fetch -q origin agent/903; origin=$(git -C "$WT903" rev-parse FETCH_HEAD)
  if [ -n "$origin" ] && git -C "$WT903" merge-base --is-ancestor "$origin" "$head"; then
    git -C "$WT903" push origin "$head:refs/heads/agent/903" && log "pushed $head ($msg)" || log "PUSH FAILED $head ($msg)"
  else log "NOT fast-forward: origin '$origin' not ancestor of '$head'; not pushed"; fi
}

cleanup() {
  local rc=$?
  detach_ram
  if [ -f "$PAUSE" ] && grep -q r3-0735 "$PAUSE"; then
    rm -f "$PAUSE"; date '+%Y-%m-%dT%H:%M:%S%z' > "$R/resume-time.txt"; log "factory resumed (pause file removed)"
  fi
  local c l; c=$(sha "$CFG"); l=$(sha "$TREE/uv.lock")
  log "final config sha $c (expect $CFG_SHA) uv.lock $l (expect $LOCK_SHA)"
  printf 'config_sha_after=%s\nuvlock_sha_after=%s\n' "$c" "$l" > "$R/shas-after.txt"
  [ "$c" = "$CFG_SHA" ] && [ "$l" = "$LOCK_SHA" ] || STATUS="${STATUS}+sha_changed"
  "$PY" "$H/analyze.py" credit "$RES/valid" > "$R/credit.json" 2>&1
  { echo "# r3-0815 #903 ablation results (Ultron, 69b50b7d)"; echo; echo "status: $STATUS"
    echo "finished: $(date '+%Y-%m-%d %H:%M %Z')"; echo; echo "## runs (every attempt)"; echo '```'; cat "$R/runs.tsv"; echo '```'
    echo; echo "## credit rule (f), valid runs only"; echo '```json'; cat "$R/credit.json"; echo '```'
    echo; echo "## RAM disk receipts"; echo '```'; cat "$RES/ramdisk-receipts.jsonl" 2>/dev/null; echo '```'
    echo; echo "## shas"; echo '```'; cat "$R/shas-before-r3-0815.txt" "$R/shas-after.txt"; echo '```'
    echo; echo "pause created: $(cat "$R/pause-created-r3-0815.txt"); resumed: $(cat "$R/resume-time.txt" 2>/dev/null)"; } > "$R/results.md"
  commit_push "final: $STATUS" "$R/results.md" "$R/credit.json" "$R/driver.log" "$R/runs.tsv" "$R/shas-after.txt"
  [ -f "$RES/ramdisk-receipts.jsonl" ] && commit_push "ramdisk receipts" "$RES/ramdisk-receipts.jsonl"
  if [ "$STATUS" = done ]; then touch "$R/DONE"; else echo "$STATUS rc=$rc" > "$R/FAILED"; fi
  log "driver exit status=$STATUS"
}
trap cleanup EXIT
trap 'STATUS=interrupted; exit 130' INT TERM HUP

state() {  # state <file>: load + VM CPU + benito/factory processes
  { date '+%Y-%m-%dT%H:%M:%S%z'; sysctl vm.loadavg; uptime; echo "vm_cpu_mean3=$(vm_sample)"
    echo "--- benito/factory processes"; ps -axo pid,ppid,etime,%cpu,command | grep -iE 'benito|factory' | grep -v grep | cut -c1-220
    echo "--- top cpu"; ps -axo pid,%cpu,command -r | head -8 | cut -c1-160; } > "$1"
}

preflight() {  # preflight <out-dir>
  local out=$1
  while pgrep -f 'factory-pass-guard.sh factory ' > /dev/null; do log "factory pass in flight; waiting"; sleep 30; done
  [ -f "$PAUSE" ] || { log "pause file missing"; STATUS=pause_missing; exit 1; }
  [ "$(sha "$CFG")" = "$CFG_SHA" ] || { STATUS=config_sha_changed; exit 1; }
  [ "$(sha "$TREE/uv.lock")" = "$LOCK_SHA" ] || { STATUS=uvlock_sha_changed; exit 1; }
  state "$out/preflight.txt"
}

run_one() {  # run_one <label> <arm> <out-dir>; sets RUN_SECONDS
  local label=$1 arm=$2 out=$3 t0 tmp
  mkdir -p "$out"; preflight "$out"; tmp=$(getconf DARWIN_USER_TEMP_DIR)
  local -a envs=(HOME="$R/home" XDG_CONFIG_HOME="$R/home/.config" UV_CACHE_DIR=/Users/mike/.cache/uv UV_EXCLUDE_NEWER=PT24H
    PATH="$BASEPATH" GIT_AUTHOR_NAME=mikeroysoft-agent GIT_COMMITTER_NAME=mikeroysoft-agent
    GIT_AUTHOR_EMAIL=339795455+mikeroysoft-agent@users.noreply.github.com GIT_COMMITTER_EMAIL=339795455+mikeroysoft-agent@users.noreply.github.com
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false LANG=en_US.UTF-8 TMPDIR="$tmp")
  case $arm in
    ram) attach_ram || { STATUS=ram_attach_failed; exit 1; }; envs+=(TMPDIR=/Volumes/RamDisk0735/) ;;
    git-cache) mkdir -p "$R/gitcache-r3-0815-$(basename "$out")"; envs+=(GIT_CACHE_DIR="$R/gitcache-r3-0815-$(basename "$out")" PATH="$H/gitbin:$BASEPATH") ;;
    scratch-throttle) envs+=(PYTHONPATH="$H/throttle-site" R3_0735_THROTTLE=1) ;;
  esac
  printf '%s\n' "${envs[@]}" > "$out/env.txt"
  log "start $(basename "$out") ($arm)"; t0=$(date +%s)
  (cd "$TREE" && exec env -i "${envs[@]}" "$PY" "$H/arm_runner.py" "$arm" "$out" > "$out/stdout.log" 2> "$out/stderr.log") &
  local pid=$!; echo "$pid" > "$out/pid"; wait "$pid"; local rc=$?
  log "end $(basename "$out") rc=$rc wall=$(( $(date +%s) - t0 ))s"
  state "$out/postflight.txt"
  [ "$arm" = ram ] && detach_ram
  local before after; before=$(sed -n 's/^vm_cpu_mean3=//p' "$out/preflight.txt"); after=$(sed -n 's/^vm_cpu_mean3=//p' "$out/postflight.txt")
  printf '{"before": %s, "after": %s, "mean": %s}\n' "$before" "$after" "$(echo "scale=2; ($before + $after) / 2" | bc -l)" > "$out/vm.json"
  tail -c 200000 "$out/stderr.log" > "$out/stderr-tail.log"
  [ -f "$out/outcomes.json" ] || { STATUS="no_outcomes_$(basename "$out")"; exit 1; }
  RUN_SECONDS=$("$PY" -c 'import json,sys;print(round(json.load(open(sys.argv[1]))["seconds"]))' "$out/outcomes.json")
  [ "$RUN_SECONDS" -gt "$MAXRUN" ] && MAXRUN=$RUN_SECONDS
}

publish() {  # publish <out-dir>
  local out=$1 stage; stage="$STAGE/$(basename "$1")"; mkdir -p "$stage"
  cp "$out"/{result.json,outcomes.json,preflight.txt,postflight.txt,vm.json,env.txt,stderr-tail.log,checks.txt} "$stage/" 2>/dev/null
  commit_push "$(basename "$out")" "$stage" "$R/runs.tsv"
}

row() {  # row <label> <arm> <out> <verdict>
  local o=$3 c v
  c=$("$PY" -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["tests_run"],len(d["failure_ids"]),len(d["error_ids"]),*[round(x,2) for x in d["load_before"]],"/",*[round(x,2) for x in d["load_after"]],sep=" ")' "$o/outcomes.json")
  v=$("$PY" -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["before"],d["after"],sep="/")' "$o/vm.json")
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(basename "$o")" "$2" "$RUN_SECONDS" "$4" "$c" "$v" "$(date '+%H:%M')" >> "$R/runs.tsv"
}

mkdir -p "$RES/valid" "$STAGE"
log "driver start pid $$; budget ${BUDGET}s"
printf 'run\tarm\tseconds\tverdict\ttests fail err load_before(1/5/15) / load_after\tvm_cpu_before/after\tended\n' > "$R/runs.tsv"
commit_push start "$R/start-meta-r3-0815.txt"
for item in "${SEQ[@]}"; do
  label=${item%%:*}; arm=${item#*:}
  for attempt in 1 2; do
    if [ $(( $(date +%s) - START + MAXRUN * 11 / 10 )) -gt "$BUDGET" ]; then STATUS=budget_exhausted_before_$label; exit 1; fi
    out="$RES/$label-try$attempt"; run_one "$label" "$arm" "$out"
    if [ "$label" = control-1 ]; then
      "$PY" "$H/analyze.py" control1 "$out/outcomes.json" > "$out/checks.txt"; ok=$?
      row "$label" "$arm" "$out" "$([ $ok = 0 ] && echo clean || echo NOT_CLEAN)"; publish "$out"
      [ $ok = 0 ] || { STATUS=control1_not_clean_strike2; exit 1; }
      cp -R "$out" "$RES/valid/$label"; break
    fi
    "$PY" "$H/analyze.py" same "$RES/valid/control-1/outcomes.json" "$out/outcomes.json"; same=$?
    "$PY" "$H/analyze.py" vmcheck "$RES/valid" "$label" "$out" > "$out/checks.txt"; vm=$?
    echo "outcome_matches_control1=$([ $same = 0 ] && echo yes || echo no)" >> "$out/checks.txt"
    if [ $same = 0 ] && [ $vm = 0 ]; then verdict=valid
    elif [ $same != 0 ] && [ $vm != 0 ]; then verdict=INVALID_outcome+vm
    elif [ $same != 0 ]; then verdict=INVALID_outcome
    else verdict=INVALID_vm; fi
    row "$label" "$arm" "$out" "$verdict"; publish "$out"
    if [ "$verdict" = valid ]; then cp -R "$out" "$RES/valid/$label"; break; fi
    log "$label try$attempt $verdict"
    [ "$attempt" = 2 ] && { STATUS="two_strikes_$label"; exit 1; }
  done
done
STATUS=done
