"""Deployment feature switches shared by HTTP and background jobs."""
import os


def search_only():
    return os.environ.get("SUPPLY_MATCH_NO_AI", "0") == "1"
