import multiprocessing
import time

from process_support import process_slowly

from p2p.application.service import CaseService, create_runtime
from p2p.infrastructure.database import demo_invoices

SLOW_SECONDS = 4.0


def timed(call):
    started = time.monotonic()
    result = call()
    return result, time.monotonic() - started


def test_a_second_process_reads_while_the_first_runs_a_slow_case(tmp_path):
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    runtime = create_runtime(database_url=url, demo_mode=True)
    reader = CaseService(runtime)
    finished = reader.process_invoice(demo_invoices()["01_exact_match"])["case_id"]

    spawn = multiprocessing.get_context("spawn")
    started, results = spawn.Event(), spawn.Queue()
    writer = spawn.Process(
        target=process_slowly,
        args=(url, "03_large_price_variance", started, SLOW_SECONDS, results),
    )
    writer.start()
    try:
        assert started.wait(60), "the writer process never reached the model"

        (rows, total), list_time = timed(lambda: reader.query_cases(limit=None))
        running = next(row["case_id"] for row in rows if row["case_id"] != finished)
        running_case, open_time = timed(lambda: reader.get_case(running))
        finished_case, other_time = timed(lambda: reader.get_case(finished))
        _, refresh_time = timed(reader.refresh_open_cases)
        still_running = writer.is_alive()

        writer.join(60)
    finally:
        if writer.is_alive():
            writer.kill()
        runtime.close()

    assert still_running, "the reads must happen while the writer is mid-run"
    assert total == 2
    assert max(list_time, open_time, other_time, refresh_time) < 1.0
    assert (running_case["busy"], running_case["status"]) == (True, "PROCESSING")
    assert finished_case["status"] == "COMPLETED"
    assert writer.exitcode == 0
    assert results.get(timeout=5) == "PENDING_REVIEW"
