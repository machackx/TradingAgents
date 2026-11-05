"""TradingAgents in a browser: a local server that runs analyses and streams them.

``webapp.server.create_app`` builds the FastAPI app; ``python -m webapp`` (or
``tradingagents-web``) serves it on this machine only. A run's progress reaches
the page as the events of ``tradingagents.graph.events``, over Server-Sent
Events, and every run is kept on disk for replay.
"""

# The API has no sign-in, so it serves and answers only on these names.
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")
