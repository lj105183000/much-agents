"""Shared Celery task/queue names (leaf module: no imports to avoid cycles)."""

PARSE_TASK_NAME = "much_agents.parse_document"
PARSE_QUEUE = "parse"

BID_TASK_NAME = "bidding.write_bid"
# Production separates parse and bid workers so long bid runs cannot starve
# uploads. The Windows development launcher consumes both queues with one
# solo-pool worker.
BID_QUEUE = "bid"
