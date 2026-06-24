"""End-to-end scenario tests (harness note 11) — full docker-compose stack.

The Level-3 layer: crash recovery (docker kill), poison-pill -> DLQ, duplicate delivery,
global TTS semaphore, content cache, and the full manuscript -> COMPLETED happy path.
Each maps to a feature_list.json verification (R3.x / R4.x). Written per rung as the
pipeline is implemented; marked `@pytest.mark.e2e` so they auto-skip without Docker.
"""
