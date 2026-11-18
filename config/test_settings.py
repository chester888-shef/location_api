import os
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from redis import ConnectionPool

from config.settings import *  # noqa: F403

test_redis = urlsplit(TEST_REDIS_URL)  # noqa: F405
if test_redis.scheme not in {"redis", "rediss"}:
    raise ImproperlyConfigured("TEST_REDIS_URL must use redis:// or rediss://.")
test_redis_db = ConnectionPool.from_url(TEST_REDIS_URL).connection_kwargs.get("db", 0)  # noqa: F405
runtime_redis_db = ConnectionPool.from_url(REDIS_URL).connection_kwargs.get("db", 0)  # noqa: F405
if test_redis_db == runtime_redis_db:
    raise ImproperlyConfigured("Tests must use a separate Redis database.")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": TEST_REDIS_URL,  # noqa: F405
        "TIMEOUT": 300,
        "KEY_PREFIX": f"location_api:test:{os.getpid()}",
    }
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
