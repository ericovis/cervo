"""The generic job queue: claiming, retrying, timing out."""

from cervo import job
from cervo.db import connect


def enqueued(kind: str = "probe", **payload) -> job.Job:
    with connect() as conn:
        return job.enqueue(conn, kind, payload)


def backdate(column: str) -> None:
    """Move a scheduling timestamp into the past, the way waiting would."""
    with connect() as conn:
        conn.execute(f"UPDATE job SET {column} = 0 WHERE {column} IS NOT NULL")


def test_a_new_job_is_pending_with_no_attempts():
    queued = enqueued(slug="a")
    assert queued.status == "pending"
    assert queued.attempts == 0
    assert queued.error is None
    assert queued.payload == {"slug": "a"}


def test_claiming_takes_the_oldest_due_job_and_marks_it_running():
    first = enqueued(slug="first")
    enqueued(slug="second")

    with connect() as conn:
        claimed = job.claim_due(conn)

    assert claimed.id == first.id
    assert claimed.status == "running"


def test_a_running_job_cannot_be_claimed_again():
    enqueued()
    with connect() as conn:
        assert job.claim_due(conn) is not None
        assert job.claim_due(conn) is None


def test_claiming_stamps_the_deadline_from_the_job_timeout():
    with connect() as conn:
        queued = job.enqueue(conn, "probe", {}, timeout=123)
        job.claim_due(conn)
        row = conn.execute(
            "SELECT times_out_at, created_at FROM job WHERE id = ?", (queued.id,)
        ).fetchone()

    assert row["times_out_at"] >= row["created_at"] + 123


def test_succeeding_marks_the_job_done():
    enqueued()
    with connect() as conn:
        claimed = job.claim_due(conn)
        done = job.succeed(conn, claimed.id)

    assert done.status == "done"
    assert done.error is None


def test_a_failed_job_waits_before_it_is_due_again():
    enqueued()
    with connect() as conn:
        claimed = job.claim_due(conn)
        failed = job.fail(conn, claimed.id, "boom")

    assert failed.status == "pending"
    assert failed.attempts == 1
    assert failed.error == "boom"

    with connect() as conn:
        assert job.claim_due(conn) is None  # the retry delay has not passed

    backdate("next_attempt_at")
    with connect() as conn:
        assert job.claim_due(conn).id == claimed.id


def test_a_job_out_of_attempts_fails_for_good():
    queued = enqueued()

    for _ in range(10):
        backdate("next_attempt_at")
        with connect() as conn:
            claimed = job.claim_due(conn)
            if claimed is None:
                break
            last = job.fail(conn, claimed.id, "boom")

    assert last.status == "failed"
    assert last.id == queued.id
    with connect() as conn:
        assert job.claim_due(conn) is None


def test_reaping_reclaims_a_job_whose_deadline_passed():
    enqueued()
    with connect() as conn:
        claimed = job.claim_due(conn)
        assert job.reap(conn) == 0  # still within its timeout

    backdate("times_out_at")
    with connect() as conn:
        assert job.reap(conn) == 1
        row = conn.execute("SELECT * FROM job WHERE id = ?", (claimed.id,)).fetchone()

    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["error"] == "timed out"


def test_reaping_respects_the_attempt_limit():
    enqueued()

    for _ in range(10):
        backdate("next_attempt_at")
        with connect() as conn:
            if job.claim_due(conn) is None:
                break
        backdate("times_out_at")
        with connect() as conn:
            job.reap(conn)

    with connect() as conn:
        row = conn.execute("SELECT * FROM job").fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "timed out"


def test_latest_matches_the_exact_kind_and_payload():
    old = enqueued(slug="a")
    other = enqueued(slug="b")  # noqa: F841 — must not be matched below
    new = enqueued(slug="a")

    with connect() as conn:
        found = job.latest_of(conn, ("probe",), {"slug": "a"})
        missing = job.latest_of(conn, ("probe",), {"slug": "c"})

    assert found.id == new.id > old.id
    assert missing is None


def test_a_serialized_kind_waits_while_one_of_it_runs():
    job.serialize("tests.solo")
    with connect() as conn:
        first = job.enqueue(conn, "tests.solo", {"n": 1})
        job.enqueue(conn, "tests.solo", {"n": 2})
        job.enqueue(conn, "tests.bystander", {})

    with connect() as conn:
        running = job.claim_due(conn)
    assert running is not None and running.id == first.id

    # The second solo job is due but not claimable; other kinds keep flowing.
    with connect() as conn:
        claimed = job.claim_due(conn)
    assert claimed is not None and claimed.kind == "tests.bystander"
    with connect() as conn:
        assert job.claim_due(conn) is None


def test_a_serialized_kind_flows_again_once_the_runner_settles():
    job.serialize("tests.turn-taker")
    with connect() as conn:
        job.enqueue(conn, "tests.turn-taker", {"n": 1})
        job.enqueue(conn, "tests.turn-taker", {"n": 2})

    with connect() as conn:
        running = job.claim_due(conn)
        assert running is not None
        job.succeed(conn, running.id)

    with connect() as conn:
        claimed = job.claim_due(conn)
    assert claimed is not None and claimed.payload == {"n": 2}
