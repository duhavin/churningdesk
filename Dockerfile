# WEwards — single container: FastAPI serves /api + built SPA.
# Built for subpath hosting at /wewards (see toolbench/hosting/finance/).

FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_BASE_PATH=/wewards/
RUN node build.mjs

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Chromium for crawl4ai offer scraping (--with-deps pulls the system libs).
RUN playwright install --with-deps chromium
COPY backend/ ./backend/
COPY --from=web /web/dist ./frontend/dist
# CWD must stay /app: DATABASE_URL default is sqlite:///data/wewards.db
# (relative) and the backend mounts ../frontend/dist.
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
