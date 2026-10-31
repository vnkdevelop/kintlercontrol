import asyncio
import threading
from pathlib import Path

from server.storage import Storage


async def test_cancelled_write_finishes_before_close(tmp_path: Path):
    storage = Storage(tmp_path / "db.sqlite3")
    started = threading.Event()
    release = threading.Event()

    def operation():
        started.set()
        assert release.wait(timeout=5)
        storage.connection.execute("INSERT INTO nodes VALUES('node_a','{}',0)")
        storage.connection.commit()

    write = asyncio.create_task(storage.run(operation))
    await asyncio.to_thread(started.wait, 5)
    write.cancel()
    closing = asyncio.create_task(storage.close())
    await asyncio.sleep(0.05)
    assert not closing.done()
    release.set()
    results = await asyncio.gather(write, closing, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] is None
