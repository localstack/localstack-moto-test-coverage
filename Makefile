VENV_DIR ?= .venv
PIP_CMD ?= pip3
VENV_BIN ?= python3 -m venv

ifeq ($(OS), Windows_NT)
	VENV_ACTIVATE = $(VENV_DIR)/Scripts/activate
else
	VENV_ACTIVATE = $(VENV_DIR)/bin/activate
endif

VENV_RUN = . $(VENV_ACTIVATE)

$(VENV_ACTIVATE):
	test -d $(VENV_DIR) || $(VENV_BIN) $(VENV_DIR)
	$(VENV_RUN); $(PIP_CMD) install --upgrade pip
	touch $(VENV_ACTIVATE)

venv: $(VENV_ACTIVATE)    ## Create a new (empty) virtual environment

checkout_moto:
	test -d moto || git clone https://github.com/getmoto/moto.git

update_moto: venv
	cd moto && git checkout master && git fetch origin master
	$(VENV_RUN); cd moto && make init

init_extension: venv
	# TODO user must be logged in already
	pip install localstack
	cd collect-raw-metric-data-extension && make install
	localstack extensions init
	localstack extensions dev enable ./collect-raw-metric-data-extension

install: venv checkout_moto init_extension
	$(VENV_RUN); $(PIP_CMD) install pytest requests
	$(VENV_RUN); $(PIP_CMD) install pytest requests

run-tests:
	cp conftest.py moto/tests/
	$(VENV_RUN); python -m pytest --capture=no --junitxml=target/reports/pytest.xml  moto --tb=line

clean:
	rm -rf moto
	rm -rf target
	rm -rf $(VENV_DIR)