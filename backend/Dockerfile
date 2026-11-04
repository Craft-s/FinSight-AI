FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY app ./app
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home --uid 10001 finsight
USER finsight
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
