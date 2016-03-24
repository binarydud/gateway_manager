import pytest
from pretend import stub
from pyramid import testing
from gateway_manager import devserver

@pytest.mark.slow
def test_import_lambda_function():
    module = devserver.import_lambda_function('good_handler')
    assert hasattr(module, 'handle')

def test_import_lambda_function_missing_handler():
    with pytest.raises(ImportError):
        devserver.import_lambda_function('missing_handler')

def test_build_error_response():
    body = devserver.build_error_response(500, Exception('sample error'))
    assert body == '{"message": "sample error", "code": 500}'

def test_filter_on_response_pattern():
    # missing pattern
    response = stub()
    assert not devserver.filter_on_response_pattern(
        Exception,
        response
    )
    response = stub(pattern="Exception.*")
    assert devserver.filter_on_response_pattern(
        Exception,
        response
    )
    response = stub(pattern="NotFound.*")
    assert not devserver.filter_on_response_pattern(
        Exception,
        response
    )

def test_add_resource():
    resource = stub(
        path="/test",
        method="get"
    )
    with testing.testConfig() as config:
        print config
        devserver.add_resource(config, resource)
        import pdb;pdb.set_trace()
        assert False
