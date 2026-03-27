import csv
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, timedelta
from itertools import product

# Selenium imports
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Local modules
from .database import JobDatabase
from .forms import ApplicationForm
from .utils import human_sleep, smart_click, scroll_slow, simulate_reading

class LinkedinEasyApply:
    def __init__(self, parameters, driver, ai_config, ai_handler_instance, user_profile, active_positions):
        self.browser = driver
        self.email = parameters['email']
        self.password = parameters['password']
        self.disable_lock = parameters.get('disableAntiLock', False)
        self.company_blacklist = parameters.get('companyBlacklist', []) or []
        self.title_blacklist = parameters.get('titleBlacklist', []) or []
        self.poster_blacklist = parameters.get('posterBlacklist', []) or []
        self.positions = active_positions
        self.locations = parameters.get('locations', [])
        self.residency = parameters.get('residentStatus', False)
        self.base_search_url = self.get_base_search_url(parameters)

        # AI Config
        self.ai_settings = ai_config['ai_settings']
        self.work_dir = os.path.join(os.getcwd(), 'work')
        if not os.path.exists(self.work_dir): os.makedirs(self.work_dir)

        # Initialize Database
        self.db = JobDatabase(self.work_dir)

        self.unified_log_file = os.path.join(self.work_dir, "application_log.csv")
        self.state_file = os.path.join(self.work_dir, "daily_state.json")
        self.ensure_log_file_exists()

        # Safe mechanism
        self.max_apps = self.ai_settings.get('max_applications', 50)
        self.ban_safe = self.ai_settings.get('ban_safe', False)

        if self.ban_safe:
            self.daily_count = self.load_daily_state()
            print(f"Daily Limit Check: {self.daily_count}/{self.max_apps} applications today.")
            if self.daily_count >= self.max_apps:
                print("!!! BAN SAFE TRIGGERED !!!")
                print(
                    f"You have already applied to {self.daily_count} jobs today. Exiting to prevent account restrictions.")
                raise Exception("BAN_SAFE_TRIGGERED")
        else:
            self.daily_count = 0

        self.ai_handler = ai_handler_instance
        self.user_profile_text = user_profile
        self.application_match_threshold = self.ai_settings.get('application_match_threshold', 70)
        
        # Initialize Form Filler
        # We pass 'parameters' as the config dictionary expected by ApplicationForm
        self.form = ApplicationForm(self.browser, self.ai_handler, self.user_profile_text, parameters)
        self.form.setup_ai_config(self.ai_settings.get('let_ai_guess_answer', False))


    def load_daily_state(self):
        """Loads the daily application count from a JSON state file."""
        today_str = datetime.now().strftime("%Y-%m-%d")

        if not os.path.exists(self.state_file):
            return 0

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == today_str:
                    return data.get('count', 0)
                else:
                    return 0
        except Exception as e:
            print(f"Warning: Could not read state file ({e}). Resetting count to 0.")
            return 0

    def save_daily_state(self):
        """Saves the current date and count to the JSON state file."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        data = {
            "date": today_str,
            "count": self.daily_count
        }
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Warning: Could not save state file: {e}")

    def ensure_log_file_exists(self):
        if not os.path.exists(self.unified_log_file):
            with open(self.unified_log_file, 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(
                    ["Status", "Score", "Company", "Title", "Link", "Location", "Search Location", "Timestamp", "Reason"])
        else:
             # Migration: Add Reason if missing
            try:
                with open(self.unified_log_file, 'r', encoding='utf-8') as f:
                    header_line = f.readline().strip()
                
                if header_line and "Reason" not in header_line:
                    print("Migrating log file to include 'Reason' column...")
                    # Read all, add empty reason, write back
                    rows = []
                    with open(self.unified_log_file, 'r', encoding='utf-8') as f:
                        reader = csv.reader(f)
                        rows = list(reader)
                    
                    if rows:
                        rows[0].append("Reason") # Header
                        for r in rows[1:]:
                            r.append("") # Empty reason for old rows
                            
                        with open(self.unified_log_file, 'w', newline='', encoding='utf-8') as f:
                            writer = csv.writer(f)
                            writer.writerows(rows)
            except Exception as e:
                print(f"Log Migration Warning: {e}")

    def login(self):
        print("Checking session...")
        try:
            self.browser.get("https://www.linkedin.com/feed/")
            human_sleep(4.5, 1.0)

            page_src = self.browser.page_source.lower()
            if "sign in" in page_src or "join now" in page_src or "feed" not in self.browser.current_url:
                print("Session inactive. Proceeding to Login...")
                self.load_login_page_and_login()
            else:
                print("Session valid.")
        except TimeoutException:
            print("Timeout loading feed. Retrying login...")
            self.load_login_page_and_login()

    # --- 2FA / Security Check Helpers ---

    def _show_2fa_banner(self):
        """Injects a visible banner into the browser so the user knows the bot is waiting."""
        try:
            self.browser.execute_script("""
            if (!document.getElementById('bot-2fa-banner')) {
                var banner = document.createElement('div');
                banner.id = 'bot-2fa-banner';
                banner.style.cssText = 'position:fixed;top:0;left:0;width:100%;padding:14px;' +
                    'background:linear-gradient(135deg,#1a73e8,#0d47a1);color:white;text-align:center;' +
                    'z-index:99999;font-size:16px;font-family:Arial,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,0.3);';
                banner.innerText = '🤖 Bot is waiting for you to complete verification / 2FA. Take your time...';
                document.body.appendChild(banner);
            }
            """)
        except Exception:
            pass  # Page might not have a body yet

    def _remove_2fa_banner(self):
        """Removes the waiting banner after login succeeds."""
        try:
            self.browser.execute_script("""
            var el = document.getElementById('bot-2fa-banner');
            if (el) el.remove();
            """)
        except Exception:
            pass

    def _wait_for_login_completion(self, max_wait=300, poll_interval=3):
        """
        Polls the browser until the user lands on the LinkedIn feed.
        Handles all 2FA / verification / security-check flows by simply waiting
        instead of timing out after a fixed period.

        Args:
            max_wait: Maximum seconds to wait (default 5 minutes).
            poll_interval: Seconds between each check.
        """
        # Keywords that indicate an intermediate auth / verification page
        AUTH_KEYWORDS = ["challenge", "checkpoint", "two-step", "verification",
                         "two_step", "security", "authenticate", "uas"]

        elapsed = 0
        banner_shown = False

        while elapsed < max_wait:
            current_url = self.browser.current_url.lower()

            # Success: we reached the feed
            if "feed" in current_url:
                if banner_shown:
                    self._remove_2fa_banner()
                print("Login Successful.")
                return

            # Detect auth / verification pages and show banner
            if any(kw in current_url for kw in AUTH_KEYWORDS):
                if not banner_shown:
                    print("2FA / Verification detected. Waiting for user to complete it...")
                    banner_shown = True
                self._show_2fa_banner()
                if elapsed % 15 == 0 and elapsed > 0:
                    print(f"  Still waiting for 2FA... ({elapsed}s elapsed)")

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise Exception(
            f"Login timed out after {max_wait}s. "
            "2FA / verification may not have been completed in time."
        )

    def load_login_page_and_login(self):
        print("Loading Login Page...")
        self.browser.get("https://www.linkedin.com/login")
        human_sleep(2.5, 0.5)

        email_elem = None
        try:
            email_elem = WebDriverWait(self.browser, 10).until(EC.presence_of_element_located((By.ID, "username")))
        except:
            try:
                email_elem = self.browser.find_element(By.NAME, "session_key")
            except:
                print("CRITICAL: Could not find username field!")
                raise Exception("Login Error: Username field not found")

        print("Entering Credentials...")
        try:
            email_elem.click()
            email_elem.clear()
            email_elem.send_keys(self.email)
            human_sleep(0.8, 0.3)

            pass_elem = self.browser.find_element(By.ID, "password")
            pass_elem.click()
            pass_elem.clear()
            pass_elem.send_keys(self.password)
            human_sleep(0.8, 0.3)

            self.browser.find_element(By.CSS_SELECTOR, ".btn__primary--large").click()
        except Exception as e:
            print(f"Error entering credentials: {e}")
            raise Exception(f"Login Error: {e}")

        print("Verifying login...")
        self._wait_for_login_completion()

    def check_for_break(self):
        if not hasattr(self, 'apps_since_last_break'):
            self.apps_since_last_break = 0
            self.next_break_threshold = random.randint(7, 12)

        self.apps_since_last_break += 1

        if self.apps_since_last_break >= self.next_break_threshold:
            break_duration = random.randint(180, 420)
            print(f"\n--- ☕ Taking a random micro-break for {break_duration / 60:.1f} minutes... ---")
            for _ in range(break_duration):
                time.sleep(1) # Keep hard sleep for breaks, accurately measuring seconds
            print("--- Resuming work ---")
            self.apps_since_last_break = 0
            self.next_break_threshold = random.randint(7, 12)

        if not os.path.exists(os.path.join("config", ".bot_active")):
            raise Exception("STOP_SIGNAL")

    def perform_idle_action(self):
        """
        Performs a random non-invasive action to simulate human 'fidgeting' or reading.
        Uses pure Selenium (ActionChains) to avoid hijacking the global mouse.
        """
        try:
            action = random.choice(['scroll', 'hover', 'pause'])
            
            if action == 'scroll':
                # Scroll up or down a tiny amount
                scroll_amt = random.randint(-150, 150)
                self.browser.execute_script(f"window.scrollBy(0, {scroll_amt});")
                human_sleep(1.0, 0.5)
                
            elif action == 'hover':
                # Move to a random element on page safely
                try:
                    # Pick a safe visible element like a job card or header
                    elements = self.browser.find_elements(By.CSS_SELECTOR, ".job-card-list__title")
                    if elements:
                        target = random.choice(elements[:3]) # Top 3
                        # Use our non-invasive move
                        from .utils import human_mouse_move
                        human_mouse_move(self.browser, target).perform()
                except:
                    pass
                human_sleep(1.5, 0.5)
                
            elif action == 'pause':
                # Just wait
                human_sleep(2.0, 1.0)
                
        except Exception:
            pass

    def start_applying(self):
        searches = list(product(self.positions, self.locations))
        random.shuffle(searches)

        for (position, location) in searches:
            print(f"Starting search: {position} in {location}")
            location_url = "&location=" + location
            job_page = -1

            try:
                while True:
                    self.check_for_break()
                    job_page += 1
                    print(f"Processing Page {job_page}")
                    self.next_job_page(position, location_url, job_page)
                    human_sleep(3.0, 0.5)

                    self.apply_jobs(location)

                    if random.random() < 0.3:
                        print(" ... (Idle Fidgeting) ...")
                        self.perform_idle_action()

                    sleep_time = random.uniform(5, 10)
                    print(f"Page done. Resting {sleep_time:.1f}s...")
                    human_sleep(sleep_time, 2.0)

            except Exception as e:
                if str(e) == "No more jobs.":
                    print(f"Ending search for {position} in {location}: No more jobs found.")
                    break
                elif str(e) == "STOP_SIGNAL":
                    print("Bot stopped by user.")
                    return
                elif str(e) == "DAILY_LIMIT_REACHED":
                    print("Daily application limit reached. Stopping bot completely.")
                    return
                elif str(e) == "API_LIMIT_REACHED":
                    print("API Limit Reached. User notification triggered.")
                    try:
                        # 1. Show Alert
                        self.browser.execute_script("alert('Critical: API Limit Reached! The bot cannot continue. Press OK to close and exit.');")
                        
                        # 2. Wait for User to Accept
                        while True:
                            try:
                                # This will succeed while alert is open
                                self.browser.switch_to.alert 
                                time.sleep(1)
                            except:
                                # Alert is gone (User clicked OK)
                                break
                                
                        print("User acknowledged limit. Exiting...")
                        self.browser.quit()
                        raise Exception("API_LIMIT_REACHED_USER_ACK")

                    except Exception as wait_err:
                        print(f"Error during limit handling: {wait_err}")
                        self.browser.quit()
                        raise Exception("API_LIMIT_REACHED_ERROR")

                print(f"Search loop finished or error: {str(e)[:100]}")
                continue

    def apply_jobs(self, location):
        try:
            if no_jobs and 'No matching jobs' in no_jobs[0].text:
                raise Exception("No more jobs.")
        except:
            pass

        try:
            job_results_header = self.browser.find_element(By.CLASS_NAME, "jobs-search-results-list__text")
            if 'Jobs you may be interested in' in job_results_header.text:
                raise Exception("No more jobs. (Detected 'Jobs you may be interested in')")
        except Exception as e:
            if "No more jobs" in str(e): raise e
            pass


        job_list = []
        try:
            # Try multiple selectors for job list
            try:
                xpath_region1 = "/html/body/div[6]/div[3]/div[4]/div/div/main/div/div[2]/div[1]/div"
                ul_element = self.browser.find_element(By.XPATH, xpath_region1).find_element(By.TAG_NAME, "ul")
            except:
                try:
                    xpath_region2 = "/html/body/div[5]/div[3]/div[4]/div/div/main/div/div[2]/div[1]/div"
                    ul_element = self.browser.find_element(By.XPATH, xpath_region2).find_element(By.TAG_NAME, "ul")
                except:
                    ul_element = self.browser.find_element(By.CSS_SELECTOR, ".scaffold-layout__list-container")
            
            job_list = ul_element.find_elements(By.CLASS_NAME, 'scaffold-layout__list-item')
        except:
            return

        if not job_list: raise Exception("No jobs found on page.")

        # --- BATCH PROCESSING START ---
        batch_jobs = []      # List of {id, text, element_index, title, company, link}
        job_results = {}     # Map link -> {score, reason}
        BATCH_SIZE = 10 

        print(f"Scanning {len(job_list)} jobs on page...")

        for idx, job_tile in enumerate(job_list):
            if self.ban_safe and self.daily_count >= self.max_apps:
                print(f"Daily application limit ({self.max_apps}) reached. Stopping.")
                raise Exception("DAILY_LIMIT_REACHED")

            try:
                self.browser.execute_script("arguments[0].scrollIntoView(true);", job_tile)
                try:
                    title_el = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title--link')
                    job_title = title_el.text.strip()
                    link = title_el.get_attribute('href').split('?')[0]
                except:
                    continue

                # 1. Database Check
                prev_status_data = self.db.get_job_status(link)
                if prev_status_data:
                    status, reason = prev_status_data
                    reason_msg = f": {reason}" if reason else ""
                    print(f"Skipping ({status}{reason_msg}): {job_title}")
                    continue

                # 2. Blacklist Check
                try:
                    company = job_tile.find_element(By.CLASS_NAME, 'artdeco-entity-lockup__subtitle').text
                except:
                    company = "Unknown"

                if any(w.lower() in job_title.lower() for w in self.title_blacklist):
                    self.db.mark_job_seen(link, job_title, "Blacklisted-Title", "Title Match")
                    self.write_log("Skipped-Blacklist", 0, company, job_title, link, location, "Title Match")
                    continue
                if any(w.lower() in company.lower() for w in self.company_blacklist):
                    self.db.mark_job_seen(link, job_title, "Blacklisted-Company", "Company Match")
                    self.write_log("Skipped-Blacklist", 0, company, job_title, link, location, "Company Match")
                    continue

                # 3. Content Extraction (Click & Read)
                smart_click(self.browser, job_tile)
                human_sleep(1.5, 0.5)

                eligibility = self.check_job_eligibility()
                if eligibility != "Ready":
                    print(f"Skipping: {job_title} -> {eligibility}")
                    self.db.mark_job_seen(link, job_title, "Skipped-NotEligible", eligibility)
                    self.write_log("Skipped-NotEligible", 0, company, job_title, link, location, eligibility)
                    continue
                
                try:
                    desc_el = self.browser.find_element(By.CLASS_NAME, "jobs-search__job-details--container")
                    description = desc_el.text
                    
                    if description:
                         print(f"Reading {job_title} at {company} ({len(description)} chars)...")
                         simulate_reading(self.browser, description, min_duration=3.0)
                except:
                    description = ""


                # 4. Heuristics (Local Filter)
                heuristic_res = self.ai_handler.check_heuristics(description, self.user_profile_text)
                if heuristic_res:
                    score, reason = heuristic_res
                    print(f"Skipped (Heuristic): {job_title}")
                    self.db.mark_job_seen(link, job_title, "Skipped-Heuristic", reason)
                    self.write_log("Skipped-Heuristic", score, company, job_title, link, location, reason)
                    continue

                # 5. Add to Buffer
                job_data = {
                    "id": link,
                    "text": description,
                    "title": job_title,
                    "company": company,
                    "index": idx,
                    "location": location
                }
                batch_jobs.append(job_data)

                # TRIGGER BATCH ANALYSIS
                if len(batch_jobs) >= BATCH_SIZE or idx == len(job_list) - 1:
                    print(f" >> Analyzing Batch of {len(batch_jobs)} jobs...")
                    
                    # Call AI
                    ai_results = self.ai_handler.evaluate_batch(batch_jobs, self.user_profile_text)
                    
                    # Process Results immediately for this batch
                    for j_data in batch_jobs:
                        res = ai_results.get(j_data['id'], {'score': 0, 'reason': 'Error'})
                        score = res['score']
                        reason = res['reason']
                        
                        j_title = j_data['title']
                        j_link = j_data['id']
                        
                        if score >= self.application_match_threshold:
                            print(f" [MATCH] {j_title} ({score}/100): {reason}")
                            # ACT: Apply
                            
                            # Refetch tile by ID/Index to be safe
                            try:
                                # Re-find the tile to click it
                                current_tile = job_list[j_data['index']] 
                                smart_click(self.browser, current_tile)
                                human_sleep(2.0, 0.5)
                                
                                # Apply
                                app_status, app_reason = self.apply_to_job()
                                self.log_application(app_status, score, j_data['company'], j_title, j_link, j_data['location'], reason=app_reason)
                            except Exception as e:
                                print(f"Failed to apply to {j_title}: {e}")
                        else:
                            print(f" [SKIP] {j_title} ({score}/100): {reason}")
                            self.db.mark_job_seen(j_link, j_title, "Skipped-LowScore", reason)
                            self.write_log("Skipped-LowScore", score, j_data['company'], j_title, j_link, j_data['location'], reason)

                    # Clear batch
                    batch_jobs = []

            except StaleElementReferenceException:
                print("Stale Element. Reloading page list...")
                return # Safe exit to next page loop
            except Exception as e:
                if str(e) == "API_LIMIT_REACHED": raise e
                if str(e) == "STOP_SIGNAL": raise e
                if str(e) == "DAILY_LIMIT_REACHED": raise e
                print(f"Job Loop Error: {e}")

    def apply_to_job(self):
        try:
            btn = self.browser.find_element(By.CLASS_NAME, 'jobs-apply-button')
            smart_click(self.browser, btn)
        except:
            return "Already Applied", "Button not found"

        human_sleep(2.0, 0.5)

        while True:
            try:
                btns = self.browser.find_elements(By.CLASS_NAME, "artdeco-button--primary")
                if not btns: break

                btn_text = btns[0].text.lower()
                if 'submit application' in btn_text:
                    self.form.fill_up()
                    self.form.unfollow()
                    smart_click(self.browser, btns[0])
                    human_sleep(4.0, 1.0)
                    try:
                        self.browser.find_element(By.CLASS_NAME, 'artdeco-modal__dismiss').click()
                    except:
                        pass
                    return "Applied", "Success"

                self.form.fill_up()
                smart_click(self.browser, btns[0])
                human_sleep(4.0, 1.0)

                if self.form.check_for_errors():
                    print("Blocking form error detected. Aborting application.")
                    self.form.close_modal()
                    return "Failed", "Form Validation Error"

            except Exception as e:
                if str(e) == "STOP_SIGNAL": raise e
                traceback.print_exc()
                self.form.close_modal()
                # Truncate error to max 5 words for dashboard readability
                err_msg = str(e)
                short_reason = " ".join(err_msg.split()[:5])
                return "Failed", short_reason

        return "Failed", "Unknown Flow Error"
    
    def log_application(self, status, score, company, title, link, loc, reason=""):
        if status == "Applied":
            self.write_log("Applied", score, company, title, link, loc, reason)
            self.db.mark_job_seen(link, title, "Applied", reason)
            self.daily_count += 1
            if self.ban_safe:
                self.save_daily_state()
                if self.daily_count >= self.max_apps:
                    print(f"Daily application limit ({self.max_apps}) reached. Stopping.")
                    raise Exception("DAILY_LIMIT_REACHED")
        elif status == "Already Applied":
             self.write_log("Already Applied", score, company, title, link, loc, reason)
             self.db.mark_job_seen(link, title, "Already Applied", reason)
        else:
             self.write_log("Failed", score, company, title, link, loc, reason)
             self.db.mark_job_seen(link, title, "Failed", reason)

    def check_job_eligibility(self):
        try:
            buttons = self.browser.find_elements(By.CLASS_NAME, 'jobs-apply-button')

            if not buttons:
                try:
                     # 1. Message banner (often green checkmark)
                    feedback_msgs = self.browser.find_elements(By.CLASS_NAME, 'artdeco-inline-feedback__message')
                    for msg in feedback_msgs:
                        msg_text = msg.text.lower()
                        if 'applied' in msg_text:
                            return "Already Applied"
                        if 'we limit daily submissions' in msg_text:
                            print("LinkedIn daily submission limit message detected. Stopping.")
                            raise Exception("API_LIMIT_REACHED")
                    # 2. Status text in header or job details
                    page_text = self.browser.find_element(By.TAG_NAME, 'body').text.lower()
                    # key word detection
                    if "applied " in page_text[:2000] and "days ago" in page_text[:2000]:
                         return "Already Applied"
                except:
                    pass

                return "Not Easy Apply (Button missing)"

            btn_text = buttons[0].text.lower()

            if 'applied' in btn_text:
                return "Already Applied"

            return "Ready"

        except Exception as e:
            return f"Error: {str(e)}"

    def write_log(self, status, score, company, title, link, loc, reason=""):
        # Header: Status, Score, Company, Title, Link, Location, Search Location, Timestamp, Reason
        # We duplicate 'loc' for both Location and Search Location since we only track the search criteria location currently
        row = [status, score, company, title, link, loc, loc, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), reason]
        with open(self.unified_log_file, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

    def get_base_search_url(self, parameters):
        url_parts = []
        url_parts.append("f_AL=true") # Easy Apply

        if parameters.get('lessthanTenApplicants'):
            url_parts.append("f_EA=true")

        date_filters = parameters.get('date', {})
        if date_filters.get('24 hours'):
            url_parts.append("f_TPR=r86400")
        elif date_filters.get('week'):
            url_parts.append("f_TPR=r604800")
        elif date_filters.get('month'):
            url_parts.append("f_TPR=r2592000")

        exp_map = {'internship': '1', 'entry': '2', 'associate': '3', 'mid-senior level': '4', 'director': '5', 'executive': '6'}
        exp_codes = [exp_map[key] for key, active in parameters.get('experienceLevel', {}).items() if active and key in exp_map]
        if exp_codes:
            url_parts.append(f"f_E={','.join(exp_codes)}")

        type_map = {'full-time': 'F', 'contract': 'C', 'part-time': 'P', 'temporary': 'T', 'internship': 'I', 'volunteer': 'V', 'other': 'O'}
        type_codes = [type_map[key] for key, active in parameters.get('jobTypes', {}).items() if active and key in type_map]
        if type_codes:
            url_parts.append(f"f_JT={','.join(type_codes)}")

        if parameters.get('remote'):
            url_parts.append("f_WT=2")

        return "&".join(url_parts)

    def next_job_page(self, position, location, page):
        self.browser.get(
            f"https://www.linkedin.com/jobs/search/?keywords={position}{location}&start={page * 25}&{self.base_search_url}")

