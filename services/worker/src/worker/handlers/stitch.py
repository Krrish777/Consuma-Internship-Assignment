"""STAGE D — Stitch + notify (spec §8, §9).

STUB. Rung 1.6 / 5.2 implement:
  handler: list_prefix(tts/<job>/), concat -> out/<job>.mp3, set COMPLETED.
  notify: POST callback_url (or log); webhook failure logs a warning but the job
          stays COMPLETED (Rung 5.2 edge).
"""
