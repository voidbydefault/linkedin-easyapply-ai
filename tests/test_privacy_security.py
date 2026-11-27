import io
import os
import yaml
from unittest.mock import MagicMock, patch

import pytest

from app.ai_handler import AIHandler, validate_local_llm_url
from app.bot.forms import ApplicationForm


def test_format_config_to_text_excludes_personal_info_and_uploads():
    handler = AIHandler({'llm_provider': 'local'})
    config_params = {
        'email': 'candidate@example.com',
        'password': 'super-secret-password',
        'gemini_api_key': 'AIzaFakeKey1234567890',
        'personalInfo': {
            'First Name': 'Alex',
            'Last Name': 'Example',
            'Mobile Phone Number': '5550199999',
            'Street address': '742 Evergreen Terrace',
            'City': 'Springfield',
            'State': 'IL',
            'Zip': '62701',
            'Country': 'USA',
            'Linkedin': 'https://www.linkedin.com/in/alex-example',
            'Website': 'https://alex.example.com',
        },
        'uploads': {'resume': r'C:\Users\someuser\work\resume.pdf'},
        'positions': ['Backend Engineer'],
        'salaryMinimum': '100000',
    }

    formatted = handler.format_config_to_text(config_params)

    for sensitive in (
        'candidate@example.com',
        'super-secret-password',
        'AIzaFakeKey1234567890',
        'Alex',
        '5550199999',
        '742 Evergreen Terrace',
        'Springfield',
        '62701',
        'alex-example',
        'someuser',
        'resume.pdf',
    ):
        assert sensitive not in formatted

    assert 'Backend Engineer' in formatted


def test_generate_user_profile_redacts_pii_for_gemini_and_preserves_dates(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    with open(cfg_dir / "bio_config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(
            {"bio_text": "Contact me at bio.user@example.com or +1 415 555 0144."},
            f,
        )

    config = {
        'llm_provider': 'gemini',
        'gemini_api_key': 'fake-key',
        'email': 'candidate@example.com',
        'personalInfo': {
            'Mobile Phone Number': '5550199999',
            'Street address': '742 Evergreen Terrace',
            'Zip': '62701',
            'Linkedin': 'https://www.linkedin.com/in/alex-example',
            'Website': 'https://portfolio.example.com',
        },
        'uploads': {'resume': str(tmp_path / 'resume.pdf')},
    }
    handler = AIHandler(config)
    handler.client = MagicMock()

    raw_resume = (
        "Alex Example\n"
        "Email: resume.user@example.com | Phone: +1 (415) 555-0199\n"
        "742 Evergreen Terrace, 62701\n"
        "https://www.linkedin.com/in/alex-example\n"
        "Senior Engineer 2018-2023\n"
        "Software Developer 2015 - 2018\n"
    )
    captured_prompts = []

    def fake_call_gemini(prompt, purpose="General"):
        captured_prompts.append(prompt)
        return "Profile summary"

    with patch.object(handler, "parse_resume", return_value=raw_resume), \
         patch.object(handler, "call_gemini", side_effect=fake_call_gemini):
        result = handler.generate_user_profile(str(tmp_path / "resume.pdf"), config_params=config)

    assert result == "Profile summary"
    assert len(captured_prompts) == 1
    sent_prompt = captured_prompts[0]

    for pii in (
        'resume.user@example.com',
        'bio.user@example.com',
        'candidate@example.com',
        '555-0199',
        '555 0144',
        '742 Evergreen Terrace',
        '62701',
        'alex-example',
        'portfolio.example.com',
    ):
        assert pii not in sent_prompt

    assert '[REDACTED_EMAIL]' in sent_prompt
    assert '[REDACTED_PHONE]' in sent_prompt
    assert '2018-2023' in sent_prompt
    assert '2015 - 2018' in sent_prompt


def test_answer_question_strict_rules_and_log_redaction(capsys):
    handler = AIHandler({
        'llm_provider': 'local',
        'email': 'candidate@example.com',
        'personalInfo': {
            'Mobile Phone Number': '5550199999',
            'City': 'SecretCity',
            'State': 'SecretState',
            'Country': 'SecretCountry',
            'Zip': '99999',
            'Street address': '742 Evergreen Terrace',
        },
        'ai_settings': {},
    })
    handler.qa_cache = {}
    handler.knowledge_base_questions = []

    with patch.object(handler, 'call_gemini', return_value='{"answer": "5", "type": "numeric"}'):
        ans_num = handler.answer_question("Number of years of Python experience?", "Text", "profile")
        assert ans_num != '5550199999'
        assert ans_num == '5'

        ans_contact = handler.answer_question("Preferred contact method?", "Text", "profile")
        assert ans_contact != '5550199999'

    with patch.object(handler, 'call_gemini', return_value='{"answer": "2 weeks", "type": "text"}'):
        ans_notice = handler.answer_question("Please state your notice period", "Text", "profile")
        assert ans_notice != 'SecretState'

    assert handler.answer_question("Mobile Phone Number", "Text", "profile") == '5550199999'
    assert handler.answer_question("State / Province", "Text", "profile") == 'SecretState'
    assert handler.answer_question("City", "Text", "profile") == 'SecretCity'
    assert handler.answer_question("Country", "Text", "profile") == 'SecretCountry'
    assert handler.answer_question("Zip / Postal Code", "Text", "profile") == '99999'

    captured_out = capsys.readouterr().out
    for sensitive in ('SecretCity', 'SecretState', 'SecretCountry', '99999', '5550199999'):
        assert sensitive not in captured_out


def test_form_handle_text_input_redacts_logged_values(capsys):
    browser = MagicMock()
    form = ApplicationForm(browser, MagicMock(), "profile", {'personalInfo': {'First Name': 'SecretFirst'}})
    form.enter_text = MagicMock()

    el = MagicMock()
    el.tag_name = 'input'
    el.get_attribute.side_effect = lambda attr: {
        'value': 'SecretPrefillValue',
        'aria-label': 'First Name',
    }.get(attr)

    form.handle_text_input(el)

    el2 = MagicMock()
    el2.tag_name = 'input'
    el2.get_attribute.side_effect = lambda attr: {
        'value': '',
        'aria-label': 'First Name',
    }.get(attr)
    form.handle_text_input(el2)

    out = capsys.readouterr().out
    assert 'SecretPrefillValue' not in out
    assert 'SecretFirst' not in out
    assert '[redacted]' in out


def test_secrets_config_never_renders_or_saves_password(tmp_path):
    pytest.importorskip('flask')
    from app import config_ui

    with open(config_ui.SECRETS_CONFIG, 'w', encoding='utf-8') as f:
        yaml.dump({
            'email': 'user@example.com',
            'password': 'legacy-plaintext-password',
            'personalInfo': {'First Name': 'Alex'},
        }, f)

    client = config_ui.app.test_client()
    res = client.get('/edit/config/secrets')
    assert res.status_code == 200
    assert b'legacy-plaintext-password' not in res.data
    assert b'name="password"' not in res.data

    post_res = client.post(
        '/save/config/secrets',
        data={
            'email': 'user@example.com',
            'password': 'should-be-ignored-password',
            'info_First Name': 'Alex',
        },
        headers={'X-CSRF-Token': config_ui.CSRF_TOKEN},
    )
    assert post_res.status_code == 302

    with open(config_ui.SECRETS_CONFIG, 'r', encoding='utf-8') as f:
        saved = yaml.safe_load(f)
    assert 'password' not in saved


def test_validate_data_does_not_require_password(tmp_path):
    import run

    resume_file = tmp_path / "resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4\n")
    params = {
        'email': 'user@example.com',
        'personalInfo': {
            'First Name': 'Alex',
            'Last Name': 'Example',
            'Mobile Phone Number': '5550199999',
            'Phone Country Code': 'United States (+1)',
        },
        'uploads': {'resume': str(resume_file)},
        'positions': ['Software Engineer'],
        'locations': ['Remote'],
    }
    run.validate_data(params, {'ai_settings': {'enable_ai_search': False}})


