#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
MODEL_REPO="ggml-org/Qwen3-1.7B-GGUF"
MODEL_FILE="Qwen3-1.7B-Q4_K_M.gguf"

for bin in git cmake ninja vulkaninfo glslc; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "Missing system tool: $bin (install build-essential cmake ninja-build libvulkan-dev mesa-vulkan-drivers vulkan-tools glslc spirv-headers git python3-venv)" >&2
    exit 1
  fi
done
vulkaninfo --summary
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt" huggingface_hub
mkdir -p "$ROOT/models/tts"
"$ROOT/.venv/bin/hf" download rhasspy/piper-voices en/en_GB/alan/medium/en_GB-alan-medium.onnx --local-dir "$ROOT/models/tts"
"$ROOT/.venv/bin/hf" download rhasspy/piper-voices en/en_GB/alan/medium/en_GB-alan-medium.onnx.json --local-dir "$ROOT/models/tts"
mv -f "$ROOT/models/tts/en/en_GB/alan/medium/en_GB-alan-medium.onnx" "$ROOT/models/tts/en_GB-alan-medium.onnx"
mv -f "$ROOT/models/tts/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json" "$ROOT/models/tts/en_GB-alan-medium.onnx.json"
rm -rf "$ROOT/models/tts/en"
mkdir -p "$ROOT/models"
if [[ ! -f "$ROOT/models/$MODEL_FILE" ]]; then
  "$ROOT/.venv/bin/hf" download "$MODEL_REPO" "$MODEL_FILE" --local-dir "$ROOT/models"
fi
if [[ ! -d "$ROOT/llama.cpp/.git" ]]; then git clone https://github.com/ggml-org/llama.cpp.git "$ROOT/llama.cpp"; fi
cmake -S "$ROOT/llama.cpp" -B "$ROOT/llama.cpp/build" -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build "$ROOT/llama.cpp/build" --config Release --target llama-server -j"$(nproc)"
"$ROOT/llama.cpp/build/bin/llama-server" --version
echo "Bootstrap complete. Run: $ROOT/install-user.sh"
