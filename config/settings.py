import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me-in-production")
ON_VERCEL = bool(os.environ.get("VERCEL"))
DEBUG = os.environ.get("DEBUG", "0" if ON_VERCEL else "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", ".vercel.app,localhost,127.0.0.1" if ON_VERCEL else "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
if ON_VERCEL:
    CSRF_TRUSTED_ORIGINS += ["https://" + h for h in (os.environ.get("VERCEL_URL"), os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")) if h]
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rms",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "rms.middleware.ContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "rms.context.global_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---- Database: SQLite by default. Set DB_ENGINE=postgresql / mysql for production.
_engine = os.environ.get("DB_ENGINE", "sqlite")
_dburl = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")   # Neon / Vercel Postgres / Supabase
if _dburl:
    from urllib.parse import urlparse, unquote, parse_qs
    _u = urlparse(_dburl.replace("postgres://", "postgresql://", 1))
    _q = {k: v[0] for k, v in parse_qs(_u.query).items() if k in ("sslmode", "channel_binding")}
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", "NAME": _u.path.lstrip("/"), "USER": unquote(_u.username or ""),
        "PASSWORD": unquote(_u.password or ""), "HOST": _u.hostname, "PORT": str(_u.port or 5432),
        "CONN_MAX_AGE": 0, "OPTIONS": {"sslmode": "require", **_q},
    }}
elif _engine == "postgresql":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "restopro"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }}
elif _engine == "mysql":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("DB_NAME", "restopro"),
        "USER": os.environ.get("DB_USER", "root"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "3306"),
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
    }}

AUTH_USER_MODEL = "rms.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en"
TIME_ZONE = "UTC"          # stored in UTC; displayed per-branch timezone
USE_I18N = False           # we use our own light-weight translation layer (rms/i18n.py)
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
os.makedirs(STATIC_ROOT, exist_ok=True)   # run `collectstatic` for production
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = Path("/tmp/media") if ON_VERCEL else BASE_DIR / "media"   # Vercel disk is read-only/ephemeral: use Vercel Blob/S3 for permanent photos

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- Outgoing email (daily reports, supplier purchase orders). Leave EMAIL_HOST empty to only record them in the log.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "restopro@localhost")
