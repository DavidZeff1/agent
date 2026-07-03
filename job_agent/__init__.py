"""job_agent — a minimal agent-and-tools system for finding and applying to jobs.

Public modules:
    config          paths / env / settings
    profile_store   the profile schema + persistence + interactive intake
    scraper         job-board search tools
    matching        deterministic + optional LLM job scoring
    generate        resume / cover-letter / application-packet generation
    tools           Tool + ToolRegistry (definitions and dispatch)
    agent           Groq tool-calling agent loop
    cli             command-line interface
"""

__version__ = "1.0.0"
