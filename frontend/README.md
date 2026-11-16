# Frontend

The frontend uses React and Vite and requires Node.js 20.19 or newer. Run ./start.sh from the repository root to start it with the backend.

To run the frontend separately, start the backend on port 8000, then run these commands from the frontend directory:

    npm ci
    npm run dev

Open http://localhost:5173. To create a production build, run:

    npm run build
