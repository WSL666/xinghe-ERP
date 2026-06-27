# Product Pipeline Digital App FastAPI

FastAPI rebuild of the original local Flask prototype.

## Stack

- FastAPI backend
- PostgreSQL + pgvector
- Redis reserved for the next worker-queue step
- Aliyun OSS for original and generated images
- Existing static frontend reused

## Local Start

1. Start infrastructure:

```powershell
docker compose up -d
```

2. Create backend env:

```powershell
copy backend\.env.example backend\.env
```

Fill the AI and OSS keys in `backend\.env`.

3. Install dependencies:

```powershell
cd backend
pip install -r requirements.txt
```

4. Run FastAPI:

```powershell
uvicorn main:app --host 0.0.0.0 --port 6688 --reload
```

Open:

- Landing page: `http://localhost:6688/`
- Dashboard: `http://localhost:6688/dashboard`
- API docs: `http://localhost:6688/docs`

## Auth

Development mode creates a default account:

- account: `admin`
- password: `123456`

You can also log in with a new account from the dashboard. If login fails, the UI offers to register it directly. SMS/email verification is intentionally stubbed for now.
