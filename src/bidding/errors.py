"""Typed bidding errors and CLI exit codes."""

from __future__ import annotations

BID_EXIT_OK = 0
BID_EXIT_INTERNAL = 1
BID_EXIT_INPUT = 2
BID_EXIT_GENERATION = 3
BID_EXIT_EXPORT = 4


class BidError(Exception):
    exit_code = BID_EXIT_INTERNAL


class BidInputError(BidError):
    exit_code = BID_EXIT_INPUT


class BidContextError(BidInputError):
    pass


class BidLLMError(BidError):
    exit_code = BID_EXIT_GENERATION


class BidGenerationError(BidError):
    exit_code = BID_EXIT_GENERATION


class BidExportError(BidError):
    exit_code = BID_EXIT_EXPORT
