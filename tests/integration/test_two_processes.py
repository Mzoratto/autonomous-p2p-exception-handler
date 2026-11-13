import fcntl
import multiprocessing

from process_support import process_held

from p2p.application.service import CaseService, create_runtime
from p2p.infrastructure.database import demo_invoices

# Upper bound only: the reader releases the writer as soon as its checks are done. A read that
# waits for the writer's command lock gets it only after the writer has moved on.
HOLD_SECONDS = 30.0


def test_a_second_process_reads_while_the_first_runs_a_slow_case(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    runtime = create_runtime(database_url=url, demo_mode=True)
    reader = CaseService(runtime)
    finished = reader.process_invoice(demo_invoices()["01_exact_match"])["case_id"]

    spawn = multiprocessing.get_context("spawn")
    started, release, resumed = spawn.Event(), spawn.Event(), spawn.Event()
    results = spawn.Queue()
    writer = spawn.Process(
        target=process_held,
        args=(url, "03_large_price_variance", started, release, resumed, HOLD_SECONDS, results),
    )
    writer.start()
    try:
        assert started.wait(60), "the writer process never reached the model"

        # Each read's try asks for the file lock once, without waiting. A bounded wait
        # (blocking flock under a timer, or retries until a deadline) still returns before the
        # writer resumes; these records show it.
        repository = reader.runtime.repository
        flock, requests = fcntl.flock, []
        try_lock, tries = repository.try_command_lock, []

        def recording_flock(fd, operation):
            requests.append(operation)
            return flock(fd, operation)

        def counted_try_lock():
            tries.append(1)
            return try_lock()

        with monkeypatch.context() as patch:
            patch.setattr(fcntl, "flock", recording_flock)
            patch.setattr(repository, "try_command_lock", counted_try_lock)
            rows, total = reader.query_cases(limit=None)
            running = next(row["case_id"] for row in rows if row["case_id"] != finished)
            running_case = reader.get_case(running)
            finished_case = reader.get_case(finished)
            reader.refresh_open_cases()
        read_while_held = not resumed.is_set()
        still_running = writer.is_alive()
    finally:
        release.set()
        writer.join(60)
        if writer.is_alive():
            writer.kill()
        runtime.close()

    assert still_running, "the reads must happen while the writer is mid-run"
    assert total == 2
    assert read_while_held, "the reads waited for the writer's command lock"
    assert len(tries) >= 3, "get_case (twice) and refresh_open_cases each try the lock"
    assert requests == [fcntl.LOCK_EX | fcntl.LOCK_NB] * len(tries), "a read waited for the lock"
    assert (running_case["busy"], running_case["status"]) == (True, "PROCESSING")
    assert finished_case["status"] == "COMPLETED"
    assert writer.exitcode == 0
    assert results.get(timeout=5) == "PENDING_REVIEW"
