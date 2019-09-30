import chord_federation
import json
import os
import sqlite3
import tornado.gen
import tornado.ioloop
import tornado.web

from datetime import datetime, timedelta
from itertools import chain
from tornado.httpclient import AsyncHTTPClient
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_unix_socket
from tornado.queues import Queue
from tornado.web import RequestHandler, url

CHORD_URL = os.environ.get("CHORD_URL")
CHORD_REGISTRY_URL = os.environ.get("CHORD_REGISTRY_URL", "http://127.0.0.1:5000/")  # "http://1.chord.dlougheed.com/"
TIMEOUT = 45
WORKERS = 10
LAST_ERRORED_CACHE_TIME = 30

db_path = os.path.join(os.getcwd(), os.environ.get("DATABASE", "data/federation.db"))

db_exists = os.path.exists(db_path)
peer_db = sqlite3.connect(os.environ.get("DATABASE", "data/federation.db"), detect_types=sqlite3.PARSE_DECLTYPES)
peer_db.row_factory = sqlite3.Row


def init_db():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"), "r") as sf:
        peer_db.executescript(sf.read())

    c = peer_db.cursor()
    c.execute("INSERT OR IGNORE INTO peers VALUES(?)", (CHORD_URL,))
    c.execute("INSERT OR IGNORE INTO peers VALUES(?)", (CHORD_REGISTRY_URL,))

    peer_db.commit()


def update_db():
    c = peer_db.cursor()

    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='peers'")
    if c.fetchone() is None:
        init_db()
        return

    # TODO


if not db_exists:
    init_db()
else:
    update_db()


# noinspection PyAbstractClass,PyAttributeOutsideInit
class ServiceInfoHandler(RequestHandler):
    async def get(self):
        # Spec: https://github.com/ga4gh-discovery/ga4gh-service-info

        if self.get_argument("update_peers", "true") == "true":
            # Hack to force lists to update when the CHORD dashboard is loaded
            c = self.application.db.cursor()
            await self.application.get_peers(c)
            self.application.db.commit()

        self.write({
            "id": "ca.distributedgenomics.chord_federation",  # TODO: Should be globally unique
            "name": "CHORD Federation",  # TODO: Should be globally unique
            "type": "ca.distributedgenomics:chord_federation:{}".format(chord_federation.__version__),  # TODO
            "description": "Federation service for a CHORD application.",
            "organization": {
                "name": "GenAP",
                "url": "https://genap.ca/"
            },
            "contactUrl": "mailto:david.lougheed@mail.mcgill.ca",
            "version": chord_federation.__version__
        })


# noinspection PyAbstractClass,PyAttributeOutsideInit
class PeerHandler(RequestHandler):
    async def get(self):
        c = self.application.db.cursor()
        peers = await self.application.get_peers(c)

        # Commit any changes triggered by the get
        self.application.db.commit()

        self.write({"peers": list(peers), "last_updated": self.application.last_peers_update.timestamp()})

    async def post(self):
        """
        Handle notifies from other nodes.
        """

        c = self.application.db.cursor()
        new_pci = self.application.peer_cache_invalidated

        try:
            # Test that the peer's peers can be seen and are providing the correct service type.

            request_data = json.loads(self.request.body)

            peer_self = request_data["self"]
            peer_peers = json.loads(self.request.body)["peers"]
            attempted_contact = {CHORD_URL}

            if peer_self in self.application.notifying:
                # Another request is already being processed from the same node. Assume the data is the same...
                # TODO: Is this a valid assumption?

                self.clear()
                self.set_status(200)

                return

            self.application.notifying.add(peer_self)

            client = AsyncHTTPClient()

            for peer_url in peer_peers:
                if peer_url in attempted_contact:
                    continue

                if (peer_url in self.application.last_errored and
                        datetime.now().timestamp() - self.application.last_errored[peer_url] < LAST_ERRORED_CACHE_TIME):
                    # Avoid repetitively hitting dead nodes
                    continue

                try:
                    r = await client.fetch(f"{peer_url}api/federation/service-info?update_peers=false",
                                           request_timeout=TIMEOUT)

                    # TODO: Check semver for compatibility
                    if "ca.distributedgenomics:chord_federation" in json.loads(r.body)["type"]:
                        # Peer two-way communication is possible
                        c.execute("SELECT 1 FROM peers WHERE url = ?", (peer_url,))
                        new_pci = new_pci or c.fetchone() is None
                        c.execute("INSERT OR IGNORE INTO peers VALUES(?)", (peer_url,))
                        self.application.db.commit()

                except Exception as e:
                    # TODO: Better / more compliant error message, don't return early
                    self.application.last_errored[peer_url] = datetime.now().timestamp()
                    print("[CHORD Federation {}] Error when processing notify from peer {}.\n"
                          "    Error: {}".format(datetime.now(), peer_url, str(e)), flush=True)

                finally:
                    attempted_contact.add(peer_url)

            self.application.notifying.remove(peer_self)

            self.application.peer_cache_invalidated = new_pci
            self.clear()
            self.set_status(200)

        except IndexError:
            # TODO: Better / more compliant error message
            self.clear()
            self.set_status(400)


