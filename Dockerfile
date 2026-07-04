# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Backend + serve built frontend
FROM python:3.12-slim AS final
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ ./app/
COPY --from=frontend-build /frontend/dist ./static/

ENV POWARR_DATA_DIR=/config
VOLUME ["/config"]
EXPOSE 7979

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7979"]
