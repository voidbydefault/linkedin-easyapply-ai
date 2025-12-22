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
        Overrides the main bot's apply_jobs to just SCANN and LOG jobs.
        Does NOT apply.
        """
        try:
            if no_jobs and 'No matching jobs' in no_jobs[0].text:
                raise Exception("No more jobs.")
        except:
            pass
        
        job_list = []
        try:
            # Try multiple selectors for job list (Copied from parent)
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
        batch_jobs = []      
        BATCH_SIZE = 10 

        print(f"🔭 Scouting {len(job_list)} jobs on page...")

        for idx, job_tile in enumerate(job_list):
            
            try:
                self.browser.execute_script("arguments[0].scrollIntoView(true);", job_tile)
                try:
                    title_el = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title--link')
                    job_title = title_el.text.strip()
                    link = title_el.get_attribute('href').split('?')[0]
                except:
                    continue

                # 1. Scout DB Check
                # 2. Blacklist Check (Same as parent)
                try:
                    company = job_tile.find_element(By.CLASS_NAME, 'artdeco-entity-lockup__subtitle').text
                except:
                    company = "Unknown"

                if any(w.lower() in job_title.lower() for w in self.title_blacklist):
                    print(f"Skipping (Blacklist): {job_title}")
                    continue
                if any(w.lower() in company.lower() for w in self.company_blacklist):
                    print(f"Skipping (Blacklist): {company}")
                    continue

                # 3. Content Extraction (Click & Read)
                smart_click(self.browser, job_tile)
                human_sleep(1.5, 0.5)

                try:
                    desc_el = self.browser.find_element(By.CLASS_NAME, "jobs-search__job-details--container")
                    description = desc_el.text
                    
                    if description:
                         simulate_reading(self.browser, description, min_duration=2.0)
                except:
                    description = ""
                
                # 4. Heuristics (Local Filter)
                heuristic_res = self.ai_handler.check_heuristics(description, self.user_profile_text)
                if heuristic_res:
                    score, reason = heuristic_res
                    print(f"Skipping (Heuristic): {job_title}")
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
                    
                    # Process Results
                    for j_data in batch_jobs:
                        res = ai_results.get(j_data['id'], {'score': 0, 'reason': 'Error'})
                        score = res['score']
                        reason = res['reason']
                        
                        j_title = j_data['title']
                        j_link = j_data['id']
                        
                        if score >= self.application_match_threshold:
                            print(f" [FOUND] {j_title} ({score}/100): {reason}")
                            # ACT: Save to Scout DB
                            self.db.add_scout_job(j_link, j_title, j_data['company'], j_data['location'], score, reason)
                        else:
                            print(f" [SKIP] {j_title} ({score}/100): {reason}")

                    batch_jobs = []

            except StaleElementReferenceException:
                print("Stale Element. Reloading page list...")
                return 
            except Exception as e:
                print(f"Scout Loop Error: {e}")
