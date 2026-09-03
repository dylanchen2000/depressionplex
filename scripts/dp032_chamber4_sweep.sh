#!/usr/bin/env bash
# DP-032：7 个视频 × 隔间 4 全扫，并打印 v1..v7 → 文件名映射表
#
# 要回答的问题只有一个：**7 个视频里，隔间 4 被判"无动物"的到底是几个？**
#   恰好 1 个（应是 20mg_3周）⇒ STATUS.md 里"v4/v7 隔间4"是读法①，虚警，DP-032 关闭
#   2 个                      ⇒ 第二个里面有活老鼠而软件说它空了 = **G10 假阳性**，必须修
#                                （spec 要求 G10 假阳性为 0）
#
# 【DP-034 拆分后的口径（2026-09-04）】"无动物"= `never_occupied`（从未有动物级掩膜）；
#   `detached` = 有尾级（悬挂失效/中途脱落）——科学含义不同，定案段分开计数。
#   实测定案：never_occupied=2（v4、v7），帧级目检两者都是真阴性 ⇒ 无假阳性，DP-032 关闭。
#
# 必须在**有视频的机器上**跑（Mac）：沙箱 cv2/ffmpeg 均损坏，解不了视频。
# 依赖：ffmpeg + ffprobe（Mac 已装）、python3 + numpy + PIL。probe_frames 只吃 PNG，不吃视频，
#       所以本脚本先抽帧再体检。
#
# 用法（在仓库根目录跑）：
#   bash scripts/dp032_chamber4_sweep.sh
#   bash scripts/dp032_chamber4_sweep.sh "<视频目录>" "<输出目录>"

set -uo pipefail

VID_DIR="${1:-$HOME/Work/heavy/depression/悬尾}"
OUT="${2:-/tmp/dp032}"
NCALIB=31      # 全片均匀抽 31 帧标定——与 2026-08-24 那次跨视频检查同口径，不要改
CUR_FROM=6000  # 静止时段 8 连帧（同 2026-08-24 的 B 批）
CUR_TO=6007

for tool in ffmpeg ffprobe python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺 ${tool}，装了再跑"; exit 127; }
done
[ -f depressionplex/cli/probe_frames.py ] || { echo "请在 depressionplex 仓库根目录跑本脚本"; exit 2; }
[ -d "$VID_DIR" ] || { echo "视频目录不存在：${VID_DIR}（用第一个参数指定）"; exit 2; }

mkdir -p "$OUT"
SUMMARY="$OUT/_汇总.txt"
: > "$SUMMARY"

# ---- 1. v1..v7 → 文件名映射表（歧义的根源就是仓库里没这张表）----
i=0
while IFS= read -r v; do
  i=$((i + 1))
  VIDS[$i]="$v"
done < <(find "$VID_DIR" -maxdepth 1 -type f \
           \( -iname '*.mp4' -o -iname '*.avi' -o -iname '*.mov' \) | LC_ALL=C sort)
NV=$i
[ "$NV" -gt 0 ] || { echo "在 $VID_DIR 下没找到视频"; exit 2; }

{
  echo "==== v1..v$NV → 文件名映射表（按文件名 LC_ALL=C sort 排序）===="
  echo "注意：2026-08-24 那次检查用的是什么排序，仓库里没记，所以本表是**假设**。"
  echo "但结论不依赖它——下面 7 个视频全跑，只数'隔间4 被判无动物'的个数即可定案。"
  for k in $(seq 1 "$NV"); do printf 'v%-2d = %s\n' "$k" "$(basename "${VIDS[$k]}")"; done
} | tee -a "$SUMMARY"

