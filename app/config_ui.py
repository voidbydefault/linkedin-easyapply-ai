import csv
import os
import sys
import time
import webbrowser
import yaml
import json
import threading
import subprocess
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from datetime import datetime

# Determine absolute path to static folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
print(f"DEBUG: Static Directory is: {STATIC_DIR}")
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR, static_url_path='/static')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
app.logger.setLevel(logging.ERROR)

# Suppress Click/Flask cli output
import click
def secho(text, file=None, nl=None, err=None, color=None, **styles):
    pass
def echo(text, file=None, nl=None, err=None, color=None, **styles):
    pass
click.echo = echo
click.secho = secho


# Global flags

DASHBOARD_PROCESS = None
NAG_ACCEPTED = False

# Project Root
PROJECT_ROOT = os.path.dirname(BASE_DIR)
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'config')
if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)

STATE_FILE = os.path.join(CONFIG_DIR, '.config_state.json')
SIGNAL_FILE = os.path.join(CONFIG_DIR, '.config_complete')
GEMINI_CONFIG = os.path.join(CONFIG_DIR, 'gemini_config.yaml')
JOB_CONFIG = os.path.join(CONFIG_DIR, 'config.yaml')
SECRETS_CONFIG = os.path.join(CONFIG_DIR, 'secrets.yaml')
README_PATH = os.path.join(PROJECT_ROOT, 'README.md')
BOT_STATUS_FILE = os.path.join(CONFIG_DIR, '.bot_active')

def recursive_merge(default, user):
    """Recursively merge dictionary user into default."""
    if not isinstance(default, dict) or not isinstance(user, dict):
        return user if user is not None else default
    
    result = default.copy()
    for k, v in user.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = recursive_merge(result[k], v)
        else:
            result[k] = v
    return result

@app.route('/bot_status')
def bot_status():
    if os.path.exists(BOT_STATUS_FILE):
        try:
            with open(BOT_STATUS_FILE, 'r') as f:
                mode = f.read().strip()
            return jsonify({'running': True, 'mode': mode})
        except:
            return jsonify({'running': True, 'mode': 'unknown'})
    return jsonify({'running': False, 'mode': None})

def load_persistent_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_persistent_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except:
        pass

# Initialize locally
VERIFIED_STATUS = load_persistent_state()

def mark_verified(filename):
    VERIFIED_STATUS[filename] = True
    save_persistent_state(VERIFIED_STATUS)

def get_file_status(filename):
    filepath = os.path.join(CONFIG_DIR, filename)
    exists = os.path.exists(filepath)
    mtime = 'N/A'
    if exists:
        dt = datetime.fromtimestamp(os.path.getmtime(filepath))
        mtime = dt.strftime("%d-%b-%y %I:%M %p")
    
    is_verified = VERIFIED_STATUS.get(filename, False)
    
    return {
        'exists': exists,
        'mtime': mtime,
        'verified': is_verified
    }

import sqlite3

