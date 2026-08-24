#!/usr/bin/env bash
# Render.com build script — installs CPU-only PyTorch to stay under 512MB RAM

set -e

echo "==> Installing CPU-only PyTorch (no CUDA) to reduce memory..."
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu --no-cache-dir

echo "==> Installing remaining dependencies..."
pip install -r requirements.txt --no-cache-dir

echo "==> Downloading YOLOv8n model weights at build time..."
python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

echo "==> Build complete!"
