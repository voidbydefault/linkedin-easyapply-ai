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
    user_data_dir = os.path.join(os.getcwd(), "work", "chrome_bot")
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



def ask_user_in_browser(browser, title, question, timeout=3):
    """
    Injects a modal into the browser and waits for user response or timeout.
    Returns True if Yes, False if No (or timeout).
    """
    modal_script = f"""
    var modal = document.createElement('div');
    modal.id = 'gen-modal';
    modal.style.position = 'fixed';
    modal.style.zIndex = '10000';
    modal.style.left = '0';
    modal.style.top = '0';
    modal.style.width = '100%';
    modal.style.height = '100%';
    modal.style.overflow = 'auto';
    modal.style.backgroundColor = 'rgba(0,0,0,0.8)';
    modal.style.display = 'flex';
    modal.style.justifyContent = 'center';
    modal.style.alignItems = 'center';
    modal.style.color = 'white';
    modal.style.fontFamily = 'Arial, sans-serif';
    
    var content = document.createElement('div');
    content.style.backgroundColor = '#333';
    content.style.padding = '20px';
    content.style.borderRadius = '10px';
    content.style.textAlign = 'center';
    content.style.border = '2px solid #555';
    
    var h2 = document.createElement('h2');
    h2.innerText = '{title}';
    content.appendChild(h2);
    
    var p = document.createElement('p');
    p.innerText = '{question}';
    p.style.fontSize = '18px';
    p.style.marginBottom = '20px';
    content.appendChild(p);
    
    var btnContainer = document.createElement('div');
    
    var yesBtn = document.createElement('button');
    yesBtn.innerText = 'Yes (Regenerate)';
    yesBtn.style.padding = '10px 20px';
    yesBtn.style.fontSize = '16px';
    yesBtn.style.margin = '10px';
    yesBtn.style.cursor = 'pointer';
    yesBtn.style.backgroundColor = '#4CAF50';
    yesBtn.style.color = 'white';
    yesBtn.style.border = 'none';
    yesBtn.onclick = function() {{ window.userChoice = 'yes'; }};
    
    var noBtn = document.createElement('button');
    noBtn.innerText = 'No (Default ' + {timeout} + 's)';
    noBtn.style.padding = '10px 20px';
    noBtn.style.fontSize = '16px';
    noBtn.style.margin = '10px';
    noBtn.style.cursor = 'pointer';
    noBtn.style.backgroundColor = '#f44336';
    noBtn.style.color = 'white';
    noBtn.style.border = 'none';
    noBtn.onclick = function() {{ window.userChoice = 'no'; }};
    
    btnContainer.appendChild(yesBtn);
    btnContainer.appendChild(noBtn);
    content.appendChild(btnContainer);
    modal.appendChild(content);
    document.body.appendChild(modal);
    
    window.userChoice = null;
    var timeLeft = {timeout};
    var timer = setInterval(function() {{
        timeLeft--;
        noBtn.innerText = 'No (Default ' + timeLeft + 's)';
        if (timeLeft <= 0) {{
            clearInterval(timer);
            if (window.userChoice === null) window.userChoice = 'no';
        }}
    }}, 1000);
    """
    
    try:
        browser.execute_script(modal_script)
        
        while True:
            choice = browser.execute_script("return window.userChoice;")
            if choice:
                # Cleanup
                browser.execute_script("document.getElementById('gen-modal').remove();")
                return choice == 'yes'
            time.sleep(0.5)
            
    except Exception as e:
        print(f"Error in browser interaction: {e}")
        return False