def test_csrf_and_origin_enforcement():
    pytest.importorskip('flask')
    from app import config_ui

    client = config_ui.app.test_client()

    # Missing CSRF token -> 403
    r1 = client.post('/accept_nag')
    assert r1.status_code == 403

    # Wrong CSRF token -> 403
    r2 = client.post('/accept_nag', headers={'X-CSRF-Token': 'wrong-token'})
    assert r2.status_code == 403

    # Cross-origin Origin header even with valid token -> 403
    r3 = client.post(
        '/accept_nag',
        headers={'X-CSRF-Token': config_ui.CSRF_TOKEN, 'Origin': 'https://evil.example'},
    )
    assert r3.status_code == 403

    # Valid CSRF token + localhost -> 200
    r4 = client.post(
        '/accept_nag',
        headers={'X-CSRF-Token': config_ui.CSRF_TOKEN, 'Origin': 'http://127.0.0.1:5001'},
    )
    assert r4.status_code == 200


def test_ssrf_and_local_llm_url_validation():
    pytest.importorskip('flask')
    from app import config_ui

    for bad_url in (
        'file:///etc/passwd',
        'http://169.254.169.254/latest/meta-data',
        'https://evil.example/v1',
        'http://8.8.8.8:8080',
        'http://0.0.0.0:8080',
    ):
        ok, _ = validate_local_llm_url(bad_url)
        assert not ok

    for good_url in (
        'http://127.0.0.1:8080',
        'http://localhost:11434',
        'http://192.168.1.50:8080',
    ):
        ok, _ = validate_local_llm_url(good_url)
        assert ok

    client = config_ui.app.test_client()
    with patch('urllib.request.urlopen') as mock_open:
        res = client.post(
            '/api/test_llm_connection',
            json={'llm_provider': 'local', 'local_llm_url': 'http://169.254.169.254/latest/meta-data'},
            headers={'X-CSRF-Token': config_ui.CSRF_TOKEN},
        )
        assert res.status_code == 400
        assert res.get_json()['success'] is False
        mock_open.assert_not_called()

    handler = AIHandler({'llm_provider': 'local', 'local_llm_url': 'https://evil.example'})
    with patch('urllib.request.urlopen') as mock_open:
        assert handler.call_local_llm("hello") is None
        mock_open.assert_not_called()


