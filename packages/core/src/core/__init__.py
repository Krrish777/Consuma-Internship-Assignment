"""core — shared library for the Consuma audio engine.

Split (spec §5):
  domain/  pure logic, no I/O — unit-testable without Docker
  infra/   adapters to external systems (Postgres, RabbitMQ, Redis, MinIO)
"""
