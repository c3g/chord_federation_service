import bento_federation_service
import tornado.gen
import tornado.ioloop
import tornado.web

from datetime import datetime
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_unix_socket
from tornado.web import RequestHandler, url

from .constants import (
    BENTO_FEDERATION_MODE,
    SERVICE_ID,
    SERVICE_TYPE,
    SERVICE_NAME,
    INITIALIZE_IMMEDIATELY,
    CHORD_URLS_SET,
    BASE_PATH,
    SERVICE_SOCKET,
)
from .db import peer_db
from .peers.handlers import PeerHandler, PeerRefreshHandler
from .peers.manager import PeerManager
from .search.dataset_search.handlers.datasets import DatasetsSearchHandler
from .search.dataset_search.handlers.private_dataset import PrivateDatasetSearchHandler
from .search.federated_dataset_search import FederatedDatasetsSearchHandler
from .search.search import SearchHandler


# noinspection PyAbstractClass,PyAttributeOutsideInit
class ServiceInfoHandler(RequestHandler):
    async def get(self):
        # Spec: https://github.com/ga4gh-discovery/ga4gh-service-info
        self.write({
            "id": SERVICE_ID,
            "name": SERVICE_NAME,  # TODO: Should be globally unique?
            "type": SERVICE_TYPE,
            "description": "Federation service for a Bento platform node.",
            "organization": {
                "name": "C3G",
                "url": "http://www.computationalgenomics.ca"
            },
            "contactUrl": "mailto:david.lougheed@mail.mcgill.ca",
            "version": bento_federation_service.__version__
        })


async def post_start_hook(peer_manager: PeerManager):
    if BENTO_FEDERATION_MODE:
        await peer_manager.get_peers()
    print(f"[{SERVICE_NAME} {datetime.utcnow()}] Post-start hook finished", flush=True)


# noinspection PyAbstractClass,PyAttributeOutsideInit
class PostStartHookHandler(RequestHandler):
    def initialize(self, peer_manager):
        self.peer_manager = peer_manager

    async def get(self):
        """
        Handles post-start hook which pings the node registry with the current node's information.
        :return:
        """
        print(f"[{SERVICE_NAME} {datetime.utcnow()}] Post-start hook invoked via URL request", flush=True)
        await post_start_hook(self.peer_manager)
        self.clear()
        self.set_status(204)


class Application(tornado.web.Application):
    def __init__(self, db, base_path: str):
        self.db = db
        self.peer_manager = PeerManager(self.db)

        args_pm = dict(peer_manager=self.peer_manager)
        args_full = dict(db=db, peer_manager=self.peer_manager)

        super().__init__([
            url(f"{base_path}/service-info", ServiceInfoHandler),
            url(f"{base_path}/private/post-start-hook", PostStartHookHandler, args_pm),
            url(f"{base_path}/dataset-search", DatasetsSearchHandler),
            url(f"{base_path}/private/dataset-search/([a-zA-Z0-9\\-_]+)", PrivateDatasetSearchHandler),
        ] + ([
            # TODO: Maybe these should be their own service
            #  If the services were split apart, the FEDERATION_MODE flag could
            #  be traded out for instead just not including the federation
            #  service, on the backend side?
            url(f"{base_path}/peers", PeerHandler, args_full),
            url(f"{base_path}/private/peers/refresh", PeerRefreshHandler, args_pm),
            url(f"{base_path}/federated-dataset-search", FederatedDatasetsSearchHandler, args_pm),
            url(f"{base_path}/search-aggregate/([a-zA-Z0-9\\-_/]+)", SearchHandler, args_pm),
        ] if BENTO_FEDERATION_MODE else []))

        if INITIALIZE_IMMEDIATELY:
            tornado.ioloop.IOLoop.current().spawn_callback(post_start_hook, self.peer_manager)


application = Application(peer_db, BASE_PATH)


def run():  # pragma: no cover
    if not CHORD_URLS_SET:
        print(f"[{SERVICE_NAME} {datetime.utcnow()}] No CHORD URLs given, terminating...")
        exit(1)

    server = HTTPServer(application)
    server.add_socket(bind_unix_socket(SERVICE_SOCKET))
    tornado.ioloop.IOLoop.current().start()
