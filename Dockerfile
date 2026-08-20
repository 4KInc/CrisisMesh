FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir google-adk google-cloud-pubsub google-cloud-firestore pydantic python-dotenv

COPY . .

EXPOSE 8080

ENV PORT=8080
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE
ENV EVENT_BUS_BACKEND=pubsub
ENV PUBSUB_TOPIC_EVENTS=crisismesh-events

CMD ["python", "-m", "src.core.server"]
