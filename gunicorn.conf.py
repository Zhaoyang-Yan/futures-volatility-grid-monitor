import os


# Keep one worker while Web and background monitor worker run in the same process.
# More workers may duplicate iFind polling, AlertLog writes, and Feishu notifications.
workers = 1

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")
worker_class = "sync"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
