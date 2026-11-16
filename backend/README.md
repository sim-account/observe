# Backend

The backend uses FastAPI and stores sessions in memory for two hours. To start both the backend and frontend, run ./start.sh from the repository root.

To run the backend on its own, run this command from the backend directory:

    uv run --locked uvicorn main:app --host 127.0.0.1 --port 8000

To run the backend tests, use this command from the repository root:

    uv run --project backend --locked --group evaluation python -m unittest discover -s backend/tests

The application and both numerical experiments use this backend. See [model behavior](IMPLEMENTATION.md) for its assumptions and supported scenario changes.