# noinspection PyAbstractClass,PyAttributeOutsideInit
class SearchHandler(RequestHandler):
    async def search_worker(self, peer_queue, search_path, responses):
        client = AsyncHTTPClient()

        async for peer in peer_queue:
            if peer is None:
                # Exit signal
                return

            try:
                r = await client.fetch(f"{peer}api/{search_path}", request_timeout=TIMEOUT, method="POST",
                                       body=self.request.body, headers={"Content-Type": "application/json"},
                                       raise_error=True)
                responses.append(json.loads(r.body))

            except Exception as e:
                # TODO: Less broad of an exception
                responses.append(None)
                print("[CHORD Federation {}] Connection issue or timeout with peer {}.\n"
                      "    Error: {}".format(datetime.now(), peer, str(e)), flush=True)

            finally:
                peer_queue.task_done()

    async def post(self, search_path):
        # TODO: NO SPEC FOR THIS YET SO I JUST MADE SOME STUFF UP

        c = self.application.db.cursor()
        peers = await self.application.get_peers(c)
        self.application.db.commit()

        peer_queue = Queue()
        for peer in peers:
            await peer_queue.put(peer)

        responses = []
        # noinspection PyTypeChecker
        workers = tornado.gen.multi([self.search_worker(peer_queue, search_path, responses) for _ in range(WORKERS)])
        await peer_queue.join()
        good_responses = [r for r in responses if r is not None]

        try:
            self.write({
                "results": list(chain.from_iterable((r["results"] for r in good_responses))),
                "peers": {"responded": len(good_responses), "total": len(responses)}
            })

        except IndexError:
            # TODO: Better / more compliant error message
            self.clear()
            self.set_status(400)

        # Trigger exit for all workers
        for _ in range(WORKERS):
            await peer_queue.put(None)

        await workers


