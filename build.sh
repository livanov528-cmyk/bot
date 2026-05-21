#!/bin/bash
echo "=== Installing system dependencies ==="
apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    python3-dev \
    build-essential

echo "=== Installing Python packages ==="
pip install --no-cache-dir -r requirements.txt

echo "=== Build completed ==="
