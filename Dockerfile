FROM python:3.11-slim

WORKDIR /app

# Installed from pyproject.toml, not from a second hand-maintained list. The
# two had drifted: google-cloud-aiplatform was a declared dependency and was
# never in the image, so the managed Memory Bank fell back to local in
# production while every local test passed.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY . .

EXPOSE 8080

ENV PORT=8080
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE
ENV EVENT_BUS_BACKEND=pubsub
ENV PUBSUB_TOPIC_EVENTS=crisismesh-events
ENV ARMOR_BACKEND=model_armor
ENV ARMOR_TEMPLATE=crisismesh-guard

CMD ["python", "-m", "src.core.server"]
