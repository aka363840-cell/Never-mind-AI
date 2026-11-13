# Deploying on Railway

1. Push this folder to a GitHub repo (`.env` is git-ignored — never commit it).
2. Railway → New Project → Deploy from GitHub repo. It builds the `Dockerfile` automatically (`railway.json` pins this and 1 replica).
3. **Add a Volume** to the service, mount path `/data`. (Without it your game data is lost on every deploy.)
4. **Variables** tab — add (copy from `.env.example`):
   - `BOT_TOKEN`, `OWNER_ID`
   - `GEMINI_API_KEYS` (and optionally `GROQ_API_KEYS`, `GROQ_MODELS`, `CLOUDFLARE_API_KEYS`, `CLOUDFLARE_ACCOUNT_ID`, `AI_PROVIDERS`, `AI_PROVIDER`)
   - Leave `DATABASE_URL` unset to use SQLite at `/data/bot.db` (default), or reference a Railway Postgres `DATABASE_URL`.
5. Deploy. Logs should show `Started as ... (@yourbot)`. Send `/start` on Telegram.

Notes: keep replicas at 1 (long-polling). No domain/port or HTTP healthcheck is needed.
