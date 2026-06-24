"""Worker entrypoint — aio-pika consume loop (spec §5, §8).

STUB. Implemented across Rung 0.4 (consume q.parse) and Rung 3.1 (manual ack LAST,
prefetch, NACK -> retry path). Routes messages to handlers/{parse,tts,stitch}.

Run by compose as: python -m worker.main
"""