def test_upload_resume_validates_pdf_signature():
    pytest.importorskip('flask')
    from app import config_ui

    client = config_ui.app.test_client()

    bad_ext = client.post(
        '/upload/resume',
        data={'resume_file': (io.BytesIO(b'%PDF-1.4\n'), 'resume.txt')},
        content_type='multipart/form-data',
        headers={'X-CSRF-Token': config_ui.CSRF_TOKEN},
    )
    assert bad_ext.status_code == 400

    fake_pdf = client.post(
        '/upload/resume',
        data={'resume_file': (io.BytesIO(b'not a real pdf'), 'resume.pdf')},
        content_type='multipart/form-data',
        headers={'X-CSRF-Token': config_ui.CSRF_TOKEN},
    )
    assert fake_pdf.status_code == 400

    good_pdf = client.post(
        '/upload/resume',
        data={'resume_file': (io.BytesIO(b'%PDF-1.7\nbody'), 'resume.pdf')},
        content_type='multipart/form-data',
        headers={'X-CSRF-Token': config_ui.CSRF_TOKEN},
    )
    assert good_pdf.status_code == 200
    assert os.path.exists(good_pdf.get_json()['path'])


def test_init_browser_omits_insecure_chrome_flags():
    import run

    captured_args = []

    class FakeOptions:
        def add_argument(self, arg):
            captured_args.append(arg)

    fake_driver = MagicMock()
    with patch('run.uc.ChromeOptions', return_value=FakeOptions()), \
         patch('run.ChromeDriverManager') as mock_cdm, \
         patch('run.uc.Chrome', return_value=fake_driver):
        mock_cdm.return_value.install.return_value = 'chromedriver'
        run.init_browser()

    assert '--ignore-certificate-errors' not in captured_args
    assert '--no-sandbox' not in captured_args


def test_streamlit_dashboards_bind_localhost_and_disable_telemetry():
    pytest.importorskip('flask')
    from app import config_ui

    client = config_ui.app.test_client()
    config_ui.DASHBOARD_PROCESS = None
    config_ui.SCOUT_DASHBOARD_PROCESS = None

    with patch('subprocess.Popen') as mock_popen, patch('time.sleep'):
        mock_popen.return_value.poll.return_value = None
        client.get('/dashboard')
        dash_args = mock_popen.call_args[0][0]
        assert '--server.address=127.0.0.1' in dash_args
        assert '--browser.gatherUsageStats=false' in dash_args

        client.get('/scout_dashboard')
        scout_args = mock_popen.call_args[0][0]
        assert '--server.address=127.0.0.1' in scout_args
        assert '--browser.gatherUsageStats=false' in scout_args


def test_templates_remove_external_cdns_and_include_referrer_policy():
    pytest.importorskip('flask')
    from app import config_ui

    client = config_ui.app.test_client()
    res = client.get('/')
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert 'cdnjs.cloudflare.com' not in html
    assert 'fonts.googleapis.com' not in html
    assert '<meta name="referrer" content="no-referrer">' in html
    assert res.headers.get('Referrer-Policy') == 'no-referrer'
    assert res.headers.get('X-Frame-Options') == 'DENY'
    assert res.headers.get('X-Content-Type-Options') == 'nosniff'


def test_wait_for_server_ready_retries_and_handles_early_exit():
    import urllib.error
    pytest.importorskip('flask')
    from app import config_ui

    ok_resp = MagicMock()
    ok_resp.status = 200
    ok_resp.__enter__.return_value = ok_resp

    calls = {'n': 0}

    def fake_urlopen(url, timeout=1):
        calls['n'] += 1
        if calls['n'] < 3:
            raise urllib.error.URLError("Connection refused")
        return ok_resp

    running_proc = MagicMock()
    running_proc.poll.return_value = None

    with patch('urllib.request.urlopen', side_effect=fake_urlopen), patch('time.sleep'):
        assert config_ui.wait_for_server_ready(timeout=5.0, process=running_proc) is True
    assert calls['n'] == 3

    exited_proc = MagicMock()
    exited_proc.poll.return_value = 1
    with patch('urllib.request.urlopen') as mock_open:
        assert config_ui.wait_for_server_ready(timeout=5.0, process=exited_proc) is False
        mock_open.assert_not_called()