@app.route('/reset/configs', methods=['POST'])
def reset_configs():
    """Deletes all configuration files."""
    files_to_delete = [
        SECRETS_CONFIG,
        JOB_CONFIG,
        GEMINI_CONFIG,
        STATE_FILE
    ]
    
    deleted = []
    errors = []
    
    for fp in files_to_delete:
        if os.path.exists(fp):
            try:
                os.remove(fp)
                deleted.append(os.path.basename(fp))
            except Exception as e:
                errors.append(f"Failed to delete {os.path.basename(fp)}: {e}")
    
    global VERIFIED_STATUS
    VERIFIED_STATUS = {}
    
    if errors:
        return jsonify({'status': 'partial_error', 'message': "; ".join(errors), 'deleted': deleted}), 500
    
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/reset/stats', methods=['POST'])
def reset_stats():
    """Deletes log and stats files."""
    work_dir = os.path.join(PROJECT_ROOT, "work")
    files_to_delete = [
        os.path.join(work_dir, "application_log.csv"),
        os.path.join(work_dir, "bot.log"),
        os.path.join(work_dir, "job_history.db"),
        os.path.join(work_dir, "api_usage_log.csv")
    ]
    
    deleted = []
    errors = []
    
    for fp in files_to_delete:
        if os.path.exists(fp):
            try:
                os.remove(fp)
                deleted.append(os.path.basename(fp))
            except OSError as e:
                if e.winerror == 32 or getattr(e, 'errno', None) == 13:
                    try:
                        with open(fp, 'w') as f:
                            f.truncate(0)
                        deleted.append(f"{os.path.basename(fp)} (Cleared)")
                    except Exception as trunc_e:
                        errors.append(f"Skipped {os.path.basename(fp)} (In use)")
                else:
                    errors.append(f"Failed to delete {os.path.basename(fp)}: {e}")
            except Exception as e:
                errors.append(f"Failed to delete {os.path.basename(fp)}: {e}")
                  
    return jsonify({
        'status': 'success', 
        'deleted': deleted, 
        'errors': errors,
        'message': "Stats reset. Some files were in use and may have been skipped or cleared." if errors else "All stats files reset."
    })

@app.route('/ping')
def ping():
    return "pong"

@app.route('/')
def index():
    status = {
        'config': get_file_status('config.yaml'),
        'gemini': get_file_status('gemini_config.yaml'),
        'secrets': get_file_status('secrets.yaml')
    }
    return render_template('index.html', status=status, show_nag=not NAG_ACCEPTED)

@app.route('/accept_nag', methods=['POST'])
def accept_nag():
    global NAG_ACCEPTED
    NAG_ACCEPTED = True
    return jsonify({'status': 'ok'})

