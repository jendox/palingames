import os
from pathlib import Path

import dj_database_url
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

env.read_env(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env.bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

CSRF_TRUSTED_ORIGINS = [
    "https://*.ngrok-free.app",
]

# Proxy/HTTPS (Caddy/nginx)
# SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # allauth core
    "allauth",
    "allauth.account",
    "allauth.headless",
    # allauth social
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.yandex",

    "django_tailwind_cli",
    "django_celery_beat",
    # my apps
    "apps.access.apps.AccessConfig",
    "apps.core.apps.CoreConfig",
    "apps.users.apps.UsersConfig",
    "apps.pages.apps.PagesConfig",
    "apps.products.apps.ProductsConfig",
    "apps.cart.apps.CartConfig",
    "apps.favorites.apps.FavoritesConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.payments.apps.PaymentsConfig",
    "apps.promocodes.apps.PromocodesConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RequestContextLoggingMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.core.rate_limit_middleware.AuthRateLimitMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # allauth
    "allauth.account.middleware.AccountMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    # allauth
    "allauth.account.auth_backends.AuthenticationBackend",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(BASE_DIR, "templates"),
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.default_currency",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_USER_MODEL = "users.CustomUser"

# Database

DATABASE_URL = env.str("DATABASE_URL")

DATABASES = {
    "default": dj_database_url.parse(
        url=DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
    ),
}

# Password hashes
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization

LANGUAGE_CODE = "ru-ru"

TIME_ZONE = "Europe/Minsk"

USE_I18N = True

USE_TZ = True

REDIS_URL = env.str("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env.str("CACHE_REDIS_URL", default=REDIS_URL),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
        "KEY_PREFIX": "palingames",
    },
    "rate_limit": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env.str("RATE_LIMIT_REDIS_URL", default=REDIS_URL),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
        "KEY_PREFIX": "palingames",
        "TIMEOUT": None,
    },
}

CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", default=300)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = env.bool("CELERY_TASK_EAGER_PROPAGATES", default=True)
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

EXPRESS_PAY_TOKEN = env.str("EXPRESS_PAY_TOKEN", default="test-token")
EXPRESS_PAY_REQUEST_SECRET_WORD = env.str("EXPRESS_PAY_REQUEST_SECRET_WORD", default="secret")
EXPRESS_PAY_WEBHOOK_SECRET_WORD = env.str("EXPRESS_PAY_WEBHOOK_SECRET_WORD", default="secret")
EXPRESS_PAY_USE_SIGNATURE = env.bool("EXPRESS_PAY_USE_SIGNATURE", default=True)
EXPRESS_PAY_IS_TEST = env.bool("EXPRESS_PAY_IS_TEST", default=True)
EXPRESS_PAY_INVOICE_LIFETIME_HOURS = env.int("EXPRESS_PAY_INVOICE_LIFETIME_HOURS", default=24)
PAYMENTS_STATUS_SYNC_BATCH_SIZE = env.int("PAYMENTS_STATUS_SYNC_BATCH_SIZE", default=50)
PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS = env.int("PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS", default=300)

S3_ENDPOINT_URL = env.str("S3_ENDPOINT_URL", default="http://127.0.0.1:9000")
S3_ACCESS_KEY_ID = env.str("S3_ACCESS_KEY_ID", default="minioadmin")
S3_SECRET_ACCESS_KEY = env.str("S3_SECRET_ACCESS_KEY", default="minioadmin")
S3_REGION_NAME = env.str("S3_REGION_NAME", default="us-east-1")
S3_BUCKET_NAME = env.str("S3_BUCKET_NAME", default="products")
S3_ADDRESSING_STYLE = env.str("S3_ADDRESSING_STYLE", default="path")
S3_PRESIGNED_EXPIRE_SECONDS = env.int("S3_PRESIGNED_EXPIRE_SECONDS", default=120)
S3_USE_SSL = env.bool("S3_USE_SSL", default=False)
S3_CONNECT_TIMEOUT_SECONDS = env.int("S3_CONNECT_TIMEOUT_SECONDS", default=5)
S3_READ_TIMEOUT_SECONDS = env.int("S3_READ_TIMEOUT_SECONDS", default=30)
S3_MAX_POOL_CONNECTIONS = env.int("S3_MAX_POOL_CONNECTIONS", default=10)
S3_RETRY_MAX_ATTEMPTS = env.int("S3_RETRY_MAX_ATTEMPTS", default=3)
SITE_BASE_URL = env.str("SITE_BASE_URL", default="http://127.0.0.1:8000")
GUEST_ACCESS_EXPIRE_HOURS = env.int("GUEST_ACCESS_EXPIRE_HOURS", default=24)
GUEST_ACCESS_MAX_DOWNLOADS = env.int("GUEST_ACCESS_MAX_DOWNLOADS", default=3)
GUEST_ACCESS_EMAIL_OUTBOX_SENT_RETENTION_DAYS = env.int("GUEST_ACCESS_EMAIL_OUTBOX_SENT_RETENTION_DAYS", default=30)
GUEST_ACCESS_EMAIL_OUTBOX_FAILED_RETENTION_DAYS = env.int("GUEST_ACCESS_EMAIL_OUTBOX_FAILED_RETENTION_DAYS", default=90)
APP_DATA_ENCRYPTION_KEY = env.str("APP_DATA_ENCRYPTION_KEY")
SENTRY_DSN = env.str("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = env.str("SENTRY_ENVIRONMENT", default="development" if DEBUG else "production")
SENTRY_RELEASE = env.str("SENTRY_RELEASE", default="")
SENTRY_TRACES_SAMPLE_RATE = env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0)

