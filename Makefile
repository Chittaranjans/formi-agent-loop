SCENARIO ?= scenarios/public_01_dev.yaml
NAME ?= $(basename $(notdir $(SCENARIO)))
PORT ?= 8700

baseline:
	python -m sim baseline --scenario $(SCENARIO) --run-dir runs/base_$(NAME)
serve:
	python -m sim serve --scenario $(SCENARIO) --run-dir runs/mine_$(NAME) --force --port $(PORT)
grade:
	mkdir -p reports && python -m sim grade --run runs/mine_$(NAME) --baseline runs/base_$(NAME) --out reports/$(NAME).md
eval:
	python scripts/run_public_eval.py --scenario $(SCENARIO) --name $(NAME) --port $(PORT)
test:
	python -m pytest -q tests/test_controller_unit.py tests/test_sim.py
