"""Vercel/WSGI entrypoint. Locally you'd normally run `python -m job_agent web` instead.

On Vercel the app runs in "hosted mode" (detected via the VERCEL env var): the server is
stateless — each visitor's profile, API key, tracker, and prepared applications live in
their own browser — and machine-bound features (form autofill, background auto-search,
Chrome-made PDFs) are disabled with in-app explanations.
"""
from job_agent.web import create_app

app = create_app()
