#!/usr/bin/env python3
"""Convenience launcher so you can use `python run.py <command>` instead of `python -m job_agent`."""
from job_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
