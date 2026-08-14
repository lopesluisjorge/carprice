"""Gunicorn configuration, read by the carprice-web systemd unit.

Only what differs from the defaults, and nothing that belongs in Django's
settings — this file never imports the project.
"""

import multiprocessing
import os

# Loopback only: nginx is the one thing that talks to it, and binding here keeps
# the port off every other interface without any socket-permission dance. A unix
# socket also works, at the cost of putting nginx's user in the carprice group:
#     bind = 'unix:/run/carprice/gunicorn.sock'
bind = os.environ.get('GUNICORN_BIND', '127.0.0.1:8000')

# The usual 2*cpu+1, capped. Every worker holds its own database connection
# (see CONN_MAX_AGE), and the cap is what keeps a many-core box from opening
# more of them than Postgres' max_connections (100 by default) allows. Raise
# both together, never just this one.
workers = int(
    os.environ.get('GUNICORN_WORKERS', min(multiprocessing.cpu_count() * 2 + 1, 8))
)

# Sync workers on purpose: web/ only ever talks to the database. The FIPE calls —
# the one thing here that blocks for minutes — live in process_crawl_queue,
# which is a separate unit precisely so they never tie up a request worker.
worker_class = 'sync'

timeout = int(os.environ.get('GUNICORN_TIMEOUT', 30))
graceful_timeout = 30
keepalive = 5

# Recycle workers to bound the damage of any slow leak; the jitter keeps them
# from all restarting on the same request.
max_requests = 1000
max_requests_jitter = 100

# Both to stderr/stdout, which is where journald picks them up:
#     journalctl -u carprice-web -f
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
# %({x-forwarded-for}i)s rather than %(h)s: behind nginx the peer is always
# 127.0.0.1, so the default access log records nothing useful.
access_log_format = '%({x-forwarded-for}i)s %(t)s "%(r)s" %(s)s %(b)s %(M)sms "%(f)s" "%(a)s"'

# Only nginx connects, and nginx sets these itself, so trusting them is safe
# here. Without it gunicorn ignores X-Forwarded-* from an unknown peer.
forwarded_allow_ips = '127.0.0.1'

proc_name = 'carprice'
