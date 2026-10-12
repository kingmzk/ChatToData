# Talk2Data — Decoupled React Frontend

Modern, responsive React application for Talk2Data conversational enterprise analytics.

## Tech Stack
- **Framework:** React 19 + Vite
- **Icons:** Lucide React
- **Styling:** Custom Vanilla CSS Design System (Midnight glassmorphism, responsive cards, micro-animations)
- **API Communication:** Built-in proxy to FastAPI (`/api -> http://127.0.0.1:8000`) and CORS-enabled

## Features
- **Conversational Analytics**: Multi-turn chat interface with live polling and status progression (`SUBMITTED`, `ANALYZING`, `ANSWERED`).
- **Interactive Clarification**: Dynamic choice prompts allowing users to clarify questions and resume execution seamlessly.
- **Decision Science Visualizations**:
  - Standard KPI Cards with metrics and deltas
  - Tabular results view
  - Bar and line charts with percentage progress tracks
  - Forecast visualizations with prediction intervals (`PI: [lower, upper]`)
  - Anomaly detection badges with baseline median
  - Scenario simulation outputs
- **Data Exporting**: Direct download links for formatted XLSX and CSV reports.
- **Dynamic Follow-ups**: Automated smart question suggestions.

## Running Locally

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Start the development server**:
   ```bash
   npm run dev
   ```
   Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Requests to `/api/...` are automatically proxied to the backend at `http://127.0.0.1:8000`.

3. **Build for production**:
   ```bash
   npm run build
   ```
   The build outputs to `frontend/dist`. The FastAPI backend automatically serves this production bundle when accessed at `http://127.0.0.1:8000/`.
