#!/usr/bin/env bash
set -euo pipefail

# 62 单卡研究部署；生产 30000 当前仍由 Qwen3.6 占用，切换前先停旧进程。
runtime_dir="${QWEN38_RUNTIME_DIR:-/home/admin2/sglang_qwen38_env}"
model_path="${QWEN38_MODEL_PATH:-/home/admin2/models/Qwen/Qwen3.8-27B-FP8-017b9c7a}"
port="${QWEN38_PORT:-30000}"
mem_fraction="${QWEN38_MEM_FRACTION:-0.85}"
context_length="${QWEN38_CONTEXT_LENGTH:-65536}"
max_requests="${QWEN38_MAX_RUNNING_REQUESTS:-8}"
max_mamba_cache="${QWEN38_MAX_MAMBA_CACHE_SIZE:-40}"

export PATH="${runtime_dir}/bin:/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin"
export CUDA_HOME=/usr/local/cuda
export SGLANG_DISABLE_CUDNN_CHECK=1

exec "${runtime_dir}/bin/python" -m sglang.launch_server \
  --model-path "${model_path}" \
  --served-model-name Qwen/Qwen3.8-27B-FP8 \
  --host 0.0.0.0 \
  --port "${port}" \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --mem-fraction-static "${mem_fraction}" \
  --context-length "${context_length}" \
  --cuda-graph-max-bs-decode 8 \
  --max-running-requests "${max_requests}" \
  --max-mamba-cache-size "${max_mamba_cache}" \
  --mamba-ssm-dtype float32 \
  --mamba-radix-cache-strategy extra_buffer \
  --kv-cache-dtype bfloat16 \
  --chunked-prefill-size 2048 \
  --sampling-backend pytorch \
  --trust-remote-code \
  --attention-backend triton
