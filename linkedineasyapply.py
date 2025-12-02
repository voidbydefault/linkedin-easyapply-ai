import csv
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
import traceback
from datetime import datetime
from itertools import product

import pyautogui
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


class LinkedinEasyApply:
    def __init__(self, parameters, driver, ai_config, ai_handler_instance, user_profile, active_positions):
        self.browser = driver
        self.email = parameters['email']
        self.password = parameters['password']
        self.disable_lock = parameters['disableAntiLock']
        self.company_blacklist = parameters.get('companyBlacklist', []) or []
        self.title_blacklist = parameters.get('titleBlacklist', []) or []
        self.poster_blacklist = parameters.get('posterBlacklist', []) or []
        self.positions = active_positions
        self.locations = parameters.get('locations', [])
        self.residency = parameters.get('residentStatus', False)
        self.base_search_url = self.get_base_search_url(parameters)

        # AI Config
        self.ai_settings = ai_config['ai_settings']
        self.work_dir = self.ai_settings.get('work_dir', './work')
        if not os.path.exists(self.work_dir): os.makedirs(self.work_dir)

        self.db_path = os.path.join(self.work_dir, "job_history.db")
        self.init_database()

        self.unified_log_file = os.path.join(self.work_dir, "application_log.csv")
        self.state_file = os.path.join(self.work_dir, "daily_state.json")
        self.ensure_log_file_exists()

        # Ban safe mechanism
        self.max_apps = self.ai_settings.get('max_applications', 50)
        self.ban_safe = self.ai_settings.get('ban_safe', False)

        if self.ban_safe:
            self.daily_count = self.load_daily_state()
            print(f"Daily Limit Check: {self.daily_count}/{self.max_apps} applications today.")
            if self.daily_count >= self.max_apps:
                print("!!! BAN SAFE TRIGGERED !!!")
                print(
                    f"You have already applied to {self.daily_count} jobs today. Exiting to prevent account restrictions.")
                sys.exit(0)
        else:
            self.daily_count = 0

        if not os.path.exists(self.unified_log_file):
            with open(self.unified_log_file, 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(
                    ["Status", "Score", "Company", "Title", "Link", "Location", "Search Location", "Timestamp"])

        self.resume_dir = parameters['uploads']['resume']
        self.cover_letter_dir = parameters['uploads'].get('coverLetter', '')
        self.checkboxes = parameters.get('checkboxes', [])
        self.university_gpa = parameters['universityGpa']
        self.salary_minimum = parameters['salaryMinimum']
        self.notice_period = int(parameters['noticePeriod'])
        self.languages = parameters.get('languages', [])
        self.experience = parameters.get('experience', [])
        self.personal_info = parameters.get('personalInfo', [])
        self.experience_default = int(self.experience.get('default', 2))
        self.is_hybrid = self.checkboxes.get('hybrid', False)
        self.is_remote = self.checkboxes.get('remote', False)
        self.ai_handler = ai_handler_instance
        self.use_ai_qa = self.ai_settings.get('let_ai_guess_answer', False)
        self.application_match_threshold = self.ai_settings.get('application_match_threshold', 70)
        self.user_profile_text = user_profile

    def smart_click(self, element):
        """
        Moves the virtual cursor to the element, pauses (hovers), and then clicks
        to simulate human-styled focus and avoid instant-click detection.
        """
        try:
            actions = ActionChains(self.browser)
            actions.move_to_element(element).perform()
            self.human_sleep(0.5, 0.2)
            actions.click().perform()

        except Exception:
            element.click()

    def load_daily_state(self):
        """Loads the daily application count from a JSON state file."""
        today_str = datetime.now().strftime("%Y-%m-%d")

        if not os.path.exists(self.state_file):
            return 0

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # If the date in file matches today, return the count.
                # Otherwise (it's a new day), return 0.
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
                    ["Status", "Score", "Company", "Title", "Link", "Location", "Search Location", "Timestamp"])

    def login(self):
        print("Checking session...")
        try:
            self.browser.get("https://www.linkedin.com/feed/")
            self.human_sleep(4.5, 1.0)

            # Check for generic "Sign In" button or Guest text
            page_src = self.browser.page_source.lower()
            if "sign in" in page_src or "join now" in page_src or "feed" not in self.browser.current_url:
                print("Session inactive. Proceeding to Login...")
                self.load_login_page_and_login()
            else:
                print("Session valid.")
        except TimeoutException:
            print("Timeout loading feed. Retrying login...")
            self.load_login_page_and_login()

    def security_check(self):
        if '/checkpoint/challenge/' in self.browser.current_url or 'security check' in self.browser.page_source:
            input("Security Check Detected! Please handle it in the browser and press Enter here...")
            time.sleep(5)

    def load_login_page_and_login(self):
        print("Loading Login Page...")
        self.browser.get("https://www.linkedin.com/login")
        time.sleep(2)

        # Try finding username field with multiple strategies
        email_elem = None
        try:
            email_elem = WebDriverWait(self.browser, 10).until(EC.presence_of_element_located((By.ID, "username")))
        except:
            try:
                email_elem = self.browser.find_element(By.NAME, "session_key")
            except:
                print("CRITICAL: Could not find username field!")
                sys.exit(1)

        print("Entering Credentials...")
        try:
            email_elem.click()
            email_elem.clear()
            email_elem.send_keys(self.email)
            time.sleep(0.5)

            pass_elem = self.browser.find_element(By.ID, "password")
            pass_elem.click()
            pass_elem.clear()
            pass_elem.send_keys(self.password)
            time.sleep(0.5)

            self.browser.find_element(By.CSS_SELECTOR, ".btn__primary--large").click()
        except Exception as e:
            print(f"Error entering credentials: {e}")
            sys.exit(1)

        # Verify Login Success
        print("Verifying login...")
        try:
            WebDriverWait(self.browser, 15).until(EC.url_contains("feed"))
            print("Login Successful.")
        except:
            if "challenge" in self.browser.current_url:
                self.security_check()
            else:
                print("Login Failed: Did not redirect to feed.")
                sys.exit(1)

    def human_sleep(self, average=3.0, variance=0.5):
        """
        Sleeps for a duration based on a Gaussian distribution.
        average: The target sleep time (mean).
        variance: How much the time can fluctuate (standard deviation).
        """
        sleep_time = abs(random.gauss(average, variance))
        # Ensure we never sleep less than 1 second to be safe
        sleep_time = max(1.0, sleep_time)
        time.sleep(sleep_time)

    def check_for_break(self):
        """
        Randomly takes a break every 7-12 applications.
        """
        # Initialize a counter if it doesn't exist
        if not hasattr(self, 'apps_since_last_break'):
            self.apps_since_last_break = 0
            self.next_break_threshold = random.randint(7, 12)

        self.apps_since_last_break += 1

        if self.apps_since_last_break >= self.next_break_threshold:
            # Time for a break (e.g., 3 to 7 minutes)
            break_duration = random.randint(180, 420)
            print(f"\n--- ☕ Taking a random micro-break for {break_duration / 60:.1f} minutes... ---")

            # Use chunks so the user can CTRL+C if needed without waiting full duration
            for _ in range(break_duration):
                time.sleep(1)

            print("--- Resuming work ---")

            # Reset counter and set new random threshold
            self.apps_since_last_break = 0
            self.next_break_threshold = random.randint(7, 12)

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
                    self.human_sleep(3.0, 0.5)

                    self.apply_jobs(location)

                    sleep_time = random.uniform(5, 10)
                    print(f"Page done. Resting {sleep_time:.1f}s...")
                    self.human_sleep(8.0, 2.0)

            except Exception as e:
                print(f"Search loop finished or error: {str(e)[:100]}")
                continue

    def init_database(self):
        """Creates the job history table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    job_hash TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    status TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Init Error: {e}")

    def get_job_hash(self, url):
        """Creates a unique short hash for a job URL to save space."""
        # Clean the URL to ensure uniqueness (remove query params)
        clean_url = url.split('?')[0]
        return hashlib.md5(clean_url.encode('utf-8')).hexdigest()

    def is_job_seen(self, url):
        """Checks if the job is already in our history."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM jobs WHERE job_hash = ?", (job_hash,))
            result = cursor.fetchone()
            conn.close()
            return result is not None
        except:
            return False

    def mark_job_seen(self, url, title, status):
        """Adds a job to history with its final status (Applied/Skipped/Failed)."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO jobs (job_hash, url, title, status)
                VALUES (?, ?, ?, ?)
            ''', (job_hash, url, title, status))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Save Error: {e}")

    def check_job_eligibility(self):
        """
        Quickly checks if the job is valid for 'Easy Apply' before we waste time/money analyzing it.
        Returns: 'Ready', 'Already Applied', or 'Not Easy Apply'
        """
        try:
            buttons = self.browser.find_elements(By.CLASS_NAME, 'jobs-apply-button')

            if not buttons:
                return "Not Easy Apply (Button missing)"

            btn_text = buttons[0].text.lower()

            if 'applied' in btn_text:
                return "Already Applied"

            return "Ready"

        except Exception as e:
            return f"Error: {str(e)}"

    def init_database(self):
        """Creates the job history table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    job_hash TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    status TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Init Error: {e}")

    def get_job_hash(self, url):
        """Creates a unique short hash for a job URL to save space."""
        # Clean the URL to ensure uniqueness (remove query params)
        clean_url = url.split('?')[0]
        return hashlib.md5(clean_url.encode('utf-8')).hexdigest()

    def is_job_seen(self, url):
        """Checks if the job is already in our history."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM jobs WHERE job_hash = ?", (job_hash,))
            result = cursor.fetchone()
            conn.close()
            return result is not None
        except:
            return False

    def mark_job_seen(self, url, title, status):
        """Adds a job to history with its final status (Applied/Skipped/Failed)."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO jobs (job_hash, url, title, status)
                VALUES (?, ?, ?, ?)
            ''', (job_hash, url, title, status))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Save Error: {e}")

    def apply_jobs(self, location):
        try:
            no_jobs = self.browser.find_elements(By.CLASS_NAME, 'jobs-search-two-pane__no-results-banner--expand')
            if no_jobs and 'No matching jobs' in no_jobs[0].text:
                raise Exception("No more jobs.")
        except:
            pass

        job_list = []
        ul_element = None

        try:
            xpath_region1 = "/html/body/div[6]/div[3]/div[4]/div/div/main/div/div[2]/div[1]/div"
            ul_element = self.browser.find_element(By.XPATH, xpath_region1).find_element(By.TAG_NAME, "ul")
        except:
            try:
                xpath_region2 = "/html/body/div[5]/div[3]/div[4]/div/div/main/div/div[2]/div[1]/div"
                ul_element = self.browser.find_element(By.XPATH, xpath_region2).find_element(By.TAG_NAME, "ul")
            except:
                try:
                    ul_element = self.browser.find_element(By.CSS_SELECTOR, ".scaffold-layout__list-container")
                except:
                    print("Could not find job list. LinkedIn devs didn't get a pay raise and possibly changed webpage structure as a revenge.")
                    return
        try:
            job_list = ul_element.find_elements(By.CLASS_NAME, 'scaffold-layout__list-item')
        except:
            return

        if not job_list: raise Exception("No jobs found on page.")

        for job_tile in job_list:

            # limit check mechanism
            if self.ban_safe and self.daily_count >= self.max_apps:
                print(f"Daily application limit ({self.max_apps}) reached during execution. Stopping.")
                return

            try:
                self.browser.execute_script("arguments[0].scrollIntoView(true);", job_tile)
                try:
                    title_el = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title--link')
                    job_title = title_el.text.strip()
                    link = title_el.get_attribute('href').split('?')[0]
                except:
                    continue

                if self.is_job_seen(link):
                    print(f"Skipping (Already in History): {job_title}")
                    continue

                try:
                    company = job_tile.find_element(By.CLASS_NAME, 'artdeco-entity-lockup__subtitle').text
                except:
                    company = "Unknown"

                if any(w.lower() in job_title.lower() for w in self.title_blacklist):
                    self.mark_job_seen(link, job_title, "Blacklisted-Title")
                    continue
                if any(w.lower() in company.lower() for w in self.company_blacklist):
                    self.mark_job_seen(link, job_title, "Blacklisted-Company")
                    continue

                # 1. Click the job (Stealth)
                self.smart_click(job_tile)

                # 2. Wait for right pane to load (Stealth)
                self.human_sleep(3.0, 0.6)

                eligibility = self.check_job_eligibility()

                if eligibility != "Ready":
                    print(f"Skipping: {job_title} -> Reason: {eligibility}")
                    self.write_log(eligibility, 0, company, job_title, link, location)
                    self.mark_job_seen(link, job_title, "Skipped-NotEligible")
                    continue

                # Extract JD and review via GenAI
                try:
                    desc_el = self.browser.find_element(By.CLASS_NAME, "jobs-search__job-details--container")
                    description = desc_el.text
                    self.scroll_slow(desc_el, end=1000)
                except:
                    description = ""

                print(f"Analyzing: {job_title} at {company}")
                score, reason = self.ai_handler.evaluate_single_job(description, self.user_profile_text)

                if score < self.application_match_threshold:
                    print(f"Skipped, score ({score}/100): {reason}")
                    self.write_log("Skipped", score, company, job_title, link, location)
                    self.mark_job_seen(link, job_title, "Skipped-LowScore")
                    continue

                app_status = self.apply_to_job()
                if app_status == "Applied":
                    print(f"Success, score ({score}/100): Applied to {job_title}")
                    self.write_log("Applied", score, company, job_title, link, location)
                    self.mark_job_seen(link, job_title, "Applied")  # <--- Added

                    self.daily_count += 1
                    if self.ban_safe:
                        self.save_daily_state()
                        print(f"Daily Count: {self.daily_count}/{self.max_apps}")

                elif app_status == "Already Applied":
                    print(f"Already applied, score ({score}/100) to {job_title}")
                    self.write_log("Already Applied", score, company, job_title, link, location)
                    self.mark_job_seen(link, job_title, "Already Applied")  # <--- Added
                else:
                    print(f"Failed ({score}/100) to Apply to {job_title}")
                    self.write_log("Failed", score, company, job_title, link, location)
                    self.mark_job_seen(link, job_title, "Failed")  # <--- Added

            except StaleElementReferenceException:
                print("Stale Element - moving to next job")
            except Exception as e:
                print(f"Job processing error: {e}")

    def apply_to_job(self):
        try:
            btn = self.browser.find_element(By.CLASS_NAME, 'jobs-apply-button')
            self.smart_click(btn)
        except:
            return "Already Applied"

        time.sleep(2)

        while True:
            try:
                btns = self.browser.find_elements(By.CLASS_NAME, "artdeco-button--primary")
                if not btns: break

                btn_text = btns[0].text.lower()
                if 'submit application' in btn_text:
                    self.fill_up()
                    self.unfollow()
                    self.smart_click(btns[0])
                    self.human_sleep(4.0, 1.0)
                    try:
                        self.browser.find_element(By.CLASS_NAME, 'artdeco-modal__dismiss').click()
                    except:
                        pass
                    return "Applied"

                self.fill_up()
                self.smart_click(btns[0])
                self.human_sleep(3.5, 0.7)

                if self.check_for_errors():
                    print("Blocking form error detected. Aborting application.")
                    self.close_modal()
                    return "Failed"

            except Exception:
                traceback.print_exc()
                self.close_modal()
                return "Failed"

        return "Failed"

    def check_for_errors(self):
        try:
            error_elements = self.browser.find_elements(By.CLASS_NAME, 'artdeco-inline-feedback__message')
            for el in error_elements:
                if el.is_displayed():
                    print(f"Form Error found: {el.text}")
                    return True
        except:
            pass
        return False

    def close_modal(self):
        try:
            self.browser.find_element(By.CLASS_NAME, 'artdeco-modal__dismiss').click()
            time.sleep(1)
            confirm_btns = self.browser.find_elements(By.CLASS_NAME, 'artdeco-modal__confirm-dialog-btn')
            if confirm_btns: confirm_btns[0].click()
        except:
            pass

    def fill_up(self):
        try:
            modal = self.browser.find_element(By.CLASS_NAME, "jobs-easy-apply-modal__content")
            form = modal.find_element(By.TAG_NAME, 'form')

            header = ""
            try:
                header = form.find_element(By.TAG_NAME, 'h3').text.lower()
            except:
                pass

            if 'home address' in header:
                self.home_address(form)
            elif 'contact info' in header:
                self.contact_info(form)
            elif 'resume' in header:
                self.send_resume()
            else:
                self.additional_questions(form)
        except:
            pass

    def additional_questions(self, form):
        questions = form.find_elements(By.CLASS_NAME, 'fb-dash-form-element')
        for q in questions:
            try:
                if q.find_elements(By.TAG_NAME, 'select'):
                    self.handle_dropdown(q)
                    continue
                if q.find_elements(By.TAG_NAME, 'fieldset'):
                    self.handle_radio(q)
                    continue
                if q.find_elements(By.TAG_NAME, 'input') or q.find_elements(By.TAG_NAME, 'textarea'):
                    self.handle_text_input(q)
                    continue
            except Exception:
                pass

    def handle_dropdown(self, q):
        select = Select(q.find_element(By.TAG_NAME, 'select'))
        label = q.find_element(By.TAG_NAME, 'label').text.lower()
        options = [o.text.lower() for o in select.options]

        print(f"  [Q] {label} (Dropdown)")

        # 1. User's configured answers are priority
        if 'hybrid' in label:
            target = "yes" if self.is_hybrid else "no"
            print(f"  -> Config (Hybrid): {target}")
            for opt in select.options:
                if target in opt.text.lower():
                    select.select_by_visible_text(opt.text)
                    return

        if 'remote' in label:
            target = "yes" if self.is_remote else "no"
            print(f"  -> Config (Remote): {target}")
            for opt in select.options:
                if target in opt.text.lower():
                    select.select_by_visible_text(opt.text)
                    return

        negatives = ['conflict', 'convict', 'disqualif', 'felony', 'misdemeanor']
        if any(x in label for x in negatives):
            print(f"  -> Detected negative Question. Force safe answer.")
            for opt in select.options:
                if any(n in opt.text.lower() for n in ['no', 'none']):
                    print(f"  -> Selected: {opt.text}")
                    select.select_by_visible_text(opt.text)
                    return

        if 'proficiency' in label:
            for lang, level in self.languages.items():
                if lang in label:
                    print(f"  -> Selecting Language Level: {level}")
                    select.select_by_visible_text(level)
                    return

        # 2. Use GenAI as a fallback (helps address user's unique situation)
        ai_ans = None
        if self.use_ai_qa:
            ai_ans = self.ai_handler.answer_question(label, f"Options: {options}", self.user_profile_text)
            if ai_ans:
                print(f"  -> AI Suggestion: {ai_ans}")
                for opt in select.options:
                    if ai_ans.lower() in opt.text.lower():
                        select.select_by_visible_text(opt.text)
                        return

        # 3. Last drop down if 1 and 2 don't work
        if 'select' in select.first_selected_option.text.lower():
            select.select_by_index(len(select.options) - 1)

    def handle_radio(self, q):
        try:
            label = q.find_element(By.CLASS_NAME, 'fb-dash-form-element__label').text.lower()
            radios = q.find_elements(By.TAG_NAME, 'label')

            print(f"  [Q] {label} (Radio)")

            # 1. Config Priority
            degrees = self.checkboxes.get('degreeCompleted', [])
            if any(deg.lower() in label for deg in degrees):
                print("  -> Config (Degree): Yes")
                for r in radios:
                    if 'yes' in r.text.lower():
                        self.smart_click(r);
                        return

            if 'hybrid' in label:
                target = "yes" if self.is_hybrid else "no"
                print(f"  -> Config (Hybrid): {target}")
                for r in radios:
                    if target in r.text.lower(): r.click(); return

            if 'remote' in label:
                target = "yes" if self.is_remote else "no"
                print(f"  -> Config (Remote): {target}")
                for r in radios:
                    if target in r.text.lower(): r.click(); return

            neg_keywords = ['gender', 'race', 'veteran', 'disability', 'conflict', 'convict', 'disqualif', 'felony']
            if any(x in label for x in neg_keywords):
                for r in radios:
                    if any(d in r.text.lower() for d in ['decline', 'prefer not', 'no', 'none']):
                        print(f"  -> Force Negative/Decline: {r.text}")
                        r.click();
                        return

            # 2. AI Decision
            target = "yes"
            if self.use_ai_qa:
                target = self.ai_handler.answer_question(label, "Yes/No", self.user_profile_text) or "yes"
            print(f"  -> AI Suggestion: {target}")

            target = target.lower()
            clicked = False
            for r in radios:
                if target in r.text.lower():
                    r.click();
                    clicked = True;
                    break

            # 3. Fallback
            if not clicked:
                for r in radios:
                    if 'no' in r.text.lower():
                        r.click();
                        clicked = True;
                        print("  -> Fallback: No");
                        break

            if not clicked:
                print("  -> Fallback: Last Option")
                radios[-1].click()

        except:
            pass

    def handle_text_input(self, q):
        try:
            inp = q.find_elements(By.TAG_NAME, 'input')
            if not inp: inp = q.find_elements(By.TAG_NAME, 'textarea')
            element = inp[0]
            label = q.find_element(By.TAG_NAME, 'label').text.lower()

            print(f"  [Q] {label} (Text)")

            val = None
            if 'salary' in label:
                val = self.salary_minimum
            elif 'years' in label:
                val = self.experience_default
            elif 'notice' in label:
                val = self.notice_period
            elif 'name' in label and 'first' in label:
                val = self.personal_info.get('First Name', '')
            elif 'name' in label and 'last' in label:
                val = self.personal_info.get('Last Name', '')
            elif 'linkedin' in label:
                val = self.personal_info.get('Linkedin', '')
            elif 'website' in label:
                val = self.personal_info.get('Website', '')
            elif self.use_ai_qa:
                val = self.ai_handler.answer_question(label, "Text", self.user_profile_text)

            if val:
                print(f"  -> Entering: {val}")
                self.enter_text(element, str(val))
        except:
            pass

    def home_address(self, form):
        try:
            groups = form.find_elements(By.CLASS_NAME, 'jobs-easy-apply-form-section__grouping')
            if len(groups) > 0:
                for group in groups:
                    lb = group.find_element(By.TAG_NAME, 'label').text.lower()
                    input_field = group.find_element(By.TAG_NAME, 'input')
                    if 'street' in lb:
                        self.enter_text(input_field, self.personal_info.get('Street address', ''))
                    elif 'city' in lb:
                        self.enter_text(input_field, self.personal_info.get('City', ''))
                        time.sleep(1)
                        input_field.send_keys(Keys.DOWN)
                        input_field.send_keys(Keys.RETURN)
                    elif 'zip' in lb or 'postal' in lb:
                        self.enter_text(input_field, self.personal_info.get('Zip', ''))
                    elif 'state' in lb or 'province' in lb:
                        self.enter_text(input_field, self.personal_info.get('State', ''))
        except:
            pass

    def contact_info(self, form):
        try:
            phone_field = form.find_elements(By.XPATH, '//input[contains(@id,"phoneNumber")]')
            if phone_field:
                self.enter_text(phone_field[0], self.personal_info.get('Mobile Phone Number', ''))
        except:
            pass

    def send_resume(self):
        try:
            file_input = self.browser.find_element(By.CSS_SELECTOR, "input[name='file']")
            file_input.send_keys(self.resume_dir)
        except:
            pass

    def enter_text(self, element, text):
        try:
            element.clear()
            element.send_keys(text)
        except:
            pass

    def scroll_slow(self, scrollable_element, start=0, end=3600, step=100, reverse=False):
        if reverse:
            start, end = end, start
            step = -step

        for i in range(start, end, step):
            self.browser.execute_script("arguments[0].scrollTo(0, {})".format(i), scrollable_element)
            self.human_sleep(0.25, 0.05)

    def unfollow(self):
        try:
            lbl = self.browser.find_element(By.XPATH, "//label[contains(.,'stay up to date')]")
            self.smart_click(lbl)
        except:
            pass

    def write_log(self, status, score, company, title, link, loc):
        row = [status, score, company, title, link, loc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        with open(self.unified_log_file, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

    def get_base_search_url(self, parameters):
        url_parts = []

        # Ensure Easy Apply only
        url_parts.append("f_AL=true")

        # If 'under 10 applicants' required by user
        if parameters.get('lessthanTenApplicants'):
            url_parts.append("f_EA=true")

        # Date posted filter where
        # r86400 = 24h, r604800 = Week, r2592000 = Month
        date_filters = parameters.get('date', {})
        if date_filters.get('24 hours'):
            url_parts.append("f_TPR=r86400")
        elif date_filters.get('week'):
            url_parts.append("f_TPR=r604800")
        elif date_filters.get('month'):
            url_parts.append("f_TPR=r2592000")

        # Experience Level (f_E)
        # 1=Intern, 2=Entry, 3=Assoc, 4=Mid-Senior, 5=Director, 6=Exec
        exp_map = {
            'internship': '1',
            'entry': '2',
            'associate': '3',
            'mid-senior level': '4',
            'director': '5',
            'executive': '6'
        }
        exp_codes = []
        for key, active in parameters.get('experienceLevel', {}).items():
            if active and key in exp_map:
                exp_codes.append(exp_map[key])

        if exp_codes:
            url_parts.append(f"f_E={','.join(exp_codes)}")

        # Job Type filter (f_JT)
        # F=Full-time, C=Contract, P=Part-time, T=Temp, I=Intern, V=Volunteer, O=Other
        type_map = {
            'full-time': 'F',
            'contract': 'C',
            'part-time': 'P',
            'temporary': 'T',
            'internship': 'I',
            'volunteer': 'V',
            'other': 'O'
        }
        type_codes = []
        for key, active in parameters.get('jobTypes', {}).items():
            if active and key in type_map:
                type_codes.append(type_map[key])

        if type_codes:
            url_parts.append(f"f_JT={','.join(type_codes)}")

        # Work type filter (f_WT)
        # 1=On-site, 2=Remote, 3=Hybrid
        # Strick based on 'remote: True' or 'hybrid: True' from config.yaml
        if parameters.get('remote'):
            url_parts.append("f_WT=2")

        return "&".join(url_parts)

    def next_job_page(self, position, location, page):
        self.browser.get(
            f"https://www.linkedin.com/jobs/search/?keywords={position}{location}&start={page * 25}&{self.base_search_url}")
        self.avoid_lock()

    def avoid_lock(self):
        if self.disable_lock: return
        pyautogui.keyDown('ctrl')
        pyautogui.press('esc')
        pyautogui.keyUp('ctrl')