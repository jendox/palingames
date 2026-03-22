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
    "apps.core.apps.CoreConfig",
    "apps.users.apps.UsersConfig",
    "apps.pages.apps.PagesConfig",
    "apps.products.apps.ProductsConfig",
    "apps.cart.apps.CartConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.payments.apps.PaymentsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RequestContextLoggingMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
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
