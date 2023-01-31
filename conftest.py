import csv
import json
import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
import boto3 as boto3
from botocore.config import Config
import pytest as pytest
import requests as requests
from requests.adapters import HTTPAdapter, Retry

from moto import settings
from moto.core.models import BaseMockAWS
from tests import DEFAULT_ACCOUNT_ID

BASE_PATH = os.path.join(os.path.dirname(__file__), "../../target/reports")

FNAME_RAW_DATA_CSV = os.path.join(BASE_PATH,"metric_data_raw.csv")


# overriding ENVs + settings to use LocalStack as endpoint
os.environ["TEST_SERVER_MODE_ENDPOINT"] = 'http://localhost:4566'
os.environ["MOTO_CALL_RESET_API"] = 'false'
settings.TEST_SERVER_MODE = True

@pytest.fixture(scope="session", autouse=True)
def default_localstack_client_config_fixture() -> Iterator[None]:
    def localstack_client(*args, org=boto3.client, **kwargs):
        # kwargs["endpoint_url"] = 'http://localhost:4566' # TODO could also use fixture to override endpoint_url
        config: Config = kwargs.get("config")
        max_retry_config = Config(
            retries={"max_attempts": 1, "total_max_attempts": 2})
        if config:
            config.merge(max_retry_config)
        else:
            kwargs["config"] = max_retry_config
        return org(*args, **kwargs)

    with patch("boto3.client", side_effect=localstack_client):
        yield


@pytest.fixture(scope="session", autouse=True)
def default_localstack_client_fixture() -> Iterator[None]:
    """
    Fixture that will batch the AWS_ACCESS_KEY_ID to use moto's DEFAULT_ACCOUNT_ID
    This is required, because some tests explicelty check the returned arn, and by default LocalStack will assume ID 00000000
    """

    def localstack_mock_env_variables(self, org=BaseMockAWS.mock_env_variables):
        # patch the access key id to use the same one in LocalStack
        self.FAKE_KEYS["AWS_ACCESS_KEY_ID"] = DEFAULT_ACCOUNT_ID
        org(self)

    with patch("moto.core.models.BaseMockAWS.mock_env_variables", autospec=True,
               side_effect=localstack_mock_env_variables):
        yield


@pytest.fixture(scope="function", autouse=True)
def default_cleanup_localstack_resources():
    """
    Fixture to cleanup localstack resources after each test case run
    We do not know which interservice communication happend, so we delete all resources, by sending a request to
    the endpoint /_pods/state/reset
    """
    yield
    # cleanup
    payload = {'persistence': True}  # defining no services means all services will be cleared (we don't know which interservice communication tests trigger)
    headers = {'content-type': 'application/json'}
    url = "http://localhost:4566/_pods/state/reset"
    requests.delete(
        url,
        json=payload,
        headers=headers,
        timeout=90
    )


@pytest.fixture(scope="function", autouse=True)
def check_if_test_failed(request):
    cur_failed = request.session.testsfailed
    yield
    now_failed = request.session.testsfailed
    node_id = request._pyfuncitem.nodeid
    if cur_failed < now_failed:
        # do something
        # teardown may still fail, but that should be fine and can be ignored
        print(f"--> test failed: {node_id}")

    else:
        print(f"test succeeded: {node_id}")

        metric_response = requests.get("http://localhost:4566/metrics/raw")
        try:
            metric_json = json.loads(metric_response.content.decode("utf-8"))

            with open(FNAME_RAW_DATA_CSV, "a") as fd:
                writer = csv.writer(fd)
                for m in metric_json.get("metrics"):
                    m["node_id"] = node_id
                    writer.writerow(m.values())
        except json.JSONDecodeError:
            print("could not decode metrics")
            pass

    url = "http://localhost:4566/metrics/reset"
    r = requests.delete(
        url,
        timeout=90
    )
    assert r.status_code == 200


def pytest_addoption(parser):
    parser.addoption(
        "--services",
        action="store",
        help="Comma separated list of services that should be tested",
    )

def _startup_localstack():
    try:
        _localstack_health_check()
    except:
        os.system('DNS_ADDRESS=127.0.0.1 EXTENSION_DEV_MODE=1 LOCALSTACK_API_KEY=$LOCALSTACK_API_KEY localstack start -d')

        _localstack_health_check()

    print("LocalStack running")

def _shutdown_localstack():
    os.system('localstack stop')


# TODO "package" doesn't seem to work, but "module" re-starts LS too often
@pytest.fixture(scope="package", autouse=True)
def startup_localstack():
    _startup_localstack()
    print("LocalStack is ready...")

    yield

    _shutdown_localstack()

@pytest.hookimpl()
def pytest_sessionstart(session: "Session") -> None:
    Path(BASE_PATH).mkdir(parents=True, exist_ok=True)
    with open(FNAME_RAW_DATA_CSV, "w") as fd:
        writer = csv.writer(fd)
        writer.writerow(["service", "operation", "parameters", "response_code", "response_data", "exception", "origin", "node_id"])


def pytest_collection_modifyitems(items, config):
    selected_services = config.option.services.split(",") if config.option.services else None

    selected_items = []
    deselected_items = []

    if not selected_services:
        # no default, select all
        _startup_localstack()
        response = requests.get("http://localhost:4566/_localstack/health").content.decode("utf-8")
        selected_services = [k for k in json.loads(response).get("services").keys()]

    #selected_services = ['acm']

    excluded_service = ["acmpca", "eks",# TODO excluding EKS because it requires a lot of resources
                 ]
    excluded_test_cases = ["test_rds.py::test_get_databases_paginated"] # exclude "specific test, because it creates 51 databases
    for item in items:
        for service in selected_services:
            if item in deselected_items or item in selected_items:
                continue
            test_class_name = item._nodeid.split("::")[0].split("/")[-1]
            if any([True for x in excluded_service if x in test_class_name]):
                deselected_items.append(item)
            elif any([True for x in excluded_test_cases if x in item._nodeid]):
                deselected_items.append(item)
            elif (f'test_{service}' in test_class_name or f'test_{service.replace("-", "")}' in test_class_name):
                selected_items.append(item)
        if item not in selected_items:
            deselected_items.append(item)

    config.hook.pytest_deselected(items=deselected_items)
    items[:] = selected_items



def _localstack_health_check():
    """Check if the localstack service is healthy"""
    localstack_health_url = "http://localhost:4566/_localstack/health"
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=2)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.get(localstack_health_url)
    session.close()

