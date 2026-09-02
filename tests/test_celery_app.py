import ssl

from app.celery_app import _ssl_options


def test_ssl_options_none_for_plain_redis():
    assert _ssl_options("redis://localhost:6379/0") is None


def test_ssl_options_set_for_rediss_url():
    # Upstash (and most managed Redis providers) require TLS -- without
    # ssl_cert_reqs set explicitly, Celery/Kombu refuses a rediss:// URL
    # at startup with "A rediss:// URL must have parameter ssl_cert_reqs".
    opts = _ssl_options("rediss://default:pw@example.upstash.io:6379")
    assert opts == {"ssl_cert_reqs": ssl.CERT_REQUIRED}
