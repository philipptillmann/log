FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 log && useradd --uid 10001 --gid log --no-create-home log && mkdir /data && chown log:log /data
COPY --chown=log:log server.py /app/server.py
COPY --chown=log:log static /app/static
USER log
EXPOSE 8080
CMD ["python", "server.py"]
