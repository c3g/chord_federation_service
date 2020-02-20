import os
import urllib.parse

from . import __version__


__all__ = [
    "BASE_PATH",
    "CHORD_DEBUG",
    "CHORD_URL",
    "CHORD_HOST",
    "CHORD_REGISTRY_URL",

    "OIDC_DISCOVERY_URI",

    "DB_PATH",

    "SERVICE_ORGANIZATION",
    "SERVICE_ARTIFACT",
    "SERVICE_TYPE",
    "SERVICE_ID",

    "SERVICE_SOCKET",

    "SOCKET_INTERNAL",
    "SOCKET_INTERNAL_DOMAIN",

    "CHORD_URLS_SET",

    "TIMEOUT",
    "WORKERS",
    "LAST_ERRORED_CACHE_TIME",
]


BASE_PATH = os.environ.get("SERVICE_URL_BASE_PATH", "")
CHORD_DEBUG = os.environ.get("CHORD_DEBUG", "false").lower() == "true"
CHORD_URL = os.environ.get("CHORD_URL", "")
CHORD_HOST = os.environ.get("CHORD_HOST", urllib.parse.urlparse(CHORD_URL).netloc if CHORD_URL != "" else "")
CHORD_REGISTRY_URL = os.environ.get("CHORD_REGISTRY_URL", "")  # "http://1.chord.dlougheed.com/"
OIDC_DISCOVERY_URI = os.environ.get("OIDC_DISCOVERY_URI", None)
DB_PATH = os.path.join(os.getcwd(), os.environ.get("DATABASE", "data/federation.db"))

SERVICE_ORGANIZATION = "ca.c3g.chord"
SERVICE_ARTIFACT = "federation"
SERVICE_TYPE = f"{SERVICE_ORGANIZATION}:{SERVICE_ARTIFACT}:{__version__}"
SERVICE_ID = os.environ.get("SERVICE_ID", SERVICE_TYPE)

SERVICE_SOCKET = os.environ.get("SERVICE_SOCKET", "/tmp/federation.sock")

SOCKET_INTERNAL = os.environ.get("SOCKET_INTERNAL", "/var/run/nginx.sock")  # internal reverse proxy socket
SOCKET_INTERNAL_DOMAIN = "nginx_internal"

CHORD_URLS_SET = CHORD_URL.strip() != "" and CHORD_REGISTRY_URL.strip() != ""

TIMEOUT = 120
WORKERS = 10
LAST_ERRORED_CACHE_TIME = 30