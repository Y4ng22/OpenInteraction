#!/bin/bash
# Smoke-test Fun-Audio-Chat-8B training on the lab server: build 10 samples, then run each config for 20 steps on the
# given GPUs, logging peak memory per card. Usage: run_smoke.sh <gpu list, e.g. 0,1,6,7> [config ...]
# The model is a copy of Fun-Audio-Chat-8B's folder (weights linked) whose config switches the speech head off:
# with text-only targets its loss averages over zero tokens and turns the total loss into NaN.
set -uo pipefail
GPUS=$1; shift
CONFIGS=${@:-smoke_lora_z3 smoke_qlora}
ROOT=/local/data/xy2764
FUN=$ROOT/Fun-Audio-Chat
HERE=$ROOT/experiments/delegation-train
WORK=$ROOT/delegation-train/smoke
PY=$ROOT/envs/funtrain/bin
mkdir -p $WORK/logs

$PY/python $HERE/make_smoke.py --speechfc $ROOT/data/SpeechFCEval --fun-repo $FUN --out $WORK || exit 1

export AUDIO_PLACEHOLDER="<|audio_bos|><|AUDIO|><|audio_eos|>"
export DISABLE_VERSION_CHECK=1 FORCE_TORCHRUN=1
export PYTHONPATH=$FUN/training/plugin:$FUN
export HF_HOME=/local/data/shared/huggingface_cache
export TRITON_CACHE_DIR=$ROOT/.cache/triton
export CUDA_VISIBLE_DEVICES=$GPUS
export PATH=$PY:$PATH

for cfg in $CONFIGS; do
  echo "== $cfg on GPUs $GPUS"
  nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
  sed -e "s#__DATA__#$WORK#" -e "s#__OUT__#$WORK/out#" -e "s#__MODEL__#$ROOT/delegation-train/model-s2t#" \
      -e "s#__DS_Z3__#$FUN/third_party/LLaMA-Factory/examples/deepspeed/ds_z3_config.json#" \
      $HERE/configs/$cfg.yaml > $WORK/$cfg.yaml
  # peak memory per card while this config runs
  : > $WORK/logs/$cfg.mem
  ( while true; do nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits >> $WORK/logs/$cfg.mem; sleep 5; done ) &
  MON=$!
  start=$(date +%s)
  (cd $FUN/training && llamafactory-cli train $WORK/$cfg.yaml) > $WORK/logs/$cfg.log 2>&1
  code=$?
  kill $MON
  echo "$cfg exit=$code seconds=$(( $(date +%s) - start ))"
  python3 -c "
import collections,sys
peak=collections.defaultdict(int)
for l in open('$WORK/logs/$cfg.mem'):
    i,m=[x.strip() for x in l.split(',')]; peak[i]=max(peak[i],int(m))
print('peak MiB per card:', dict(peak))"
  grep -E "'loss'|train_runtime|Error|error" $WORK/logs/$cfg.log | tail -6
done
