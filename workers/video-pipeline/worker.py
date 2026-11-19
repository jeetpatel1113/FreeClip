"""Entry point: consumes the BullMQ queues produced by the API.

  video-pipeline : process-project, render-clip   (GPU/CPU heavy, low concurrency)
  publish        : publish-post                    (network bound)

Heavy work runs in worker threads so the asyncio loop keeps renewing BullMQ job locks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from bullmq import Worker

from pipeline import jobs
from pipeline.config import settings

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("worker")


async def pipeline_processor(job, _token):  # noqa: ANN001
    log.info("pipeline job %s (%s)", job.id, job.name)
    if job.name == "process-project":
        await asyncio.to_thread(jobs.process_project, job.data)
    elif job.name == "render-clip":
        await asyncio.to_thread(jobs.render_clip, job.data)
    else:
        log.warning("unknown pipeline job %s", job.name)


async def publish_processor(job, _token):  # noqa: ANN001
    log.info("publish job %s", job.id)
    if job.name == "publish-post":
        await asyncio.to_thread(jobs.publish_post, job.data)


def _check_env() -> None:
    missing = [k for k in ("INTERNAL_API_TOKEN", "GEMINI_API_KEY") if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")


async def main() -> None:
    _check_env()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    opts = {"connection": settings.redis_url, "lockDuration": 120_000}
    workers = [
        Worker("video-pipeline", pipeline_processor, {**opts, "concurrency": settings.pipeline_concurrency}),
        Worker("publish", publish_processor, {**opts, "concurrency": settings.publish_concurrency}),
    ]
    log.info("worker started (pipeline=%d, publish=%d)", settings.pipeline_concurrency, settings.publish_concurrency)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: stop.set())
    await stop.wait()
    log.info("shutting down")
    await asyncio.gather(*(w.close() for w in workers))


if __name__ == "__main__":
    asyncio.run(main())
