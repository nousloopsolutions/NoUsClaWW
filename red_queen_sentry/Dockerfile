FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir numpy==1.26.4 scikit-learn==1.4.2 pydantic==2.7.1
COPY . /app/red_queen_sentry
RUN useradd -u 10001 sentryuser && chown -R sentryuser:sentryuser /app
USER sentryuser
VOLUME ["/tmp"]
ENTRYPOINT ["python", "/app/red_queen_sentry/mcts_evaluator.py"]
