#!/bin/bash
# -*- coding: utf-8 -*-
# 华为云 ECS 部署脚本 —— 在 ECS 上执行
# 用法: bash deploy.sh
set -e

APP_DIR="/opt/werewolf"
SERVICE_NAME="werewolf"

echo "=== 狼人杀部署脚本 ==="

# 1. 安装系统依赖
echo "[1/6] 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip

# 2. 创建应用目录（如已存在则跳过）
echo "[2/6] 创建应用目录..."
if [ ! -d "$APP_DIR" ]; then
    sudo mkdir -p "$APP_DIR"
    sudo chown $(whoami):$(whoami) "$APP_DIR"
fi

# 3. 创建虚拟环境
echo "[3/6] 创建 Python 虚拟环境..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
source "$APP_DIR/venv/bin/activate"

# 4. 安装 Python 依赖
echo "[4/6] 安装 Python 依赖..."
pip install --upgrade pip -q
pip install -r "$APP_DIR/requirements.txt" -q

# 5. 安装 systemd 服务
echo "[5/6] 配置 systemd 服务..."
sudo cp "$APP_DIR/werewolf.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME

# 6. 启动服务
echo "[6/6] 启动服务..."
sudo systemctl restart $SERVICE_NAME
sleep 2
sudo systemctl status $SERVICE_NAME --no-pager

echo ""
echo "=== 部署完成 ==="
echo "访问地址: http://<ECS公网IP>:5000/"
echo "查看日志: sudo journalctl -u $SERVICE_NAME -f"
echo "重启服务: sudo systemctl restart $SERVICE_NAME"
echo "停止服务: sudo systemctl stop $SERVICE_NAME"