# ---- 2. 逐视频抽帧 + 体检 ----
for k in $(seq 1 "$NV"); do
  v="${VIDS[$k]}"
  base=$(basename "$v")
  D="$OUT/v$k"
  rm -rf "$D"; mkdir -p "$D/calib" "$D/cur"

  N=$(ffprobe -v error -select_streams v:0 -count_frames \
        -show_entries stream=nb_read_frames -of csv=p=0 "$v" 2>/dev/null | tr -dc '0-9')
  [ -n "$N" ] && [ "$N" -gt 0 ] || { echo "v$k [$base] 读不出帧数，跳过" | tee -a "$SUMMARY"; continue; }
  STEP=$((N / NCALIB)); [ "$STEP" -ge 1 ] || STEP=1

  ffmpeg -nostdin -v error -y -i "$v" \
    -vf "select='not(mod(n\,$STEP))'" -vsync 0 "$D/calib/c_%04d.png"
  ffmpeg -nostdin -v error -y -i "$v" \
    -vf "select='between(n,$CUR_FROM,$CUR_TO)'" -vsync 0 "$D/cur/f_%04d.png"

  NCUR=$(ls "$D/cur" | wc -l | tr -d ' ')
  if [ "$NCUR" -lt 2 ]; then   # 视频比 6007 帧短，退到末尾 8 帧
    s=$((N - 8)); [ "$s" -ge 0 ] || s=0
    ffmpeg -nostdin -v error -y -i "$v" \
      -vf "select='gte(n,$s)'" -vsync 0 "$D/cur/f_%04d.png"
  fi

  python3 -m depressionplex.cli.probe_frames "$D"/cur/f_*.png \
    --chambers 4 --calibrate-from "$D"/calib/c_*.png > "$D/probe.txt" 2>&1

  {
    echo
    echo "==== v$k  $base   （总帧 ${N}，标定抽 $(ls "$D/calib" | wc -l | tr -d ' ') 帧，步长 ${STEP}）===="
    echo "-- 隔间定位 --"; grep -E '个: \[' "$D/probe.txt" || echo "  (无)"
    echo "-- 第4节 逐隔间分割（只看隔间4）--"
    awk '/== 4\./,/== 5\./' "$D/probe.txt" | grep -A3 '隔间4' || echo "  (无隔间4 输出)"
    echo "-- 第5节 试次级有效性（这一行是判据）--"
    awk '/== 5\./,0' "$D/probe.txt" | grep -E '隔间4|建议排除|疑似截断' || echo "  (无第5节——标定帧不足?)"
  } | tee -a "$SUMMARY"
done

# ---- 3. 定案：数一数隔间 4 被判"非 valid"的有几个 ----
{
  echo
  echo "==================== 定案（DP-034 拆分口径）===================="
  hit=0; bad=0; n_never=0; n_det=0; n_other=0
  for k in $(seq 1 "$NV"); do
    line=$(awk '/== 5\./,0' "$OUT/v$k/probe.txt" 2>/dev/null | grep -E '隔间4:' | head -1)
    st=$(printf '%s' "$line" | sed -n 's/.*隔间4: *\([a-z_]*\).*/\1/p')
    [ -n "$st" ] || st="脚本无输出"
    printf 'v%-2d 隔间4 = %-18s %s\n' "$k" "$st" "$(basename "${VIDS[$k]}")"
    case "$st" in
      valid)           ;;
      脚本无输出)       bad=$((bad + 1)) ;;
      never_occupied)  hit=$((hit + 1)); n_never=$((n_never + 1)) ;;
      detached)        hit=$((hit + 1)); n_det=$((n_det + 1)) ;;
      *)               hit=$((hit + 1)); n_other=$((n_other + 1)) ;;
    esac
  done
  echo
  if [ "$bad" -gt 0 ]; then
    echo "[!] 有 $bad 个视频没跑出第 5 节结果——先查 $OUT/v*/probe.txt，**不要**用下面的计数定案"
  fi
  echo "隔间 4 被判'非 valid' 的视频数 = $hit（其中 never_occupied=$n_never / detached=$n_det / 其他=$n_other）"
  echo "  '被判无动物' = never_occupied 计数；detached 是悬挂失效/中途脱落（G10b，实验失败须上报），两支**不得合并计数**"
  echo "  = 1  ⇒ 读法①成立（且应是 20mg_3周），DP-032 关闭，无假阳性"
  echo "  = 2  ⇒ 若两个都是 never_occupied ⇒ 与 DP-005 复核一致（v4 为尾级应为 detached）；"
  echo "         若其一是 detached/valid 而被算进'无动物' ⇒ 有活鼠被判空 = **G10 假阳性**，必须修"
  echo " >= 3  ⇒ 与道俊目检（只有 20mg_3周-ch4 是空的）冲突，先别改代码，回来对口径"
  echo
  echo "全文在 $OUT/v*/probe.txt，本汇总在 $SUMMARY"
} | tee -a "$SUMMARY"
