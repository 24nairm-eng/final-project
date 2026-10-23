# One container = website + API + live engine.
# 1) build the website
FROM node:20-alpine AS web
WORKDIR /web
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ .
RUN npm run build

# 2) the Python server, which also serves the built website
FROM python:3.12-slim
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=web /web/dist /srv/dashboard/dist
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
