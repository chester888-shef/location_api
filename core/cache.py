import json
import logging
from decimal import Decimal
from hashlib import sha256

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone
from redis.exceptions import RedisError

from core.models import CacheRevision

logger = logging.getLogger(__name__)

LOCATION_LIST_TTL = 300


def location_list_cache_key(validated_params, page):
    params = {
        name: format(value.normalize(), "f") if isinstance(value, Decimal) else value
        for name, value in validated_params.items()
    }
    params["page"] = page
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    revision = CacheRevision.objects.get(name="locations").version
    digest = sha256(canonical.encode()).hexdigest()
    return f"loc:list:v{revision}:{digest}"


def get_location_list_cache(key):
    try:
        return cache.get(key)
    except (RedisError, OSError) as error:
        logger.warning("Redis location list read failed (%s).", type(error).__name__)
        return None


def cache_location_list(key, data):
    try:
        cache.set(key, data, timeout=LOCATION_LIST_TTL)
    except (RedisError, OSError) as error:
        logger.warning("Redis location list write failed (%s).", type(error).__name__)


def bump_location_revision():
    """Call inside the transaction that changes location list data."""
    revision, _ = CacheRevision.objects.get_or_create(name="locations")
    CacheRevision.objects.filter(pk=revision.pk).update(version=F("version") + 1)


def view_hint_exists(location_id, visitor_hash):
    try:
        expires_at = cache.get(f"view:{location_id}:{visitor_hash}")
    except (RedisError, OSError) as error:
        logger.warning("Redis view hint read failed (%s).", type(error).__name__)
        return False
    now = timezone.now().timestamp()
    return (
        isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and now < expires_at <= now + 3600
    )


def remember_view_hint(location_id, visitor_hash, expires_at):
    remaining = int((expires_at - timezone.now()).total_seconds())
    if remaining <= 0:
        return
    try:
        cache.set(
            f"view:{location_id}:{visitor_hash}", expires_at.timestamp(),
            timeout=min(remaining, 3600),
        )
    except (RedisError, OSError) as error:
        logger.warning("Redis view hint write failed (%s).", type(error).__name__)
