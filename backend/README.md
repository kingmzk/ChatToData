# Backend - Talk2Data Analytics Engine

The backend core modules live directly inside `backend/`.

### Subsystems Overview
- **`api/`**: FastAPI REST server, routes, middleware, and CORS configuration.
- **`graph/`**: Target Architecture LangGraph bounded state workflow (`workflow.py`, `state.py`, `nodes.py`).
- **`catalog/`**: Semantic catalog schema, dimensions, measures, scenario models, and store.
- **`compiler/`**: Read-only parameterized SQL transpiler and AST generator.
- **`execution/`**: GenericSourceGateway and Decimal-exact analysis kernel.
- **`science/`**: Governed decision science engines (Forecast with holdouts, MAD Anomaly, Scenario simulation, and Bounded Goal Seek).
- **`jobs/`**: Durable job worker, lease fencing, and background daemon.
- **`security/`**: Tenant isolation, access control, and disclosure suppression.
- **`storage/`**: PersistentApplicationStore with SHA-256 evidence sealing and audit chain.
- **`config.py`**: Runtime configuration.
- **`demo.py`**: Demo SQLite dataset generator.
- **`runtime.py`**: Unified analytics engine runtime.

### How to Run
From the repository root:
```powershell
# Using the single-command launcher
python run.py

# Or 1-click batch / powershell:
.\start.ps1
```
