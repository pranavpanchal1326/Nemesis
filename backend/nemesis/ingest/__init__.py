"""Submission intake: bytes off the wire, one event into the log.

``media`` turns an upload into a verified, content-addressed file under
quarantine. ``service`` turns a validated submission into a
``complaint_submitted`` event, its projection, and its outbox row — in one
transaction — and hands the complaint to the pipeline once that transaction has
committed.
"""

from __future__ import annotations
