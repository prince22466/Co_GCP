.PHONY: setup deploy-gke deploy-agent run-gke run-agent compare clean trace

setup:
	./scripts/setup_gcp.sh

deploy-gke:
	./scripts/deploy_gke.sh

deploy-agent:
	./scripts/deploy_mig.sh

run-gke:
	./scripts/run_experiment.sh gke

run-agent:
	./scripts/run_experiment.sh agent

compare:
	./scripts/compare_last.sh

trace:
	python3 scenarios/make_trace.py

clean:
	./scripts/cleanup.sh
