# -*- coding: utf-8 -*-
"""gunicorn 启动配置 —— 生产环境用 gunicorn 替代 Flask 开发服务器。
SSE 是长连接，必须用 gevent worker 处理，sync worker 会阻塞。
"""
import os

bind = "0.0.0.0:5000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "gevent"
worker_connections = 1000
timeout = 120
keepalive = 5
loglevel = "info"
accesslog = "-"
errorlog = "-"
preload_app = True