# Static files (CSS, JavaScript, Images)

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "assets"),
    os.path.join(BASE_DIR, "static"),
]

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# Default primary key field type

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        # "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
}

# All Auth
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"  # подтверждение обязательно
ACCOUNT_UNIQUE_EMAIL = True

ACCOUNT_CHANGE_EMAIL = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_EMAIL_CONFIRMATION_ANONYMOUS_REDIRECT_URL = "/accounts/email-confirmed/"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_LOGIN_ON_PASSWORD_RESET = True

HEADLESS_SERVE_SPECIFICATION = True
HEADLESS_ONLY = True  # отключаем обычные account views

# Deep links for headless flows (used in emails and other redirects).
# We keep users on the home page and let frontend JS open the needed modal.
HEADLESS_FRONTEND_URLS = {
    "account_signup": "/?dialog=signup",
    "account_confirm_email": "/?dialog=confirm-email&key={key}",
    "account_reset_password": "/?dialog=password-reset",
    "account_reset_password_from_key": "/?dialog=password-reset&key={key}",
    "socialaccount_login_error": "/?dialog=login&social_error=1",
}

LOGIN_REDIRECT_URL = "/"
# ACCOUNT_LOGOUT_REDIRECT_URL = "/"

SITE_ID = 1

# Dev Email
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "localhost"  # или 'mailhog' при использовании Docker
EMAIL_PORT = 25
DEFAULT_FROM_EMAIL = "noreply@palingames.by"

TAILWIND_CLI_SRC_CSS = "assets/css/input.css"
TAILWIND_CLI_DIST_CSS = "css/tailwind.css"
TAILWIND_CLI_USE_MINIFY = False  # в dev удобнее
TAILWIND_CLI_VERSION = "4.1.18"
TAILWIND_CLI_AUTOMATIC_DOWNLOAD = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "logging_context": {
            "()": "apps.core.logging.LoggingContextFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "apps.core.logging.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["logging_context"],
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env.str("DJANGO_LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": env.str("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}

# RATE LIMITS
CHECKOUT_CREATE_EMAIL_RATE_LIMIT = env.int("CHECKOUT_CREATE_EMAIL_RATE_LIMIT", default=5)
CHECKOUT_CREATE_EMAIL_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "CHECKOUT_CREATE_EMAIL_RATE_LIMIT_WINDOW_SECONDS",
    default=600,
)

CHECKOUT_CREATE_IP_RATE_LIMIT = env.int("CHECKOUT_CREATE_IP_RATE_LIMIT", default=20)
CHECKOUT_CREATE_IP_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "CHECKOUT_CREATE_IP_RATE_LIMIT_WINDOW_SECONDS",
    default=3600,
)

CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT = env.int("CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT", default=20)
CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT_WINDOW_SECONDS",
    default=600,
)

CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT = env.int("CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT", default=30)
CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT_WINDOW_SECONDS",
    default=600,
)

AUTH_LOGIN_EMAIL_RATE_LIMIT = env.int("AUTH_LOGIN_EMAIL_RATE_LIMIT", default=5)
AUTH_LOGIN_EMAIL_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "AUTH_LOGIN_EMAIL_RATE_LIMIT_WINDOW_SECONDS",
    default=600,
)

AUTH_LOGIN_IP_RATE_LIMIT = env.int("AUTH_LOGIN_IP_RATE_LIMIT", default=30)
AUTH_LOGIN_IP_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "AUTH_LOGIN_IP_RATE_LIMIT_WINDOW_SECONDS",
    default=600,
)

AUTH_SIGNUP_EMAIL_RATE_LIMIT = env.int("AUTH_SIGNUP_EMAIL_RATE_LIMIT", default=3)
AUTH_SIGNUP_EMAIL_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "AUTH_SIGNUP_EMAIL_RATE_LIMIT_WINDOW_SECONDS",
    default=3600,
)

AUTH_SIGNUP_IP_RATE_LIMIT = env.int("AUTH_SIGNUP_IP_RATE_LIMIT", default=10)
AUTH_SIGNUP_IP_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "AUTH_SIGNUP_IP_RATE_LIMIT_WINDOW_SECONDS",
    default=3600,
)

AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT = env.int("AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT", default=3)
AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT_WINDOW_SECONDS",
    default=3600,
)

AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT = env.int("AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT", default=10)
AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT_WINDOW_SECONDS",
    default=3600,
)
