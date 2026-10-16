"""Eval harness for gemini2api (Gemini 4 Argon situations).

Everything here is test tooling: it is not imported by the application and is excluded from the
Docker image. Deterministic checks live in code (``sse``, ``checks``); LLM agents (``agents``) only
propose cases, judge semantic checks and propose patches, and their output is always untrusted data.
"""
