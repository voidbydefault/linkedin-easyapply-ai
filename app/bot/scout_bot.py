from .bot import LinkedinEasyApply
from .utils import human_sleep, smart_click, simulate_reading
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException

class ScoutBot(LinkedinEasyApply):
    def __init__(self, parameters, driver, ai_config, ai_handler_instance, user_profile, active_positions):
        super().__init__(parameters, driver, ai_config, ai_handler_instance, user_profile, active_positions)
        print("🔭 Scout Bot Initialized. Application Limit Ignored.")
        # Ensure scout table exists
        self.db.init_scout_table()

    def get_base_search_url(self, parameters):
        """Builds LinkedIn search filter params for Scout mode, intentionally omitting Easy Apply (f_AL=true)."""
        url_parts = []

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

    def apply_jobs(self, location):
        """
        Overrides the main bot's apply_jobs to just SCAN and LOG jobs.
        Does NOT apply.
        """
        job_list = self._get_job_cards()

        if not job_list:
            self.check_no_jobs()
            print("No jobs found on page (0 job cards matched).")
            raise Exception("No jobs found on page.")

        if not hasattr(self, '_seen_search_job_ids'):
            self._seen_search_job_ids = set()

        page_job_ids = set()
        for tile in job_list:
            info = self._extract_card_info(tile)
            if info and info[3]:
                page_job_ids.add(info[3])
            elif info and info[2]:
                page_job_ids.add(info[2])

        if page_job_ids and page_job_ids.issubset(self._seen_search_job_ids):
            raise Exception("No more jobs. (All jobs on page already seen)")
        self._seen_search_job_ids.update(page_job_ids)

        # --- BATCH PROCESSING START ---
        batch_jobs = []
        BATCH_SIZE = int(self.ai_settings.get('batch_size', 5))
        seen_on_page = set()

        print(f"🔭 Scouting {len(job_list)} jobs on page...")

        for idx, job_tile in enumerate(job_list):
            try:
                card_info = self._extract_card_info(job_tile)
                if not card_info:
                    continue
                job_title, company, link, job_id = card_info

                dedupe_key = job_id or self.db.get_job_hash(link)
                if dedupe_key in seen_on_page:
                    continue
                seen_on_page.add(dedupe_key)

                prev_status_data = self.db.get_scout_status(link, job_id=job_id) or self.db.get_job_status(link, job_id=job_id)
                if prev_status_data:
                    status, reason = prev_status_data
                    reason_msg = f": {reason}" if reason else ""
                    print(f"Skipping (Already Scanned - {status}{reason_msg}): {job_title}")
                    continue

                if self._is_card_already_applied(job_tile, job_title=job_title, company=company):
                    print(f"Skipping (Already Applied on LinkedIn): {job_title}")
                    self.db.mark_scout_seen(link, job_title, "Already Applied", "LinkedIn Applied badge", job_id=job_id)
                    self.db.mark_job_seen(link, job_title, "Already Applied", "LinkedIn Applied badge", job_id=job_id)
                    continue

                if any(w.lower() in job_title.lower() for w in self.title_blacklist):
                    print(f"Skipping (Blacklist): {job_title}")
                    self.db.mark_scout_seen(link, job_title, "Blacklisted-Title", "Title Match", job_id=job_id)
                    continue
                if any(w.lower() in company.lower() for w in self.company_blacklist):
                    print(f"Skipping (Blacklist): {company}")
                    self.db.mark_scout_seen(link, job_title, "Blacklisted-Company", "Company Match", job_id=job_id)
                    continue

                # Content Extraction (Click & Read)
                self._click_job_card(job_tile)
                human_sleep(1.5, 0.5)

                description = self._get_job_description()
                if not (description or "").strip():
                    print(f"Skipping (Empty Description - retry later): {job_title}")
                    continue
                simulate_reading(self.browser, description, min_duration=2.0)

                # Heuristics (Local Filter)
                heuristic_res = self.ai_handler.check_heuristics(description, self.user_profile_text)
                if heuristic_res:
                    score, reason = heuristic_res
                    print(f"Skipping (Heuristic): {job_title}")
                    self.db.mark_scout_seen(link, job_title, "Skipped-Heuristic", reason, job_id=job_id)
                    continue

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

                if len(batch_jobs) >= BATCH_SIZE:
                    self._process_scout_batch(batch_jobs)
                    batch_jobs = []

            except StaleElementReferenceException:
                print("Stale Element. Reloading page list...")
                return
            except Exception as e:
                if str(e) in ("API_LIMIT_REACHED", "STOP_SIGNAL"):
                    raise e
                print(f"Scout Loop Error: {e}")

        if batch_jobs:
            self._process_scout_batch(batch_jobs)

    def _process_scout_batch(self, batch_jobs):
        """Evaluates a batch of scouted jobs with AI and stores matches in the Scout DB."""
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
                print(f" [FOUND] {j_title} ({score}/100): {reason}")
                self.db.add_scout_job(j_link, j_title, j_data['company'], j_data['location'], score, reason, job_id=j_job_id)
                self.db.mark_scout_seen(j_link, j_title, "Scouted-Match", reason, job_id=j_job_id)
            else:
                if self._is_transient_batch_error(reason):
                    print(f" [RETRY LATER] {j_title} ({score}/100): {reason}")
                else:
                    print(f" [SKIP] {j_title} ({score}/100): {reason}")
                    self.db.mark_scout_seen(j_link, j_title, "Scouted-LowScore", reason, job_id=j_job_id)
