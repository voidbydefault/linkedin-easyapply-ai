import csv
import json
import os
import random
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from itertools import product
from urllib.parse import quote_plus

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

    def _show_login_banner(self):
        """Injects a visible banner into the browser so the user knows the bot is waiting for login."""
        try:
            self.browser.execute_script("""
            if (!document.getElementById('bot-login-banner')) {
                var banner = document.createElement('div');
                banner.id = 'bot-login-banner';
                banner.style.cssText = 'position:fixed;top:0;left:0;width:100%;padding:14px;' +
                    'background:linear-gradient(135deg,#e53935,#b71c1c);color:white;text-align:center;' +
                    'z-index:99999;font-size:18px;font-family:Arial,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,0.5);font-weight:bold;';
                banner.innerText = '🤖 ACTION REQUIRED: Please log in to LinkedIn manually. Once you see your Feed, go back to the bot terminal and press Enter.';
                document.body.appendChild(banner);
            }
            """)
        except Exception:
            pass

    def _remove_login_banner(self):
        """Removes the login banner."""
        try:
            self.browser.execute_script("""
            var el = document.getElementById('bot-login-banner');
            if (el) el.remove();
            """)
        except Exception:
            pass

    def login(self):
        print("\nChecking LinkedIn session...")
        try:
            self.browser.get("https://www.linkedin.com/feed/")
            human_sleep(4.0, 1.0)

            page_src = self.browser.page_source.lower()
            # If we see sign-in elements or we aren't on the feed, we need login
            if "sign in" in page_src or "join now" in page_src or "feed" not in self.browser.current_url.lower():
                print("\n" + "!" * 60)
                print("   ⚠️  MANUAL LOGIN REQUIRED  ⚠️")
                print("1. A new browser window/tab has opened the login page.")
                print("2. Please log in manually (and complete 2FA if needed).")
                print("3. Once you reach the LinkedIn Feed page, return here.")
                print("4. Press [ENTER] in this terminal to start applying.")
                print("!" * 60 + "\n")

                if "login" not in self.browser.current_url:
                    self.browser.get("https://www.linkedin.com/login")
                
                self._show_login_banner()
                
                # Wait for user input in terminal
                input(">>> Press [ENTER] in terminal once you have logged in and are on the Feed page...")
                
                self._remove_login_banner()

                # Re-verify session
                print("Verifying session...")
                self.browser.get("https://www.linkedin.com/feed/")
                human_sleep(3.0, 1.0)
                
                if "feed" not in self.browser.current_url.lower():
                    print("Verification FAILED. We still don't see the LinkedIn feed.")
                    print("Retrying login prompt...")
                    self.login() # Recursive retry
                else:
                    print("✅ Session verified. Starting automation...")
            else:
                print("✅ Session is already active. Proceeding...")
        except TimeoutException:
            print("Timeout loading LinkedIn. Retrying...")
            self.login()
        except Exception as e:
            print(f"Error during login check: {e}")
            raise e

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
                    elements = self.browser.find_elements(
                        By.CSS_SELECTOR, 'div[componentkey^="job-card-component-ref-"] p, .job-card-list__title'
                    )
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
            self._seen_search_job_ids = set()

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
                if "No more jobs" in str(e) or "No jobs found on page" in str(e):
                    print(f"Ending search for {position} in {location}: No more matching jobs found. Moving to next search...")
                    continue
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
                        self.browser.execute_script(
                            "alert('API Limit Reached! The bot has stopped. Press OK to return to the dashboard.');"
                        )

                        # 2. Wait for User to Accept
                        while True:
                            try:
                                # This will succeed while alert is open
                                self.browser.switch_to.alert
                                time.sleep(0.5)
                            except Exception:
                                # Alert is gone (User clicked OK or tab closed)
                                break

                        print("User acknowledged limit. Stopping bot and returning to dashboard...")
                    except Exception as wait_err:
                        print(f"Warning during limit notification: {wait_err}")

                    return

                print(f"Search loop finished or error: {str(e)[:100]}")
                continue

    def _find_job_cards_now(self):
        """Locates job cards on the page across SDUI and legacy LinkedIn DOMs."""
        self.browser.implicitly_wait(0)
        try:
            selectors = (
                (By.CSS_SELECTOR, 'div[componentkey^="job-card-component-ref-"]'),
                (By.CSS_SELECTOR, 'div[data-job-id]'),
                (By.CSS_SELECTOR, 'li[data-occludable-job-id]'),
                (By.CSS_SELECTOR, '.scaffold-layout__list-container .scaffold-layout__list-item'),
                (By.CLASS_NAME, 'scaffold-layout__list-item'),
            )
            for by, sel in selectors:
                try:
                    els = self.browser.find_elements(by, sel)
                    if els:
                        return els
                except Exception:
                    pass

            for xpath in (
                "/html/body/div[6]/div[3]/div[4]/div/div/main/div/div[2]/div[1]/div",
                "/html/body/div[5]/div[3]/div[4]/div/div/main/div/div[2]/div[1]/div",
            ):
                try:
                    region = self.browser.find_element(By.XPATH, xpath)
                    ul_element = region.find_element(By.TAG_NAME, "ul")
                    els = ul_element.find_elements(By.CLASS_NAME, 'scaffold-layout__list-item')
                    if els:
                        return els
                except Exception:
                    pass
            return []
        finally:
            self.browser.implicitly_wait(4)

    def _get_job_cards(self, timeout=8.0):
        """Waits briefly for SDUI/legacy job cards to render and returns them."""
        deadline = time.time() + timeout
        cards = self._find_job_cards_now()
        while not cards and time.time() < deadline:
            time.sleep(0.5)
            cards = self._find_job_cards_now()
        if cards:
            try:
                self.browser.execute_script("arguments[0].scrollIntoView({block: 'nearest'});", cards[-1])
                time.sleep(0.3)
                refreshed = self._find_job_cards_now()
                if refreshed:
                    cards = refreshed
            except StaleElementReferenceException:
                refreshed = self._find_job_cards_now()
                if refreshed:
                    cards = refreshed
            except Exception:
                pass
        return cards

    def _extract_card_info(self, job_tile):
        """Extracts (job_title, company, link, job_id) from an SDUI or legacy job card."""
        self.browser.implicitly_wait(0)
        try:
            job_id = None
            link = None
            job_title = None

            comp_key = job_tile.get_attribute('componentkey') or ''
            m = re.search(r'job-card-component-ref-(\d+)', comp_key)
            if m:
                job_id = m.group(1)
                link = f"https://www.linkedin.com/jobs/view/{job_id}/"

            if not job_id:
                for attr in ('data-job-id', 'data-occludable-job-id'):
                    val = (job_tile.get_attribute(attr) or '').strip()
                    if val.isdigit():
                        job_id = val
                        link = f"https://www.linkedin.com/jobs/view/{job_id}/"
                        break

            for by, sel in (
                (By.CLASS_NAME, 'job-card-list__title--link'),
                (By.CSS_SELECTOR, 'a[href*="/jobs/view/"]'),
            ):
                try:
                    els = job_tile.find_elements(by, sel)
                    if els:
                        href = els[0].get_attribute('href') or ''
                        if not link and '/jobs/view/' in href:
                            link = href.split('?')[0]
                            m_href = re.search(r'/jobs/view/(\d+)', link)
                            if m_href and not job_id:
                                job_id = m_href.group(1)
                        txt = (els[0].text or '').strip()
                        if txt:
                            job_title = txt
                            break
                except StaleElementReferenceException:
                    raise
                except Exception:
                    pass

            p_els = []
            try:
                p_els = job_tile.find_elements(By.TAG_NAME, 'p')
            except StaleElementReferenceException:
                raise
            except Exception:
                pass

            if not job_title and p_els:
                try:
                    spans = p_els[0].find_elements(By.CSS_SELECTOR, 'span[aria-hidden="true"]')
                    if spans and spans[0].text.strip():
                        job_title = spans[0].text.strip()
                except StaleElementReferenceException:
                    raise
                except Exception:
                    pass
                if not job_title:
                    raw_title = (p_els[0].text or '').strip()
                    raw_title = re.sub(r'^Selected,\s*', '', raw_title, flags=re.IGNORECASE)
                    raw_title = re.sub(r'\s*\(Verified job\)\s*$', '', raw_title, flags=re.IGNORECASE).strip()
                    if raw_title:
                        job_title = raw_title

            if not job_title:
                try:
                    dismiss_btns = job_tile.find_elements(By.CSS_SELECTOR, 'button[aria-label^="Dismiss "]')
                    if dismiss_btns:
                        aria = (dismiss_btns[0].get_attribute('aria-label') or '').strip()
                        cleaned = re.sub(r'^Dismiss\s+', '', aria, flags=re.IGNORECASE)
                        cleaned = re.sub(r'\s+job$', '', cleaned, flags=re.IGNORECASE).strip()
                        if cleaned:
                            job_title = cleaned
                except StaleElementReferenceException:
                    raise
                except Exception:
                    pass

            if not link or not job_title:
                return None

            company = None
            for by, sel in (
                (By.CLASS_NAME, 'artdeco-entity-lockup__subtitle'),
                (By.CLASS_NAME, 'job-card-container__company-name'),
            ):
                try:
                    els = job_tile.find_elements(by, sel)
                    if els and els[0].text.strip():
                        company = els[0].text.strip()
                        break
                except StaleElementReferenceException:
                    raise
                except Exception:
                    pass

            if not company and p_els:
                p_texts = [(p.text or '').strip() for p in p_els if (p.text or '').strip()]
                if len(p_texts) >= 2:
                    company = p_texts[1]

            return job_title, (company or "Unknown"), link, job_id
        finally:
            self.browser.implicitly_wait(4)

    _APPLIED_STATUS_RE = re.compile(
        r'^applied(?:\s+(?:on\s+\S+(?:\s+\S+){0,4}|\d+\s*[a-z]*\s*ago|just now|today|yesterday|\d+\s*(?:s|m|h|d|w|mo|min|hr|hour|day|wk|week|month)s?))?$'
    )

    def _is_card_already_applied(self, job_tile, job_title="", company=""):
        """Detects LinkedIn's card-level 'Applied' footer badge before clicking a job card."""
        self.browser.implicitly_wait(0)
        try:
            try:
                for state_el in job_tile.find_elements(By.CLASS_NAME, 'job-card-container__footer-job-state'):
                    if (state_el.text or '').strip().lower().startswith('applied'):
                        return True
            except Exception:
                pass

            candidates = []
            for tag in ('p', 'li', 'span'):
                try:
                    for el in job_tile.find_elements(By.TAG_NAME, tag):
                        txt = (el.text or '').strip()
                        if txt:
                            candidates.append(txt)
                except Exception:
                    pass

            tile_text = (job_tile.text or '').strip()
            if tile_text:
                candidates.extend(tile_text.splitlines())

            title_l = (job_title or '').strip().lower()
            comp_l = (company or '').strip().lower()

            for raw in candidates:
                for line in raw.splitlines():
                    for part in re.split(r'[·•|]', line):
                        seg = part.strip().lower()
                        if not seg or seg == title_l or seg == comp_l:
                            continue
                        if self._APPLIED_STATUS_RE.match(seg):
                            return True
            return False
        except Exception:
            return False
        finally:
            self.browser.implicitly_wait(4)

    def _click_job_card(self, job_tile):
        """Scrolls to and clicks the job card title/text to avoid hitting the Dismiss button."""
        try:
            self.browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", job_tile)
        except Exception:
            pass
        self.browser.implicitly_wait(0)
        target = None
        try:
            for by, sel in (
                (By.CLASS_NAME, 'job-card-list__title--link'),
                (By.CSS_SELECTOR, 'a[href*="/jobs/view/"]'),
                (By.TAG_NAME, 'p'),
            ):
                els = job_tile.find_elements(by, sel)
                if els:
                    target = els[0]
                    break
        except Exception:
            pass
        finally:
            self.browser.implicitly_wait(4)
        smart_click(self.browser, target or job_tile)

    def _get_details_pane(self):
        """Returns the right-hand job details pane for SDUI or legacy layouts."""
        self.browser.implicitly_wait(0)
        try:
            for by, sel in (
                (By.CSS_SELECTOR, 'div[data-sdui-screen="com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"]'),
                (By.CSS_SELECTOR, '[data-sdui-screen*="JobDetails"]'),
                (By.CLASS_NAME, 'jobs-search__job-details--container'),
                (By.ID, 'job-details'),
            ):
                try:
                    els = self.browser.find_elements(by, sel)
                    for el in els:
                        if el.is_displayed():
                            return el
                    if els:
                        return els[0]
                except Exception:
                    pass
            return None
        finally:
            self.browser.implicitly_wait(4)

    def _get_job_description(self):
        """Expands 'more' inside About the job if present and returns the job details text."""
        pane = self._get_details_pane()
        if not pane:
            return ""
        self.browser.implicitly_wait(0)
        try:
            if "about the job" in (pane.text or "").lower():
                for btn in pane.find_elements(By.TAG_NAME, 'button'):
                    try:
                        btxt = (btn.text or "").strip().lower()
                        if btn.is_displayed() and 'more' in btxt and 'company' not in btxt:
                            smart_click(self.browser, btn)
                            time.sleep(0.3)
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            self.browser.implicitly_wait(4)
        return pane.text or ""

    def _find_easy_apply_button(self):
        """Finds the Easy Apply button in the SDUI details pane or legacy top card."""
        pane = self._get_details_pane()
        roots = [pane, self.browser] if pane else [self.browser]
        self.browser.implicitly_wait(0)
        try:
            for root in roots:
                for by, sel in (
                    (By.CSS_SELECTOR, 'button[aria-label*="Easy Apply"]'),
                    (By.CSS_SELECTOR, 'a[aria-label*="Easy Apply"]'),
                    (By.CLASS_NAME, 'jobs-apply-button'),
                ):
                    try:
                        for el in root.find_elements(by, sel):
                            if el.is_displayed():
                                return el
                    except Exception:
                        pass
                for tag in ('button', 'a'):
                    try:
                        for el in root.find_elements(By.TAG_NAME, tag):
                            txt = (el.text or "").strip().lower()
                            aria = (el.get_attribute('aria-label') or "").strip().lower()
                            href = (el.get_attribute('href') or "").strip().lower()
                            if el.is_displayed() and (
                                txt.startswith('easy apply')
                                or 'easy apply' in aria
                                or 'opensduiapplyflow=true' in href
                            ):
                                return el
                    except Exception:
                        pass
            return None
        finally:
            self.browser.implicitly_wait(4)

    def _has_external_apply_button(self, pane=None):
        """Returns True if an external Apply action is already rendered in the details pane."""
        root = pane if pane is not None else self._get_details_pane()
        if not root:
            return False
        self.browser.implicitly_wait(0)
        try:
            for tag in ('a', 'button'):
                try:
                    for el in root.find_elements(By.TAG_NAME, tag):
                        if not el.is_displayed():
                            continue
                        txt = (el.text or "").strip().lower()
                        aria = (el.get_attribute('aria-label') or "").strip().lower()
                        href = (el.get_attribute('href') or "").strip().lower()
                        if 'easy apply' in txt or 'easy apply' in aria or 'opensduiapplyflow=true' in href:
                            continue
                        if txt == 'apply' or 'apply on company' in aria or aria.startswith('apply to '):
                            return True
                except Exception:
                    pass
            return False
        finally:
            self.browser.implicitly_wait(4)

    @staticmethod
    def _jittered_poll_sleep(attempt, remaining):
        """Sleeps a non-linear, jittered interval bounded by remaining timeout."""
        wait_step = min(0.4 + attempt * 0.15, 1.1) + random.uniform(0.0, 0.45)
        time.sleep(min(wait_step, remaining))

    def _wait_for_easy_apply_button(self, timeout=None):
        """Polls with human-like non-linear jitter for the Easy Apply button to render."""
        if timeout is None:
            timeout = random.uniform(4.5, 7.0)
        deadline = time.time() + timeout
        attempt = 0
        while True:
            btn = self._find_easy_apply_button()
            if btn:
                if attempt > 0:
                    time.sleep(random.uniform(0.35, 0.95))
                return btn
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            self._jittered_poll_sleep(attempt, remaining)
            attempt += 1

    def _refetch_job_tile(self, job_id, fallback_list, index):
        """Re-locates a job card by job_id to avoid stale element references after batch AI calls."""
        if job_id:
            self.browser.implicitly_wait(0)
            try:
                for sel in (
                    f'div[componentkey="job-card-component-ref-{job_id}"]',
                    f'div[data-job-id="{job_id}"]',
                    f'li[data-occludable-job-id="{job_id}"]',
                ):
                    els = self.browser.find_elements(By.CSS_SELECTOR, sel)
                    if els:
                        return els[0]
            except Exception:
                pass
            finally:
                self.browser.implicitly_wait(4)
        if 0 <= index < len(fallback_list):
            return fallback_list[index]
        return None

    def check_no_jobs(self):
        self.browser.implicitly_wait(0)
        try:
            # a) Banner / empty state elements
            try:
                banners = self.browser.find_elements(
                    By.CLASS_NAME, 'jobs-search-two-pane__no-results-banner--expand')
                banners += self.browser.find_elements(By.CLASS_NAME, 'jobs-search-no-results-banner')
                banners += self.browser.find_elements(By.CSS_SELECTOR, 'div[class*="no-results"]')
                banners += self.browser.find_elements(By.CSS_SELECTOR, '.artdeco-empty-state__headline')
                for banner in banners:
                    if 'no matching jobs' in banner.text.lower():
                        raise Exception("No more jobs. (Detected 'No matching jobs')")
            except Exception as e:
                if "No more jobs" in str(e): raise e

            # b) Search results headers
            try:
                headers = self.browser.find_elements(By.CLASS_NAME, 'jobs-search-results-list__text')
                headers += self.browser.find_elements(By.CSS_SELECTOR, '.jobs-search-results-list__header')
                headers += self.browser.find_elements(By.CSS_SELECTOR, '.jobs-search-results-list__subtitle')
                for header in headers:
                    header_text = header.text.lower()
                    if ('no matching jobs' in header_text or
                            'jobs you may be interested in' in header_text or
                            'explore relevant jobs' in header_text):
                        raise Exception("No more jobs. (Detected 'Jobs you may be interested in' / 'No matching jobs')")
            except Exception as e:
                if "No more jobs" in str(e): raise e

            # c) Search results list container text
            try:
                containers = self.browser.find_elements(
                    By.CSS_SELECTOR, '.jobs-search-results-list, .scaffold-layout__list')
                for container in containers:
                    container_text = container.text[:500].lower()
                    if ('no matching jobs' in container_text or
                            'jobs you may be interested in' in container_text or
                            'explore relevant jobs' in container_text):
                        raise Exception("No more jobs.")
            except Exception as e:
                if "No more jobs" in str(e): raise e

            # d) SDUI empty state check (only when no job cards are rendered; ignores "trouble finding exact matches")
            try:
                body_els = self.browser.find_elements(By.TAG_NAME, 'main') or self.browser.find_elements(By.TAG_NAME, 'body')
                if body_els:
                    body_text = (body_els[0].text or '')[:2000].lower()
                    if 'no matching jobs' in body_text or 'no jobs found' in body_text:
                        raise Exception("No more jobs. (Detected SDUI empty state)")
            except Exception as e:
                if "No more jobs" in str(e): raise e
        finally:
            self.browser.implicitly_wait(4)

    @staticmethod
    def _is_transient_batch_error(reason):
        r = (reason or "").strip().lower()
        return (
            "batch error" in r
            or "parsing failed" in r
            or r == "error"
            or "description data was empty" in r
        )

    def _collect_page_cards(self, max_stale_retries=3):
        """Fetches job cards and extracts page job IDs, retrying if LinkedIn refreshes the DOM mid-scan."""
        for attempt in range(max_stale_retries):
            job_list = self._get_job_cards()
            if not job_list:
                self.check_no_jobs()
                print("No jobs found on page (0 job cards matched).")
                raise Exception("No jobs found on page.")

            page_job_ids = set()
            stale_hit = False
            for tile in job_list:
                try:
                    info = self._extract_card_info(tile)
                except StaleElementReferenceException:
                    stale_hit = True
                    break
                if info and info[3]:
                    page_job_ids.add(info[3])
                elif info and info[2]:
                    page_job_ids.add(info[2])

            if not stale_hit:
                return job_list, page_job_ids

            print(f"Page DOM refreshed during card scan (attempt {attempt + 1}/{max_stale_retries}). Retrying...")
            time.sleep(1.0)

        job_list = self._get_job_cards()
        if not job_list:
            self.check_no_jobs()
            print("No jobs found on page (0 job cards matched).")
            raise Exception("No jobs found on page.")

        valid_cards = []
        page_job_ids = set()
        for tile in job_list:
            try:
                info = self._extract_card_info(tile)
            except StaleElementReferenceException:
                continue
            valid_cards.append(tile)
            if info and info[3]:
                page_job_ids.add(info[3])
            elif info and info[2]:
                page_job_ids.add(info[2])
        return valid_cards, page_job_ids

    def apply_jobs(self, location):
        job_list, page_job_ids = self._collect_page_cards()

        if not hasattr(self, '_seen_search_job_ids'):
            self._seen_search_job_ids = set()

        if page_job_ids and page_job_ids.issubset(self._seen_search_job_ids):
            raise Exception("No more jobs. (All jobs on page already seen)")
        self._seen_search_job_ids.update(page_job_ids)

        # --- BATCH PROCESSING START ---
        batch_jobs = []      # List of {id, job_id, text, index, title, company, location}
        BATCH_SIZE = int(self.ai_settings.get('batch_size', 5))
        seen_on_page = set()

        print(f"Scanning {len(job_list)} jobs on page...")

        max_stale_reloads = 3
        stale_reloads = 0
        while True:
            reloaded = False
            for idx, job_tile in enumerate(job_list):
                if self.ban_safe and self.daily_count >= self.max_apps:
                    print(f"Daily application limit ({self.max_apps}) reached. Stopping.")
                    raise Exception("DAILY_LIMIT_REACHED")

                current_dedupe_key = None
                try:
                    card_info = self._extract_card_info(job_tile)
                    if not card_info:
                        continue
                    job_title, company, link, job_id = card_info

                    dedupe_key = job_id or self.db.get_job_hash(link)
                    if dedupe_key in seen_on_page:
                        continue
                    seen_on_page.add(dedupe_key)
                    current_dedupe_key = dedupe_key

                    # 1. Database Check
                    prev_status_data = self.db.get_job_status(link, job_id=job_id)
                    if prev_status_data:
                        status, reason = prev_status_data
                        reason_msg = f": {reason}" if reason else ""
                        print(f"Skipping ({status}{reason_msg}): {job_title}")
                        continue

                    # 1b. LinkedIn Card "Applied" Badge Check (skip before clicking)
                    if self._is_card_already_applied(job_tile, job_title=job_title, company=company):
                        print(f"Skipping (Already Applied on LinkedIn): {job_title}")
                        self.db.mark_job_seen(link, job_title, "Already Applied", "LinkedIn Applied badge", job_id=job_id)
                        self.write_log("Already Applied", 0, company, job_title, link, location, "LinkedIn Applied badge")
                        continue

                    # 2. Blacklist Check
                    if any(w.lower() in job_title.lower() for w in self.title_blacklist):
                        self.db.mark_job_seen(link, job_title, "Blacklisted-Title", "Title Match", job_id=job_id)
                        self.write_log("Skipped-Blacklist", 0, company, job_title, link, location, "Title Match")
                        continue
                    if any(w.lower() in company.lower() for w in self.company_blacklist):
                        self.db.mark_job_seen(link, job_title, "Blacklisted-Company", "Company Match", job_id=job_id)
                        self.write_log("Skipped-Blacklist", 0, company, job_title, link, location, "Company Match")
                        continue

                    # 3. Content Extraction (Click & Read)
                    self._click_job_card(job_tile)
                    human_sleep(1.5, 0.5)

                    eligibility = self.check_job_eligibility()
                    if eligibility != "Ready":
                        if eligibility.startswith("Loading Timeout"):
                            print(f"Skipping ({eligibility}): {job_title}")
                            continue
                        print(f"Skipping: {job_title} -> {eligibility}")
                        self.db.mark_job_seen(link, job_title, "Skipped-NotEligible", eligibility, job_id=job_id)
                        self.write_log("Skipped-NotEligible", 0, company, job_title, link, location, eligibility)
                        continue

                    description = self._get_job_description()
                    if not (description or "").strip():
                        print(f"Skipping (Empty Description - retry later): {job_title}")
                        continue
                    print(f"Reading {job_title} at {company} ({len(description)} chars)...")
                    simulate_reading(self.browser, description, min_duration=3.0)

                    # 4. Heuristics (Local Filter)
                    heuristic_res = self.ai_handler.check_heuristics(description, self.user_profile_text)
                    if heuristic_res:
                        score, reason = heuristic_res
                        print(f"Skipped (Heuristic): {job_title}")
                        self.db.mark_job_seen(link, job_title, "Skipped-Heuristic", reason, job_id=job_id)
                        self.write_log("Skipped-Heuristic", score, company, job_title, link, location, reason)
                        continue

                    # 5. Add to Buffer
                    job_data = {
                        "id": link,
                        "job_id": job_id,
                        "text": description,
                        "title": job_title,
                        "company": company,
                        "index": idx,
                        "location": location
                    }
                    batch_jobs.append(job_data)

                    # TRIGGER BATCH ANALYSIS
                    if len(batch_jobs) >= BATCH_SIZE:
                        self._process_apply_batch(batch_jobs, job_list)
                        batch_jobs = []

                except StaleElementReferenceException:
                    if current_dedupe_key:
                        seen_on_page.discard(current_dedupe_key)
                    stale_reloads += 1
                    if stale_reloads > max_stale_reloads:
                        print("Stale Element persisted after retries. Moving to next page...")
                        break
                    print(f"Stale Element. Reloading page list ({stale_reloads}/{max_stale_reloads})...")
                    time.sleep(1.0)
                    refreshed_list = self._get_job_cards()
                    if not refreshed_list:
                        break
                    job_list = refreshed_list
                    reloaded = True
                    break
                except Exception as e:
                    if str(e) == "API_LIMIT_REACHED": raise e
                    if str(e) == "STOP_SIGNAL": raise e
                    if str(e) == "DAILY_LIMIT_REACHED": raise e
                    print(f"Job Loop Error: {e}")

            if not reloaded:
                break

        if batch_jobs:
            self._process_apply_batch(batch_jobs, job_list)

    def _process_apply_batch(self, batch_jobs, job_list):
        """Evaluates a buffered batch with AI and applies to matches meeting the threshold."""
        print(f" >> Analyzing Batch of {len(batch_jobs)} jobs...")
        ai_results = self.ai_handler.evaluate_batch(batch_jobs, self.user_profile_text)

        for j_data in batch_jobs:
            res = ai_results.get(j_data['id'], {'score': 0, 'reason': 'Error'})
            score = res['score']
            reason = res['reason']
            j_title = j_data['title']
            j_link = j_data['id']
            j_job_id = j_data.get('job_id')

            if score >= self.application_match_threshold:
                print(f" [MATCH] {j_title} ({score}/100): {reason}")
                try:
                    current_tile = self._refetch_job_tile(j_job_id, job_list, j_data['index'])
                    if not current_tile:
                        print(f"Failed to apply to {j_title}: Job tile no longer available")
                        continue
                    self._click_job_card(current_tile)
                    human_sleep(2.0, 0.5)

                    app_status, app_reason = self.apply_to_job()
                    if app_status == "Retry Later":
                        print(f" [RETRY LATER] {j_title} ({score}/100): {app_reason}")
                    else:
                        self.log_application(app_status, score, j_data['company'], j_title, j_link, j_data['location'], reason=app_reason, job_id=j_job_id)
                except Exception as e:
                    if str(e) in ("API_LIMIT_REACHED", "STOP_SIGNAL", "DAILY_LIMIT_REACHED"):
                        raise e
                    print(f"Failed to apply to {j_title}: {e}")
            else:
                if self._is_transient_batch_error(reason):
                    print(f" [RETRY LATER] {j_title} ({score}/100): {reason}")
                else:
                    print(f" [SKIP] {j_title} ({score}/100): {reason}")
                    self.db.mark_job_seen(j_link, j_title, "Skipped-LowScore", reason, job_id=j_job_id)
                    self.write_log("Skipped-LowScore", score, j_data['company'], j_title, j_link, j_data['location'], reason)

    def apply_to_job(self):
        btn = self._wait_for_easy_apply_button()
        if not btn:
            status, _ = self._check_job_eligibility_once()
            if status == "Already Applied":
                return "Already Applied", "Button not found"
            return "Retry Later", "Easy Apply button load timeout"
        smart_click(self.browser, btn)
        human_sleep(2.0, 0.5)

        for _ in range(15):
            try:
                primary_btn = self.form.find_primary_button()
                if not primary_btn:
                    break

                btn_text = (primary_btn.text or primary_btn.get_attribute('aria-label') or '').strip().lower()
                if 'submit application' in btn_text:
                    self.form.fill_up()
                    self.form.unfollow()
                    smart_click(self.browser, primary_btn)
                    human_sleep(4.0, 1.0)
                    self.form.dismiss_post_submit()
                    return "Applied", "Success"

                self.form.fill_up()
                smart_click(self.browser, primary_btn)
                human_sleep(3.5, 0.5)

                if self.form.check_for_errors():
                    print("Blocking form error detected. Aborting application.")
                    self.form.close_modal()
                    return "Failed", "Form Validation Error"

            except Exception as e:
                if str(e) in ("STOP_SIGNAL", "API_LIMIT_REACHED"): raise e
                traceback.print_exc()
                self.form.close_modal()
                err_msg = str(e)
                short_reason = " ".join(err_msg.split()[:5])
                return "Failed", short_reason

        return "Failed", "Unknown Flow Error"
    
    def log_application(self, status, score, company, title, link, loc, reason="", job_id=None):
        if status == "Applied":
            self.write_log("Applied", score, company, title, link, loc, reason)
            self.db.mark_job_seen(link, title, "Applied", reason, job_id=job_id)
            self.daily_count += 1
            if self.ban_safe:
                self.save_daily_state()
                if self.daily_count >= self.max_apps:
                    print(f"Daily application limit ({self.max_apps}) reached. Stopping.")
                    raise Exception("DAILY_LIMIT_REACHED")
        elif status == "Already Applied":
             self.write_log("Already Applied", score, company, title, link, loc, reason)
             self.db.mark_job_seen(link, title, "Already Applied", reason, job_id=job_id)
        else:
             self.write_log("Failed", score, company, title, link, loc, reason)
             self.db.mark_job_seen(link, title, "Failed", reason, job_id=job_id)

    def _check_job_eligibility_once(self):
        """Performs a single eligibility check, returning (status, is_definitive)."""
        btn = self._find_easy_apply_button()

        if btn:
            try:
                btn_text = ((btn.text or '') + ' ' + (btn.get_attribute('aria-label') or '')).lower()
            except StaleElementReferenceException:
                return None, False
            if 'applied' in btn_text:
                return "Already Applied", True
            return "Ready", True

        pane = None
        self.browser.implicitly_wait(0)
        try:
            feedback_msgs = self.browser.find_elements(By.CLASS_NAME, 'artdeco-inline-feedback__message')
            for msg in feedback_msgs:
                msg_text = msg.text.lower()
                if 'applied' in msg_text:
                    return "Already Applied", True
                if 'we limit daily submissions' in msg_text:
                    print("LinkedIn daily submission limit message detected. Stopping.")
                    raise Exception("API_LIMIT_REACHED")

            pane = self._get_details_pane()
            if pane:
                pane_text = (pane.text or '').lower()
            else:
                body_els = self.browser.find_elements(By.TAG_NAME, 'body')
                pane_text = (body_els[0].text[:3000].lower()) if body_els else ''

            if 'we limit daily submissions' in pane_text:
                print("LinkedIn daily submission limit message detected. Stopping.")
                raise Exception("API_LIMIT_REACHED")
            if 'application submitted' in pane_text:
                return "Already Applied", True
            if 'applied on ' in pane_text or ('applied ' in pane_text and any(k in pane_text for k in ('ago', 'today', 'just now', 'yesterday'))):
                return "Already Applied", True
            if 'no longer accepting applications' in pane_text:
                return "Not Easy Apply (No longer accepting applications)", True
        except Exception as inner_e:
            if str(inner_e) == "API_LIMIT_REACHED":
                raise inner_e
        finally:
            self.browser.implicitly_wait(4)

        if self._has_external_apply_button(pane):
            return "Not Easy Apply (Button missing)", True

        return "Loading Timeout (Retry later)", False

    def check_job_eligibility(self, timeout=None):
        """Checks job eligibility, polling non-linearly only while the details pane is still loading."""
        try:
            if timeout is None:
                timeout = random.uniform(4.5, 7.0)
            deadline = time.time() + timeout
            attempt = 0

            while True:
                status, is_definitive = self._check_job_eligibility_once()
                if is_definitive:
                    if attempt > 0 and status == "Ready":
                        time.sleep(random.uniform(0.35, 0.95))
                    return status

                remaining = deadline - time.time()
                if remaining <= 0:
                    return "Loading Timeout (Retry later)"
                self._jittered_poll_sleep(attempt, remaining)
                attempt += 1

        except Exception as e:
            if str(e) == "API_LIMIT_REACHED":
                raise e
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

    def _try_click_next_page(self, page):
        """Attempts SDUI/legacy pagination button click before falling back to URL navigation."""
        self.browser.implicitly_wait(0)
        try:
            selectors = (
                (By.CSS_SELECTOR, f'button[data-testid="pagination-indicator-{page}"]'),
                (By.CSS_SELECTOR, f'button[aria-label="Page {page + 1}"]'),
                (By.CSS_SELECTOR, 'button[data-testid="pagination-controls-next-button-visible"]'),
                (By.CSS_SELECTOR, 'button[aria-label="View next page"]'),
            )
            for by, sel in selectors:
                try:
                    for btn in self.browser.find_elements(by, sel):
                        aria_dis = (btn.get_attribute('aria-disabled') or '').lower() == 'true'
                        dis_attr = btn.get_attribute('disabled') is not None
                        if btn.is_displayed() and btn.is_enabled() and not aria_dis and not dis_attr:
                            try:
                                self.browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                            except Exception:
                                pass
                            smart_click(self.browser, btn)
                            human_sleep(2.5, 0.5)
                            return True
                except Exception:
                    pass

            indicators = []
            for by, sel in (
                (By.CSS_SELECTOR, 'button[data-testid^="pagination-indicator-"]'),
                (By.CSS_SELECTOR, '.artdeco-pagination__indicator'),
            ):
                try:
                    indicators.extend(self.browser.find_elements(by, sel))
                except Exception:
                    pass
            if indicators:
                raise Exception("No more jobs. (Reached last page)")
            return False
        finally:
            self.browser.implicitly_wait(4)

    def next_job_page(self, position, location, page):
        if page > 0 and self._try_click_next_page(page):
            return

        raw_loc = location[len("&location="):] if location.startswith("&location=") else location
        encoded_pos = quote_plus(position)
        encoded_loc = quote_plus(raw_loc)
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={encoded_pos}&location={encoded_loc}&start={page * 25}"
        if self.base_search_url:
            search_url += f"&{self.base_search_url}"
        self.browser.get(search_url)