if __name__ == '__main__':
    try:
        # Cleanup stale status
        if os.path.exists(os.path.join("config", ".bot_active")):
            try:
                os.remove(os.path.join("config", ".bot_active"))
            except: pass

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

        # Loop to allow Starting/Stopping without killing script
        BOT_STATUS_FILE = os.path.join("config", ".bot_active")
        
        while True:
            print("\nWaiting for user to click 'Run Bot' in dashboard...")
            
            # Ensure signal is clear before waiting
            if os.path.exists(config_ui.SIGNAL_FILE):
                os.remove(config_ui.SIGNAL_FILE)
                
            config_ui.wait_for_user()
            print("Configuration Complete. Starting Bot...")

            # --- SEPARATE TAB LOGIC ---
            bot_tab = None
            main_tab = browser.current_window_handle
            
            try:
                print("Opening new tab for Bot execution...")
                browser.execute_script("window.open('about:blank', '_blank');")
                time.sleep(1)
                
                new_handles = browser.window_handles
                if len(new_handles) > 1:
                    bot_tab = new_handles[-1]
                    browser.switch_to.window(bot_tab)
                    print(" -> Switched to new tab.")
                else:
                    print(" -> Warning: Tab count did not increase.")
            except Exception as e:
                print(f"Warning: Could not open new tab ({e}).")

            # ---------------------------

            print("Initializing Bot...")
            
            try:
                # Create status file to signal UI
                with open(BOT_STATUS_FILE, 'w') as f:
                    f.write("running")

                # Load config
                params, ai_params = load_config()

                # --- PRE-RUN BROWSER CHECKS ---
                work_dir = ai_params['ai_settings'].get('work_dir', './work')
                if not os.path.exists(work_dir): os.makedirs(work_dir)

                # 1. Check Profile
                profile_path = os.path.join(work_dir, "user_profile.txt")
                if os.path.exists(profile_path):
                    if ask_user_in_browser(browser, "Profile Found", "Existing user_profile.txt found. Regenerate?", timeout=3):
                        os.remove(profile_path)
                        print(" -> User requested profile regeneration.")
                    else:
                        print(" -> Using existing profile.")

                # 2. Check Positions
                positions_path = os.path.join(work_dir, "ai_positions.txt")
                if os.path.exists(positions_path):
                     if ask_user_in_browser(browser, "Positions List Found", "Existing ai_positions.txt found. Regenerate?", timeout=3):
                        os.remove(positions_path)
                        print(" -> User requested positions regeneration.")
                     else:
                        print(" -> Using existing positions.")
                # ------------------------------

                # 1. AI Setup & Profile Generation
                ai_handler = AIHandler(ai_params)
                print("\n--- Profile Setup ---")

                profile_text = ai_handler.generate_user_profile(params['uploads']['resume'], config_params=params)

                # 2. Position Selection Logic
                final_positions = params['positions']
                if ai_params['ai_settings'].get('enable_ai_search'):
                    print("\n--- Position Selection (AI Enabled) ---")
                    ai_pos = ai_handler.generate_positions(profile_text)
                    print(f"AI Suggestions: {ai_pos}")
                    
                    combined_positions = list(set(final_positions + ai_pos))
                    print(f"Manual Config:  {final_positions}")
                    print(f"Combined List:  {combined_positions}")
                    final_positions = combined_positions
                else:
                    print(f"\n--- Position Selection (Manual) ---")
                    print(f"Targeting: {final_positions}")

                # 4. Usage Check
                curr_usage, max_rpd = ai_handler.get_usage_stats()
                if curr_usage > 0:
                    print(f"\n[API Usage: {curr_usage}/{max_rpd}]")

                print(f"\nTargeting {len(final_positions)} positions.")
                
                # 4. Start Bot
                bot = LinkedinEasyApply(params, browser, ai_params, ai_handler, profile_text, final_positions)
                bot.login()
                bot.start_applying()
                
            except Exception as e:
                print(f"Bot execution stopped: {e}")
                
            finally:
                # Clean up status file
                if os.path.exists(BOT_STATUS_FILE):
                    os.remove(BOT_STATUS_FILE)
                    print(" -> Bot Status: Stopped.")
                
                # Cleanup Tab
                try:
                    if len(browser.window_handles) > 1:
                        # Close current tab if we are in it
                        # Or close the bot_tab specifically
                        if bot_tab and bot_tab in browser.window_handles:
                            browser.switch_to.window(bot_tab)
                            browser.close()
                        
                        # Switch back to main
                        browser.switch_to.window(main_tab)
                except Exception as ex:
                    print(f"Tab cleanup error: {ex}")

    except Exception as e:
        print(f"\nCRITICAL ERROR: {e}")
        # import traceback
        # traceback.print_exc()
        input("Press Enter to exit...")