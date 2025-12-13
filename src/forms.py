import time
import traceback
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
from .utils import smart_click, human_sleep, human_type

class ApplicationForm:
    def __init__(self, driver, ai_handler, user_profile, config):
        self.browser = driver
        self.ai_handler = ai_handler
        self.user_profile_text = user_profile
        
        # Config params
        self.personal_info = config.get('personalInfo', {})
        self.checkboxes = config.get('checkboxes', {})
        self.languages = config.get('languages', {})
        self.uploads = config.get('uploads', {})
        self.resume_dir = self.uploads.get('resume', '')
        self.salary_minimum = config.get('salaryMinimum', '')
        self.notice_period = config.get('noticePeriod', '')
        self.experience_default = config.get('experience', {}).get('default', 2)
        
        self.is_hybrid = self.checkboxes.get('hybrid', False)
        self.is_remote = self.checkboxes.get('remote', False)
        
        ai_settings = config.get('ai_settings', {}) 
        
        self.use_ai_qa = False # Set by setter or passed in config if we merge.

    def setup_ai_config(self, use_ai_qa):
        self.use_ai_qa = use_ai_qa

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

        # User's configured answers are priority
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

        # Use GenAI as a fallback
        ai_ans = None
        if self.use_ai_qa:
            ai_ans = self.ai_handler.answer_question(label, f"Options: {options}", self.user_profile_text)
            if ai_ans:
                print(f"  -> AI Suggestion: {ai_ans}")
                for opt in select.options:
                    if ai_ans.lower() in opt.text.lower():
                        select.select_by_visible_text(opt.text)
                        return

        # Last drop down if 1 and 2 don't work
        try:
            if 'select' in select.first_selected_option.text.lower():
                select.select_by_index(len(select.options) - 1)
        except:
            pass

    def handle_radio(self, q):
        try:
            label = q.find_element(By.CLASS_NAME, 'fb-dash-form-element__label').text.lower()
            radios = q.find_elements(By.TAG_NAME, 'label')

            print(f"  [Q] {label} (Radio)")

            # Config Priority
            degrees = self.checkboxes.get('degreeCompleted', [])
            if any(deg.lower() in label for deg in degrees):
                print("  -> Config (Degree): Yes")
                for r in radios:
                    if 'yes' in r.text.lower():
                        smart_click(self.browser, r)
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

            # Citizenship Logic
            citizenship_map = {
                'us_citizen': ['us citizen', 'united states', 'u.s.', 'usa'],
                'eu_citizen': ['eu citizen', 'european union', 'eu passport'],
                'gcc_citizen': ['gcc citizen', 'gulf cooperation'],
                'canadian_citizen': ['canadian citizen', 'canada'],
                'uk_citizen': ['uk citizen', 'united kingdom', 'british'],
                'australian_citizen': ['australian citizen', 'australia']
            }
            
            for config_key, keywords in citizenship_map.items():
                if any(k in label for k in keywords):
                    is_citizen = self.checkboxes.get(config_key, False)
                    target = "yes" if is_citizen else "no"
                    print(f"  -> Config ({config_key}): {target}")
                    for r in radios:
                        if target in r.text.lower(): r.click(); return

            neg_keywords = ['gender', 'race', 'veteran', 'disability', 'conflict', 'convict', 'disqualif', 'felony']
            if any(x in label for x in neg_keywords):
                for r in radios:
                    if any(d in r.text.lower() for d in ['decline', 'prefer not', 'no', 'none']):
                        print(f"  -> Force Negative/Decline: {r.text}")
                        r.click()
                        return

            # AI Decision
            target = "yes"
            if self.use_ai_qa:
                target = self.ai_handler.answer_question(label, "Yes/No", self.user_profile_text) or "yes"
            print(f"  -> AI Suggestion: {target}")

            target = target.lower()
            clicked = False
            for r in radios:
                if target in r.text.lower():
                    r.click()
                    clicked = True
                    break

            # Fallback
            if not clicked:
                for r in radios:
                    if 'no' in r.text.lower():
                        r.click()
                        clicked = True
                        print("  -> Fallback: No")
                        break

            if not clicked and radios:
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
            # Use human_type for natural typing (multitasking safe via Selenium)
            human_type(element, text)
        except:
            pass

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

    def unfollow(self):
        try:
            lbl = self.browser.find_element(By.XPATH, "//label[contains(.,'stay up to date')]")
            smart_click(self.browser, lbl)
        except:
            pass
