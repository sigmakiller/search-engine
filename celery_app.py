from celery import Celery
import config

app = Celery(
    "search_engine",
    broker=config.CELERY_BROKER_URL,
    include=["indexer"]
)

app.conf.update(
    result_expires=3600,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

if __name__ == "__main__":
    app.start()
