import pytest
import os
import yaml
from unittest.mock import patch, MagicMock
from app.ai_handler import AIHandler

@pytest.fixture
def sample_config():
    return {
        'llm_provider': 'local',
        'local_llm_url': 'http://127.0.0.1:8080',
        'local_model_name': 'local-model',
        'gemini_api_key': '',
        'model_name': 'gemini-2.0-flash',
        'ai_settings': {
            'enable_ai_search': True,
            'let_ai_guess_answer': True,
            'application_match_threshold': 80,
            'batch_size': 5,
            'user_prompt_timeout_seconds': 5,
            'api_retry_attempts': 2,
            'api_retry_backoff_seconds': 1,
            'max_applications': 25,
            'ban_safe': True
        }
    }

def test_ai_handler_init_local(sample_config):
    handler = AIHandler(sample_config)
    assert handler.provider == 'local'
    assert handler.local_url == 'http://127.0.0.1:8080'
    assert handler.local_model_name == 'local-model'
    assert handler.client is None
    assert handler.max_rpd == 100000

def test_call_local_llm_chat_completions(sample_config):
    handler = AIHandler(sample_config)
    handler.cache = {} # Ensure fresh cache for test
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"choices": [{"message": {"content": "{\\"score\\": 95, \\"reason\\": \\"Great fit\\"}"}}]}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_urlopen:
        result = handler.call_local_llm("test unique prompt 123", retries=1, purpose="Job Screening")
        assert result == '{"score": 95, "reason": "Great fit"}'
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == 'http://127.0.0.1:8080/v1/chat/completions'

def test_call_local_llm_completion_fallback(sample_config):
    import urllib.error
    handler = AIHandler(sample_config)
    handler.cache = {} # Ensure fresh cache for test
    
    # First call (v1/chat/completions) gives 404, second call (/completion) succeeds
    mock_response_completion = MagicMock()
    mock_response_completion.read.return_value = b'{"content": "Yes"}'
    mock_response_completion.__enter__.return_value = mock_response_completion

    def side_effect(req, timeout=None):
        if req.full_url.endswith('/v1/chat/completions'):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return mock_response_completion

    with patch('urllib.request.urlopen', side_effect=side_effect):
        result = handler.call_local_llm("test fallback prompt 456", retries=1, purpose="Question Answering")
        assert result == "Yes"

def test_call_gemini_routes_to_local_when_provider_is_local(sample_config):
    handler = AIHandler(sample_config)
    handler.cache = {}
    with patch.object(handler, 'call_local_llm', return_value="Local Result") as mock_local:
        res = handler.call_gemini("test routing prompt 789")
        assert res == "Local Result"
        mock_local.assert_called_once_with("test routing prompt 789", retries=3, purpose="General")

def test_api_test_llm_connection_local_success():
    pytest.importorskip('flask')
    from app.config_ui import app
    client = app.test_client()
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch('urllib.request.urlopen', return_value=mock_resp):
        res = client.post('/api/test_llm_connection', json={
            'llm_provider': 'local',
            'local_llm_url': 'http://127.0.0.1:8080'
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert '127.0.0.1:8080' in data['message']

def test_api_test_llm_connection_local_failure():
    pytest.importorskip('flask')
    from app.config_ui import app
    client = app.test_client()

    with patch('urllib.request.urlopen', side_effect=Exception("Connection refused")):
        res = client.post('/api/test_llm_connection', json={
            'llm_provider': 'local',
            'local_llm_url': 'http://127.0.0.1:8080'
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is False
        assert 'Connection refused' in data['message']

def test_call_local_llm_strips_think_tags(sample_config):
    handler = AIHandler(sample_config)
    handler.cache = {}
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"choices": [{"message": {"content": "<think>Thinking about candidate fit...</think>{\\"score\\": 88, \\"reason\\": \\"Strong fit\\"}"}}]}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        result = handler.call_local_llm("test thinking tags", retries=1, purpose="Job Screening")
        assert result == '{"score": 88, "reason": "Strong fit"}'

def test_evaluate_batch_chunking(sample_config):
    handler = AIHandler(sample_config)
    # Configure batch_size = 2
    handler.settings['batch_size'] = 2
    jobs = [
        {'id': f'job_{i}', 'text': f'Job description {i}'} for i in range(5)
    ]
    # Mock call_gemini returning json for jobs
    def fake_call_gemini(json_prompt, purpose="General"):
        data = {}
        for j in range(5):
            if f'job_{j}' in json_prompt:
                data[f'job_{j}'] = {'score': 80 + j, 'reason': f'Match {j}'}
        import json as jmod
        return jmod.dumps(data)

    with patch.object(handler, 'call_gemini', side_effect=fake_call_gemini) as mock_call:
        res = handler.evaluate_batch(jobs, "user profile text")
        assert len(res) == 5
        assert res['job_0']['score'] == 80
        assert res['job_4']['score'] == 84
        # Since jobs length is 5 and batch_size is 2, it should chunk into 3 calls: [2, 2, 1]
        assert mock_call.call_count == 3


