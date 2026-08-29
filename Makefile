# ReadyPick, top-level task runner (spec-doc6 §3.2).
#
#   make test               backend suite against the containerised test stack
#   make test-integration   only the tests that touch real Postgres/Redis/S3
#   make test-all           backend + agent evals + frontend
#   make stack-up           start the test stack and leave it running
#   make stack-down         stop it and drop its volumes
#
# EVERY TARGET DELEGATES TO scripts/test.sh, AND THAT IS DELIBERATE.
# `make` is not present on every machine this project is developed on -- it is
# absent from the Git Bash environment on the Windows workstation this was
# written on -- so the recipes here must not be where the logic lives. The shell
# script is the implementation and is what CI and the documentation call; this
# file is the conventional entry point for anybody who does have make. A
# capability that exists only behind a tool half the team lacks is not a
# capability, so both are real and both are tested. See CONTRIBUTING.md.
SHELL := /usr/bin/env bash
COMPOSE := docker compose -f docker-compose.test.yml

.PHONY: test test-integration test-all stack-up stack-down help

help:
	@echo "make test               backend suite against the containerised test stack"
	@echo "make test-integration   only the tests that touch real Postgres/Redis/S3"
	@echo "make test-all           backend + agent evals + frontend"
	@echo "make stack-up           start the test stack and leave it running"
	@echo "make stack-down         stop it and drop its volumes"
	@echo ""
	@echo "No make on this machine? Every target is one line of shell:"
	@echo "  ./scripts/test.sh [unit|integration|all]"

test:
	./scripts/test.sh unit

test-integration:
	./scripts/test.sh integration

test-all:
	./scripts/test.sh all

stack-up:
	$(COMPOSE) up -d --wait

# `-v` on purpose. The Postgres data directory and the MinIO store are tmpfs, so
# there is nothing here worth preserving and a volume that outlives its run is
# state the next run did not declare.
stack-down:
	$(COMPOSE) down -v --remove-orphans