class Application(tornado.web.Application):
    async def peer_worker(self, peers, peers_to_check, peers_to_check_set, attempted_contact, results):
        client = AsyncHTTPClient()

        async for peer in peers_to_check:
            if peer is None:
                # Exit signal
                return

            if (peer in self.last_errored and
                    datetime.now().timestamp() - self.last_errored[peer] < LAST_ERRORED_CACHE_TIME):
                # Avoid repetitively hitting dead nodes
                print("[CHORD Federation {}] Skipping dead peer {}".format(datetime.now(), peer), flush=True)
                peers_to_check_set.remove(peer)
                peers_to_check.task_done()
                continue

            if peer in attempted_contact:
                peers_to_check_set.remove(peer)
                peers_to_check.task_done()
                # if peers_to_check.qsize() == 0:
                #     return
                continue

            if peer in self.contacting:
                print("[CHORD Federation {}] Avoiding race on peer {}".format(datetime.now(), peer), flush=True)
                # TODO: Do we call task_done() here?
                continue

            self.contacting.add(peer)

            print("[CHORD Federation {}] Contacting peer {}".format(datetime.now(), peer), flush=True)

            peer_peers = []

            try:
                await client.fetch(
                    f"{peer}api/federation/peers",
                    request_timeout=TIMEOUT,
                    method="POST",
                    body=json.dumps({"peers": list(peers), "self": CHORD_URL}),
                    headers={"Content-Type": "application/json"},
                    raise_error=True
                )

                r = await client.fetch(f"{peer}api/federation/peers",
                                       method="GET",
                                       request_timeout=TIMEOUT)

                # If a non-200 response is encountered, an error is raised

                self.connected_to_peer_network = True
                peer_peers = json.loads(r.body)["peers"]

            except IndexError:
                print(f"[CHORD Federation] Error: Invalid 200 response returned by {peer}.", flush=True)

            except Exception as e:
                # TODO: Less generic error
                print("[CHORD Federation] Peer contact error for {}".format(peer), flush=True)
                self.last_errored[peer] = datetime.now().timestamp()
                print(peer, str(e), flush=True)

            peers = peers.union(peer_peers)
            new_peer = False

            for p in peer_peers:
                if p not in peers_to_check_set and p not in self.contacting and p not in attempted_contact:
                    new_peer = True
                    await peers_to_check.put(p)
                    peers_to_check_set.add(p)

            results.append(new_peer)

            attempted_contact.add(peer)
            self.contacting.remove(peer)

            peers_to_check_set.remove(peer)
            peers_to_check.task_done()

    async def get_peers(self, c):
        c.execute("SELECT url FROM peers")
        peers = set([p[0] for p in c.fetchall()])

        if (datetime.utcnow() - timedelta(hours=1) > self.last_peers_update or self.peer_cache_invalidated) \
                and not self.fetching_peers:
            self.fetching_peers = True
            self.last_peers_update = datetime.utcnow()

            # Peer queue
            peers_to_check = Queue()
            peers_to_check_set = set()
            for p in peers:
                await peers_to_check.put(p)
                peers_to_check_set.add(p)

            results = []
            attempted_contact = {CHORD_URL}

            # noinspection PyAsyncCall,PyTypeChecker
            workers = tornado.gen.multi([
                self.peer_worker(peers, peers_to_check, peers_to_check_set, attempted_contact, results)
                for _ in range(WORKERS)])

            # Wait for all peers to be processed
            await peers_to_check.join()

            self.peer_cache_invalidated = self.peer_cache_invalidated or (True in results)

            for peer in peers:
                c.execute("INSERT OR IGNORE INTO peers VALUES (?)", (peer,))

            self.fetching_peers = False

            # Trigger exit for all workers
            for _ in range(WORKERS):
                await peers_to_check.put(None)

            await workers

        return peers

    def __init__(self, db, base_url):
        self.db = db
        self.last_peers_update = datetime.utcfromtimestamp(0)
        self.peer_cache_invalidated = False
        self.connected_to_peer_network = False
        self.fetching_peers = False
        self.last_errored = {}
        self.contacting = set()
        self.notifying = set()

        handlers = [
            url(f"{base_url}/service-info", ServiceInfoHandler),
            url(f"{base_url}/peers", PeerHandler),
            url(f"{base_url}/search-aggregate/([a-zA-Z0-9\\-_/]+)", SearchHandler),
        ]

        super(Application, self).__init__(handlers)


application = Application(peer_db, os.environ.get("BASE_URL", ""))


def run():
    if CHORD_URL is None:
        print("[CHORD Federation] No CHORD URL given, terminating...")
        exit(1)

    server = HTTPServer(application)
    server.add_socket(bind_unix_socket(os.environ.get("SOCKET", "/tmp/federation.sock")))
    tornado.ioloop.IOLoop.instance().start()