import os
import sys
import time
import yaml
import undetected_chromedriver as uc

from app.bot.bot import LinkedinEasyApply
from app.ai_handler import AIHandler

def init_browser():
    browser_options = uc.ChromeOptions()

    # Standard arguments
    options = [
        '--disable-blink-features',
        '--no-sandbox',
        '--start-maximized',
        '--disable-extensions',
        '--ignore-certificate-errors',
        '--disable-blink-features=AutomationControlled'
    ]
    for opt in options:
        browser_options.add_argument(opt)

    # Restore session (Profile Persistence)
    user_data_dir = os.path.join(os.getcwd(), "data", "chrome_bot")
    browser_options.add_argument(f"--user-data-dir={user_data_dir}")

    driver = uc.Chrome(options=browser_options, version_main=None)

    driver.implicitly_wait(4)
    driver.maximize_window()
    return driver

def validate_data(params):
    """
    Strictly validates that all necessary data exists before starting.
    """
    errors = []

    # 1. Credentials
    if not params.get('email') or not params.get('password'):
        errors.append("MISSING: 'email' or 'password' in secrets.yaml")

    # 2. Personal Info Structure
    p_info = params.get('personalInfo', {})
    if not p_info:
        errors.append("MISSING: 'personalInfo' block in secrets.yaml")
    else:
        required_keys = ['First Name', 'Last Name', 'Mobile Phone Number', 'Phone Country Code']
        for k in required_keys:
            if not p_info.get(k):
                errors.append(f"MISSING: '{k}' inside 'personalInfo' (secrets.yaml)")

    # 3. File Paths
    uploads = params.get('uploads', {})
    resume_path = uploads.get('resume')
    if not resume_path:
        errors.append("MISSING: 'resume' path in config/config.yaml")
    elif not os.path.exists(resume_path):
        errors.append(f"FILE NOT FOUND: Resume at path '{resume_path}'")

    # 4. Search Parameters
    if not params.get('positions') or len(params['positions']) == 0:
        errors.append("MISSING: 'positions' list is empty in config.yaml")

    if not params.get('locations') or len(params['locations']) == 0:
        errors.append("MISSING: 'locations' list is empty in config/config.yaml")

    if errors:
        print("\n--- CONFIGURATION ERRORS ---")
        for err in errors:
            print(f"[X] {err}")
        print("----------------------------")
        raise Exception("Configuration validation failed. Please fix the errors above.")

    print(" -> Configuration Validated.")


def load_config():
    # 1. Load Main Config
    if not os.path.exists("config/config.yaml"):
        raise Exception("config/config.yaml not found.")
    with open("config/config.yaml", 'r', encoding='utf-8') as f:
        params = yaml.safe_load(f)

    # 2. Load Secrets (Credentials + Personal Info)
    if os.path.exists("config/secrets.yaml"):
        with open("config/secrets.yaml", 'r', encoding='utf-8') as f:
            secrets = yaml.safe_load(f)
            if secrets:
                params.update(secrets)
                print(" -> Success: Data loaded from secrets.yaml")
    else:
        print(" -> Notice: secrets.yaml not found.")

    # 3. Run Validation
    validate_data(params)

    # 4. Load AI Config
    if not os.path.exists("config/gemini_config.yaml"):
        raise Exception("config/gemini_config.yaml not found.")
    with open("config/gemini_config.yaml", 'r', encoding='utf-8') as f:
        ai_params = yaml.safe_load(f)

    return params, ai_params


if __name__ == '__main__':
    try:
        print("\n" + "!" * 60)
        print("PLEASE DO NOT ABUSE THE LINKEDIN PLATFORM OR THIS TOOL.")
        print("RESPONSIBLE USE IS REQUIRED TO AVOID ACCOUNT RESTRICTIONS.")
        print("!" * 60 + "\n")

        # --- NEW CONFIGURATION UI ---
        # 1. Launch Config UI and Browser
        import app.config_ui as config_ui
        
        # Start Flask Server
        config_ui.run_configuration_wizard()

        print("Launching Browser for Configuration...")
        # We init the browser early to show the config page
        browser = init_browser()
        
        # Open Config Page
        browser.get("http://localhost:5001")

        print("Waiting for user to complete configuration in browser...")
        config_ui.wait_for_user()
        print("Configuration Complete. Proceeding...")
        # ----------------------------

        print("Initializing Bot...")
        # NOW we load config, after user had a chance to fix it
        params, ai_params = load_config()

        # 1. AI Setup & Profile Generation
        ai_handler = AIHandler(ai_params)
        print("\n--- Profile Setup ---")

        # PASSING FULL PARAMS (Config + Secrets) to AI Handler to build the "Super Profile"
        profile_text = ai_handler.generate_user_profile(params['uploads']['resume'], config_params=params)



        # 2. Position Selection Logic
        final_positions = params['positions']
        if ai_params['ai_settings'].get('enable_ai_search'):
            print("\n--- Position Selection ---")
            ai_pos = ai_handler.generate_positions(profile_text)
            print(f"\nAI Suggestions: {ai_pos}")
            print(f"Manual Config:  {params['positions']}")

            choice = ai_handler.prompt_user(
                "\n[3/4] Use [a] AI-generated, [m] Manual (config.yaml), or [c] Combined?",
                valid_keys=['a', 'm', 'c'],
                default='m'
            )

            if choice == 'a':
                final_positions = ai_pos
            elif choice == 'c':
                final_positions = list(set(final_positions + ai_pos))
            else:
                pass # Use params['positions']

        # 4. Usage Reset Check (Last)
        curr_usage, max_rpd = ai_handler.get_usage_stats()
        if curr_usage > 0:
            print(f"\n[API Usage: {curr_usage}/{max_rpd}]")
            choice = ai_handler.prompt_user(
                "[4/4] Reset daily counter? (Use 'y' if Gemini has reset daily limit)",
                valid_keys=['y', 'n'],
                default='n'
            )
            if choice == 'y':
                ai_handler.reset_usage()

        print(f"\nTargeting {len(final_positions)} positions.")
        print("-" * 50)
        print("NOTE: API Optimizations are ACTIVE.")
        print("1. Job scanning is batched (up to 10 jobs/scan).")
        print("2. Answers are cached & learned locally.")
        print("   -> Bot gets smarter over time. Do NOT delete 'work/qa_cache.json'.")
        print("-" * 50)
        # print("Launching Browser in 3 seconds...")
        # time.sleep(3)

        # 3. Launch Browser
        # browser = init_browser()  <-- Already launched for UI

        # 4. Start Bot
        bot = LinkedinEasyApply(params, browser, ai_params, ai_handler, profile_text, final_positions)
        bot.login()
        bot.start_applying()

    except Exception as e:
        print(f"\nCRITICAL ERROR: {e}")
        # import traceback
        # traceback.print_exc()
        input("Press Enter to exit...")