@app.route('/readme')
def readme():
    content = "README.md not found."
    if os.path.exists(README_PATH):
        with open(README_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
    return render_template('readme.html', content=content)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(STATIC_DIR, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/docs/<path:filename>')
def serve_docs(filename):
    docs_dir = os.path.join(PROJECT_ROOT, 'docs')
    return send_from_directory(docs_dir, filename)

DEFAULT_GEMINI_CONFIG = {
    'gemini_api_key': 'YOUR_GEMINI_API_KEY_HERE',
    'model_name': 'gemma-3-27b-it',
    'ai_settings': {
        'enable_ai_search': True,
        'let_ai_guess_answer': True,
        'application_match_threshold': 70,
        'batch_size': 5,
        'user_prompt_timeout_seconds': 10,
        'api_retry_attempts': 3,
        'api_retry_backoff_seconds': 2,
        'max_applications': 25,
        'ban_safe': True
    }
}

@app.route('/edit/gemini', methods=['GET'])
def edit_gemini():
    if not os.path.exists(GEMINI_CONFIG):
        config = DEFAULT_GEMINI_CONFIG
    else:
        with open(GEMINI_CONFIG, 'r', encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
            config = recursive_merge(DEFAULT_GEMINI_CONFIG, user_config)

    if 'ai_settings' in config and 'work_dir' in config['ai_settings']:
        config['ai_settings'].pop('work_dir', None)

    return render_template('gemini_config.html', config=config, settings_metadata=AI_SETTINGS_METADATA)

@app.route('/save/gemini', methods=['POST'])
def save_gemini():  
    if os.path.exists(GEMINI_CONFIG):
        with open(GEMINI_CONFIG, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = DEFAULT_GEMINI_CONFIG.copy()

    api_key = request.form.get('gemini_api_key', '').strip()
    if api_key and api_key != '***Loaded***':
        config['gemini_api_key'] = api_key

    model_choice = request.form.get('model_name')
    if model_choice == 'custom':
        config['model_name'] = request.form.get('custom_model_name', '').strip()
    else:
        config['model_name'] = model_choice

    if 'ai_settings' not in config:
        config['ai_settings'] = {}

    for key, val in config['ai_settings'].items():
        form_key = f"ai_setting_{key}"
        if isinstance(val, bool):
            config['ai_settings'][key] = (form_key in request.form)
        elif isinstance(val, int):
             if form_key in request.form:
                 try:
                     config['ai_settings'][key] = int(request.form[form_key])
                 except:
                     pass
        elif isinstance(val, str):
            if form_key in request.form:
                config['ai_settings'][key] = request.form[form_key]

    if 'work_dir' in config['ai_settings']:
        config['ai_settings'].pop('work_dir', None)

    with open(GEMINI_CONFIG, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, sort_keys=False)

    mark_verified('gemini_config.yaml')
    return redirect(url_for('index'))

AI_SETTINGS_METADATA = {
    'enable_ai_search': {
        'label': 'Enable AI Search', 
        'tooltip': 'If enabled, the bot will analyze your resume/profile to search for relevant jobs in addition to the ones manually formatted in <a href="/edit/config/job" style="color: #4CAF50; text-decoration: underline;">Job Search Parameters</a>.',
        'order': 1
    },
    'let_ai_guess_answer': {
        'label': 'Let AI Guess Answer', 
        'tooltip': 'If checked, the bot is smarter and can handle unique questions but uses Gemini API (costing tokens). Unchecking may save API calls but the bot may not be able to answer questions other than those defined.', 
        'order': 2
    },
    'application_match_threshold': {
        'label': 'Job Suitability Threshold', 
        'tooltip': 'The minimum score (0-100) a job must receive from the AI to be considered a "Match" for submitting application. Jobs below this score are skipped.',
        'order': 3
    },
    'batch_size': {
        'label': 'Batch Size', 
        'tooltip': 'Number of jobs to analyze in a single AI request. Higher values are faster but consume more context window.',
        'order': 4
    },
    'user_prompt_timeout_seconds': {
        'label': 'User Prompt Timeout', 
        'tooltip': 'Seconds to wait for user input (if interactive mode is on) before taking default action.',
        'order': 5
    },
    'api_retry_attempts': {
        'label': 'API Retry Attempts', 
        'tooltip': 'Number of times to retry a failed AI API call before giving up.',
        'order': 6
    },
    'api_retry_backoff_seconds': {
        'label': 'API Retry Backoff', 
        'tooltip': 'Seconds to wait between API retries.',
        'order': 7
    },

    'max_applications': {
        'label': 'Max Applications', 
        'tooltip': 'Maximum number of applications the bot is allowed to submit in a single session/day.',
        'order': 9
    },
    'ban_safe': {
        'label': 'Ban Safe Mode', 
        'tooltip': 'If enabled, strictly enforces the Max Applications limit and adds extra delays to prevent LinkedIn account restrictions. "Max Applications" defines the limit, "Ban Safe" enforces it rigorously.',
        'order': 10
    }
}

# Default Configuration Structures
DEFAULT_JOB_CONFIG = {
    'remote': False,
    'lessthanTenApplicants': False,
    'experienceLevel': {
        'internship': False,
        'entry': False,
        'associate': True,
        'mid-senior level': True,
        'director': True,
        'executive': True
    },
    'jobTypes': {
        'full-time': True,
        'contract': True,
        'part-time': True,
        'temporary': True,
        'internship': False,
        'other': False,
        'volunteer': False
    },
    'date': {
        'all time': False,
        'month': False,
        'week': True,
        '24 hours': False
    },
    'positions': [],
    'locations': [],
    'residentStatus': False,
    'distance': 100,
    'outputFileDirectory': './work/',
    'companyBlacklist': [],
    'titleBlacklist': [],
    'posterBlacklist': [],
    'uploads': {'resume': ''},
    'checkboxes': {
        'driversLicence': True,
        'requireVisa': True,
        'legallyAuthorized': False,
        'certifiedProfessional': True,
        'urgentFill': False,
        'commute': True,
        'remote': True,
        'drugTest': True,
        'assessment': True,
        'backgroundCheck': True,
        'hybrid': True,
        'Australian citizen': False,
        'Canadian citizen': False,
        'Chinese citizen': False,
        'EU citizen': False,
        'GCC citizen': False,
        'Russian citizen': False,
        'UK citizen': False,
        'US citizen': False
    },
    'universityGpa': "",
    'degreeCompleted': [],
    'salaryMinimum': "",
    'languages': {'english': 'Native or bilingual'},
    'noticePeriod': "",
    'experience': {'default': ""},
    'eeo': {}
}

@app.route('/edit/config/job', methods=['GET'])
def edit_job_config():
    if not os.path.exists(JOB_CONFIG):
        config = DEFAULT_JOB_CONFIG.copy()
    else:
        with open(JOB_CONFIG, 'r', encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
            config = recursive_merge(DEFAULT_JOB_CONFIG, user_config)

    # Verify resume existence logic
    uploads = config.get('uploads') or {}
    resume_path = uploads.get('resume', '')
    status = {'valid_resume': os.path.exists(resume_path) if resume_path else False}

    return render_template('job_config.html', config=config, status=status)

@app.route('/upload/resume', methods=['POST'])
def upload_resume_ajax():
    if os.path.exists(JOB_CONFIG):
        with open(JOB_CONFIG, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = DEFAULT_JOB_CONFIG.copy()

    if 'resume_file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file part'}), 400
        
    file = request.files['resume_file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'}), 400
        
    if file:
        # Create work dir
        data_dir = os.path.join(PROJECT_ROOT, 'work')
        if not os.path.exists(data_dir): os.makedirs(data_dir)
        
        # Save file
        filename = 'resume.pdf' 
        save_path = os.path.join(data_dir, filename)
        file.save(save_path)
        
        # Update config path
        if 'uploads' not in config: config['uploads'] = {}
        config['uploads']['resume'] = save_path
        
        # Save config
        with open(JOB_CONFIG, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, sort_keys=False)
            
        mark_verified('config.yaml')
        
        return jsonify({'status': 'success', 'path': save_path})
    
    return jsonify({'status': 'error', 'message': 'Unknown error'}), 500

@app.route('/save/config/job', methods=['POST'])
def save_job_config():
    if os.path.exists(JOB_CONFIG):
        with open(JOB_CONFIG, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = DEFAULT_JOB_CONFIG.copy()

    form = request.form

    # 1. Booleans
    bool_keys = ['remote', 'lessthanTenApplicants', 'residentStatus']
    for k in bool_keys:
        config[k] = (k in form)
    
    config['distance'] = int(form.get('distance', 100))

    # 2. Date Filter (Radio)
    target_date = form.get('date_filter', 'week')
    for k in config['date']:
        config['date'][k] = (k == target_date)

    # 3. Deep Dicts - Experience & JobType
    if 'experienceLevel' in config:
        for level in config['experienceLevel']:
            form_key = f"exp_level_{level}"
            config['experienceLevel'][level] = (form_key in form)

    if 'jobTypes' in config:
        for jtype in config['jobTypes']:
            form_key = f"job_type_{jtype}"
            config['jobTypes'][jtype] = (form_key in form)

    # 4. Text Areas (Lists)
    def parse_list(text):
        return [line.strip() for line in text.split('\n') if line.strip()]

    config['positions'] = parse_list(form.get('positions', ''))
    config['locations'] = parse_list(form.get('locations', ''))
    config['companyBlacklist'] = parse_list(form.get('companyBlacklist', ''))
    config['titleBlacklist'] = parse_list(form.get('titleBlacklist', ''))
    config['posterBlacklist'] = parse_list(form.get('posterBlacklist', ''))

    # 5. Simple Values
    config['salaryMinimum'] = form.get('salaryMinimum', '').strip()
    config['noticePeriod'] = form.get('noticePeriod', '').strip()
    config['universityGpa'] = form.get('universityGpa', '').strip()
    config['degreeCompleted'] = request.form.getlist('degreeCompleted')

    # 6. Checkboxes (Massive list)
    if 'checkboxes' in config:
        for k, v in config['checkboxes'].items():
            if isinstance(v, bool):
                config['checkboxes'][k] = (f"chk_{k}" in form)

    # 7. File Upload (Resume)
    if 'resume_file' in request.files:
        file = request.files['resume_file']
        if file and file.filename:
            # Create work dir
            data_dir = os.path.join(PROJECT_ROOT, 'work')
            if not os.path.exists(data_dir): os.makedirs(data_dir)
            
            # Save file
            filename = 'resume.pdf' # Force name for simplicity or use file.filename
            save_path = os.path.join(data_dir, filename)
            file.save(save_path)
            
            config['uploads']['resume'] = save_path

    # 8. Languages
    target_langs = ['english', 'arabic', 'french', 'german', 'chinese', 'korean', 'japanese']
    if 'languages' not in config: config['languages'] = {}
    
    for lang in target_langs:
        val = form.get(f'lang_{lang}')
        if val:
            config['languages'][lang] = val

    # Save
    with open(JOB_CONFIG, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, sort_keys=False)

    mark_verified('config.yaml')
    return redirect(url_for('index'))

# Default Secrets Structure
DEFAULT_SECRETS = {
    'email': "",
    'password': "",
    'personalInfo': {
        'Pronouns': "",
        'First Name': "",
        'Last Name': "",
        'Phone Country Code': "",
        'Mobile Phone Number': "",
        'Street address': "",
        'City': "",
        'State': "",
        'Zip': "",
        'Country': "",
        'Linkedin': "",
        'Website': ""
    }
}

@app.route('/edit/config/secrets', methods=['GET'])
def edit_secrets_config():
    if not os.path.exists(SECRETS_CONFIG):
        config = DEFAULT_SECRETS.copy()
    else:
        with open(SECRETS_CONFIG, 'r', encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
            config = recursive_merge(DEFAULT_SECRETS, user_config)

    return render_template('secrets_config.html', config=config)

@app.route('/save/config/secrets', methods=['POST'])
def save_secrets_config():
    if os.path.exists(SECRETS_CONFIG):
         with open(SECRETS_CONFIG, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = DEFAULT_SECRETS.copy()
    
    # 1. Login
    config['email'] = request.form.get('email', '').strip()
    config['password'] = request.form.get('password', '').strip()
    
    # 2. Personal Info
    if 'personalInfo' not in config:
        config['personalInfo'] = {}
        
    info_keys = ['Pronouns', 'First Name', 'Last Name', 'Phone Country Code', 
                 'Mobile Phone Number', 'Street address', 'City', 
                 'State', 'Zip', 'Country', 'Linkedin', 'Website']
                 
    for key in info_keys:
        val = request.form.get(f'info_{key}', '').strip()
        config['personalInfo'][key] = val

    with open(SECRETS_CONFIG, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, sort_keys=False)

    mark_verified('secrets.yaml')
    return redirect(url_for('index'))

@app.route('/edit/config/<filename>', methods=['GET'])
def edit_config(filename):
    # Route config.yaml to new editor
    if filename == 'config.yaml':
        return redirect(url_for('edit_job_config'))
    
    # Route secrets.yaml to new editor
    if filename == 'secrets.yaml':
        return redirect(url_for('edit_secrets_config'))

    return "Invalid file", 400

@app.route('/save/generic/<filename>', methods=['POST'])
def save_generic(filename):
    if filename not in ['config.yaml', 'secrets.yaml']:
        return "Invalid file", 400
    
    content = request.form.get('file_content')
    # Validate YAML
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        return f"Invalid YAML: {e}", 400

    target = os.path.join(CONFIG_DIR, filename)
    with open(target, 'w', encoding='utf-8') as f:
        f.write(content)
        
    mark_verified(filename)
    return redirect(url_for('index'))

@app.route('/intelligence')
def intelligence_dashboard():
    return render_template('intelligence.html')

try:
    from app.defaults import DEFAULT_SEEDS
except ImportError:
    from defaults import DEFAULT_SEEDS

@app.route('/api/intelligence/seeds', methods=['GET'])
def get_seeds():
    work_dir = os.path.join(PROJECT_ROOT, 'work')
    seeds_path = os.path.join(work_dir, "ml_seeds.json")
    qa_cache_path = os.path.join(work_dir, "qa_cache.json")
    
    seeds = []

    # 1. Load Seeds
    if os.path.exists(seeds_path):
        try:
           with open(seeds_path, 'r', encoding='utf-8') as f:
               seeds = json.load(f)
        except:
           pass
    
    if not seeds:
        seeds = DEFAULT_SEEDS
        if not os.path.exists(work_dir): os.makedirs(work_dir)
        try:
            with open(seeds_path, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_SEEDS, f, indent=2)
        except:
            pass

    # 2. Filter (Show only Learned + Custom Rules)
    default_questions = set(s[0] for s in DEFAULT_SEEDS)
    
    display_seeds = [s for s in seeds if s[0] not in default_questions]

    # 3. Merge Learned Answers (QA Cache)
    if os.path.exists(qa_cache_path):
        try:
            with open(qa_cache_path, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                
          
            existing_questions = set(s[0] for s in seeds) # Check against ALL saved seeds to avoid duplicates
            
            for q, a in cache.items():
                if q not in existing_questions:
                    # Add learned answer
                    display_seeds.append([q, f"raw:{a}"])
        except Exception as e:
            print(f"Error loading qa_cache: {e}")
        
    return jsonify(display_seeds)

@app.route('/api/intelligence/seeds', methods=['POST'])
def save_seeds():
    try:
        new_seeds = request.json
        if not isinstance(new_seeds, list):
            return jsonify({'error': 'Invalid format'}), 400
            
        work_dir = os.path.join(PROJECT_ROOT, 'work')
        if not os.path.exists(work_dir): os.makedirs(work_dir)
        seeds_path = os.path.join(work_dir, "ml_seeds.json")
        
        with open(seeds_path, 'w', encoding='utf-8') as f:
            json.dump(new_seeds, f, indent=2)
            
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/intelligence/instruction', methods=['GET', 'POST'])
def handle_instruction():
    if not os.path.exists(GEMINI_CONFIG):
         return jsonify({'instruction': ''})

    if request.method == 'GET':
        try:
            with open(GEMINI_CONFIG, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
            
            settings = config.get('ai_settings', {})
            return jsonify({'instruction': settings.get('custom_instruction_prompt', '')})
        except:
             return jsonify({'instruction': ''})

    if request.method == 'POST':
        try:
            data = request.json
            new_instruction = data.get('instruction', '').strip()
            
            with open(GEMINI_CONFIG, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
                
            if 'ai_settings' not in config: config['ai_settings'] = {}
            config['ai_settings']['custom_instruction_prompt'] = new_instruction
            
            with open(GEMINI_CONFIG, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, sort_keys=False)
                
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

@app.route('/validate', methods=['GET'])
def validate_configs():
    missing = []
    if not os.path.exists(JOB_CONFIG):
        missing.append('Job Search Parameters (config.yaml)')
    if not os.path.exists(GEMINI_CONFIG):
        missing.append('AI Model Settings (gemini_config.yaml)')
    if not os.path.exists(SECRETS_CONFIG):
        missing.append('Secret Credentials (secrets.yaml)')
    
    return jsonify({'valid': len(missing) == 0, 'missing': missing})

@app.route('/dashboard')
def open_dashboard():
    global DASHBOARD_PROCESS

    if DASHBOARD_PROCESS is None or DASHBOARD_PROCESS.poll() is not None:

        dashboard_path = os.path.join(BASE_DIR, 'dashboard.py')
        print(f"Launching dashboard from: {dashboard_path}")

        DASHBOARD_PROCESS = subprocess.Popen(
            ["streamlit", "run", "dashboard.py", "--server.headless=true"], 
            cwd=BASE_DIR,
            shell=True 
        )

        time.sleep(2)
        
    return redirect("http://localhost:8501")

SCOUT_DASHBOARD_PROCESS = None

@app.route('/scout_dashboard')
def open_scout_dashboard():
    global SCOUT_DASHBOARD_PROCESS

    if SCOUT_DASHBOARD_PROCESS is None or SCOUT_DASHBOARD_PROCESS.poll() is not None:

        dashboard_path = os.path.join(BASE_DIR, 'scout_dashboard.py')
        print(f"Launching Scout Dashboard from: {dashboard_path}")

        SCOUT_DASHBOARD_PROCESS = subprocess.Popen(
            ["streamlit", "run", "scout_dashboard.py", "--server.port=8502", "--server.headless=true"], 
            cwd=BASE_DIR,
            shell=True 
        )

        time.sleep(2)
        
    return redirect("http://localhost:8502")

@app.route('/run', methods=['POST'])
def run_bot():

    with open(SIGNAL_FILE, 'w') as f:
        f.write("done")
    return jsonify({'status': 'success', 'message': 'Bot starting in background'})

SCOUT_PROCESS = None

@app.route('/run_scout', methods=['POST'])
def run_scout():
    # Signal main process to run ScoutBot
    with open(SIGNAL_FILE, 'w') as f:
        f.write("scout")
    return jsonify({'status': 'success', 'message': 'Scout Mode starting...'})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    if os.path.exists(BOT_STATUS_FILE):
        try:
            os.remove(BOT_STATUS_FILE)
            return jsonify({'status': 'success', 'message': 'Stop signal sent.'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    return jsonify({'status': 'ignored', 'message': 'Bot not running.'})

@app.route('/reset/scout', methods=['POST'])
def reset_scout_data():
    try:
        from app.bot.database import JobDatabase
        work_dir = os.path.join(PROJECT_ROOT, "work")
        db = JobDatabase(work_dir)
        if db.clear_scout_table():
            return jsonify({'status': 'success', 'message': 'Scout data cleared.'})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to clear data.'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/get_logs')
def get_logs():
    log_path = os.path.join(PROJECT_ROOT, "work", "bot.log")
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                return jsonify({'logs': lines[-500:]}) # Return last 500 lines
        except Exception as e:
            return jsonify({'logs': [f"Error reading logs: {str(e)}"]})
    return jsonify({'logs': []})

@app.route('/shutdown', methods=['POST'])
def shutdown():
    print("Shutting down server...")
    
    with open(SIGNAL_FILE, 'w') as f:
        f.write("abort")
        
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()
    
    def force_exit():
        time.sleep(1)
        os._exit(0)
    
    threading.Thread(target=force_exit).start()
    return "Server shutting down..."

def run_server():
    print("DEBUG: Executing app.run()")
    app.run(port=5001, debug=False, use_reloader=False, threaded=True)

def run_configuration_wizard():
    print("Starting Configuration UI on http://localhost:5001")
    if os.path.exists(SIGNAL_FILE):
        try:
            os.remove(SIGNAL_FILE)
        except:
            pass

    subprocess.Popen([sys.executable, os.path.abspath(__file__)], 
                     cwd=os.path.dirname(os.path.abspath(__file__)))
    
def wait_for_user():
    print("Waiting for user to complete configuration...")
    while not os.path.exists(SIGNAL_FILE):
        time.sleep(1)
    
    status = "done"
    try:
        with open(SIGNAL_FILE, 'r') as f:
            status = f.read().strip()
        
        if status == 'abort':
            print("\nUser aborted via Nag Screen. Exiting...")
            sys.exit(0)
    except Exception as e:
        pass
    
    # Returning status so run.py knows what to do (done vs scout)
    time.sleep(2) 
    return status

if __name__ == '__main__':
    run_server() 
