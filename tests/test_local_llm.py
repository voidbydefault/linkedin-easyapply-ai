import pytest
import os
import yaml
from unittest.mock import patch, MagicMock
from app.ai_handler import AIHandler
from app.defaults import DEFAULT_GEMINI_MODEL

@pytest.fixture
def sample_config():
    return {
        'llm_provider': 'local',
        'local_llm_url': 'http://127.0.0.1:8080',
        'local_model_name': 'local-model',
        'gemini_api_key': '',
        'model_name': DEFAULT_GEMINI_MODEL,
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
    assert handler.max_rpd is None
    assert handler.get_usage_stats() == (0, None)

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


def test_local_llm_ignores_rpd_limits_and_usage_tracking(sample_config, tmp_path):
    sample_config['ai_settings']['max_rpd'] = 1
    handler = AIHandler(sample_config)
    handler.work_dir = str(tmp_path)
    handler.usage_file = str(tmp_path / "ai_usage.json")
    handler.init_usage_tracker()

    assert handler.max_rpd is None
    assert handler.get_usage_stats() == (0, None)

    handler.track_api_usage()
    handler.track_api_usage()
    assert not os.path.exists(handler.usage_file)


def test_gemini_default_model_and_rpd_limits(tmp_path):
    handler = AIHandler({})
    handler.work_dir = str(tmp_path)
    handler.usage_file = str(tmp_path / "ai_usage.json")
    handler.init_usage_tracker()

    assert handler.provider == 'gemini'
    assert handler.model_name == DEFAULT_GEMINI_MODEL
    assert handler.model_name == 'gemini-3.5-flash-lite'
    assert handler.max_rpd == 500

    flash_handler = AIHandler({'llm_provider': 'gemini', 'model_name': 'gemini-2.5-flash'})
    assert flash_handler.max_rpd == 250

    pro_handler = AIHandler({'llm_provider': 'gemini', 'model_name': 'gemini-2.5-pro'})
    assert pro_handler.max_rpd == 100

    gemma_handler = AIHandler({'llm_provider': 'gemini', 'model_name': 'gemma-3-27b-it'})
    assert gemma_handler.max_rpd == 14400

    auto_rpd_handler = AIHandler({'llm_provider': 'gemini', 'ai_settings': {'max_rpd': 0}})
    assert auto_rpd_handler.max_rpd == 500

    override_int_handler = AIHandler({'llm_provider': 'gemini', 'ai_settings': {'max_rpd': 1200}})
    assert override_int_handler.max_rpd == 1200

    override_str_handler = AIHandler({'llm_provider': 'gemini', 'ai_settings': {'max_rpd': '1,200'}})
    assert override_str_handler.max_rpd == 1200


def _make_isolated_gemini_handler(tmp_path, model_name=DEFAULT_GEMINI_MODEL, max_rpd=2):
    handler = AIHandler({
        'llm_provider': 'gemini',
        'gemini_api_key': 'fake-key',
        'model_name': model_name,
        'ai_settings': {'max_rpd': max_rpd},
    })
    handler.work_dir = str(tmp_path)
    handler.cache_path = str(tmp_path / "ai_cache.json")
    handler.qa_cache_path = str(tmp_path / "qa_cache.json")
    handler.usage_file = str(tmp_path / "ai_usage.json")
    handler.api_log_path = str(tmp_path / "api_usage_log.csv")
    handler.cache = {}
    handler.qa_cache = {}
    handler._ensure_api_log_exists()
    handler.init_usage_tracker()
    handler.client = MagicMock()
    return handler


def test_call_gemini_quota_accuracy_per_model_and_no_count_on_failure(tmp_path):
    handler = _make_isolated_gemini_handler(tmp_path, model_name='gemini-3.5-flash-lite', max_rpd=2)
    ok_resp = MagicMock()
    ok_resp.text = "ok"

    # Failed API call (e.g., 404 or network error) must NOT consume RPD quota
    with patch('time.sleep'):
        handler.client.models.generate_content.side_effect = RuntimeError("404 NOT_FOUND")
        res_fail = handler.call_gemini("fail-prompt", retries=1, purpose="Test")
    assert res_fail is None
    assert handler.get_usage_stats() == (0, 2)

    # Exactly max_rpd (2) successful calls should succeed without raising
    handler.client.models.generate_content.side_effect = None
    handler.client.models.generate_content.return_value = ok_resp
    assert handler.call_gemini("prompt-1", retries=1, purpose="Test") == "ok"
    assert handler.get_usage_stats() == (1, 2)
    assert handler.call_gemini("prompt-2", retries=1, purpose="Test") == "ok"
    assert handler.get_usage_stats() == (2, 2)

    # Call max_rpd + 1 must raise API_LIMIT_REACHED without inflating count
    with pytest.raises(Exception, match="API_LIMIT_REACHED"):
        handler.call_gemini("prompt-3", retries=1, purpose="Test")
    assert handler.get_usage_stats() == (2, 2)

    # A different model tracks its own daily quota independently
    other_handler = _make_isolated_gemini_handler(tmp_path, model_name='gemini-2.5-flash', max_rpd=2)
    assert other_handler.get_usage_stats() == (0, 2)
    other_handler.client.models.generate_content.return_value = ok_resp
    assert other_handler.call_gemini("other-prompt-1", retries=1, purpose="Test") == "ok"
    assert other_handler.get_usage_stats() == (1, 2)
    assert handler.get_usage_stats() == (2, 2)


def test_call_gemini_detects_429_and_resource_exhausted(tmp_path):
    class Code429Error(Exception):
        def __init__(self, msg, code=429):
            super().__init__(msg)
            self.code = code

    handler = _make_isolated_gemini_handler(tmp_path, max_rpd=100)
    handler.client.models.generate_content.side_effect = Code429Error("Rate limited", code=429)

    with patch('time.sleep') as mock_sleep:
        with pytest.raises(Exception, match="API_LIMIT_REACHED"):
            handler.call_gemini("rate-limit-prompt-1", retries=2, purpose="Test")
    assert mock_sleep.call_count == 1
    assert handler.get_usage_stats() == (0, 100)

    handler.client.models.generate_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
    with patch('time.sleep') as mock_sleep:
        with pytest.raises(Exception, match="API_LIMIT_REACHED"):
            handler.call_gemini("rate-limit-prompt-2", retries=2, purpose="Test")
    assert mock_sleep.call_count == 1
    assert handler.get_usage_stats() == (0, 100)


def test_bot_start_applying_api_limit_returns_without_quitting_browser(tmp_path):
    from app.bot.bot import LinkedinEasyApply

    class SwitchToStub:
        def __init__(self):
            self.checks = 0
            self._alert = MagicMock()

        @property
        def alert(self):
            self.checks += 1
            if self.checks == 1:
                return self._alert
            raise Exception("no such alert")

    mock_browser = MagicMock()
    mock_browser.switch_to = SwitchToStub()

    params = {
        'email': 'test@example.com',
        'password': 'secret',
        'locations': ['Remote'],
        'uploads': {'resume': ''},
    }
    ai_config = {'ai_settings': {'ban_safe': False, 'max_applications': 10}}

    with patch('os.getcwd', return_value=str(tmp_path)):
        bot = LinkedinEasyApply(params, mock_browser, ai_config, MagicMock(), "profile", ["Engineer"])

    with patch.object(bot, 'check_for_break'), \
         patch.object(bot, 'next_job_page'), \
         patch.object(bot, 'apply_jobs', side_effect=Exception("API_LIMIT_REACHED")), \
         patch('app.bot.bot.human_sleep'), \
         patch('time.sleep'):
        bot.start_applying()

    mock_browser.execute_script.assert_called_once()
    assert mock_browser.switch_to.checks == 2
    mock_browser.quit.assert_not_called()

    # Alert error case must also return cleanly without quitting the browser
    mock_browser_err = MagicMock()
    mock_browser_err.execute_script.side_effect = RuntimeError("tab crashed")
    with patch('os.getcwd', return_value=str(tmp_path)):
        bot_err = LinkedinEasyApply(params, mock_browser_err, ai_config, MagicMock(), "profile", ["Engineer"])
    with patch.object(bot_err, 'check_for_break'), \
         patch.object(bot_err, 'next_job_page'), \
         patch.object(bot_err, 'apply_jobs', side_effect=Exception("API_LIMIT_REACHED")), \
         patch('app.bot.bot.human_sleep'):
        bot_err.start_applying()

    mock_browser_err.quit.assert_not_called()


def test_run_cleanup_bot_tab_preserves_browser_and_returns_to_dashboard():
    import run

    # 1. Two-tab case: closes bot_tab, switches to main_tab, never calls quit()
    browser_2tabs = MagicMock()
    handles = ['main', 'bot']
    type(browser_2tabs).window_handles = property(lambda self: list(handles))

    def close_current_tab():
        handles.remove('bot')

    browser_2tabs.close.side_effect = close_current_tab

    run.cleanup_bot_tab(browser_2tabs, 'main', 'bot')
    browser_2tabs.close.assert_called_once()
    assert browser_2tabs.switch_to.window.call_args_list[0][0][0] == 'bot'
    assert browser_2tabs.switch_to.window.call_args_list[-1][0][0] == 'main'
    browser_2tabs.quit.assert_not_called()

    # 2. Single-tab fallback (bot_tab is None): navigates main_tab back to home URL
    browser_1tab = MagicMock()
    browser_1tab.window_handles = ['main']
    run.cleanup_bot_tab(browser_1tab, 'main', None)
    browser_1tab.close.assert_not_called()
    browser_1tab.switch_to.window.assert_called_with('main')
    browser_1tab.get.assert_called_once_with('http://localhost:5001')
    browser_1tab.quit.assert_not_called()

    # 3. Closed main_tab case: switches to remaining handle and navigates home
    browser_no_main = MagicMock()
    browser_no_main.window_handles = ['other']
    run.cleanup_bot_tab(browser_no_main, 'main', 'bot')
    browser_no_main.close.assert_not_called()
    browser_no_main.switch_to.window.assert_called_with('other')
    browser_no_main.get.assert_called_once_with('http://localhost:5001')
    browser_no_main.quit.assert_not_called()


def test_config_ui_uses_central_default_gemini_model():
    pytest.importorskip('flask')
    from app import config_ui

    assert config_ui.DEFAULT_GEMINI_CONFIG['model_name'] == DEFAULT_GEMINI_MODEL
    assert config_ui.DEFAULT_GEMINI_CONFIG['ai_settings']['max_rpd'] == 0

    client = config_ui.app.test_client()
    with patch('os.path.exists', return_value=False):
        res = client.get('/edit/gemini')
        assert res.status_code == 200
        assert DEFAULT_GEMINI_MODEL.encode('utf-8') in res.data
        assert b'ai_setting_max_rpd' in res.data


def test_config_ui_save_gemini_persists_max_rpd(tmp_path, monkeypatch):
    pytest.importorskip('flask')
    from app import config_ui

    tmp_cfg = tmp_path / "gemini_config.yaml"
    with open(tmp_cfg, 'w', encoding='utf-8') as f:
        yaml.dump({
            'llm_provider': 'gemini',
            'gemini_api_key': 'existing-secret',
            'model_name': DEFAULT_GEMINI_MODEL,
            'ai_settings': {
                'enable_ai_search': True,
                'custom_instruction_prompt': 'Keep answers concise'
            }
        }, f, sort_keys=False)

    monkeypatch.setattr(config_ui, 'GEMINI_CONFIG', str(tmp_cfg))
    monkeypatch.setattr(config_ui, 'mark_verified', lambda filename: None)

    client = config_ui.app.test_client()

    # Save with comma-formatted custom max_rpd
    res = client.post('/save/gemini', data={
        'llm_provider': 'gemini',
        'model_name': DEFAULT_GEMINI_MODEL,
        'ai_setting_enable_ai_search': 'on',
        'ai_setting_max_rpd': '1,500',
    })
    assert res.status_code == 302
    with open(tmp_cfg, 'r', encoding='utf-8') as f:
        saved = yaml.safe_load(f)
    assert saved['ai_settings']['max_rpd'] == 1500
    assert saved['ai_settings']['custom_instruction_prompt'] == 'Keep answers concise'

    # Save with 0 (auto-detect)
    res_zero = client.post('/save/gemini', data={
        'llm_provider': 'gemini',
        'model_name': DEFAULT_GEMINI_MODEL,
        'ai_setting_max_rpd': '0',
    })
    assert res_zero.status_code == 302
    with open(tmp_cfg, 'r', encoding='utf-8') as f:
        saved_zero = yaml.safe_load(f)
    assert saved_zero['ai_settings']['max_rpd'] == 0



