"""Shared Celery task/queue names (leaf module: no imports to avoid cycles)."""

PARSE_TASK_NAME = "much_agents.parse_document"
PARSE_QUEUE = "parse"

BID_TASK_NAME = "much_agents.write_bid"
BID_QUEUE = PARSE_QUEUE
