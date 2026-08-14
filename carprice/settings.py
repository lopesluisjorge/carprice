"""
Django settings for carprice project.

Every knob that differs between a laptop and the server is read from the
environment, so this file is the same in both places. Development keeps those
values in a ``.env`` (see ``.env.example``); production hands them to systemd
through ``EnvironmentFile`` (see ``docs/deploy.md``).

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.1/ref/settings/
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# Optional: production passes the whole configuration through the environment
# instead of a file.
if (BASE_DIR / '.env').exists():
    environ.Env.read_env(BASE_DIR / '.env')


# DEBUG defaults to False so an unconfigured box fails closed: it refuses to
# start rather than quietly serving tracebacks to the internet. Development
# turns it back on in .env — `cp .env.example .env` is the whole setup.
DEBUG = env.bool('DEBUG', default=False)


def require(name, cast=None):
    """Read a setting that may only fall back to a development default.

    With DEBUG on it is the caller's default; with DEBUG off there is no
    default at all, and a missing value raises here — at import time, on the
    box that is misconfigured — instead of at the first request.
    """
    try:
        return env(name, cast=cast)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(
            f'{name} precisa estar definida quando DEBUG=False. '
            f'Veja .env.example e docs/deploy.md.'
        ) from exc


if DEBUG:
    SECRET_KEY = env(
        'SECRET_KEY',
        default='django-insecure-1d-mlgn9*0r7ly7)iydal&=lfs1%=)2+9hg@+ris#bx2_qf*ak',
    )
    ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', '[::1]'])
else:
    SECRET_KEY = require('SECRET_KEY')
    ALLOWED_HOSTS = require('ALLOWED_HOSTS', cast=list)

# Derived from ALLOWED_HOSTS rather than configured twice: behind a TLS proxy
# every POST needs the origin trusted, and forgetting it shows up as a 403 on
# the search form — far from anything that mentions CSRF. A leading dot is a
# subdomain wildcard on both settings, so the hosts carry over as they are.
CSRF_TRUSTED_ORIGINS = env.list(
    'CSRF_TRUSTED_ORIGINS',
    default=[f'https://{host}' for host in ALLOWED_HOSTS if host != '*'],
)


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'crawler',
    'web',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Right after SecurityMiddleware and before everything else: static files
    # are served without touching sessions, auth or the database.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'carprice.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'carprice.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

# Postgres in development and in production; the default matches compose.yml, so
# `docker compose up -d` is the whole local setup. SQLite is still supported —
# `DATABASE_URL=sqlite:///db.sqlite3` runs the suite with no container at all —
# and both engines are exercised by the same tests.
DATABASES = {
    'default': env.db_url(
        'DATABASE_URL',
        default='postgres://carprice:carprice@127.0.0.1:5432/carprice',
    )
}

# Persistent connections: each gunicorn worker keeps its connection instead of
# reconnecting per request. The health check is what makes that safe — a
# connection dropped by a Postgres restart is discarded, not handed to a view.
DATABASES['default']['CONN_MAX_AGE'] = env.int('CONN_MAX_AGE', default=0 if DEBUG else 60)
DATABASES['default']['CONN_HEALTH_CHECKS'] = not DEBUG


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'pt-br'

TIME_ZONE = 'America/Sao_Paulo'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'

# collectstatic lands here and WhiteNoise serves from here. Kept out of the
# repo: it is a build product, rebuilt on every deploy.
STATIC_ROOT = Path(env('STATIC_ROOT', default=str(BASE_DIR / 'staticfiles')))

# Hashed names plus a manifest, so every asset can be cached forever and a
# deploy invalidates it by changing the name. Only outside DEBUG: the manifest
# only exists after collectstatic, and demanding it in development would mean
# running collectstatic to see a CSS edit.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': (
            'django.contrib.staticfiles.storage.StaticFilesStorage'
            if DEBUG
            else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
        ),
    },
}


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

# Nothing in the app sends mail today — there is no signup, no password reset
# flow and no alerting. So SMTP is opt-in: set EMAIL_HOST and it is used, leave
# it empty and mail goes to the log like it always did.
#
# The locals are deliberately lowercase. Django 6 refuses to start if a
# deprecated EMAIL_* setting is defined alongside MAILERS, and any uppercase
# module-level name here becomes a setting.
_email_host = env('EMAIL_HOST', default='')

if _email_host:
    MAILERS = {
        'default': {
            'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'OPTIONS': {
                'host': _email_host,
                'port': env.int('EMAIL_PORT', default=587),
                'username': env('EMAIL_HOST_USER', default=''),
                'password': env('EMAIL_HOST_PASSWORD', default=''),
                'use_tls': env.bool('EMAIL_USE_TLS', default=True),
            },
        },
    }
else:
    MAILERS = {
        'default': {
            'BACKEND': 'django.core.mail.backends.console.EmailBackend',
        },
    }
    # mail.E001 flags the console backend as development-only, and it is right
    # in general — it is silenced only for the case it cannot see: an app with
    # no outgoing mail at all. Setting EMAIL_HOST un-silences it by taking the
    # branch above.
    SILENCED_SYSTEM_CHECKS = ['mail.E001']


# Logging
# Everything to stderr, which is where systemd picks it up: `journalctl -u
# carprice-web`. Without this the default configuration mails unhandled errors
# to ADMINS and prints nothing, so with DEBUG off a 500 leaves no trace.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': env('LOG_LEVEL', default='INFO'),
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}


# On-demand collection
# The worker's single-process lock. Configurable because the deployed tree may
# be read-only, and because /run is where a runtime lock belongs on a server.
CRAWL_QUEUE_LOCK_PATH = Path(
    env('CRAWL_QUEUE_LOCK_PATH', default=str(BASE_DIR / '.crawl_queue.lock'))
)


# Security
# Only outside DEBUG: forcing HTTPS on a laptop would redirect runserver into a
# port nothing is listening on.

if not DEBUG:
    # nginx terminates TLS and proxies plain HTTP, so Django learns the real
    # scheme from the header nginx sets. It must be set by the proxy on every
    # request — a proxy that forwards a client-supplied value would let anyone
    # claim HTTPS.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Off by default, and turned on by hand after HTTPS is confirmed working:
    # HSTS is remembered by the browser, so a premature year-long value locks
    # visitors out of a domain whose certificate later breaks. docs/deploy.md
    # has the ramp. `check --deploy` warns while this is 0, on purpose.
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
    SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)

    # Nothing here is meant to be framed.
    X_FRAME_OPTIONS = 'DENY'
