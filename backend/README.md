# Backend

The FastAPI application uses an in-memory session store with a two-hour TTL. Run from this directory with uv run --locked uvicorn main:app --host 127.0.0.1 --port 8000, or use the root start.sh to start both servers.

The evaluation environment is installed with uv sync --locked --group evaluation. From the repository root, run:

    uv run --project backend --locked --group evaluation python -m unittest discover -s backend/tests

Both numerical evaluations use this backend. See [implementation notes](IMPLEMENTATION.md) for discovery and scenario semantics.
